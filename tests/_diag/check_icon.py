r"""图标验证：把程序图标从**外面**抠回来，跟母版逐像素比。

跑法（board venv）：

```
python tests/_diag/check_icon.py            # 窗口图标 + 托盘源 + dist 里的 exe 图标
python tests/_diag/check_icon.py --exe <别的 exe 路径>   # 对照另一份构建
```

为什么要这么麻烦：图标是"看得见但测不着"的东西 ——
`webview.start(icon=...)` 传错、`--icon` 没加、`--add-data` 漏了 `res/`，
全都会**静默**回落到 python.exe 的默认图标，单测永远绿。

验三处：

- **窗口图标（标题栏 / 任务栏）**：真开一个窗口，用 `FindWindowW(None, 全等标题)`
  拿 HWND → `SendMessageW(WM_GETICON, ICON_BIG/ICON_SMALL)`，
  兜底 `GetClassLongPtrW(GCLP_HICON/GCLP_HICONSM)` → `GetIconInfo` 取 hbmColor
  → `GetDIBits`（`biHeight` 传**负数**才是自顶向下）→ 转 PIL。
- **exe 文件图标**：`shell32.ExtractIconExW`（Explorer 走的同一条路）再按上面抠图。
- **托盘图标**：`paths.icon_file()` 存在且能打开（托盘读的就是这个文件）。

比对基准是**现场从 `app/res/logo.svg` 重渲染**的那一版 —— 不是另一份预存位图，
所以"母版换了但 ico 忘了重出"这种情况也会当场露馅。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

import paths  # noqa: E402
from version import EXE_STEM, TITLE  # noqa: E402

PY = sys.executable
u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)

u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
u.FindWindowW.restype = ctypes.c_void_p
u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
u.SendMessageW.restype = ctypes.c_void_p
for _name in ("GetClassLongPtrW", "GetClassLongW"):
    if hasattr(u, _name):
        _cls = getattr(u, _name)
        _cls.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _cls.restype = ctypes.c_void_p
        break
u.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u.GetIconInfo.restype = ctypes.c_bool
# ⚠ 句柄/样式必须显式按指针宽度声明，默认 int 会把 64 位值截断（本项目踩过）
g.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
g.DeleteObject.argtypes = [ctypes.c_void_p]

WM_GETICON, ICON_SMALL, ICON_BIG = 0x007F, 0, 1
GCLP_HICON, GCLP_HICONSM = -14, -34
DIBSECTION_SIZE = 104  # BITMAPINFOHEADER + 调色板 + 掩码/色彩空间字段，给够就行


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def hicon_to_image(hicon: int):
    """HICON → PIL RGBA（32bpp，自顶向下）。"""
    from PIL import Image

    info = ctypes.create_string_buffer(64)
    if not u.GetIconInfo(ctypes.c_void_p(hicon), info):
        return None
    # ICONINFO: fIcon(BOOL) xHotspot yHotspot hbmMask hbmColor
    hbm_color = ctypes.c_void_p.from_buffer(info, 24).value
    bm = BITMAP()
    if not g.GetObjectW(ctypes.c_void_p(hbm_color), ctypes.sizeof(BITMAP), ctypes.byref(bm)):
        return None
    w, h = int(bm.bmWidth), int(bm.bmHeight)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h        # 负数 = 自顶向下
    bi.biPlanes, bi.biBitCount = 1, 32
    bi.biCompression = 0                   # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = u.GetDC(None)
    n = g.GetDIBits(hdc, ctypes.c_void_p(hbm_color), 0, h, buf,
                    ctypes.byref(bi), 0)
    u.ReleaseDC(None, hdc)
    if not n:
        return None
    return Image.frombytes("RGBA", (w, h), buf.raw, "raw", "BGRA")


def expected(size: int):
    """现场从母版重渲染一张 —— 比对基准不是预存位图。"""
    import resvg_py
    from PIL import Image

    data = resvg_py.svg_to_bytes(svg_path=paths.logo_svg(), width=size,
                                 skip_system_fonts=True)
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def diff(a, b) -> tuple[float, str]:
    """逐像素比。透明处只比 alpha（底下颜色是垃圾值）。"""
    if a is None or b is None:
        return 999.0, "有一边是空的"
    if a.size != b.size:
        return 999.0, "尺寸不同 %s vs %s" % (a.size, b.size)
    from PIL import ImageChops

    if a.size != b.size:
        a = a.resize(b.size)
    worst = 0.0
    for band_a, band_b in zip(a.split(), b.split()):
        d = ImageChops.difference(band_a, band_b)
        worst = max(worst, max(d.getextrema()))
    # 允许 1 的量化误差：GDI 位图过一手可能 ±1
    return worst / 255.0, ("最大单通道差 %d" % worst)


def check_window_icon(dbfile: str) -> list:
    """真开一个窗口，把它的图标抠回来比。"""
    env = dict(os.environ)
    env["WEBVIEW2_USER_DATA_FOLDER"] = tempfile.mkdtemp(prefix="board_icon_wv2_")
    env["BOARD_NOSANDBOX"] = "1"          # 本机沙箱起不来（见 README 踩坑），降级档一样能验图标
    proc = subprocess.Popen([PY, os.path.join(APP, "main.py"), "--db", dbfile, "--no-tray"],
                            cwd=APP, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = []
    try:
        hwnd = 0
        for _ in range(60):
            time.sleep(1)
            hwnd = u.FindWindowW(None, TITLE) or 0
            if hwnd:
                break
        out.append(("窗口按**全等标题**找得到（%s）" % TITLE, bool(hwnd), ""))
        if not hwnd:
            return out
        time.sleep(2)                      # 等 pywebview 把 icon= 应用上 Form
        for name, flag in (("标题栏", ICON_SMALL), ("任务栏", ICON_BIG)):
            h = u.SendMessageW(ctypes.c_void_p(hwnd), WM_GETICON, ctypes.c_void_p(flag), None) or 0
            src = "WM_GETICON"
            if not h:
                h = (u.GetClassLongPtrW(ctypes.c_void_p(hwnd),
                                        GCLP_HICONSM if flag == ICON_SMALL else GCLP_HICON) or 0)
                src = "GetClassLongPtrW"
            if not h:
                out.append(("%s图标抠得到（%s）" % (name, src), False, "句柄是 0"))
                continue
            img = hicon_to_image(h)
            if img is None:
                out.append(("%s图标抠得到（%s）" % (name, src), False, "GetDIBits 失败"))
                continue
            w, hh = img.size
            exp = expected(w)              # 拿同样边长重渲染一张来比
            d, why = diff(img, exp)
            out.append(("%s图标 = 母版（%dx%d，%s）" % (name, w, hh, src), d <= 0.01,
                        "%s · 差 %.4f" % (why, d)))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(1.5)
        if proc.poll() is None:
            proc.kill()
    return out


def check_exe_icon(exe: str) -> list:
    """exe 文件本身的图标：走 Explorer 那条 ExtractIconExW。"""
    from PIL import Image

    out = []
    big, small = ctypes.c_void_p(), ctypes.c_void_p()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ExtractIconExW.argtypes = [ctypes.c_wchar_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint]
    n = shell.ExtractIconExW(os.path.abspath(exe), 0, ctypes.byref(big), ctypes.byref(small), 1)
    out.append(("exe 资源段里有图标（ExtractIconExW 返回 %d）" % n, n >= 1, ""))
    if n < 1:
        return out
    for label, h in (("大图标", big.value), ("小图标", small.value)):
        if not h:
            continue
        img = hicon_to_image(h)
        if img is None:
            out.append(("exe %s 抠得出来" % label, False, "GetDIBits 失败"))
            continue
        w, hh = img.size
        exp = expected(w)
        d, why = diff(img, exp)
        out.append(("exe %s = 母版（%dx%d）" % (label, w, hh), d <= 0.05,
                    "%s · 差 %.4f" % (why, d)))
    return out


def main() -> int:
    print("母版 :", paths.logo_svg())
    print("ico  :", paths.icon_file(), "存在", os.path.exists(paths.icon_file()))
    print("png  :", paths.logo_png(), "存在", os.path.exists(paths.logo_png()))

    results = []
    # ① 托盘读的就是那个 ico —— 存在且能打开
    from PIL import Image
    try:
        tray = Image.open(paths.icon_file())
        results.append(("托盘图标源（res/icon.ico）能打开 %s" % (tray.size,), tray.size[0] >= 16, ""))
    except Exception as exc:
        results.append(("托盘图标源能打开", False, str(exc)))

    # ② 窗口图标（标题栏 / 任务栏）
    tmp = tempfile.mkdtemp(prefix="board_icon_db_")
    results += check_window_icon(os.path.join(tmp, "board.sqlite"))

    # ③ exe 文件图标（有就验；没有就跳过 —— 源码模式下没有 exe）
    #    产物名从 version.EXE_STEM 取，改名不用回来找这个地方
    exe = os.path.join(ROOT, "dist", EXE_STEM, EXE_STEM + ".exe")
    if "--exe" in sys.argv:
        exe = os.path.abspath(sys.argv[sys.argv.index("--exe") + 1])
    if os.path.exists(exe):
        results += check_exe_icon(exe)
    else:
        print("（没找到 exe，跳过文件图标：%s）" % exe)

    print()
    ok = True
    for label, cond, why in results:
        ok = ok and cond
        print(("  PASS " if cond else "  FAIL ") + label + (("  —— " + why) if why and not cond else ""))
    print()
    print("check_icon: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
