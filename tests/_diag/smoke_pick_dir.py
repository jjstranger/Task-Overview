"""真开一次「选择文件夹」对话框，验证 pick_dir 能弹出来、也能正常返回。

================================================================
⚠️ 这个脚本**不属于日常回归**，跑它一定会弹出一个 Windows 文件夹对话框
   （约 2 秒后自动关掉）。它只在"点浏览没反应"这类问题冒出来时才手动跑一次。
   想跑全量回归请用 smoke_fin_gui.py + smoke_exe.py，那两个不会弹任何系统窗口。
================================================================

为什么值得单独测：pick_dir 原来调的是 pywebview 6.x 里**已经删掉**的
`webview.create_file_dialog`，抛 AttributeError 又被吞成 `ok: False`，
前端当时也不看返回值 —— 表现就是「点浏览没反应」。这类失败纯 Python 单测测不到，
只有真的弹一次才知道。

对话框是模态的，没人点它会一直挂着，所以这里：
1. 起一个线程去 EnumWindows 找它（class = #32770 且属于本进程）
2. 等它出现后再等 1.5 秒，发 WM_CLOSE（等价于用户点「取消」）
3. 断言 **对话框确实出现过**，且 pick_dir 返回 `{"ok": True, "path": ""}`

跑法（会短暂弹一个系统对话框，2 秒后自动关掉）：
    ... python tests\\_diag\\smoke_pick_dir.py
    ... python tests\\_diag\\smoke_pick_dir.py fallback   # 关沙箱（本机日常档）
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

LOG = os.path.join(HERE, "_pick_dir.log")
MODE = (sys.argv[1] if len(sys.argv) > 1 else "normal").lower()

# WebView2 的环境变量必须在 import webview 之前设好。
# profile 用独立目录，才能和用户正开着的看板同时跑（一个 profile 只能一个环境用）。
os.environ["WEBVIEW2_USER_DATA_FOLDER"] = os.path.join(ROOT, "data", "webview2_smoke")
if MODE == "fallback":
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--no-sandbox"

import webview  # noqa: E402

from api import Api  # noqa: E402
from db import Db  # noqa: E402
from models import Board  # noqa: E402

WM_CLOSE = 0x0010
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_ESCAPE = 0x1B

_out = {"mode": MODE, "dialog_seen": False, "hwnds": [], "result": None, "note": ""}
_lines: list[str] = []


def say(*a):
    line = " ".join(str(x) for x in a)
    _lines.append(line)
    try:
        print(line)
    except Exception:
        pass


# ---------------------------------------------------------------- 找对话框

def _pid_windows():
    """本进程里所有可见的对话框窗口（#32770）。"""
    found = []
    user32 = ctypes.windll.user32
    me = os.getpid()
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _l):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != me:
            return True
        buf = ctypes.create_unicode_buffer(120)
        user32.GetClassNameW(hwnd, buf, 120)
        if buf.value == "#32770" and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(PROC(cb), 0)
    return found


def _dialogs_forever(seen, stop):
    """盯 12 秒：一旦出现对话框就等它稳定后发关闭消息。"""
    end = time.time() + 12
    while time.time() < end and not stop.is_set():
        hs = _pid_windows()
        if hs:
            seen["hwnds"] = hs
            seen["t"] = time.time()
            time.sleep(1.5)                     # 让它真正画出来再关
            seen["hwnds2"] = _pid_windows()
            for h in (seen["hwnds2"] or hs):
                ctypes.windll.user32.PostMessageW(h, WM_CLOSE, 0, 0)
            time.sleep(1.0)
            left = _pid_windows()
            if left:                            # WM_CLOSE 不认就补一发 Esc
                for h in left:
                    ctypes.windll.user32.PostMessageW(h, WM_KEYDOWN, VK_ESCAPE, 0)
                    ctypes.windll.user32.PostMessageW(h, WM_KEYUP, VK_ESCAPE, 0)
            return
        time.sleep(0.12)


def _watchdog():
    """兜底：真挂住了也别把调用方一直吊着。"""
    time.sleep(45)
    say("watchdog: 45 秒还没结束，强制退出")
    if not os.path.exists(LOG):
        _dump(9)
    os._exit(9)


def _dump(code: int = 0) -> None:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
        f.write(json.dumps(_out, ensure_ascii=False, indent=1) + "\n")
        f.write(f"exit={code}\n")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    threading.Thread(target=_watchdog, daemon=True).start()

    tmp = tempfile.mkdtemp(prefix="board_pick_")
    db = Db(os.path.join(tmp, "board.sqlite"))
    db.open()
    state: dict = {}
    api = Api(Board(db), db, state)

    win = webview.create_window(
        "pick-dir 冒烟", html="<html><body style='font:13px sans-serif'>pick dir</body></html>",
        js_api=api, width=460, height=200,
    )
    state["window"] = win

    seen: dict = {}
    result: dict = {}

    def worker():
        stop = threading.Event()
        watcher = threading.Thread(target=_dialogs_forever, args=(seen, stop), daemon=True)
        watcher.start()

        # 和真实调用一样：js_api 的回调跑在独立线程上，不是 GUI 线程
        def invoke():
            t0 = time.time()
            try:
                result["r"] = api.pick_dir()
            except Exception as exc:                      # pragma: no cover
                result["err"] = repr(exc)
            result["ms"] = int((time.time() - t0) * 1000)

        th = threading.Thread(target=invoke, daemon=True)
        th.start()
        th.join(20)
        stop.set()
        result["thread_done"] = not th.is_alive()

        _out["dialog_seen"] = bool(seen.get("hwnds"))
        _out["hwnds"] = len(seen.get("hwnds") or [])
        _out["result"] = result.get("r")
        _out["err"] = result.get("err")
        _out["ms"] = result.get("ms")
        _out["thread_done"] = result.get("thread_done")

        ok = bool(seen.get("hwnds")) and isinstance(result.get("r"), dict) \
            and result["r"].get("ok") is True and result["r"].get("path") == ""
        say("对话框出现过 :", bool(seen.get("hwnds")))
        say("pick_dir 结果 :", json.dumps(result, ensure_ascii=False))
        say("PICK SMOKE:", "OK" if ok else "FAILED")
        _out["ok"] = ok
        _dump(0 if ok else 1)
        try:
            db.close()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    webview.start(worker, debug=False)
    return 0 if _out.get("ok") else 1


if __name__ == "__main__":
    try:
        code = main()
    except BaseException as exc:                              # pragma: no cover
        say("异常:", repr(exc))
        _dump(3)
        code = 3
    sys.exit(code)
