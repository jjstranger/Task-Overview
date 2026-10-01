"""活跃度口径回归：哪些动作计活跃、哪些只记流水。

热力图统计的是 `SUM(weight) WHERE weight>0`（见 models.activity），
所以「不计入活跃」= 权重写 0，**不是不写流水** —— 流水照样留着可查。

口径（2026-09-30 用户定版）：
  计入（W_ACT）：环节流转到 models.NODE_ACTIVE_STATUS 里的状态
                （制作中 / 已提交 / 反馈 / 可优化 / 交付）、打卡
  不计（W_IDLE）：建项目、加环节（单个/批量）、改**任何**项目整体状态、
                环节流转到白名单外的状态（待开始/等上游/暂停/中止/通过）、勾完成

理由：只有「这一下是真的在推进产出」才该在热力图上留痕。把活儿录进看板、
来回切状态、随手勾完成都能刷满的话，「活跃」就不再是产出的度量了。

⚠ 勾完成（把环节置成「通过」）与状态菜单改成「通过」**必须同为不计** ——
两条路落同一个数，否则同一件事从哪儿点会有两种活跃度。
"""
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db import Db  # noqa: E402
from models import (  # noqa: E402
    NODE_ACTIVE_STATUS,
    NODE_STATUS,
    W_ACT,
    W_IDLE,
    Board,
)

# 白名单外的环节状态 —— 跟 NODE_ACTIVE_STATUS 一起必须刚好覆盖整张状态表
NODE_IDLE_STATUS = ("待开始", "等上游", "暂停", "中止", "通过")


def main() -> int:
    assert W_ACT == 1 and W_IDLE == 0, (W_ACT, W_IDLE)

    # 词表改版时最容易漏的一步：白名单里出现了不存在的状态词，静默就永不生效
    for s in NODE_ACTIVE_STATUS:
        assert s in NODE_STATUS, ("白名单里有不存在的环节状态", s)
    assert len(set(NODE_ACTIVE_STATUS)) == len(NODE_ACTIVE_STATUS), "白名单有重复项"
    assert sorted(list(NODE_ACTIVE_STATUS) + list(NODE_IDLE_STATUS)) == sorted(NODE_STATUS), (
        "白名单 + 白名单外必须刚好等于整张环节状态表",
        set(NODE_STATUS) ^ set(NODE_ACTIVE_STATUS) ^ set(NODE_IDLE_STATUS),
    )

    tmp = tempfile.mkdtemp(prefix="board_act_")
    db = Db(os.path.join(tmp, "board.sqlite"))
    db.open()
    board = Board(db)
    db.run("DELETE FROM nodes")
    db.run("DELETE FROM projects")
    db.run("DELETE FROM activity_log")

    def counted():
        """热力图真正会统计进去的总权重。"""
        return int(db.one(
            "SELECT COALESCE(SUM(weight),0) AS w FROM activity_log WHERE weight>0"
        )["w"])

    def last_row():
        return db.one("SELECT kind,weight,detail FROM activity_log ORDER BY id DESC LIMIT 1")

    # ---------- 不计活跃的：整理动作 ----------

    base = counted()
    pid = board.add_project("整理-建项目", "commercial")
    r = last_row()
    assert counted() == base, "建项目不该计活跃"
    assert r["weight"] == 0 and "新建项目" in r["detail"], r   # 流水要留

    stage = board.add_node(pid, None, "整理-加环节")
    assert counted() == base, "加环节不该计活跃"
    assert last_row()["weight"] == 0 and "新增环节" in last_row()["detail"]

    board.add_nodes(pid, stage, ["s001", "s002", "s003"])
    assert counted() == base, "批量加环节不该计活跃"
    assert last_row()["weight"] == 0 and "批量新增" in last_row()["detail"]

    board.set_status("project", pid, "阻塞")
    assert counted() == base, "改项目整体状态不该计活跃"
    assert last_row()["weight"] == 0 and "阻塞" in last_row()["detail"]

    # ---------- 环节流转：白名单里的进、白名单外的出 ----------

    shot = board.add_node(pid, stage, "s004")          # 加环节本身不计
    assert counted() == base

    for s in NODE_ACTIVE_STATUS:
        n0 = counted()
        board.set_status("node", shot, s)
        assert counted() == n0 + 1, ("白名单里的状态该计活跃：", s)
        assert last_row()["weight"] == W_ACT, (s, last_row())

    for s in NODE_IDLE_STATUS:
        n0 = counted()
        board.set_status("node", shot, s)
        assert counted() == n0, ("白名单外的状态不该计活跃：", s)
        row = last_row()
        assert row["weight"] == W_IDLE and s in row["detail"], (s, row)   # 流水要留

    # ---------- 勾完成也不计：它落到的正是「通过」 ----------

    n0 = counted()
    board.set_done(shot, 1)
    assert counted() == n0, "勾完成不该计活跃（落到的「通过」不在白名单里）"
    row = last_row()
    assert row["weight"] == W_IDLE and "完成任务" in row["detail"], row

    # ---------- 打卡照旧计（它跟状态流转无关，是另一条产出线） ----------

    board.checkin([{"id": pid, "title": "整理-建项目"}])
    assert counted() == n0 + 1, "打卡要计活跃"
    assert last_row()["kind"] == "checkin"

    # ---------- 口径一致性：热力图读到的就是这个数 ----------

    act = board.activity(dt.date.today().year)
    assert act["total"] == counted(), (act["total"], counted())
    assert act["days"].get(dt.date.today().isoformat(), 0) == counted(), act["days"]
    assert act["active_days"] == 1 and act["current"] >= 1, act

    # ---------- 不计活跃的也仍可追溯（流水没被删） ----------

    details = [r["detail"] for r in db.q("SELECT detail FROM activity_log ORDER BY id")]
    for kw in ("新建项目", "新增环节", "批量新增 3 个环节", "→ 阻塞", "→ 通过", "完成任务"):
        assert any(kw in d for d in details), (kw, details)

    # weight=0 的那几条整理动作，正好是：
    #   建项目 / 加 stage / 批量加 3 个 / 改项目状态 / 加 s004 共 5 条
    #   + 白名单外 5 个状态 + 勾完成 1 条 = 11
    idle = db.q("SELECT weight FROM activity_log WHERE weight=0")
    assert len(idle) == 11, ("只记流水不计活跃的应当是 11 条", len(idle))

    db.close()
    print("smoke_activity_weight: OK（打卡 或 环节变更为 %s 才计活跃；"
          "建项目 / 加环节 / 改项目状态 / 白名单外状态 / 勾完成 都只记流水）"
          % "/".join(NODE_ACTIVE_STATUS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
