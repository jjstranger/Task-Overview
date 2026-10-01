r"""数据路径用不了时，真的会弹引导页（而不是一句话报错就退出）。

跑法：

    python tests/_diag/smoke_boot.py

**会真开两个窗口**（各停几秒后自己关掉），所以：

- 跑之前先把看板退干净（单实例互斥量会互相挡）
- A、B 各用一个独立的 WebView2 用户数据目录（`data/webview2_boot_a/b`），
  不跟日常那个抢；跑完删掉（共用一个是踩过的坑：前一段的 WebView2 子进程
  还占着 profile，后一段就起不来窗口）

三段验证：

- **0 判断逻辑**（不开窗口）：页面那套"目录里有数据文件就用、没有就新建"的
  归一规则，直接打 `boot.describe_target()`。这是本次简化的核心，
  窗口测试反而验不到它（要真点对话框）。顺带验网络预检
  （`paths.preflight` / `check_db_target` 都不会去碰连不上的网络位置）。
- **A 引导页**：`--db` 指一个**不存在的盘** → 必须出现标题带「选择数据文件」的窗口，
  并且 `data/boot.log` 里要出现「引导页已就绪」——只断言"窗口出现了"是不够的，
  白窗口和能点的窗口在外部看一模一样（这条教训来自那次 TDZ：页面渲染全炸，
  可是窗口、句柄、标题全都正常）。关掉窗口后进程要能退出。
- **B 配置生效**：`data/config.json` 指一个能用的临时库 → 必须**不**出现引导页、
  直接起主窗口。证明"选完记进配置、下次启动就照它来"这条路真的通。
  ⚠ 会临时备份并还原真实的 `data/config.json`。
- **C 网络位置连不上**：`--db` 指一个**保留地址**上的共享 → 引导页必须在
  **十几秒以内**就出来。旧行为是先 mkdir 那个父目录，Windows 去重连断链的
  驱动器，实测白等 16.5 秒（"读不到数据库时启动很慢"的根因）。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))

# 主窗口标题从 version.py 取（**不是**抄一份字面量）：它就是 main._hwnd() 用
# FindWindowW 找窗口的那个全等字符串，改名时这里必须跟着动。
from version import APP_NAME, TITLE as MAIN_TITLE  # noqa: E402

KEYWORD = APP_NAME        # 找"含这个字样的窗口"时用的关键词（子串匹配）
BOOT_TITLE_HINT = "选择数据文件"
APP = os.path.join(ROOT, "app", "main.py")
PY = sys.executable

# 这两项会被 --exe 改写：打包后 `data/` 在 exe 同级（不是项目根），
# 引导页本身也必须真的打进包里（`web/boot.html` 靠 --add-data 收进去）。
CMD = [PY, APP]
BASE = ROOT

# 注意：EnumWindows 那条**不能**给 argtypes —— 回调要按函数指针传，
# 声明成 c_void_p 的话 ctypes 直接拒绝这个参数类型。
_u = ctypes.WinDLL("user32", use_last_error=True)
_u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_u.IsWindowVisible.argtypes = [ctypes.c_void_p]
_u.IsWindowVisible.restype = ctypes.c_bool
_u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p]
WM_CLOSE = 0x0010

fails = []


def chk(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  <" + str(extra) + ">") if extra else ""))
    if not cond:
        fails.append(label)


def windows():
    """[(hwnd, 标题)] —— 只收可见窗口。"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lp):
        if not _u.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        _u.GetWindowTextW(hwnd, buf, 512)
        if buf.value:
            out.append((hwnd, buf.value))
        return True

    _u.EnumWindows(cb, None)
    return out


def find_title(sub):
    """子串匹配。⚠ 引导页标题里也含产品名（`天在看 · 选择数据文件`）——
    所以找主窗口必须用 exact_title()，否则永远"两个都找到了"。"""
    for hwnd, t in windows():
        if sub in t:
            return hwnd, t
    return None, ""


def exact_title(title):
    for hwnd, t in windows():
        if t == title:
            return hwnd, t
    return None, ""


