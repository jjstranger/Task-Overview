"""验证自动降级链路：双击 run.bat -> 桥不通 -> 看门狗带 --no-sandbox 重启 -> 可用。

这是用户真实走的路径，必须端到端过一遍。全程不给任何提示性环境变量。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BAT = os.path.join(ROOT, "run.bat")
BOOT_LOG = os.path.join(ROOT, "data", "boot.log")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_fallback.log")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app"))
from version import TITLE  # noqa: E402  标题栏改名的唯一来源
u = ctypes.windll.user32


class _Tee:
    """控制台输出会被调用方按 GBK 解码，中文会乱；同时落一份 UTF-8 日志。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


sys.stdout = _Tee(sys.stdout, open(LOG, "w", encoding="utf-8"))


def pids():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV"],
                       capture_output=True)
    out = []
    for ln in r.stdout.decode("gbk", "replace").splitlines()[1:]:
        c = [x.strip('"') for x in ln.split('","')]
        if len(c) >= 2 and c[1].isdigit():
            out.append(int(c[1]))
    return sorted(out)


def log_size():
    return os.path.getsize(BOOT_LOG) if os.path.exists(BOOT_LOG) else 0


def tail_log():
    if not os.path.exists(BOOT_LOG):
        return "(no boot.log)"
    return open(BOOT_LOG, encoding="utf-8").read().strip().splitlines()[-3:]


BASE = set(pids())          # 开跑前就在的 pythonw：用户可能正开着看板，全程不碰
if u.FindWindowW(None, TITLE):
    print("检测到看板正在运行 —— 请先退出看板再跑这个回归。")
    print("  · 同时开着会撞同一个 WebView2 用户数据目录（新进程报 0x8007139F 起不来），")
    print("    而且两边都会走降级看门狗，日志互相污染，结论不可信")
    sys.exit(2)

tmp = tempfile.mkdtemp(prefix="relaunch_")
db_path = os.path.join(tmp, "board.sqlite")
env = os.environ.copy()
env.pop("PYTHONPATH", None)
# 关键：不给 BOARD_NOSANDBOX，也不给 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
env.pop("BOARD_NOSANDBOX", None)
env.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
env["BOARD_DB"] = db_path
# 真实双击时用的是 app 目录下这套 profile，这里保持一致（上面已确认没有实例占着它）
env.pop("WEBVIEW2_USER_DATA_FOLDER", None)

before = log_size()
t0 = time.time()
subprocess.Popen(["cmd", "/c", BAT], cwd=ROOT, env=env)
print("启动（正常模式），等看门狗判定…")

gen1 = set()
for i in range(120):
    time.sleep(1)
    now = set(pids())
    if not gen1 and now:
        gen1 = now
        print(f"  t={i+1:3d}s 第一批进程 {sorted(gen1)}")
    if gen1 and now - gen1:
        print(f"  t={i+1:3d}s 出现新进程 {sorted(now - gen1)} -> 触发降级重启")
        break
    if now - gen1 == set() and i > 50 and now:
        pass

print()
print("boot.log 新增内容:")
for ln in tail_log():
    print("   ", ln)
print()
print("窗口存在:", bool(u.FindWindowW(None, TITLE)))
print("活跃 pythonw:", pids())
print("总耗时 %.1fs" % (time.time() - t0))

# 降级重启后应该把 nosandbox=1 落到库里，下次启动就不用再等一遍超时
time.sleep(4)
try:
    import sqlite3

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT value FROM settings WHERE key='nosandbox'").fetchone()
    con.close()
    val = row[0] if row else "(没有这个键)"
except Exception as exc:
    val = "读取失败: %r" % (exc,)
print("库里 nosandbox =", val)
print("判定:", "PASS 降级链路 + 记忆都成立" if val == "1" else "FAIL 没把降级结果记住")

# 只杀本测试自己起的进程：别用 taskkill /IM pythonw.exe，那会把用户开着的看板一起带走
for p in sorted(set(pids()) - BASE):
    subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True)
