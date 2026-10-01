"""启动兜底入口：run.bat 指向这里，不直接指向 main.py。

为什么需要这一层：run.bat 用 pythonw.exe，没有控制台。
如果 main.py 在 import 阶段就炸了（缺依赖、WebView2 环境问题、
pythonnet 抽风），进程直接消失，用户看到的就是"双击没反应"。
这一层负责把任何异常写成日志并弹窗，之后才把控制权交给 main。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import paths  # noqa: E402
from version import TITLE  # noqa: E402

LOG = paths.data_file("launch_error.log")


def note(msg: str) -> None:
    """普通启动事件（不算错误），单独一份日志，方便回看降级过没有。"""
    paths.log_line("boot.log", msg)


def fatal(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{msg}\n")
    except Exception:
        pass
    # --smoke 无人值守：弹模态框会让测试一直等，看着像启动卡死
    if "--smoke" in sys.argv:
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, msg[:1500], TITLE, 0x10)
    except Exception:
        pass


# 独立的 WebView2 数据目录，避免与其它 WebView2 宿主争抢同一份 profile
os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", paths.data_file("webview2"))

# 降级模式：某些安全软件 / 策略会拦住 WebView2 渲染进程的沙箱，
# 症状是窗口能弹出、但页面永远不加载（JS 桥不通）。
# main.py 的看门狗检测到这种情况会带 BOARD_NOSANDBOX=1 重启自己。
if os.environ.get("BOARD_NOSANDBOX") == "1":
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox")
    note("启动于降级模式（--no-sandbox）")

# 兜底 CWD。程序里所有路径都走 paths.py 的绝对路径，这一句只是为了
# 万一有第三方库按相对路径落文件时，别落在用户当前所在目录。
os.chdir(APP_DIR)

if __name__ == "__main__":
    try:
        import main  # noqa: WPS433
    except BaseException:
        fatal("依赖导入失败：\n\n" + traceback.format_exc())
        sys.exit(1)
    try:
        sys.exit(main.main())
    except SystemExit:
        raise
    except BaseException:
        fatal("运行异常：\n\n" + traceback.format_exc())
        sys.exit(1)
