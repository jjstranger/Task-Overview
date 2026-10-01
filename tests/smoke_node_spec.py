"""批量录入子环节的解析回归：分隔符、连续号、字面量保护、上限。

这块最怕的不是「不展开」，而是**多展开了**：`SANTI-OneDay` 被拆成两段、
`shot-001` 突然变成 shot001 —— 名字错了还得一个个改回来。
所以每条「不该展开」的写法都有断言。

跑法：
    <venv>\\Scripts\\python.exe tests\\smoke_node_spec.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import node_spec  # noqa: E402

n = 0


def eq(label, got, want):
    global n
    n += 1
    assert got == want, f"{label}\n  got : {got}\n  want: {want}"


def bad(label, text, needle=""):
    """应该报错（而不是悄悄建出一堆奇怪的名字）。"""
    global n
    n += 1
    try:
        got = node_spec.names(text)
    except ValueError as exc:
        assert needle in str(exc), f"{label}: 报错文案不对：{exc}"
        return
    raise AssertionError(f"{label}: 本该报错，却解析出 {got}")


# ---------- 1. 用户给的例子 ----------
eq("例子 s001,s003A,s006-009",
   node_spec.names("s001,s003A,s006-009"),
   ["s001", "s003A", "s006", "s007", "s008", "s009"])

# ---------- 2. 各种分隔符 ----------
eq("半角逗号", node_spec.names("s001,s002"), ["s001", "s002"])
eq("半角分号", node_spec.names("s001;s002"), ["s001", "s002"])
eq("全角逗号", node_spec.names("s001，s002"), ["s001", "s002"])
eq("全角分号", node_spec.names("s001；s002"), ["s001", "s002"])
eq("顿号", node_spec.names("s001、s002"), ["s001", "s002"])
eq("空格", node_spec.names("s001 s002"), ["s001", "s002"])
eq("换行 / Tab", node_spec.names("s001\ns002\ts003"), ["s001", "s002", "s003"])
eq("混着来 + 多余空白", node_spec.names("  s001 , ; s002  "), ["s001", "s002"])

# ---------- 3. 连续号 ----------
eq("同宽", node_spec.names("s006-009"), ["s006", "s007", "s008", "s009"])
eq("两边都带前缀", node_spec.names("C001-C003"), ["C001", "C002", "C003"])
eq("右侧短写继承前缀", node_spec.names("s006-9"), ["s006", "s007", "s008", "s009"])
eq("倒序", node_spec.names("s09-s06"), ["s09", "s08", "s07", "s06"])
eq("纯数字", node_spec.names("001-003"), ["001", "002", "003"])
eq("带下划线的长前缀", node_spec.names("EP01_S001_C001-C003"),
   ["EP01_S001_C001", "EP01_S001_C002", "EP01_S001_C003"])
eq("前缀大小写不同也认", node_spec.names("C001-c003"), ["C001", "C002", "C003"])
eq("无补零", node_spec.names("s1-3"), ["s1", "s2", "s3"])
eq("全角减号", node_spec.names("s006\uff0d009"),
   ["s006", "s007", "s008", "s009"])
eq("短破折号 / 长破折号", node_spec.names("s006\u2013s009"), ["s006", "s007", "s008", "s009"])
eq("连续号夹在中间一起用", node_spec.names("s001,s006-008,s010"),
   ["s001", "s006", "s007", "s008", "s010"])

# ---------- 4. 不该被展开的（字面量保护）----------
eq("SANTI-OneDay：左边没有数字尾巴", node_spec.names("SANTI-OneDay"), ["SANTI-OneDay"])
eq("shot-001：左边没有数字尾巴", node_spec.names("shot-001"), ["shot-001"])
eq("s001_alpha：右边不是数字结尾", node_spec.names("s001_alpha"), ["s001_alpha"])
eq("EP01-EP02_v2：右边不是数字结尾", node_spec.names("EP01-EP02_v2"), ["EP01-EP02_v2"])
eq("两个破折号（日期）", node_spec.names("2026-09-25"), ["2026-09-25"])
eq("前缀不同 → 字面量", node_spec.names("A001-B003"), ["A001-B003"])
eq("两端相同 → 字面量", node_spec.names("s007-007"), ["s007-007"])
eq("引号强制字面量", node_spec.names('"phase1-3"'), ["phase1-3"])
eq("单引号也认", node_spec.names("'phase1-3'"), ["phase1-3"])
eq("中文名照常", node_spec.names("建模,绑定,渲染"), ["建模", "绑定", "渲染"])

# 顺带记录：这类写法**会被**展开（不是 bug，是约定；界面里有实时预览兜底）
eq("phase1-3 会被当连续号", node_spec.names("phase1-3"), ["phase1", "phase2", "phase3"])

# ---------- 5. 去重与上限 ----------
eq("大小写重复只留一个", node_spec.names("s001,S001,s001"), ["s001"])
eq("重复的连续号", node_spec.names("s001-s003,s002"), ["s001", "s002", "s003"])

bad("空文本", "   ")
bad("只有分隔符", " , ; ")
bad("单个连续号跨太大", "s001-900", "跨度太大")
bad("总量超上限", ",".join(f"s{i:03d}" for i in range(1, 520)), "最多建")

# ---------- 6. 预览接口 ----------
pv = node_spec.preview("s001,s003A,s006-009")
assert pv["ok"] is True and pv["count"] == 6, pv
assert pv["items"][-1] == "s009", pv

bad_pv = node_spec.preview("  ")
assert bad_pv["ok"] is False and bad_pv["count"] == 0 and bad_pv["msg"], bad_pv

# ---------- 7. 与 API 的连接（跳过的逻辑在 api.add_nodes）----------
import tempfile  # noqa: E402

from api import Api  # noqa: E402
from db import Db  # noqa: E402
from models import Board  # noqa: E402

tmp = tempfile.mkdtemp(prefix="board_spec_")
db = Db(os.path.join(tmp, "board.sqlite"))
db.open()
api = Api(Board(db), db, {})
for t in ("finance", "links", "nodes", "projects", "clients", "activity_log"):
    db.run(f"DELETE FROM {t}")

pid = api.add_project("解析测试项目", "commercial")["id"]
r = api.add_nodes(pid, None, "s001,s003A,s006-009")
eq("API 批量建出 6 个", (r["ok"], r["count"], r["created"][-1]),
   (True, 6, "s009"))

r2 = api.add_nodes(pid, None, "s001,s003A,s006-009")
eq("再提交同一批 → 全部跳过", (r2["count"], len(r2["skipped"])), (0, 6))
eq("库里仍然只有 6 个",
   len(api._board.child_titles(pid, None)), 6)

r3 = api.add_nodes(pid, None, "s006-008,s099")
eq("已存在 3 个 + 新 1 个", (r3["count"], len(r3["skipped"])), (1, 3))
eq("结果是 s099", r3["created"], ["s099"])

r4 = api.add_nodes(pid, None, "   ")
eq("空文本要拒绝", r4["ok"], False)

r5 = api.add_nodes(pid, None, "s010-950")
eq("跨度太大要拒绝", r5["ok"], False)

# 大小写不同也算已存在（Windows 上本来就是同一个名字）
r6 = api.add_nodes(pid, None, "S001")
eq("大小写重复也跳过", (r6["count"], len(r6["skipped"])), (0, 1))

db.close()
print(f"node spec smoke: OK（{n} 项断言）")