def boot_log_tail(mark):
    """`data/boot.log` 里 mark 之后新增的内容（判断这次运行真的渲染了引导页）。

    ⚠ `mark` 是 `log_size()` 给的**字节数**，所以这里必须用二进制读 ——
    用文本模式 `read(mark)` 是按**字符**数走的，而日志里全是中文（UTF-8 一字 3 字节），
    字符数远小于字节数 → 一下子读到文件末尾，返回空串，
    于是"引导页已就绪"明明写进去了却判 FAIL（日志越长越必错）。
    """
    p = os.path.join(BASE, "data", "boot.log")
    try:
        with open(p, "rb") as f:
            f.read(mark)
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def log_size():
    p = os.path.join(BASE, "data", "boot.log")
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def free_drive() -> str:
    """找一个**真的不存在**的盘符（存在的话 mkdir 会成功，就不会走引导页了）。"""
    for ch in "QZYXWVUT":
        root = ch + ":\\"
        if not os.path.exists(root):
            return root
    return ""


def wait_window(sub, timeout):
    """等一个**标题含 sub** 的窗口。⚠ 不给主窗口用：桌面上开着名字里带产品名的
    文件夹时，资源管理器窗口的标题也含这几个字，子串匹配会立刻命中它并当作
    "找到了"，于是断言在真正的主窗口出现之前就执行了 —— 恒 FAIL，且看不出原因。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        hwnd, title = find_title(sub)
        if hwnd:
            return hwnd, title
        time.sleep(0.4)
    return None, ""


def wait_exact(title, timeout):
    """等标题**完全等于** title 的窗口（找主窗口只能这么找）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        hwnd, t = exact_title(title)
        if hwnd:
            return hwnd, t
        time.sleep(0.4)
    return None, ""


def wait_file(p, timeout):
    """等文件被建出来（主窗口出来时库未必已经落盘）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(p):
            return True
        time.sleep(0.4)
    return False


def kill(proc, hwnd=None, timeout=15):
    if hwnd:
        _u.PostMessageW(hwnd, WM_CLOSE, None, None)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return True
        time.sleep(0.3)
    try:
        proc.kill()
    except Exception:
        pass
    return False


def env_for_run(tag="a"):
    """⚠ A、B 两段各用一个 WebView2 用户数据目录。

    共用一个的话，前一段的进程刚退，WebView2 的子进程还占着 profile 没放，
    后一段就可能起不来窗口（表现成"配置明明对，主窗口就是不出来"）——
    这个坑第一次跑就是这么踩到的。
    """
    env = os.environ.copy()
    env["WEBVIEW2_USER_DATA_FOLDER"] = os.path.join(BASE, "data", "webview2_boot_" + tag)
    # 这台机器上沙箱时好时坏（主程序里已经持久化了 nosandbox=1）。
    # 引导页要在"跟日常启动一样的条件"下验证，所以也带上。
    env["BOARD_NOSANDBOX"] = "1"
    env.pop("BOARD_DB", None)
    return env


def hard_rmtree(path):
    """kernel32 直删。

    ⚠ 本机的 safe-delete shim 对**一批文件**（>50）会 FAIL_CLOSED：既不删，
    还会把进程带崩（表现为：所有断言 PASS，脚本却 exit=1、连收尾那行
    「boot smoke: OK」都不打印）。profile 动辄上百个文件，所以这里不用
    shutil.rmtree。
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import nasfs
    return nasfs.rmtree(path)


def clean_profiles():
    """清掉冒烟专用的 WebView2 目录：被强杀过的 profile 会让下次启动白屏。"""
    for tag in ("a", "b", "old"):
        hard_rmtree(os.path.join(BASE, "data", "webview2_boot_" + tag))


