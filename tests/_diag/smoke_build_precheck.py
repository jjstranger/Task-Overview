# -*- coding: utf-8 -*-
"""build_exe.py 的「旧产物被占用」预检回归（非日常，改 build_exe.py 时跑）。

三层覆盖：
  A. `scan_locked` 纯逻辑 —— 不存在 / 空目录 / 无占用 / 被占用 / 释放后
  B. 端到端 —— 真造一个占用，build_exe.py 必须 exit 3，且**不许跑 PyInstaller**
  C. 目标不存在（首次构建 / --outdir 换目录）→ 跳过预检直接进构建

⚠ 为什么 B 不能省：防御性代码最典型的失败不是崩，而是**静默失效** ——
   判据写错（比如 HANDLE 用 c_int 被截断）就会永远回"没被占用"，
   预检看着一切正常，[3/4] 照样白复制 51 MB 再报 err 32。
   所以这里不打桩，而是真起一个进程、按 Windows loader 的方式
   （FILE_SHARE_READ|FILE_SHARE_DELETE）持住 exe。

跑法：python tests/_diag/smoke_build_precheck.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable

_ok = 0
_fail = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print("  PASS  " + name)
    else:
        _fail += 1
        print("  FAIL  " + name + (("   <- " + extra) if extra else ""))


# 持有者：模拟 Windows 加载 PE 映像后的句柄（读 + 允许别人读/删）
HOLD_SRC = r'''# -*- coding: utf-8 -*-
import ctypes, sys, time
from ctypes import wintypes
GR = 0x80000000
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k.CreateFileW.restype = ctypes.c_void_p
h = k.CreateFileW(sys.argv[1], GR, 1 | 4, None, 3, 0x80, None)
if not h or h == 0xFFFFFFFFFFFFFFFF:
    print("HOLD-FAILED", ctypes.get_last_error())
    sys.exit(1)
open(sys.argv[2], "w").write("1")
time.sleep(120)
'''


def _spawn_holder(hold_py: str, target: str, ready: str) -> subprocess.Popen:
    proc = subprocess.Popen([PY, hold_py, target, ready],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(150):
        if os.path.exists(ready):
            break
        time.sleep(0.1)
    time.sleep(0.2)
    return proc


def _release(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait()
    except OSError:
        pass


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "build_exe", os.path.join(PROJ, "tools", "build_exe.py"))
    be = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(be)

    base = tempfile.mkdtemp(prefix="precheck_")
    hold_py = os.path.join(base, "_hold.py")
    with open(hold_py, "w", encoding="utf-8") as f:
        f.write(HOLD_SRC)

    # ---------------- A 纯逻辑 ----------------
    print("=== A scan_locked 纯逻辑 ===")
    chk("不存在的路径 -> 无占用", be.scan_locked(os.path.join(base, "nope")) == [])

    d_empty = os.path.join(base, "empty")
    os.makedirs(d_empty)
    chk("空目录 -> 无占用", be.scan_locked(d_empty) == [])

    d_free = os.path.join(base, "free")
    os.makedirs(d_free)
    with open(os.path.join(d_free, "天在看.exe"), "wb") as f:
        f.write(b"MZ" + b"\0" * 1024)
    chk("普通文件 -> 无占用", be.scan_locked(d_free) == [])

    d_held = os.path.join(base, "held")
    os.makedirs(d_held)
    p_held = os.path.join(d_held, "天在看.exe")
    with open(p_held, "wb") as f:
        f.write(b"MZ" + b"\0" * 1024)
    for i in range(3):
        with open(os.path.join(d_held, "other%d.dll" % i), "wb") as f:
            f.write(b"x" * 128)
    chk("真实产物未被占用（不误报，未运行看板时）",
        os.path.isdir(os.path.join(PROJ, "dist", "天在看"))
        and be.scan_locked(os.path.join(PROJ, "dist", "天在看")) == [])

    proc: subprocess.Popen | None = None
    try:
        # A2：持住假 exe
        ready = os.path.join(base, "ready_a.flag")
        proc = _spawn_holder(hold_py, p_held, ready)
        chk("持有者已就位", os.path.exists(ready), "子进程没起来")
        hits = be.scan_locked(d_held)
        chk("被持有 -> 检出占用",
            len(hits) == 1 and os.path.basename(hits[0]) == "天在看.exe",
            "hits=%r" % hits)
        chk("未被持有的文件不误报",
            all(os.path.basename(h) != "other0.dll" for h in hits))
        chk("单文件路径也探得出", be.scan_locked(p_held) == [p_held])

        _release(proc)
        proc = None
        time.sleep(0.4)
        chk("释放后 -> 不再报占用", be.scan_locked(d_held) == [])

        # ---------------- B 端到端拦截 ----------------
        print("=== B 端到端：占用时 build_exe.py 必须拦下 ===")
        exe = os.path.join(PROJ, "dist", "天在看", "天在看.exe")
        if not os.path.exists(exe):
            print("  SKIP  (dist\\天在看 不存在，先跑一次 tools/build_exe.py 再来)")
        else:
            ready_b = os.path.join(base, "ready_b.flag")
            proc = _spawn_holder(hold_py, exe, ready_b)
            chk("持有者已持住真产物", os.path.exists(ready_b), "子进程没起来")
            t0 = time.time()
            r = subprocess.run([PY, "-u", "tools/build_exe.py"], cwd=PROJ,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
            dt = time.time() - t0
            out = (r.stdout or "") + (r.stderr or "")
            chk("退出码 = 3", r.returncode == 3, "实际 %r" % r.returncode)
            chk("打出 [0/4] 预检标记", "[0/4]" in out)
            chk("点名 err 32", "err 32" in out)
            chk("给出 --force 逃生门", "--force" in out)
            chk("给出 --outdir 出路", "--outdir" in out)
            chk("没有真跑 PyInstaller（无 [1/4]）", "[1/4]" not in out)
            chk("快速返回（<15s）", dt < 15, "耗时 %.1fs" % dt)
    finally:
        _release(proc)
        proc = None
        time.sleep(0.4)

    # ---------------- C 目标不存在 -> 跳过预检 ----------------
    print("=== C 目标不存在 -> 跳过预检直接进构建 ===")
    d_probe = tempfile.mkdtemp(prefix="outdir_probe_")
    # ⚠ 必须 -u：stdout 走管道时是块缓冲，kill 时没 flush 会把"已进构建"误判成"没进"
    p = subprocess.Popen([PY, "-u", "tools/build_exe.py", "--outdir", d_probe], cwd=PROJ,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    time.sleep(5)
    p.kill()
    try:
        out3 = p.stdout.read() if p.stdout else ""
    except (ValueError, OSError):
        out3 = ""
    p.wait()
    chk("没有 [0/4] 预检标记", "[0/4]" not in out3)
    chk("直接进入 [1/4]", "[1/4]" in out3)
    chk("探测目录仍为空（[3/4] 之前不落产物）",
        os.listdir(d_probe) == [], "实际 %r" % os.listdir(d_probe))
    shutil.rmtree(d_probe, ignore_errors=True)

    print()
    print("结果: %d PASS / %d FAIL" % (_ok, _fail))
    shutil.rmtree(base, ignore_errors=True)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
