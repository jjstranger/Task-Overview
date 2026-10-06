"""打包 `天在看 - Travail Task Overview`：PyInstaller 封装成 Windows exe。

用法（在 board venv 里跑）：

```
python tools/build_exe.py              # 默认 onedir：dist\天在看\天在看.exe
python tools/build_exe.py --onefile    # 单文件：dist\天在看.exe
python tools/build_exe.py --console    # 保留控制台窗口（排查打包问题用）
python tools/build_exe.py --outdir X   # 出到别的目录，做对照构建用
python tools/build_exe.py --force      # 跳过「旧产物被占用」预检（明知占用还硬来）
```

构建前会先做一次 **[0/4] 预检**：如果 `dist\天在看` 正被运行中的看板占用，直接
报清楚并退出（码 3），不会先把产物跑完再在让位那一步失败。

## 为什么默认 onedir 而不是 onefile

pywebview 在 Windows 上靠 pythonnet 加载 `webview/lib` 里的 .NET 程序集，
onefile 每次启动都要把几十 MB（含 `Microsoft.Web.WebView2.Core.dll`）解到临时目录，
启动慢，而且 .NET 程序集从临时目录加载更容易出幺蛾子。
onedir 下这些 DLL 就躺在 exe 旁边，最稳。要单文件再显式 `--onefile`。

## 名字里的中文

PyInstaller 的 `--name` 直接喂中文会在 build 目录里造一堆中文路径，历史上踩过坑。
所以**统一用 ASCII 名 `Task-Overview` 构建，出包后再落位**成正式名
（`version.EXE_STEM`，2026-10-01 起就叫 `天在看`）。
PyInstaller 6 的 onedir 布局里配套目录固定叫 `_internal`、不随 exe 名走，改名是安全的。

## 为什么绝不用 rename

QNAP SMB 上实测（2026-09-27）：**对文件做 rename 会把该对象永久毒化** —— 之后不能覆盖、
不能二次改名，连删除都是 ACCESS_DENIED（err 5）；对目录 rename 还会毒化目录内**已存在**的文件。
也就是说 rename 出来的东西再也清不掉，会在 dist/ 里越堆越多（`.prev-*` 就是这么来的）。
所以这里一律走 `nasfs` 的「复制 + 删原」：副本和原件都没被 rename 过，两者都可删。

旧产物默认**直接删掉**；只有当它里面含毒化对象、删不动时，才复制让位成 `.prev-<时间戳>`
（那种只能请用户在 NAS 端 File Station / SSH 里删）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nasfs  # noqa: E402  NAS 上 rename 会毒化对象，搬运一律走它的「复制+删原」

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")

sys.path.insert(0, APP)
import paths  # noqa: E402    web/ 与 icon.ico 的落点只有它说了算（别在这儿再拼一份）
from version import EXE_STEM  # noqa: E402  产物名的唯一来源（见 app/version.py）

BUILD_NAME = "Task-Overview"        # 构建用 ASCII 名（PyInstaller 不吃中文名）
FINAL_NAME = EXE_STEM               # 出包后的正式名：天在看


# ---- 构建前「旧产物被占用」预检（2026-10-05 加） ------------------------------
#
# 为什么要有它：[3/4] 的 move_aside 是「先 rmtree，删不掉才复制让位」。如果 `dist\天在看`
# 正被运行中的看板占着，rmtree 会失败 → **先老老实实复制 51 MB 备份**，然后才发现原件
# 还在原地、新产物放不进去，最后抛 err 32。两次实测的代价分别是「白跑 30 秒」和
# 「dist 里多一个 32 MB 的 .prev-* 垃圾」。
# 真正的处理办法（先把看板退干净）藏在报错的中括号里，读的人得先看懂 err 32。
#
# 这里把判据**原样提前**：用和 DeleteFileW 一模一样的条件去探 —— 以 DELETE 权限、
# share=0 独占打开目录里每个文件。打不开（ERROR_SHARING_VIOLATION = 32）就是被占用。
# 同一判据意味着「预检说没事、让位却照样挂」的概率很低；但仍留 `--force` 逃生门。

_DELETE = 0x00010000                 # DeleteFileW 需要的访问权限
_OPEN_EXISTING = 3
_ERR_SHARING_VIOLATION = 32          # 与 move_aside 失败时看到的 err 32 同一个码
_INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF
_SCAN_LIMIT = 3000                   # 扫这么多文件还没发现占用就认为目录是干净的

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
# ⚠ HANDLE 必须用 c_void_p / LPVOID：用 c_int 会被截断成 32 位，
#   返回值恒不等于 INVALID_HANDLE_VALUE → 探测永远假阴性（这个坑本项目踩过一次）
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_k32.CreateFileW.restype = ctypes.c_void_p
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL


def _file_locked(path: str) -> bool:
    """这个文件能不能删？True = 被别的进程占着。

    **只探测、不真删**：拿到句柄立刻关掉。判据与 DeleteFileW 一致。
    """
    h = _k32.CreateFileW(path, _DELETE, 0, None, _OPEN_EXISTING, 0, None)
    if h is None or h in (0, _INVALID_HANDLE, -1):
        return ctypes.get_last_error() == _ERR_SHARING_VIOLATION
    _k32.CloseHandle(h)
    return False


def scan_locked(root: str, keep: int = 8) -> list[str]:
    """扫 root（目录或单个文件），返回被占用的文件路径，最多 keep 条。"""
    if os.path.isfile(root):
        return [root] if _file_locked(root) else []
    if not os.path.isdir(root):
        return []
    hits: list[str] = []
    seen = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            seen += 1
            if seen > _SCAN_LIMIT or len(hits) >= keep:
                return hits
            p = os.path.join(base, name)
            if _file_locked(p):
                hits.append(p)
    return hits


def move_aside(path: str) -> str | None:
    """让开 path：能删就删（干净利落），删不掉才复制让位成 .prev-<时间戳>。

    返回说明字符串；本来就不存在则返回 None。**绝不用 rename** —— 见文件头。

    ⚠ 坑（2026-09-27 踩到）：`nasfs.move_aside` 是「复制 + 删原」。要是**原件删不掉**
    （被占用 / 毒化），复制出来的副本是有了，但**原名仍然被占着** ——
    调用方接下来往原名放新产物就会 `FileExistsError`。所以这里复制完必须再确认一次
    原件真的没了；没删掉就直接报错说清楚「谁在那儿挡路」，别让调用方撞上去。
    """
    if not os.path.exists(path):
        return None
    _n, fails = nasfs.rmtree(path)
    if not fails:
        return "(旧产物已直接删除)"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i in range(100):
        dst = "%s.prev-%s%s" % (path, stamp, "" if i == 0 else "-%d" % i)
        if not os.path.exists(dst):
            nasfs.move_aside(path, dst)
            left = os.path.exists(path)
            print("   ! 旧产物删不掉（%d 项）：已复制让位到 %s" % (len(fails), dst))
            if left:
                # 副本留下了，但原名还被占着 —— 说人话，别让后面的 copytree 崩得莫名其妙
                why = ""
                errs = sorted({e for _p, e in fails})
                if 32 in errs:
                    why = "（错误码 32 = 文件被某进程占用，先把程序退干净再试）"
                elif 5 in errs:
                    why = "（错误码 5 = 拒绝访问，多半是这份产物被 rename 毒化过）"
                raise RuntimeError(
                    "旧产物 %s 删不掉%s，原名还被占着，没法放新产物。\n"
                    "   副本已备份到：%s\n"
                    "   处理办法：① 关掉正在运行的看板（含托盘里的）；"
                    "② 手动删掉 %s；③ 或者加 --outdir dist\\_verify 换个目录出包对照。"
                    % (path, why, dst, path)
                )
            return dst
    raise RuntimeError("让位失败：%s 已经堆了太多 .prev 备份，手动清一下吧" % path)


def main() -> int:
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="打成单文件 exe（启动慢一些）")
    ap.add_argument("--console", action="store_true", help="保留控制台窗口（排查打包问题时用）")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dist"), help="产物目录，默认 dist/")
    ap.add_argument("--stagepath", default="",
                    help="PyInstaller 的 distpath（暂存目录），默认放本地临时目录（见下）")
    ap.add_argument("--workpath", default="",
                    help="PyInstaller 中间目录，默认放本地临时目录（见下）")
    ap.add_argument("--force", action="store_true",
                    help="跳过「旧产物被占用」预检（明知被占用还硬来）")
    args = ap.parse_args()
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    # [0/4] 预检：旧产物被占用的话现在就说清楚，别等 [3/4] 白复制 51 MB 备份才报 err 32。
    #        判据与让位失败时完全一致（见 _file_locked 上方那段注释）。
    target = os.path.join(outdir, FINAL_NAME + ".exe") if args.onefile \
        else os.path.join(outdir, FINAL_NAME)
    if not args.force and os.path.exists(target):
        print("[0/4] 预检旧产物是否被占用 …")
        locked = scan_locked(target)
        if locked:
            print("   !! 旧产物正被占用 —— 现在继续的话，[3/4] 让位时必定报 err 32（构建白做）")
            for p in locked[:5]:
                try:
                    shown = os.path.relpath(p, outdir)
                except ValueError:
                    shown = p
                print("        " + shown)
            if len(locked) >= 8:
                print("        …（还有更多，先按下面的办法处理）")
            print()
            print("   处理办法（任选其一）：")
            print("     ① 关掉正在运行的看板 —— 别忘了右下角托盘图标里还挂着一份")
            print("     ② 关掉后若还有残留：任务管理器里结束 msedgewebview2.exe")
            print("     ③ 换个目录出包做对照：--outdir dist\\_verify")
            print("     ④ 确定要跳过这道检查：加 --force")
            return 3
        print("   旧产物未被占用，继续")

    # 打包前先语法检查：拼错一个 import，PyInstaller 只会在运行时才炸
    print("[1/4] py_compile")
    r = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", APP],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        return 1

    print("[2/4] pyinstaller ...")
    ico = paths.icon_file()
    # ⚠ res 的 add-data 源必须是 **app/res**（图标目录本身），不是 res_dir()。
    #   源码下 res_dir() = app/ 目录 —— 以前写成 paths.res_dir() 等于把整个 app/
    #   塞进了 `_internal/res/`，图标实际落在 `_internal/res/res/icon.ico`，
    #   而 frozen 下 paths.icon_file() 找的是 `_internal/res/icon.ico` → 找不到，
    #   托盘永远显示兜底的纯色方块（2026-10-01 用户报的「托盘图标不对」就是它）。
    res = os.path.join(paths.res_dir(), "res")
    web = os.path.join(paths.res_dir(), "web")
    for need in (ico, os.path.join(res, "logo.svg"), os.path.join(web, "index.html")):
        if not os.path.exists(need):
            print("   资源缺失：%s\n   图标缺了先跑：python tools\\make_icon.py" % need)
            return 1
    # 中间产物（build/ 与 spec）一律放**本地临时目录**，不落在项目里：
    #   · spec 以前写在项目根目录，那份是随源码放在只读网络盘上的，PyInstaller 每轮重写它 → 直接挂
    #   · build/ 更麻烦：PyInstaller 每轮要把里面的中间文件**删掉**重来，
    #     而网络盘上删除是被禁的（safe-delete 无回收站 → FAIL_CLOSED），同样挂
    # 这两个跟源码没有半点关系，放本地既省事又更快。
    workpath = os.path.abspath(args.workpath) if args.workpath \
        else os.path.join(tempfile.gettempdir(), "Task-Overview_build")
    specpath = workpath
    os.makedirs(workpath, exist_ok=True)
    # ⚠ distpath **也**放本地临时目录，不要直接给 outdir（2026-10-01 踩到）：
    #   PyInstaller 的 COLLECT 会先把已存在的 `<distpath>\Task-Overview` 整个删掉重来
    #   （实测 160 个对象），而环境的安全删除兜底是**按轮累计**的 —— 超阈值就要逐次确认，
    #   拦下来会**直接掐掉整条命令**（exit=1）。症状：已经构建好的 exe 白做，
    #   报告只在最后一行留一句 SAFE_DELETE_BULK_CONFIRM_REQUIRED，看着像打包自己炸了。
    #   放 TEMP 之后 PyInstaller 永远不碰 dist\，落位统一由 [3/4] 的「复制+删原」来做。
    stage = os.path.abspath(args.stagepath) if args.stagepath \
        else os.path.join(tempfile.gettempdir(), "Task-Overview_dist")
    os.makedirs(stage, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", BUILD_NAME,
        "--paths", APP,
        "--distpath", stage,
        "--specpath", specpath,
        # web/ 是运行时才用到的静态资源，PyInstaller 静态分析看不到，必须显式带进去
        "--add-data", web + os.pathsep + "web",
        # res/ 同理：图标要在**运行时**被 paths.icon_file() 读到（窗口图标 + 托盘）。
        # frozen 下 icon_file() = `<_internal>/res/icon.ico`，所以 dest 必须是 res。
        "--add-data", res + os.pathsep + "res",
        # 而 exe 文件本身那个图标是**编译期**塞进 PE 资源段的，运行时不参与
        "--icon", ico,
        # 平台后端是运行时按平台挑的，静态分析跟不到，显式钉住 Windows 这两个
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "clr",
        # 托盘后端同理
        "--hidden-import", "pystray._win32",
        # 用不上，带上纯属涨体积
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pytest",
        "--exclude-module", "PyInstaller",
        "--workpath", workpath,
        os.path.join(APP, "main.py"),
        "--onefile" if args.onefile else "--onedir",
    ]
    if not args.console:
        cmd.append("--windowed")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        return 1
    warn = [ln for ln in r.stdout.splitlines() if "WARNING" in ln or "ERROR" in ln]
    if warn:
        print("  pyinstaller 警告（%d 条，尾部 12 条）：" % len(warn))
        for ln in warn[-12:]:
            print("   ", ln.strip())

    print("[3/4] 落位 %s -> %s（从暂存目录复制，绝不用 rename）" % (BUILD_NAME, FINAL_NAME))
    if args.onefile:
        dst = os.path.join(outdir, FINAL_NAME + ".exe")
        moved = move_aside(dst)
        ok, err = nasfs.retarget(os.path.join(stage, BUILD_NAME + ".exe"), dst)
        if not ok:
            print("   ! exe 落位失败 err=%d" % err)
        exe = dst
    else:
        # exe 先在**暂存目录**里换成正式名（复制+删原），再把整个目录复制到 outdir。
        # 不再依赖 rename，所以没有先后次序的坑；好处是产物里不会留下删不掉的毒化对象。
        # 暂存目录里的 ASCII 目录**不用清**：下次 PyInstaller 自己在 TEMP 里删重建
        # （TEMP 不在删除兜底的管辖范围内，2026-10-01 实测）。
        src_dir = os.path.join(stage, BUILD_NAME)
        old = os.path.join(src_dir, BUILD_NAME + ".exe")
        new = os.path.join(src_dir, FINAL_NAME + ".exe")
        ok, err = nasfs.retarget(old, new)
        if not ok:
            print("   ! exe 落位失败 err=%d" % err)
        dst_dir = os.path.join(outdir, FINAL_NAME)
        moved = move_aside(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        exe = os.path.join(dst_dir, FINAL_NAME + ".exe")
        if moved:
            print("  旧的已让位到:", moved)

    print("[4/4] 收尾")
    # 运行时资源落点自检：这三样少一样，托盘/窗口图标或整个页面就会悄悄退化
    # （托盘退纯色方块、webview 白屏），而 PyInstaller 本身一个警告都不给。
    if not args.onefile:
        internal = os.path.join(os.path.dirname(exe), "_internal")
        must = [
            ("窗口/托盘图标", os.path.join(internal, "res", "icon.ico")),
            ("图标母版", os.path.join(internal, "res", "logo.svg")),
            ("页面入口", os.path.join(internal, "web", "index.html")),
        ]
        missing = [label for label, p in must if not os.path.exists(p)]
        if missing:
            print("   !! 产物里缺运行时资源：%s —— 这个包不能用，别发出去" % "、".join(missing))
            return 1
        print("   运行时资源落点 OK（res/icon.ico · res/logo.svg · web/index.html）")
    print()
    print("   exe :", exe)
    print("   体积:", "%.1f MB" % (_size(exe) / 1024 / 1024))
    if not args.onefile:
        d = os.path.dirname(exe)
        print("   目录:", d)
        print("   总大小:", "%.1f MB" % (_du(d) / 1024 / 1024))
    print("   冒烟: python tests\\_diag\\smoke_exe.py" + (" --onefile" if args.onefile else ""))
    print("   时间: %.1fs" % (time.time() - t0))
    return 0


def _size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _du(p: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(p):
        for f in files:
            total += _size(os.path.join(base, f))
    return total


if __name__ == "__main__":
    sys.exit(main())
