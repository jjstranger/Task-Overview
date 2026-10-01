"""树模型的读写与汇总。

层级约定：projects 是根，nodes 自引用 parent_id，层级无上限。
状态一律手动设置；父节点的状态不被子节点覆盖，只在旁边给出汇总进度。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from db import GROUPS_DEFAULT, now, today  # noqa: F401

# 项目层。2026-09-30 用户改版：去掉「待交付」这一档（它跟「进行中 / 已交付」两头都沾，
# 实际用起来没人分得清），「搁置」改叫「中止」—— 跟环节层统一口径。
PROJECT_STATUS = ["筹备", "进行中", "阻塞", "已交付", "已结款", "归档", "中止"]

# 环节（镜头）的制作流转，主线：
#   待开始 → 等上游 → 制作中 → 已提交 → 反馈 → 可优化 → 通过 → 交付
# 「暂停」「中止」是随时可切的旁支（中止 = 这条不做了，属于收工态）
# 「可优化」= 能交了但还想再打磨（2026-09-30 新增）：**不是**收工态、也不算完成，
# 免得"还想改"的活儿被算进交付量。
NODE_STATUS = [
    "待开始", "等上游", "制作中", "暂停", "中止",
    "已提交", "反馈", "可优化", "通过", "交付",
]

# 「收工态」：这些状态下不再报到期提醒、也不算停滞
NODE_CLOSED = ("通过", "交付", "中止")
# 中止 = 这单不做了，同样不再报到期提醒 / 不算停滞
PROJECT_CLOSED = ("已交付", "已结款", "归档", "中止")
CLOSED_ALL = NODE_CLOSED + PROJECT_CLOSED

# 「这条做完了」：进度条按它算。
# 注意勾选框（done）只是最快的那个动作，真正被大量使用的是状态流转 ——
# 早先进度只认 done，用户把环节改成「通过/交付」后进度条始终是 0，就是这个原因。
NODE_DONE = ("通过", "交付")

# 「不用再催停滞」的项目状态。
# ⚠ 判定必须**只有一处实现**：底栏「停滞 N」（stats）、告警列表（alerts）、
# 行上的「停滞」小标（前端 app.js:isStale）三个地方看得见同一个数，
# 各写一套的话底栏说 3 个、点开明细只有 2 个 —— 用户第一反应是"数据不对"。
# 前端那份只能逐字对齐，改这里要一起改（冒烟里钉着三处口径一致）。
STALE_SKIP = ("已结款", "归档")

# 老库迁移：旧环节状态词 → 新词，表定义在 db.py（`Db._connect` 每次开库跑一遍，幂等）

# 分类（分组）现在存在库里（groups 表），可以增删改；代码里已经没有写死的分类了
# —— 出厂值在 db.GROUPS_DEFAULT，只在表还是空的时候种进去（见 db._init_groups）。

# ---------- 活跃度权重 ----------
# 热力图只统计 weight>0 的流水（`activity()` 里 SUM(weight) ... WHERE weight>0）。
# 分两档：
#   W_ACT  = 计入活跃：这一下是**真的在推进产出**
#   W_IDLE = 只记流水不计活跃
# 后者要是计权重，光是把活儿录进看板、来回切状态就能把热力图刷满，
# 「活跃」就不再是产出的度量了。流水本身照写（活跃页的列表可查），只是不参与计数。
W_ACT = 1
W_IDLE = 0

# 环节状态流转里**只有这 5 个**算「在推进产出」（2026-09-30 用户定的白名单）：
# 制作中 / 已提交 / 反馈 / 可优化 / 交付。
#   · 其余环节状态（待开始 / 等上游 / 暂停 / 中止 / 通过）只记流水不计分
#   · 项目层状态（筹备 / 进行中 / 阻塞 / 已交付 / 已结款 / 归档 / 中止）**一律不计**
#   · 勾选框「标记完成」（把环节置成「通过」）同样不计 —— 口径与上一条保持一致
# 词表改版时这里要跟着核一遍（冒烟里断言它必须是 NODE_STATUS 的子集）。
NODE_ACTIVE_STATUS = ("制作中", "已提交", "反馈", "可优化", "交付")

STALE_DAYS_DEFAULT = 3

# ---------- 活跃流水在界面上的显示口径 ----------
# 面板上只列最近一周、最多这么多条（超出的进「查看更多」弹窗翻页）。
# 流水是会一直涨的表（每改一次状态、每打一次卡都写一行），
# 不限制的话活跃页底下那条列表几年后能有几万行 —— 渲染一次就卡。
LOG_WINDOW_DAYS = 7
LOG_PANEL_CAP = 300


def stale_days(db) -> int:
    """停滞阈值：几天没动静算停滞。**全库一个数**（2026-09-30 起不再分商业/个人）。

    坏值 / 没设 / 非正数一律退回默认，别让一个手滑填的 0 把整棵树标成停滞。
    """
    try:
        v = int(str(db.get_setting("stale_days", "") or "").strip())
    except ValueError:
        return STALE_DAYS_DEFAULT
    return v if v > 0 else STALE_DAYS_DEFAULT


def _s(v):
    return v if v is not None else ""


def _parse_ts(ts: str):
    """`last_activity_at` 是 "YYYY-MM-DD HH:MM:SS"；解析不了返回 None。"""
    try:
        return datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def stale_gap(last_activity_at: str, status: str, threshold: int) -> int | None:
    """这条多久没动了**且已经算停滞** → 返回间隔天数；否则 None。

    **全工程唯一的停滞判据**（见 STALE_SKIP 的注释）：
    收工状态、时间戳坏了 / 空、还没到阈值 —— 三种都返回 None，不误报。
    """
    if status in STALE_SKIP:
        return None
    t = _parse_ts(last_activity_at)
    if t is None:
        return None
    gap = (datetime.now() - t).days
    return gap if gap >= threshold else None


def _days_left(d: str) -> int | None:
    if not d:
        return None
    try:
        return (datetime.strptime(d, "%Y-%m-%d").date() - date.today()).days
    except ValueError:
        return None


def _due_label(days_left: int) -> str:
    """到期提醒的文案。

    「D-1」「今天」这种记号看着像密码 —— 用户得先知道 D 是 deadline 才读得懂，
    所以一律写成整句话。**前端 `dueTag()` 必须跟这里逐字一致**：
    同一个数字在两处（行上的小标、告警面板）显示成两种说法最容易让人怀疑数据不对。
    """
    if days_left < 0:
        return "逾期 %d 天" % (-days_left)
    if days_left == 0:
        return "今天到期"
    return "%d 天后到期" % days_left


class Board:
    def __init__(self, db):
        self.db = db

    # ---------- 读取 ----------

    def load(self, include_archived: bool = False) -> dict:
        """整棵树 + 分组 + 统计 + 设置。`include_archived` 为真时把归档项目也带出来。

        归档项目默认**根本不发往前端**（不是前端藏起来），这样归档是真的「收进抽屉」；
        要看的时候由项目页的筛选菜单显式要一次。
        """
        links: dict[tuple[str, int], list] = {}
        for r in self.db.q("SELECT * FROM links ORDER BY sort_order,id"):
            links.setdefault((r["owner_type"], r["owner_id"]), []).append(
                {"id": r["id"], "label": r["label"], "path": r["path"]}
            )

        nodes_by_parent: dict[tuple[int, int | None], list] = {}
        for r in self.db.q("SELECT * FROM nodes ORDER BY sort_order,id"):
            nodes_by_parent.setdefault((r["project_id"], r["parent_id"]), []).append(r)

        def build(pid: int, parent_id: int | None) -> list:
            out = []
            for r in nodes_by_parent.get((pid, parent_id), []):
                out.append(
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "status": r["status"],
                        # 进度只看这个字段：勾过 或 状态已经走到收工（通过/交付）都算完成
                        "done": 1 if (r["done"] or r["status"] in NODE_DONE) else 0,
                        "collapsed": r["collapsed"],
                        "deadline": _s(r["deadline"]),
                        "note": _s(r["note"]),
                        "artist": _s(r["artist"]),
                        "last_activity_at": _s(r["last_activity_at"]),
                        "links": links.get(("node", r["id"]), []),
                        "children": build(pid, r["id"]),
                    }
                )
            return out

        # 归档有**两条路**：⋯ 菜单里的「归档」写 archived 字段；直接在状态徽章里
        # 选「归档」改的是 status。用户两条都在用（后者更顺手），所以隐藏归档时
        # 必须两条都认 —— 只认字段的话，状态归档的项目照样冒出来，开关看着像坏了。
        projects = []
        for r in self.db.q(
            "SELECT p.*, c.name AS client FROM projects p "
            "LEFT JOIN clients c ON c.id=p.client_id "
            "WHERE ((p.archived=0 AND p.status<>'归档') OR ?) "
            "ORDER BY p.pinned DESC, p.sort_order, p.id",
            (1 if include_archived else 0,),
        ):
            projects.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "category": r["category"],
                    "status": r["status"],
                    "archived": r["archived"],
                    "client": _s(r["client"]),
                    "client_id": r["client_id"],
                    "priority": r["priority"],
                    "deadline": _s(r["deadline"]),
                    "note": _s(r["note"]),
                    "artist": _s(r["artist"]),
                    "collapsed": r["collapsed"],
                    "last_activity_at": _s(r["last_activity_at"]),
                    "links": links.get(("project", r["id"]), []),
                    "children": build(r["id"], None),
                }
            )

        groups = [
            {
                "key": g["key"],
                "name": g["name"],
                "projects": [p for p in projects if p["category"] == g["key"]],
            }
            for g in self.groups()
        ]
        return {
            "groups": groups,
            "status": {"project": PROJECT_STATUS, "node": NODE_STATUS},
            "stats": self.stats(groups),
            "settings": self.db.all_settings(),
            "clients": self.clients(),
        }

    def stats(self, groups=None) -> dict:
        """底栏那几个数。`groups` 给了就复用（load() 里刚算过），不给就自己 load 一份。"""
        if groups is None:
            groups = self.load()["groups"]
        threshold = stale_days(self.db)

        def last_act(node) -> str:
            """这条子树上最新的活动时间（含全部后代）。"""
            vals = [node.get("last_activity_at") or ""]
            for c in node.get("children", []):
                vals.append(last_act(c))
            return max([v for v in vals if v], default="")

        def count_due(node) -> int:
            """这棵子树里「临期 / 逾期」的条数（关闭判定跟 alerts() 的 due 同一套）。"""
            n = 0
            d = _days_left(node.get("deadline"))
            if d is not None and d <= 3 and node.get("status") not in CLOSED_ALL:
                n += 1
            for c in node.get("children", []):
                n += count_due(c)
            return n

        today_n = running = blocked = stale = due = 0
        for g in groups:
            for p in g["projects"]:
                if p["status"] == "阻塞":
                    blocked += 1
                if p["status"] == "进行中":
                    running += 1
                if p["priority"]:
                    today_n += 1
                # 停滞：判据走 stale_gap（跟 alerts 和前端同一套，含 STALE_SKIP）
                if stale_gap(last_act(p), p["status"], threshold) is not None:
                    stale += 1
                due += count_due(p)
        return {
            "today": today_n,
            "running": running,
            "blocked": blocked,
            "stale": stale,
            "due": due,
        }

    def priority_count(self) -> int:
        """「今日必做」的项目数。

        托盘提示只要这一个数，别为它跑一次整树 `load()`（stats() 无参调用会，
        而 load() 内部又会再算一遍 stats —— 启动时白算两遍整棵树）。
        """
        row = self.db.one(
            "SELECT COUNT(*) AS n FROM projects "
            "WHERE priority<>0 AND archived=0 AND status<>'归档'"
        )
        return int(row["n"] if row else 0)

    def clients(self) -> list:
        return [
            {"id": r["id"], "name": r["name"], "settlement_cycle": _s(r["settlement_cycle"])}
            for r in self.db.q("SELECT * FROM clients ORDER BY id")
        ]

    # ---------- 分类（分组） ----------
    # 项目树顶层那几个筐。键（gkey）是稳定标识，项目用 category 挂它：
    # 改名只动 name，所以「重命名」不会碰到任何项目。

    def groups(self) -> list[dict]:
        """库里的分类，顺序按 sort_order。

        兜底：项目的 category 指向一个**不存在的键**时（手工改库、老快照回滚
        到分类被删之前……），不能把这些项目凭空弄丢 —— 那样看着像数据没了。
        给它们临时编一个框，名字用键本身，排在最后。
        """
        rows = [
            {"key": r["gkey"], "name": r["name"]}
            for r in self.db.q("SELECT * FROM groups ORDER BY sort_order,gkey")
        ]
        known = {g["key"] for g in rows}
        orphans = [
            r["category"]
            for r in self.db.q(
                "SELECT DISTINCT category FROM projects "
                "WHERE category IS NOT NULL AND category<>''"
            )
            if r["category"] not in known
        ]
        for k in sorted(orphans):
            rows.append({"key": k, "name": k})
        if not rows:                       # 理论上不会（_init_groups 会种），防御一下
            rows = [{"key": k, "name": n} for k, n in GROUPS_DEFAULT]
        return rows

    def group_keys(self) -> list[str]:
        return [g["key"] for g in self.groups()]

    def group_add(self, name: str, key: str | None = None) -> str:
        """新建一个分类，返回它的键。

        键是自动生成的短标识（`g3` 这种），跟名字解耦 —— 以后改名不影响挂靠。
        名字允许重名（筐叫什么都行），但键一定唯一。
        """
        n = str(name or "").strip()
        if not n:
            raise ValueError("分类名不能为空")
        if key is None:
            row = self.db.one(
                "SELECT COALESCE(MAX(CAST(SUBSTR(gkey,2) AS INTEGER)),0) AS m "
                "FROM groups WHERE gkey LIKE 'g%'"
            )
            key = "g" + str(int((row["m"] if row else 0) or 0) + 1)
        order = self.db.one("SELECT COALESCE(MAX(sort_order),-1)+1 AS o FROM groups")
        self.db.run(
            "INSERT INTO groups(gkey,name,sort_order) VALUES(?,?,?)",
            (str(key), n, int((order["o"] if order else 0) or 0)),
        )
        return str(key)

    def group_rename(self, key: str, name: str) -> None:
        n = str(name or "").strip()
        if not n:
            raise ValueError("分类名不能为空")
        if not self.db.one("SELECT 1 AS x FROM groups WHERE gkey=?", str(key)):
            raise ValueError("没有这个分类")
        self.db.run("UPDATE groups SET name=? WHERE gkey=?", (n, str(key)))

    def group_delete(self, key: str) -> None:
        """删分类。**空筐才让删** —— 跟删客户一个规矩：
        还有项目挂在这儿的时候，先让用户把它们挪走（或删掉），
        免得一个手滑端掉一筐项目（还不好解释"东西去哪儿了"）。
        最后一个分类也不让删：一个筐都没有的树画不出来。
        """
        k = str(key or "")
        row = self.db.one("SELECT name FROM groups WHERE gkey=?", k)
        if not row:
            raise ValueError("没有这个分类")
        used = self.db.one("SELECT COUNT(*) AS c FROM projects WHERE category=?", k)
        n = int(used["c"]) if used else 0
        if n:
            raise ValueError(f"还有 {n} 个项目挂在这个分类上，先把它们挪到别的分类（或删掉）再删")
        cnt = self.db.one("SELECT COUNT(*) AS c FROM groups")
        if int(cnt["c"] if cnt else 0) <= 1:
            raise ValueError("至少得留一个分类")
        self.db.run("DELETE FROM groups WHERE gkey=?", k)

    def set_group(self, project_id: int, key: str) -> None:
        """把项目挪到另一个分类。键必须真实存在，免得挂到一个不存在的筐里。"""
        k = str(key or "")
        if not self.db.one("SELECT 1 AS x FROM groups WHERE gkey=?", k):
            raise ValueError("没有这个分类")
        self.set_field("project", int(project_id), "category", k)

    # ---------- 项目 ----------

    def add_project(self, title: str, category: str, client_id: int | None = None) -> int:
        """建项目。分类键**认不出来就落到第一个分类**上。

        为什么兜一下：分类现在能删，前端 localStorage 里可能还留着刚被删掉的那个键
        （或者调用方直接传了 `commercial` 这种老写法）。让项目挂到一个不存在的筐里，
        界面上就成了一条谁也点不到的野项目 —— 不如老老实实放进第一个筐。
        """
        keys = self.group_keys()
        cat = str(category or "")
        if keys and cat not in keys:
            cat = keys[0]
        pid = self.db.run(
            "INSERT INTO projects(title,category,client_id,status,created_at,updated_at,last_activity_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (title, cat, client_id, "进行中", now(), now(), now()),
        )
        # 建项目是「整理看板」，不算产出 —— 记流水但不计活跃
        self.db.log("status", W_IDLE, f"新建项目 {title}", project_id=pid)
        return pid

    def add_node(self, project_id: int, parent_id: int | None, title: str) -> int:
        nid = self.db.run(
            "INSERT INTO nodes(project_id,parent_id,title,status,created_at,updated_at,last_activity_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (project_id, parent_id, title, "待开始", now(), now(), now()),
        )
        self._touch(project_id)
        # 同上：把环节录进来是整理动作，不计活跃
        self.db.log("task", W_IDLE, f"新增环节 {title}", project_id=project_id, node_id=nid)
        return nid

    def add_nodes(self, project_id: int, parent_id: int | None, titles: list[str]) -> list[int]:
        """批量新增同级环节。

        批量录入（`s001,s003A,s006-009`）走这条：一次事务、只 touch 一次、只记一条流水，
        否则 20 个子环节就是 20 次跨桥往返 + 20 条日志。
        """
        pid, par = int(project_id), (int(parent_id) if parent_id else None)
        ts = now()
        ids: list[int] = []
        with self.db.mutex:
            # isolation_level=None 下每条 INSERT 都各自 autocommit，批量必须显式包事务：
            # 否则 20 个环节就是 20 次提交（NAS 上每条 10ms+），且中途失败会留半拉子
            self.db.conn.execute("BEGIN")
            try:
                for t in titles:
                    cur = self.db.conn.execute(
                        "INSERT INTO nodes(project_id,parent_id,title,status,created_at,"
                        "updated_at,last_activity_at) VALUES(?,?,?,?,?,?,?)",
                        (pid, par, str(t).strip(), "待开始", ts, ts, ts),
                    )
                    ids.append(cur.lastrowid or 0)
                self.db.conn.execute("COMMIT")
            except Exception:
                self.db.conn.execute("ROLLBACK")
                raise
            self.db.touch()     # 直连 conn 写的，得手动告诉数据层"库动过了"
        self._touch(pid)
        self.db.log(
            "task", W_IDLE, f"批量新增 {len(ids)} 个环节：{'、'.join(titles[:6])}"
            + ("…" if len(titles) > 6 else ""),
            project_id=pid,
        )
        return ids

    def child_titles(self, project_id: int, parent_id: int | None) -> list[str]:
        """某个父级下已有的子环节标题（批量录入时用来跳过重复）。"""
        if parent_id:
            rows = self.db.q(
                "SELECT title FROM nodes WHERE project_id=? AND parent_id=?",
                int(project_id), int(parent_id),
            )
        else:
            rows = self.db.q(
                "SELECT title FROM nodes WHERE project_id=? AND parent_id IS NULL",
                int(project_id),
            )
        return [r["title"] for r in rows]

    def rename(self, kind: str, oid: int, title: str) -> None:
        table = "projects" if kind == "project" else "nodes"
        self.db.run(f"UPDATE {table} SET title=?, updated_at=? WHERE id=?", (title, now(), oid))
        if kind == "node":
            self._touch_by_node(oid)

    def set_status(self, kind: str, oid: int, status: str) -> None:
        table = "projects" if kind == "project" else "nodes"
        row = self.db.one(f"SELECT title,project_id FROM {table} WHERE id=?", oid) if kind == "node" \
            else self.db.one("SELECT title,id AS project_id FROM projects WHERE id=?", oid)
        self.db.run(
            f"UPDATE {table} SET status=?, updated_at=?, last_activity_at=? WHERE id=?",
            (status, now(), now(), oid),
        )
        pid = row["project_id"] if row else None
        if kind == "node":
            self._touch(pid)
        # 活跃度只看**环节**流转到没流转到「在推进」的那几个状态（NODE_ACTIVE_STATUS）；
        # 项目整体状态属于整理看板，一律不计。
        self.db.log(
            "status",
            W_ACT if (kind == "node" and status in NODE_ACTIVE_STATUS) else W_IDLE,
            f"{row['title'] if row else oid} → {status}",
            project_id=pid, node_id=oid if kind == "node" else None,
        )

    def set_done(self, oid: int, done: int) -> None:
        """勾选框：勾上 = 这条过了（→「通过」），取消 = 回到「制作中」。

        状态一律手动为主，但勾选框是叶子节点上最快的一个动作，
        让它跟一个状态联动，免得出现「勾上了状态还写着制作中」的自相矛盾。
        进度条读的是 `load()` 派生的 done（勾过 或 状态已收工），两边天然一致。
        """
        row = self.db.one("SELECT title,project_id FROM nodes WHERE id=?", oid)
        self.db.run(
            "UPDATE nodes SET done=?, status=?, updated_at=?, last_activity_at=? WHERE id=?",
            (done, "通过" if done else "制作中", now(), now(), oid),
        )
        if row:
            self._touch(row["project_id"])
            if done:
                # 勾完是「通过」，而「通过」不在 NODE_ACTIVE_STATUS 里 ——
                # 两条路（状态菜单 / 勾选框）必须落同一个数，不然同一件事
                # 从哪儿点会有两种活跃度，热力图就成了笔糊涂账。
                self.db.log("task", W_IDLE, f"完成任务 {row['title']}",
                            project_id=row["project_id"], node_id=oid)

    def set_collapsed(self, kind: str, oid: int, val: int) -> None:
        table = "projects" if kind == "project" else "nodes"
        self.db.run(f"UPDATE {table} SET collapsed=? WHERE id=?", (val, oid))

    def set_field(self, kind: str, oid: int, field: str, value) -> None:
        table = "projects" if kind == "project" else "nodes"
        allowed = {
            "projects": {"deadline", "note", "artist", "priority", "pinned",
                         "client_id", "category", "archived"},
            "nodes": {"deadline", "note", "artist", "sort_order"},
        }[table]
        if field not in allowed:
            raise ValueError(f"字段不可写：{field}")
        self.db.run(
            f"UPDATE {table} SET {field}=?, updated_at=? WHERE id=?", (value, now(), oid)
        )
        if kind == "node":
            self._touch_by_node(oid)
        else:
            self._touch(oid)

    def bulk_update(self, ids, status=None, artist=None) -> dict:
        """多选批量改「环节状态」和「制作人」（issue #3）。

        逐个走 set_status / set_field 而不是拼一条 UPDATE，是为了让**活跃度口径**
        自动保持一致：环节流转到 NODE_ACTIVE_STATUS 里的状态才计活跃、
        改制作人不计 —— 一处改口径两处都跟上。
        整体包在一个事务里，中途出错不留半拉子。
        返回 {ok, done, skipped, msg}，让界面能说清"改了几条、跳了几条"。
        """
        want = []
        for i in ids or []:
            try:
                want.append(int(i))
            except Exception:
                continue
        want = list(dict.fromkeys(want))                 # 去重且保序
        if not want:
            return {"ok": False, "done": 0, "skipped": 0, "msg": "没选环节"}
        if status is None and artist is None:
            return {"ok": False, "done": 0, "skipped": 0, "msg": "没给要改成什么"}

        done, skipped, msg = 0, 0, None
        self.db.conn.execute("BEGIN")
        try:
            for nid in want:
                row = self.db.one("SELECT id FROM nodes WHERE id=?", nid)
                if not row:
                    skipped += 1
                    continue
                if status is not None:
                    self.set_status("node", nid, str(status))
                if artist is not None:
                    self.set_field("node", nid, "artist", str(artist))
                done += 1
            self.db.conn.execute("COMMIT")
        except Exception as exc:
            try:
                self.db.conn.execute("ROLLBACK")
            except Exception:
                pass
            return {"ok": False, "done": 0, "skipped": len(want), "msg": str(exc)}
        return {"ok": True, "done": done, "skipped": skipped, "msg": msg}


    # ---------- 排序与折叠（M4） ----------

    def set_collapsed_all(self, mode: str = "collapse") -> None:
        """一条 UPDATE 批量展开/折叠。

        前端逐个调 set_collapsed 的话，几百个节点就是几百次跨桥往返，
        这里一次搞定。mode: expand / collapse / toProject（collapse = 现在的默认态）

        顶栏那三个「全部展开 / 全部折叠 / 只看项目」按钮按用户要求撤掉了，
        这个方法留着给程序化调用（开库迁移、回归测试）用。
        """
        if mode == "expand":
            p, n = 0, 0
        elif mode == "toProject":
            # 项目行都展开（看得见下一层），环节全收起来
            p, n = 0, 1
        else:
            p, n = 1, 1
        with self.db.mutex:
            self.db.conn.execute("UPDATE projects SET collapsed=?", (p,))
            self.db.conn.execute("UPDATE nodes SET collapsed=?", (n,))
            self.db.touch()

    def move(self, kind: str, oid: int, target_id, pos: str = "after") -> dict:
        """拖拽后的落位。pos: before / after / inside。

        - project：同分类、「今日必做」状态相同的项目之间重排
        - node：同项目内换位置；inside 表示挂到目标环节下面当子级
        """
        oid = int(oid)
        target_id = int(target_id) if target_id else None
        pos = str(pos or "after").lower()
        if pos not in ("before", "after", "inside"):
            raise ValueError(f"未知的放置方式：{pos}")
        if target_id == oid:
            return {"ok": True, "moved": False}
        if kind == "project":
            return self._move_project(oid, target_id, pos)
        return self._move_node(oid, target_id, pos)

    def _move_project(self, oid: int, target_id, pos: str) -> dict:
        me = self.db.one("SELECT id,category,pinned FROM projects WHERE id=?", oid)
        tgt = (
            self.db.one("SELECT id,category,pinned FROM projects WHERE id=?", target_id)
            if target_id
            else None
        )
        if not me:
            raise ValueError("要移动的项目不存在")
        if not tgt:
            raise ValueError("找不到放置目标")
        if me["category"] != tgt["category"]:
            raise ValueError("只能在同一分类里排序")

        pin_me = int(me["pinned"] or 0)
        # 「常驻顶部」的项目永远排在列表最前（ORDER BY pinned DESC）。跨过这条线
        # 拖拽，视觉上不会有任何变化，用户只会以为「拖不动」——直接说清楚。
        if pin_me != int(tgt["pinned"] or 0):
            raise ValueError("「常驻顶部」的项目固定在列表最前，先取消它的置顶再排序")

        sibs = [
            r["id"]
            for r in self.db.q(
                "SELECT id FROM projects WHERE archived=0 AND category=?"
                " AND COALESCE(pinned,0)=? ORDER BY sort_order,id",
                (me["category"], pin_me),
            )
        ]
        if oid not in sibs or target_id not in sibs:
            raise ValueError("排序范围对不上，刷新后再试")
        sibs.remove(oid)
        i = sibs.index(target_id)
        sibs.insert(i if pos == "before" else i + 1, oid)
        with self.db.mutex:
            self.db.conn.execute("BEGIN")
            try:
                for n, pid in enumerate(sibs):
                    self.db.conn.execute("UPDATE projects SET sort_order=? WHERE id=?", (n, pid))
                self.db.conn.execute("COMMIT")
            except Exception:
                self.db.conn.execute("ROLLBACK")
                raise
            self.db.touch()
        return {"ok": True, "order": sibs}

    def _move_node(self, oid: int, target_id, pos: str) -> dict:
        me = self.db.one("SELECT id,project_id,parent_id FROM nodes WHERE id=?", oid)
        if not me:
            raise ValueError("要移动的环节不存在")
        pid = me["project_id"]

        if target_id is None:
            # 没给目标：落到本项目根层
            new_parent, before_id = None, None
        else:
            tgt = self.db.one(
                "SELECT id,project_id,parent_id FROM nodes WHERE id=?", target_id
            )
            if not tgt:
                raise ValueError("找不到放置目标")
            if tgt["project_id"] != pid:
                raise ValueError("暂不支持把环节移到别的项目")
            if pos == "inside":
                new_parent, before_id = int(tgt["id"]), None
            else:
                new_parent, before_id = tgt["parent_id"], int(tgt["id"])

        # 挂到自己或自己的后代下面会成环，直接剪掉
        if new_parent is not None and self._is_descendant(pid, oid, new_parent):
            raise ValueError("不能把环节移进它自己的子环节里")

        args = (pid,) if new_parent is None else (pid, new_parent)
        sibs = [
            r["id"]
            for r in self.db.q(
                "SELECT id FROM nodes WHERE project_id=? AND "
                + ("parent_id IS NULL" if new_parent is None else "parent_id=?")
                + " ORDER BY sort_order,id",
                args,
            )
        ]
        sibs = [x for x in sibs if x != oid]
        if before_id is not None and before_id in sibs:
            i = sibs.index(before_id)
            sibs.insert(i if pos == "before" else i + 1, oid)
        else:
            # inside，或目标没落在候选里 —— 追加到末尾
            sibs.append(oid)

        with self.db.mutex:
            self.db.conn.execute("BEGIN")
            try:
                self.db.conn.execute(
                    "UPDATE nodes SET parent_id=?, updated_at=? WHERE id=?", (new_parent, now(), oid)
                )
                for n, nid in enumerate(sibs):
                    self.db.conn.execute("UPDATE nodes SET sort_order=? WHERE id=?", (n, nid))
                self.db.conn.execute("COMMIT")
            except Exception:
                self.db.conn.execute("ROLLBACK")
                raise
            self.db.touch()
        # 故意不 _touch：重新排序不是工作进展，不该把「停滞」告警清掉
        return {"ok": True, "parent_id": new_parent, "order": sibs}

    def _is_descendant(self, project_id: int, ancestor_id: int, candidate_id: int) -> bool:
        """candidate 是否在 ancestor 的子树里（含 candidate==ancestor）。"""
        cur = candidate_id
        for _ in range(512):  # 防脏数据成环时死循环
            if cur == ancestor_id:
                return True
            row = self.db.one(
                "SELECT parent_id FROM nodes WHERE id=? AND project_id=?", (cur, project_id)
            )
            if not row or row["parent_id"] is None:
                return False
            cur = row["parent_id"]
        return False

    def delete(self, kind: str, oid: int) -> None:
        d = self.db
        if kind == "project":
            d.run("DELETE FROM nodes WHERE project_id=?", oid)
            d.run("DELETE FROM links WHERE owner_type='project' AND owner_id=?", oid)
            d.run("DELETE FROM finance WHERE project_id=?", oid)
            d.run("DELETE FROM projects WHERE id=?", oid)
        else:
            row = d.one("SELECT project_id FROM nodes WHERE id=?", oid)

            def collect(i: int, acc: list):
                acc.append(i)
                for r in d.q("SELECT id FROM nodes WHERE parent_id=?", i):
                    collect(r["id"], acc)

            ids: list[int] = []
            collect(oid, ids)
            marks = ",".join("?" * len(ids))
            d.run(f"DELETE FROM nodes WHERE id IN ({marks})", ids)
            d.run(
                f"DELETE FROM links WHERE owner_type='node' AND owner_id IN ({marks})", ids
            )
            if row:
                self._touch(row["project_id"])

    # ---------- 目录入口 ----------

    def add_link(self, owner_type: str, owner_id: int, label: str, path: str) -> int:
        return self.db.run(
            "INSERT INTO links(owner_type,owner_id,label,path) VALUES(?,?,?,?)",
            (owner_type, owner_id, label, path),
        )

    def delete_link(self, oid: int) -> None:
        self.db.run("DELETE FROM links WHERE id=?", (oid,))

    def checkin(self, items: list) -> None:
        for it in items:
            pid = int(it.get("id") or 0)
            if not pid:
                continue
            title = it.get("title") or ""
            self.db.log("checkin", W_ACT, f"打卡 {title}", project_id=pid)
            self._touch(pid)

    # ---------- 活跃与告警（M2） ----------

    def _log_rows(self, limit: int = 200, offset: int = 0, since: str = "") -> list[dict]:
        """流水行（带项目名）。`since` = 只取这天及以后的。

        面板和「查看更多」共用这一个查询，省得两边口径慢慢跑偏。
        """
        sql = (
            "SELECT a.ts,a.day,a.kind,a.detail,a.weight, p.title AS prj "
            "FROM activity_log a LEFT JOIN projects p ON p.id=a.project_id "
        )
        params: list = []
        if since:
            sql += "WHERE a.day>=? "
            params.append(since)
        sql += "ORDER BY a.id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        return [
            {
                "ts": _s(r["ts"]),
                "day": _s(r["day"]),
                "kind": _s(r["kind"]),
                "detail": _s(r["detail"]),
                "project": _s(r["prj"]),
                "weight": r["weight"],
            }
            for r in self.db.q(sql, params)
        ]

    def activity(self, year: int | None = None) -> dict:
        """热力图数据：按天聚合权重，外加连续天数统计与最近流水。"""
        if not year:
            year = date.today().year
        y = str(int(year))

        counts = {
            r["day"]: int(r["w"])
            for r in self.db.q(
                "SELECT day, SUM(weight) AS w FROM activity_log "
                "WHERE day LIKE ? AND weight>0 GROUP BY day",
                (y + "-%",),
            )
        }
        days = sorted(counts)
        d_of = lambda s: datetime.strptime(s, "%Y-%m-%d").date()  # noqa: E731

        longest = run = 0
        prev = None
        for s in days:
            dt = d_of(s)
            run = run + 1 if prev and (dt - prev).days == 1 else 1
            longest = max(longest, run)
            prev = dt

        # 当前连续：今天有活动就从今天数，否则从昨天数；非当前年份从年末倒数
        anchor = date.today() if int(y) == date.today().year else date(int(y), 12, 31)
        if anchor.isoformat() not in counts:
            anchor = date.fromordinal(anchor.toordinal() - 1)
        current = 0
        while anchor.isoformat() in counts:
            current += 1
            anchor = date.fromordinal(anchor.toordinal() - 1)

        years = [
            r["y"] for r in self.db.q(
                "SELECT DISTINCT substr(day,1,4) AS y FROM activity_log ORDER BY y DESC"
            )
        ]
        yc = str(date.today().year)
        if yc not in years:
            years.insert(0, yc)

        # 面板上只给**最近一周**（2026-09-30 用户要求）：流水会越攒越多，
        # 全量往回拉既没人看、又把面板拉得老长。超出的走 activity_logs() 翻页看。
        since = (date.today() - timedelta(days=LOG_WINDOW_DAYS - 1)).isoformat()
        recent = self._log_rows(limit=LOG_PANEL_CAP, since=since)
        recent_total = int(
            self.db.one(
                "SELECT COUNT(*) AS n FROM activity_log WHERE day>=?", (since,)
            )["n"]
        )
        log_total = int(self.db.one("SELECT COUNT(*) AS n FROM activity_log")["n"])
        return {
            "year": int(y),
            "years": years,
            "days": counts,
            "total": sum(counts.values()),
            "active_days": len(days),
            "longest": longest,
            "current": current,
            "recent": recent,
            "recent_since": since,
            "recent_total": recent_total,
            "recent_more": recent_total - len(recent),   # 一周内被截掉的条数
            "log_total": log_total,
            "window_days": LOG_WINDOW_DAYS,
        }

    def activity_logs(self, offset: int = 0, limit: int = 200) -> dict:
        """全量流水翻页 —— 给「查看更多」那个弹窗用。

        按 id 倒序（新→旧）。带 total/has_more，前端据此决定「加载更多」还露不露。
        """
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 200
        limit = min(500, max(1, limit))
        total = int(self.db.one("SELECT COUNT(*) AS n FROM activity_log")["n"])
        rows = self._log_rows(limit=limit, offset=offset)
        return {
            "rows": rows,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < total,
        }

    def checked_in_today(self) -> bool:
        return bool(
            self.db.one(
                "SELECT 1 AS x FROM activity_log WHERE kind='checkin' AND day=? LIMIT 1",
                today(),
            )
        )

    def alerts(self) -> dict:
        """停滞与到期两类告警，供系统通知和前端展示共用。"""
        thr = stale_days(self.db)

        stale: list[dict] = []
        for r in self.db.q(
            "SELECT p.id,p.title,p.category,p.status,p.last_activity_at,"
            " (SELECT MAX(n.last_activity_at) FROM nodes n WHERE n.project_id=p.id) AS nact"
            " FROM projects p WHERE p.archived=0"
        ):
            la = max([x for x in (r["last_activity_at"], r["nact"]) if x], default="")
            gap = stale_gap(la, r["status"], thr)
            if gap is None:
                continue
            stale.append(
                {"kind": "stale", "title": r["title"], "detail": f"{gap} 天无进展",
                 "project_id": r["id"]}
            )

        due: list[dict] = []
        for r in self.db.q(
            "SELECT 'project' AS t, p.id, p.title, p.deadline, p.status, p.title AS prj"
            " FROM projects p WHERE p.archived=0 AND p.deadline IS NOT NULL AND p.deadline<>''"
            " UNION ALL "
            "SELECT 'node', n.id, n.title, n.deadline, n.status, p.title"
            " FROM nodes n JOIN projects p ON p.id=n.project_id"
            " WHERE n.deadline IS NOT NULL AND n.deadline<>''"
        ):
            if r["status"] in CLOSED_ALL:
                continue
            d = _days_left(r["deadline"])
            if d is None or d > 3:
                continue
            label = _due_label(d)
            due.append(
                {"kind": "due", "title": r["title"], "detail": label,
                 "project": _s(r["prj"]), "days": d,
                 "owner": r["t"], "id": r["id"]}
            )
        due.sort(key=lambda x: x["days"])
        return {"stale": stale, "due": due, "total": len(stale) + len(due)}

    # ---------- 内部 ----------

    def _touch(self, project_id: int | None) -> None:
        if project_id:
            self.db.run(
                "UPDATE projects SET last_activity_at=?, updated_at=? WHERE id=?",
                (now(), now(), project_id),
            )

    def _touch_by_node(self, node_id: int) -> None:
        row = self.db.one("SELECT project_id FROM nodes WHERE id=?", node_id)
        if row:
            self._touch(row["project_id"])
