"""真开一次系统「另存为」对话框，验证财务导出「挑位置 / 挑文件名」那条路。

================================================================
⚠️ 这个脚本**不属于日常回归**：跑它一定会弹出一个 Windows 保存对话框
   （约 2 秒后自动关掉 = 取消）。日常回归用 smoke_export.py（数据层）+
   smoke_fin_gui.py / smoke_exe.py（界面），那三个都不弹任何系统窗口。
================================================================

为什么要单独测：`finance_export_save` 的两半，纯 Python 单测都够不着 ——

  A. 「把文件写到用户挑的地方」得绕开对话框才测得了 → 这里 monkeypatch 掉
     `_pick`，分别喂「给了路径但没扩展名」「返回空串（取消）」「ok:False（失败）」；
  B. 「对话框真的弹出来、默认文件名真的填进去了」只能真弹一次看 ——
     "点导出没反应"这类 bug 全在这条路上（`smoke_pick_dir.py` 的教训：
     `create_file_dialog` 被挪走那次，单元测试一路全绿，界面点了毫无反应）。

跑法（会短暂弹一个系统对话框，2 秒后自动关掉）：
    ... python tests\\_diag\\smoke_export_save.py
    ... python tests\\_diag\\smoke_export_save.py fallback   # 本机日常档
"""
from __future__ import annotations

import csv
import ctypes
import json
import os
import sys
import tempfile
import threading
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

LOG = os.path.join(HERE, "_export_save.log")
MODE = (sys.argv[1] if len(sys.argv) > 1 else "normal").lower()

# ⚠ WebView2 的环境变量必须在 import webview 之前设好；profile 用独立目录，
# 才能和用户正开着的看板同时存在（同一个用户数据目录不能被两个环境同时用，
# 撞上的表现是建环境时直接 0x8007139F，看着像"页面起不来"）。
# ⚠ 也别换成"刚新建的目录"：这一版实测过 —— 新建的 data/webview2_expsave 会让
# WebView2 直接 E_ABORT(0x80004004) 起不来，而沿用已有的 webview2_smoke 就正常。
# 所以这里跟 smoke_fin_gui.py / smoke_pick_dir.py 共用同一个 profile，**必须串行跑**。
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

FAILS: list[str] = []
N = [0]
_out: dict = {"mode": MODE}
_lines: list[str] = []
T0 = time.time()


def _tr(msg):
    """时间线。窗口起不来时，"谁在什么时候把窗口拆了"只有这条线看得出来
       （实测遇到过一次 0x80004004 E_ABORT：InitCoreWebView2Async 还在跑，
       窗口已经被 destroy 了 —— 光看异常堆栈完全指不到 destroy 那一侧）。"""
    _lines.append("[t=%.2f] %s" % (time.time() - T0, msg))


def chk(name, cond):
    N[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def say(*a):
    line = " ".join(str(x) for x in a)
    _lines.append(line)
    try:
        print(line)
    except Exception:
        pass


# ---------------------------------------------------------------- Win32（显式声明签名）
# ⚠ ctypes 一定要写 argtypes/restype：不写的话 64 位 HWND 会被当成 int 截断，
# 拿不到窗口却什么都不报（这个项目在「置顶」上踩过同一个坑）。
_u32 = ctypes.windll.user32
_ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_ENUM, wintypes.LPARAM]
_u32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUM, wintypes.LPARAM]
_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextLengthW.restype = ctypes.c_int
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def _cls(hwnd) -> str:
    b = ctypes.create_unicode_buffer(120)
    _u32.GetClassNameW(hwnd, b, 120)
    return b.value


def _pid_windows():
    """本进程里所有可见的对话框窗口（#32770）。"""
    found = []
    me = os.getpid()

    def cb(hwnd, _l):
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != me:
            return True
        if _cls(hwnd) == "#32770" and _u32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    _u32.EnumWindows(_ENUM(cb), 0)
    return found


def _child_texts(hwnd):
    """对话框里所有子控件的文字（文件名框也在里面）。"""
    texts = []

    def cb(h, _l):
        n = _u32.GetWindowTextLengthW(h)
        if n > 0:
            b = ctypes.create_unicode_buffer(n + 2)
            _u32.GetWindowTextW(h, b, n + 2)
            if b.value:
                texts.append(b.value)
        return True

    _u32.EnumChildWindows(hwnd, _ENUM(cb), 0)
    return texts


