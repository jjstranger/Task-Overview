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
    if nosandbox:
        # 模拟"看门狗降级过一次"的状态：库里记着 nosandbox=1
        db.set_setting("nosandbox", "1")
    # 提醒巡检在启动 60s 后首次触发，而 do_front() 会把窗口**强行拉回置顶** ——
    # 那一脚正好容易落在「置顶开关」自检上，表现为偶发 FAIL（不是置顶功能坏了）。
    # 冒烟里把提醒关掉：notify_enabled=0 关到期告警，remind_level=1 让打卡只发通知不拉前台。
    db.set_setting("notify_enabled", "0")
    db.set_setting("remind_level", "1")

    cid = fin.client_save(None, {"name": "冒烟客户", "settlement_cycle": "月结 30"})
    pid = board.add_project("冒烟商业项目", "commercial", cid)
    # 制作人：界面上要挨着备注显示，菜单里要能改
    board.set_field("project", pid, "artist", "冒烟制作人")
    # 这一行故意挂满徽章：今日必做 + 状态（+ 客户 / 制作人）——
    # 用来验「点**状态**徽章才出状态菜单」（裸 .badge 会取到「今日必做」那个，2026-09-30 修的）
    board.set_field("project", pid, "priority", 1)
    # 到期小标要有活样本：3 天后到期
    board.set_field("project", pid, "deadline", (date.today() + timedelta(days=3)).isoformat())
    stage = board.add_node(pid, None, "第一环节")
    sub = board.add_node(pid, stage, "子任务")
    board.set_field("node", sub, "note", "备注示例")
    board.set_field("node", sub, "artist", "冒烟小工")
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
    fin.add({"kind": "outsource", "project_id": pid, "amount": 20000,
             "date": today.isoformat(), "status": "已付", "note": "给外协"})
    fin.add({"kind": "outsource", "project_id": pid, "amount": 5000,
             "date": today.isoformat(), "status": "应付", "note": "还没付"})

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
    chk("导出菜单 = Excel / CSV / JSON / 打开导出目录",
        [x for x in (fin_.get("exportMenu") or []) if x] ==
        ["导出 Excel（四张表）", "导出 CSV", "导出 JSON", "打开导出目录"])
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
    chk("口径说明已渲染",
        "待收 = 合同额" in (fin_.get("tip") or "")
        and "实际收入 = 已收 − 外包支出" in (fin_.get("tip") or ""))
    # 制作人：跟备注挨着显示，行菜单里能改
    chk("制作人显示在项目行里",
        ar.get("tags") == 1 and "冒烟制作人" in (ar.get("text") or ""))
    chk("行菜单里有「编辑制作人」",
        any("编辑制作人" in str(x) for x in (ar.get("menu") or [])))
    # 新建项目弹窗里的款项区（M3 追加）
    chk("新建项目弹窗 7 个字段", neww.get("flds") == 7)
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
    chk("实时预览报了「将创建」", nd.get("prevOk") is True)
    chk("预览里六个名字都在", nd.get("prevAll") is True)
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
            and nm.get("whoInvOk") is True and not nm.get("afterWhoClr"))

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
        gp.get("addOpen") is True and gp.get("addFlds") == 7
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
    chk("多选：再点第二条 → 两条都是勾的，第一条没有延迟",
        (mu.get("pickClick2") or {}).get("ok") is True)
    chk("多选：再点第一条取消 → 复选框当场灭、第二条不受影响",
        (mu.get("pickClick3") or {}).get("ok") is True)
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
    # 「全选本层」按用户要求撤掉，改成点父级自带级联。
    # 断言方式：把批量条上实际有的按钮采回来 —— 既确认没把"全选"加回来，也能发现少了谁。
    _bb = mu.get("bulkBtns") or []
    chk("多选：批量条就这四颗按钮、没有「全选本层」（实际=%s）" % (_bb,),
        len(_bb) == 4
        and not any("全选" in t for t in _bb)
        and all(any(k in t for t in _bb) for k in ("状态", "制作人", "清空", "退出")))
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

    # ---- 客户删除（财务页「客户」面板里的删除入口） ----
    cl = rep.get("clients") or {}
    chk("客户删除：面板刚打开时删除按钮是收着的（新建态，防误删上次编辑那个）",
        cl.get("opened") is True and cl.get("delHiddenFresh") is True)
    chk("客户删除：点开某个客户后删除按钮露出来、表单填上",
        cl.get("rowFound") is True and cl.get("delShownEdit") is True
        and cl.get("nameFilled") is True)
    chk("客户删除：真点删除后客户从列表消失、表单清空、不报错（alert=%s）"
        % (cl.get("delAlert"),), cl.get("delOk") is True)
    chk("客户删除：还有项目挂靠的客户删不掉，并把原因说出来（msg=%s）"
        % (cl.get("busyMsg"),),
        cl.get("busyBlocked") is True and cl.get("stillRefused") is True)

    print()
    print("GUI smoke:", "OK" if ok else "FAILED")
    if not ok:
        print("stages:", rep.get("stages"), "failed:", rep.get("failed"))
        print("m4:", m4)
        print("fix:", fx)
        print("settings:", st)
        print("multi:", mu, "dbFile:", dbf)
        print("clients:", cl)
    logf.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