def phase_logic():
    """不开窗口，只验页面调的那个判断：目录里有数据文件就用它，没有就新建。"""
    print("\n[0] 路径归一（引导页只有一个路径框，全靠这一层自己判断）")
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import sqlite3
    import boot
    import paths

    have = tempfile.mkdtemp(prefix="board_boot_have_")
    empty = tempfile.mkdtemp(prefix="board_boot_empty_")
    try:
        real = os.path.join(have, "myboard.sqlite")
        sqlite3.connect(real).execute("CREATE TABLE t(x)")
        r = boot.describe_target(have)
        chk("0 目录里已有数据文件 → 直接用它（不新建）",
            r.get("ok") and r.get("path") == real and r.get("exists") is True, r.get("path"))
        r = boot.describe_target(empty)
        chk("0 目录里没有 → 在那里新建 board.sqlite",
            r.get("ok") and r.get("path") == os.path.join(empty, "board.sqlite")
            and r.get("exists") is False, r.get("path"))
        r = boot.describe_target("")
        chk("0 空路径被拒绝（不静默通过）", r.get("ok") is False, r.get("msg"))
        r = boot.describe_target(os.path.join(empty, "sub", "deep"))
        chk("0 不存在的目录当成目录（新建），不猜成无扩展名文件",
            r.get("ok") and r.get("path") == os.path.join(empty, "sub", "deep", "board.sqlite"),
            r.get("path"))
        # 页面给用户看的那句话得跟实际行为一致
        chk("0 提示语与行为一致（有→读取 / 无→新建）",
            "直接用它" in boot.describe_target(have).get("msg", "")
            and "新建" in boot.describe_target(empty).get("msg", ""),
            boot.describe_target(empty).get("msg"))

        # ---- 网络位置预检（启动提速那一改，是"读不到库时启动很慢"的解药）----
        # 背景：断链的映射第一次被访问要等 Windows 重连，本机实测 16.5 秒。
        t0 = time.time()
        chk("0 本地路径不需要预检（返回空、且快）",
            paths.preflight(os.path.join(have, "board.sqlite")) == ""
            and time.time() - t0 < 0.5, f"{time.time() - t0:.2f}s")
        chk("0 UNC 路径认得出主机名",
            paths.net_host(r"\\<NAS名>\<共享名>\x\board.sqlite") == "<NAS名>"
            and paths.net_host("E:\\x\\board.sqlite") == "")
        # 192.0.2.1 是保留地址（TEST-NET-1），一定连不上 —— 拿它当"NAS 没开"
        t0 = time.time()
        bad = paths.preflight(r"\\192.0.2.1\share\board.sqlite")
        dt = time.time() - t0
        chk("0 连不上的网络位置被预检拦下（而且不拖时间）",
            bool(bad) and dt < 4.0, f"{bad!r} {dt:.2f}s")
        t0 = time.time()
        bad2 = paths.check_db_target(r"\\192.0.2.1\share\board.sqlite")
        chk("0 校验数据文件目标也走预检（先拦下来，不去碰那个路径）",
            bool(bad2) and time.time() - t0 < 4.0, f"{bad2!r}")
        r = boot.describe_target(r"\\192.0.2.1\share\board.sqlite")
        chk("0 引导页的路径提示同样先过预检（每敲一个字都会调它）",
            r.get("ok") is False and "连不上" in (r.get("msg") or ""), r.get("msg"))
    finally:
        shutil.rmtree(have, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)


