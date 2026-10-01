"""数据文件用不了时的引导启动页。

**为什么需要它**：内置默认库路径是 `<挂载的共享盘>\\<共享目录>\\board.sqlite`，
只在这台机器的 NAS 上存在。软件包拷给别人之后，别人没有 S 盘，
`Db.open()` 第一件事就是 `mkdir` 那个父目录 —— 直接抛异常。
以前这里是一句话错误弹窗然后退出，用户看到的就是"这软件打不开"。

**现在**：起一个极简引导页，**只有一个路径**。用户指一个位置就行：

- 那个位置里已经有数据文件 → 直接用（想用别人给的数据）；
- 没有 → 在那里新建 `board.sqlite`（目录不存在会顺手建出来）。

选完写进 `data/config.json`（应用级配置，跟库放一起又是鸡生蛋），
然后重启自己 —— 这一次就能正常起来了。

⚠ 这一页**不许变复杂**：用户碰到的第一个界面，多一个选项就多一次犹豫。
目录里有没有数据由 `paths.resolve_target()` 自己判断，不要甩回给用户选。

**这一页刻意不依赖数据库**：连不上库的时候还要用的东西，不能靠库。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time

import webview

import paths
from version import BOOT_TITLE

# ⚠ 引导页标题**必须**跟主窗口不一样：按标题找窗口的代码（冒烟、置顶、
# 单实例）用的是全等匹配，两个窗口同名会互相认错。
TITLE = BOOT_TITLE


def _exit_soon(delay: float) -> None:
    """给新进程/日志一点时间，然后用 Win32 硬退。

    跟 main.quit_app 一个道理：主线程卡在 pywebview 的消息循环里时，
    Python 线程可能拿不到 GIL，`sys.exit` 不一定能把进程收掉。
    """
    def _go():
        time.sleep(max(0.0, delay))
        ctypes.windll.kernel32.ExitProcess(0)

    threading.Thread(target=_go, daemon=True).start()


def _restart_soon() -> None:
    """拉起一个新进程，然后把自己退掉。

    ⚠ 调用方**必须先让出单实例互斥量**（`db.close()`）：不然新进程起来
    第一件事就是撞上我们自己持有的互斥量，被当成"重复启动"直接退出，
    表现成"点了确定之后看板就再也没起来"。
    """
    subprocess.Popen(paths.restart_cmd(), cwd=paths.base_dir(), close_fds=True)
    _exit_soon(1.2)


def default_dir() -> str:
    """输入框的默认值：`<程序目录>\\data`。

    拷给别人用的时候最省事的落点 —— 程序目录在哪，数据就在哪，
    不依赖任何网络盘。所以直接把它填好，用户点一下"确定"就能走。
    """
    return paths.data_dir()


def describe_target(path: str) -> dict:
    """这条路径最后会落到哪个数据文件、是"用现成的"还是"新建"。

    给页面显示一行提示用。**只读，不建目录不写文件**（`apply` 才动手）。

    ⚠ 先过一道网络预检：这函数是**每敲一个字**都会被调一次的，
    而碰一个断链的网络盘要等 16.5 秒（见 paths 里那段注释）。
    """
    p = str(path or "").strip().strip('"')
    if not p:
        return {"ok": False, "msg": "还没填路径"}
    net = paths.preflight(p)
    if net:
        return {"ok": False, "msg": net}
    final = paths.resolve_target(p)
    if not final:
        return {"ok": False, "msg": "这个路径看着不太对"}
    exists = os.path.exists(final)
    return {
        "ok": True,
        "input": p,
        "path": final,
        "exists": exists,
        "is_dir_input": os.path.isdir(p),
        "msg": ("找到现成的数据文件，会直接用它" if exists
                else "这里还没有数据文件，会在该位置新建"),
    }


class BootApi:
    """引导页的 JS 桥。

    名字带下划线的属性不会被 pywebview 序列化，内部引用一律下划线开头。
    """

    def __init__(self, reason: str, tried: str):
        self._reason = str(reason or "")
        self._tried = str(tried or "")

    # ---------- 只读 ----------

    def info(self):
        # 这行不只是日志：引导页的端到端测试靠它判断"窗口出来了"之外，
        # "页面真的渲染了、JS 桥真的通了" —— 白窗口和能点的窗口在别处看不出区别。
        paths.log_line("boot.log", "引导页已就绪（info 已被调用）")
        return {
            "ok": True,
            "reason": self._reason,
            "tried": self._tried,
            "dir": default_dir(),
            "config": paths.config_file(),
            # 有原位置就给个「重试」的路子：网络位置的毛病经常是临时的
            # （NAS 刚开机、笔记本换了网络），不该逼用户重选一个位置。
            "can_retry": bool(self._tried),
        }

    def retry(self):
        """再探一次**原来那个位置**；通了就把配置保持原样、重启进主界面。

        这条最省事：网络恢复了就什么都不用改。依然不依赖数据库。
        """
        p = str(self._tried or "").strip()
        if not p:
            return {"ok": False, "msg": "不知道上次用的是哪个位置"}
        net = paths.preflight(p)
        if net:
            return {"ok": False, "msg": net}
        bad = paths.check_db_target(p, allow_new_parent=True)
        if bad:
            return {"ok": False, "msg": bad}
        paths.log_line("boot.log", f"重试原位置成功，重启：{p}")
        _restart_soon()
        return {"ok": True, "path": p}

    def probe(self, path):
        """只回一句"这条路径会怎么处理"，不落地。输入框里改一个字就调一次。"""
        return describe_target(path)

    # ---------- 挑路径 ----------

    def _start_dir(self, start: str) -> str:
        """打开「选择文件夹」时的起始目录。

        ⚠ 每个候选都先过网络预检：老位置往往是**断链的网络盘**
        （引导页十次有九次就是因为它才出现的），`isdir` 一下要等 16 秒。
        """
        cands = [str(start or "").strip(), default_dir(),
                 os.path.dirname(self._tried)]
        for cand in cands:
            if not cand or paths.preflight(cand):
                continue
            if os.path.isdir(cand):
                return cand
        return os.path.expanduser("~")

    def pick_folder(self, start=""):
        """挑一个目录（只有一个路径框，所以挑目录而不是挑文件）。

        目录里有没有数据由 `apply` 那边判断 —— 用户不需要知道这回事。
        """
        win = webview.windows[0] if getattr(webview, "windows", None) else None
        if win is None:
            return {"ok": False, "msg": "窗口还没就绪，稍后再试"}
        d = self._start_dir(start)
        try:
            res = win.create_file_dialog(webview.FileDialog.FOLDER, directory=d)
        except Exception as exc:
            paths.log_line("pick.log", f"boot.pick_folder 失败：{exc!r}")
            return {"ok": False, "msg": "打不开「选择文件夹」窗口：" + str(exc)}
        if not res:
            return {"ok": True, "path": ""}          # 用户取消
        first = res[0] if isinstance(res, (list, tuple)) else res
        return {"ok": True, "path": str(first)}

    # ---------- 落地 ----------

    def apply(self, path):
        """把归一后的路径写进配置并重启。

        目录里已有数据文件就用它，没有就新建 —— 这一步不区分，也不问用户。
        父目录不存在会建出来（别人机器上第一次跑就是这种情况）。
        """
        info = describe_target(path)
        if not info.get("ok"):
            return {"ok": False, "msg": info.get("msg") or "这条路径用不了"}
        p = info["path"]
        bad = paths.check_db_target(p, allow_new_parent=True)
        if bad:
            return {"ok": False, "msg": bad}
        err = paths.save_config({"db_path": p})
        if err:
            return {"ok": False, "msg": "写配置文件失败：" + err}
        paths.log_line(
            "boot.log",
            f"引导页选定数据文件：{p}（{'用现成' if info['exists'] else '新建'}，输入={info['input']}）",
        )
        _restart_soon()
        return {"ok": True, "path": p, "exists": info["exists"]}

    def quit_app(self):
        _exit_soon(0)


def run_boot(reason: str, tried: str) -> int:
    """起引导页并等它结束（用户选完会重启自己，或直接退出）。"""
    api = BootApi(reason, tried)
    html = os.path.join(paths.res_dir(), "web", "boot.html")
    webview.create_window(
        TITLE, url=html, js_api=api, width=680, height=470,
        min_size=(560, 400),
    )
    paths.log_line("boot.log", f"数据文件不可用，进入引导页。tried={tried} reason={reason}")
    webview.start(debug=False, icon=paths.icon_file())
    return 0
