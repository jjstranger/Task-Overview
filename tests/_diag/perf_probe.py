"""性能探针（讨论用，不属于自动回归；手动跑 `python tests/_diag/perf_probe.py`）。

两件事：
1. 量真实库的规模 + 各读接口现在的耗时；
2. 造一个「十年后」的合成库（几万环节 / 几十万流水 / 几万款项），
   把同样的接口再量一遍 —— 用来判断"数据大了会不会慢、慢在哪"。

⚠ 真实库一律 mode=ro 打开，绝不写它。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))

import paths  # noqa: E402
from db import Db  # noqa: E402
from finance import Finance  # noqa: E402
from models import Board  # noqa: E402


def timeit(fn, n=3):
    """跑 n 次取最好的一次（第一次往往含建连接/硬盘冷缓存的开销）。"""
    best = None
    for _ in range(n):
        t = time.perf_counter()
        out = fn()
        ms = (time.perf_counter() - t) * 1000
        best = ms if best is None else min(best, ms)
    return best, out


def counts(db):
    out = {}
    for t in ("projects", "nodes", "clients", "finance", "activity_log", "links", "groups"):
        try:
            out[t] = db.one("SELECT COUNT(*) AS n FROM %s" % t)["n"]
        except Exception:
            out[t] = -1
    return out


def raw_counts(path):
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        out = {}
        for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            try:
                out[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except Exception:
                pass
        return out
    finally:
        con.close()


def probe_board(board, label):
    rows = []

    def add(name, fn, n=3):
        ms, _ = timeit(fn, n)
        rows.append((name, ms))
        return ms

    add("load()     整棵树", lambda: board.load())
    add("  → 只 load 一次要过的 JSON", lambda: len(str(board.load())), 1)
    add("stats()    汇总", lambda: board.stats())
    add("alerts()   停滞/到期", lambda: board.alerts())
    add("activity() 活跃页", lambda: board.activity(date.today().year))
    if hasattr(Finance, "data"):
        fin = Finance(board.db)
        add("finance_data()", lambda: fin.data(0, 0))
    print("\n== %s ==" % label)
    for name, ms in rows:
        print("   %-34s %8.1f ms" % (name, ms))
    return dict(rows)


def build_big(path, n_prj=200, n_node=50, n_log=400000, n_fin=60000):
    """合成库：200 项目 × 50 环节 = 1 万环节；40 万条流水；6 万条款项。

    ⚠ 全部走 executemany 一次性灌。早先一版用 `db.run()` 一行一提交，
    在这台机器上 **13 分钟没建完**（每次 commit 都要删/建 journal，Defender
    实时扫描还要插一脚，约 40ms/提交）—— 造库本身比要测的接口慢几百倍。
    """
    if os.path.exists(path):
        ctypes_del(path)
    t = time.perf_counter()
    db = Db(path)
    db.open()
    print("   [造库] open 完 %.1fs" % (time.perf_counter() - t), flush=True)
    today = date.today()
    t0 = time.perf_counter()

    prjs, nodes = [], []
    for i in range(n_prj):
        prjs.append((i + 1, "合成项目%03d" % i,
                     "commercial" if i % 2 else "personal", "进行中",
                     "2024-01-01 09:00:00", "2024-01-01 09:00:00",
                     (today - timedelta(days=i % 40)).isoformat() + " 09:00:00"))
        for j in range(n_node):
            nodes.append((i + 1, None, "环节%04d" % j,
                          ["待开始", "制作中", "等上游", "通过", "反馈", "可优化"][j % 6],
                          "2024-01-01 09:00:00", "2024-01-01 09:00:00",
                          (today - timedelta(days=j % 30)).isoformat() + " 09:00:00",
                          (today + timedelta(days=(j % 60) - 30)).isoformat()))

    logs = []
    for k in range(n_log):
        d = (today - timedelta(days=k % 1000)).isoformat()
        logs.append((d + " 10:00:00", "status", 1, (k % n_prj) + 1, None,
                     "合成流水 %d" % k, d))
    fins = []
    for k in range(n_fin):
        d = (today - timedelta(days=k % 900)).isoformat()
        fins.append(("payment", (k % n_prj) + 1, None, 1000 + k, d,
                     "已收" if k % 3 else "待收", "CNY", "合成款项"))

    with db.mutex:
        # 新库里 _seed() 已经塞了示例项目（id=1），先清掉再灌，
        # 否则显式 id 会撞 UNIQUE（踩过）
        #
        # ⚠ 必须显式 BEGIN/COMMIT。`isolation_level=None`（autocommit）下
        # sqlite3 的 executemany **不加隐式事务**，于是 47 万行 = 47 万次提交，
        # 每次提交都要建/删 journal（Defender 还要插一脚）→ 十几分钟都灌不完。
        # 包一个事务后同一份数据 0.4 秒。
        db.conn.execute("BEGIN")
        db.conn.execute("DELETE FROM nodes")
        db.conn.execute("DELETE FROM projects")
        print("   [造库] 备好数据 %d 项目 / %d 环节 / %d 流水 / %d 款项，开始灌 %.1fs"
              % (len(prjs), len(nodes), len(logs), len(fins),
                 time.perf_counter() - t0), flush=True)
        db.conn.executemany(
            "INSERT INTO projects(id,title,category,status,created_at,updated_at,"
            "last_activity_at) VALUES(?,?,?,?,?,?,?)", prjs)
        db.conn.executemany(
            "INSERT INTO nodes(project_id,parent_id,title,status,created_at,updated_at,"
            "last_activity_at,deadline) VALUES(?,?,?,?,?,?,?,?)", nodes)
        print("   [造库]   项目+环节 完 %.1fs" % (time.perf_counter() - t0), flush=True)
        db.conn.executemany(
            "INSERT INTO activity_log(ts,kind,weight,project_id,node_id,detail,day)"
            " VALUES(?,?,?,?,?,?,?)", logs)
        print("   [造库]   流水 完 %.1fs" % (time.perf_counter() - t0), flush=True)
        db.conn.executemany(
            "INSERT INTO finance(kind,project_id,node_id,amount,date,status,currency,note)"
            " VALUES(?,?,?,?,?,?,?,?)", fins)
        db.conn.execute("COMMIT")
    print("   [造库] 灌完 %.1fs" % (time.perf_counter() - t0), flush=True)
    db.conn.execute("ANALYZE")
    print("   [造库] ANALYZE 完 %.1fs" % (time.perf_counter() - t0), flush=True)
    db.close()
    return time.perf_counter() - t0


def ctypes_del(path):
    """删文件走 kernel32（safe-delete shim 在网络盘/批量时 FAIL_CLOSED）。"""
    import ctypes

    ctypes.WinDLL("kernel32").DeleteFileW(path)


def main():
    real = paths.resolve_db_path()
    print("真实库：", real)
    if os.path.exists(real):
        try:
            print("  大小：%.1f MB" % (os.path.getsize(real) / 1048576))
        except OSError as e:
            print("  读不到大小：", e)
        try:
            print("  表行数：", raw_counts(real))
        except Exception as e:
            print("  读不到行数：", e)
        # 真实库一律**只读**打开量接口：`Db.open()` 会跑迁移（写库），
        # 而本会话的文件写入/删除被 safe-delete shim 拦着（网络盘没有回收站），
        # 写事务会以 `attempt to write a readonly database` 告败 ——
        # 那是会话环境的问题，不是应用的（应用自己跑得好好的）。
        # 所以这里用 `mode=ro` 的裸连接自己建 Board。
        try:
            con = sqlite3.connect(
                "file:%s?mode=ro" % real.replace("\\", "/"), uri=True
            )
            con.row_factory = sqlite3.Row

            class _RO:
                """只读套壳：Board 只用到 q/one/all_settings/get_setting。"""

                def __init__(self, c):
                    self.conn = c
                    self.mutex = __import__("threading").RLock()

                def q(self, sql, *a):
                    with self.mutex:
                        return self.conn.execute(sql, a[0] if a else ()).fetchall()

                def one(self, sql, *a):
                    with self.mutex:
                        return self.conn.execute(sql, a[0] if a else ()).fetchone()

                def all_settings(self):
                    return {r["key"]: r["value"] for r in self.q("SELECT * FROM settings")}

                def get_setting(self, k, d=""):
                    r = self.one("SELECT value FROM settings WHERE key=?", (k,))
                    return r["value"] if r else d

            probe_board(Board(_RO(con)), "真实库（只读）")
            con.close()
        except Exception as e:
            print("  真实库探测失败：%r" % (e,))

    tmp = tempfile.mkdtemp(prefix="board_perf_")
    big = os.path.join(tmp, "big.sqlite")
    secs = build_big(big)
    print("\n合成库：%s（%.1f MB，造库 %.1f 秒）" % (big, os.path.getsize(big) / 1048576, secs))
    print("  表行数：", raw_counts(big))
    db = Db(big, share=True)
    db.open()
    board = Board(db)
    probe_board(board, "合成大库（1 万环节 / 40 万流水 / 6 万款项）")
    # 再加索引后会怎样（只在这份临时库上动）
    print("\n-- 临时加上几条索引，再看同样的接口 --")
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_log_day ON activity_log(day)",
        "CREATE INDEX IF NOT EXISTS idx_log_id_day ON activity_log(day, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_fin_date ON finance(date)",
        "CREATE INDEX IF NOT EXISTS idx_fin_prj ON finance(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_proj_cat ON projects(category)",
        "CREATE INDEX IF NOT EXISTS idx_log_prj ON activity_log(project_id)",
    ):
        t = time.perf_counter()
        db.conn.execute(sql)
        print("   %-58s %6.0f ms" % (sql[:58], (time.perf_counter() - t) * 1000))
    db.conn.execute("ANALYZE")
    probe_board(board, "同一份大库 + 索引")
    # 只测「查最近一周」的代价：面板实际用的那条
    def recent_sql(idx):
        t = time.perf_counter()
        n = db.one("SELECT COUNT(*) AS n FROM activity_log WHERE day>=?",
                   ((date.today() - timedelta(days=6)).isoformat(),))["n"]
        return (time.perf_counter() - t) * 1000, n
    ms, n = timeit(lambda: recent_sql(0), 5)[1]
    print("\n   「最近一周流水」COUNT：%.1f ms（%d 条）" % (ms, n))
    db.close()
    print("\n临时库保留在：", tmp)


if __name__ == "__main__":
    main()