def _dialogs_forever(seen, stop):
    """盯 15 秒：对话框一出现就读它的文件名框，等它画稳了再关掉（= 用户取消）。"""
    end = time.time() + 15
    while time.time() < end and not stop.is_set():
        hs = _pid_windows()
        if hs:
            seen["hwnds"] = hs
            time.sleep(2.0)                     # 让它把默认文件名画上去再读
            seen["texts"] = _child_texts(hs[0])
            for h in (_pid_windows() or hs):
                _u32.PostMessageW(h, WM_CLOSE, 0, 0)
            time.sleep(1.0)
            left = _pid_windows()
            if left:                            # WM_CLOSE 不认就补一发 Esc
                for h in left:
                    _u32.PostMessageW(h, WM_KEYDOWN, VK_ESCAPE, 0)
                    _u32.PostMessageW(h, WM_KEYUP, VK_ESCAPE, 0)
            return
        time.sleep(0.12)


def _watchdog():
    time.sleep(60)
    say("watchdog: 60 秒还没结束，强制退出")
    if not os.path.exists(LOG):
        _dump(9)
    os._exit(9)


def _dump(code: int = 0) -> None:
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
        f.write(json.dumps(_out, ensure_ascii=False, indent=1) + "\n")
        f.write("exit=%d\n" % code)


# ---------------------------------------------------------------- 主流程

def _seed(tmp: str):
    db = Db(os.path.join(tmp, "board.sqlite"))
    db.open()
    board = Board(db)
    from finance import Finance
    fin = Finance(db)
    cid = fin.client_save(None, {"name": "甲客户"})
    pid = board.add_project("导出冒烟项目", "commercial", cid)
    fin.add({"kind": "contract", "project_id": pid, "amount": 100000,
             "date": "2026-01-10", "status": "有效"})
    fin.add({"kind": "payment", "project_id": pid, "amount": 30000,
             "date": "2026-02-20", "status": "已收"})
    return db, board, pid


def _part_a(api, tmp: str) -> None:
    """不打桩「对话框」的那半：写盘 / 扩展名 / 取消 / 失败 / 提前校验。"""
    calls: list = []

    def stub(path, ok=True, msg=""):
        def f(*a, **k):
            calls.append({"args": a, "kw": k})
            return {"ok": ok, "path": path} if ok else {"ok": False, "msg": msg}
        return f

    out_dir = os.path.join(tmp, "picked")
    os.makedirs(out_dir, exist_ok=True)

    # 1) 用户挑了个没有扩展名的名字 → 扩展名以「所选格式」为准补上
    api._pick = stub(os.path.join(out_dir, "我的报表"))
    r = api.finance_export_save({"fmt": "xlsx", "sections": ["records"]})
    want = os.path.join(out_dir, "我的报表.xlsx")
    chk("写到用户挑的路径、按格式补扩展名（%s）" % r.get("path"),
        r.get("ok") is True and r.get("path") == want and os.path.getsize(want) > 0)
    chk("保存对话框拿到的是预填了默认文件名的调用",
        calls and "save_filename" in calls[-1]["kw"]
        and str(calls[-1]["kw"]["save_filename"]).startswith("board_finance"))
    chk("对话框的起始目录 = 导出目录",
        os.path.abspath(str(calls[-1]["args"][1] or calls[-1]["args"][2]))
        == os.path.abspath(api._fin.export_dir()))
    chk("记住这次挑的目录（下次从这儿开始）",
        api._db.get_setting("export_last_dir", "") == out_dir)

    # 2) 已经有正确扩展名 → 不动它
    p2 = os.path.join(out_dir, "定名.csv")
    api._pick = stub(p2)
    r = api.finance_export_save({"fmt": "csv"})
    chk("扩展名已对就不改（%s）" % r.get("path"), r.get("path") == p2)
    with open(p2, encoding="utf-8-sig", newline="") as f:
        chk("CSV 内容对（11 列 + 表头）", len(list(csv.reader(f))[0]) == 11)

    # 3) 用户点取消 = 不是失败
    api._pick = stub("")
    r = api.finance_export_save({"fmt": "json"})
    chk("取消返回 cancelled（不是 ok:False）",
        r.get("ok") is True and r.get("cancelled") is True and "msg" not in r)

    # 4) 对话框本身失败 → 原因要透传出去（前端要 alert 出来）
    api._pick = stub("", ok=False, msg="打不开选择窗口：X")
    r = api.finance_export_save({"fmt": "json"})
    chk("对话框失败时 ok:False 且 msg 透传", r.get("ok") is False and "X" in str(r.get("msg")))

    # 5) 四项内容全不勾 → 直接拒绝，而且**根本没弹对话框**（不然用户白挑一次路径）
    n0 = len(calls)
    r = api.finance_export_save({"fmt": "xlsx", "sections": []})
    chk("内容一项不勾：报错且不弹对话框（%s）" % r.get("msg"),
        r.get("ok") is False and "内容" in str(r.get("msg")) and len(calls) == n0)

    # 6) plan（界面预览走的就是它）：只算不开窗
    r = api.finance_export_plan({"fmt": "xlsx", "sections": ["records"]})
    chk("plan 返回文件名 / 条数 / 内容名，不开对话框（调用次数没涨）",
        r.get("ok") is True and str(r.get("name")).endswith(".xlsx")
        and r.get("count") == 2 and r.get("sections") == ["款项明细"]
        and len(calls) == n0)
    r = api.finance_export_plan({"fmt": "xlsx", "sections": []})
    chk("plan 也会把「一项不勾」报成错误", r.get("ok") is False and "内容" in str(r.get("msg")))


