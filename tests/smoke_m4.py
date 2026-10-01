"""M4 打磨期的数据层回归：拖拽排序、批量折叠、快照回滚。

排序这块最怕的不是「排不动」，而是**悄悄排错**（比如跨分类拖拽被默默吞掉、
拖进自己的子环节形成环）。所以这里对每一种非法操作都断言了「必须报错且说明原因」。

跑法：
    <venv>\\Scripts\\python.exe tests\\smoke_m4.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from api import Api  # noqa: E402
from db import Db  # noqa: E402
from models import Board  # noqa: E402


def _find(ns, nid):
    for n in ns:
        if n["id"] == nid:
            return n
        r = _find(n["children"], nid)
        if r is not None:
            return r
    return None


def kids(board, kind, oid):
    """按界面顺序取子级 id。

    项目表和环节表各自自增，id 会撞号（项目的 2 号就是环节的 2 号），
    所以必须显式说明看的是哪种。
    """
    for g in board.load()["groups"]:
        for p in g["projects"]:
            if kind == "project" and p["id"] == oid:
                return [c["id"] for c in p["children"]]
            n = _find(p["children"], oid)
            if kind == "node" and n is not None:
                return [c["id"] for c in n["children"]]
    return []


def proj_order(board, cat="commercial"):
    for g in board.load()["groups"]:
        if g["key"] == cat:
            return [p["id"] for p in g["projects"]]
    return []


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="board_m4_")
    db = Db(os.path.join(tmp, "board.sqlite"))
    db.open()
    board = Board(db)
    api = Api(board, db, {})

    # 建库时带两条示例数据，先清干净，后面的断言才好数数
    for t in ("finance", "links", "nodes", "projects", "clients", "activity_log"):
        db.run(f"DELETE FROM {t}")

    # ---------- 1. 项目拖拽排序 ----------
    a = api.add_project("A", "commercial")["id"]
    b = api.add_project("B", "commercial")["id"]
    c = api.add_project("C", "commercial")["id"]
    assert proj_order(board) == [a, b, c], proj_order(board)

    r = api.move("project", c, a, "before")
    assert r["ok"] and proj_order(board) == [c, a, b], (r, proj_order(board))

    r = api.move("project", c, b, "after")
    assert r["ok"] and proj_order(board) == [a, b, c], (r, proj_order(board))

    r = api.move("project", a, c, "before")
    assert r["ok"] and proj_order(board) == [b, a, c], (r, proj_order(board))

    # 排到第一位 / 最后一位
    r = api.move("project", c, b, "before")
    assert r["ok"] and proj_order(board) == [c, b, a], (r, proj_order(board))

    # 拖到自己身上：不动，也不算失败
    r = api.move("project", a, a, "after")
    assert r["ok"] and r.get("moved") is False, r

    # ---------- 2. 非法拖拽必须报错，不能默默不动 ----------
    p1 = api.add_project("个人项目", "personal")["id"]
    bad = api.move("project", a, p1, "after")
    assert bad["ok"] is False and "分类" in bad["msg"], bad

    # 置顶的项目固定在列表最前，跨这条线拖拽要明确拒绝
    api.set_field("project", a, "pinned", 1)
    assert proj_order(board)[0] == a, proj_order(board)
    bad = api.move("project", a, b, "after")
    assert bad["ok"] is False and "置顶" in bad["msg"], bad
    api.set_field("project", a, "pinned", 0)

    bad = api.move("project", a, b, "sideways")
    assert bad["ok"] is False and "放置方式" in bad["msg"], bad

    # ---------- 3. 环节同级排序 ----------
    n1 = api.add_node(b, None, "环节1")["id"]
    n2 = api.add_node(b, None, "环节2")["id"]
    n3 = api.add_node(b, None, "环节3")["id"]
    assert kids(board, "project", b) == [n1, n2, n3], kids(board, "project", b)

    r = api.move("node", n3, n1, "before")
    assert r["ok"] and kids(board, "project", b) == [n3, n1, n2], (r, kids(board, "project", b))

    r = api.move("node", n3, n2, "after")
    assert r["ok"] and kids(board, "project", b) == [n1, n2, n3], (r, kids(board, "project", b))

    # ---------- 4. 拖到环节「里面」＝ 变成它的子级 ----------
    sub = api.add_node(b, n2, "子环节")["id"]
    assert kids(board, "node", n2) == [sub], kids(board, "node", n2)

    r = api.move("node", n3, n2, "inside")
    assert r["ok"] and r["parent_id"] == n2, r
    assert kids(board, "node", n2) == [sub, n3], kids(board, "node", n2)
    assert kids(board, "project", b) == [n1, n2], kids(board, "project", b)

    # ---------- 5. 不能拖进自己的后代（会成环） ----------
    bad = api.move("node", n2, n3, "inside")
    assert bad["ok"] is False and "子环节" in bad["msg"], bad
    bad = api.move("node", n2, n3, "after")
    assert bad["ok"] is False and "子环节" in bad["msg"], bad
    bad = api.move("node", n2, n2, "inside")
    assert bad["ok"] and bad.get("moved") is False, bad

    # ---------- 6. 不能跨项目搬环节 ----------
    ext = api.add_node(c, None, "别的项目的环节")["id"]
    bad = api.move("node", n1, ext, "after")
    assert bad["ok"] is False and "别的项目" in bad["msg"], bad

    # ---------- 7. 拖回项目根层 ----------
    r = api.move("node", n3, None, "inside")
    assert r["ok"] and r["parent_id"] is None, r
    assert kids(board, "project", b) == [n1, n2, n3], kids(board, "project", b)
    assert kids(board, "node", n2) == [sub], kids(board, "node", n2)

    # 重新排序不该被当成「有进展」，否则停滞告警会被拖拽刷掉
    before_act = db.one("SELECT last_activity_at AS t FROM projects WHERE id=?", b)["t"]
    api.move("node", n3, n1, "before")
    after_act = db.one("SELECT last_activity_at AS t FROM projects WHERE id=?", b)["t"]
    assert before_act == after_act, (before_act, after_act)

    # ---------- 8. 批量折叠 ----------
    def collapsed():
        p = db.one("SELECT SUM(collapsed) AS n FROM projects")["n"] or 0
        n = db.one("SELECT SUM(collapsed) AS n FROM nodes")["n"] or 0
        return int(p), int(n)

    api.collapse_all("collapse")
    assert collapsed() == (4, 5), collapsed()

    api.collapse_all("expand")
    assert collapsed() == (0, 0), collapsed()

    api.collapse_all("toProject")          # 项目行展开、环节全收
    assert collapsed() == (0, 5), collapsed()

    api.collapse_all("expand")

    # ---------- 9. 快照 ----------
    snap = api.snapshot_now()
    assert snap["ok"] and snap["name"].startswith("board_"), snap

    lst = api.snapshot_list()
    assert lst["ok"] and os.path.isdir(lst["dir"]), lst
    names = [x["name"] for x in lst["items"]]
    assert snap["name"] in names, (snap["name"], names)
    assert all(x["size"] > 0 and x["mtime"] for x in lst["items"]), lst["items"][:2]

    # 同一秒内连打两份，不能互相覆盖（覆盖掉的可能是回滚源）
    s2 = api.snapshot_now()
    assert s2["name"] != snap["name"], (snap, s2)
    assert len(api.snapshot_list()["items"]) >= 2

    # 打快照 → 改数据 → 回滚 → 必须回到快照那一刻
    before_p = proj_order(board)
    before_n = db.one("SELECT COUNT(*) AS n FROM nodes")["n"]
    before_t = sorted((r["id"], r["title"]) for r in db.q("SELECT id,title FROM projects"))

    api.add_project("回滚之后应该消失", "commercial")
    api.add_project("这个也该消失", "commercial")
    assert len(proj_order(board)) == len(before_p) + 2

    res = api.snapshot_restore(snap["name"])
    assert res["ok"], res
    assert res["safety"], res            # 回滚前自动存了一份
    assert res["safety"] != snap["name"], res

    assert proj_order(board) == before_p, (proj_order(board), before_p)
    assert db.one("SELECT COUNT(*) AS n FROM nodes")["n"] == before_n
    assert sorted((r["id"], r["title"]) for r in db.q("SELECT id,title FROM projects")) == before_t
    assert "回滚之后应该消失" not in [r["title"] for r in db.q("SELECT title FROM projects")]

    # 回滚后连接仍然可用（不是把库关坏了）
    assert isinstance(board.stats(), dict)

    # ---------- 10. 回滚的失败路径 ----------
    bad = api.snapshot_restore("board_19990101_000000.sqlite")
    assert bad["ok"] is False and "找不到" in bad["msg"], bad

    junk = api.snapshot_restore("../../board.sqlite")     # 目录穿越要挡掉
    assert junk["ok"] is False, junk

    # 一个不是数据库的假快照，必须拒绝，绝不能拿它覆盖现网数据
    fake = os.path.join(lst["dir"], "board_19990102_000000.sqlite")
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write("这不是数据库")
    bad = api.snapshot_restore(os.path.basename(fake))
    assert bad["ok"] is False and ("不像是" in bad["msg"] or "读不出来" in bad["msg"]), bad
    # 拒绝之后数据必须原封不动
    assert proj_order(board) == before_p, proj_order(board)
    os.unlink(fake)

    # 空文件同样拒绝
    empty = os.path.join(lst["dir"], "board_19990103_000000.sqlite")
    open(empty, "wb").close()
    r = api.snapshot_restore(os.path.basename(empty))
    assert r["ok"] is False, r
    assert proj_order(board) == before_p
    os.unlink(empty)

    db.close()
    print("smoke_m4: OK（拖拽排序 / 非法拖拽拒绝 / 批量折叠 / 快照回滚 全通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
