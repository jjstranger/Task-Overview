"""路径解析：源码运行与 PyInstaller 打包（frozen）两套布局，这里统一。

| | 源码运行 | 打包后（onedir / onefile） |
| --- | --- | --- |
| 代码 | `personal-board/app/*.py` | 解包到 `_MEIPASS` |
| `web/` 只读资源 | `app/web/` | `_MEIPASS/web/`（打进包） |
| `res/` 图标 | `app/res/` | `_MEIPASS/res/`（打进包） |
| `data/` 可写目录 | `personal-board/data/` | exe 同级的 `data/` |
| 日志 / 备份 / 快照 | 同上 | 同上 |

**判据只用 `sys.frozen`，不能用 `__file__` 猜**：onefile 模式下 `__file__` 指向临时解包目录，
拿它去算 `data/` 会得到一份重启就没了的数据目录（WebView2 用户数据目录尤其致命，
被强杀一次就污染，见 README 的踩坑记录）。
"""
from __future__ import annotations

import os
import sys
import threading


def is_frozen() -> bool:
    """PyInstaller 打包后为 True。"""
    return bool(getattr(sys, "frozen", False))


def res_dir() -> str:
    """只读资源（`web/`、`res/`）所在目录。打包后是解包临时目录，只读，别往里写。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def base_dir() -> str:
    """可写数据的落点：`data/`、日志、测试输出都挂在这下面。

    打包后是 **exe 所在目录**（不是解包目录）；源码下是项目根（`app/` 的上一层）。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(res_dir())


def data_dir() -> str:
    return os.path.join(base_dir(), "data")


def data_file(*parts: str) -> str:
    """`data/` 下的文件全路径，顺带建好目录（写日志时不想再到处 mkdir）。"""
    d = data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, *parts)


# ---------- 应用级配置（库路径这类"鸡生蛋"设置只能存在库外面） ----------

def config_file() -> str:
    """应用级配置文件：`data/config.json`，记着"该用哪个库文件"。

    为什么不能存在库里：库路径本身存在库里的话，读它就得先知道库在哪 —— 死循环。
    所以这一项必须落在库外面（exe 同级 / 源码项目根的 `data/`）。
    """
    return data_file("config.json")


