"""从母版 SVG 生成程序图标：`res/icon.ico`（标题栏 / 任务栏 / 托盘 / exe）+ `res/icon.png`。

用法（board venv）：

```
python tools\\make_icon.py            # 出 ico + png
python tools\\make_icon.py --sheet    # 另外拼一张对照图（看小尺寸糊不糊）
```

## 母版在哪

**唯一来源 = `app/res/logo.svg`**（Inkscape 做的 512 网格图，`<矢量母版目录>\\ttov_logo.svg`
已按用户要求拷进程序目录，运行时不再引用 NAS 上的任何路径）。

改形只改这个 svg，再跑一次本脚本 —— `ico` / `png` 都是从它现场光栅化出来的，
不存在"svg 改了图标没跟着变"。

## 为什么用 resvg 而不是 PIL 现画

上一版（三列泳道那张）是用 PIL 照一份坐标表硬画的：能用，但那份坐标表是**第二份真相**，
svg 和 ico 只是"碰巧长得像"。现在这张母版里有渐变、裁剪路径、叠加变换和
`mix-blend-mode:overlay`，PIL 画不了这些；照抄一份等价坐标出来又必然跟母版慢慢走样。

改用 `resvg_py`（Rust resvg 的绑定，纯 wheel 装得上，不需要 cairo/inkscape/node）：
**每个尺寸各自从矢量渲染一遍**，所以 16px 是 resvg 自己在那个尺寸上抗锯齿的结果，
不是把 256 缩下去糊的。

小尺寸（≤32px）本来就糊 —— 环形装饰和落日条纹在这个尺度上只剩几个像素，
这是母版本身的细节量决定的，只能接受；没有单出一份"简化形"，
是因为用户给的就是这一份母版，另画一份就等于又多了第二份真相。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "app", "res")

SVG = os.path.join(RES, "logo.svg")
ICO = os.path.join(RES, "icon.ico")
PNG = os.path.join(RES, "icon.png")

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
BIG = 256                      # icon.png 的边长（网页 favicon / 引导页用）


def _render(size: int) -> Image.Image:
    """把一个尺寸从母版光栅化出来。

    `skip_system_fonts=True`：这张图里没有任何文字，跳过字体加载省时间，
    也免得在别的机器上因为字体差异渲染出不一样的东西。
    """
    import resvg_py

    data = resvg_py.svg_to_bytes(svg_path=SVG, width=size, skip_system_fonts=True)
    img = Image.open(io.BytesIO(bytes(data))).convert("RGBA")
    if img.size != (size, size):
        raise SystemExit(f"渲染出来的尺寸不对：要 {size}×{size}，得到 {img.size}")
    return img


def _sanity(img: Image.Image, size: int) -> None:
    """确认真的画出了东西 —— 不然一个空白 ico 也能"成功"出包。

    看两件事：① 有不透明像素（不是整张全透明）；② 颜色不是单一色
    （resvg 认不出这份 svg 时会渲染成一块纯色，那也是"没画出来"）。
    """
    small = img.convert("RGBA")
    # 别用 getdata()（Pillow 12 起废弃）：通道级 API 更快也更稳。
    if small.getchannel("A").getextrema()[1] <= 8:
        raise SystemExit(f"{size}px：整张全透明，母版没渲染出来")
    if size >= 32:
        # resvg 认不出这份 svg 时会渲染成一块纯色 —— 那也算"没画出来"。
        colors = small.convert("RGB").getcolors(maxcolors=1 << 20) or []
        if len(colors) < 6:
            raise SystemExit(f"{size}px：只有 {len(colors)} 种颜色，看着像渲染失败")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", action="store_true", help="拼一张对照图（8 倍放大看小尺寸）")
    args = ap.parse_args()

    if not os.path.exists(SVG):
        raise SystemExit("找不到母版 SVG：" + SVG)
    blob = open(SVG, "rb").read()
    print("母版:", SVG, len(blob), "字节  md5", hashlib.md5(blob).hexdigest()[:12])

    frames = {}
    for s in ICO_SIZES:
        img = _render(s)
        _sanity(img, s)
        frames[s] = img

    # 每个尺寸**各自**渲染再塞进 ico：Pillow 的 ICO writer 按"尺寸精确匹配"挑图，
    # append_images 给齐就不需要它自己去缩放（它缩得比 resvg 差）。
    frames[BIG].save(
        ICO, format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[frames[s] for s in ICO_SIZES if s != BIG],
        # BMP 条目而不是 PNG 条目：pywebview 的窗口图标走 System.Drawing.Icon，
        # BMP 是它最不挑食的一种；alpha 由 32bpp 的 XOR 位图带着，不会丢。
        bitmap_format="bmp",
    )
    frames[BIG].save(PNG, format="PNG")

    print("ico :", ICO, "尺寸", ICO_SIZES, "%.0f KB" % (os.path.getsize(ICO) / 1024))
    print("png :", PNG, "%d×%d" % (BIG, BIG))

    if args.sheet:
        import tempfile

        strip = [frames[s].resize((s * 8, s * 8), Image.NEAREST) for s in (16, 32, 48)]
        w = frames[BIG].width + 16 + sum(i.width + 16 for i in strip)
        h = max(frames[BIG].height, max(i.height for i in strip))
        sheet = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        x = 0
        sheet.paste(frames[BIG], (x, 0), frames[BIG]); x += frames[BIG].width + 16
        for i in strip:
            sheet.paste(i, (x, 0), i); x += i.width + 16
        p = os.path.join(tempfile.gettempdir(), "icon_sheet.png")
        sheet.convert("RGB").save(p)
        print("对照图:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
