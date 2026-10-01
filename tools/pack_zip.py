"""把 dist/ 下的 exe 产物打成一个 zip，方便拷走給别人。

用法：
    python tools\\pack_zip.py
    python tools\\pack_zip.py --outdir D:\\share     # 换个地方放 zip

**为什么不直接打包整个 dist 目录**：那里混着本机运行状态，打进去会连累接收方——

- `data\\webview2` 是 WebView2 的用户数据目录，几十 MB 的浏览器缓存，还留着本机
  被强杀过的痕迹。残留的 profile 会让**第一台解压的机器**启动失败（0x8007139F），
  症状是窗口弹出但页面空白；
- `data\\config.json` 记着"上次读哪个库"，虽然未必含 db_path，但没有理由带过去；
- `dist\\tests\\smoke_note.txt` 是冒烟回传的自检报告，跟使用者无关。

所以这里显式**按白名单收集**（exe + _internal），而不是"整个目录打包后再删两个"。
后者一旦哪天冒烟又落了新东西在里面，会静默跟着进包。

产物名与版本号都从 `app/version.py` 取，不在工具里再抄一份。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")

sys.path.insert(0, APP)
from version import EXE_STEM, VERSION  # noqa: E402


def collect(src: str) -> list[tuple[str, str]]:
    """返回 [(磁盘路径, zip 内的相对路径)]。只收 exe 与 _internal，其余一概不要。"""
    if not os.path.isdir(src):
        raise SystemExit("找不到打包产物：%s\n先跑：python tools\\build_exe.py" % src)
    out: list[tuple[str, str]] = []
    names = os.listdir(src)
    if EXE_STEM + ".exe" not in names:
        raise SystemExit("里面没有 %s.exe，这是别的构建留下的目录吗？" % EXE_STEM)
    for n in names:
        if n.startswith(".") or ".prev-" in n:
            continue                      # 让位备份、ASCII 中间产物，不带
        full = os.path.join(src, n)
        # ⚠ 一定要包一层以产物名命名的顶层目录：解压出来的应当是一个**文件夹**
        # （整个拷走即可），而不是 exe 和 _internal 散在下载目录里。
        if n == EXE_STEM + ".exe":
            out.append((full, os.path.join(EXE_STEM, n)))
        elif n == "_internal" and os.path.isdir(full):
            for dirpath, _dirs, files in os.walk(full):
                for f in files:
                    if f.endswith(".pyc") or f.endswith(".pyo"):
                        continue
                    p = os.path.join(dirpath, f)
                    out.append((p, os.path.join(EXE_STEM,
                                                os.path.relpath(p, src))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="把 dist 下的 exe 产物打成 zip")
    ap.add_argument("--outdir", default="", help="zip 落目录，默认 dist/")
    args = ap.parse_args()

    t0 = time.time()
    src = os.path.join(ROOT, "dist", EXE_STEM)
    items = collect(src)
    if not items:
        raise SystemExit("没有可打包的内容")

    outdir = args.outdir or os.path.join(ROOT, "dist")
    os.makedirs(outdir, exist_ok=True)
    zip_path = os.path.join(outdir, "%s_v%s.zip" % (EXE_STEM, VERSION))

    raw = sum(os.path.getsize(p) for p, _ in items)
    print("[1/2] 收集 %d 个文件，%.1f MB" % (len(items), raw / 1024 / 1024))
    skipped = [n for n in os.listdir(src)
               if n not in (EXE_STEM + ".exe", "_internal")]
    if skipped:
        print("      已排除本机残留：%s" % "、".join(skipped))

    print("[2/2] 压缩 %s" % EXE_STEM)
    # ZIP_DEFLATED：exe 里那几个大 DLL 压得动，实测 45 MB → 十几 MB
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p, rel in items:
            # 路径分隔符统一成 /（Windows 解压认，别的系统也认）
            z.write(p, rel.replace(os.sep, "/"))

    got = os.path.getsize(zip_path)
    print()
    print("   zip   :", zip_path)
    print("   原始  : %.1f MB" % (raw / 1024 / 1024))
    print("   压缩后: %.1f MB （省了 %.0f%%）"
          % (got / 1024 / 1024, (1 - got / raw) * 100))
    print("   内含  : %s.exe + _internal\\（解压后整个文件夹一起拷，别只拷 exe）"
          % EXE_STEM)
    print("   时间  : %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