def _part_b(api, seen: dict, result: dict, real_pick) -> None:
    """真弹一次：对话框出现过 + 默认文件名真的预填了 + 取消语义对。"""
    # ⚠ A 段把 `_pick` 打成了桩，这里必须换回真的。忘了换的话 B 段会拿着 A 段
    # 最后那个「失败桩」立刻返回，表现是「对话框没弹 + 报了个跟本次无关的错」，
    # 而时间线看着一切正常（第一版就是这么栽的：B 段 0 毫秒结束）。
    api._pick = real_pick
    chk("B 段用回了真的 _pick（不是 A 段打的桩）",
        getattr(api._pick, "__name__", "") == "_pick")

    stop = threading.Event()
    threading.Thread(target=_dialogs_forever, args=(seen, stop), daemon=True).start()
    t0 = time.time()
    try:
        result["r"] = api.finance_export_save({"fmt": "xlsx",
                                               "sections": ["records", "projects"]})
    except Exception as exc:                                  # pragma: no cover
        result["err"] = repr(exc)
    result["ms"] = int((time.time() - t0) * 1000)
    stop.set()

    texts = seen.get("texts") or []
    hit = [t for t in texts if str(t).startswith("board_finance")]
    chk("系统「另存为」对话框真的弹出来了", bool(seen.get("hwnds")))
    chk("对话框里的文件名框预填了默认名（读到 %r）" % (hit or texts[:4]),
        bool(hit) and str(hit[0]).endswith(".xlsx"))
    r = result.get("r") or {}
    chk("关掉对话框（=取消）返回 cancelled，不是失败",
        r.get("ok") is True and r.get("cancelled") is True)


def main() -> int:
    threading.Thread(target=_watchdog, daemon=True).start()

    tmp = tempfile.mkdtemp(prefix="board_expsave_")
    db, board, _pid = _seed(tmp)
    state: dict = {}
    api = Api(board, db, state)
    real_pick = api._pick          # A 段会打桩，B 段要靠这个还原

    def worker():
        _out["tmp"] = tmp
        _tr("worker 开始")
        try:
            _part_a(api, tmp)
        except Exception as exc:                              # pragma: no cover
            say("A 段异常:", repr(exc))
            chk("A 段不该抛异常", False)
        _tr("A 段结束")

        seen: dict = {}
        result: dict = {}
        try:
            _part_b(api, seen, result, real_pick)
        except Exception as exc:                              # pragma: no cover
            say("B 段异常:", repr(exc))
            chk("B 段不该抛异常", False)
        _tr("B 段结束")
        _out["seen_texts"] = (seen.get("texts") or [])[:6]
        _out["hwnds"] = len(seen.get("hwnds") or [])
        _out["result"] = result
        _out["checks"] = N[0]
        _out["fails"] = FAILS
        _out["trace"] = [x for x in _lines if x.startswith("[t=")]

        if FAILS:
            say("FAIL %d / %d" % (len(FAILS), N[0]))
            for x in FAILS:
                say("   -", x)
        else:
            say("ALL PASS (%d)" % N[0])
        _dump(1 if FAILS else 0)
        try:
            db.close()
        except Exception:
            pass
        try:
            state["window"].destroy()
        except Exception:
            pass

    win = webview.create_window(
        "export-save 冒烟",
        html="<html><body style='font:13px sans-serif'>export save</body></html>",
        js_api=api, width=460, height=200,
    )
    state["window"] = win
    try:
        win.events.shown += lambda: _tr("窗口 shown")
        win.events.closed += lambda: _tr("窗口 closed")
    except Exception as exc:                                  # pragma: no cover
        _tr("挂窗口事件失败: %r" % (exc,))
    _tr("窗口已创建，准备 start")
    webview.start(worker, debug=False)
    _tr("start 返回")
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException as exc:                              # pragma: no cover
        say("异常:", repr(exc))
        _dump(3)
        code = 3
    sys.exit(code)
