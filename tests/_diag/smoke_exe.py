r"""打包后的 exe 冒烟：验证「能起、页面渲染正常、data 落在 exe 同级」。

跑法（先 `python tools/build_exe.py` 出包）：

```
python tests/_diag/smoke_exe.py                     # 默认 persist（库里有 nosandbox=1）
python tests/_diag/smoke_exe.py fallback            # 给环境变量 BOARD_NOSANDBOX=1
python tests/_diag/smoke_exe.py sandbox             # 强制开沙箱跑一次（这台机器上多半会挂）
python tests/_diag/smoke_exe.py --onefile           # dist\天在看_Travail_Task_Overview.exe
python tests/_diag/smoke_exe.py --exe <路径>        # 指定别的构建做对照
```

它做四件事：

1. 往临时库塞一份带财务数据的样本（复用 `smoke_fin_gui.seed`，保证和源码冒烟同一份数据）
2. 用 `--smoke --no-tray` 起 exe，等它把页面状态写进 `<exe 同级>/tests/smoke_note.txt`
3. 断言渲染统计与源码版一致，且 `frozen=True`、`data` 就在 exe 同级（**不是**解包临时目录）
4. 报出 exe 同级 `data\` 的位置（默认**不删**，删目录不可逆；要清加 `--clean`）

为什么值得单独一份：打包出问题几乎都不是"代码逻辑错"，而是**资源没带进去 / 路径算错了**。
这两类在源码模式下永远测不出来，只有真的跑 exe 才会暴露。

为什么要分沙箱两档：这台机器的 WebView2 渲染进程沙箱时好时坏（见 README 踩坑记录），
沙箱起不来时症状是"页面加载了、桥也通了，但 JS 过了一会儿就没动静"——
自检报告会停在半路（`stages` 只有前几项），特别像应用自己的 bug。所以默认走降级档，
另留 `sandbox` 档专门复现那个环境问题。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

from smoke_fin_gui import seed  # noqa: E402  同一个 seed，别再造一份数据
from version import (  # noqa: E402
    AUMID, EXE_STEM, TITLE, VERSION, VERSION_LINE)

FINAL_NAME = EXE_STEM  # 产物名同源，改品牌名这里自动跟上（见 app/version.py）
LOG = os.path.join(HERE, "_smoke_exe.log")


def kill(path: str) -> None:
    """删目录树。WebView2 有时候会晚一拍松句柄，重试几次。"""
    for _ in range(5):
        if not os.path.exists(path):
            return
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.6)


def main() -> int:
    logf = open(LOG, "w", encoding="utf-8")

    def say(*a):
        line = " ".join(str(x) for x in a)
        print(line)
        logf.write(line + "\n")
        logf.flush()

    onefile = "--onefile" in sys.argv
    # 沙箱档位：persist（库 flag）/ fallback（环境变量）/ sandbox（强制开，用来复现环境问题）
    modes = [a for a in sys.argv[1:] if a in ("persist", "fallback", "sandbox")]
    mode = modes[0] if modes else "persist"
    # --exe <路径>：指向别处的构建（比如 tests\_diag\_exe_dbg 下的带控制台对照版）
    forced = ""
    if "--exe" in sys.argv:
        forced = sys.argv[sys.argv.index("--exe") + 1]
    if forced:
        # onedir 下 exe 就在自己的目录里，note / data 都相对它算
        exe = os.path.abspath(forced)
        exe_dir = os.path.dirname(exe)
    elif onefile:
        exe = os.path.join(ROOT, "dist", FINAL_NAME + ".exe")
        exe_dir = os.path.join(ROOT, "dist")
    else:
        exe_dir = os.path.join(ROOT, "dist", FINAL_NAME)
        exe = os.path.join(exe_dir, FINAL_NAME + ".exe")

    if not os.path.exists(exe):
        say("FAIL: 找不到 exe ——", exe)
        say("      先跑 python tools\\build_exe.py")
        return 1

    tmp = os.path.join(os.environ.get("TEMP", "."), "board_exe_smoke_%d" % os.getpid())
    kill(tmp)
    os.makedirs(tmp, exist_ok=True)
    db_path = os.path.join(tmp, "board.sqlite")
    seed(db_path, nosandbox=(mode == "persist"))
    say("exe     :", exe)
    say("mode    :", mode)
    say("temp db :", db_path)
    say("size    : %.1f MB" % (os.path.getsize(exe) / 1024 / 1024))

    note = os.path.join(exe_dir, "tests", "smoke_note.txt")

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if mode == "fallback":
        env["BOARD_NOSANDBOX"] = "1"
    else:
        env.pop("BOARD_NOSANDBOX", None)
        env.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)

    # 与 GUI 冒烟同一套处理：WebView2 渲染进程会被安全策略时断时续地拖慢，
    # 表现为自检跑一半停在某个 phase（err="超时 25000ms @ xxx"）。
    # 后端实测是毫秒级，所以那是环境问题不是打包问题 —— 自动重跑一次。
    # 「页面根本没起来」（没有 note / 非 JSON）不重试，那是真故障。
    def run_once():
        t0 = time.time()
        proc = subprocess.Popen(
            [exe, "--smoke", "--no-tray", "--db", db_path],
            cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out = b""
        try:
            out, _ = proc.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        took = time.time() - t0
        return proc.returncode, took, out.decode("utf-8", "replace")

    def clear_note():
        # 清掉上一次的报告。**不能直接 remove**：exe 目录在网络盘上时没有回收站，
        # 删除会被兜底成失败 —— 这里要的只是"别读到旧内容"，清空就够了。
        if os.path.exists(note):
            try:
                os.remove(note)
            except Exception:
                with open(note, "w", encoding="utf-8") as _f:
                    _f.write("")

    clear_note()
    code, took, child = run_once()
    say("exit=%s  用时 %.1fs" % (code, took))
    if child.strip():
        say("--- exe stdout ---")
        say(child[-2000:])

    if os.path.exists(note):
        try:
            probe = json.loads(open(note, encoding="utf-8").read())
        except Exception:
            probe = {}
        if isinstance(probe.get("err"), str) and probe["err"].startswith("超时") \
                and probe.get("stages"):
            say("--- 自检在 %s 阶段超时，这是本机环境抖动，自动重跑一次 ---"
                % (probe.get("failed") or "?"))
            clear_note()
            code, took, child = run_once()
            say("exit=%s  用时 %.1fs（重跑）" % (code, took))
            if child.strip():
                say("--- exe stdout（重跑） ---")
                say(child[-2000:])

    ok = True

    def chk(label, cond):
        nonlocal ok
        say(("  PASS " if cond else "  FAIL ") + label)
        ok = ok and cond

    if not os.path.exists(note):
        say("FAIL: exe 没有写出 smoke_note.txt（页面没报到，或崩在启动阶段）")
        crash = os.path.join(exe_dir, "data", "launch_error.log")
        if os.path.exists(crash):
            say("--- launch_error.log 尾部 ---")
            say(open(crash, encoding="utf-8", errors="replace").read()[-2000:])
        logf.close()
        return 1

    try:
        rep = json.loads(open(note, encoding="utf-8").read())
    except Exception as exc:
        say("FAIL: smoke_note 不是 JSON：", exc)
        logf.close()
        return 1

    tree = rep.get("tree", {})
    fin_ = rep.get("fin", {})
    ex_ = rep.get("export", {})
    heat = rep.get("heat", {})
    m4 = rep.get("m4", {})
    neww = rep.get("neww", {})
    nd = rep.get("nodes", {})
    cr = rep.get("create", {})

    chk("exe 能启动并报到", bool(tree) and (tree.get("rows") or 0) > 0)
    chk("页面无 JS 错误 (err=null)", rep.get("err") is None)
    chk("自检巡视跑完全程 (failed=null)", rep.get("failed") is None)
    chk("web/ 资源打进去了（默认折叠态：树里 5 行）", tree.get("rows") == 5)
    chk("默认一个环节都不展开、工具栏 = 筛选 + 多选",
        tree.get("nodeRows") == 0 and tree.get("treeBtns") == 2)
    chk("顶栏三个页签（项目 / 财务 / 活跃）",
        tree.get("pages") == 3 and tree.get("btns") == ["项目", "财务", "活跃"])
    chk("热力图渲染出来（371 格）", heat.get("cells") == 371)
    chk("点页签能切到财务页", fin_.get("panel") == "block" and fin_.get("tabAct") == "fin")
    chk("财务数据读得到（6 卡 / 两行三张 + 账龄 4 档）",
        fin_.get("cards") == 6 and fin_.get("cardRows") == [3, 3]
        and fin_.get("aging") == 4)
    chk("财务默认看本年份", fin_.get("yearVal") == str(date.today().year))
    # 项目页筛选：这是本轮新加的前端 + 后端（load 带 include_archived），打包后必须还在
    chk("项目页有「筛选 ▾」按钮", (tree.get("filterText") or "").strip() == "筛选 ▾")
    chk("筛选菜单首项是「隐藏归档项目」且默认勾上",
        "隐藏归档项目" in ((m4.get("filterItems") or [""])[0] or ""))
    chk("项目数据带 archived 字段（归档开关靠它）", tree.get("archField") is True)
    chk("状态「归档」的项目默认也隐藏（两条归档路径都认）",
        m4.get("archStatOff") is True and m4.get("archStatOn") is True)
    # 到期小标文案 + 状态徽章定位（2026-09-30）：前端改动必须在打包态也生效
    chk("到期小标写整句话、没有 D-n（%s）" % (tree.get("dueTexts"),),
        any("天后到期" in t for t in (tree.get("dueTexts") or []))
        and not any(t.startswith("D-") for t in (tree.get("dueTexts") or [])))
    sc = tree.get("stClick") or {}
    chk("点状态徽章才弹状态菜单；点「今日必做」那颗不弹（%s / %s）"
        % (sc.get("stText"), sc.get("firstText")),
        sc.get("firstText") == "今日必做"
        and (sc.get("stText") or "") not in ("", "今日必做")
        and bool(sc.get("menu")) and sc.get("firstPops") is False)
    # 品牌 / 版本号（2026-09-30）：改名 + 顶栏不显示名字，打包态必须一起在。
    # ⚠ 顶栏 **不写字**（2026-10-01 用户口径）—— 名字已经在窗口标题栏上，
    #   这里断言「图标还在、文字没了」，别拿品牌名去对。
    chk("顶栏不显示名字、只留图标（实得 文字%r / %s 个 img）"
        % (tree.get("brandText"), tree.get("brandImg")),
        tree.get("brandText") == "" and tree.get("brandImg") == 1)
    _hdr = tree.get("headerOrder") or []
    _seq = ("segPage", "date", "btnRefresh")
    chk("顶栏顺序是 图标→页签→日期→刷新（实得 %s）" % (_hdr,),
        all(x in _hdr for x in _seq)
        and _hdr.index("segPage") < _hdr.index("date") < _hdr.index("btnRefresh"))
    chk("document.title 带版本号（实得 %s）" % tree.get("docTitle"),
        tree.get("docTitle") == TITLE and VERSION in tree.get("docTitle", ""))
    chk("设置页版本行 = 版本号 + 发布日期（实得 %s）" % tree.get("verLine"),
        tree.get("verLine") == VERSION_LINE)
    chk("活跃页提示文案是用户要的那句（实得 %s）" % heat.get("tip"),
        (heat.get("tip") or "")
        == "打卡 或环节变更为 制作中/已提交/反馈/可优化/交付 才会计入活跃。")
    chk("外包支出 / 实际收入口径对得上",
        (fin_.get("money") or {}).get("outsource") == 20000
        and (fin_.get("money") or {}).get("net") == 40000)
    # 财务筛选栏：年份 / 客户下拉、币种整组撤掉、导出收进一个菜单（前端改动也要过打包这关）
    chk("财务筛选栏：年份是下拉、币种组已撤",
        fin_.get("yearTag") == "SELECT" and fin_.get("ccySeg") == 0)
    chk("导出下拉菜单（导出… / 打开导出目录）",
        [x for x in (fin_.get("exportMenu") or []) if x] == ["导出…", "打开导出目录"])
    # 导出选项弹窗（范围 / 项目 / 内容 / 文件名预览）—— 打包后前端也得是同一份。
    # ⚠ 只看界面：#exOk 会弹模态系统对话框，冒烟里不能点（smoke_fin_gui.py 有同样的注释）
    chk("导出选项弹窗能打开、默认 Excel + 四项内容全勾",
        ex_.get("dlg") is True and ex_.get("fmtAct") == "xlsx"
        and ex_.get("secsOn") == ["records", "projects", "clients", "aging"])
    chk("导出预览文件名是 board_finance_*.xlsx (%s)" % ex_.get("planName"),
        str(ex_.get("planName") or "").startswith("board_finance")
        and str(ex_.get("planName") or "").endswith(".xlsx"))
    chk("导出弹窗：选指定项目出列表、选自定义出日期框",
        ex_.get("prjShown") is True and (ex_.get("prjN") or 0) >= 1
        and ex_.get("datesShown") is True)
    chk("导出弹窗：CSV 锁成只导款项明细",
        ex_.get("csvOn") == ["records"] and ex_.get("csvOff") == 4)
    chk("制作人字段打进包了（行里显示 + 菜单可改）",
        rep.get("artist", {}).get("tags") == 1)
    chk("拖拽 / 快照等 M4 功能可用", m4.get("moved") is True and m4.get("snapOk") is True)
    # 进度条：状态改「通过」就该计入完成（打包后也用同一份前端）
    chk("进度条按状态算（1/1 · 100%）",
        (m4.get("prog") or {}).get("after") == [1, 1]
        and (m4.get("prog") or {}).get("barW") == "100%")
    chk("环节状态菜单 = 制作流转 10 态（含新增的「可优化」）",
        m4.get("nodeStatus") == ["待开始", "等上游", "制作中", "暂停", "中止",
                                 "已提交", "反馈", "可优化", "通过", "交付"])
    # 打包最容易漏的是「新增的模块」（node_spec）和「新绑的事件」——这两条就是冲着它们去的
    chk("新建项目弹窗：客户框是组合框 + 浏览已绑事件",
        neww.get("clientTag") == "INPUT" and neww.get("pickBound") is True)
    # 截止日期：原生 date 框 + 空值时自绘的 YYYY/MM/DD
    # （跟 smoke_fin_gui.py 同一套断言，改一侧必须两处一起改）
    chk("截止日期是原生 date 框 + 空值时显示自绘「YYYY/MM/DD」（%s / %s / 显示=%s）"
        % (neww.get("dueType"), neww.get("duePh"), neww.get("duePhShown")),
        neww.get("dueType") == "date" and neww.get("duePh") == "YYYY/MM/DD"
        and neww.get("duePhShown") is True)
    chk("自绘占位不吃点击（点到的必须是输入框自己，%s）" % neww.get("duePhHit"),
        neww.get("duePhHit") == "newDl")
    chk("截止日期置 ISO 后落库（%s → %s）"
        % (cr.get("dueIn"), cr.get("dueStored")), cr.get("dueStored") == "2026-12-31")
    chk("有值时自绘占位收起、清空后回来（%s / %s）"
        % (cr.get("duePhWithVal"), cr.get("duePhBack")),
        cr.get("duePhWithVal") == "none" and cr.get("duePhBack") == "block")
    chk("原生 date 框拒绝非法文本（下周三→%r、2026/12/31→%r）"
        % (cr.get("rejectCn"), cr.get("rejectSlash")),
        cr.get("rejectCn") == "" and cr.get("rejectSlash") == "")
    chk("客户框输入新名字会自动建客户（跨桥 + 落库）",
        cr.get("newClient") == "冒烟新客户X")
    chk("批量录入一行建出 6 个环节、重复批次全跳过（node_spec 打进包了）",
        nd.get("n") == 7 and nd.get("dupCreated") == 0 and nd.get("dupSkipped") == 6)
    # 前端文案也算"打进包了"的一部分：热替换漏了某个文件、或打包收错目录都该被逮住
    _hs = str(nd.get("hintSay") or "")
    chk("弹窗文案随包发出：添加到 / 占位 / 空态 / 新说明句",
        str(nd.get("toSay") or "").startswith("添加到：")
        and nd.get("phSay") == "填写环节名称..."
        and nd.get("emptySay") == "填写环节名称后，这里将展示。"
        and "使用英文" in _hs and "英文双引号" in _hs
        and "顿号" not in _hs and "名字里本来" not in _hs)
    chk("跑的是打包模式 (frozen=True)", tree.get("frozen") is True)
    # 通知身份：显式 AUMID 注册成功（pathinfo 回读的是系统真实值）。
    # 不设的话 Win11 通知显示「天在看.exe」+ fallback 图标（2026-10-01 蓝方块就是它）
    chk("通知身份已显式注册 (aumid=%s)" % tree.get("aumid"),
        tree.get("aumid") == AUMID)
    # 本轮四项修复：置顶要问得到真实状态（Win32 那层最容易在打包后掉链子）、
    # 客户开关与子环节筛选是纯前端，但同样得走一遍真实入口才算数
    fx = rep.get("fix") or {}
    tp = fx.get("top") or {}
    chk("置顶：问得到真实状态且开/关都真的生效",
        tp.get("queried") is True and tp.get("onReal") is True and tp.get("offReal") is True)
    chk("置顶：按钮亮灭跟窗口真实状态一致", tp.get("btnSync") is True)
    cl = fx.get("client") or {}
    chk("客户开关：关掉后客户名消失、再点回来",
        (cl.get("before") or 0) > 0 and cl.get("after") == 0
        and cl.get("back") == cl.get("before"))
    nf = fx.get("nodeFilter") or {}
    chk("子环节筛选框：有子环节的行上各有一个，带搜索框",
        nf.get("hasBtn") is True and nf.get("hasInput") is True
        and nf.get("txtHit") is True and nf.get("txtTrimmed") is True
        and nf.get("txtNoLeak") is True)
    chk("子环节筛选：按状态筛完只剩该状态及其父环节、清除后恢复",
        (nf.get("feedbackRows") or 0) > 0 and nf.get("statKeepOk") is True
        and nf.get("statTrimmed") is True and nf.get("cleared") is True)
    # 制作人反选的补集要带上「未分配」，否则没派工的环节会被一起吞掉（2026-10-04 用户报）
    _nm = nf.get("multi") or {}
    if _nm.get("hasWhoGroup") and _nm.get("noneInLayer"):
        chk("制作人反选：补集含「未分配」且没人派工的环节还在屏幕上 (rows=%s)"
            % (_nm.get("whoInvRows"),),
            (_nm.get("whoInv") or [None])[-1] == ""
            and _nm.get("noneKept") is True and _nm.get("whoDropped") is True
            and _nm.get("noneRowShown") is True and _nm.get("noneRowTicked") is True)

    # 设置项逐项测定 / 多选批量 / 数据文件可设定（都是这一轮新加的）
    st = rep.get("settings") or {}
    chk("设置项逐项有效（提醒强度 / 打卡时间 / 停滞 / 重提醒 / 开关 / 配色）",
        st.get("allOk") is True)
    chk("设置：重开面板回显的是库里的值",
        st.get("echoOk") is True and not (st.get("bad") or []))
    mu = rep.get("multi") or {}
    chk("多选批量：点「多选」出现复选框、批量条露出来",
        (mu.get("pickAfter") or 0) >= 3 and mu.get("barAfter") == "flex")
    # 用户报的核心 bug：点复选框本身时勾选显示差一拍
    chk("多选批量：点复选框本身当场显示勾",
        (mu.get("pickClick1") or {}).get("ok") is True)
    # 2026-10-02 语义变更：直接点 = **单选**（第一条让位）；连点同一条 = 取消。
    #   以前这里写的是"第二条不影响第一条"，恰好把累加 bug 当成了正确行为。
    chk("多选批量：直接点第二条 = 单选（第一条让位）；再点同一条 = 全取消",
        (mu.get("pickClick2") or {}).get("ok") is True
        and (mu.get("pickClick3") or {}).get("ok") is True)
    chk("多选批量：Ctrl 点仍然是加选",
        (mu.get("pickCtrl") or {}).get("ok") is True)
    chk("多选批量：状态色块是竖条（比老圆点醒目）", mu.get("dotOk") is True)
    chk("多选批量：点父环节级联选中它 + 全部后代、再点整棵撤掉",
        mu.get("hasParent") is True
        and mu.get("cascadeSel") == mu.get("cascadeWant")
        and mu.get("cascadeAllIn") is True
        and mu.get("cascadeOff") == 0 and mu.get("cascadeNoneIn") is True)
    chk("多选批量：Shift 范围选覆盖中间整段、再 Shift 整段取消",
        (mu.get("shift") or {}).get("all") is True
        and (mu.get("shift") or {}).get("span", 0) >= 2
        and (mu.get("shiftOff") or {}).get("sel") == 0)
    chk("多选批量：Shift 范围选后屏幕上真的勾上了（不是只有数据对）",
        (mu.get("shiftDom") or {}).get("allDrawn") is True
        and not (mu.get("shiftDom") or {}).get("bad"))
    chk("多选批量：Shift 落在折叠箭头上也照常范围选",
        (mu.get("shiftTw") or {}).get("ok") is True)
    chk("多选批量：Shift 点**复选框**也要能范围选（只点整行才认的 bug）",
        (mu.get("shiftCp") or {}).get("ok") is True)
    # 2026-10-02 新增：改**任意一条选中项**的状态 / 制作人 → 所有选中项一起改
    chk("多选批量：点选中项的状态徽章 → 选中的全都改成同一个状态",
        (mu.get("rowStat") or {}).get("ok") is True)
    chk("多选批量：点选中项的「制作人」→ 选中的全都改成同一个制作人",
        (mu.get("rowArtist") or {}).get("ok") is True)
    # 2026-10-03 新增：截止日期 / 备注接进同一套批量改法（跟 smoke_fin_gui 同一套）
    chk("多选批量：点选中项的「截止日期」→ 选中的全都改成同一个日期",
        (mu.get("rowDue") or {}).get("ok") is True)
    chk("多选批量：点选中项的「备注」→ 选中的全都改成同一个备注",
        (mu.get("rowNote") or {}).get("ok") is True)
    _k = mu.get("rowStatKeep") or {}
    chk("多选批量：点状态徽章不会把选择集清掉（实际剩 %s / 应剩 %s）"
        % (_k.get("sel"), _k.get("want")),
        _k.get("ok") is True)
    # 「全选本层」按用户要求撤掉了，改由点父级自带级联。
    # 采回批量条上真实存在的按钮来断言：既确认"全选"没被加回来，也能发现少了谁。
    _bl = mu.get("barLayout") or {}
    chk("多选批量：批量条一行排完，「退出多选」在「清空」右边（实际=%s）" % (_bl,),
        _bl.get("ok") is True)
    chk("多选批量：一条没选时计数整段留白、不写「还没选」",
        _bl.get("cntWhenNone") == "")
    _bs = mu.get("barSticky") or {}
    chk("多选批量：树往下滚时批量条粘在内容区顶部、不被行盖住（实际=%s）" % (_bs,),
        _bs.get("ok") is True)
    _bb = mu.get("bulkBtns") or []
    chk("多选批量：批量条就这六颗按钮、没有「全选本层」（实际=%s）" % (_bb,),
        len(_bb) == 6
        and not any("全选" in t for t in _bb)
        and all(any(k in t for t in _bb)
                for k in ("状态", "制作人", "截止日期", "备注", "清空", "退出")))
    chk("多选批量：改状态真的落库（勾的几条全变了）", mu.get("statOk") is True)
    chk("多选批量：改制作人真的落库", mu.get("artistOk") is True)
    chk("多选批量：退出后复选框消失", mu.get("pickGone") == 0)
    dbf = rep.get("dbFile") or {}
    chk("数据文件可设定：面板按钮在、非法目标被拒绝（不静默成功）",
        dbf.get("btn") == 1 and dbf.get("sameRejected") is True
        and dbf.get("badDirRejected") is True and dbf.get("emptyRejected") is True)
    # 多机同时打开（与 smoke_fin_gui.py 同一套断言，改一侧必须两处一起改）
    chk("多机同时打开：开关在、set_share_db 通、sync_state 结构齐、提示条在",
        dbf.get("shareBtn") == 1 and dbf.get("setShare") is True
        and dbf.get("sync") is True and dbf.get("syncBar") == 1)

    # 客户删除（与 smoke_fin_gui.py 同一套断言，改一侧必须两处一起改）
    # 入口已从弹窗底部挪到列表行右边的 ⋯ 菜单（2026-10-04）
    cl = rep.get("clients") or {}
    chk("客户弹窗：底部删除按钮撤了、结算周期提示撤了、每行都有 ⋯",
        cl.get("opened") is True and cl.get("delBtnGone") is True
        and cl.get("subGone") is True and (cl.get("ops") or 0) > 0
        and cl.get("rows") == cl.get("ops"))
    chk("⋯ 菜单里有「编辑客户」「删除客户」，且菜单画在弹窗之上（z=%s）"
        % (cl.get("popZ"),),
        cl.get("hasEdit") is True and cl.get("hasDel") is True
        and (cl.get("popZ") or 0) > 200)
    chk("「删除客户」染红、跟「编辑客户」不同色（%s）" % (cl.get("delColor"),),
        cl.get("delRed") is True)
    chk("编辑客户仍能进编辑态、删除客户删得掉、有项目挂靠删不掉",
        cl.get("made") is True and cl.get("nameFilled") is True
        and cl.get("saveTxt") == "更新客户" and cl.get("delOk") is True
        and cl.get("busyBlocked") is True and cl.get("stillRefused") is True)

    base = os.path.abspath(tree.get("base") or "")
    data = os.path.abspath(tree.get("data") or "")
    chk("base 就是 exe 同级目录", os.path.normcase(base) == os.path.normcase(os.path.abspath(exe_dir)))
    chk("data 落在 exe 同级（不是解包临时目录）",
        os.path.normcase(data) == os.path.normcase(os.path.join(os.path.abspath(exe_dir), "data")))
    chk("data\\ 真的被建出来了", os.path.isdir(data))
    profile = os.path.join(data, "webview2")
    chk("WebView2 用户数据目录在 exe 同级 data\\webview2", os.path.isdir(profile))

    # 运行时资源落点（2026-10-01）：add-data 源写错成整个 app/ 时，页面照样能跑
    # （web 有另一条 add-data），但托盘/窗口图标会悄悄退化成兜底纯色方块 ——
    # PyInstaller 不报警、应用不报错，只有文件真的在不在能说明问题。
    if not onefile and not forced:
        internal = os.path.join(exe_dir, "_internal")
        chk("_internal\\res\\icon.ico 在运行时落点（托盘/窗口图标靠它）",
            os.path.exists(os.path.join(internal, "res", "icon.ico")))
        chk("_internal\\res\\logo.svg / web\\index.html 落点齐",
            os.path.exists(os.path.join(internal, "res", "logo.svg"))
            and os.path.exists(os.path.join(internal, "web", "index.html")))
        chk("_internal\\res 下不是整个 app/（add-data 源别再写错）",
            not os.path.exists(os.path.join(internal, "res", "db.py")))

    say("")
    say("EXE smoke:", "OK" if ok else "FAILED")
    if not ok:
        say("停在阶段:", rep.get("stages"))
        say("smoke_note:", json.dumps(rep, ensure_ascii=False)[:1200])

    # exe 同级的 data\ 是应用自己的数据目录（含上百 MB 的 WebView2 profile），
    # 默认**不删**：删目录不可逆，确认没问题后自己清，或者显式加 --clean。
    if "--clean" in sys.argv:
        say("清理冒烟产物:", data)
        kill(data)
        chk("清理后 dist 里没有残留 data\\", not os.path.exists(data))
    else:
        say("冒烟产物留在:", data, "（要清加 --clean）")
    kill(tmp)
    logf.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
