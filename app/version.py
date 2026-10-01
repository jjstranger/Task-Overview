"""软件名与版本号 —— **全工程唯一的一处**，改版本只改这个文件。

为什么要单独一个模块：名字和版本号要出现在好几个地方（窗口标题栏、引导页标题、
托盘提示、设置页的版本行、前端 `document.title`、exe 属性），以前各写各的
（`main.TITLE` / `boot.TITLE` / `index.html` 里一份），改一次名字就得满项目找，
漏一处就出现"标题栏换了、托盘还是老名字"这种半截改动。

`TITLE` 就是**窗口标题栏那一整串**，也是 `main._hwnd()` 用来找窗口的字符串 ——
`FindWindowW` 是**全等匹配**，所以别在别处拼一个长得差不多的标题，
一律从这里取，否则置顶按钮会找不到自己的窗口。

VERSION 用 `#.#` 两位（用户口径）。发新版时改 VERSION + RELEASE_DATE 两项。
"""
from __future__ import annotations

APP_NAME = "天在看"                       # 品牌中文名：窗口标题、引导页、托盘用它
APP_NAME_EN = "Travail Task Overview"     # 英文名：跟着版本号出现在标题栏

VERSION = "1.1"                           # 软件版本号（#.#）
RELEASE_DATE = "2026-09-30"               # 这一版的发布日期（YYYY-MM-DD）

# 标题栏：`天在看 - Travail Task Overview v1.1`
TITLE = f"{APP_NAME} - {APP_NAME_EN} v{VERSION}"

# 引导页（选数据文件）是**另一个窗口**，标题必须跟主窗口不一样，
# 不然按标题找窗口的代码（冒烟、置顶、单实例）会把两个窗口认混。
BOOT_TITLE = f"{APP_NAME} · 选择数据文件"

# 设置页「版本」那一栏显示的内容
VERSION_LINE = f"v{VERSION} · {RELEASE_DATE} 发布"

# 打包产物的名字（exe 文件名与 onedir 目录名，两者同名）：`天在看`。
# 构建时仍用 ASCII 名 `ProjectBoard`（PyInstaller 对非 ASCII 名不稳），
# 出包后由 tools/build_exe.py 落位成这个名字。
# 早期版本后缀还带着英文名（长长一串 exe 名），太啰嗦，
# 2026-10-01 起按用户口径简化成光一个品牌名。
# ⚠ 它决定 exe 文件名 —— 改这里会让用户桌面上的快捷方式失效，动之前先打招呼。
EXE_STEM = APP_NAME

# 系统通知的身份（AppUserModelID）。不显式设置的话，Win11 会给托盘通知生成
# `NotifyIconGeneratedAumid_<哈希>`：显示名是「天在看.exe」、图标走系统 fallback
# 解析（2026-10-01 实测：通知图标被系统按旧进程时代的蓝方块记住了，修好托盘也刷不掉）。
# 显式注册后（main.setup_notify_identity 会写 HKCU\...\AppUserModelId\<AUMID>），
# 通知显示名 = APP_NAME、图标 = IconUri 指向的 res/icon.ico，跟窗口/托盘同一个源。
AUMID = "TaskOV.Board"


def tray_tip(today: int | None = None) -> str:
    """系统托盘的悬停提示：`天在看：今日必做 3`。

    用户口径（2026-10-01）：**只留产品名 + 今日待办数**，不要把整串标题栏
    （`天在看 - Travail Task Overview v1.1 · ...`）塞进去 —— 托盘提示是
    鼠标划过时瞄一眼的东西，太长读不到重点。拿不到统计时退化成只有产品名。
    """
    if today is None:
        return APP_NAME
    return f"{APP_NAME}：今日必做 {today}"


def info() -> dict:
    """发给前端的一份（`Api.load()` 挂进 JSON）。"""
    return {
        "name": APP_NAME,
        "name_en": APP_NAME_EN,
        "version": VERSION,
        "release": RELEASE_DATE,
        "title": TITLE,
        "version_line": VERSION_LINE,
    }
