"""新建项目时顺带登记款项（合同额 / 首款）的回归测试。

重点验证两件事：
  1. 金额文本的解析（千分位、「元」后缀）和**填错就整体拒绝**——
     宁可不让建项目，也不能建出一个"以为记上了其实没记"的空项目。
  2. 建完之后汇总口径对得上（合同额 / 已收 / 待收 / 客户归属）。

跑法：
    <venv>\\Scripts\\python.exe tests\\smoke_project_money.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from api import Api  # noqa: E402
from db import Db  # noqa: E402
from finance import Finance, parse_money  # noqa: E402
from models import Board  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="board_pm_")
    db = Db(os.path.join(tmp, "board.sqlite"))
    db.open()
    board = Board(db)
    fin = Finance(db)
    api = Api(board, db, {})

    def n_projects() -> int:
        return db.one("SELECT COUNT(*) AS n FROM projects")["n"]

    cid = fin.client_save(None, {"name": "ACME 影业", "settlement_cycle": "月结 30"})

    # ---- 1. 客户 + 合同额 ----
    r1 = api.add_project("项目A", "commercial", cid, "", {"contract": "120000"})
    assert r1["ok"], r1
    assert r1["money"] == ["contract"], r1
    got = db.one("SELECT client_id, title FROM projects WHERE id=?", r1["id"])
    assert got["client_id"] == cid and got["title"] == "项目A", got

    # ---- 2. 合同额 + 首款；金额写法要宽容（千分位 / 带「元」）----
    r2 = api.add_project("项目B", "commercial", cid, "",
                         {"contract": "80,000", "paid": "30000 元", "currency": "CNY",
                          "tax_rate": "6", "date": "2026-03-01"})
    assert r2["ok"], r2
    assert r2["money"] == ["contract", "payment"], r2

    # ---- 3. 一分钱不填也照常建项目 ----
    r3 = api.add_project("项目C")
    assert r3["ok"] and r3["money"] == [], r3

    # ---- 4. 金额写错 → 整体拒绝，且不留半成品项目 ----
    n0 = n_projects()
    for bad_val in ("1万", "abc", "-5"):
        bad = api.add_project("项目D", "commercial", cid, "", {"contract": bad_val})
        assert not bad["ok"], bad
        assert ("不是数字" in bad["msg"]) or ("负数" in bad["msg"]), bad
    # 首款写错同样整体拒绝（合同额是对的也不能建）
    bad2 = api.add_project("项目E", "commercial", cid, "",
                           {"contract": "100", "paid": "面议"})
    assert not bad2["ok"], bad2
    assert n_projects() == n0, f"拒绝后不该留下项目：{n0} -> {n_projects()}"
    orph = db.one("SELECT COUNT(*) AS n FROM finance WHERE project_id NOT IN"
                  " (SELECT id FROM projects)")["n"]
    assert orph == 0, f"有 {orph} 笔款项挂在已不存在的项目上"

    # ---- 5. 汇总口径 ----
    d = fin.data()
    c = d["cards"]
    assert c["contract"] == 200000, c            # 120000 + 80000
    assert c["received"] == 30000, c
    assert c["pending"] == 170000, c
    assert c["uninvoiced"] == 200000, c          # 一笔票都没开

    pay = [x for x in d["records"] if x["kind"] == "payment"]
    assert len(pay) == 1, pay
    assert pay[0]["status"] == "已收" and pay[0]["note"] == "首款", pay
    assert pay[0]["date"] == "2026-03-01" and pay[0]["currency"] == "CNY", pay

    con = [x for x in d["records"] if x["kind"] == "contract" and x["project_id"] == r2["id"]]
    assert len(con) == 1 and con[0]["status"] == "有效", con
    assert con[0]["tax_rate"] == 6, con          # "6" 要落成数字 6

    pb = [p for p in d["projects"] if p["project_id"] == r2["id"]][0]
    assert (pb["contract"], pb["received"], pb["pending"]) == (80000, 30000, 50000), pb

    cs = [x for x in d["client_summary"] if x["client"] == "ACME 影业"]
    assert cs and cs[0]["projects"] == 2 and cs[0]["contract"] == 200000, cs
    # 没填客户、也没款项的项目（项目C）不该被塞进客户汇总
    assert [x["client"] for x in d["client_summary"]] == ["ACME 影业"], d["client_summary"]

    # ---- 6. parse_money 边界 ----
    assert parse_money("") == 0 and parse_money(None) == 0 and parse_money("   ") == 0
    assert parse_money("1,234.5") == 1234.5
    assert parse_money("6000元") == 6000
    assert parse_money("0") == 0
    for bogus in ("1万", "abc", "面议"):
        try:
            parse_money(bogus)
            raise AssertionError(f"应当报错：{bogus}")
        except ValueError:
            pass

    db.close()
    print("smoke_project_money: OK（客户关联 / 金额解析 / 填错回滚 / 汇总口径 全通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
