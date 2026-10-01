"""M3 财务回归测试：口径、账龄、环节挂载、三种导出格式。

跑法：
    <venv>\\Scripts\\python.exe tests\\smoke_finance.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db import Db  # noqa: E402
from finance import Finance, money  # noqa: E402
from models import Board  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="board_fin_")
    path = os.path.join(tmp, "board.sqlite")
    db = Db(path)
    db.open()
    board = Board(db)
    fin = Finance(db)

    # ---- 准备数据 ----
    cid = fin.client_save(None, {"name": "ACME 影业", "contact": "王工",
                                 "settlement_cycle": "月结 30"})
    pid = board.add_project("测试项目 A", "commercial", cid)
    stage = board.add_node(pid, None, "第一集")
    sub = board.add_node(pid, stage, "合成")

    today = date.today()
    fin.add({"kind": "contract", "project_id": pid, "amount": 100000,
             "date": (today - timedelta(days=45)).isoformat(), "status": "有效"})
    fin.add({"kind": "invoice", "project_id": pid, "amount": 60000,
             "date": (today - timedelta(days=20)).isoformat(), "status": "已开",
             "invoice_no": "INV-001"})
    fin.add({"kind": "payment", "project_id": pid, "amount": 40000,
             "date": (today - timedelta(days=10)).isoformat(), "status": "已收"})
    # 挂在二级子任务上的到账（分批款）——验证深层节点也能挂
    fin.add({"kind": "payment", "project_id": pid, "node_id": sub, "amount": 10000,
             "date": (today - timedelta(days=3)).isoformat(), "status": "已收"})
    # 待收，日期靠后
    fin.add({"kind": "payment", "project_id": pid, "amount": 50000,
             "date": (today + timedelta(days=15)).isoformat(), "status": "待收"})
    # 未开票部分应能被算出来：合同 100000 - 已开 60000 = 40000

    # ---- 口径 ----
    d = fin.data()
    c = d["cards"]
    assert c["contract"] == 100000, c
    assert c["received"] == 50000, c            # 40000 + 10000（含环节那笔）
    assert c["pending"] == 50000, c             # 100000 - 50000
    assert c["invoiced"] == 60000, c
    assert c["uninvoiced"] == 40000, c
    assert c["rate"] == 50.0, c

    # ---- 外包支出：付给外协的钱要从收入里扣掉，但只有「已付」才扣 ----
    fin.add({"kind": "outsource", "project_id": pid, "amount": 30000,
             "date": today.isoformat(), "status": "已付", "note": "给外协"})
    fin.add({"kind": "outsource", "project_id": pid, "amount": 7000,
             "date": today.isoformat(), "status": "应付", "note": "还没付"})
    d2 = fin.data()
    c2 = d2["cards"]
    assert c2["outsource"] == 30000, c2               # 应付那笔不算
    assert c2["net"] == 20000, c2                     # 已收 50000 − 外包 30000
    assert c2["received"] == 50000, c2                # 外包不该动「已收」
    assert c2["pending"] == 50000, c2                 # 也不该动「待收」
    pj0 = d2["projects"][0]
    assert pj0["outsource"] == 30000 and pj0["net"] == 20000, pj0
    assert d2["client_summary"][0]["net"] == 20000, d2["client_summary"]
    # 改成「应付」→ 不再冲减；改回「已付」→ 又扣回来
    last_out = [r for r in d2["records"] if r["kind"] == "outsource"
                and r["status"] == "已付"][0]
    fin.update(last_out["id"], {"kind": "outsource", "project_id": pid,
                                "amount": 30000, "status": "应付",
                                "date": today.isoformat()})
    assert fin.data()["cards"]["net"] == 50000, "「应付」不该冲减实际收入"
    fin.update(last_out["id"], {"kind": "outsource", "project_id": pid,
                                "amount": 30000, "status": "已付",
                                "date": today.isoformat()})
    assert fin.data()["cards"]["net"] == 20000

    # ---- 环节挂载 ----
    proj = d["projects"][0]
    assert proj["node_records"] == 1, proj
    node_rows = [r for r in d["records"] if r["node_id"]]
    assert len(node_rows) == 1 and node_rows[0]["target"] == "合成", node_rows

    # ---- 状态不计入 ----
    rid = fin.add({"kind": "payment", "project_id": pid, "amount": 99999,
                   "status": "待收", "date": today.isoformat()})
    assert fin.data()["cards"]["received"] == 50000, "待收不应计入已收"
    fin.update(rid, {"kind": "payment", "project_id": pid, "amount": 99999,
                     "status": "已收", "date": today.isoformat()})
    assert fin.data()["cards"]["received"] == 149999, "改成已收后应计入"
    fin.delete(rid)
    assert fin.data()["cards"]["received"] == 50000, "删除后应回退"

    # ---- 作废合同不计入 ----
    void = fin.add({"kind": "contract", "project_id": pid, "amount": 5000,
                    "status": "作废", "date": today.isoformat()})
    assert fin.data()["cards"]["contract"] == 100000, "作废合同不该计入"
    fin.delete(void)

    # ---- 账龄：合同日期 45 天前 -> 落在 31-60 档 ----
    ag = fin.data()["aging"]
    hit = [b for b in ag["buckets"] if b["label"] == "31-60 天"]
    assert hit and hit[0]["amount"] == 50000, ag
    assert ag["total"] == 50000, ag

    # ---- 客户汇总 ----
    cs = fin.data()["client_summary"]
    assert cs and cs[0]["client"] == "ACME 影业" and cs[0]["pending"] == 50000, cs

    # ---- 局部更新：只给一个字段，别的字段不能被顺手清空 ----
    # 以前 update() 一律 payload.get(k)，取不到就写空回去 —— 这里会抹掉发票号、
    # 把税率/环节清成空。前端表单每次提交都带全字段所以看不出来，只有别的调用方才踩。
    inv0 = [r for r in fin.data()["records"] if r["kind"] == "invoice"][0]
    assert inv0["invoice_no"] == "INV-001" and inv0["amount"] == 60000, inv0
    fin.update(inv0["id"], {"note": "只改备注"})
    inv1 = [r for r in fin.data()["records"] if r["id"] == inv0["id"]][0]
    assert inv1["invoice_no"] == "INV-001", inv1     # 发票号不该被抹掉
    assert inv1["amount"] == 60000, inv1             # 金额不该被清成 0
    assert inv1["kind"] == "invoice" and inv1["status"] == "已开", inv1
    assert inv1["node_id"] == inv0["node_id"], inv1  # 挂靠环节不该被摘掉
    assert inv1["note"] == "只改备注", inv1
    assert fin.data()["cards"]["invoiced"] == 60000  # 口径不受影响
    fin.update(inv0["id"], {"note": ""})

    # ---- 年份 / 币种筛选 ----
    assert fin.data(year=today.year)["cards"]["contract"] == 100000
    assert fin.data(currency="USD")["cards"]["contract"] == 0
    usd = fin.add({"kind": "contract", "project_id": pid, "amount": 2000,
                   "currency": "USD", "status": "有效", "date": today.isoformat()})
    du = fin.data(currency="USD")
    assert du["cards"]["contract"] == 2000, du["cards"]
    assert "USD" in du["currencies"] and du["other_currency"] >= 1, du
    fin.delete(usd)

    # ---- 导出 ----
    for fmt, ext in (("csv", "csv"), ("json", "json"), ("xlsx", "xlsx")):
        p = fin.export(fmt)
        assert p.endswith("." + ext), p
        assert os.path.getsize(p) > 0, p
    xlsx = [f for f in os.listdir(os.path.dirname(p)) if f.endswith(".xlsx")][-1]
    from openpyxl import load_workbook
    wb = load_workbook(os.path.join(os.path.dirname(p), xlsx))
    assert wb.sheetnames == ["款项明细", "项目汇总", "客户汇总", "应收账龄"], wb.sheetnames
    assert wb["款项明细"].max_row >= 6, wb["款项明细"].max_row
    # 看板上不展示开票口径，但导出里要留着（数据完整），外包/实际收入两列也在
    head2 = [c.value for c in wb["项目汇总"][1]]
    assert head2 == ["项目", "客户", "合同额", "已收", "外包支出", "实际收入", "待收",
                     "已开票", "未开票", "合同日期", "最近到账", "笔数", "挂在环节"], head2
    row2 = [c.value for c in wb["项目汇总"][2]]
    assert row2[4] == 30000 and row2[5] == 20000, row2      # 外包支出 / 实际收入
    head3 = [c.value for c in wb["客户汇总"][1]]
    assert head3 == ["客户", "项目数", "合同额", "已收", "外包支出", "实际收入", "待收",
                     "收款率%", "最早合同日"], head3

    # ---- 客户删除保护 ----
    try:
        fin.client_delete(cid)
        raise AssertionError("客户还有项目时不该允许删除")
    except ValueError:
        pass

    # ---- 参数校验 ----
    for bad in ({"kind": "payment", "project_id": pid, "amount": 0},
                {"kind": "payment", "project_id": 0, "amount": 100},
                {"kind": "unknown", "project_id": pid, "amount": 100}):
        try:
            fin.add(bad)
            raise AssertionError(f"应当被拒绝：{bad}")
        except ValueError:
            pass

    out_dir = fin.export_dir()
    db.close()
    db2 = Db(path)
    db2.close()
    print("smoke finance ok · 导出目录:", out_dir)
    print("  cards:", money(c["contract"]), "contract /", money(c["received"]), "received")
    print("  aging:", [(b["label"], b["amount"]) for b in ag["buckets"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
