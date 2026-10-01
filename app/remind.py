"""打卡调度、停滞与到期告警、系统通知。

调度跑在**独立后台线程**里：通知走 `state["notify"]`（托盘气泡），
拉前台走 `state["front"]`，直接调前端走 `evaluate_js`。
⚠ 顺序不能反 —— 绝不能在 GUI 事件回调（events.shown / closing）里调 evaluate_js，会死锁；
这两个回调是 main.py 注册的普通函数，从本线程调用才安全。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

TICK = 30           # 巡检间隔（秒）
BOOT_DELAY = 60     # 启动后首次告警的静默期（秒）


class Reminder:
    def __init__(self, board, db, state: dict):
        self._board = board
        self._db = db
        self._state = state      # window / notify / front
        self._stop = threading.Event()
        self._boot = time.time()
        self._nag_at = 0.0
        self._alert_at = 0.0

    # ---------- 生命周期 ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK):
            try:
                self._tick()
            except Exception:
                pass

    # ---------- 巡检 ----------

    def _tick(self) -> None:
        s = self._db.all_settings()
        t = time.time()
        level = self._int(s.get("remind_level"), 2)

        if not self._board.checked_in_today() and self._past(s.get("checkin_time") or "09:00"):
            if t >= self._nag_at:
                self._fire_checkin(level)
                self._nag_at = t + max(5, self._int(s.get("nag_minutes"), 30)) * 60

        if s.get("notify_enabled", "1") == "1" and t - self._boot > BOOT_DELAY and t >= self._alert_at:
            self._fire_alerts()
            self._alert_at = t + max(30, self._int(s.get("alert_interval"), 180)) * 60

    # ---------- 动作 ----------

    def _fire_checkin(self, level: int) -> None:
        if level <= 1:
            self._notify("该打卡了", "今天还没确认要推进的项目")
            return
        front = self._state.get("front")
        if front:
            try:
                front()
            except Exception:
                pass
        w = self._state.get("window")
        if w:
            try:
                w.evaluate_js(f"window.__openCheckin && window.__openCheckin({level})")
            except Exception:
                pass

    def _fire_alerts(self) -> None:
        a = self._board.alerts()
        if not a["total"]:
            return
        parts = []
        if a["stale"]:
            names = "、".join(x["title"] for x in a["stale"][:3])
            if len(a["stale"]) > 3:
                names += f" 等 {len(a['stale'])} 项"
            parts.append("停滞：" + names)
        if a["due"]:
            names = "、".join(x["title"] + " " + x["detail"] for x in a["due"][:3])
            if len(a["due"]) > 3:
                names += f" 等 {len(a['due'])} 项"
            parts.append("临期：" + names)
        self._notify(f"{a['total']} 项需要关注", " ｜ ".join(parts))

    def _notify(self, title: str, msg: str) -> None:
        cb = self._state.get("notify")
        if cb:
            try:
                cb(title, msg)
            except Exception:
                pass

    # ---------- 工具 ----------

    @staticmethod
    def _int(v, default: int) -> int:
        """设置项读出脏值（空串 / 手改过非数字）时退回默认。

        直接用 `int()` 会抛异常，而 `_loop` 的兜底是「这轮不跑了」——
        于是提醒永远不再触发、界面上又没有任何线索（静默失效最难查）。
        """
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _past(hhmm: str) -> bool:
        try:
            h, m = str(hhmm).split(":")
            now = datetime.now()
            return now >= now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        except Exception:
            return False
