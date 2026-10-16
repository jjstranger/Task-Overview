"""「结款 = 核销待收」的 db 层回归（2026-10-16 改口径）。

旧口径（2026-10-13 ~ 10-16）是"把合同额改成已收累计、多余的合同额记录合并删除"。
它能把待收归零，但**把损失从账面上抹掉了** —— 合同 15 万、实收 12 万，结完款
账上变成"合同额 12 万、收款率 100%"，那 3 万去哪了查不到。用户给的两个真实场景
是 ① 银行手续费 / 税差这类小额差；② 尾款确定收不回但项目要收尾 —— 两者要的
都是"**把待收核掉**"，不是"篡改合同额"。

新口径要点：
1. **合同额一条不动**：签了多少就是多少，收款率因此保持诚实
   （15 万收 12 万 = 80%，而不是把分母改小后的 100%）。
2. 待收 = 合同额 − 已收 − 已核销。
3. 结款 = 让「核销」补到刚好盖住待收（max(0, 合同额 − 已收)），由**它自己那条**
   记录（备注 `结款核销`）承载：不够 → 改大 / 新记一条；不需要了 → 作废它。
4. **幂等**：已经平了就一个字节都不写（连点两次不会多出记录）。
5. 用户手记的核销**不碰**（备注原样保留），但算进"已经核销了多少"。
6. 一笔「已收」都没有 → 拒绝（那等于把整份合同一次性核销掉）。
7. 累计**不受界面年份筛选影响** —— 2025 年到的那笔，2026 年点结款也得算进去
   （所以 payoff 是直接查库，不是拿 data() 的筛后结果）。
8. preview 是**只读**的：连调它不会写库。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "app"))

from db import Db            # noqa: E402
from finance import Finance  # noqa: E402
from models import Board     # noqa: E402

FAILS = []


def chk(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def main() -> int:
    tmp = os.path.join(tempfile.mkdtemp(prefix="smoke_payoff_"), "b.sqlite")
    db = Db(tmp)
    db.open()
    board = Board(db)
    fin = Finance(db)

    def n_fin(pid, kind=""):
        sql = "SELECT COUNT(*) AS c FROM finance WHERE project_id=?"
        args = [pid]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return db.one(sql, *args)["c"]

    def contract_sum(pid):
        """库里该项目所有**非作废**合同额之和 —— 就是看板上显示的那个数。"""
        rows = db.q("SELECT amount, status FROM finance"
                    " WHERE project_id=? AND kind='contract'", pid)
        return round(sum(float(r["amount"] or 0) for r in rows
                         if r["status"] != "作废"), 2)

    def woff_sum(pid):
        """该项目**生效中**的核销合计（作废的不算，跟 _counted 一个口径）。"""
        rows = db.q("SELECT amount, status FROM finance"
                    " WHERE project_id=? AND kind='writeoff'", pid)
        return round(sum(float(r["amount"] or 0) for r in rows
                         if r["status"] == "已核销"), 2)

    def auto_row(pid):
        """结款生成的那条核销记录（备注是「结款核销」）。"""
        return db.one("SELECT id, amount, status FROM finance"
                      " WHERE project_id=? AND kind='writeoff' AND note='结款核销'"
                      " ORDER BY id LIMIT 1", pid)

    def row_of(pid):
        return [p for p in fin.data()["projects"] if p["project_id"] == pid][0]

    # ---- 1. preview 口径：合同额 / 已收 / 待收 ----
    pid = board.add_project("结款测试", "commercial", None)
    fin.add({"kind": "contract", "project_id": pid, "amount": 100000,
             "date": "2026-03-01", "status": "有效"})
    fin.add({"kind": "payment", "project_id": pid, "amount": 30000,
             "date": "2026-03-10", "status": "已收"})
    p_wait = fin.add({"kind": "payment", "project_id": pid, "amount": 20000,
                      "date": "2026-04-10", "status": "待收"})      # 没到手，不算

    pv = fin.payoff_preview(pid)
    chk("preview：合同额 100000、已收 30000（待收那笔不算）",
        pv["contract"] == 100000 and pv["received"] == 30000)
    chk("preview：待收 = 100000 − 30000 = 70000，该核销也是 70000",
        pv["pending"] == 70000 and pv["target"] == 70000)
    chk("preview：一条核销记录都还没有",
        pv["writeoff"] == 0 and pv["counts"]["writeoffs"] == 0)
    chk("preview：列了 2 条到账明细、其中 1 条计入",
        pv["counts"]["payments"] == 2 and pv["counts"]["counted"] == 1)
    chk("preview：明细里标出了哪条计入",
        [x["counted"] for x in pv["payments"]] == [True, False])

    # ---- 2. preview 是只读的 ----
    before = n_fin(pid)
    fin.payoff_preview(pid)
    chk("preview 不写库（记录数没变）", n_fin(pid) == before)

    # ---- 3. 跨年份的到账也要算（不跟着界面筛选走） ----
    fin.add({"kind": "payment", "project_id": pid, "amount": 5000,
             "date": "2025-12-31", "status": "已收"})
    pv = fin.payoff_preview(pid)
    chk("preview 把上一年的到账也算进来（30000 + 5000 = 35000）",
        pv["received"] == 35000 and pv["pending"] == 65000)
    # 对照：按 2026 年筛出来的只有当年那部分 —— 证明两套口径确实不同
    d26 = fin.data(year=2026)
    chk("对照：按年份筛的看板只算当年（30000，不含上一年那笔 5000）",
        d26["cards"]["received"] == 30000)

    # 那笔「待收」的到账**故意留着不作废** —— 它是这一轮的关键样本：
    # 客户说要分三期付、后来没付齐，项目里就会留下这种记录。
    # 2026-10-16 之前「已结清」还要求"没有任何 payment 记录挂在「待收」"，
    # 于是核销把待收冲成 0 之后项目仍标不出已结清 —— 那条判据撤掉了。
    chk("结款前：待收 65000、还挂着「待收」的到账 → 不算已结清",
        row_of(pid)["pending"] == 65000 and row_of(pid)["settled"] is False)

    # ---- 4. 结款：核销 65000，合同额不动 ----
    # ⚠ 是 65000 不是 70000 —— 第 3 步那笔 2025 年的 5000 也算「已收」，
    #   待收 = 100000 − 35000。跨年份那笔正是这个功能的要点。
    n_before = n_fin(pid)
    r = fin.payoff_apply(pid)
    chk("结款成功且真的改了库", r["ok"] is True and r["changed"] is True)
    chk("**合同额一条没动**（还是 100000 / 1 条）",
        contract_sum(pid) == 100000 and n_fin(pid, "contract") == 1)
    chk("新增了一条核销 = 65000（待收刚好被盖住）",
        woff_sum(pid) == 65000 and n_fin(pid, "writeoff") == 1)
    chk("只多了一条记录（总记录数 +1，不是把合同额改小）",
        n_fin(pid) == n_before + 1)
    chk("返回体里合同额原样带回来（100000）、本次核销 65000",
        r["contract"] == 100000 and r["writeoff_new"] == 65000
        and r["pending"] == 0)

    d = fin.data()
    row = row_of(pid)
    chk("看板：待收归零、合同额不变、已收不变",
        d["cards"]["pending"] == 0 and row["pending"] == 0
        and d["cards"]["contract"] == 100000 and d["cards"]["received"] == 35000)
    chk("**收款率诚实**（35000 / 100000 = 35%，没被核销改小分母）",
        abs(d["cards"]["rate"] - 35.0) < 0.05)
    chk("卡片里单列出已核销 65000", d["cards"]["writeoff"] == 65000)
    chk("项目行也带上了已核销 65000", row["writeoff"] == 65000)
    chk("项目算「已结清」——**哪怕还挂着一条「待收」的到账记录**（2026-10-16 收敛判据）",
        row["settled"] is True and n_fin(pid, "payment") == 3
        and db.one("SELECT status FROM finance WHERE id=?", p_wait)["status"] == "待收")

    # ---- 5. 幂等：再点一次一个字节都不写 ----
    n_after = n_fin(pid)
    r2 = fin.payoff_apply(pid)
    chk("再点一次：changed=False，记录数和金额都没变",
        r2["changed"] is False and n_fin(pid) == n_after
        and woff_sum(pid) == 65000 and n_fin(pid, "writeoff") == 1)

    # ---- 6. 多条合同额：**一条都不删**（这就是这轮改口径的意义） ----
    pid2 = board.add_project("多条合同额", "commercial", None)
    c1 = fin.add({"kind": "contract", "project_id": pid2, "amount": 100000,
                  "date": "2026-01-05", "status": "有效"})
    c2 = fin.add({"kind": "contract", "project_id": pid2, "amount": 50000,
                  "date": "2026-02-05", "status": "有效"})      # 增补合同
    fin.add({"kind": "payment", "project_id": pid2, "amount": 120000,
             "date": "2026-05-01", "status": "已收"})
    r6 = fin.payoff_apply(pid2)
    chk("**两条合同额一条都没删**（原合同 10 万 + 增补 5 万都还在）",
        n_fin(pid2, "contract") == 2 and contract_sum(pid2) == 150000)
    chk("两条的 id 都还是原来那两个（不是删了重建）",
        {int(r["id"]) for r in db.q(
            "SELECT id FROM finance WHERE project_id=? AND kind='contract'", pid2)}
        == {int(c1), int(c2)})
    chk("核销 = 150000 − 120000 = 30000", r6["writeoff_new"] == 30000
        and woff_sum(pid2) == 30000)
    row2 = row_of(pid2)
    chk("待收归零，而合同额仍是 150000、已收 120000（损失看得见）",
        row2["pending"] == 0 and row2["contract"] == 150000
        and row2["received"] == 120000 and row2["writeoff"] == 30000)

    # ---- 7. 结款后又收到钱 → 核销自动调小（而不是删记录） ----
    a1 = auto_row(pid2)
    fin.add({"kind": "payment", "project_id": pid2, "amount": 20000,
             "date": "2026-06-01", "status": "已收"})
    r7 = fin.payoff_apply(pid2)
    a2 = auto_row(pid2)
    chk("又收到 2 万后再结款：核销从 30000 调成 10000",
        woff_sum(pid2) == 10000 and float(a2["amount"]) == 10000)
    chk("调的是**同一条**（id 没变，记录数没多）",
        int(a2["id"]) == int(a1["id"]) and n_fin(pid2, "writeoff") == 1)
    chk("待收还是 0", row_of(pid2)["pending"] == 0)

    # ---- 8. 收齐了 → 核销记录作废（留痕，不是删除） ----
    fin.add({"kind": "payment", "project_id": pid2, "amount": 10000,
             "date": "2026-06-02", "status": "已收"})
    r8 = fin.payoff_apply(pid2)
    chk("收齐后再结款：报告 cleared、生效核销归零",
        r8["cleared"] is True and woff_sum(pid2) == 0)
    chk("那条核销是**作废留痕**，不是被删掉",
        n_fin(pid2, "writeoff") == 1
        and db.one("SELECT status FROM finance WHERE id=?", a1["id"])["status"] == "作废")
    chk("合同额依旧 150000（始终没动过）", contract_sum(pid2) == 150000)

    # ---- 9. 用户手记的核销：不碰，但算进"已核销多少" ----
    #     场景①：银行手续费这类小额差，用户自己记一条就够了
    pid3 = board.add_project("手续费差额", "commercial", None)
    fin.add({"kind": "contract", "project_id": pid3, "amount": 100000,
             "date": "2026-01-01", "status": "有效"})
    fin.add({"kind": "payment", "project_id": pid3, "amount": 99900,
             "date": "2026-01-20", "status": "已收"})
    m3 = fin.add({"kind": "writeoff", "project_id": pid3, "amount": 100,
                   "date": "2026-01-20", "status": "已核销", "note": "银行手续费"})
    pv3 = fin.payoff_preview(pid3)
    chk("手记核销 100 就盖住了待收（100000 − 99900）",
        pv3["pending"] == 0 and pv3["writeoff_manual"] == 100
        and pv3["writeoff_auto"] == 0)
    r9 = fin.payoff_apply(pid3)
    chk("结款不用动（手记的已经够了）", r9["changed"] is False)
    chk("手记那条的备注原样保留（没被结款覆盖）",
        db.one("SELECT note FROM finance WHERE id=?", m3)["note"] == "银行手续费")

    #     场景②：尾款确定收不回，手记的那条还在，结款只补差额
    pid4 = board.add_project("尾款收不回", "commercial", None)
    fin.add({"kind": "contract", "project_id": pid4, "amount": 150000,
             "date": "2026-02-01", "status": "有效"})
    fin.add({"kind": "payment", "project_id": pid4, "amount": 120000,
             "date": "2026-02-20", "status": "已收"})
    m4 = fin.add({"kind": "writeoff", "project_id": pid4, "amount": 100,
                   "date": "2026-02-20", "status": "已核销", "note": "银行手续费"})
    r10 = fin.payoff_apply(pid4)
    chk("结款只补差额 29900（不是 30000），合计核销 30000",
        r10["writeoff_new"] == 29900 and woff_sum(pid4) == 30000)
    chk("手记那条一分没动（100 / 备注都在）",
        float(db.one("SELECT amount FROM finance WHERE id=?", m4)["amount"]) == 100
        and db.one("SELECT note FROM finance WHERE id=?", m4)["note"] == "银行手续费")
    chk("项目行：合同额 150000 / 已收 120000 / 核销 30000 / 待收 0",
        (lambda x: (x["contract"], x["received"], x["writeoff"], x["pending"]))
        (row_of(pid4)) == (150000, 120000, 30000, 0))

    # ---- 10. 一笔「已收」都没有 → 拒绝（不能把整份合同默默核销掉） ----
    pid5 = board.add_project("没收到钱", "commercial", None)
    fin.add({"kind": "contract", "project_id": pid5, "amount": 60000,
             "date": "2026-07-01", "status": "有效"})
    fin.add({"kind": "payment", "project_id": pid5, "amount": 30000,
             "date": "2026-07-02", "status": "待收"})
    err = ""
    try:
        fin.payoff_apply(pid5)
    except ValueError as exc:
        err = str(exc)
    chk("一笔已收都没有 → 拒绝执行并说明原因（%s）" % err[:20],
        bool(err) and "已收" in err)
    chk("拒绝时一个字节都没写（合同额原样、没核销、记录数没多）",
        contract_sum(pid5) == 60000 and n_fin(pid5, "writeoff") == 0
        and n_fin(pid5) == 2)

    # ---- 11. 作废的合同额：不计入当前合同额，结款也不动它 ----
    pid6 = board.add_project("含作废", "commercial", None)
    fin.add({"kind": "contract", "project_id": pid6, "amount": 40000,
             "date": "2026-08-01", "status": "有效"})
    fin.add({"kind": "contract", "project_id": pid6, "amount": 99999,
             "date": "2026-08-02", "status": "作废"})
    fin.add({"kind": "payment", "project_id": pid6, "amount": 25000,
             "date": "2026-08-10", "status": "已收"})
    pv6 = fin.payoff_preview(pid6)
    chk("作废那条不计入当前合同额（40000，不是 139999）", pv6["contract"] == 40000)
    chk("preview 把 2 条合同额都列出来了（作废的也给人看见）",
        pv6["counts"]["contracts"] == 2)
    fin.payoff_apply(pid6)
    chk("结款后两条合同额原样都在（含作废那条，一条不删）",
        n_fin(pid6, "contract") == 2 and contract_sum(pid6) == 40000)
    chk("核销 = 40000 − 25000 = 15000", woff_sum(pid6) == 15000)

    # ---- 12. 核销也是款项记录：进明细、进汇总、能单独改 ----
    recs = fin.records(project_id=pid6)
    wr = [x for x in recs if x["kind"] == "writeoff"]
    chk("核销出现在流水明细里，中文名是「核销」（不是空 / 不是 writeoff）",
        len(wr) == 1 and wr[0]["kind_cn"] == "核销" and wr[0]["status"] == "已核销")
    chk("核销不带外包商（那不是它的字段）", not (wr[0].get("vendor") or ""))
    # 作废掉 → 待收自己回来（"撤销核销"就这一下）
    fin.update(int(wr[0]["id"]), {"status": "作废"})
    chk("把核销作废掉 → 待收 15000 自己回来了",
        row_of(pid6)["pending"] == 15000 and row_of(pid6)["settled"] is False)
    fin.update(int(wr[0]["id"]), {"status": "已核销"})
    chk("再改回已核销 → 待收又归零", row_of(pid6)["pending"] == 0)

    # ---- 13. 核销的默认状态 / 状态档位 ----
    rid = fin.add({"kind": "writeoff", "project_id": pid6, "amount": 1,
                   "date": "2026-08-11"})
    chk("核销不传状态时的默认值是「已核销」",
        db.one("SELECT status FROM finance WHERE id=?", rid)["status"] == "已核销")
    chk("核销的状态档位就两档（已核销 / 作废）",
        fin.meta()["status"]["writeoff"] == ["已核销", "作废"])
    fin.delete(rid)

    # ---- 14. 脏参数 ----
    err = ""
    try:
        fin.payoff_preview(0)
    except ValueError as exc:
        err = str(exc)
    chk("不传项目 → 报错而不是瞎算", bool(err))
    err = ""
    try:
        fin.payoff_preview(999999)
    except ValueError as exc:
        err = str(exc)
    chk("项目不存在 → 报错", bool(err))

    # ---- 15. 结款要写一条流水（但不计活跃：改钱不算"做工"） ----
    #     ⚠ 别直接取最后一条 —— 后面还有 fin.delete(rid)，它也会写流水。
    last = db.one("SELECT kind, weight, detail FROM activity_log"
                  " WHERE detail LIKE '%结款%' ORDER BY id DESC LIMIT 1")
    chk("结款写了流水 kind=note 且 weight=0、提到核销（%s）"
        % ((last or {})["detail"] if last else "（没找到）")[:30],
        bool(last) and last["kind"] == "note" and last["weight"] == 0
        and "核销" in last["detail"])

    db.close()
    print()
    print("payoff smoke:", "OK" if not FAILS else "FAILED  " + str(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
