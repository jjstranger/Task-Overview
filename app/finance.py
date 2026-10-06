"""财务：款项记录、汇总口径、账龄与导出（M3）。

口径（与 DESIGN.md §4.5 一致）：
    总合同额 = Σ contract(非作废)
    已开票   = Σ invoice(已开 / 已寄)          —— 只进导出表，看板上不展示
    已收     = Σ payment(已收)
    外包支出 = Σ outsource(已付)               —— 付给外协/外包的那部分，不算自己的收入
    实际收入 = 已收 − 外包支出
    待收     = 合同额 − 已收
    未开票   = 合同额 − 已开票                  —— 同上，只进导出表

款项可以挂在项目整体（node_id 为空），也可以挂在某个环节/子任务上
（比如分批付款对应到某个阶段），两种都参与汇总。
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import date, datetime

# 顺序就是「记一笔」弹窗里按钮的顺序：常用的到账、外包放前面，开票垫后
KINDS = [
    ("contract", "合同额"),
    ("payment", "到账"),
    ("outsource", "外包支出"),
    ("invoice", "开票"),
]
KIND_CN = dict(KINDS)

STATUS = {
    "contract": ["有效", "预估", "作废"],
    "payment": ["待收", "已收"],
    "outsource": ["应付", "已付", "作废"],
    "invoice": ["未开", "已开", "已寄"],
}
DEFAULT_STATUS = {"contract": "有效", "payment": "待收",
                  "outsource": "已付", "invoice": "未开"}

# 账龄分档（天）
AGE_BUCKETS = [(30, "0-30 天"), (60, "31-60 天"), (90, "61-90 天"), (10 ** 9, "90 天以上")]

FIELDS = ("project_id", "node_id", "kind", "amount", "currency",
          "tax_rate", "date", "status", "invoice_no", "note")

# 导出：格式 → 扩展名，导出内容 → 中文名。
# 内容这四项跟四张表一一对应：界面上的勾选框、xlsx 的工作表名、json 的键
# 用的都是这一份清单 —— 以后加一项内容只改这里（含 _write_xlsx/_write_json）。
EXTS = {"xlsx": "xlsx", "csv": "csv", "json": "json"}
SECTIONS = [
    ("records", "款项明细"),
    ("projects", "项目汇总"),
    ("clients", "客户汇总"),
    ("aging", "应收账龄"),
]
SECTION_CN = dict(SECTIONS)


def norm_fmt(fmt) -> str:
    """认不出来的格式一律当 xlsx（老调用只传 "xlsx"/"csv"/"json"）。"""
    f = str(fmt or "xlsx").lower().lstrip(".")
    return f if f in EXTS else "xlsx"


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe(name: str, limit: int = 24) -> str:
    """文件名里只留「各家文件系统都不会炸」的字符。

    ⚠ Windows 文件名不能有 \\ / : * ? " < > |，而项目名里带「/」很正常
    （「SANTI / 一天」这种），不洗掉的话系统另存为对话框会直接报路径非法 ——
    用户看到的是「导不出来」，且完全猜不到是项目名里的一个斜杠引起的。
    """
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" _.")
    return s[:limit]


def _s(v) -> str:
    return v if v is not None else ""


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def money(x: float) -> float:
    """金额统一保留两位，避免浮点累加出现 0.30000000000000004。"""
    return round(float(x or 0), 2)


def parse_money(v, label: str = "金额") -> float:
    """把输入框里的文本转成金额：空 = 0，非数字**报错**。

    和 _f 的区别是关键：_f 走宽容路线（脏数据当 0），内部汇总这么干没问题；
    但用户在输入框里手打「1万」「1000块」「1,200.5」时，宽容会**静默把钱变 0**，
    数据就悄悄丢了。手输入口一律走这个严格版。
    """
    s = str(v if v is not None else "").strip()
    if not s:
        return 0.0
    s = s.replace(",", "").replace("，", "").rstrip("元").strip()
    try:
        n = float(s)
    except ValueError:
        raise ValueError(f"{label}「{v}」不是数字，填纯数字即可，如 120000 或 1.2")
    if n < 0:
        raise ValueError(f"{label}不能是负数")
    return n


def _tax(v):
    """税率：留空存 NULL（不参与计算），填了就必须是数字。顺便容错「6%」。"""
    if v is None or str(v).strip() == "":
        return None
    return parse_money(str(v).strip().rstrip("%").strip(), "税率")


class Finance:
    def __init__(self, db):
        self.db = db

    # ---------- 读取 ----------

    def meta(self) -> dict:
        return {
            "kinds": [{"key": k, "name": n} for k, n in KINDS],
            "status": STATUS,
            "default_status": DEFAULT_STATUS,
        }

    def records(self, year: int | None = None, client_id: int | None = None,
                project_id: int | None = None, kind: str = "",
                date_from: str = "", date_to: str = "",
                project_ids: list[int] | None = None) -> list[dict]:
        """流水明细，带项目 / 客户 / 环节名。

        `date_from` / `date_to` 是 ISO 日期、**闭区间**（导出时用；看板上的年份
        下拉还是走 `year`）。⚠ 设了范围就把没填日期的流水排除掉 —— 它归不到
        任何一段时间里，硬塞进来会让人以为"这段时间真有这笔"。
        `project_ids` 是指定项目导出（多选）；`project_id` 仍然保留给单个用。
        """
        sql = (
            "SELECT f.*, p.title AS prj, p.client_id AS cid, c.name AS client,"
            " n.title AS node, p.category AS cat"
            " FROM finance f"
            " LEFT JOIN projects p ON p.id=f.project_id"
            " LEFT JOIN clients  c ON c.id=p.client_id"
            " LEFT JOIN nodes    n ON n.id=f.node_id"
            " WHERE 1=1"
        )
        args: list = []
        if year:
            sql += " AND substr(f.date,1,4)=?"
            args.append(str(int(year)))
        if client_id:
            sql += " AND p.client_id=?"
            args.append(int(client_id))
        if project_id:
            sql += " AND f.project_id=?"
            args.append(int(project_id))
        if kind:
            sql += " AND f.kind=?"
            args.append(kind)
        if date_from:
            sql += " AND f.date<>'' AND f.date>=?"
            args.append(str(date_from))
        if date_to:
            sql += " AND f.date<>'' AND f.date<=?"
            args.append(str(date_to))
        if project_ids:
            ids = [_int(x) for x in project_ids]
            sql += " AND f.project_id IN (" + ",".join("?" * len(ids)) + ")"
            args.extend(ids)
        sql += " ORDER BY (f.date IS NULL OR f.date=''), f.date DESC, f.id DESC"

        out = []
        for r in self.db.q(sql, tuple(args)):
            out.append({
                "id": r["id"],
                "project_id": r["project_id"],
                "node_id": r["node_id"],
                "kind": r["kind"],
                "kind_cn": KIND_CN.get(r["kind"], r["kind"]),
                "amount": _f(r["amount"]),
                "currency": _s(r["currency"]) or "CNY",
                "tax_rate": r["tax_rate"],
                "date": _s(r["date"]),
                "status": _s(r["status"]),
                "invoice_no": _s(r["invoice_no"]),
                "note": _s(r["note"]),
                "project": _s(r["prj"]),
                "node": _s(r["node"]),
                "client": _s(r["client"]),
                "client_id": r["cid"],
                "category": _s(r["cat"]),
                "target": _s(r["node"]) or "项目整体",
            })
        return out

    # 有效金额：只有状态成立才计入汇总，其余按 0 处理但仍显示在流水里
    @staticmethod
    def _counted(r: dict) -> float:
        k, st = r["kind"], r["status"]
        if k == "contract":
            return r["amount"] if st != "作废" else 0.0
        if k == "invoice":
            return r["amount"] if st in ("已开", "已寄") else 0.0
        if k == "payment":
            return r["amount"] if st == "已收" else 0.0
        if k == "outsource":
            # 只有真付出去的才扣；「应付」还没付，先不冲减实际收入
            return r["amount"] if st == "已付" else 0.0
        return 0.0

    def data(self, year: int | None = None, client_id: int | None = None,
             currency: str = "CNY") -> dict:
        """一次把所有前端要的都算出来，避免多次跨桥调用。"""
        all_rows = self.records(year=year, client_id=client_id)
        currencies = sorted({r["currency"] for r in all_rows} | {"CNY"})

        rows = [r for r in all_rows if r["currency"] == currency]

        projects = self._by_project(rows)
        clients = self._by_client(projects)
        aging = self._aging(rows, projects)

        # 年份列表：拿全量数据里的年份，保证切年份时不丢选项
        years = [
            int(r["y"]) for r in self.db.q(
                "SELECT DISTINCT substr(date,1,4) AS y FROM finance"
                " WHERE date IS NOT NULL AND date<>'' ORDER BY y DESC"
            ) if str(r["y"]).isdigit()
        ]
        this_year = date.today().year
        if this_year not in years:
            years.insert(0, this_year)

        return {
            "meta": self.meta(),
            "year": int(year or 0),
            "years": years,
            "currency": currency,
            "currencies": currencies,
            "client_id": client_id or 0,
            "clients": self.client_options(),
            "records": rows,
            "other_currency": len(all_rows) - len(rows),
            "projects": projects,
            "client_summary": clients,
            "aging": aging,
            "cards": self._cards(rows),
            "quarter": self._quarter(rows),
            "setting_export_dir": self.db.get_setting("export_dir", ""),
        }

    # ---------- 汇总 ----------

    def _accumulate(self, rows: list[dict]) -> dict:
        acc = {"contract": 0.0, "invoice": 0.0, "payment": 0.0, "outsource": 0.0,
               "pending_receivable_date": None, "last_payment_date": None,
               "contract_date": None, "count": len(rows), "with_node": 0}
        for r in rows:
            v = self._counted(r)
            acc[r["kind"]] = acc.get(r["kind"], 0.0) + v
            d = _d(r["date"])
            if r["node_id"]:
                acc["with_node"] += 1
            if r["kind"] == "contract" and r["status"] != "作废" and d:
                if not acc["contract_date"] or d < acc["contract_date"]:
                    acc["contract_date"] = d
            if r["kind"] == "payment":
                if r["status"] == "已收" and d:
                    if not acc["last_payment_date"] or d > acc["last_payment_date"]:
                        acc["last_payment_date"] = d
                if r["status"] == "待收" and d:
                    if not acc["pending_receivable_date"] or d < acc["pending_receivable_date"]:
                        acc["pending_receivable_date"] = d
        return acc

    def _cards(self, rows: list[dict]) -> dict:
        a = self._accumulate(rows)
        contract, received, invoiced = a["contract"], a["payment"], a["invoice"]
        outsource = a["outsource"]
        y, m = date.today().year, date.today().month
        month_in = month_inv = 0.0
        for r in rows:
            d = _d(r["date"])
            if not d or d.year != y or d.month != m:
                continue
            v = self._counted(r)
            if r["kind"] == "payment":
                month_in += v
            elif r["kind"] == "invoice":
                month_inv += v
        return {
            "contract": money(contract),
            "received": money(received),
            # 付给外协的那部分不是自己的收入，从已收里扣掉才是实际到手
            "outsource": money(outsource),
            "net": money(received - outsource),
            "pending": money(contract - received),
            "invoiced": money(invoiced),
            "uninvoiced": money(contract - invoiced),
            "month_received": money(month_in),
            "month_invoiced": money(month_inv),
            "rate": round(received / contract * 100, 1) if contract else 0.0,
        }

    def _quarter(self, rows: list[dict]) -> dict:
        today = date.today()
        q = (today.month - 1) // 3 + 1
        months = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}[q]
        inv = pay = out = 0.0
        for r in rows:
            d = _d(r["date"])
            if not d or d.year != today.year or not (months[0] <= d.month <= months[1]):
                continue
            v = self._counted(r)
            if r["kind"] == "invoice":
                inv += v
            elif r["kind"] == "payment":
                pay += v
            elif r["kind"] == "outsource":
                out += v
        return {"name": f"{today.year} Q{q}", "invoiced": money(inv),
                "received": money(pay), "outsource": money(out)}

    def _by_project(self, rows: list[dict]) -> list[dict]:
        groups: dict[int, list[dict]] = {}
        for r in rows:
            groups.setdefault(r["project_id"] or 0, []).append(r)

        out = []
        for pid, rs in groups.items():
            a = self._accumulate(rs)
            contract, received, invoiced = a["contract"], a["payment"], a["invoice"]
            outsource = a["outsource"]
            head = rs[0]
            terminal = all(
                x["status"] in ("已收", "作废") for x in rs if x["kind"] == "payment"
            ) and contract > 0
            out.append({
                "project_id": pid,
                "project": head["project"] or f"#{pid}",
                "client": head["client"],
                "client_id": head["client_id"],
                "category": head["category"],
                "contract": money(contract),
                "invoiced": money(invoiced),
                "received": money(received),
                "outsource": money(outsource),
                "net": money(received - outsource),
                "pending": money(contract - received),
                "uninvoiced": money(contract - invoiced),
                "last_payment": a["last_payment_date"].isoformat() if a["last_payment_date"] else "",
                "contract_date": a["contract_date"].isoformat() if a["contract_date"] else "",
                "settled": bool(terminal and contract - received <= 0.009),
                "count": len(rs),
                "node_records": a["with_node"],
                "records": sorted(rs, key=lambda x: (x["date"] or "0000", x["id"]), reverse=True),
            })
        out.sort(key=lambda x: (-x["pending"], x["project"]))
        return out

    def _by_client(self, projects: list[dict]) -> list[dict]:
        """按客户归并。**键取 client_id，不取名字** —— 名字重名的两个客户
        （比如两个"个人"）会被并成一笔，而报出来的 client_id 还是先遇到的那个；
        没挂客户的项目统一落到 client_id=0 这一档。"""
        agg: dict[int, dict] = {}
        for p in projects:
            cid = int(p["client_id"] or 0)
            c = agg.get(cid)
            if c is None:
                c = agg[cid] = {
                    "client": p["client"] or "未指定客户", "client_id": cid or None,
                    "contract": 0.0, "received": 0.0, "outsource": 0.0, "net": 0.0,
                    "pending": 0.0, "invoiced": 0.0, "uninvoiced": 0.0,
                    "projects": 0, "oldest_contract": "",
                }
            c["contract"] += p["contract"]
            c["received"] += p["received"]
            c["outsource"] += p["outsource"]
            c["net"] += p["net"]
            c["pending"] += p["pending"]
            c["invoiced"] += p["invoiced"]
            c["uninvoiced"] += p["uninvoiced"]
            c["projects"] += 1
            if p["contract_date"] and (not c["oldest_contract"]
                                       or p["contract_date"] < c["oldest_contract"]):
                c["oldest_contract"] = p["contract_date"]
        for c in agg.values():
            for k in ("contract", "received", "outsource", "net",
                      "pending", "invoiced", "uninvoiced"):
                c[k] = money(c[k])
            c["rate"] = round(c["received"] / c["contract"] * 100, 1) if c["contract"] else 0.0
        return sorted(agg.values(), key=lambda x: (-x["pending"], x["client"]))

    def _aging(self, rows: list[dict], projects: list[dict] | None = None) -> dict:
        """应收账龄：起算日取该项目最早一笔合同日期，只统计还有待收的项目。

        `projects` 由调用方传进来复用（`_by_project` 在这条链路上已经被算过一次，
        再算一遍纯属白跑；导出 xlsx 时它会被调三次）。
        """
        projects = self._by_project(rows) if projects is None else projects
        buckets = [{"label": lbl, "amount": 0.0, "items": []} for _, lbl in AGE_BUCKETS]
        today = date.today()
        for p in projects:
            if p["pending"] <= 0.009 or not p["contract_date"]:
                continue
            d = _d(p["contract_date"])
            age = (today - d).days if d else 0
            for i, (limit, _) in enumerate(AGE_BUCKETS):
                if age <= limit:
                    buckets[i]["amount"] += p["pending"]
                    buckets[i]["items"].append({
                        "project": p["project"], "client": p["client"],
                        "pending": p["pending"], "age": age,
                        "date": p["contract_date"],
                    })
                    break
        for b in buckets:
            b["amount"] = money(b["amount"])
        total = money(sum(b["amount"] for b in buckets))
        return {"buckets": buckets, "total": total}

    # ---------- 客户 ----------

    def client_options(self) -> list[dict]:
        return [
            {
                "id": r["id"], "name": r["name"], "contact": _s(r["contact"]),
                "phone": _s(r["phone"]), "email": _s(r["email"]),
                "settlement_cycle": _s(r["settlement_cycle"]),
                "currency": _s(r["default_currency"]) or "CNY", "note": _s(r["note"]),
                "projects": r["n"],
            }
            for r in self.db.q(
                "SELECT c.*, (SELECT COUNT(*) FROM projects p WHERE p.client_id=c.id) AS n"
                " FROM clients c ORDER BY c.name"
            )
        ]

    def client_save(self, cid: int | None, payload: dict) -> int:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("客户名不能为空")
        vals = (
            name,
            str(payload.get("contact") or "").strip(),
            str(payload.get("phone") or "").strip(),
            str(payload.get("email") or "").strip(),
            str(payload.get("settlement_cycle") or "").strip(),
            str(payload.get("currency") or "CNY").strip() or "CNY",
            str(payload.get("note") or "").strip(),
        )
        if cid:
            self.db.run(
                "UPDATE clients SET name=?,contact=?,phone=?,email=?,settlement_cycle=?,"
                "default_currency=?,note=? WHERE id=?",
                vals + (int(cid),),
            )
            return int(cid)
        return self.db.run(
            "INSERT INTO clients(name,contact,phone,email,settlement_cycle,"
            "default_currency,note) VALUES(?,?,?,?,?,?,?)",
            vals,
        )

    def client_find_or_create(self, name: str) -> int:
        """按名字找客户，没有就建一个（比较时忽略大小写与首尾空白）。

        新建项目时客户是个可输入的下拉框：直接敲个新名字就该能用，
        而不是先跑去财务页建客户再回来 —— 那样等于「客户填不了」。
        """
        n = str(name or "").strip()
        if not n:
            raise ValueError("客户名不能为空")
        hit = self.db.one("SELECT id FROM clients WHERE name=? ORDER BY id LIMIT 1", n)
        if hit:
            return int(hit["id"])
        # 完全同名没有，再比一遍忽略大小写/空白的（下拉框里带个空格重打很常见）。
        # SQLite 的 COLLATE NOCASE 只认 ASCII，中文名照样得在 Python 里比。
        key = n.casefold()
        for r in self.db.q("SELECT id, name FROM clients"):
            if str(r["name"] or "").strip().casefold() == key:
                return int(r["id"])
        return int(self.client_save(None, {"name": n}))

    def client_delete(self, cid: int) -> None:
        n = self.db.one("SELECT COUNT(*) AS n FROM projects WHERE client_id=?", int(cid))["n"]
        if n:
            raise ValueError(f"还有 {n} 个项目挂在这个客户上，先把项目的客户改掉")
        self.db.run("DELETE FROM clients WHERE id=?", int(cid))

    # ---------- 增删改 ----------

    def add(self, payload: dict) -> int:
        kind = str(payload.get("kind") or "payment")
        if kind not in KIND_CN:
            raise ValueError("款项类型不对")
        pid = int(payload.get("project_id") or 0)
        if not pid:
            raise ValueError("必须挂到一个项目上")
        amount = parse_money(payload.get("amount"))
        if amount <= 0:
            raise ValueError("金额要大于 0")
        d = str(payload.get("date") or "").strip() or date.today().isoformat()
        vals = (
            pid,
            int(payload["node_id"]) if payload.get("node_id") else None,
            kind,
            amount,
            str(payload.get("currency") or "CNY").strip() or "CNY",
            _tax(payload.get("tax_rate")),
            d,
            str(payload.get("status") or DEFAULT_STATUS[kind]),
            str(payload.get("invoice_no") or "").strip(),
            str(payload.get("note") or "").strip(),
        )
        rid = self.db.run(
            "INSERT INTO finance(project_id,node_id,kind,amount,currency,tax_rate,date,"
            "status,invoice_no,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
            vals,
        )
        self._log(payload, kind, amount, rid, "新增")
        return rid

    def update(self, rid: int, payload: dict) -> None:
        """改一笔款项。

        **只覆盖 payload 里真的带了的字段**，没带的沿用原值。
        前端表单每次提交都带全字段，所以看到的行为没变；但"只改备注"这种
        局部调用不会再顺手把税率 / 发票号 / 挂靠环节清成空
        （以前一律 `payload.get(k)`，取不到就写空回去，是静默丢数据）。
        """
        cur = self.db.one("SELECT * FROM finance WHERE id=?", int(rid))
        if not cur:
            raise ValueError("这笔记录不存在了")

        def keep(key, old):
            """payload 里没有这个键（或显式给了 null）就用原值。"""
            v = payload.get(key)
            return old if v is None else v

        kind = str(keep("kind", cur["kind"]) or cur["kind"])
        if kind not in KIND_CN:
            raise ValueError("款项类型不对")
        amount = parse_money(keep("amount", cur["amount"]))
        if amount <= 0:
            raise ValueError("金额要大于 0")
        node_id = keep("node_id", cur["node_id"])
        self.db.run(
            "UPDATE finance SET project_id=?,node_id=?,kind=?,amount=?,currency=?,"
            "tax_rate=?,date=?,status=?,invoice_no=?,note=? WHERE id=?",
            (
                int(keep("project_id", cur["project_id"]) or cur["project_id"]),
                int(node_id) if node_id else None,
                kind,
                amount,
                str(keep("currency", cur["currency"]) or cur["currency"] or "CNY"),
                _tax(keep("tax_rate", cur["tax_rate"])),
                str(keep("date", cur["date"]) or cur["date"] or date.today().isoformat()),
                str(keep("status", cur["status"]) or cur["status"]),
                str(keep("invoice_no", cur["invoice_no"]) or ""),
                str(keep("note", cur["note"]) or ""),
            ) + (int(rid),),
        )
        self._log(payload, kind, amount, rid, "修改")

    def delete(self, rid: int) -> None:
        row = self.db.one("SELECT * FROM finance WHERE id=?", int(rid))
        if not row:
            return
        self.db.run("DELETE FROM finance WHERE id=?", int(rid))
        # 记流水但不计权重：财务变更不算"做工"
        self.db.log("note", 0, f"删除款项 {KIND_CN.get(row['kind'], row['kind'])} "
                               f"{money(row['amount'])}", project_id=row["project_id"])

    def _log(self, payload: dict, kind: str, amount: float, rid: int, verb: str) -> None:
        self.db.log(
            "note", 0,
            f"{verb}款项 {KIND_CN.get(kind, kind)} {money(amount)}"
            f"（{payload.get('status') or ''}）",
            project_id=int(payload.get("project_id") or 0) or None,
            node_id=int(payload["node_id"]) if payload.get("node_id") else None,
        )

    # ---------- 导出 ----------

    def export_dir(self) -> str:
        """导出目录：设置里指定过就用它，否则 `<数据文件同目录>/export`。

        顺带把目录建出来 —— 界面上「打开导出目录」是在导出**之前**就能点的，
        目录不存在的话资源管理器会报错。已经建好时不再重复 makedirs / 写设置
        （网络盘上每次导出省一次往返）。
        """
        d = self.db.get_setting("export_dir", "") or ""
        if not d:
            d = os.path.join(os.path.dirname(os.path.abspath(self.db.path)), "export")
            self.db.set_setting("export_dir", d)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        return d

    def export(self, fmt: str, year: int | None = None) -> str:
        """不弹对话框、直接落到导出目录（快捷导出走这条）。
        挑位置/文件名的那条路是 `export_to` + api 里的系统另存为对话框。"""
        o = self.norm_opts({"fmt": fmt, "year": year or 0})
        path = os.path.join(self.export_dir(), self.build_name(o["fmt"], o))
        return self.export_to(path, o["fmt"], o)

    # 导出选项：前端传过来的一个 dict，每一项都可以不给。
    # 默认（什么都不给）= 全部时间 / 全部项目 / 四项内容全要 —— 就是老行为，
    # 所以老的调用方（只传 fmt+year）不用改。
    def norm_opts(self, opts: dict | None) -> dict:
        o = dict(opts or {})
        fmt = norm_fmt(o.get("fmt"))
        raw = o.get("sections")
        if raw is None:
            secs = [k for k, _ in SECTIONS]
        else:
            secs = [s for s in (raw if isinstance(raw, (list, tuple)) else []) if s in SECTION_CN]
            # ⚠ 界面上四项全不勾时**必须报错**，不能悄悄退回"全要" ——
            # 那会导出一份用户明确表示不要的东西，比拒绝更坏。
            if not secs:
                raise ValueError("至少要选一项导出内容")
        return {
            "fmt": fmt,
            "year": _int(o.get("year")),
            "date_from": str(o.get("date_from") or "").strip(),
            "date_to": str(o.get("date_to") or "").strip(),
            "client_id": _int(o.get("client_id")),
            "project_ids": [_int(x) for x in (o.get("project_ids") or [])],
            # CSV 是一张平表，没有"四张表"可挑 —— 只给明细，别让界面勾了
            # 项目汇总却导不出来（那种"少了东西"最难发现）
            "sections": ["records"] if fmt == "csv" else secs,
        }

    def pick_rows(self, o: dict) -> list[dict]:
        return self.records(
            year=o["year"] or None,
            client_id=o["client_id"] or None,
            date_from=o["date_from"], date_to=o["date_to"],
            project_ids=o["project_ids"],
        )

    def range_label(self, o: dict) -> str:
        """范围给人看的一句话（json 里写一份，界面上也显示同一句）。"""
        f, t = o["date_from"], o["date_to"]
        if f and t:
            return f"{f} 至 {t}"
        if f:
            return f"{f} 起"
        if t:
            return f"截至 {t}"
        if o["year"]:
            return f"{o['year']} 年"
        return "全部时间"

    def range_tag(self, o: dict) -> str:
        """范围写进文件名的样子（只用 ASCII，省得跨系统传来传去出岔子）。"""
        f, t = o["date_from"], o["date_to"]
        if f and t:
            return f"{f.replace('-', '')}-{t.replace('-', '')}"
        if f:
            return "from" + f.replace("-", "")
        if t:
            return "to" + t.replace("-", "")
        if o["year"]:
            return str(o["year"])
        return "all"

    def project_title(self, pid: int) -> str:
        r = self.db.q("SELECT title FROM projects WHERE id=?", (_int(pid),))
        return _s(r[0]["title"]) if r else ""

    def build_name(self, fmt: str, o: dict) -> str:
        """默认文件名：`board_finance_<范围>[_<项目名>]_<时间戳>.<ext>`。

        系统「另存为」对话框只是把它填进文件名框，用户可以改。
        """
        parts = ["board_finance", self.range_tag(o)]
        ids = [i for i in (o.get("project_ids") or [])]
        if len(ids) == 1:
            nm = _safe(self.project_title(ids[0]))
            if nm:
                parts.append(nm)
        elif ids:
            parts.append("%dprj" % len(ids))
        parts.append(datetime.now().strftime("%Y%m%d_%H%M"))
        return "_".join(parts) + "." + EXTS[norm_fmt(fmt)]

    def export_to(self, path: str, fmt: str, o: dict) -> str:
        fmt = norm_fmt(fmt)
        o = self.norm_opts({**o, "fmt": fmt})
        rows = self.pick_rows(o)
        if fmt == "csv":
            self._write_csv(path, rows)
        elif fmt == "json":
            self._write_json(path, rows, o)
        else:
            self._write_xlsx(path, rows, o)
        return path

    def _table(self, rows: list[dict]) -> tuple[list[str], list[list]]:
        head = ["日期", "类型", "状态", "客户", "项目", "挂到", "金额", "币种",
                "税率%", "发票号", "备注"]
        body = [[
            r["date"], r["kind_cn"], r["status"], r["client"], r["project"], r["target"],
            r["amount"], r["currency"],
            "" if r["tax_rate"] in (None, "") else r["tax_rate"],
            r["invoice_no"], r["note"],
        ] for r in rows]
        return head, body

    def _write_csv(self, path: str, rows: list[dict]) -> None:
        head, body = self._table(rows)
        # utf-8-sig：Excel 直接双击打开才不会乱码
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(head)
            w.writerows(body)

    def _write_json(self, path: str, rows: list[dict], o: dict) -> None:
        secs = o["sections"]
        projects = self._by_project(rows)
        payload = {
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "range": self.range_label(o),
            "year": o["year"] or 0,
        }
        # cards 和 records 来自同一批 rows（cards 就是这批数的汇总口径），
        # 所以勾了「款项明细」就一起给，不单独立一项
        if "records" in secs:
            payload["cards"] = self._cards(rows)
            payload["records"] = rows
        if "projects" in secs:
            payload["projects"] = [{k: v for k, v in p.items() if k != "records"}
                                   for p in projects]
        if "clients" in secs:
            payload["clients"] = self._by_client(projects)
        if "aging" in secs:
            payload["aging"] = self._aging(rows, projects)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _write_xlsx(self, path: str, rows: list[dict], o: dict) -> None:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        # 建表顺序 = SECTIONS 的顺序 = 界面上的勾选顺序
        secs = o["sections"]
        wb = Workbook()
        # ⚠ Workbook() 自带一张空表，不删掉的话勾四项就会多出第五张叫「Sheet」的表
        wb.remove(wb.active)
        bold = Font(bold=True)
        fill = PatternFill("solid", fgColor="EFEFEF")
        right = Alignment(horizontal="right")

        def sheet(title, head, body, widths, right_cols=()):
            ws = wb.create_sheet(title)
            ws.append(head)
            for c in ws[1]:
                c.font = bold
                c.fill = fill
            for r in body:
                ws.append(r)
            for i, w in enumerate(widths, start=1):
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
            for r in range(2, ws.max_row + 1):
                for c in right_cols:
                    ws.cell(row=r, column=c).alignment = right
            ws.freeze_panes = "A2"

        # 三张汇总表共用同一份 projects（以前每张各自再算一遍，一共跑三次）
        projects = self._by_project(rows)

        if "records" in secs:
            head, body = self._table(rows)
            sheet("款项明细", head, body, [12, 9, 9, 16, 26, 20, 12, 7, 8, 14, 24])

        if "projects" in secs:
            sheet("项目汇总",
                  ["项目", "客户", "合同额", "已收", "外包支出", "实际收入", "待收",
                   "已开票", "未开票", "合同日期", "最近到账", "笔数", "挂在环节"],
                  [[p["project"], p["client"], p["contract"], p["received"], p["outsource"],
                    p["net"], p["pending"], p["invoiced"], p["uninvoiced"],
                    p["contract_date"], p["last_payment"], p["count"], p["node_records"]]
                   for p in projects],
                  [26, 16, 12, 12, 12, 12, 12, 12, 12, 12, 12, 6, 10],
                  right_cols=range(3, 12))

        if "clients" in secs:
            sheet("客户汇总",
                  ["客户", "项目数", "合同额", "已收", "外包支出", "实际收入", "待收",
                   "收款率%", "最早合同日"],
                  [[c["client"], c["projects"], c["contract"], c["received"], c["outsource"],
                    c["net"], c["pending"], c["rate"], c["oldest_contract"]] for c in
                   self._by_client(projects)],
                  [20, 8, 12, 12, 12, 12, 12, 9, 13], right_cols=range(3, 9))

        if "aging" in secs:
            sheet("应收账龄", ["账龄", "待收金额"],
                  [[b["label"], b["amount"]] for b in self._aging(rows, projects)["buckets"]],
                  [14, 14], right_cols=(2,))

        wb.save(path)
