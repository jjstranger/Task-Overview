"""看板工程的版本追踪小助手（git 的简化入口）。

为什么要这个：git 原生命令对这个工程有几个坑 ——
  * 工程在 E 盘、库在 NAS，误加 data/dist 会把几十 MB 垃圾塞进历史
  * 提交信息想写清楚"这轮改了什么"，每次手打太长
  * 出包前忘了提交，回头查"这个 exe 对应的哪版代码"就断线了

用法：
    python tools/ver.py                  # 看当前状态（有没有没提交的改动）
    python tools/ver.py log [n]          # 看最近 n 次提交（默认 15）
    python tools/ver.py save "说明"       # 提交当前改动
    python tools/ver.py tag v1.2 "说明"   # 给当前版本打个标签（里程碑用）
    python tools/ver.py show <版本>       # 看某一版改了什么
    python tools/ver.py files <版本>      # 看某一版涉及哪些文件
    python tools/ver.py diff <版本>       # 跟某一版比现在的差异
    python tools/ver.py back <版本>       # 回到某一版（会先自动存一份当前状态）
    python tools/ver.py undo             # 撤销上一次提交（内容保留在暂存区）
    python tools/ver.py tags             # 列出所有里程碑标签
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def git(*args, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git"] + list(args), cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        print("  git 出错：", msg)
        sys.exit(r.returncode)
    return r


def out(*args) -> str:
    return git(*args).stdout.strip()


def cmd_status() -> int:
    branch = out("rev-parse", "--abbrev-ref", "HEAD") or "main"
    total = out("rev-list", "--count", "HEAD") or "0"
    print("分支：%s    提交数：%s" % (branch, total))
    last = out("log", "-1", "--format=%h %ad %s", "--date=format:%m-%d %H:%M")
    if last:
        print("最新：%s" % last)
    tags = out("tag", "--sort=-creatordate").splitlines()
    if tags:
        print("里程碑：%s" % "、".join(tags[:6]))
    print()
    st = out("status", "--short")
    if not st:
        print("工作区干净，没有未提交的改动。")
        return 0
    lines = st.splitlines()
    print("有 %d 处未提交的改动：" % len(lines))
    for ln in lines[:40]:
        print("   " + ln)
    if len(lines) > 40:
        print("   …… 还有 %d 处" % (len(lines) - 40))
    print()
    print("要提交：python tools\\ver.py save \"这轮改了什么\"")
    return 1


def cmd_log(n: str = "15") -> int:
    try:
        k = int(n)
    except ValueError:
        k = 15
    print(out("log", "-%d" % k,
              "--format=%h  %ad  %s%n           %an", "--date=format:%m-%d %H:%M"))
    return 0


def cmd_save(msg: str) -> int:
    if not msg or not msg.strip():
        print("要写一句说明，不然以后翻历史看不懂这轮干了啥。")
        print('例：python tools\\ver.py save "子环节筛选挪到每行自己的筛选框"')
        return 2
    st = out("status", "--short")
    if not st:
        print("没有改动要提交。")
        return 0
    n = len(st.splitlines())
    git("add", "-A")
    # 把「哪些文件变了」自动附在说明后面 —— 写历史时省事
    files = out("diff", "--cached", "--name-only").splitlines()
    body = "改动文件（%d）：\n" % len(files) + "\n".join("  " + f for f in files[:60])
    if len(files) > 60:
        body += "\n  …… 还有 %d 个" % (len(files) - 60)
    git("commit", "-q", "-m", msg.strip(), "-m", body)
    print("已提交 %d 个文件：%s" % (n, out("log", "-1", "--format=%h %s")))
    return 0


def cmd_tag(name: str, msg: str = "") -> int:
    if not name:
        print("要给标签起个名，比如 v1.0 或 m4-完成")
        return 2
    if out("status", "--short"):
        print("工作区还有未提交的改动，先 save 再打标签，不然标签指的位置不干净。")
        return 2
    git("tag", "-a", name, "-m", msg.strip() or name)
    print("已打标签 %s → %s" % (name, out("rev-parse", "--short", "HEAD")))
    return 0


def cmd_show(rev: str) -> int:
    print(out("show", "--stat", "--format=commit %h%n日期  %ad%n作者  %an%n%n%B",
              rev, "--date=format:%Y-%m-%d %H:%M"))
    return 0


def cmd_files(rev: str) -> int:
    print("该版本涉及的文件：")
    print(out("show", "--name-only", "--format=", rev))
    return 0


def cmd_diff(rev: str) -> int:
    r = git("diff", rev, "--stat", check=False)
    if not r.stdout.strip():
        print("跟 %s 没有差异。" % rev)
    else:
        print("相对 %s 的差异：" % rev)
        print(r.stdout)
    return 0


def cmd_back(rev: str) -> int:
    """回到某一版。**不动 git 历史**，而是把那版的文件内容取回来覆盖工作区，
    并把「当前状态」先存成一个自动提交 —— 回错了还能再回来。"""
    if not rev:
        print("要指定回到哪一版（用 python tools\\ver.py log 查）。")
        return 2
    ok = git("rev-parse", "--verify", rev + "^{commit}", check=False)
    if ok.returncode != 0:
        print("找不到版本：%s" % rev)
        return 2
    target = out("rev-parse", "--short", rev)

    if out("status", "--short"):
        git("add", "-A")
        git("commit", "-q", "-m",
            "自动存档：回退到 %s 之前的现场（%s）"
            % (target, time.strftime("%Y-%m-%d %H:%M:%S")))
        print("当前状态已自动存档：%s" % out("log", "-1", "--format=%h %s"))

    r = git("checkout", target, "--", ".", check=False)
    if r.returncode != 0:
        print("取回文件失败：", (r.stderr or "").strip())
        return 1
    # checkout <rev> -- . 会把内容同时写进**工作区**和**索引**，
    # 于是 git status 会把"跟刚才存档之间的差别"显示成一堆 staged 改动 ——
    # 用户看到"怎么还有改动"会以为没回干净。这里把索引也重置到存档状态，
    # 让 status 反映的正好是「相对于刚才现场，这次回退改了什么」。
    git("add", "-A")
    print("已把工作区恢复成 %s 的内容。" % target)
    print()
    print("注意：这只改了工作区文件，没动历史。")
    print("      觉得对了就 `save` 一版固化；后悔了用 `back %s` 倒回来。"
          % (out("log", "-1", "--format=%h") or "<刚才那个存档>"))
    print("      跟刚才现场相比，这次回退改动如下：")
    d = git("diff", "--cached", "--stat", check=False).stdout.strip()
    print(d or "      （没有差异 —— 两份内容本来就一样）")
    return 0


def cmd_undo() -> int:
    r = git("reset", "--soft", "HEAD~1", check=False)
    if r.returncode != 0:
        print("没有可撤销的提交（可能已经是第一个了）。")
        return 1
    print("已撤销上一次提交，改动还在暂存区，可以直接再 save。")
    return 0


def cmd_tags() -> int:
    t = out("tag", "--sort=-creatordate", "--format=%(refname:short)|%(creatordate:format:%Y-%m-%d %H:%M)|%(contents:subject)")
    if not t:
        print("还没有里程碑标签。用 python tools\\ver.py tag v1.0 \"第一个可用版\" 打一个。")
        return 0
    print("里程碑：")
    for ln in t.splitlines():
        parts = ln.split("|")
        print("  %-14s %s  %s" % (parts[0], parts[1] if len(parts) > 1 else "",
                                  parts[2] if len(parts) > 2 else ""))
    return 0


def main() -> int:
    a = sys.argv[1:]
    if not a:
        return cmd_status()
    c = a[0].lower()
    if c in ("status", "st", "s"):
        return cmd_status()
    if c in ("log", "l"):
        return cmd_log(a[1] if len(a) > 1 else "15")
    if c in ("save", "commit", "c"):
        return cmd_save(" ".join(a[1:]))
    if c in ("tag", "t"):
        return cmd_tag(a[1] if len(a) > 1 else "", " ".join(a[2:]))
    if c in ("show", "sh"):
        return cmd_show(a[1] if len(a) > 1 else "HEAD")
    if c in ("files", "f"):
        return cmd_files(a[1] if len(a) > 1 else "HEAD")
    if c in ("diff", "d"):
        return cmd_diff(a[1] if len(a) > 1 else "HEAD")
    if c in ("back", "backto", "b"):
        return cmd_back(a[1] if len(a) > 1 else "")
    if c in ("undo", "u"):
        return cmd_undo()
    if c in ("tags",):
        return cmd_tags()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
