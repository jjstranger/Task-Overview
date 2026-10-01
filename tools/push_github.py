"""推送到 GitHub（SSH 通道，带重试）。

为什么单独一个脚本而不是直接 `git push`：
这台机器上到 github.com 的 **HTTPS 抖动很大**（2026-10-01 实测：6 次探测量
4 次 10 秒超时、2 次 200），而 **SSH 22/443 一直稳**（0.18s 全通）。
git 自己不会重试超时，所以这里强制走 SSH + 显式重试；万一降落到 HTTPS
（例如 SSH 端口被封）也留一条路。

用法：
    python tools/push_github.py git@github.com:<owner>/<repo>.git
若不传地址，用环境变量 `GIT_REPO` 或默认 `origin` 上已有的地址。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RETRIES = 4          # 总尝试次数（ SSH 通的话第一次就成）
SSH_TEST_TIMEOUT = 8


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def ssh_alive() -> bool:
    """握手探一下：没配好公钥时会快速返回 publickey 而不是卡住。"""
    rc, out = run(["ssh", "-T", "-o", "BatchMode=yes",
                   "-o", f"ConnectTimeout={SSH_TEST_TIMEOUT}",
                   "git@github.com"])
    return rc != 255 and "Could not connect" not in out


def main() -> int:
    repo = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GIT_REPO", "")
    if not repo:
        rc, out = run(["git", "remote", "get-url", "origin"])
        if rc:
            print("没给仓库地址，也没有 origin。用法：")
            print("  python tools/push_github.py git@github.com:<owner>/<repo>.git")
            return 2
        repo = out.strip()
    print(f"目标仓库：{repo}")

    if "github.com" in repo:
        print("SSH 握手探测：", "通" if ssh_alive() else "不通 —— 先去 GitHub 加公钥")
        if not ssh_alive():
            return 3

    rc, out = run(["git", "remote", "add", "origin", repo]) if \
        run(["git", "remote", "get-url", "origin"])[0] else (0, "")
    if rc == 128:                       # 已存在同名 remote，换掉更省事
        run(["git", "remote", "set-url", "origin", repo])
    elif rc:
        print(out.strip())
        return 4

    branch = run(["git", "branch", "--show-current"])[1].strip() or "main"
    for i in range(1, RETRIES + 1):
        print(f"[{i}/{RETRIES}] git push -u origin {branch} …")
        rc, out = run(["git", "push", "-u", "origin", f"HEAD:{branch}"])
        if rc == 0:
            print("\n推送成功。")
            if run(["git", "status", "--porcelain"])[1].strip():
                print("⚠ 还有未提交改动，记得再推一次：git add -A && git commit")
            return 0
        print(out.strip()[:600])
        if i < RETRIES:
            time.sleep(2 * i)           # 退避，别一秒四次猛敲 GitHub
    print(f"\n{RETRIES} 次都失败了。看上面的报错；必要时换成 HTTPS 地址重试。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
