"""把 E 盘的工作空间同步到 NAS 上的副本。

背景：NAS（QNAP SMB）上**权限变更前就存在的文件**对我是只读的 —— 不可覆盖、不可删。
所以同步分两种结果：

* 目标里没有的文件：直接新建，没问题
* 目标里已有的老文件：覆盖会 `PermissionError 13` —— 只能请你在 NAS 端把目录删掉，
  然后带上 `--reset` 重跑，让脚本整份重建（重建出来的文件归我，之后就能正常覆盖了）

用法：

```
python tools/sync_to_nas.py                # 同步（默认不含 dist/build/data）
python tools/sync_to_nas.py --check        # 只体检不写：列出差异 + 哪些老文件会卡住
python tools/sync_to_nas.py --all          # 连 dist/ 一起同步（43MB+）
python tools/sync_to_nas.py --reset        # 先整份删掉目标再重建（目标被清空时用）
python tools/sync_to_nas.py --dst X:\\path # 换目标目录
```

返回码：0 全通；2 有文件写不动（需要你去 NAS 端删目录）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nasfs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DST = r"S:\RND_Projects\Travail\TaskOV"

# 默认不跟过去的：产物 / 中间文件 / 运行时数据 / 缓存
SKIP_DIRS = {"__pycache__", "build", "dist", "data", "_exe_dbg",
             "webview2", "webview2_smoke", ".git", ".workbuddy"}
SKIP_FILE_SUFFIX = (".pyc", ".log", ".tmp")


def _md5(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _walk(root: str, include_dist: bool):
    """产出 (rel_path, abs_path) 列表，rel 用 / 分隔。"""
    out = []
    for base, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(base, root)
        parts = set(rel_dir.replace("\\", "/").split("/")) if rel_dir != "." else set()
        if parts & SKIP_DIRS and not (include_dist and "dist" in parts):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS or (include_dist and d == "dist")]
        for f in files:
            if f.endswith(SKIP_FILE_SUFFIX):
                continue
            rel = f if rel_dir == "." else os.path.join(rel_dir, f)
            out.append((rel.replace("\\", "/"), os.path.join(base, f)))
    return out


def _writable(p: str) -> bool:
    """能不能写这个已存在的文件（只开句柄，不动内容）。"""
    try:
        with open(p, "r+b"):
            return True
    except (PermissionError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", default=DEFAULT_DST, help="目标目录（默认 %s）" % DEFAULT_DST)
    ap.add_argument("--check", action="store_true", help="只体检，不写")
    ap.add_argument("--all", action="store_true", help="连 dist/ 一起同步")
    ap.add_argument("--reset", action="store_true", help="先整份删掉目标再重建")
    args = ap.parse_args()

    dst = os.path.abspath(args.dst)
    items = _walk(ROOT, args.all)
    print("源  :", ROOT)
    print("目标:", dst)
    print("待同步: %d 个文件%s" % (len(items), "（--check 只体检）" if args.check else ""))

    if args.reset and not args.check:
        if not os.path.exists(dst):
            print("  目标不存在，直接新建")
        else:
            n, fails = nasfs.rmtree(dst)
            if fails:
                print("  ! 删不动 %d 项（都是权限变更前就存在的老文件）" % len(fails))
                print("    Windows 侧已无解，请在 NAS 端 File Station / SSH 删掉整个目录后重跑：")
                print("      rm -rf '%s'" % dst)
                return 2
            print("  已删除旧目标（%d 项）" % n)

    os.makedirs(dst, exist_ok=True)

    same = updated = created = blocked = 0
    blocked_list = []
    for rel, src in items:
        target = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True) if os.path.dirname(target) else None
        if os.path.exists(target):
            if _md5(src) == _md5(target):
                same += 1
                continue
            if args.check:
                if _writable(target):
                    updated += 1
                else:
                    blocked += 1
                    blocked_list.append(rel)
                continue
            try:
                shutil.copyfile(src, target)
                updated += 1
            except PermissionError:
                blocked += 1
                blocked_list.append(rel)
        else:
            if args.check:
                created += 1
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(src, target)
                created += 1
            except PermissionError:
                blocked += 1
                blocked_list.append(rel)

    print()
    print("  相同跳过 %d / 已更新 %d / 新建 %d / 写不动 %d" % (same, updated, created, blocked))
    if blocked_list:
        print()
        print("  写不动的前 10 个（都是目标里已存在的老文件）：")
        for r in blocked_list[:10]:
            print("    -", r)
        print()
        print("  处理办法：在 NAS 端删掉 %s 整个目录，然后" % dst)
        print("    python tools\\sync_to_nas.py --reset")
        print("  重建出来的文件归我，之后再同步就能正常覆盖了。")
        return 2
    if args.check:
        print("  （体检模式，没写任何东西）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