def load_config() -> dict:
    import json

    try:
        with open(config_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_config(patch: dict) -> str | None:
    """合并写回；返回错误信息（None 表示成功）。"""
    import json

    d = load_config()
    d.update(patch or {})
    try:
        with open(config_file(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return None
    except Exception as exc:
        return f"{exc}"


def default_db_path() -> str:
    """内置默认库位置（`db.DEFAULT_DB_PATH`）。

    延迟导入：`paths` 是被 `db` 反向依赖的底层模块，模块级 import 会成环。
    工程里"内置默认在哪"只该有这一处 —— 以前 api / paths 各写一遍，
    换默认位置就得记得改两处。
    """
    try:
        from db import default_db_path

        return str(default_db_path())
    except Exception:
        return ""


def resolve_db_path(cli: str = "") -> str:
    """这一次启动到底读哪个库：`--db` > 环境变量 `BOARD_DB` > `data/config.json` > 内置默认。

    单独抽出来是因为以前这段优先级散在三处（main / api.db_info / 前端），
    加一条来源就要改三个地方，改漏了就出现"界面说的和实际读的不是一个文件"。
    """
    cli = (cli or "").strip()
    if cli:
        return cli
    env = (os.environ.get("BOARD_DB") or "").strip()
    if env:
        return env
    cfg = str(load_config().get("db_path") or "").strip()
    if cfg:
        return cfg
    return default_db_path()


# ---------- 多机同时打开（跨机共享同一个库文件） ----------

def share_db() -> bool:
    """是否允许多台机器同时打开同一个数据文件。

    存在应用级配置而不是库里的 settings：库被别的机器占着的时候
    恰恰就是最需要读这个开关的时候，而那时连不上库、读不到 settings。
    """
    return bool(load_config().get("share_db"))


# ---------- 诊断日志（网络盘/权限问题全靠它事后复盘） ----------

def log_line(name: str, msg: str) -> None:
    """往 `data/<name>` 追加一行带时间戳的记录。

    以前这些地方是 `except Exception: pass`，于是"为什么这台机器点浏览没反应"
    在事后完全没有线索。文件名分开是为了别把不同来源混成一本流水账。
    """
    try:
        import time

        with open(data_file(name), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ---------- 数据文件路径的可用性检查 ----------

DB_FILTER = ("SQLite 数据库 (*.sqlite;*.sqlite3;*.db;*.db3)", "所有文件 (*.*)")
DB_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".db3")
DB_NAME = "board.sqlite"


def has_db_file(d: str) -> bool:
    """目录里有没有**真的**像库的文件（不是只看扩展名）。"""
    return bool(find_db_in_dir(d))


def find_db_in_dir(d: str) -> str:
    """目录里已经有的数据文件；没有就返回空串。

    只给用户挑一个**目录**的时候，得自己判断"这里面是不是已经有数据了"。
    优先认 `board.sqlite`（本程序自己的命名），其次才扫别的扩展名 ——
    而且**必须过一遍 SQLite 文件头**：用户挑的可能是下载目录，
    里面躺着一堆同名的 `.db`（缩略图缓存、别的软件的），
    光看扩展名就认，会把人家的东西当数据文件打开。
    """
    try:
        names = os.listdir(d)
    except Exception:
        return ""
    lower = {n.lower(): n for n in names}
    if DB_NAME.lower() in lower:
        p = os.path.join(d, lower[DB_NAME.lower()])
        try:
            if os.path.getsize(p) > 0 and looks_like_db(p) is None:
                return p
        except OSError:
            pass
    cands = []
    for n in names:
        if not n.lower().endswith(DB_SUFFIXES):
            continue
        p = os.path.join(d, n)
        try:
            if os.path.getsize(p) == 0:
                continue
        except OSError:
            continue
        if looks_like_db(p) is None:
            cands.append(p)
    cands.sort()
    return cands[0] if cands else ""


def resolve_target(p: str) -> str:
    """把用户给的路径归一成"这次要用哪个数据文件"（空串 = 没给有效的）。

    引导页只有一个路径输入框，所以"目录 / 文件 / 不存在的目录"三种写法都得认：

    - 目录（已存在，或结尾带分隔符）→ 里面有库就用它，没有就 `<目录>/board.sqlite`（新建）
    - 带扩展名的路径 → 就当是文件本身
    - 其余（无扩展名、不存在）→ 当目录处理，比"猜成无扩展名的文件"更符合直觉

    ⚠ 只做**路径归一**，不校验能不能写、也不建任何东西 —— 那是
    `check_db_target()` 的事，两者分开才好单测。
    """
    p = str(p or "").strip().strip('"')
    if not p:
        return ""
    p = os.path.abspath(p)
    if os.path.isdir(p) or p.endswith(("\\", "/")):
        return find_db_in_dir(p) or os.path.join(p, DB_NAME)
    if os.path.splitext(p)[1]:
        return p
    return os.path.join(p, DB_NAME)


def probe_writable(d: str) -> str | None:
    """真写一个临时文件试试，别拿权限位猜（网络盘上权限位会骗人）。

    返回错误信息（None = 可写）。用完立刻删掉探测文件。
    """
    import uuid

    t = os.path.join(d, "_wtest_" + uuid.uuid4().hex[:8] + ".tmp")
    try:
        with open(t, "w", encoding="utf-8") as f:
            f.write("x")
    except Exception as exc:
        return str(exc)
    try:
        os.remove(t)
    except Exception:
        pass
    return None


def dir_writable(d: str) -> bool:
    return probe_writable(d) is None


def looks_like_db(p: str) -> str | None:
    """是不是个 SQLite 文件。返回错误信息（None = 看着没问题）。"""
    try:
        with open(p, "rb") as f:
            head = f.read(16)
    except Exception as exc:
        return "读不了这个文件：" + str(exc)
    if not head:
        return None                     # 0 字节：当成新建的空库，交给 sqlite 初始化
    if head[:15] != b"SQLite format 3":
        return "这个文件不是 SQLite 数据库（换一个 .sqlite 文件）"
    return None


def check_db_target(p: str, allow_new_parent: bool = False) -> str | None:
    """能不能把 `p` 当数据文件用。返回错误信息（None = 可以）。

    **只验到"目录在、能写、已存在的文件像个 sqlite"为止，不碰文件内容。**
    文件不存在是允许的 —— 那就是"在这里新建一个库"的意思。

    `allow_new_parent=True` 时父目录可以不存在（顺手建出来）；
    引导页「新建到某个目录」用得上，设置页改路径时不用（选完就该是有效的）。
    """
    p = os.path.abspath(p)
    net = preflight(p)
    if net:
        return net
    parent = os.path.dirname(p) or "."
    if not os.path.isdir(parent):
        if not allow_new_parent:
            return "目录不存在：" + parent
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as exc:
            return f"建不了目录 {parent}：{exc}"
    err = probe_writable(parent)
    if err:
        return "这个目录不可写，换一个地方：" + err
    if os.path.exists(p):
        bad = looks_like_db(p)
        if bad:
            return bad
    return None


# ---------- 网络位置预检：断链的网络盘**别去碰** ----------
#
# 背景（本机实测 2026-09-30）：N:/P:/S:/Z: 都是 `\\<NAS 名>\...` 的映射且**已断开**。
# `net use` 列出来是毫秒级的，但**第一次真的去访问** S:\ 会触发 Windows 重连，
# 实测 `os.path.isdir("S:\\")` 等了 **16.5 秒**（之后同一进程里才被缓存成"不可用"）。
# 于是"默认库在 S 盘、NAS 没开"时，启动要白等十几秒才轮到引导页。
#
# 对策：启动前先用**毫秒级的本地查询 + TCP 探针**问一句"这台机器活着吗"，
# 死了就根本别去碰那个路径 —— 直接进引导页（那里有「重试」）。
# 两个 API 都不产生网络 I/O：GetDriveTypeW 读盘符类型，WNetGetConnectionW 读 MPR
# 里那张"盘符 → 共享"的表（实测 6 毫秒，断链的映射照样查得到）。

DRIVE_REMOTE = 4


def _win_drive_type(root: str) -> int:
    try:
        import ctypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        k.GetDriveTypeW.restype = ctypes.c_uint
        return int(k.GetDriveTypeW(str(root)))
    except Exception:
        return 0


def _win_unc(root: str) -> str:
    """盘符对应的 UNC：`S:` → `\\<NAS 名>\\<共享名>`。查不到返回空串。"""
    try:
        import ctypes
        from ctypes import wintypes

        mpr = ctypes.WinDLL("mpr", use_last_error=True)
        mpr.WNetGetConnectionW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        mpr.WNetGetConnectionW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if mpr.WNetGetConnectionW(str(root)[:2], buf, ctypes.byref(n)) != 0:
            return ""
        return buf.value or ""
    except Exception:
        return ""


def net_host(path: str) -> str:
    """这个路径落在哪台**网络主机**上？本地路径返回空串。

    只认两种写法：UNC（`\\\\主机\\共享\\...`）和一映射到 UNC 的盘符。
    全程不产生网络流量（`os.path.abspath` 只做字符串规范化）。
    """
    p = str(path or "").strip().strip('"')
    if not p:
        return ""
    if p.startswith("\\\\"):
        parts = [x for x in p[2:].split("\\") if x]
        return parts[0] if parts else ""
    try:
        drive = os.path.splitdrive(os.path.abspath(p))[0]
    except Exception:
        return ""
    if not drive:
        return ""
    if _win_drive_type(drive + "\\") != DRIVE_REMOTE:
        return ""
    unc = _win_unc(drive + "\\")
    if not unc.startswith("\\\\"):
        return ""
    parts = [x for x in unc[2:].split("\\") if x]
    return parts[0] if parts else ""


def host_reachable(host: str, timeout: float = 0.7, budget: float = 2.5) -> bool:
    """那台机器的 SMB（445）端口现在应答吗？

    只连端口、不读数据，也**不碰任何路径** —— 所以不会触发驱动器重连。
    `budget` 是整体上限（DNS 解析卡住时也算连不上）：宁可误判成"不可用"
    也不要让启动卡在那儿，反正引导页上有「重试」。
    """
    h = str(host or "").strip()
    if not h:
        return True
    out: dict = {}

    def probe() -> None:
        import socket

        try:
            infos = socket.getaddrinfo(h, 445, 0, socket.SOCK_STREAM)
        except Exception:
            out["ok"] = False
            return
        for fam, typ, proto, _cn, sa in infos:
            s = None
            try:
                s = socket.socket(fam, typ, proto)
                s.settimeout(timeout)
                s.connect(sa)
                out["ok"] = True
                return
            except Exception:
                continue
            finally:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
        out["ok"] = False

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(budget)
    return bool(out.get("ok"))


def preflight(path: str) -> str:
    """启动 / 改数据文件前先问一句：这个位置现在能碰吗？

    **空串 = 可以试**（本地路径，或者那台网络主机应答了）；
    否则返回一句给用户看的理由 —— 拿到理由就别再碰这个路径了，
    因为碰下去的代价是等十几秒（见本节顶部）。

    只探测、不缓存也不改配置：NAS 刚开机时这里会判"不可用"，
    用户在引导页点一次「重试」就能过。
    """
    host = net_host(path)
    if not host:
        return ""
    if host_reachable(host):
        return ""
    return f"网络位置 {host} 现在连不上（SMB 没应答）"


def web_index() -> str:
    return os.path.join(res_dir(), "web", "index.html")


# ---------- 图标（标题栏 / 任务栏 / exe / 托盘） ----------
#
# 三个落点用的其实是**不同**的东西，别混：
#   · 窗口标题栏 + 任务栏 → pywebview 的 `webview.start(icon=...)`，
#     它在 winforms 里走 `System.Drawing.Icon(路径)`，**只认 .ico**，给 png 会抛
#   · exe 文件本身的图标 → PyInstaller `--icon`，编译期就塞进资源段了，运行时改不了
#   · 托盘 → pystray 走 PIL，ico/png 都吃
# 两者都由 `tools\make_icon.py` 生成，改形只需改那一处再重跑。

def icon_file() -> str:
    """多尺寸 ico（16→256）。给窗口 / exe / 托盘用。"""
    return os.path.join(res_dir(), "res", "icon.ico")


def logo_png() -> str:
    """256×256 png，给需要位图的地方（网页 favicon、自检贴图）。"""
    return os.path.join(res_dir(), "res", "icon.png")


def logo_svg() -> str:
    """矢量母版（512 网格）。**它自己就是唯一来源**（Inkscape 做的图），
    ico / png 都由 `tools\\make_icon.py` 从它现场光栅化出来。"""
    return os.path.join(res_dir(), "res", "logo.svg")


def smoke_note() -> str:
    """`--smoke` 的回传落点。源码下仍在 `tests/`，打包后落在 exe 同级 `tests/`。"""
    d = os.path.join(base_dir(), "tests")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "smoke_note.txt")


def restart_cmd() -> list[str]:
    """降级重启要用什么命令启动自己（看门狗用）。

    打包后就是 exe 本身；源码下是 `python + _boot.py`（走兜底日志那一层）。
    注意：打包后**不能**再拿 `sys.executable` 加 `_boot.py` 拼命令行 ——
    `_boot.py` 根本不在包里，exe 会把它当成普通参数丢给 argparse。
    """
    if is_frozen():
        return [sys.executable]
    return [sys.executable, os.path.join(res_dir(), "_boot.py")]


def start_cmd() -> str:
    """开机自启写进注册表 Run 键的命令行。"""
    if is_frozen():
        return f'"{os.path.abspath(sys.executable)}"'
    exe_dir = os.path.dirname(sys.executable)
    pyw = os.path.join(exe_dir, "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{os.path.join(res_dir(), "main.py")}"'


def describe() -> dict:
    """给设置页 / 诊断用：一条一眼能看出跑在哪种模式的信息。"""
    d = {
        "frozen": is_frozen(),
        "res": res_dir(),
        "base": base_dir(),
        "data": data_dir(),
    }
    # 通知身份回读（冒烟断言用）：真问系统当前进程的 AUMID，不是报常量 ——
    # 这样"设置了"和"设置成功"是同一件事。
    # ⚠ c_wchar_p 在 ctypes 顶层，wintypes 里没有这个别名（别改过去）。
    # 返回的 PWSTR 由 CoTaskMemAlloc 分配，这里是一次性诊断读取，泄漏可忽略。
    try:
        import ctypes

        get_id = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
        get_id.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
        get_id.restype = ctypes.HRESULT
        buf = ctypes.c_wchar_p()
        if get_id(ctypes.byref(buf)) == 0 and buf.value:
            d["aumid"] = buf.value
        else:
            d["aumid"] = ""
    except Exception:
        d["aumid"] = ""
    return d
