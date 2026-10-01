"""run.bat 打不开这件事的完整回归：

case1 干净启动能起来
case2 重复双击 -> 唤醒已有实例，不起第二个
case3 孤儿锁文件（pid 已死）-> 接管后照常启动
case4 进程被强杀后立即重启 -> 必须能马上打开（互斥量随进程消亡，不该等 60 秒）
case5 崩溃兜底：模拟 import 失败，确认弹出错误框且有日志
"""
import ctypes
import os
import subprocess
import sys
import time

u = ctypes.windll.user32
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app"))
from version import TITLE  # noqa: E402  标题栏改名的唯一来源
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VENV = r"~\.workbuddy\binaries\python\envs\board\Scripts"
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_locktest.sqlite")
LOCK = DB[:-7] + ".lock"
BAT = os.path.join(ROOT, "run.bat")
ERRLOG = os.path.join(ROOT, "data", "launch_error.log")

ok = lambda b: "PASS" if b else "FAIL"   # noqa: E731


def pids():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV"],
                       capture_output=True)
    out = []
    for ln in r.stdout.decode("gbk", "replace").splitlines()[1:]:
        c = [x.strip('"') for x in ln.split('","')]
        if len(c) >= 2 and c[1].isdigit():
            out.append(int(c[1]))
    return sorted(out)


BASE = set()      # 开跑前就在的 pythonw：用户可能正开着看板，全程不碰
SPAWNED = set()   # 本测试自己起的，收尾只杀这些


def spawn_snapshot():
    SPAWNED.update(set(pids()) - BASE)


def kill_spawned():
    """只杀本测试自己起的进程。

    以前这里是 `taskkill /F /IM pythonw.exe` —— 那会**把你正开着的看板一起杀掉**。
    """
    for p in sorted(SPAWNED):
        subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True)
    SPAWNED.clear()
    time.sleep(0.8)


def cleanup():
    kill_spawned()
    time.sleep(1.2)
    for f in (DB, LOCK, DB + "-journal"):
        if os.path.exists(f):
            os.unlink(f)


def boot(timeout=20):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["BOARD_DB"] = DB
    subprocess.Popen(["cmd", "/c", BAT], cwd=ROOT, env=env)
    for _ in range(timeout * 2):
        time.sleep(0.5)
        spawn_snapshot()
        if u.FindWindowW(None, TITLE):
            return True
    return False


BASE.update(pids())
if u.FindWindowW(None, TITLE):
    print("检测到看板正在运行 —— 请先退出看板再跑这个回归。")
    print("  · 同时开着会撞同一个 WebView2 用户数据目录，新进程报 0x8007139F 根本起不来，")
    print("    看起来像'代码坏了'，其实是环境撞车")
    raise SystemExit(2)


print("== case1 干净启动 ==")
cleanup()
r = boot()
print("  ", ok(r), "window up:", r)

print("== case2 重复双击应唤醒而非新起一个 ==")
before = pids()
boot(timeout=8)
time.sleep(6)
after = pids()
print("  ", ok(not set(after) - set(before)), "new pids:", sorted(set(after) - set(before)))
print("   ", ok(bool(after)), "instance still alive")

print("== case3 孤儿锁文件（pid 已死）应被接管 ==")
cleanup()
import socket  # noqa: E402
with open(LOCK, "w", encoding="utf-8") as f:
    f.write(f"{socket.gethostname()}:999999|{time.time()}")
r = boot()
print("  ", ok(r), "window up:", r)

print("== case4 强杀后立刻双击，应能马上打开 ==")
kill_spawned()
time.sleep(1.5)
print("   stale lock still there:", os.path.exists(LOCK))
t0 = time.time()
r = boot(timeout=25)
print("  ", ok(r), f"window up in {time.time()-t0:.1f}s")

print("== case5 崩溃兜底：任何异常都要留下证据，不能静默消失 ==")
cleanup()
n = len(open(ERRLOG, encoding="utf-8").read()) if os.path.exists(ERRLOG) else 0
code = (
    "import sys; sys.path.insert(0, r'%s');"
    "import _boot; _boot.fatal('boom-test from verify_run')"
) % os.path.join(ROOT, "app")
try:
    # fatal 会弹 MessageBox 阻塞，所以给个超时后杀掉
    subprocess.run([os.path.join(VENV, "pythonw.exe"), "-c", code],
                   capture_output=True, timeout=8)
except subprocess.TimeoutExpired:
    pass
spawn_snapshot()
kill_spawned()
grown = os.path.exists(ERRLOG) and len(open(ERRLOG, encoding="utf-8").read()) > n
print("  ", ok(grown), "crash log:", ERRLOG if grown else "(no growth)")

cleanup()
print("\nall cases done")
