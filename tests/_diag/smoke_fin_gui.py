"""GUI 冒烟：往临时库塞一份带财务数据的样本，跑 --smoke，回读页面状态。

断言页面真的渲染出了东西（卡片 / 财务表格 / 账龄 / 客户汇总），
而不是只看"窗口有没有出来"。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

from db import Db  # noqa: E402
from finance import Finance  # noqa: E402
from models import Board  # noqa: E402

# 标题栏 / 产品名 / 版本号从 version.py 取，测试里**不再抄一份字面量**：
# 抄一份的结果是"代码改名了测试还绿"，或者"测试红了其实代码是对的"。
from version import (  # noqa: E402
    AUMID, APP_NAME, TITLE, VERSION, VERSION_LINE, tray_tip)

# ⚠ 必须 expanduser：subprocess 传给 CreateProcess 的是原样字符串，**不展开 `~`**，
# 写成 `~\.workbuddy\...` 会直接 WinError 2。（2026-10-01 实测踩到）
EXE = os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "python",
                   "envs", "board", "Scripts", "python.exe")
NOTE = os.path.join(ROOT, "tests", "smoke_note.txt")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_smoke_gui.log")
CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_smoke_gui_child.txt")


class _Tee:
    """同时写控制台和日志文件。

    直接用控制台输出会被调用方的编码（GBK）转坏，中文全成乱码，
    所以这里落一份 UTF-8 的日志，排查时看那个文件。
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _parse_note(text: str) -> dict:
    """smoke_note.txt 正常是 JSON；超时报告是纯文本，包一层便于统一断言。"""
    import json

    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text}


def num(v):
    """页面回传的钱数 → float；取不到（None / ""）时返回 None。

    ⚠ 取不到别兜成 0 —— 0 是这些断言里**合法的正确值**（比如"已付 0"），
    写成 `v or -1` / `float(v or 0)` 会让"值没发回来"和"真的是 0"混成一件事，
    然后那条断言要么永远绿、要么永远红（2026-10-16 踩过后者）。
    """
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def seed(path: str, nosandbox: bool = False) -> None:
    db = Db(path)
    db.open()
    board = Board(db)
    fin = Finance(db)
    db.run("DELETE FROM finance")
    db.run("DELETE FROM links")
    db.run("DELETE FROM nodes")
    db.run("DELETE FROM projects")
    db.run("DELETE FROM clients")
    # 外包商名录也要清 —— 它是"全局名录"，上一轮留下的条目会让计数类断言飘
    # （DELETE 顺序：先断掉引用它的 projects/nodes/finance，再删它自己）
    db.run("DELETE FROM vendors")
    if nosandbox:
        # 模拟"看门狗降级过一次"的状态：库里记着 nosandbox=1
        db.set_setting("nosandbox", "1")
    # 提醒巡检在启动 60s 后首次触发，而 do_front() 会把窗口**强行拉回置顶** ——
    # 那一脚正好容易落在「置顶开关」自检上，表现为偶发 FAIL（不是置顶功能坏了）。
    # 冒烟里把提醒关掉：notify_enabled=0 关到期告警，remind_level=1 让打卡只发通知不拉前台。
    db.set_setting("notify_enabled", "0")
    db.set_setting("remind_level", "1")

    cid = fin.client_save(None, {"name": "冒烟客户", "settlement_cycle": "月结 30"})
    # 外包商 / 制作人名录（2026-10-16 起强关联）。
    # ⚠ 不能再 `set_field(..., "artist", "名字")` —— 裸文本那列已撤出白名单；
    # 必须先建条目、再挂 id。三个样本各司其职：
    #   v_zb 挂项目（界面上要挨着备注显示、菜单里要能改）
    #   v_xg 挂环节（子任务那行）
    #   v_st 是「团队」，两笔外包支出都记在他名下（一笔已付、一笔应付 → 汇总要分列）
    v_zb = fin.vendor_save(None, {"name": "冒烟制作人", "kind": "团队"})
    v_xg = fin.vendor_save(None, {"name": "冒烟小工", "kind": "个人"})
    v_st = fin.vendor_save(None, {"name": "冒烟工作室", "kind": "团队",
                                  "contact": "wx:smoke", "note": "外协样本"})
    pid = board.add_project("冒烟商业项目", "commercial", cid)
    board.set_field("project", pid, "artist_id", v_zb)
    # 这一行故意挂满徽章：今日必做 + 状态（+ 客户 / 制作人）——
    # 用来验「点**状态**徽章才出状态菜单」（裸 .badge 会取到「今日必做」那个，2026-09-30 修的）
    board.set_field("project", pid, "priority", 1)
    # 到期小标要有活样本：3 天后到期
    board.set_field("project", pid, "deadline", (date.today() + timedelta(days=3)).isoformat())
    stage = board.add_node(pid, None, "第一环节")
    sub = board.add_node(pid, stage, "子任务")
    board.set_field("node", sub, "note", "备注示例")
    board.set_field("node", sub, "artist_id", v_xg)
    # 第二个商业项目：M4 的拖拽排序至少要两个同级才试得出来
    board.add_project("冒烟商业项目B", "commercial", cid)
    pid2 = board.add_project("冒烟个人项目", "personal", None)
    # 「今天到期」这档也要有活样本
    board.set_field("project", pid2, "deadline", date.today().isoformat())
    board.add_node(pid2, None, "个人环节")

    today = date.today()
    fin.add({"kind": "contract", "project_id": pid, "amount": 120000,
             "date": (today - timedelta(days=40)).isoformat(), "status": "有效"})
    fin.add({"kind": "invoice", "project_id": pid, "amount": 80000,
             "date": (today - timedelta(days=12)).isoformat(), "status": "已开",
             "invoice_no": "SMOKE-1"})
    fin.add({"kind": "payment", "project_id": pid, "amount": 50000,
             "date": today.isoformat(), "status": "已收"})
    fin.add({"kind": "payment", "project_id": pid, "node_id": sub, "amount": 10000,
             "date": today.isoformat(), "status": "已收"})
    fin.add({"kind": "payment", "project_id": pid, "amount": 60000,
             "date": (today + timedelta(days=20)).isoformat(), "status": "待收"})
    # 外包：已付的 20000 要从实际收入里扣掉；还没付的 5000 不扣
    # ⚠ 「外包支出」必填外包商（2026-10-16 起），两笔都得挂同一个团队，
    # 「外包商汇总」才会有"已付 20000 / 应付 5000"的一行可分列。
    fin.add({"kind": "outsource", "project_id": pid, "amount": 20000,
             "date": today.isoformat(), "status": "已付", "note": "给外协",
             "vendor_id": v_st})
    fin.add({"kind": "outsource", "project_id": pid, "amount": 5000,
             "date": today.isoformat(), "status": "应付", "note": "还没付",
             "vendor_id": v_st})

    # ---- 变更记录（2026-09-30）----
    # 跨 10 天各铺 2 条：面板应该只列**最近 7 天**（14 条），剩下 3 天（6 条）
    # 只能从「查看更多」里翻到。就冲着这两条口径去的。
    db.run("DELETE FROM activity_log")
    # 倒着插（先老后新）：id 单调递增才跟真实数据一致 —— 查询按 id 倒序，
    # 正着插的话「最新一天」会排到最后，把面板的顺序测反了
    for back in range(9, -1, -1):
        d = (today - timedelta(days=back)).isoformat()
        for k in range(2):
            db.run(
                "INSERT INTO activity_log(ts,kind,weight,project_id,node_id,detail,day)"
                " VALUES(?,?,?,?,?,?,?)",
                (d + " 0%d:00:00" % (k + 1), "status", 1, pid, None,
                 "冒烟流水 D%d-%d" % (back, k), d),
            )
    db.close()


