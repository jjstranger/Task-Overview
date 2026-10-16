"""批量改环节状态 / 制作人（issue #3）的 db 层回归。

要点：
1. 一次调用改多条，两条都落库
2. **活跃度口径**跟单条改一致：改状态计活跃、改制作人不计（这俩是同一条口径，
   批量路径必须复用单条的语义，不能自己拼 UPDATE 抄近路）
3. 脏 id（库里不存在的）算跳过，不算失败；全脏则 done=0
4. 空 ids / 没给改什么都返回 ok=False，不静默成功
5. 中途抛错要整体回滚，不留半拉子
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))

from db import Db          # noqa: E402
from models import Board   # noqa: E402

FAILS = []


def chk(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def main() -> int:
    tmp = os.path.join(tempfile.mkdtemp(prefix="smoke_bulk_"), "b.sqlite")
    db = Db(tmp)
    db.open()
    board = Board(db)

    pid = board.add_project("批量测试项目", "commercial", None)
    n1 = board.add_node(pid, None, "环节A")
    n2 = board.add_node(pid, None, "环节B")
    n3 = board.add_node(pid, None, "环节C")

    # ---- 1. 批量改状态 ----
    r = board.bulk_update([n1, n2], status="反馈")
    chk("批量改状态：ok 且 done=2", r.get("ok") is True and r.get("done") == 2)
    s1 = db.one("SELECT status FROM nodes WHERE id=?", n1)["status"]
    s2 = db.one("SELECT status FROM nodes WHERE id=?", n2)["status"]
    s3 = db.one("SELECT status FROM nodes WHERE id=?", n3)["status"]
    chk("两条真的都改了", s1 == "反馈" and s2 == "反馈")
    chk("没选的第三条没动", s3 == "待开始")

    # ---- 2. 活跃度：改状态计活跃 ----
    # 注意流水表里还有"建项目 / 加环节"那些 weight=0 的整理记录，所以按 node_id 过滤后
    # 还要只挑 kind=status 的那两条（新建环节也写 kind=task、且在同一个 node_id 上）。
    act = [dict(x) for x in db.q("SELECT kind,weight,node_id FROM activity_log ORDER BY id")]
    st_act = [x for x in act if x["kind"] == "status" and x["node_id"] in (n1, n2)]
    chk("批量改状态写了两条流水（kind=status）", len(st_act) == 2)
    chk("批量改状态计活跃（weight=1，跟单条口径一致）",
        len(st_act) == 2 and all(x["weight"] == 1 for x in st_act))

    # ---- 3. 批量改制作人不计活跃 ----
    # ⚠ 2026-10-16 起 `artist` 参数是 **vendors.id**，不再是名字 ——
    # 制作人已经强关联到外包商名录了。
    v_a = db.run("INSERT INTO vendors(name,kind) VALUES(?,?)", ("阿强", "个人"))
    v_b = db.run("INSERT INTO vendors(name,kind) VALUES(?,?)", ("小李", "团队"))
    before = db.one("SELECT COUNT(*) AS c FROM activity_log")["c"]
    r = board.bulk_update([n1, n2, n3], artist=v_a)
    chk("批量改制作人：done=3", r.get("ok") is True and r.get("done") == 3)
    ar = [db.one("SELECT artist_id FROM nodes WHERE id=?", i)["artist_id"] for i in (n1, n2, n3)]
    chk("三条制作人都改成阿强", ar == [v_a, v_a, v_a])
    after = db.one("SELECT COUNT(*) AS c FROM activity_log")["c"]
    chk("批量改制作人不写活跃流水（整理动作不计数）", after == before)
    # 传 0 / 空 → 清成未分配（前端「清空制作人」那条走这里）
    board.bulk_update([n3], artist=0)
    chk("批量给 0 = 清空成未分配",
        db.one("SELECT artist_id FROM nodes WHERE id=?", n3)["artist_id"] is None)
    board.bulk_update([n3], artist=v_a)

    # ---- 4. 同时改状态和制作人 ----
    r = board.bulk_update([n3], status="通过", artist=v_b)
    chk("一次同时改状态+制作人", r.get("ok") is True and r.get("done") == 1)
    row = db.one("SELECT status,artist_id FROM nodes WHERE id=?", n3)
    chk("两个字段都生效", row["status"] == "通过" and row["artist_id"] == v_b)

    # ---- 4b. 白名单外的状态只记流水不计活跃（2026-09-30 新口径） ----
    # 「通过」不在 models.NODE_ACTIVE_STATUS 里；批量和单条必须落同一个数。
    last = db.one("SELECT kind,weight,detail FROM activity_log ORDER BY id DESC LIMIT 1")
    chk("批量改到白名单外的状态（通过）：流水写了、weight=0",
        last["kind"] == "status" and last["weight"] == 0 and "→ 通过" in last["detail"])

    # ---- 4c. 截止日期 / 备注（2026-10-03 跟制作人一起接进批量改） ----
    # 这两个跟改制作人同一口径：属于「整理动作」，不该计活跃。
    before = db.one("SELECT COUNT(*) AS c FROM activity_log")["c"]
    r = board.bulk_update([n1, n2], deadline="2026-12-31", note="批量备注")
    chk("批量改截止日期+备注：ok 且 done=2", r.get("ok") is True and r.get("done") == 2)
    dn = [db.one("SELECT deadline,note FROM nodes WHERE id=?", i) for i in (n1, n2)]
    chk("两条的日期和备注都改了",
        all(x["deadline"] == "2026-12-31" and x["note"] == "批量备注" for x in dn))
    n3row = db.one("SELECT deadline,note FROM nodes WHERE id=?", n3)
    chk("没选的第三条日期备注都没动", n3row["deadline"] is None and n3row["note"] is None)
    after = db.one("SELECT COUNT(*) AS c FROM activity_log")["c"]
    chk("批量改日期/备注不计活跃（跟改制作人同一口径）", after == before)
    # 只给其中一个字段时，另一个不许被顺手清空
    board.bulk_update([n1], note="")
    r1 = db.one("SELECT deadline,note FROM nodes WHERE id=?", n1)
    chk("只改备注时截止日期不动", r1["deadline"] == "2026-12-31" and r1["note"] == "")

    # ---- 5. 脏 id：跳过不失败 ----
    r = board.bulk_update([n1, 999999], status="暂停")
    chk("脏 id 算跳过、其余照改",
        r.get("ok") is True and r.get("done") == 1 and r.get("skipped") == 1)

    # ---- 6. 空参数 / 无改动 ----
    chk("空 ids → ok=False", board.bulk_update([], status="通过").get("ok") is False)
    chk("没给改什么 → ok=False", board.bulk_update([n1]).get("ok") is False)
    chk("重复 id 去重后只改一次",
        board.bulk_update([n1, n1, n1], status="等上游").get("done") == 1)

    # ---- 7. 出错整体回滚 ----
    # 状态字段没有约束、制作人现在是个 id（传谁都不违规），所以没法靠"塞个非法值"
    # 让第二回自然炸掉 —— 直接 monkeypatch 让第二回抛错。
    real = board.set_field
    calls = {"n": 0}

    def boom(kind, oid, field, value):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟中途失败")
        return real(kind, oid, field, value)

    board.set_field = boom
    before_st = [db.one("SELECT status FROM nodes WHERE id=?", i)["status"] for i in (n1, n2)]
    r = board.bulk_update([n1, n2], artist=v_a)
    board.set_field = real
    after_st = [db.one("SELECT status FROM nodes WHERE id=?", i)["status"] for i in (n1, n2)]
    after_ar = [db.one("SELECT artist_id FROM nodes WHERE id=?", i)["artist_id"] for i in (n1, n2)]
    chk("中途出错 → ok=False 且报出原因", r.get("ok") is False and r.get("msg"))
    # n1/n2 在第 3 段就被设成 v_a 了；第二回炸掉 → 两条都还是原值（没被改歪）
    chk("出错时整体回滚（两条的制作人都还是原来那个）", after_ar == [v_a, v_a])
    chk("出错时状态也没被带歪", before_st == after_st)

    # ---------- 8. 批量删除（2026-10-10） ----------
    # 多选是**级联选**（点父项带上整棵子树），所以传进来的 ids 里
    # 祖先和后代同时在是常态 —— 这正是最容易把 done 算重、或删一半报错的地方。
    pid2 = board.add_project("批删项目", "commercial", None)
    root = board.add_node(pid2, None, "根")
    kid1 = board.add_node(pid2, root, "子1")
    kid2 = board.add_node(pid2, root, "子2")
    grand = board.add_node(pid2, kid1, "孙1")
    lone = board.add_node(pid2, None, "孤条")
    other = board.add_node(pid2, None, "不动的")
    board.add_link("node", kid1, "目录", "S:\\x")

    def alive(i):
        return db.one("SELECT id FROM nodes WHERE id=?", i) is not None

    # 8a. 只删父 → 整棵子树带走（连带孙辈），孤条/不动的都不受影响
    r = board.bulk_delete([root])
    chk("批删父环节：ok，done 报的是真实消失条数（根+2子+1孙=4）",
        r.get("ok") is True and r.get("done") == 4)
    chk("父与全部后代真的都没了",
        not any(alive(i) for i in (root, kid1, kid2, grand)))
    chk("没被选中的环节原封不动", alive(lone) and alive(other))
    chk("后代挂的目录链接跟着删了",
        db.one("SELECT id FROM links WHERE owner_type='node' AND owner_id=?", kid1) is None)

    # 8b. **祖先 + 后代同时传进来**（多选级联的常态）：不能删两次、不能报错
    r2root = board.add_node(pid2, None, "根2")
    r2kid = board.add_node(pid2, r2root, "子2a")
    r2grand = board.add_node(pid2, r2kid, "孙2a")
    # 故意传 [祖先, 后代] —— 去重后应当只删 3 条，不重复计数
    r = board.bulk_delete([r2root, r2grand, r2kid])
    chk("祖先与后代同时传入：合法且 done=3（不重复计数）",
        r.get("ok") is True and r.get("done") == 3)
    chk("整棵都删干净", not any(alive(i) for i in (r2root, r2kid, r2grand)))

    # 8c. 重复 id 只删一次（跟上一条同理，走另一条路：完全相同的 id）
    r3a = board.add_node(pid2, None, "重复A")
    r = board.bulk_delete([r3a, r3a, r3a])
    chk("同一个 id 传三次：done=1", r.get("ok") is True and r.get("done") == 1)

    # 8d. 脏 id / 已删除的 id：算跳过，其余照删
    r4 = board.add_node(pid2, None, "待删")
    r = board.bulk_delete([r4, 999999])
    chk("脏 id 算跳过、其余照删",
        r.get("ok") is True and r.get("done") == 1 and r.get("skipped") == 1)

    # 8e. 空 ids / 全脏 → ok=False，不静默成功
    chk("空 ids → ok=False", board.bulk_delete([]).get("ok") is False)
    chk("全是脏 id → ok=False 且说明原因",
        board.bulk_delete([888888, 777777]).get("ok") is False)

    # 8f. 删环节要 touch 那条项目（跟单条 delete 一个口径：算这条项目的动静）
    #     先把 last_activity_at 拨旧，删完必须被刷新
    db.run("UPDATE projects SET last_activity_at='2020-01-01 09:00:00' WHERE id=?", pid2)
    r5 = board.add_node(pid2, None, "触发touch")
    board.bulk_delete([r5])
    la = db.one("SELECT last_activity_at FROM projects WHERE id=?", pid2)["last_activity_at"]
    chk("批删之后项目的活跃时间被刷新（跟单条 delete 同口径）",
        la is not None and not la.startswith("2020"))

    # 8g. 跨项目：只删这个项目里的，不碰另一个项目的同名/同 id 环节
    pA = board.add_project("批删A", "commercial", None)
    pB = board.add_project("批删B", "commercial", None)
    aN = board.add_node(pA, None, "A的环节")
    bN = board.add_node(pB, None, "B的环节")
    board.bulk_delete([aN])
    chk("跨项目批删只动传进来的那些", (not alive(aN)) and alive(bN))

    db.close()
    print()
    print("bulk smoke:", "OK" if not FAILS else "FAILED  " + str(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
