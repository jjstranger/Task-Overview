"""SQLite 数据层：连接、建表、跨机锁、快照备份。

网络盘（SMB）上的 SQLite 注意事项：
- 不用 WAL（依赖共享内存文件，网络盘上反而更危险），用 rollback journal
- 事务尽量短，减少锁持有时间
- 默认仍走"跨机单写"：`board.lock` 里**每台机器一行**心跳，别人的还新鲜就拒绝启动
- 打开「多机同时打开」后不再拒绝，改成只登记在线机器 + 靠 SQLite 自己的
  busy_timeout 排队。**这不是官方支持的用法**（SQLite 明确说网络盘上的锁不可靠），
  所以：写操作一律带重试、对方改过数据要主动提示重新加载，见 `poll_external()`。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import socket
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

DB_NAME = "board.sqlite"


def default_db_path() -> str:
    """内置默认库位置：程序目录下的 `data/board.sqlite`。

    ⚠ 这里**不写死任何机器的真实网络盘路径**：这个仓库是公开的，
    把某台机器的 NAS 共享名、目录结构塞进源码等于公告出去。真要读哪个库，
    看的是 `paths.resolve_db_path()`（`--db` > `BOARD_DB` > `data/config.json`），
    只有调用方没显式传路径时才会落到这儿；新机器拷一份软件由引导页接管去问用户。
    """
    try:
        from paths import data_dir

        return os.path.join(data_dir(), DB_NAME)
    except Exception:
        return os.path.join(os.getcwd(), "data", DB_NAME)

# `board.lock` 里一行就是一台机器：`主机名:进程号|unix时间戳`。
# 心跳 20 秒一次（main.py），超过这个秒数还没刷新就当作掉线，
# 免得另一台机器停电后留下一个永远删不掉、也永远拦人的锁。
LOCK_TTL = 60


def _lock_log(msg: str) -> None:
    """锁文件相关事件留个证据。

    照着 api._pick_log 的样式：这类失败以前是静默 except pass，
    网络盘上"为什么另一台机器能开而我不能"就完全没有线索。
    """
    try:
        import paths

        p = paths.data_file("lock.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

# 环节状态改版时的一次性迁移表：旧词 → 新词。
# 每次开库跑一遍、幂等：改完就不再命中任何行，老库不会带着「野状态」进界面。
# 定义在数据层是为了避免 models → db → models 的循环引用。
NODE_STATUS_MIGRATE = {
    "未开始": "待开始",
    "进行中": "制作中",
    "阻塞": "等上游",
    "待确认": "反馈",
    "完成": "通过",
    "搁置": "暂停",
    "取消": "中止",          # 2026-09-30 与项目层统一口径（「取消」这个词不用了）
}

# 项目状态的同款迁移表。注意它**只在 projects 表上跑** ——
# 上面的 "搁置"→"暂停" 是环节层的规则，两张表混着跑会把项目改成环节的状态词。
PROJECT_STATUS_MIGRATE = {
    "搁置": "中止",
    # 「待交付」这一档被撤销了。退到「进行中」而不是「已交付」：
    # 没交付的活儿本质上还在进行，判成已交付会凭空多出一批交付量。
    "待交付": "进行中",
}

SETTINGS_DEFAULTS = {
    "theme": "dark",
    "db_path": default_db_path(),
    "autostart": "1",
    "on_top": "1",
    "remind_level": "2",
    "checkin_time": "09:00",
    # 停滞阈值：全库**一个**数（2026-09-30 之前分「商业 / 个人」两档，
    # 用户要求统一 —— 分类现在是可增删改的，再绑两档就说不通了）
    "stale_days": "3",
    "export_dir": "",
    "nag_minutes": "30",
    "alert_interval": "180",
    "notify_enabled": "1",
    # "1" = 启动时给 WebView2 加 --no-sandbox。看门狗判定沙箱被拦会自动置 1，
    # 也可以在设置里手动开关（改完要重启）
    "nosandbox": "0",
}

# 分类（分组）的出厂值：只在 `groups` 表**还是空的**时候种进去。
# 之后增删改一律在库里，代码里这份不再参与 —— 用户重命名「商业项目」为
# 「客户活儿」之后，键还是 commercial（项目挂靠靠键），名字随他改。
GROUPS_DEFAULT = [("commercial", "商业项目"), ("personal", "个人项目")]

# collapsed 默认 1（收起）：默认打开只展开「商业 / 个人」两个分组标题行，
# 项目行和环节都折着，一眼看到全貌而不用先滚一屏。想展开哪条自己点开。
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'commercial',
    client_id INTEGER,
    status TEXT NOT NULL DEFAULT '进行中',
    priority INTEGER NOT NULL DEFAULT 0,
    deadline TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    artist TEXT,
    collapsed INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    last_activity_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    parent_id INTEGER,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待开始',
    done INTEGER NOT NULL DEFAULT 0,
    collapsed INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    deadline TEXT,
    note TEXT,
    artist TEXT,
    last_activity_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    path TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- 分类（分组）：项目树顶层的几个筐（商业 / 个人 / …）
-- 早先是代码里写死的两个常量，现在可增删改（2026-09-30）。
-- `gkey` 是稳定标识，项目用 projects.category 挂它；改名只动 name，键不动 ——
-- 所以重命名不会波及任何项目。（列名没叫 key 是为了不跟 SQL 关键字打照面。）
CREATE TABLE IF NOT EXISTS groups (
    gkey TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact TEXT,
    phone TEXT,
    email TEXT,
    settlement_cycle TEXT,
    default_currency TEXT DEFAULT 'CNY',
    note TEXT
);

CREATE TABLE IF NOT EXISTS finance (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    node_id INTEGER,
    kind TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CNY',
    tax_rate REAL,
    date TEXT,
    status TEXT,
    invoice_no TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 1,
    project_id INTEGER,
    node_id INTEGER,
    detail TEXT,
    day TEXT NOT NULL
);
-- ⚠ 索引必须写在**建表之后**：执行 SCHEMA 时表还不存在，CREATE INDEX 会直接报
-- `no such table`（踩过一次 —— 冒烟里全库都打不开）。
-- activity_log 按天取（热力图 GROUP BY day、面板「最近一周」）：40 万行时实测
-- 活跃页 245ms → 175ms；老库开一次就自动补上（executescript 是幂等的）
CREATE INDEX IF NOT EXISTS idx_log_day ON activity_log(day);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _params(args):
    """允许 run(sql, (1,2)) 和 run(sql, 1, 2) 两种写法。"""
    if len(args) == 1 and isinstance(args[0], (tuple, list)):
        return tuple(args[0])
    return tuple(args)


def _retry_locked(fn, tries: int = 4, wait: float = 0.4):
    """`database is locked` 时补几次重试，其它错误原样抛。

    ⚠ **光靠 `busy_timeout` 不够**：SQLite 文档里写明了，如果"先拿了读锁、
    再想升级成写锁"时撞上别人持着写锁，它会判断这可能是死锁，于是**不调用
    busy handler**，直接返回 SQLITE_BUSY。而 autocommit 下一条 INSERT
    正好就是这个形状（先 SHARED 再 RESERVED）。所以这里必须自己补几次。
    重试点大约落在 0 / 0.8 / 2.4 / 4.8 秒 —— 对面一笔正常写入是毫秒级，
    这个跨度足够；真的等不到就报错，不无限等下去。
    """
    last: Exception | None = None
    for i in range(max(1, tries)):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last = exc
            if i + 1 < tries:
                time.sleep(wait * (i + 1) * 2)
    raise last  # type: ignore[misc]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class Locked(Exception):
    """被另一台机器占用（NAS 上两个人同时开）。

    本机重复启动由互斥量拦下并抛 AlreadyRunning，不走这里。
    """


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
# ⚠ 句柄一律按**指针宽度**声明。不声明的话 ctypes 默认按 c_int 传，
# 而 CreateMutexW 的返回值是个 Python int —— 64 位下句柄稍大就被截成 32 位，
# ReleaseMutex/CloseHandle 拿到一个错句柄，静默失败（跟 main.py 置顶那个坑同源）。
_kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
_kernel32.ReleaseMutex.restype = ctypes.c_bool
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_bool

ERROR_ALREADY_EXISTS = 183


class AlreadyRunning(Exception):
    """本机已有实例占着（互斥量判定，比看 pid 可靠得多）。"""


def try_instance_mutex(key: str) -> ctypes.c_void_p:
    """用命名互斥量做本机单实例检测，拿不到就抛 AlreadyRunning。

    为什么不查 pid：托管版 Python 会先起一个 launcher 再 exec 真正的进程，
    锁文件里记的 pid 不一定对应活着的那个，判错过一次。
    互斥量由内核持有，进程崩溃/被强杀都会自动释放，不会留下需要催收的残骸。
    """
    h = _kernel32.CreateMutexW(None, True, f"Local\\ProjectBoard_{key}")
    if not h:
        # 建不了互斥量就退化按"没有别的实例"处理
        return 0  # type: ignore[return-value]
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(h)
        raise AlreadyRunning()
    return h


def release_instance_mutex(h) -> None:
    try:
        if h:
            _kernel32.ReleaseMutex(h)
            _kernel32.CloseHandle(h)
    except Exception:
        pass


def path_key(path: str) -> str:
    import hashlib

    return hashlib.md5(os.path.abspath(path).lower().encode("utf-8")).hexdigest()[:16]


def host_of(who: str) -> str:
    """从 `主机名:进程号` 里取主机名（进程号里不会有冒号，用 rpartition 最稳）。"""
    return str(who or "").rpartition(":")[0] or str(who or "")


class Db:
    def __init__(self, path: str | None = None, share: bool = False):
        self.path = path or default_db_path()
        # 多机同时打开：锁文件只用来"登记谁在线"，不再用来拒绝启动
        self.share = bool(share)
        self.conn: sqlite3.Connection | None = None
        # 本机实例互斥量句柄；注意别叫 self.mutex，那是下面 DB 访问的 RLock
        self.instance_mutex: ctypes.c_void_p = 0
        self.lock_path: str = ""
        self.readonly = False
        # JS 桥的请求跑在 pywebview 的 HTTP 线程里，调度器又是一个线程，
        # 所以连接必须允许跨线程，并用一把锁把访问串行化
        self.mutex = threading.RLock()
        # 本进程在锁文件里的身份，全程复用（心跳、注销都要对得上）
        self.me = f"{socket.gethostname()}:{os.getpid()}"
        # 共享模式下写冲突要排队等，单机模式没这个必要
        self.busy_ms = 30000 if self.share else 10000
        # 库文件上次"我们已知"的状态（mtime+size）；用来判断是不是别人改过
        self._stamp: tuple | None = None
        # 启动时发现的、除了我以外还在用这个库的机器（共享模式下给界面提示）
        self._other_holders: list[tuple[str, float]] = []

    # ---------- 生命周期 ----------

    def open(self) -> None:
        p = Path(self.path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise RuntimeError(f"无法创建数据目录 {p.parent}：{exc}")

        # 本机单实例：抢一个 Windows 命名互斥量，拿不到说明别人在跑
        self.instance_mutex = try_instance_mutex(path_key(str(p)))

        self.lock_path = str(p.with_suffix(".lock"))
        self._acquire_lock()

        # 建库交给统一的连接函数：快照恢复后也要用它重连
        self._connect()

    def _connect(self) -> None:
        p = Path(self.path)
        first = not p.exists()
        self.conn = sqlite3.connect(
            str(p), timeout=self.busy_ms / 1000.0, isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(f"PRAGMA busy_timeout={int(self.busy_ms)}")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # 老快照可能缺后来新增的表，这里补齐（SCHEMA 全是 IF NOT EXISTS）
        self.conn.executescript(SCHEMA)
        self._migrate_columns()
        self._migrate_statuses()
        # ⚠ 顺序要紧：这一条必须在 _init_settings 之前跑 ——
        # 它判据是"新的统一键还不存在"，_init_settings 一跑就会塞上默认值。
        self._migrate_stale_unified()
        self._init_settings()
        self._init_groups()
        self._migrate_collapse_default()
        if first:
            self._seed()
        self._stamp = self._file_stamp()

    def _migrate_columns(self) -> None:
        """补老库缺的列（SCHEMA 的 CREATE TABLE IF NOT EXISTS 不会给已有表加列）。

        幂等：列已存在就跳过，跑第二遍什么都不做。
        """
        for table in ("projects", "nodes"):
            cols = {
                r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if "artist" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN artist TEXT")

    def _migrate_stale_unified(self) -> None:
        """停滞阈值从「商业 / 个人两档」并成一项 `stale_days`。

        老库取**商业那档**的值：他实际在盯的是商业项目，宁可严一点。
        用户没改过的话两档就是默认的 3 / 7，并成 3。
        幂等：标记写过就不再动 —— 用户之后自己改的统一值不能被下次启动掰回去。
        快照回滚走 `_connect`，老快照恢复回来同样会被并一次。
        """
        if self.get_setting("mig_stale_unified", "") == "1":
            return
        old = self.get_setting("stale_days_commercial", "")
        if old and not self.get_setting("stale_days", ""):
            self.set_setting("stale_days", old)
        self.set_setting("mig_stale_unified", "1")

    def _init_groups(self) -> None:
        """分类表为空时种进出厂那两条（商业 / 个人）。

        只在**空表**上种：用户建过、改过、删过之后表里就有东西了，
        这里不会再往里塞（否则删掉一条下次启动就复活）。
        老库、老快照第一次开时表是刚建的 → 正好把两条补上，
        项目里那些 `category='commercial'` 的挂靠关系就还认得上。
        """
        if self.one("SELECT 1 AS x FROM groups LIMIT 1"):
            return
        for i, (k, n) in enumerate(GROUPS_DEFAULT):
            self.conn.execute(
                "INSERT OR IGNORE INTO groups(gkey,name,sort_order) VALUES(?,?,?)",
                (k, n, i),
            )

    def _migrate_collapse_default(self) -> None:
        """一次性把整棵树折起来（默认改成「只展开分组标题行」）。

        只在第一次开库时跑：之后用户自己点开的项目该是开的，
        不能每次启动都被重新折回去。快照回滚走 `_connect`，
        老快照里的 collapsed 是旧习惯（全展开），恢复后同样被折一次。
        """
        if self.get_setting("mig_collapse_default", "") == "1":
            return
        with self.mutex:
            self.conn.execute("UPDATE projects SET collapsed=1")
            self.conn.execute("UPDATE nodes SET collapsed=1")
        self.set_setting("mig_collapse_default", "1")

    def _migrate_statuses(self) -> None:
        """把老状态词换成新词（环节 `未开始`→`待开始`、`搁置`→`暂停`；项目 `搁置`→`中止`）。

        幂等：只 UPDATE 命中旧词的行，跑第二遍就是 0 行。
        快照回滚也是走 `_connect`，所以旧快照恢复回来同样会被顺手迁移。

        ⚠ 两张表**各用各的映射表**：环节的 `搁置`→`暂停` 绝不能落到项目行上
        （项目那边是 `搁置`→`中止`），混着跑会写出两套都认不出的野状态。
        """
        for table, table_map in (
            ("nodes", NODE_STATUS_MIGRATE),
            ("projects", PROJECT_STATUS_MIGRATE),
        ):
            for old, new in table_map.items():
                with self.mutex:
                    self.conn.execute(
                        f"UPDATE {table} SET status=? WHERE status=?", (new, old)
                    )

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
        release_instance_mutex(self.instance_mutex)
        self.instance_mutex = 0
        self._release_lock()

    def _acquire_lock(self) -> None:
        """跨机锁：登记自己在用这台库。

        默认模式（`share=False`）：另一台机器心跳还新鲜就拒绝启动。
        共享模式（`share=True`）：不拒绝，只登记 —— 两台机器都开着，
        谁写谁排队（SQLite 自己靠 busy_timeout 排）。

        为什么锁文件是"每台机器一行"而不是一行覆盖：早先是一行 `host:pid|ts`，
        两台机器各写各的会互相盖，心跳时间戳来回跳 —— 谁也说不准对面是不是还活着。
        """
        entries = self._read_lock()
        now_ts = time.time()
        live = [(w, t) for (w, t) in entries
                if w and w != self.me and (now_ts - t) < LOCK_TTL]
        others = [(w, t) for (w, t) in live
                  if host_of(w) != socket.gethostname()]
        self._other_holders = others

        if others and not self.share:
            who, ts = others[0]
            raise Locked(
                f"数据文件正被 {who} 使用（{int(now_ts - ts)} 秒前仍有心跳）。\n\n"
                "如果确实要两台机器一起用，去「设置 → 多机同时打开」打开共享模式；"
                "或者先把另一台机器上的看板退出。"
            )

        self._write_lock(self._registry())

    def _read_lock(self) -> list[tuple[str, float]]:
        """读锁注册表。读不到/格式乱了都当"没人"—— 锁不该拦住打不开的程序。"""
        if not self.lock_path:
            return []
        try:
            raw = Path(self.lock_path).read_text(encoding="utf-8")
        except Exception:
            return []
        out: list[tuple[str, float]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            who, _, ts = line.partition("|")
            who = who.strip()
            if not who:
                continue
            try:
                out.append((who, float(ts or 0)))
            except ValueError:
                continue
        return out

    def _write_lock(self, entries: list[tuple[str, float]]) -> bool:
        try:
            Path(self.lock_path).write_text(
                "".join(f"{w}|{t}\n" for w, t in entries), encoding="utf-8"
            )
            return True
        except Exception as exc:
            # 网络盘上写不了锁文件（权限/离线）不该让程序起不来：
            # 锁是"防误撞"，不是"没它就活不了"。留个证据就行。
            _lock_log(f"写锁文件失败 {self.lock_path}: {exc!r}")
            return False

    def _registry(self, with_me: bool = True) -> list[tuple[str, float]]:
        """该写回锁文件的完整名单：其它机器**还新鲜**的行 + 我自己这一行。"""
        now_ts = time.time()
        out = [(w, t) for (w, t) in self._read_lock()
               if w != self.me and (now_ts - t) < LOCK_TTL]
        if with_me:
            out.append((self.me, now_ts))
        return out

    def heartbeat(self) -> None:
        if not self.lock_path:
            return
        self._write_lock(self._registry())

    def holders(self) -> list[dict]:
        """除了我以外，还有哪些机器在线（心跳新鲜）。给界面显示用。"""
        now_ts = time.time()
        return [
            {"who": w, "host": host_of(w), "age": int(now_ts - t)}
            for (w, t) in self._read_lock()
            if w != self.me and (now_ts - t) < LOCK_TTL
        ]

    def _release_lock(self) -> None:
        """退出时只划掉自己那一行；名单空了才删文件。

        绝不能直接把整个锁文件删掉：共享模式下那是另一台机器还在用的登记表。
        """
        if not self.lock_path:
            return
        rest = self._registry(with_me=False)
        if rest:
            self._write_lock(rest)
            return
        try:
            os.remove(self.lock_path)
        except Exception:
            pass

    # ---------- 别人改过没有（多机同时打开时要靠它提示刷新） ----------

    def _file_stamp(self) -> tuple | None:
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def touch(self) -> None:
        """我们自己刚写完，把基线刷新到最新 —— 否则下一次轮询会把自己的写
        当成"别人改过"，然后一直提示刷新。"""
        self._stamp = self._file_stamp()

    def poll_external(self) -> bool:
        """库文件是不是被别的地方改过（不含本进程自己的写）。

        只看 mtime+size：便宜、够用。宁可偶尔多提示一次"重新加载"，
        也不要让用户对着一份旧数据继续改，改完写回去把对方的改动盖掉。
        单机模式没有"别人"，直接返回 False。
        """
        if not self.share:
            return False
        cur = self._file_stamp()
        if cur is None or self._stamp is None:
            self._stamp = cur
            return False
        if cur != self._stamp:
            self._stamp = cur
            return True
        return False

    def backup(self, keep: int = 10) -> str:
        """退出前把当前库复制一份到 <dir>/backup/。"""
        src = Path(self.path)
        if not src.exists():
            return ""
        dst_dir = src.parent / "backup"
        dst_dir.mkdir(exist_ok=True)
        dst = dst_dir / f"board_{datetime.now():%Y%m%d_%H%M%S}.sqlite"
        # 文件名精确到秒，同一秒里备份两次会互相覆盖 —— 回滚时「先存一份当前状态」
        # 正好紧跟着手动备份，就是这种情形，被盖掉的还偏偏是回滚源。
        n = 1
        while dst.exists():
            dst = dst_dir / f"board_{datetime.now():%Y%m%d_%H%M%S}-{n}.sqlite"
            n += 1
        # 用 sqlite 的 backup API，避免复制到一个写了一半的文件。
        # ⚠ out 必须在 finally 里关：抛错时不关的话，句柄一直占着那个半截文件，
        # 下面 shutil.copy2 的兜底会失败，用户之后想删这个文件也删不掉。
        try:
            out = sqlite3.connect(str(dst))
            try:
                with self.mutex:
                    self.conn.backup(out)
            finally:
                out.close()
        except Exception:
            try:
                shutil.copy2(src, dst)
            except Exception:
                return ""
        # keep<=0 会被 files[:-keep] 解读成「一个都不删」，跟"只留 0 份"的直觉反着来
        keep = max(1, int(keep))
        files = sorted(dst_dir.glob("board_*.sqlite"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep]:
            try:
                f.unlink()
            except Exception:
                pass
        return str(dst)

    # ---------- 快照列表与回滚（M4） ----------

    def backup_dir(self) -> Path:
        return Path(self.path).parent / "backup"

    def snapshots(self, limit: int = 60) -> list:
        """列出现有快照，新的在前。"""
        d = self.backup_dir()
        if not d.exists():
            return []
        out = []
        for f in d.glob("board_*.sqlite"):
            try:
                st = f.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": f.name,
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "ts": st.st_mtime,
                }
            )
        out.sort(key=lambda x: x["ts"], reverse=True)
        return out[:limit]

    def snapshot_now(self) -> dict:
        p = self.backup()
        return {"ok": bool(p), "name": os.path.basename(p) if p else ""}

    def restore(self, name: str) -> dict:
        """用某个快照覆盖当前库。

        恢复本身也是破坏性写操作，所以先给「现在」存一份：
        万一选错了快照，还能再倒回来。
        """
        # basename 挡掉 ../ 这类路径穿越
        src = self.backup_dir() / os.path.basename(str(name))
        if not src.exists():
            raise ValueError("找不到这个快照")

        # 先确认快照能打开、且确实是看板的库，别拿一个坏文件覆盖现网数据
        try:
            chk = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError(f"这个快照读不出来：{exc}")
        try:
            has = chk.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone()[0]
        except sqlite3.Error as exc:
            raise ValueError(f"这个快照读不出来：{exc}")
        finally:
            # 一定要关。假快照（不是数据库的文件）会在这里抛错，
            # 不关的话句柄一直占着，那个文件用户之后删都删不掉
            chk.close()
        if not has:
            raise ValueError("这个文件不像是看板的数据库，已中止")

        safety = self.backup()

        with self.mutex:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
            # 残留的 journal 会让 sqlite 拿旧事务把刚覆盖进去的文件再改回去
            for tail in ("-journal", "-wal"):
                j = Path(self.path + tail)
                if j.exists():
                    try:
                        j.unlink()
                    except Exception:
                        pass
            shutil.copy2(src, self.path)
            self._connect()
        return {"ok": True, "safety": os.path.basename(safety) if safety else ""}

    # ---------- settings ----------

    def _init_settings(self) -> None:
        for k, v in SETTINGS_DEFAULTS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v)
            )

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key=?", key)
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.mutex:
            _retry_locked(lambda: self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            ))
            self.touch()

    def all_settings(self) -> dict:
        return {r["key"]: r["value"] for r in self.q("SELECT * FROM settings")}

    # ---------- 示例数据 ----------

    def _seed(self) -> None:
        self.conn.execute(
            "INSERT INTO projects(title,category,status,created_at,updated_at,last_activity_at)"
            " VALUES(?,?,?,?,?,?)",
            ("示例商业项目", "commercial", "进行中", now(), now(), now()),
        )
        pid = self.conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        self.conn.execute(
            "INSERT INTO nodes(project_id,parent_id,title,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?)",
            # 环节状态里没有「进行中」这一档（已改名「制作中」），这里用现行词
            (pid, None, "第一个环节", "制作中", now(), now()),
        )
        self.conn.execute(
            "INSERT INTO projects(title,category,status,created_at,updated_at,last_activity_at)"
            " VALUES(?,?,?,?,?,?)",
            ("示例个人项目", "personal", "进行中", now(), now(), now()),
        )

    # ---------- 便捷执行 ----------

    def q(self, sql: str, *args):
        with self.mutex:
            return self.conn.execute(sql, _params(args)).fetchall()

    def one(self, sql: str, *args):
        with self.mutex:
            return self.conn.execute(sql, _params(args)).fetchone()

    def run(self, sql: str, *args) -> int:
        with self.mutex:
            cur = _retry_locked(lambda: self.conn.execute(sql, _params(args)))
            self.touch()
            return cur.lastrowid or 0

    def log(self, kind: str, weight: int, detail: str,
            project_id: int | None = None, node_id: int | None = None) -> None:
        with self.mutex:
            _retry_locked(lambda: self.conn.execute(
                "INSERT INTO activity_log(ts,kind,weight,project_id,node_id,detail,day)"
                " VALUES(?,?,?,?,?,?,?)",
                (now(), kind, weight, project_id, node_id, detail, today()),
            ))
            self.touch()