def launch(env, db_path, mode):
    """起一次应用、等它的自检报告。返回 smoke_note 的原文（拿不到则 None）。"""
    # 每次重跑都用新的临时库：上次的报告可能已经把状态改过了（比如默认折叠），
    # 沿用旧的会让第二次跑的结果对不上。
    seed(db_path, nosandbox=(mode == "persist"))
    # 清掉上一次的报告。**不能直接 unlink**：数据目录在网络盘上时没有回收站，
    # 删除会被兜底成失败（FAIL_CLOSED）而这里根本不需要"删"——清空即可，
    # 后面判断的是文件内容不是文件在不在。
    if os.path.exists(NOTE):
        try:
            os.unlink(NOTE)
        except Exception:
            with open(NOTE, "w", encoding="utf-8") as _f:
                _f.write("")

    proc = subprocess.Popen(
        [EXE, "-X", "utf8", "-u", os.path.join(APP, "main.py"),
         "--smoke", "--no-tray", "--db", db_path],
        cwd=APP, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    out = b""
    try:
        out, _ = proc.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    log = out.decode("utf-8", "replace")
    with open(CHILD, "w", encoding="utf-8") as f:
        f.write(log)
    print("--- child stdout（完整内容见 _smoke_gui_child.txt）---")
    print(log[-3000:] or "(empty)")

    if not os.path.exists(NOTE):
        print("FAIL: 没写出 smoke_note.txt")
        return None
    return open(NOTE, encoding="utf-8").read()


def main() -> int:
    logf = open(LOG, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logf)
    # normal   = 用户日常路径（沙箱开启，在这台机器上时好时坏）
    # fallback = 看门狗降级后的路径（环境变量给 --no-sandbox）
    # persist  = 库里已经记着 nosandbox=1，不给任何环境变量，验证记忆生效
    mode = (sys.argv[1] if len(sys.argv) > 1 else "normal").lower()
    tmp = tempfile.mkdtemp(prefix="board_gui_")
    db_path = os.path.join(tmp, "board.sqlite")
    print("temp db:", db_path)

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)          # 模拟真实双击环境，别让 shim 干扰
    # 用独立 profile，别用日常那个 data\webview2：
    # 同一个 WebView2 用户数据目录**不能被两个环境同时用**——你要是正开着看板，
    # 冒烟就会在建环境这一步直接挂（0x8007139F 组或资源状态不对），
    # 看起来像"页面起不来"，其实是撞 profile。
    # 也别用 tempfile：系统 Temp 下新建目录 ACL 过宽，沙箱会拒绝启动。
    prof = os.path.join(ROOT, "data", "webview2_smoke")
    os.makedirs(prof, exist_ok=True)
    env["WEBVIEW2_USER_DATA_FOLDER"] = prof
    if mode == "fallback":
        env["BOARD_NOSANDBOX"] = "1"
    else:
        env.pop("BOARD_NOSANDBOX", None)
        env.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
    print("mode:", mode)

    note = launch(env, db_path, mode)
    if note is None:
        return 1

    # WebView2 渲染进程在这台机器上会被安全策略时断时续地拖慢，
    # 表现是自检**跑了一半**停在某个 phase（报告里 err="超时 25000ms @ xxx"）。
    # 那不是应用的问题：重跑一次就全绿。以前只能靠人眼看出来再去手工重跑，
    # 现在这里自己重试一次 —— 只有当重试也超时时才判失败。
    # 注意「页面根本没起来」（纯文本报告、没有 stages）不重试，那是真故障。
    rep0 = _parse_note(note)
    if isinstance(rep0.get("err"), str) and rep0["err"].startswith("超时") \
            and rep0.get("stages"):
        print("--- 自检在 %s 阶段超时，这是本机环境抖动，自动重跑一次 ---"
              % (rep0.get("failed") or "?"))
        note = launch(env, db_path, mode)
        if note is None:
            return 1
    print("--- smoke_note ---")
    print(note)

    ok = True

    def chk(label, cond):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + label)
        ok = ok and cond

    rep = _parse_note(note)
    if "_raw" in rep:
        print("FAIL: 页面没有回传 JSON，只剩超时报告。")
        print("     原始内容：", rep["_raw"][:400])
        return 1

    tree = rep.get("tree", {})
    heat = rep.get("heat", {})
    fin_ = rep.get("fin", {})
    ex_ = rep.get("export", {})
    neww = rep.get("neww", {})
    cr = rep.get("create", {})
    ar = rep.get("artist", {})

    chk("页面已启动并报到", bool(tree) and (tree.get("rows") or 0) > 0)
    chk("页面无 JS 错误 (err=null)", rep.get("err") is None)
    chk("自检巡视跑完全程 (failed=null)", rep.get("failed") is None)
    # 默认状态：只展开「商业 / 个人」两个分组标题行，项目行与环节都折着
    chk("默认只展开分组：树里 5 行（2 分组 + 3 项目）", tree.get("rows") == 5)
    chk("默认项目行 3 行", tree.get("prjRows") == 3)
    chk("默认一个环节都不展开", tree.get("nodeRows") == 0)
    chk("带子级的行都是收起态 ▶", (tree.get("shutArrows") or 0) >= 2)
    # 工具栏现在是「筛选 ▾ + 多选」两个（展开折叠那三个早已撤掉；多选是 issue #3 加的）
    chk("项目页工具栏 = 筛选 + 多选两个按钮（展开折叠三个已撤）", tree.get("treeBtns") == 2)
    chk("筛选按钮默认文案是「筛选 ▾」", (tree.get("filterText") or "").strip() == "筛选 ▾")
    # 顶栏页签：项目 / 财务 / 活跃 三个平级页面（用户定的顺序），旧的分类筛选（全部/商业/个人）已撤掉
    chk("顶栏三个页面页签", tree.get("pages") == 3)
    chk("页签就是「项目 / 财务 / 活跃」", tree.get("btns") == ["项目", "财务", "活跃"])
    chk("旧的分类筛选按钮已移除", tree.get("cats") == 0)
    # 滚动条：内容装得下时 main 不许有滚动间隙（旧 height:100% 会凭空多出一条）
    # 幻影的判定：slack = main底 - 内容底。装得下时 slack ≈ main 的 bottom padding(10)，
    # 真超长时 slack 为负。所以「gap>2 且 slack>=9」才是幻影，负 slack 是正常滚动。
    chk("项目页短内容无滚动间隙 (gap=%s)" % tree.get("gap"), tree.get("gap") == 0)
    chk("热力图 371 格 / 4 卡片", heat.get("cells") == 371 and heat.get("cards") == 4)
    # 活跃/财务同一条规则修的：内容装得下时不得出现幻影滚动
    chk("活跃页无幻影滚动 (gap=%s slack=%s)" % (heat.get("gap"), heat.get("slack")),
        not ((heat.get("gap") or 0) > 2 and (heat.get("slack") if heat.get("slack") is not None else 99) >= 9))
    chk("点页签切到活跃页后页签高亮", heat.get("tabAct") == "heat")
    chk("L3 遮挡层已激活 (block=flex)", rep.get("block") == "flex")
    chk("财务面板已显示", fin_.get("panel") == "block")
    chk("切到财务页时项目页收起", fin_.get("tree") == "none")
    chk("点页签切页后财务页签高亮", fin_.get("tabAct") == "fin")
    chk("财务页无幻影滚动 (gap=%s slack=%s)" % (fin_.get("gap"), fin_.get("slack")),
        not ((fin_.get("gap") or 0) > 2 and (fin_.get("slack") if fin_.get("slack") is not None else 99) >= 9))
    # 汇总卡：两行各三张，第一行看钱的规模，第二行看成本与回收
    chk("财务卡片 6 张（本月到账已下架）", fin_.get("cards") == 6)
    chk("卡片分两行、每行三张", fin_.get("cardRows") == [3, 3])
    chk("第一行 = 总合同额 / 已收 / 待收",
        (fin_.get("cardLabels") or [])[:3] == ["总合同额", "已收", "待收"])
    chk("第二行 = 外包支出 / 实际收入 / 收款率",
        (fin_.get("cardLabels") or [])[3:] == ["外包支出", "实际收入", "收款率"])
    chk("卡片上不再出现开票口径与本月到账",
        not any(("开票" in str(x) or "本月" in str(x)) for x in (fin_.get("cardLabels") or [])))
    # 默认看本年份，不是「全部年份」
    chk("年份默认选中本年份", fin_.get("yearVal") == str(date.today().year))
    chk("项目表 9 列，已开票列已下架",
        fin_.get("cols") == 9 and "已开票" not in (fin_.get("head") or []))
    # 四组数据做成可切换标签页（2026-10-16，用户要求省纵向空间）
    chk("财务页四组数据是标签页、顺序对（%s）" % (fin_.get("finTabBtns"),),
        fin_.get("finTabBtns") == ["项目款项", "客户汇总", "外包商汇总", "应收账龄"])
    chk("标签页默认停在「项目款项」（%s）" % (fin_.get("finTabAct"),),
        fin_.get("finTabAct") == "prj")
    chk("默认只露项目款项一组（%s）" % (fin_.get("paneVis"),),
        fin_.get("paneVis") == [True, False, False, False])
    chk("切到「应收账龄」真的换表（act=%s vis=%s）"
        % (fin_.get("ageAct"), fin_.get("ageVis")),
        fin_.get("ageAct") == "age" and fin_.get("ageVis") == [False, False, False, True])
    chk("切过去后账龄表还在、有格子（%s）" % (fin_.get("ageTable"),),
        (fin_.get("ageTable") or 0) > 0)
    chk("能再切回「项目款项」", fin_.get("backVis") is True)
    # 筛选栏收敛：年份 / 客户都是下拉，币种那一组撤掉（只按人民币结算），
    # 导出相关的四条命令收进一个下拉菜单
    chk("年份是下拉菜单（不是一排按钮）", fin_.get("yearTag") == "SELECT")
    chk("年份下拉有「全部年份」+ 各年份（含本年）",
        (fin_.get("yearOpts") or [])[:1] == ["全部年份"]
        and str(date.today().year) + " 年" in (fin_.get("yearOpts") or []))
    chk("客户也是下拉", fin_.get("clientTag") == "SELECT")
    chk("币种筛选组已撤掉（只按人民币结算）", fin_.get("ccySeg") == 0)
    chk("「导出目录」按钮已并进菜单", fin_.get("dirBtn") == 0)
    chk("导出是一个按钮 + 下拉菜单", fin_.get("exportTag") == "BUTTON")
    chk("导出菜单 = 导出… / 打开导出目录",
        [x for x in (fin_.get("exportMenu") or []) if x] == ["导出…", "打开导出目录"])
    # ---- 税后提示 + 结款（核销待收）（2026-10-13 新增，2026-10-16 改口径）----
    # 两条都跑在**临时样本项目**上：建 → 点 → 确认 → 回查库 → 删干净。
    # ⚠ 只断言"按钮画出来了"是假绿（2026-10-10 的删除按钮就这么漏掉一个 bug），
    #   所以这里连"库里真的多出一条核销、合同额一条没动"一起钉住。
    po = rep.get("payoff") or {}
    chk("结款样本建得出来、记一笔弹窗打得开、项目选对了（%s/%s/%s）"
        % (po.get("made"), po.get("dlgOpen"), po.get("prjOk")),
        po.get("made") is True and po.get("dlgOpen") is True and po.get("prjOk") is True)
    chk("「到账」类型下露出结款按钮，文字对（实得 %r）" % po.get("btnText"),
        po.get("btnText") == "结款（核销待收）" and po.get("visWhenPayment") is True)
    chk("结款按钮染红（warn 类，实得 %r）" % po.get("btnCls"),
        "warn" in (po.get("btnCls") or ""))
    # 2026-10-16 新增的第五种款项：「核销」
    chk("「记一笔」里有「核销」这颗类型按钮（实得 %s）" % (po.get("kinds"),),
        "writeoff" in (po.get("kinds") or []))
    chk("选「核销」时外包商 / 发票号 / 结款三条行都收起（%s）" % (po.get("woHides"),),
        po.get("woHides") is True)
    chk("「核销」的状态档位就两档：已核销 / 作废（实得 %s 默认 %r）"
        % (po.get("woStatusList"), po.get("woStatus")),
        (po.get("woStatusList") or []) == ["已核销", "作废"]
        and po.get("woStatus") == "已核销")
    chk("换成合同额 / 开票时结款按钮收起（%s / %s）"
        % (po.get("btnHiddenWhenContract"), po.get("btnHiddenWhenInvoice")),
        po.get("btnHiddenWhenContract") is True and po.get("btnHiddenWhenInvoice") is True)
    chk("切回「到账」按钮又出来", po.get("btnBackWhenPayment") is True)
    # 税后 = 金额 ÷ (1+税率%)：100000 / 1.06 = 94339.62（不含税口径）
    chk("税后提示：100000 + 6%% → 「（税后 94,339.62 元）」（实得 %r）" % po.get("netText"),
        po.get("netText") == "（税后 94,339.62 元）")
    chk("「6%%」带百分号也认（实得 %r）" % po.get("netPercent"),
        po.get("netPercent") == "（税后 94,339.62 元）")
    chk("税率清空 → 税后提示消失", po.get("netEmptyNoTax") is True)
    chk("金额乱填 → 税后提示消失（不许瞎算）", po.get("netEmptyBadAmount") is True)
    chk("建项目弹窗那条通道也算了税后（实得 %r）" % po.get("newNetText"),
        po.get("newNetText") == "（税后 94,339.62 元）")
    # 版式（2026-10-13 用户反馈"金额和税率这行文字换行、难看"）：
    # 金额 + 税率% + 税后提示挤一行，**余量本来是 0** —— `.ovbox` 是
    # max-height:86% + overflow:auto，窗口一矮就出纵向滚动条、白吃 15px，
    # 行里的「税率%」随即被拆成两行（整行 50px → 67px）。
    # ⚠ 判据必须是**整行所有文字的最大行数**：那次真正折的是一旁的小字，
    #   只盯税后提示是看不见的（第一版就这么漏过去了）。
    chk("金额行一行画完：常规 100000（整行 %s 行 / 行高 %s）"
        % (po.get("finRowLines"), (po.get("finRow") or {}).get("h")),
        po.get("finRowLines") == 1)
    chk("金额行一行画完：七位数 1234567.89（整行 %s 行）" % po.get("finRowLinesBig"),
        po.get("finRowLinesBig") == 1)
    chk("金额行一行画完：**弹窗带纵向滚动条时**也一行（整行 %s 行 / 行高 %s / 滚动条吃 %s px）"
        % (po.get("finRowLinesScroll"), po.get("finRowHScroll"), po.get("finScrollCost")),
        po.get("finRowLinesScroll") == 1)
    chk("新建项目页的合同额行同样一行画完（%s / %s）"
        % (po.get("newRowLines"), po.get("newRowLinesBig")),
        po.get("newRowLines") == 1 and po.get("newRowLinesBig") == 1)
    chk("结款确认弹层打得开", po.get("confirmOpen") is True)
    chk("弹层把账摊开写：合同额 100,000.00 − 已收 30,000.00 = 待收 70,000.00（实得 %r）"
        % po.get("head"),
        "100,000.00" in (po.get("head") or "")
        and "30,000.00" in (po.get("head") or "")
        and "70,000.00" in (po.get("head") or "")
        and "待收" in (po.get("head") or ""))
    chk("弹层脚注说明会核销多少、合同额不动（实得 %r）" % po.get("foot"),
        "核销" in (po.get("foot") or "") and "70,000.00" in (po.get("foot") or "")
        and "不动" in (po.get("foot") or ""))
    chk("弹层列出该项目 2 条到账记录（实得 %s 行）" % po.get("listRows"),
        po.get("listRows") == 2)
    chk("确认结款后弹层和表单都关掉",
        po.get("confirmClosed") is True and po.get("formClosed") is True)
    # 样本是「已收 30000 + 待收 5000」，只有 30000 该进「已收」。
    # 要是把待收也算进来就会变成 35000 —— 这条同时钉住了"待收不算"。
    chk("结款不改合同额：还是 100,000 且还是那 1 条（实得 %s / %s 条）"
        % (po.get("contractAfter"), po.get("contractRecs")),
        po.get("contractAfter") == 100000 and po.get("contractRecs") == 1)
    chk("结款落库 = 凭空多出一条核销 70000（100000 − 30000，实得 %s）"
        % po.get("writeoffAfter"),
        po.get("writeoffAfter") == 70000)
    chk("那条核销是「已核销 + 备注 结款核销」（实得 %s）" % (po.get("woRecs"),),
        (po.get("woRecs") or []) == [[70000, "已核销", "结款核销"]])
    chk("记录数从 3 条变 4 条（合同 1 + 到账 2 + 核销 1，实得 %s）"
        % po.get("recCount"),
        po.get("recCount") == 4)
    chk("结款后待收归零、项目算已结清（实得 %s / %s）"
        % (po.get("pendingAfter"), po.get("settled")),
        po.get("pendingAfter") == 0 and po.get("settled") is True)
    chk("幂等：再点一次只说「已经结清了」、不开确认弹层、不加记录（实得 %r %s %s）"
        % (po.get("againAlert"), po.get("againNoDlg"), po.get("againSameCount")),
        "已经结清" in (po.get("againAlert") or "")
        and po.get("againNoDlg") is True and po.get("againSameCount") is True)
    chk("结款全程没有未捕获的报错（实得 %r）" % po.get("errNew"),
        po.get("errNew") is None)
    chk("结款样本已清干净（不留垃圾项目）", po.get("gone") is True)
    chk("结款整条链路 OK", po.get("ok") is True)
    # ---- 逾期文案三条（2026-10-13 用户定的）----
    # 筹备不算停滞 / 已交付不算停滞、改报「结款逾期 N 天」/ 其余报「制作逾期 N 天」。
    # 结款逾期的起算日是**用户手填的结款日期**：没填就不报 —— 2026-10-13 第二轮
    # 用户否掉了"拿交付日期反推"，那会凭空冒出一个跟合同无关的"逾期"。
    # ⚠ 样本也是临时的：建 → 真写库 → load → 看行上渲染 → 删干净。
    sv = rep.get("settle") or {}
    chk("已交付 + 没填结款日期 → 行上一颗红字都没有（实得 %r）" % sv.get("noSettleDate"),
        sv.get("noSettleDate") == "")
    chk("结款日期填 12 天前并读得回来（实得 %r）" % sv.get("settleDate"),
        sv.get("settleDate") == (date.today() - timedelta(days=12)).isoformat())
    chk("前端数据里已经没有 delivered_at 字段了（%s）" % sv.get("oldFieldGone"),
        sv.get("oldFieldGone") is True)
    chk("进行中 + 截止日过期 5 天 → 行上写「制作逾期 5 天」（实得 %r）" % sv.get("dueA"),
        sv.get("dueA") == "制作逾期 5 天")
    chk("已交付 → 行上写「结款逾期 12 天」，不报制作逾期（实得 %r）" % sv.get("dueC"),
        sv.get("dueC") == "结款逾期 12 天")
    chk("筹备 / 已交付都不带「停滞」小标（%s / %s）"
        % (sv.get("staleB"), sv.get("staleC")),
        sv.get("staleB") is False and sv.get("staleC") is False)
    chk("底栏「临期/逾期」把结款逾期也算进去（实得 %s）" % sv.get("statsDue"),
        (sv.get("statsDue") or 0) >= 2)
    chk("告警面板：结款那条带 settle 标记、文案对（实得 %s）" % sv.get("alertsSettle"),
        sv.get("alertsSettle") is True)
    chk("告警面板：制作那条不带 settle 标记、文案对（实得 %s）" % sv.get("alertsNormal"),
        sv.get("alertsNormal") is True)
    chk("筹备 / 已交付都没进停滞名单（实得 %s）" % sv.get("inStaleList"),
        sv.get("inStaleList") is False)
    chk("逾期样本全程无未捕获报错（实得 %r）" % sv.get("errNew"),
        sv.get("errNew") is None)
    chk("逾期样本已清干净（不留垃圾项目）", sv.get("gone") is True)
    chk("逾期整条链路 OK", sv.get("ok") is True)
    # ---- 项目设置页（2026-10-13 新增）----
    # ⋯ 菜单里原来散着「编辑截止日期 / 改客户 / 改分类」，现在合成一页（版面照抄新建项目）。
    # ⚠ 真走点击路径：菜单 → 点「项目设置…」→ 改字段 → 点「保存」→ 断落库。
    #   直调 call("set_field") 会把"菜单项没接上 / 弹窗没打开 / 保存没绑事件"全放过。
    pv = rep.get("prjset") or {}
    chk("项目行 ⋯ 菜单里有「项目设置…」（%s）" % pv.get("hasSet"), pv.get("hasSet") is True)
    chk("旧入口已撤：编辑截止日期 / 改客户 / 改分类都不在菜单里了（%s）" % (pv.get("menu"),),
        pv.get("oldGone") is True)
    chk("⋯ 里「编辑备注」还留着（环节和项目都要用）", pv.get("keepNote") is True)
    chk("点「项目设置…」能开出设置页（命中=%s）" % pv.get("itemHit"), pv.get("itemHit") is True)
    chk("设置页真的打开了", pv.get("dlgOpen") is True)
    chk("设置页预填的是这个项目现在的值（标题 %r / 结款日 %r / 目录 %r）"
        % (pv.get("preTitle"), pv.get("preSettle"), pv.get("prePath")),
        pv.get("preTitle") == "设置页冒烟P" and pv.get("preSettle") == ""
        and pv.get("prePath") == "")
    chk("分类选择器按库里的分类现画、当前项选中（按钮 %s / 选中 %s）"
        % (pv.get("catBtns"), pv.get("catAct")), pv.get("catAct") == "commercial")
    chk("改完点保存 → 标题落库（实得 %r）" % pv.get("title"), pv.get("title") == "设置页冒烟P改")
    chk("行上标题跟着变了（实得 %r）" % pv.get("rowTitle"), pv.get("rowTitle") == "设置页冒烟P改")
    chk("截止日期落库（实得 %r，期望 %r）"
        % (pv.get("dl"), (date.today() + timedelta(days=30)).isoformat()),
        pv.get("dl") == (date.today() + timedelta(days=30)).isoformat())
    chk("结款日期落库（实得 %r，期望 %r）"
        % (pv.get("settle"), (date.today() - timedelta(days=7)).isoformat()),
        pv.get("settle") == (date.today() - timedelta(days=7)).isoformat())
    chk("客户落库（实得 %r）" % pv.get("client"), pv.get("client") == pv.get("seedClient"))
    chk("分类落库（实得 %r，期望 %r）" % (pv.get("cat"), pv.get("wantCat")),
        pv.get("cat") == pv.get("wantCat"))
    chk("项目根目录写成了链接（实得 %r）" % pv.get("path"),
        pv.get("path") == "/冒烟/设置页目录")
    chk("目录链接只有一条（旧条删干净了，不是留两条，%s）" % pv.get("links"),
        pv.get("links") == 1)
    chk("保存后弹窗自己关掉", pv.get("dlgClosed") is True)
    chk("结款日期能清空（不是改了不动，%s）" % pv.get("settleCleared"),
        pv.get("settleCleared") is True)
    chk("设置页样本全程无未捕获报错（实得 %r）" % pv.get("errNew"),
        pv.get("errNew") is None)
    chk("设置页样本已清干净", pv.get("gone") is True)
    # 制作人（2026-10-16 起是外包商名录的下拉，库里存 vendors.id）
    chk("设置页「制作人」框是可输入的组合框（list=%s）" % (pv.get("artistList"),),
        pv.get("artistList") == "vendorOpts")
    chk("设置页「制作人」预填是名字不是 id（%r）" % (pv.get("artistPre"),),
        pv.get("artistPre") == "")
    chk("候选里有种子外包商（%s）" % (pv.get("artistOpts"),),
        "冒烟制作人" in (pv.get("artistOpts") or []))
    # ⚠ 本轮最容易假绿的一条：填一个新名字 → 界面看着有名字，但库里可能还是个空 id。
    # 所以断言落到 load() 的 artist（名字，从 vendors JOIN）+ artist_id（真外键）两个键上。
    chk("设置页填新名字：条目是**现建**的（%r）" % (pv.get("artist"),),
        pv.get("vendorMade") is True and pv.get("artist") == "设置页冒烟V")
    chk("同时落下 artist_id，且指向刚建的那条（id=%s）" % (pv.get("artistId"),),
        pv.get("artistLinked") is True)
    chk("重开设置页把那个名字带回来（%r）" % (pv.get("artistPre2"),),
        pv.get("artistPre2") == "设置页冒烟V")
    chk("制作人能清空（不是改了不动）", pv.get("artistCleared") is True)
    chk("清空挂靠 ≠ 删条目（名录里那个人还在）", pv.get("vendorKept") is True)
    chk("清空后条目可删、且真删掉了", pv.get("vendorGone") is True)
    chk("项目设置整条链路 OK", pv.get("ok") is True)
    # 导出选项弹窗：格式 / 时间范围 / 项目 / 内容 + 文件名预览。
    # ⚠ 只验界面。#exOk（「导出…」）会弹**模态**系统对话框，冒烟里点它会把无人值守的
    # 整轮卡在那儿等点击 —— 那条路走 tests/_diag/smoke_export_save.py（另开线程关窗口）。
    chk("导出选项弹窗能打开", ex_.get("dlg") is True)
    chk("格式三个 Excel / CSV / JSON，默认 Excel",
        ex_.get("fmtBtns") == ["xlsx", "csv", "json"] and ex_.get("fmtAct") == "xlsx")
    chk("内容五项默认全勾（多了「外包商汇总」）",
        ex_.get("secs") == ["records", "projects", "clients", "vendors", "aging"]
        and ex_.get("secsOn") == ["records", "projects", "clients", "vendors", "aging"])
    chk("时间范围四项，且「同当前筛选」写在最前",
        (ex_.get("rangeOpts") or [])[1:] == ["全部时间", "本年度", "自定义范围…"])
    chk("「同当前筛选」写清了年份 (%s)" % ex_.get("curLabel"),
        "同当前筛选" in (ex_.get("curLabel") or "")
        and str(date.today().year) in (ex_.get("curLabel") or ""))
    chk("文件名预览是 board_finance_*.xlsx (%s)" % ex_.get("planName"),
        str(ex_.get("planName") or "").startswith("board_finance")
        and str(ex_.get("planName") or "").endswith(".xlsx"))
    chk("预览带出笔数 (%s)" % ex_.get("planCount"), str(ex_.get("planCount") or "").isdigit())
    chk("项目默认「全部项目」、列表收起", ex_.get("prjClosed") is True)
    chk("选「指定项目」后列表展开、默认全勾",
        ex_.get("prjShown") is True and (ex_.get("prjN") or 0) >= 1
        and ex_.get("prjOn") == ex_.get("prjN"))
    chk("选「自定义范围」后两个日期框出现", ex_.get("datesShown") is True)
    chk("CSV 固定只导款项明细（五项全锁死，只有明细是勾的）",
        ex_.get("csvOn") == ["records"] and ex_.get("csvOff") == 5)
    chk("切 CSV 后预览文件名变 .csv (%s)" % ex_.get("csvName"),
        str(ex_.get("csvName") or "").endswith(".csv"))
    chk("关掉后弹窗收起", ex_.get("closed") is True)
    # 外包只扣「已付」：已收 60000 − 外包 20000 = 实际收入 40000（另有一笔 5000 应付不扣）
    mt = fin_.get("money") or {}
    chk("外包支出只算已付，实际收入 = 已收 − 外包",
        mt.get("received") == 60000 and mt.get("outsource") == 20000
        and mt.get("net") == 40000)
    pj = fin_.get("proj") or {}
    chk("项目行同口径（外包 20000 / 实际收入 40000）",
        pj.get("outsource") == 20000 and pj.get("net") == 40000)
    chk("财务表格有项目行", (fin_.get("rows") or 0) >= 1)
    chk("账龄 4 档", fin_.get("aging") == 4)
    chk("客户汇总有行", (fin_.get("clients") or 0) >= 2)
    # 外包商汇总（2026-10-16 打通外包商 ↔ 外包支出）：跟客户汇总对称的一节。
    # ⚠ 行数按 .ftrow.body 数，不能按 .ftrow —— 那是把表头也算进去，
    # 恰好会在"一条数据都没有"时也 ≥1，是个假绿陷阱。
    chk("外包商汇总有数据行（表头不算，%s 行）" % fin_.get("vendorRows"),
        (fin_.get("vendorRows") or 0) >= 1)
    chk("外包商汇总表头八列（%s）" % (fin_.get("vendorHead"),),
        fin_.get("vendorHead") == ["外包商", "类型", "项目数", "已付", "应付", "合计",
                                   "笔数", "最近一笔"])
    vr0 = fin_.get("vendorRow") or {}
    chk("汇总行：样本「冒烟工作室」分列出来了（%s）" % (vr0.get("cells"),),
        (vr0.get("type") or "").strip() == "团队"
        and "20,000" in "".join(vr0.get("cells") or [])
        and "5,000" in "".join(vr0.get("cells") or []))
    chk("款项明细里带着外包商名字（谁收的钱，%s）" % (fin_.get("outsourceVendor"),),
        (fin_.get("outsourceVendor") or []) == ["冒烟工作室", "冒烟工作室"])
    chk("口径说明已渲染",
        "待收 = 合同额" in (fin_.get("tip") or "")
        and "实际收入 = 已收 − 外包支出" in (fin_.get("tip") or ""))
    # 制作人：跟备注挨着显示，行菜单里能改
    chk("制作人显示在项目行里",
        ar.get("tags") == 1 and "冒烟制作人" in (ar.get("text") or ""))
    chk("行菜单里有「编辑制作人」",
        any("编辑制作人" in str(x) for x in (ar.get("menu") or [])))
    # 「编辑截止日期 / 改客户 / 改分类」已经收进「项目设置」页（2026-10-13 用户要求）：
    # 项目行菜单里不该再出现，否则界面上两处入口并存、用户以为没生效
    chk("项目行菜单里有「项目设置…」",
        any(str(x).startswith("项目设置") for x in (ar.get("menu") or [])))
    chk("项目行 ⋯ 里不再有 编辑截止日期 / 改客户 / 改分类",
        not any(str(x).startswith(("编辑截止日期", "改客户", "改分类"))
                for x in (ar.get("menu") or [])))
    # 新建项目弹窗里的款项区（M3 追加）
    chk("新建项目弹窗 8 个字段（多了「结款日期」）", neww.get("flds") == 8)
    # 客户框必须是「可选可填」的组合框 —— 只是 select 的话，库里没客户就等于填不了
    chk("客户框是可输入的组合框（input + datalist）",
        neww.get("clientTag") == "INPUT" and neww.get("clientList") is True)
    chk("客户候选里有种子客户", neww.get("clients") == 1)
    chk("「浏览」按钮已绑定事件", neww.get("pickBound") is True)
    chk("款项输入框齐全（合同额/首款/税率/日期）", neww.get("money") is True)
    chk("新建项目弹窗里的币种框也撤掉了", neww.get("ccyGone") is True)
    chk("首款日期默认今天", neww.get("date") == 10)
    # ---- 精简版面（2026-09-30 用户要求） ----
    chk("新建项目页那句说明撤掉了", neww.get("subGone") is True)
    chk("名称框占位符改成「输入项目名，可用中文。」（%s）" % neww.get("titlePh"),
        neww.get("titlePh") == "输入项目名，可用中文。")
    chk("客户栏右边那句「库里没有的名字…」提示撤掉了", neww.get("noCliHint") is True)
    # 截止日期：**原生 date 框**（要它的日历选择器）+ 空值时自绘的 YYYY/MM/DD。
    # 原生框空值时画的是「yyyy/mm/日」—— 页面改不了（lang 属性实测无效、placeholder 无效），
    # 所以把那段 mask 设成透明、自己顶一个占位。这三条钉"占位真的在、且不吃点击"。
    chk("截止日期是原生 date 框（要日历选择器，%s）" % neww.get("dueType"),
        neww.get("dueType") == "date")
    chk("空值时框里显示自绘的「YYYY/MM/DD」（%s，显示=%s）"
        % (neww.get("duePh"), neww.get("duePhShown")),
        neww.get("duePh") == "YYYY/MM/DD" and neww.get("duePhShown") is True)
    # ⚠ 占位是盖在 input 上的：少了 pointer-events:none，点它就点不到原生日历。
    #   只有 elementFromPoint 能逮住这种"看得见点不着"。
    chk("自绘占位不吃点击（占位中心点上必须是输入框自己，%s）" % neww.get("duePhHit"),
        neww.get("duePhHit") == "newDl")
    # 结款日期（2026-10-13 新增）：跟截止日期同一套原生 date 框 + 自绘占位。
    # 它是「结款逾期 N 天」的**唯一**依据，框不在就整条功能都不在。
    chk("结款日期是原生 date 框（%s）" % neww.get("settleType"),
        neww.get("settleType") == "date")
    chk("结款日期空值时显示自绘的「YYYY/MM/DD」（%s，显示=%s）"
        % (neww.get("settlePh"), neww.get("settlePhShown")),
        neww.get("settlePh") == "YYYY/MM/DD" and neww.get("settlePhShown") is True)
    # 设置页：所有提示文字都收进了悬停工具提示，一个都不占版面
    chk("设置页没有任何常驻提示文字（.hint=%s）" % neww.get("setHints"),
        neww.get("setHints") == 0)
    tips = neww.get("setTipKeys") or []
    chk("设置页 5 处悬停工具提示都在（%s）" % tips,
        neww.get("setTips") == 5
        and all(k in tips for k in ["数据文件", "多机同时打开", "兼容模式", "数据快照", "回滚"]))
    # ---- 变更记录：面板只列一周、按天折叠，更早的走「查看更多」（2026-09-30）----
    # 种子跨 10 天各铺 2 条 → 面板该有 7 组（14 条），弹窗里该多出 3 组。
    lgs = rep.get("logs") or {}
    chk("变更记录头写着「最近 7 天」", "最近 7 天" in (lgs.get("hint") or ""))
    chk("面板只列最近一周：%s 组（不是种子的 10 天）" % (lgs.get("days"),),
        lgs.get("days") == 7)
    chk("面板里没有比窗口起点(%s)更早的流水" % (lgs.get("since"),),
        lgs.get("oldestOk") is True)
    chk("按天分组：每组都有日期头 + 条数（%s）" % ((lgs.get("headText") or "").strip(),),
        lgs.get("everyHasHead") is True and "条" in (lgs.get("headText") or ""))
    chk("默认只展开最近那天、其余收起",
        lgs.get("firstOpen") is True and lgs.get("restClosed") is True)
    chk("点日期头能收起（那一组的行真从 DOM 里没了）",
        lgs.get("collapsed") is True and (lgs.get("collapsedRows") or 0)
        < (lgs.get("rows") or 0))
    chk("再点一下能展开回来", lgs.get("reopened") is True)
    chk("「查看更多」开出全部记录弹窗", lgs.get("dlgOpen") == "flex")
    chk("弹窗里按天分组，且能看到面板看不到的那几天（%s > %s 组）"
        % (lgs.get("allDays"), lgs.get("days")),
        lgs.get("allDaysGt") is True)
    chk("弹窗条数对得上活动接口给的 log_total（%s）" % (lgs.get("totalText"),),
        lgs.get("totalOk") is True)
    chk("弹窗里每天也能单独折叠", lgs.get("allCollapsed") is True)
    chk("条数没超过一页 → 「加载更多」自己藏起来", lgs.get("moreShown") is False)
    chk("弹窗能关掉", lgs.get("dlgClosed") == "none")

    # ---- 顶栏刷新按钮（用户要求：有环形箭头就做成按钮，不引用外部图片） ----
    chk("顶栏有刷新按钮，图标是内联 SVG（不依赖外部图片）",
        (neww.get("refreshIcon") or 0) >= 1 and (neww.get("refreshImg") or 0) == 0)
    rf = rep.get("refresh") or {}
    chk("刷新按钮上也确实挂着一个 svg 图标", (rf.get("icon") or 0) >= 1)
    chk("刷新按钮点下去：日期被重画回今天（%s -> %s）"
        % (rf.get("fake"), rf.get("date")), rf.get("dateOk") is True)
    chk("刷新前界面停在旧值（说明它不会自己刷，这个按钮是有用的）",
        rf.get("staleBefore") is False)
    chk("刷新按钮真的重新读了数据文件：库里刚改的名字出现在树上",
        rf.get("pickedUp") is True)
    chk("点下去图标有转动反馈", rf.get("spin") is True)
    # 真点一次「新建项目 + 合同额 + 首款」，看有没有真的落库
    chk("建项目带款项能落库（合同 1234 / 首款 234 / 待收 1000）",
        cr.get("ok") is True and cr.get("contract") == 1234
        and cr.get("received") == 234 and cr.get("pending") == 1000)
    chk("新项目的客户挂对了", cr.get("client") == "冒烟客户")
    # 截止日期：原生 date 框的 value 只可能是空串或 ISO —— 库里必须落成 2026-12-31
    chk("截止日期置 ISO 后落库（%s → %s）"
        % (cr.get("dueIn"), cr.get("dueStored")), cr.get("dueStored") == "2026-12-31")
    # 结款日期（2026-10-13 新增）：新建页填了就得真落库 —— 只在界面上填、
    # 没发那条 set_field 的话，行上永远不报结款逾期，而肉眼完全看不出来
    chk("结款日期置 ISO 后落库（%s → %s）"
        % (cr.get("settleIn"), cr.get("settleStored")), cr.get("settleStored") == "2027-01-15")
    chk("结款日期框里有值时自绘占位也收起（display=%s）" % cr.get("settlePhWithVal"),
        cr.get("settlePhWithVal") == "none")
    # 有值时自绘占位必须让位（那时原生自己画的就是 2026/12/31，不能拿占位去盖）
    chk("框里有值时自绘占位收起（display=%s）" % cr.get("duePhWithVal"),
        cr.get("duePhWithVal") == "none")
    chk("清空后自绘占位回来（display=%s）" % cr.get("duePhBack"),
        cr.get("duePhBack") == "block")
    # 重开新建页时，上一个项目的结款日期不能留在框里（reset 漏清这一个的经典症状：
    # 下个项目莫名带上前一个的结款日，然后行上就开始报结款逾期）
    chk("重开新建页后结款日期框是空的（留下 %r，占位显示=%s）"
        % (cr.get("settleLeft"), cr.get("settlePhBack")),
        cr.get("settleLeft") == "" and cr.get("settlePhBack") == "block")
    # 原生框**天然写不进非法日期**：塞中文 / 斜杠进去 value 都变空串，只有 ISO 收得下。
    # 这也是原来手写框那套"格式写错就拦下 + normDay 归一"可以整个删掉的原因。
    chk("原生 date 框拒绝非法文本：下周三→%r、2026/12/31→%r、ISO→%r"
        % (cr.get("rejectCn"), cr.get("rejectSlash"), cr.get("acceptIso")),
        cr.get("rejectCn") == "" and cr.get("rejectSlash") == ""
        and cr.get("acceptIso") == "2026-12-31")
    # 客户框里写一个库里没有的名字：应当自动建客户并挂上
    chk("客户框输入新名字会自动建客户", cr.get("newClient") == "冒烟新客户X")
    chk("客户数从 1 变 2", cr.get("clientCount") == 2)

    # M4 打磨：拖拽排序 / 批量折叠 / 快捷键 / 快照 / 进度条 / 项目页筛选
    m4 = rep.get("m4", {})
    chk("项目页工具栏只剩「筛选 ▾ + 多选」（展开折叠三个已撤）", m4.get("bar") == 2)
    # 筛选菜单：归档开关 + 分隔线 + 全部状态 + 8 个项目状态，共 11 项
    fi = m4.get("filterItems") or []
    chk("筛选菜单第一项是「隐藏归档项目」（默认就是勾上的）",
        "隐藏归档项目" in (fi[0] if fi else "") and "✓" in (fi[0] if fi else ""))
    chk("归档开关默认关闭归档显示、按钮上不挂额外字样",
        m4.get("hideArchDefault") == 1
        and (m4.get("filterText0") or "").strip() == "筛选 ▾")
    # 加了「显示客户」开关之后，菜单不再是固定 11 项 —— 改成断言结构，
    # 别把项数写死（制作人数量本来就跟数据有关）
    chk("筛选菜单里有「显示客户」开关（默认勾上）",
        any("显示客户" in x and "✓" in x for x in fi))
    # 多选（2026-09-30）：状态一组多选，配「反选 / 清空」两个横排动作。
    # 「全选」按用户要求撤掉了 —— 空集合本来就等于全选，那个按钮纯属重复。
    chk("筛选菜单里有「反选 / 清空」两个动作（多选）",
        any("反选" in x and "清空" in x for x in fi))
    chk("筛选菜单里不再有「全选」按钮", not any("全选" in x for x in fi))
    chk("筛选菜单只管项目：子环节那两组已挪到每行自己的筛选框",
        not any("全部环节状态" in x for x in fi)
        and not any("全部制作人" in x for x in fi))
    chk("筛选菜单列出全部项目状态（含归档 / 中止，且已无「待交付」）",
        len(fi) >= 10 and any("归档" in x for x in fi[2:])
        and any("中止" in x for x in fi[2:]) and not any("待交付" in x for x in fi))
    af = m4.get("afterFilter") or {}
    chk("真点一次状态筛选：只剩 1 个项目且就是那条",
        (af.get("stats") or []) == ["中止"] and af.get("rows") == 1
        and af.get("title") == m4.get("filterExpect"))
    chk("筛选按钮上写着当前筛的是什么", "中止" in (af.get("text") or ""))
    chk("多选：菜单里点一下状态不关弹层（要能连着勾）", af.get("stayedOpen") is True)
    # ---- 多选 / 反选（项目页） ----
    pfl = m4.get("projFilter") or {}
    chk("多选菜单：动作行是「反选 / 清空」两个按钮 + 组标题（没有全选）",
        pfl.get("actLabels") == ["反选", "清空"] and pfl.get("hasTitle") is True
        and pfl.get("noAllBtn") is True)
    chk("多选：清空后没有筛选、项目全回来",
        pfl.get("clrClicked") is True and pfl.get("selAfterClear") == []
        and (pfl.get("rowsCleared") or 0) == ((m4.get("filterBack") or {}).get("total")))
    picks = pfl.get("picks") or []
    alive = pfl.get("menuAlive") or []
    chk("多选：连勾两个状态，两下菜单都还开着",
        len(picks) == 2 and len(alive) == 2 and all(alive))
    chk("多选：勾两个状态 → 只剩这两个状态的项目、行数对得上",
        len(pfl.get("sel") or []) == len(picks)
        and (pfl.get("rows") or 0) > 0
        and pfl.get("rows") == pfl.get("expectRows")
        and pfl.get("onlyPicked") is True)
    chk("反选：选中集合正好是补集",
        pfl.get("invClicked") is True and pfl.get("invOk") is True)
    chk("反选两次 = 回到原来那组（没有全选按钮，反选是唯一的取补入口）",
        pfl.get("invClicked2") is True and pfl.get("invBackOk") is True
        and sorted(pfl.get("invBack") or []) == sorted(picks))
    chk("清空后筛选按钮复位成「筛选 ▾」",
        (pfl.get("textBack") or "").strip() == "筛选 ▾")
    fb = m4.get("filterBack") or {}
    chk("切回「全部状态」后项目都回来了、按钮复位",
        fb.get("rows") == fb.get("total") and (fb.get("total") or 0) >= 3
        and (fb.get("text") or "").strip() == "筛选 ▾")
    chk("默认隐藏归档（后端就不发）", m4.get("archOff") is True)
    chk("关掉「隐藏归档项目」后能看到，且带已归档标记、按钮上留痕",
        m4.get("archOn") is True and "含归档" in (m4.get("archText") or ""))
    # 归档的另一条路：直接把状态改成「归档」。用户实际就是这么干的 ——
    # 真实库里两条归档项目 archived 字段都还是 0，开关只认字段的话点了没反应
    chk("状态「归档」的项目默认也看不到", m4.get("archStatOff") is True)
    chk("关掉隐藏后状态「归档」的项目看得到", m4.get("archStatOn") is True)
    chk("筛「归档」状态时不会是空树（隐藏也自动放开）",
        m4.get("archStatFilter") is True)
    chk("树的行可拖拽（分组标题行除外）", (m4.get("draggable") or 0) >= 5)
    chk("拖拽排序真的落库（末位拖到首位）", m4.get("moved") is True)
    chk("拖回原位能还原", m4.get("restored") is True)
    chk("批量折叠落库", m4.get("allShut") is True)
    # 进度条以前只认勾选框，用户是改状态的 → 进度恒为 0。现在按状态派生。
    pg = m4.get("prog") or {}
    chk("进度条认状态：改「通过」后 1/1、进度条 100%",
        pg.get("before") == [0, 1] and pg.get("after") == [1, 1]
        and pg.get("barW") == "100%")
    chk("快捷键 Ctrl+2 切到活跃页", m4.get("hotkey") is True)
    chk("快捷键切页也点亮活跃页签", m4.get("hotkeyTab") == "heat")
    chk("手动快照成功且出现在列表里",
        m4.get("snapOk") is True and m4.get("snapInList") is True)
    # 环节状态改版：点徽章弹出来的必须是制作流转的状态表（旧词一个都不该剩）
    chk("环节状态菜单 = 制作流转 10 态（含新增的「可优化」）",
        m4.get("nodeStatus") == ["待开始", "等上游", "制作中", "暂停", "中止",
                                 "已提交", "反馈", "可优化", "通过", "交付"])

    # 批量新增子环节：真实入口（弹窗 → 实时预览 → 创建）
    nd = rep.get("nodes", {})
    want6 = ["s001", "s003A", "s006", "s007", "s008", "s009"]
    titles = nd.get("titles") or []
    chk("批量录入弹窗能打开", nd.get("dlg") == "flex")
    # 弹窗里的说明文字是用户逐句定过的措辞：新的必须在，老的不许回来
    chk("弹窗文案：「挂到」改成了「添加到」",
        str(nd.get("toSay") or "").startswith("添加到："))
    chk("弹窗文案：名称占位提示 = 填写环节名称...",
        nd.get("phSay") == "填写环节名称...")
    chk("弹窗文案：未输入时的预览区提示",
        nd.get("emptySay") == "填写环节名称后，这里将展示。")
    _hs = str(nd.get("hintSay") or "")
    chk("弹窗文案：写法说明已换新句（连接号 / 引号），老句不许回来",
        "使用英文" in _hs and "连接号" in _hs and "英文双引号" in _hs
        and "顿号" not in _hs and "名字里本来" not in _hs)
    chk("实时预览报了「将创建」", nd.get("prevOk") is True)
    chk("预览里六个名字都在", nd.get("prevAll") is True)
    # 序列模板 `WD####(12-20)`：既是新语法，也是"预览框真接上了它"的证据
    chk("hint 写了序列模板写法（WD####(12-20)）",
        "WD####(12-20)" in _hs and "几个" in _hs)
    chk("序列模板解析：9 个，首末 = WD0012 / WD0020",
        nd.get("seqPvOk") is True and nd.get("seqPvCount") == 9
        and nd.get("seqPvFirst") == "WD0012" and nd.get("seqPvLast") == "WD0020")
    chk("预览框真接上了序列模板（将创建 + 首末名字都在）",
        nd.get("seqPrevOk") is True)
    chk("写歪的序列当场红着（解析 ok=False + 预览出 ✕）",
        nd.get("seqBadOk") is False and nd.get("seqPrevRed") is True)
    chk("一行文本建出 6 个环节（含原有的「子任务」共 7 个）",
        nd.get("n") == 7 and all(t in titles for t in want6))
    chk("同一批再提交 → 一个都不重复建",
        nd.get("dupCreated") == 0 and nd.get("dupSkipped") == 6)

    # ---- 本轮四项修复：置顶 / 客户开关 / 子环节筛选（#7 活跃度在 db 侧，另测） ----
    fx = rep.get("fix") or {}
    tp = fx.get("top") or {}
    chk("置顶：能问到窗口真实状态", tp.get("queried") is True)
    chk("置顶：点了「开」窗口真的开着、点了「关」真的关了",
        tp.get("onReal") is True and tp.get("offReal") is True)
    chk("置顶：按钮亮灭跟窗口真实状态一致（老毛病就是反着来）",
        tp.get("btnSync") is True)
    cl = fx.get("client") or {}
    chk("客户开关：默认显示 → 关掉后项目行上的客户名真的没了",
        (cl.get("before") or 0) > 0 and cl.get("after") == 0)
    chk("客户开关：再点一次客户名回来（行数和原来一致）",
        cl.get("back") == cl.get("before"))
    # 子环节筛选：不再塞在主菜单里，改成每个有子环节的行各挂一个筛选框
    nf = fx.get("nodeFilter") or {}
    chk("有子环节的行上都有自己的筛选框", nf.get("hasBtn") is True)
    chk("筛选框里是搜索框，打文字就筛（剩下的行里有目标、行数变少）",
        nf.get("hasInput") is True and nf.get("txtHit") is True
        and nf.get("txtTrimmed") is True)
    # 条件要压到深层：只有「自己命中 or 子孙命中」的那些行能露出来，
    # 父环节的子树不许按它自己的空条件重画一遍（老 bug：被筛掉的子环节从缝里钻回来）
    chk("筛选条件压到深层：不含关键字、子孙也没有关键字的行不跟着露出来 (%s/%s 行=%s)"
        % (nf.get("txtShown"), nf.get("txtExpN"), nf.get("txtRows")),
        nf.get("txtNoLeak") is True)
    # 留下的行要么自己就是该状态，要么子树里有该状态（父环节要留着才看得出归属）
    chk("按状态筛：只剩「该状态 + 它的父环节」",
        nf.get("statPicked") is True and (nf.get("feedbackRows") or 0) > 0
        and nf.get("statKeepOk") is True and nf.get("statTrimmed") is True)
    chk("按钮上写着筛的是什么、筛上了就一直露着",
        "反馈" in (nf.get("label") or "") and nf.get("btnAlways") is True)
    chk("「清除本层筛选」后行都回来",
        nf.get("cleared") is True and (nf.get("back") or 0) == (nf.get("bAll") or 0))
    # ---- 子环节筛选的多选 / 反选（2026-09-30） ----
    nm = nf.get("multi") or {}
    chk("子环节筛选：状态组带「反选 / 清空」两个按钮（全选已撤）",
        ((nm.get("actRows") or [[]])[0] or []) == ["反选", "清空"]
        and nm.get("noAllBtn") is True)
    chk("子环节筛选·反选：本来只勾「反馈」，反选后 = 其余 8 个",
        nm.get("pickFB") is True and (nm.get("stStart") or []) == ["反馈"]
        and nm.get("invClicked") is True
        and nm.get("invLen") == (nm.get("allExpect") or 0) - 1
        and nm.get("invNoFB") is True)
    chk("子环节筛选·反选两次 = 回到「只勾反馈」",
        nm.get("invClicked2") is True and nm.get("invBackOk") is True
        and (nm.get("invBack") or []) == ["反馈"])
    chk("子环节筛选·清空：这一组条件整个消失",
        nm.get("clrClicked") is True and not nm.get("afterClr"))
    if nm.get("hasWhoGroup"):
        chk("子环节筛选·制作人：勾一个 → 反选 = 其余的人，清空后条件消失",
            len(nm.get("pickedWho") or []) == 1
            and ((nm.get("pickedWho") or [None])[0] == (nm.get("whoAll") or [None])[0])
            and nm.get("whoInvClicked") is True and nm.get("whoInvOk") is True
            and not nm.get("afterWhoClr"))
    # 用户 2026-10-04 报的：制作人反选把"还没派人"的环节一起吞了。
    # 补集里必须有「未分配」，且屏幕上要真的多出那些行。
    if nm.get("hasWhoGroup") and nm.get("noneInLayer"):
        _want = nm.get("whoInvWant") or []
        chk("制作人反选：补集里带上了「未分配」(want=%s)" % (_want,),
            _want and _want[-1] == ""
            and (nm.get("whoInv") or []) == _want)
        chk("制作人反选后：没人派工的环节真的还在屏幕上 (rows=%s)"
            % (nm.get("whoInvRows"),),
            nm.get("noneKept") is True and nm.get("whoDropped") is True)
        chk("制作人反选后：菜单里「（未分配）」这一行露出来、且带着勾",
            nm.get("noneRowShown") is True and nm.get("noneRowTicked") is True)

    # ---- 到期小标文案（2026-09-30 用户要求：别再用「D-1」「今天」这种像密码的记号） ----
    tree0 = rep.get("tree") or {}
    dues = tree0.get("dueTexts") or []
    chk("到期小标写的是整句话（%s）" % (dues,),
        any("天后到期" in t for t in dues)
        and not any(t.strip() == "今天" for t in dues)
        and not any(t.startswith("D-") for t in dues))
    chk("今天到期写得明明白白（不是光一个「今天」）", "今天到期" in dues)
    chk("3 天后到期这档也写成整句「3 天后到期」", "3 天后到期" in dues)

    # ---- 「点状态徽章才出状态菜单」（用户报的：点「今日必做」的文字才弹） ----
    sc = tree0.get("stClick") or {}
    chk("挂「今日必做」的项目行：状态徽章带 .st，且它不是行上第一个 badge",
        sc.get("firstText") == "今日必做" and sc.get("stText") not in (None, "", "今日必做")
        and (tree0.get("rawBadges") or 0) > (tree0.get("stBadges") or 0))
    chk("点状态徽章弹出的是项目状态菜单（%s）" % (sc.get("menu"),),
        bool(sc.get("menu")) and "归档" in (sc.get("menu") or []))
    chk("点「今日必做」那颗不再弹任何菜单（菜单就是被它抢走的）",
        sc.get("firstPops") is False)

    # ---- 打卡弹窗（2026-10-09：打卡 = 重置「今日必做」）----
    # 真重置语义在数据层钉（tests/smoke_db.py）；这里只确认界面这一头没断：
    # 弹窗能开、列得出项目、priority=1 的项默认勾上、关得掉。
    ck = rep.get("checkin") or {}
    chk("打卡弹窗打得开", ck.get("dlgOpen") is True)
    chk("弹窗里列出了候选项目（%s 个）" % ck.get("n"), (ck.get("n") or 0) >= 1)
    chk("已挂「今日必做」的项目在弹窗里默认勾上（它标记残留态）",
        ck.get("hasProj") is True and ck.get("defaultChecked") is True)
    chk("默认勾选数 >= 1（priority 或「进行中」任一命中）",
        (ck.get("checkedCount") or 0) >= 1)
    chk("打卡弹窗关得掉", ck.get("dlgClosed") is True)

    # ---- 真走「⋯ → 删除」这条点击路径（2026-10-10 补）----
    # 以前这里只调后端 API（call("delete", ...)），把菜单、confirm、handler
    # 全绕过去了 —— 于是「删除项引用了不存在的变量、点下去抛 ReferenceError」
    # 这种 bug 能瞒过 255 条全绿断言，用户点不动而测试零红灯。
    # 这几条必须真的走到"点下去"为止。
    dd = rep.get("deldlg") or {}
    # 行没渲染 / 样本没建出来：直接失败，别静默跳过（静默跳过就是假绿的起点）
    _dderr = dd.get("err")
    chk("删除冒烟样本环节建得出来、行渲染得出来"
        + ("（%s）" % _dderr if _dderr else ""),
        dd.get("made") is True and dd.get("rowFound") is True and not _dderr)
    chk("点 ⋯ 能弹出操作菜单", dd.get("popupOpen") is True)
    chk("菜单里找得到「删除」这一项（%s）" % (dd.get("labels"),),
        dd.get("delFound") is True)
    # 颜色语义：删除必须是 danger（红）—— dim（灰）的意思是"别指望点它"。
    # 比对的是 CSS 变量的真实值（--red），不写死色号：主题有明暗两套。
    chk("「删除」带 danger 类（实得 %r）" % dd.get("delClass"),
        "danger" in (dd.get("delClass") or ""))
    chk("「删除」渲染成 --red 红（实得 %s，期望 %s）"
        % (dd.get("delColor"), dd.get("wantRed")),
        dd.get("isRed") is True)
    chk("「删除」不再是 --tx3 灰（实得 %s，灰是 %s）"
        % (dd.get("delColor"), dd.get("dimGray")),
        dd.get("isGray") is False)
    # 核心：点下去真的问到 confirm、真的删掉了、且没有静默异常
    chk("点「删除」真的弹了确认框（问了 %s 次）" % dd.get("asked"),
        (dd.get("asked") or 0) >= 1)
    chk("确认文案说清删的是谁（%r）" % dd.get("askedMsg"),
        "删除" in (dd.get("askedMsg") or ""))
    chk("确认后环节真的从库里没了", dd.get("stillThere") is False)
    chk("整条点击路径没有静默异常（__err=%r）" % dd.get("errAfterClick"),
        not dd.get("errAfterClick"))
    chk("deldlg 自评通过", dd.get("checked") is True)
    # 行尾 ⋯ 也钉了明确 class：裸 `.mini` 取的是第一个匹配，是同一条教训
    chk("每个项目 / 环节行尾的 ⋯ 都带 .mini.more（%s 个，项目行 %s 个）"
        % (tree0.get("moreBtns"), tree0.get("prjRows")),
        tree0.get("moreBtns") == tree0.get("prjRows"))
    # 只有状态徽章有"能点"的样子（cursor:pointer）；纯标记的徽章不该暗示可点
    chk("只有状态徽章是可点的样子（状态=%s，其它徽章=%s）"
        % (tree0.get("stCursor"), tree0.get("rawCursor")),
        tree0.get("stCursor") == "pointer" and tree0.get("rawCursor") == "default")

    # ---- 顶栏：不显示名字，只剩图标；日期与刷新跟着页签走（2026-10-01 用户口径）----
    # 名字以前在顶栏单独写一行（「项目看板」），可窗口标题栏本来就写着品牌名 ——
    # 同一个信息占两块地方，现在只留左上角图标。**反着钉**：哪天又有人把名字
    # 加回顶栏，这里立刻变红，不用靠肉眼发现。
    chk("顶栏不显示任何名字文字（实得 %r）" % tree0.get("brandText"),
        tree0.get("brandText") == "")
    chk("顶栏左上角挂着程序目录内的图标（%s 个 img）" % tree0.get("brandImg"),
        tree0.get("brandImg") == 1)
    # 顺序必须是 图标 → 页签 → 日期 → 刷新：日期说的是「这一页的数据几点读的」，
    # 放回图标边上会被读成页面标题的一部分；刷新紧跟日期，因为它俩是一组操作。
    _hdr = tree0.get("headerOrder") or []
    _seq = ("segPage", "date", "btnRefresh")
    chk("顶栏顺序是 图标→页签→日期→刷新（实得 %s）" % (_hdr,),
        all(x in _hdr for x in _seq)
        and _hdr.index("segPage") < _hdr.index("date") < _hdr.index("btnRefresh"))
    chk("日期与刷新属于页签那一组，没混进右侧按钮里（实得 %s）" % (_hdr,),
        "spacer" in _hdr and _hdr.index("spacer") > _hdr.index("btnRefresh"))
    chk("document.title 带版本号（实得 %s）" % tree0.get("docTitle"),
        tree0.get("docTitle") == TITLE)
    chk("版本号是 #.# 两位（%s）" % VERSION, len(str(VERSION).split(".")) == 2
        and all(x.isdigit() for x in str(VERSION).split(".")))
    chk("设置页末尾有版本行、显示版本号 + 发布日期（实得 %s）" % tree0.get("verLine"),
        tree0.get("verLine") == VERSION_LINE and VERSION in tree0.get("verLine", ""))
    # 托盘提示的拼装（tooltip 本身拿不回来读，只能钉住这个纯函数）
    chk("托盘提示只留产品名 + 今日数（%s / %s）" % (tray_tip(3), tray_tip()),
        tray_tip(3) == f"{APP_NAME}：今日必做 3"
        and tray_tip(1) == f"{APP_NAME}：今日必做 1"
        and tray_tip() == APP_NAME
        and TITLE not in tray_tip(3))
    # 通知身份：显式 AUMID 注册成功（pathinfo 回读的是系统真实值，不是常量）。
    # 不设的话 Win11 通知显示「天在看.exe」+ fallback 图标（2026-10-01 蓝方块就是它）
    chk("通知身份已显式注册（实得 %s）" % tree0.get("aumid"),
        tree0.get("aumid") == AUMID)

    # ---- 活跃页口径文案（2026-09-30 用户原话，逐字对） ----
    tip = heat.get("tip") or ""
    chk("活跃页提示就是用户要的那句（实得 %s）" % tip,
        tip == "打卡 或环节变更为 制作中/已提交/反馈/可优化/交付 才会计入活跃。")
    # 文案里的 5 个状态必须跟后端白名单同源，不然提示会"写着一个早已改掉的规则"
    from models import NODE_ACTIVE_STATUS  # noqa: E402
    chk("提示里的状态词跟后端白名单一致（%s）" % (NODE_ACTIVE_STATUS,),
        all(s in tip for s in NODE_ACTIVE_STATUS))

    # ---- #5 设置项：已逐项实测确认全部有效（2026-09-27），不再逐条回归。
    #      只留两条总检查：① 没有失效项；② 重开面板回显的是库里的值
    #      （回显那个是当时唯一真出过问题的点，值得继续盯着）。
    #      逐项明细仍在自检里采集（st.level / st.time / st.stale / st.nag /
    #      st.tg / st.theme），要排查时直接看 report 就行。 ----
    st = rep.get("settings") or {}
    chk("设置项没有失效的（bad=%s）" % (st.get("bad"),), st.get("allOk") is True)
    chk("设置·重开面板回显的是库里的值（老 bug 是显示旧值）",
        st.get("echoOk") is True)
    # 停滞阈值统一成一项（2026-09-30）：面板上「停滞」只有一行，库里也只有一个键
    sl = st.get("stale") or {}
    chk("停滞阈值只剩一项（fields=%s v=%s）" % (sl.get("fields"), sl.get("v")),
        sl.get("fields") == 1 and sl.get("v") == "5" and sl.get("ok") is True)

    # ---- 分类（分组）可增删改（2026-09-30） ----
    gp = rep.get("groups") or {}
    tree = rep.get("tree") or {}
    chk("每个分组行尾都有 ⋯（分类操作）",
        (gp.get("menus") or 0) >= 2 and (tree.get("grpMenus") or 0) >= 2)
    # 用户要的：分类行右边多一颗 ＋，点了直接在这个分类下建项目
    chk("每个分组行尾也都有 ＋（在这个分类下新建项目）",
        (gp.get("adds") or 0) == (gp.get("menus") or 0)
        and (tree.get("grpAdds") or 0) >= 2)
    chk("分组 ⋯ 菜单 = 重命名 / 新建 / 删除",
        any("重命名分类" in str(x) for x in (gp.get("menuItems") or []))
        and any("新建分类" in str(x) for x in (gp.get("menuItems") or []))
        and any("删除这个分类" in str(x) for x in (gp.get("menuItems") or [])))
    chk("新建分类：库里多一条、树里多一行、名字对",
        gp.get("addOk") is True and gp.get("rowAdded") is True
        and gp.get("titleAdded") == "冒烟分类")
    # ＋ 必须开的是**同一个**新建项目页（不是另造一套），字段一样齐
    chk("分组行 ＋ 开的就是新建项目页（flds=%s）" % gp.get("addFlds"),
        gp.get("addOpen") is True and gp.get("addFlds") == 8
        and gp.get("addHasTitle") is True)
    chk("从 ＋ 进来时，分类默认就是这个分组（%s）" % gp.get("addSel"),
        gp.get("addSel") == gp.get("newKey"))
    # 用户要的：分类文字处双击可编辑。这里真发 dblclick + 改文字 + blur
    chk("双击分类名进入可编辑（contentEditable）", gp.get("dblEdit") is True)
    chk("双击改名真落库：**库里的**名字换了（%s）" % gp.get("renamed"),
        gp.get("renamed") is True)
    chk("树上那行显示的就是新名字（%s）" % gp.get("renamedDom"),
        gp.get("renamedDom") is True)
    chk("双击改名不动键 —— 挂在这个分类下的项目不会丢",
        gp.get("renamedKeyKept") is True)
    chk("改完名字退出编辑态（不是一直停在可编辑框里）", gp.get("dblOff") is True)
    chk("改完名字焦点也不该还留在那行里（%s）" % gp.get("dblFocus"),
        gp.get("dblFocus") is True)
    chk("项目能挪到新分类里（挪完就挂在那个分组行下面）",
        gp.get("moved") is True)
    chk("非空分类删不掉，并且说清还有几个项目（%s）" % (gp.get("delBusyMsg"),),
        gp.get("delBusyOk") is True)
    chk("项目挪走之后空筐能删、树里那行也没了",
        gp.get("delOk") is True and gp.get("rowGone") is True)
    chk("这一轮下来分类回到原样（%s -> %s）" % (gp.get("before"), gp.get("after")),
        gp.get("restored") is True)
    # 用户点名要确认的：分类增删之后，「＋项目」弹窗里的分类要跟着变
    cf = gp.get("catFollow") or {}
    chk("删掉分类后再开新建页：按钮跟着少一个（%s -> %s）" % (gp.get("before"), cf.get("keys")),
        cf.get("n") == len(gp.get("before") or []) and cf.get("gone") is True)
    chk("而且没停在一个已经删掉的分类上（act=%s key=%s）" % (cf.get("act"), cf.get("actKey")),
        cf.get("act") == 1 and (cf.get("actKey") in (gp.get("before") or [])))
    chk("分类跟随变化的结论被记下来了", cf.get("ok") is True)
    nw2 = rep.get("neww") or {}
    chk("新建项目弹窗的分类按钮按库里的分类现画（不是写死的两个）",
        (nw2.get("catBtns") or 0) >= 2 and (nw2.get("catAct") or 0) == 1
        and "商业项目" in (nw2.get("catTexts") or []))
    chk("弹窗分类按钮带的是分类键（%s）" % (nw2.get("catKeys"),),
        len(nw2.get("catKeys") or []) >= 2 and "" not in (nw2.get("catKeys") or []))

    # ---- #3 多选批量改状态 / 制作人（真勾、真改、回读库） ----
    mu = rep.get("multi") or {}
    chk("多选：默认没有复选框、批量条是收着的",
        mu.get("pickBefore") == 0 and mu.get("barBefore") == "none")
    chk("多选：点「多选」后每行出现复选框、批量条露出来",
        (mu.get("pickAfter") or 0) >= 3 and mu.get("barAfter") == "flex")
    # 用户报的核心 bug：点复选框本身时"勾了不显示勾，点下一个才补上"（差一拍）
    chk("多选：点复选框本身 → 当场就显示勾（老 bug 是差一拍）",
        (mu.get("pickClick1") or {}).get("ok") is True)
    # 2026-10-10 用户要求改回「不按任何键 = 加选」。
    #   这条断言历史上来回翻过两次，别再写反：
    #     最初"取反"→累加（用户报"现在是加减选模式"）→ 10-02 改真单选 → 10-10 又改加选。
    #   现在的口径：无修饰键 = 加选、Shift = 范围选、连点同一条 = 只撤它自己。
    chk("多选：直接点第二条 = **加选**（第一条还在，两条都进集合）",
        (mu.get("pickClick2") or {}).get("ok") is True)
    chk("多选：再点同一条 → 只撤它自己（别人不动，不是清空整个选择集）",
        (mu.get("pickClick3") or {}).get("ok") is True)
    chk("多选：Ctrl 点 → 仍然是加选（两条都在）",
        (mu.get("pickCtrl") or {}).get("ok") is True)
    chk("多选：Shift 点**复选框**也要能范围选（以前只有点整行才认）",
        (mu.get("shiftCp") or {}).get("ok") is True)
    # 级联选：点父环节 → 它 + 全部后代一起进选择集；再点一次整棵子树全撤
    chk("多选：状态色块是竖条（%sx%s、有色），比老圆点醒目"
        % (mu.get("dotW"), mu.get("dotH")), mu.get("dotOk") is True)
    chk("多选：点父环节 → 它 + 全部后代一起选中（级联选）",
        mu.get("hasParent") is True
        and (mu.get("cascadeWant") or 0) >= 2
        and mu.get("cascadeSel") == mu.get("cascadeWant")
        and mu.get("cascadeAllIn") is True)
    chk("多选：再点同一父环节 → 整棵子树全部取消（级联撤）",
        mu.get("cascadeOff") == 0 and mu.get("cascadeNoneIn") is True)
    # 用户报的 bug（2026-09-27）：勾主项后减选一个子项 → 主项要当场变未勾，
    #   而且它也要从选择集里剔出去（不然批量会改到屏幕上没勾的那条）
    chk("多选：主项只在「自己和全部子项都选中」时才算勾（减选子项后主项当场变未勾）",
        (mu.get("partial") or {}).get("kidRow") is True
        and (mu.get("partial") or {}).get("ok") is True)
    chk("多选：残缺的主项再点一下 → 整棵全选上（按显示态取反，不是取消）",
        (mu.get("partial") or {}).get("reclickAllIn") is True
        and (mu.get("partial") or {}).get("reclickDomOn") is True)
    # Ctrl 加选 / 减选（用例特意挑了两条**互不包含**的行 —— 挑到父子关系会误报）
    chk("多选：Ctrl 点 → 加选（选区变大、目标进集合）；再 Ctrl 点同一条 → 减选",
        (mu.get("ctrlPair") or {}).get("has") is True
        and (mu.get("ctrlAdd") or {}).get("has2") is True
        and (mu.get("ctrlAdd") or {}).get("after", 0) > (mu.get("ctrlAdd") or {}).get("single", 0)
        and (mu.get("ctrlSub") or {}).get("gone1") is True)
    # Shift 范围选：锚点 → 目标，中间整段一起选；再来一次整段取消
    chk("多选：Shift 点另一条 → 中间整段全选上（Maya 大纲式范围选）",
        (mu.get("shift") or {}).get("span", 0) >= 2
        and (mu.get("shift") or {}).get("all") is True
        and (mu.get("shift") or {}).get("sel", 0) >= 2)
    # 光看选择集不够：屏幕上真的勾上了没有（用户看的是这个）
    chk("多选：范围选后屏幕上真的勾上了（首条/末条/所有画出来的都勾）",
        (mu.get("shiftDom") or {}).get("first") is True
        and (mu.get("shiftDom") or {}).get("last") is True
        and (mu.get("shiftDom") or {}).get("allDrawn") is True
        and not (mu.get("shiftDom") or {}).get("bad"))
    # 老 bug：Shift 落点在折叠箭头上会被吞掉 → 范围选完全没发生
    chk("多选：Shift 落在折叠箭头上也照常范围选（老 bug 是整击被吞）",
        (mu.get("shiftTw") or {}).get("ok") is True)
    chk("多选：目标已选时再 Shift 点 → 整段一起取消",
        (mu.get("shiftOff") or {}).get("sel") == 0
        and (mu.get("shiftOffDom") or {}).get("last") is False)
    # 2026-10-02 用户要的：改**任意一条选中项**的状态 / 制作人 → 其它选中项跟着改。
    #   断言回到数据库里读（call("load")），光看界面不够。
    chk("多选：点选中项的状态徽章 → 选中的全都改成同一个状态",
        (mu.get("rowStat") or {}).get("ok") is True)
    chk("多选：点选中项的「制作人」→ 选中的全都改成同一个制作人",
        (mu.get("rowArtist") or {}).get("ok") is True)
    # 2026-10-03：截止日期 / 备注接进同一套批量改法
    chk("多选：点选中项的「截止日期」→ 选中的全都改成同一个日期",
        (mu.get("rowDue") or {}).get("ok") is True)
    chk("多选：点选中项的「备注」→ 选中的全都改成同一个备注",
        (mu.get("rowNote") or {}).get("ok") is True)
    # 2026-10-03 用户报的 bug：点状态徽章（不 stopPropagation）→ 冒泡到整行被当成
    #   「直接点=单选」，选择集只剩一条，于是"改了一批、屏幕上却只剩一个勾着"。
    _k = mu.get("rowStatKeep") or {}
    chk("多选：点状态徽章不会把选择集清掉（实际剩 %s / 应剩 %s）"
        % (_k.get("sel"), _k.get("want")),
        _k.get("ok") is True)
    # 「全选本层」按用户要求撤掉，改成点父级自带级联。
    # 断言方式：把批量条上实际有的按钮采回来 —— 既确认没把"全选"加回来，也能发现少了谁。
    _bl = mu.get("barLayout") or {}
    chk("多选：批量条一行排完（计数+提示+%s 颗按钮都要在同一行，实得 %s 个元素）"
        % ((_bl.get("wantKids") or 0) - 2, _bl.get("kids")),
        _bl.get("ok") is True)
    chk("多选：一条没选时计数整段留白、不写「还没选」",
        _bl.get("cntWhenNone") == "")
    _bs = mu.get("barSticky") or {}
    chk("多选：树往下滚时批量条粘在内容区顶部、不被行盖住（实际=%s）" % (_bs,),
        _bs.get("ok") is True)
    _bb = mu.get("bulkBtns") or []
    chk("多选：批量条就这七颗按钮、没有「全选本层」（实际=%s）" % (_bb,),
        len(_bb) == 7
        and not any("全选" in t for t in _bb)
        and all(any(k in t for t in _bb)
                for k in ("状态", "制作人", "截止日期", "备注", "删除", "清空", "退出")))
    # ---- 批量删除（2026-10-10）----
    # 关键点跟单条删除那次一个道理：**必须真点按钮**。直调 call("bulk_delete")
    # 会把"按钮没绑上 / confirm 没弹 / 选择集没清"全绕过去。
    _bd = mu.get("bulkDel") or {}
    _bdbtn = mu.get("bulkDelBtn") or {}
    chk("批量条上有「删除」按钮，且带 warn 类（红）",
        _bdbtn.get("found") is True and "warn" in (_bdbtn.get("cls") or ""))
    chk("「删除」在「清空」左边、「退出多选」在最右（破坏性按钮不贴边）",
        _bdbtn.get("delBeforeClear") is True and _bdbtn.get("exitRightmost") is True)
    chk("批量删除样本建得出来、行上有点选框",
        _bd.get("made") is True and _bd.get("rowFound") is True)
    chk("勾父环节 → 级联选中整棵（含 2 个子）", (_bd.get("sel") or 0) >= 3)
    chk("点「删除」真的弹了确认框（问了 %s 次）" % _bd.get("asked"),
        (_bd.get("asked") or 0) >= 1)
    chk("确认文案里说清删几个环节（%r）" % _bd.get("askedMsg"),
        "删除选中的" in (_bd.get("askedMsg") or ""))
    chk("确认后父环节真的从库里没了", _bd.get("parentGone") is True)
    chk("两个子环节也被级联删掉了",
        _bd.get("kid1Gone") is True and _bd.get("kid2Gone") is True)
    chk("删完选择集清空、批量条不挂幽灵计数",
        (_bd.get("selAfter") or 0) == 0 and not (_bd.get("cntAfter") or "").strip())
    chk("多选·改状态：菜单里有状态项、勾的几条真的都改了",
        mu.get("hasMenu") is True and mu.get("statOk") is True)
    chk("多选·改制作人：勾的几条制作人都改成了选的人",
        bool(mu.get("artistPick")) and mu.get("artistOk") is True)
    chk("多选：「清空」把选择集清干净",
        mu.get("none") == 0)
    chk("多选：退出后复选框消失、批量条收起",
        mu.get("pickGone") == 0 and mu.get("barGone") == "none")

    # ---- #6 设置里能改数据文件 ----
    dbf = rep.get("dbFile") or {}
    chk("数据文件：面板上「更改 / 恢复默认位置」两个按钮在",
        dbf.get("btn") == 1 and dbf.get("btnDefault") == 1)
    chk("数据文件：能问出来路（默认位置 / 设置指定 / 环境变量）",
        dbf.get("pathOk") is True and bool(dbf.get("from")))
    chk("数据文件：选同一个文件会被明确拒绝（不静默成功）",
        dbf.get("sameRejected") is True)
    chk("数据文件：目录不存在会被拒绝并说明原因",
        dbf.get("badDirRejected") is True)
    chk("数据文件：空路径会被拒绝", dbf.get("emptyRejected") is True)

    # ---- #2 多机同时打开（开关 + 状态接口） ----
    chk("多机同时打开：设置面板里有这个开关", dbf.get("shareBtn") == 1)
    chk("多机同时打开：set_share_db 能写回去（写的是当前值，不动真实配置）",
        dbf.get("setShare") is True)
    chk("多机同时打开：sync_state 结构齐（share/changed/holders）",
        dbf.get("sync") is True)
    chk("多机同时打开：顶部提示条在（平时不占位）", dbf.get("syncBar") == 1)

    # ---- 客户删除（入口已从弹窗底部挪到列表行右边的 ⋯ 菜单） ----
    cl = rep.get("clients") or {}
    chk("客户弹窗：底部那颗「删除客户」按钮撤了（挪进列表行的 ⋯ 菜单）",
        cl.get("opened") is True and cl.get("delBtnGone") is True)
    chk("客户弹窗：结算周期那句提示文字撤掉了", cl.get("subGone") is True)
    chk("客户列表每行右边都有 ⋯（%s 行 / %s 个）" % (cl.get("rows"), cl.get("ops")),
        (cl.get("ops") or 0) > 0 and cl.get("rows") == cl.get("ops"))
    chk("⋯ 菜单里有「编辑客户」和「删除客户」（%s）" % (cl.get("menu"),),
        cl.get("hasEdit") is True and cl.get("hasDel") is True)
    # ⚠ 菜单得画在客户弹窗（遮罩 z-index 200）之上，不然"点了三点没反应"
    chk("菜单画在客户弹窗之上（z=%s > 200）" % (cl.get("popZ"),),
        (cl.get("popZ") or 0) > 200 and (cl.get("popTop") or 0) > 0)
    chk("「删除客户」真的染红了、跟「编辑客户」不同色（%s）" % (cl.get("delColor"),),
        cl.get("delRed") is True)
    chk("点「编辑客户」还是能进编辑态（表单填上、按钮变「更新客户」%s）"
        % (cl.get("saveTxt"),),
        cl.get("nameFilled") is True and cl.get("saveTxt") == "更新客户")
    chk("点「删除客户」后客户从列表消失、表单清空、不报错（alert=%s）"
        % (cl.get("delAlert"),), cl.get("delOk") is True)
    chk("还有项目挂靠的客户删不掉，并把原因说出来（msg=%s）"
        % (cl.get("busyMsg"),),
        cl.get("busyBlocked") is True and cl.get("stillRefused") is True)

    # ---- 外包商 / 制作人名录（2026-10-16 打通） ----
    # 这一整段是"外包商 ↔ 制作人 ↔ 外包支出"三处联动的回归：造条目 → 挂上去 →
    # 记一笔 → 看汇总对不对 → 删条目被拦。
    vr = rep.get("vendor") or {}
    chk("种子里三条外包商都在（%s）" % (vr.get("seeded"),),
        sorted(vr.get("seeded") or []) == ["冒烟制作人", "冒烟小工", "冒烟工作室"])
    chk("#vendorOpts 候选里有种子（两处共用的那份，%s）" % (vr.get("optsHasSeed"),),
        "冒烟制作人" in (vr.get("optsHasSeed") or []))
    chk("点「外包商」按钮能开面板", vr.get("opened") is True)
    chk("外包商列表每行右边都有 ⋯（%s 行 / %s 个）"
        % (vr.get("rows"), vr.get("ops")),
        (vr.get("ops") or 0) > 0 and vr.get("rows") == vr.get("ops"))
    chk("列表表头是 外包商 / 类型 / 挂靠（%s）" % (vr.get("head"),),
        vr.get("head") == ["外包商", "类型", "挂靠", ""])
    # 「挂靠」列 2026-10-16 起只显示挂靠项目数（用户要求精简掉「环节 / 笔款」）
    _hang = vr.get("hangCell") or vr.get("hangCell2")
    chk("「挂靠」列只写挂靠项目数、且真写出来了（%s / %s）"
        % (vr.get("hangCell"), vr.get("hangCell2")),
        (_hang or "").endswith("项目"))
    chk("「挂靠」列不再混入「环节 / 笔款」（整列 %s）" % (vr.get("hangCells"),),
        all(("环节" not in (c or "")) and ("笔款" not in (c or ""))
            for c in (vr.get("hangCells") or [])))
    chk("没挂项目的那条（冒烟工作室，只有 2 笔款）显示「—」（%s）" % (vr.get("hangCellNone"),),
        vr.get("hangCellNone") == "—")
    chk("⋯ 菜单里有「编辑外包商」和「删除外包商」（%s）" % (vr.get("menu"),),
        vr.get("hasEdit") is True and vr.get("hasDel") is True)
    chk("菜单画在外包商弹窗之上（z=%s）" % (vr.get("popZ"),),
        (vr.get("popZ") or 0) > 200)
    chk("「删除外包商」真的染红了、跟「编辑」不同色（%s）" % (vr.get("delColor"),),
        vr.get("delRed") is True)
    chk("点「编辑外包商」四栏回填、按钮变「更新外包商」（%r / %r）"
        % (vr.get("nameFilled"), vr.get("saveTxt")),
        vr.get("nameFilled") == "冒烟待删外包商"
        and vr.get("saveTxt") == "更新外包商"
        and vr.get("kindFilled") == "团队")
    chk("联系方式 / 备注也回填（%r / %r）" % (vr.get("contactW"), vr.get("noteW")),
        vr.get("contactW") == "wx:smoke" and vr.get("noteW") == "外协样本")
    chk("类型两档能切（切到「个人」=%s）" % (vr.get("kindSwitch"),),
        vr.get("kindSwitch") == "个人")
    chk("「清空表单」回到新建态（名字清空、按钮变回「保存外包商」）",
        vr.get("resetCleared") is True)
    # 改名不能断关联 —— 断了等于这一轮"强关联"白做
    chk("改名后名录跟着变（%s）" % (vr.get("renamed"),), vr.get("renamed") is True)
    chk("改名**保住了同一个 id**（%s == %s）"
        % (vr.get("renameKeptId"), vr.get("nodeBeforeId")),
        vr.get("renameSameId") is True)
    chk("挂靠也没断：项目上还是那条外键（%r）" % (vr.get("renameKeptLink"),),
        vr.get("renameKeptLink") is True)
    chk("项目行上的制作人名字跟着改了（数据层 %r / 行上 %r）"
        % (vr.get("renameTreeName"), vr.get("renameRowTxt")),
        (vr.get("renameTreeName") or "").strip() == "冒烟小工V2"
        and "冒烟小工V2" in (vr.get("renameRowTxt") or ""))
    chk("保存后外包商弹窗没被顺手关掉", vr.get("dlgStillOpen") is True)
    chk("还有外包支出挂着的删不掉，原因说到「外包支出」上（msg=%s）"
        % (vr.get("busyMsg"),),
        vr.get("busyBlocked") is True and vr.get("busyStill") is True)
    chk("没人挂靠的能删掉、表单清空、不报错（alert=%s）" % (vr.get("delAlert"),),
        vr.get("deleted") is True and vr.get("delAlert") is None
        and vr.get("formCleared") is True)
    # 「外包商」那一行只跟「外包支出」绑在一起（原来两处各写一遍显隐，必然漏一处）
    chk("「记一笔」：造款/开票时外包商行藏着，外包支出才露出来（%s/%s/%s/%s）"
        % (vr.get("vdrRowOnPayment"), vr.get("vdrRowOnOutsource"),
           vr.get("vdrRowOnInvoice"), vr.get("vdrRowBack")),
        vr.get("vdrRowOnPayment") is False and vr.get("vdrRowOnOutsource") is True
        and vr.get("vdrRowOnInvoice") is False and vr.get("vdrRowBack") is True)
    chk("外包支出没选外包商：提示出来了、弹窗不关、库里也没多记录（%r）"
        % (vr.get("emptyToast"),),
        "外包商" in (vr.get("emptyToast") or "")
        and vr.get("emptyKept") is True and vr.get("emptyNoRec") is True)
    chk("「记一笔」里填个新名字 → 条目现建、钱挂到他名下（%s / %s）"
        % (vr.get("newMade"), vr.get("newGot")),
        vr.get("newMade") is True and vr.get("newLinked") is True)
    chk("那笔的钱数是界面上填的那个（%s / %s）"
        % (vr.get("newAmount"), vr.get("newStatus")),
        abs(float(vr.get("newAmount") or 0) - 777) < 0.01
        and vr.get("newStatus") == "应付")
    chk("汇总口径：新条目 已付 0 / 应付 777 / 合计 777 / 1 笔 / 1 个项目"
        "（%s/%s/%s/%s/%s）"
        % (vr.get("sumPaid"), vr.get("sumPending"), vr.get("sumTotal"),
           vr.get("sumCount"), vr.get("sumProjects")),
        # ⚠ 不能用 `x or -1` 兜底：0 是**要断言的正确值**，会被 or 吃掉成 -1，
        # 于是"已付 0"这条永远红着，看着像汇总算错了。
        num(vr.get("sumPaid")) == 0
        and num(vr.get("sumPending")) == 777
        and num(vr.get("sumTotal")) == 777
        and vr.get("sumCount") == 1 and vr.get("sumProjects") == 1)
    chk("同一个团队的两笔合成一行（已付 20000 / 应付 5000 / 2 笔，%s/%s/%s）"
        % (vr.get("seedPaid"), vr.get("seedPending"), vr.get("seedCount")),
        num(vr.get("seedPaid")) == 20000
        and num(vr.get("seedPending")) == 5000
        and vr.get("seedCount") == 2)
    chk("样本全收拾干净、名录回到种子三条（%s）" % (vr.get("backTo"),),
        vr.get("cleaned") is True
        and (vr.get("backTo") or []) == ["冒烟制作人", "冒烟小工", "冒烟工作室"])
    chk("外包商全程无未捕获报错（实得 %r）" % (vr.get("errNew"),),
        vr.get("errNew") is None)

    # 日期选择弹窗（2026-10-16）：环节「编辑截止日期」从 prompt 手输换成**日历控件**。
    # 走真实入口（⋯ 菜单 → 编辑截止日期）+ 真点保存 + 回查库 —— 只验"弹窗画出来了"
    # 会假绿（点了没反应那类 bug 只有真点才暴露）。
    dt = rep.get("date") or {}
    chk("日期弹窗样本建出来了", dt.get("made") is True)
    chk("样本一开始没有截止日期（%r）" % (dt.get("before"),),
        (dt.get("before") or "") == "")
    chk("⋯ 菜单里有「编辑截止日期」（%s）" % (dt.get("menu"),), dt.get("menuHas") is True)
    chk("点它真开出日期弹窗（title=%r sub=%r）" % (dt.get("title"), dt.get("sub")),
        dt.get("dlgOpen") is True and dt.get("title") == "编辑截止日期"
        and "日期弹窗样本" in (dt.get("sub") or ""))
    chk("弹窗里是个**日期控件**（原生 date 框 + 自绘占位）",
        dt.get("inputType") == "date" and dt.get("hasPh") is True)
    # ⚠「聚焦时占位让位」的渲染结果**依赖窗口是不是系统前台**：后台窗口下
    # `:focus` 不匹配，占位照旧露着（2026-10-16 实测：同一份代码前台读 false、
    # 后台读 true）。所以判据拆三层，只有第一层与前台无关；渲染态只在
    # `document.hasFocus()` 为真时才严格要求 —— 不玩"读不到就当过"。
    _df = dt.get("docHasFocus")
    chk("空值不给「清空」、focus 给到输入框、聚焦让位的规则在、失焦后露出"
        "（hidden=%s active=%s rule=%r blur=%s 前台=%s）"
        % (dt.get("clearHiddenWhenEmpty"), dt.get("activeIsInput"),
           dt.get("focusRule"), dt.get("phVisibleWhenEmpty"), _df),
        dt.get("clearHiddenWhenEmpty") is True
        and dt.get("activeIsInput") is True
        and bool(dt.get("focusRule"))
        and dt.get("phVisibleWhenEmpty") is True
        and (dt.get("phVisibleWhileFocused") is False if _df else True))
    chk("选了值 → 自绘占位让位（hav=%s ph=%s）"
        % (dt.get("hav"), dt.get("phHiddenWhenHas")),
        dt.get("hav") is True and dt.get("phHiddenWhenHas") is True)
    chk("点保存：弹窗关闭、库里真写上（%s / %s）" % (dt.get("closed"), dt.get("saved")),
        dt.get("closed") is True and dt.get("saved") == "2027-03-15")
    chk("再打开回填旧值、这回「清空」露头（%s / %s）"
        % (dt.get("reopenVal"), dt.get("clearShownWhenHas")),
        dt.get("reopenVal") == "2027-03-15" and dt.get("clearShownWhenHas") is True)
    chk("点「清空日期」：库里真清掉（%r）" % (dt.get("afterClear"),),
        (dt.get("afterClear") or "") == "")
    chk("日期弹窗样本已收拾干净", dt.get("cleaned") is True)
    chk("日期弹窗全程无未捕获报错（实得 %r）" % (dt.get("errNew"),),
        dt.get("errNew") is None)

    print()
    print("GUI smoke:", "OK" if ok else "FAILED")
    # 分段耗时一律打出来（不管绿红）：那阵子多段开始贴着预算超时，得先分清
    # 是"某段本来就慢"还是"这台机器整体变慢"。diag.js 的 phase() 记的。
    tm = rep.get("timings") or {}
    print("耗时(秒):", " ".join("%s=%s" % (k, v) for k, v in tm.items()))
    if not ok:
        print("stages:", rep.get("stages"), "failed:", rep.get("failed"))
        print("m4:", m4)
        print("fix:", fx)
        print("settings:", st)
        print("multi:", mu, "dbFile:", dbf)
        print("clients:", cl)
        print("vendor:", vr)
    logf.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