def phase_a():
    print("\n[A] --db 指一个不存在的盘 → 应当出现引导页")
    drive = free_drive()
    if not drive:
        chk("A 找到可用的空盘符", False, "所有候选盘符都真实存在")
        return
    bad = os.path.join(drive, "__no_such_dir__", "board.sqlite")
    mark = log_size()
    proc = subprocess.Popen(CMD + ["--db", bad, "--no-tray"],
                            cwd=BASE, env=env_for_run("a"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd, title = wait_window(BOOT_TITLE_HINT, 40)
    chk("A 引导页窗口出现（不是一闪而过的报错弹窗）", hwnd is not None,
        title or f"没找到含「{BOOT_TITLE_HINT}」的窗口")
    # 窗口先出来、页面后渲染 —— 新 profile 下 WebView2 起得慢，
    # 所以要等日志，而不是窗口一出现就去读一次。
    ready = ""
    t0 = time.time()
    while time.time() - t0 < 30:
        ready = boot_log_tail(mark)
        if "引导页已就绪" in ready:
            break
        time.sleep(0.5)
    chk("A 引导页真的渲染了（后端收到 info 调用，页面不是白窗口）",
        "引导页已就绪" in ready, ready.strip()[-160:])
    chk("A 主窗口没被一起开出来", exact_title(MAIN_TITLE)[0] is None,
        exact_title(MAIN_TITLE)[1])
    gone = kill(proc, hwnd)
    chk("A 关掉引导页后进程自己退出", gone)


def phase_b():
    print("\n[B] data/config.json 指一个能用的库 → 不该再进引导页")
    cfg = os.path.join(BASE, "data", "config.json")
    # ⚠ 原始内容读进**内存**、还原时直接写回，全程**不落备份文件**。
    # 以前是 `config.json.smokebak` + 收尾 unlink，在这台机器上会被删除兜底掐死
    # （2026-09-30 实测：那一下把整趟冒烟打断，C 段根本跑不到）——
    # 少一个临时文件，也就少一次删除，问题从根上没了。
    orig = None
    if os.path.exists(cfg):
        with open(cfg, "rb") as f:
            orig = f.read()
    tmp = tempfile.mkdtemp(prefix="board_boot_")
    target = os.path.join(tmp, "board.sqlite")
    proc = None
    try:
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{\n  "db_path": %s\n}\n' % _json_str(target))
        proc = subprocess.Popen(CMD + ["--no-tray"], cwd=BASE, env=env_for_run("b"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # ⚠ 必须等**完全等于** MAIN_TITLE 的窗口，不能用子串 —— 桌面上开着
        # 名字里带这三个字的文件夹时，资源管理器窗口的标题也含它。
        hwnd, title = wait_exact(MAIN_TITLE, 60)
        bh, bt = find_title(BOOT_TITLE_HINT)
        chk("B 直接起了主窗口（配置里的路径被采纳）", hwnd is not None,
            title or "60 秒内没等到标题恰为「%s」的窗口" % MAIN_TITLE)
        chk("B 没有误弹引导页", bh is None, bt)
        # 库是主窗口起来之后才建的，别立刻断言（冷启动要十几秒）
        chk("B 库文件确实建在配置指定的位置", wait_file(target, 30), target)
    finally:
        if proc is not None:
            # 主窗口的关闭事件是「缩到托盘」，WM_CLOSE 不会退 —— 这里直接收掉进程，
            # 反正它用的是冒烟专用的 WebView2 目录，跑完就删。
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
        # 收尾一律不得抛异常：这里只是把真实 config.json 还原回去，
        # 出问题也绝不能因此让后面的 C 段跑不到。
        try:
            if orig is None:
                # 本来就没有：留一份原样的（应用自己启动也会写这个文件），
                # **刻意不删** —— 删除在本机是被兜底拦的，为 25 字节冒这个险不值。
                with open(cfg, "w", encoding="utf-8") as f:
                    f.write("{}\n")
            else:
                with open(cfg, "wb") as f:
                    f.write(orig)
        except Exception as exc:
            print("   （收尾警告：config.json 还原没做干净：%s）" % exc)
        shutil.rmtree(tmp, ignore_errors=True)


def phase_c():
    """数据位置是**连不上的网络共享**时，启动必须快（这是"读不到库时启动很慢"那一改）。"""
    print("\n[C] 网络位置连不上 → 不碰它、直接进引导页，而且要快")
    bad = r"\\192.0.2.1\share\__no_such_dir__\board.sqlite"   # 保留地址，必然连不上
    mark = log_size()
    t0 = time.time()
    proc = subprocess.Popen(CMD + ["--db", bad, "--no-tray"],
                            cwd=BASE, env=env_for_run("a"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd, title = wait_window(BOOT_TITLE_HINT, 40)
    dt = time.time() - t0
    chk("C 引导页窗口出现（没有卡在访问那个网络路径上）", hwnd is not None,
        title or f"没找到含「{BOOT_TITLE_HINT}」的窗口")
    # 旧行为：先 mkdir 那个父目录 → Windows 去重连断链驱动器 → 白等 16.5 秒才轮到引导页。
    # 预检把它压到毫秒级，这里给 12 秒的宽松上限。
    chk("C 而且很快就出来了（旧行为要等十几秒）", dt < 12.0, f"{dt:.1f}s")
    tail = boot_log_tail(mark)
    chk("C 日志里写明了是预检拦下的（不是一句笼统的「打不开」）",
        "预检不通过" in tail, tail.strip()[-160:])
    chk("C 主窗口没被一起开出来", exact_title(MAIN_TITLE)[0] is None,
        exact_title(MAIN_TITLE)[1])
    kill(proc, hwnd)


def _json_str(s):
    import json
    return json.dumps(s, ensure_ascii=False)


def init_cmd() -> None:
    """`--exe <路径>`：拿打包后的 exe 跑同一套检查（引导页有没有真的打进包里、
    data/ 有没有落在 exe 同级，只有真跑 exe 才知道）。"""
    global CMD, BASE
    if "--exe" in sys.argv:
        exe = os.path.abspath(sys.argv[sys.argv.index("--exe") + 1])
        CMD = [exe]
        BASE = os.path.dirname(exe)


def main() -> int:
    init_cmd()
    print("boot guide smoke —— 数据路径用不了时会不会真的给出路")
    print("  目标：", " ".join(CMD))
    clean_profiles()
    try:
        phase_logic()
        phase_a()
        phase_b()
        phase_c()
    finally:
        clean_profiles()
    print()
    print("boot smoke:", "OK" if not fails else "FAILED")
    for f in fails:
        print("  -", f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
