"""入口：窗口、托盘、开机自启、置顶、锁与备份。"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
import traceback
import winreg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths  # noqa: E402

# 独立的 WebView2 用户数据目录，避免与其他 WebView2 宿主争抢。
# ⚠ 落点必须问 paths（打包后是 **exe 同级的 data/**）；早先这里用 `__file__` 往上拼，
# 而打包后 `__file__` 指向解包临时目录 —— profile 会在 _internal/ 里，重启即丢。
# 必须在 import webview **之前**设好，webview 启动时就读它了。
os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", paths.data_file("webview2"))

# 降级模式：某些安全策略会拦住 WebView2 渲染进程的沙箱，症状是窗口能弹出、
# 页面永远不加载。看门狗（见 bridge_watchdog）检测到后带 BOARD_NOSANDBOX=1 重启自己。
# 映射写在这里而不是只在 _boot.py，因为直接跑 main.py（如 --smoke）时也要生效。
if os.environ.get("BOARD_NOSANDBOX") == "1":
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox")

import webview  # noqa: E402

import boot  # noqa: E402
import opener  # noqa: E402
import remind  # noqa: E402
from api import Api  # noqa: E402
from db import AlreadyRunning, Db, Locked  # noqa: E402
from models import Board  # noqa: E402
from version import AUMID, APP_NAME, TITLE, tray_tip  # noqa: E402

# 标题栏那一整串（`天在看 - Travail Task Overview v1.1`）来自 version.py 的**唯一一处**：
# 它同时是 `_hwnd()` 用 FindWindowW 找窗口的 key（全等匹配），
# 在别处另拼一个"长得差不多"的标题会让置顶直接失效。

_SF = None            # 冒烟时的栈转储文件句柄，必须一直开着别被 GC（见下方 --smoke 分支）
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE_NOSIZE = 0x0001 | 0x0002


# ---------- 置顶：直接用 Win32，比依赖 pywebview 属性稳 ----------
#
# 关键改动：**回读真实状态，而不是信参数**。窗口层级会被好几个地方改
# （托盘菜单、提醒拉前台、pywebview 自己的 on_top），而按钮亮灭以前只读数据库里的
# `on_top` 设置 —— 两边对不上，表现就是「点了置顶没反应」：启动时窗口本来就置着顶、
# 按钮也亮着，用户点一下其实是把置顶关掉了。所以这里一律返回操作后窗口的真实状态。

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

_u = ctypes.WinDLL("user32", use_last_error=True)
_u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_u.FindWindowW.restype = ctypes.c_void_p
_u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_uint]
_u.SetWindowPos.restype = ctypes.c_bool
# 句柄/样式必须按指针宽度声明：默认按 int 返回会把 64 位值截成 32 位，
# 句柄稍大一点就变成 0，于是「置顶成功」而窗口毫无变化。
try:
    _GetWindowLongPtr = _u.GetWindowLongPtrW
except AttributeError:                       # 32 位 Windows 上只有 GetWindowLongW
    _GetWindowLongPtr = _u.GetWindowLongW
_GetWindowLongPtr.argtypes = [ctypes.c_void_p, ctypes.c_int]
_GetWindowLongPtr.restype = ctypes.c_void_p

# 前台化 / 弹窗也走这个句柄表。默认参数转换是 c_int，传 HWND 会被截断 ——
# 跟上面 SetWindowPos 是同一个坑，只是平时句柄值小、不容易撞上。
_u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_u.ShowWindow.restype = ctypes.c_bool
_u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
_u.SetForegroundWindow.restype = ctypes.c_bool
_u.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
_u.MessageBoxW.restype = ctypes.c_int


def _msgbox(text: str, flags: int = 0x10) -> int:
    """弹一个系统消息框（MB_ICONERROR）。pythonw 下这是唯一能"说话"的通道。"""
    try:
        return int(_u.MessageBoxW(None, str(text)[:1200], TITLE, flags))
    except Exception:
        return 0


def _hard_exit(code: int = 0) -> None:
    """不经 Python 直接退出进程，**不返回**。

    主线程卡在 pywebview 的消息循环里时，别的线程可能长时间拿不到 GIL，
    那会儿 `sys.exit()` 是抛异常给主线程（它正忙）、`os._exit()` 也要先拿 GIL ——
    都可能一直卡着。只有 Win32 这个调用能保证结束进程。
    """
    ctypes.windll.kernel32.ExitProcess(int(code))


def _hwnd() -> int:
    return int(_u.FindWindowW(None, TITLE) or 0)


def is_top() -> bool:
    """窗口**现在**是不是真的置顶（读 WS_EX_TOPMOST，不读设置）。"""
    hwnd = _hwnd()
    if not hwnd:
        return False
    style = int(_GetWindowLongPtr(ctypes.c_void_p(hwnd), GWL_EXSTYLE) or 0)
    return bool(style & WS_EX_TOPMOST)


def set_top(on: bool) -> bool:
    """置顶 / 取消置顶，返回**操作后**窗口的真实状态。"""
    hwnd = _hwnd()
    if not hwnd:
        return False
    _u.SetWindowPos(
        ctypes.c_void_p(hwnd),
        ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST),
        0, 0, 0, 0, SWP_NOMOVE_NOSIZE,
    )
    return is_top()


def _append_log(msg: str) -> None:
    """普通启动事件（含降级重启），与崩溃堆栈分开记。"""
    paths.log_line("boot.log", msg)


def _fatal(msg: str) -> None:
    """pythonw 没有控制台，出错必须留下证据，否则就是"双击没反应"。"""
    try:
        with open(paths.data_file("launch_error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{msg}\n")
    except Exception:
        pass
    _msgbox(msg)


def activate() -> bool:
    """把窗口拉到前台（置顶只改层级，不会激活）。"""
    hwnd = _hwnd()
    if not hwnd:
        return False
    h = ctypes.c_void_p(hwnd)
    _u.ShowWindow(h, 9)            # SW_RESTORE
    _u.SetForegroundWindow(h)
    return True


def wake_existing(seconds: float = 3.0) -> bool:
    """轮询几秒等待已有实例的窗口出现并拉到前台。

    已有实例可能还在启动中，窗口标题尚未就绪，所以不能只查一次。
    """
    end = time.time() + seconds
    while time.time() < end:
        if activate():
            return True
        time.sleep(0.4)
    return activate()


# ---------- 开机自启 ----------
#
# 命令行统一走 paths.start_cmd()：它按 frozen 分流（打包后只写 exe 路径，
# 源码下才是 python+main.py）。以前这里自己拼一遍，打包后会写出
# 「exe + main.py」这种不存在的组合，自启直接失效。


def set_autostart(on: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, "ProjectBoard", 0, winreg.REG_SZ, paths.start_cmd())
            else:
                try:
                    winreg.DeleteValue(k, "ProjectBoard")
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def autostart_on() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "ProjectBoard")
            return True
    except Exception:
        return False


# ---------- 托盘 ----------

def tray_icon():
    """托盘图标：直接用 `res/icon.ico`，别再在这儿用代码画一遍。

    图标统一由 `tools\\make_icon.py` 从 `res/logo.svg` 生成（窗口 / exe / 托盘同一个源），
    改形不用再满项目找哪里还画了一份。读不到文件才退一块纯色方块占位 ——
    以前这里用 PIL 照坐标把 logo 现画一遍，等于给母版留了第二份"真相"。

    ⚠ 只挑 32px 那一帧给 pystray：它会把传入的图重存成单源 ico 交给 LoadImage，
    喂 256px 的话各小帧全是 Pillow 自己缩的（糊），32px 恰好等于 LoadImage
    `LR_DEFAULTSIZE` 的系统值，精确命中零缩放，各 DPI 下再由 Explorer 收缩也最接近母版。
    """
    from PIL import Image

    try:
        p = paths.icon_file()
        if os.path.exists(p):
            im = Image.open(p)
            try:
                im.size = (32, 32)   # IcoImageFile：按尺寸选帧（Pillow 官方用法）
            except Exception:
                pass                 # ico 里没有 32 帧就退回默认帧，别让它炸
            return im.convert("RGBA")
    except Exception as exc:
        paths.log_line("pick.log", f"托盘图标读 res/icon.ico 失败：{exc}")
    return Image.new("RGBA", (64, 64), (91, 156, 248, 255))


def start_tray(window, on_quit, on_checkin, on_open_data, tip=""):
    import pystray

    def show():
        try:
            window.restore()
            window.show()
        except Exception:
            pass
        activate()

    menu = pystray.Menu(
        pystray.MenuItem("显示", lambda: show(), default=True),
        pystray.MenuItem("今日打卡", lambda: on_checkin()),
        pystray.MenuItem("置顶", lambda: set_top(True)),
        pystray.MenuItem("取消置顶", lambda: set_top(False)),
        pystray.MenuItem("打开数据目录", lambda: on_open_data()),
        pystray.MenuItem("退出", lambda: on_quit()),
    )
    icon = pystray.Icon("Task-Overview", tray_icon(), tip or TITLE, menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


# ---------- 主流程 ----------

def _offer_share(msg: str) -> bool:
    """数据文件被另一台机器占着时，问一句要不要改成「多机同时打开」。

    直接给一条出路，而不是让用户自己去找设置项 —— 这个提示出现的时刻
    正是他最想"现在就用"的时刻，而那会儿连设置面板都打不开（程序已经退了）。
    """
    text = (msg + "\n\n"
            "要改成「多机同时打开」吗？\n"
            "两台机器读同一个数据文件，写操作由 SQLite 自己排队；"
            "另一边改完会提示你重新加载。\n\n"
            "（选「是」会记住这个设置并重新启动）")
    # MB_YESNO | MB_ICONQUESTION
    if _msgbox(text, 0x04 | 0x20) != 6:
        return False
    err = paths.save_config({"share_db": True})
    if err:
        _msgbox("写配置文件失败：" + err)
        return False
    try:
        subprocess.Popen(paths.restart_cmd(), cwd=paths.base_dir(), close_fds=True)
    except Exception as exc:
        _msgbox("重启失败：" + str(exc))
        return False
    return True


def setup_notify_identity() -> None:
    """给系统通知注册显式身份（AppUserModelID）。

    不设的话 Win11 会给托盘通知生成 `NotifyIconGeneratedAumid_<哈希>`：
    通知上显示「天在看.exe」，图标走系统 fallback 解析 —— 2026-10-01 实测，
    旧进程时代的兜底蓝方块被系统按这个身份记住了，托盘修好后 toast 仍显示蓝方块。
    显式注册后：显示名 = APP_NAME，图标 = IconUri（res/icon.ico，与窗口/托盘同一源）。

    ⚠ `SetCurrentProcessExplicitAppUserModelID` 必须在**任何窗口创建之前**调用，
    所以放在 main() 的最前头（引导页、主窗口、托盘都排在它后面）。
    注册表项每次启动幂等重写一遍：IconUri 是绝对路径，装到哪儿都要跟着对。
    失败静默降级 —— 身份没注册上只是显示回退到「天在看.exe」，应用本体照常跑。
    """
    try:
        shell = ctypes.windll.shell32
        set_id = shell.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.HRESULT
        set_id(AUMID)
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\AppUserModelId" + "\\" + AUMID)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, paths.icon_file())
        winreg.CloseKey(key)
    except Exception as exc:
        paths.log_line("pick.log", f"注册通知身份失败（不影响运行）：{exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="启动数秒后回读页面状态写 tests/smoke_note.txt 并退出")
    args = ap.parse_args()

    # 通知身份要在任何窗口（主窗口 / 引导页 / 托盘）出现之前定下来
    setup_notify_identity()

    # 优先级只有这一处实现：--db > BOARD_DB > data/config.json > 内置默认
    db_path = paths.resolve_db_path(args.db)
    share = paths.share_db()

    # 网络位置先探一下再碰它。断链的映射（本机 N:/P:/S:/Z: 经常是这个状态）
    # 第一次访问要等 Windows 重连，实测 16.5 秒 —— 那十几秒纯粹是白等。
    # 探针只连一下那台机器的 SMB 端口，毫秒级；连不上就直接进引导页
    # （那里有「重试」，NAS 开机了再点一次就行）。
    net_bad = paths.preflight(db_path)
    if net_bad:
        if args.smoke:
            _append_log(f"smoke: 数据位置预检不通过，跳过引导页：{net_bad}")
            return 2
        paths.log_line("boot.log", f"预检不通过，不去碰这个路径：{net_bad}（{db_path}）")
        return boot.run_boot(net_bad, db_path)

    db = Db(db_path or None, share=share)
    try:
        db.open()
    except AlreadyRunning:
        # 本机已有实例在跑（窗口可能缩在托盘里）：唤醒它，别重复开票
        if wake_existing():
            return 0
        _msgbox("看板已在运行，但拉不到前台。\n"
                "通常缩在右下角托盘里，点一下托盘图标即可。", 0x30)
        return 2
    except Locked as exc:
        # 被另一台机器占着。先让出互斥量，否则新进程会被我们自己挡住。
        try:
            db.close()
        except Exception:
            pass
        if args.smoke:
            _append_log("smoke: 数据文件被占用，跳过弹窗直接退出")
            return 2
        if _offer_share(str(exc)):
            return 0
        return 2
    except Exception as exc:
        # 数据文件用不了（别人的机器上没有 S 盘就是这一条）。
        # 别再"一句话弹窗然后退出"了 —— 起引导页让用户当场指一个位置。
        try:
            db.close()
        except Exception:
            pass
        if args.smoke:
            _append_log(f"smoke: 数据文件打不开，跳过引导页：{exc}")
            return 2
        return boot.run_boot(str(exc), db.path)

    board = Board(db)
    state: dict = {}
    state["diag"] = bool(args.smoke)     # 让前端做一次自检巡视
    api = Api(board, db, state)

    # --smoke 时每 5 秒把所有线程的栈写进 data/stack.log。
    # 页面"卡死"到底是卡在浏览器侧还是 Python 侧，只有这个能定性 ——
    # 2026-09-30 就是靠它一眼看出是某个 API 卡在路径检查上（不是前端坏了）。
    # 只在冒烟时开，正常使用不写任何东西。
    if args.smoke:
        global _SF
        try:
            import faulthandler
            _SF = open(paths.data_file("stack.log"), "w", encoding="utf-8")
            faulthandler.dump_traceback_later(5, repeat=True, file=_SF)
        except Exception:
            pass

    # 沙箱在这台机器上起不来时，看门狗会降级重启一次；降级成功后把结果记下来，
    # 之后每次启动直接用 --no-sandbox，不用再白等一遍看门狗超时。
    if db.get_setting("nosandbox", "0") == "1":
        os.environ["BOARD_NOSANDBOX"] = "1"
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox")

    html = paths.web_index()
    top = db.get_setting("on_top", "1") == "1"
    window = webview.create_window(
        TITLE, url=html, js_api=api, width=1020, height=760,
        min_size=(720, 480), on_top=top,
    )
    state["window"] = window
    state["set_top"] = set_top
    state["is_top"] = is_top
    state["set_autostart"] = set_autostart
    db.set_setting("autostart", "1" if autostart_on() else "0")

    def heartbeat():
        while True:
            time.sleep(20)
            try:
                db.heartbeat()
            except Exception:
                pass

    threading.Thread(target=heartbeat, daemon=True).start()

    def on_shown():
        try:
            if top:
                set_top(True)
        except Exception:
            pass

    def on_closing():
        try:
            window.hide()
        except Exception:
            pass
        return False

    window.events.shown += on_shown
    window.events.closing += on_closing

    tray = [None]

    # ---------- 提醒（M2） ----------

    def do_notify(title, msg):
        icon = tray[0]
        if not icon:
            return False
        try:
            icon.notify(msg, title)
            return True
        except Exception:
            return False

    def do_front():
        try:
            window.restore()
            window.show()
        except Exception:
            pass
        try:
            set_top(True)
            activate()
        except Exception:
            pass

    def do_checkin():
        level = int(db.get_setting("remind_level", "2") or 2)
        do_front()
        try:
            window.evaluate_js(f"window.__openCheckin && window.__openCheckin({level})")
        except Exception:
            pass

    def do_open_data():
        try:
            opener.open_dir(os.path.dirname(os.path.abspath(db.path)))
        except Exception:
            pass

    state["notify"] = do_notify
    state["front"] = do_front

    reminder = remind.Reminder(board, db, state)

    def quit_app():
        state["quitting"] = True
        reminder.stop()
        try:
            db.backup()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
        if tray[0]:
            tray[0].stop()
        try:
            window.destroy()
        except Exception:
            pass
        _hard_exit()

    if not args.no_tray and not args.smoke:
        # 托盘提示只留「天在看：今日必做 N」（用户口径 2026-10-01）——
        # 以前拼的是整串标题栏 + 统计，鼠标划过去一长条没人读得完。
        # 数用 priority_count()：一条 COUNT，不为了这一个数跑一遍整树 load()
        # （load() 内部又会再算一遍 stats()，启动时等于白算两遍全库）。
        try:
            tip = tray_tip(board.priority_count())
        except Exception:
            tip = tray_tip()
        tray[0] = start_tray(window, quit_app, do_checkin, do_open_data, tip)

    state["quit"] = quit_app
    reminder.start()

    # ---------- 桥接看门狗：WebView2 沙箱起不来时自动降级重启 ----------
    #
    # 症状：窗口正常弹出，但页面永远不加载（渲染进程沙箱被安全策略拦住）。
    # 判断方式是用前端报到（api.boot_ok），不能用 evaluate_js —— 桥坏的时候
    # 它会直接卡死，把看门狗自己一起挂住。

    def bridge_watchdog():
        # 健康的话本地页面 2 秒内就能报到；15 秒还没动静基本就是沙箱被拦了。
        # 别设太长，否则每次启动都要干等。
        deadline = 15
        for _ in range(deadline):
            time.sleep(1)
            if state.get("bridge_ok") or state.get("quitting"):
                return
        if os.environ.get("BOARD_NOSANDBOX") == "1":
            _fatal(
                "WebView2 无法初始化：页面已加载但 JS 桥不通，"
                "关闭沙箱后仍然失败。\n\n"
                "多半是 WebView2 Runtime 需要修复：\n"
                "设置 → 应用 → Microsoft Edge WebView2 Runtime → 修改 → 修复\n"
            )
            return
        _append_log("桥接 %d 秒内未就绪，改用 --no-sandbox 重启" % deadline)
        # 记住这次降级，下次启动直接走 --no-sandbox，不再等一遍超时
        try:
            db.set_setting("nosandbox", "1")
        except Exception:
            pass
        # 让出锁和互斥量，否则新进程会被自己挡住
        try:
            reminder.stop()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        env = os.environ.copy()
        env["BOARD_NOSANDBOX"] = "1"
        # 必须用 paths.restart_cmd()：打包后 _boot.py **不在包里**，
        # 拿 `sys.executable + _boot.py` 拼命令行，exe 会把它当成普通参数丢给 argparse 然后退出 ——
        # 沙箱降级这条救命路径在 exe 里就废了。
        try:
            subprocess.Popen(paths.restart_cmd() + sys.argv[1:], env=env,
                             cwd=paths.base_dir(), close_fds=True)
        except Exception as exc:
            _fatal("降级重启失败：" + repr(exc))
        _hard_exit()

    if not args.smoke:
        threading.Thread(target=bridge_watchdog, daemon=True).start()

    def on_started():
        """--smoke：等前端把各页面渲染统计回传后写文件退出。

        这里刻意不用 evaluate_js —— 它要等 window 的 loaded 事件，
        而那个事件在部分环境下不触发，会报 "Main window failed to start"
        或者干脆空等；JS→Python 这条方向才是日常真正在用的通道。
        """
        if not args.smoke:
            return
        rep = ""
        # 前端自检巡视本身每步都有超时。⚠ 2026-09-30：自检用例越加越多，实测全程
        # 已经到 55~70 秒，贴着旧上限（75 秒）。只要某一步恰好要等 ——
        # 典型是路径检查撞上**已断链的网络盘符**（Windows 重连实测能等 21 秒）——
        # 就会假报"页面没回传状态"，看着像被测功能坏了。上限放宽到 120 秒：
        # 正常情况下 2 秒内就拿到报告，只有真出问题才会耗到上限。
        for _ in range(120):
            time.sleep(1)
            rep = str(state.get("boot_report") or "")
            if rep:
                break
        if not rep:
            rep = ("NO_REPORT: 页面 120 秒内没有回传状态；"
                   f"bridge_ok={bool(state.get('bridge_ok'))}；"
                   f"stages={state.get('diag_stages')}")

        # 落点必须走 paths.smoke_note()：它按 sys.frozen 判断，
        # 打包后指向 **exe 同级的 tests/**（并且会建目录）。
        # 早先这里用 `__file__` 往上拼一层 —— 打包后 __file__ 在解包目录里，
        # 拼出来那个 tests/ 根本不存在，open 失败又被 except 吞掉，
        # 于是"exe 冒烟永远读不到报告"，看着像 exe 崩在启动阶段。
        p = paths.smoke_note()
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(rep)
        except Exception as exc:
            _append_log("smoke_note 写入失败 %s: %r" % (p, exc))
        try:
            db.close()
        except Exception:
            pass
        _hard_exit()

    # debug=True 会连带打开 devtools，给冒烟引入无关变量；这里只需要 evaluate_js
    # icon 是给 winforms 的窗口图标（标题栏 + 任务栏）；只认 .ico。
    # 传了它就走明确的加载路径，不会掉进 pywebview 自带的
    # `ExtractIconW(handle, sys.executable, 0)` 兜底分支（那条路里有个
    # `IntPtr.op_Explicit(Int32(...))`，64 位下会把句柄截断 —— 见 README 的置顶同类坑）。
    webview.start(on_started, debug=False, icon=paths.icon_file())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _fatal("启动失败：\n\n" + traceback.format_exc())
        sys.exit(1)
