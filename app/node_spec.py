"""子环节的批量录入解析：把一行文本拆成一批名称。

支持的写法
----------

    分隔符   半角/全角逗号、分号、顿号、空格、Tab、换行
             `s001,s003A` `s001；s003A` `s001、s003A` `s001 s003A`
    连续号   `-`（也认全角与长短破折号）连接两个**带数字尾巴**的写法

例：

    s001,s003A,s006-009   →  s001  s003A  s006  s007  s008  s009
    C001-C003             →  C001  C002  C003
    EP01_S001_C001-C005   →  EP01_S001_C001 … EP01_S001_C005
    s09-s06               →  s09  s08  s07  s06        （倒序也认）
    001-003               →  001  002  003             （纯数字也认）

只有当**两边都是「前缀 + 数字」，且右边的前缀是左边前缀的后缀（或不写）**时，
才当成连续号展开。这样这些常见的名字不会被误伤，仍然是原样一个字面名：

    SANTI-OneDay     左边 SANTI 没有数字尾巴 → 字面量
    shot-001         同上 → 字面量
    s001_alpha       右边不是数字结尾 → 字面量
    EP01-EP02_v2     右边不是数字结尾 → 字面量
    2026-09-25       两个破折号 → 字面量
    A001-B003        右边前缀 B 不是左边前缀 A 的后缀 → 字面量
    "phase1-3"       引号包起来 = 强制字面量（想建就叫这个名字时用它）

（`phase1-3` 这种**不带引号**的写法两边数字宽度一样，会被当成连续号展开成
phase1/phase2/phase3 —— 这是有歧义的写法，界面里有实时预览，看一眼就知道。）

点「创建」之前，界面会实时把这行解析结果显示出来，
所以真拿不准的时候，看一眼预览就行，不用记规则。
"""
from __future__ import annotations

import re

# 分隔符：半角/全角逗号分号、顿号、各种空白
SEP = re.compile(r"[,;，；、\s]+")
# 把一个 token 拆成「前缀 + 数字尾巴」
TAIL = re.compile(r"(.*?)(\d+)\Z")

# 破折号家族都当连续号用（全角减号、短破折号、长破折号）
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\uff0d"
# 引号包起来表示「就要这个字面名字，别展开」
QUOTES = "\"'“”‘’"

MAX_ITEMS = 500     # 一次最多建这么多，防手滑写成 1-99999
MAX_SPAN = 200      # 单个连续号最多展开这么多项


def _unquote(tok: str) -> str | None:
    """`"phase1-3"` → `phase1-3`；没被引号包住则返回 None。"""
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in QUOTES:
        return tok[1:-1].strip()
    return None


def expand(token: str) -> list[str]:
    """展开单个 token；不是连续号写法就原样返回。"""
    literal = _unquote(token)
    if literal is not None:
        return [literal] if literal else []

    # 破折号统一成 ASCII 的 - 再判断，避免半角/全角混着写认不出来
    for dash in DASHES:
        token = token.replace(dash, "-")
    if token.count("-") != 1:
        return [token]

    left, right = token.split("-")
    ml, mr = TAIL.fullmatch(left), TAIL.fullmatch(right)
    if not ml or not mr:
        return [token]
    pre_l, num_l = ml.group(1), ml.group(2)
    pre_r, num_r = mr.group(1), mr.group(2)
    # 右边的前缀要么不写（继承左边），要么是左边前缀的后缀 —— 这样才能认
    # `EP01_S001_C001-C005`（只有最后一段编号在变），又不会把 `A001-B003` 认成范围
    if pre_r and not pre_l.lower().endswith(pre_r.lower()):
        return [token]

    width = max(len(num_l), len(num_r))
    a, b = int(num_l), int(num_r)
    if b - a == 0:
        return [token]                      # s007-007 这种没意义，当字面量
    span = abs(b - a) + 1
    if span > MAX_SPAN:
        raise ValueError(f"连续号跨度太大：{token}（{span} 项，上限 {MAX_SPAN}）")
    step = 1 if b > a else -1
    return [pre_l + str(i).zfill(width) for i in range(a, b + step, step)]


def names(text: str, limit: int = MAX_ITEMS) -> list[str]:
    """整行文本 → 名称列表。重复的（不分大小写）只留第一个。

    解析不出来 / 超出上限时抛 ValueError，消息直接给用户看。
    """
    raw = [t for t in SEP.split(str(text or "").strip()) if t]
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        for name in expand(tok):
            key = name.strip().lower()
            if not name.strip():
                continue
            if key in seen:
                continue
            if len(out) >= limit:
                raise ValueError(f"一次最多建 {limit} 个，这批太多了")
            seen.add(key)
            out.append(name.strip())
    if not out:
        raise ValueError("没解析出任何名称")
    return out


def preview(text: str) -> dict:
    """给前端实时预览用：解析失败不抛异常，走 {"ok": False, "msg": ...}。"""
    try:
        items = names(text)
    except ValueError as exc:
        return {"ok": False, "msg": str(exc), "items": [], "count": 0}
    return {"ok": True, "items": items, "count": len(items), "msg": ""}
