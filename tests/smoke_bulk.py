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
    before = db.one("SELECT COUNT(*) AS c FROM activity_log")["c"]
    r = board.bulk_update([n1, n2, n3], artist="阿强")
    chk("批量改制作人：done=3", r.get("ok") is True and r.get("done") == 3)
    ar = [db.one("SELECT artist FROM nodes WHERE id=?", i)["artist"] for i in (n1, n2, n3)]
    chk("三条制作人都改成阿强", ar == ["阿强", "阿强", "阿强"])
    after = db.one("SELECT COUNT(*) AS c FROM activity_log")["c"]
    chk("批量改制作人不写活跃流水（整理动作不计数）", after == before)

    # ---- 4. 同时改状态和制作人 ----
    r = board.bulk_update([n3], status="通过", artist="小李")
    chk("一次同时改状态+制作人", r.get("ok") is True and r.get("done") == 1)
    row = db.one("SELECT status,artist FROM nodes WHERE id=?", n3)
    chk("两个字段都生效", row["status"] == "通过" and row["artist"] == "小李")

    # ---- 4b. 白名单外的状态只记流水不计活跃（2026-09-30 新口径） ----
    # 「通过」不在 models.NODE_ACTIVE_STATUS 里；批量和单条必须落同一个数。
    last = db.one("SELECT kind,weight,detail FROM activity_log ORDER BY id DESC LIMIT 1")
    chk("批量改到白名单外的状态（通过）：流水写了、weight=0",
        last["kind"] == "status" and last["weight"] == 0 and "→ 通过" in last["detail"])

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
    # 用一个不存在的状态？状态字段没有约束。改用"中途把表锁死"不现实，
    # 换个办法：传一个 set_field 会拒绝的字段值是行不通的（artist 是自由文本）。
    # 这里直接验证事务边界：故意塞一个会在第二轮炸的输入（把 title 当 id 传不会炸，
    # 因为已经被转 int 过滤）—— 所以用 monkeypatch 让第二回抛错。
    real = board.set_field
    calls = {"n": 0}

    def boom(kind, oid, field, value):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟中途失败")
        return real(kind, oid, field, value)

    board.set_field = boom
    before_st = [db.one("SELECT status FROM nodes WHERE id=?", i)["status"] for i in (n1, n2)]
    r = board.bulk_update([n1, n2], artist="不该留下")
    board.set_field = real
    after_st = [db.one("SELECT status FROM nodes WHERE id=?", i)["status"] for i in (n1, n2)]
    after_ar = [db.one("SELECT artist FROM nodes WHERE id=?", i)["artist"] for i in (n1, n2)]
    chk("中途出错 → ok=False 且报出原因", r.get("ok") is False and r.get("msg"))
    chk("出错时整体回滚（第一条的制作人没留下）", after_ar == [None, None] or
        all(a != "不该留下" for a in after_ar))
    chk("出错时状态也没被带歪", before_st == after_st)

    db.close()
    print()
    print("bulk smoke:", "OK" if not FAILS else "FAILED  " + str(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
