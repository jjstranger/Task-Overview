"""暴露给前端的 JS API。

注意：pywebview 会把对象的**公开属性**也序列化进 JS 桥，
所以内部引用一律用下划线前缀，且不能挂 window/db 等不可序列化对象。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

import webview

import node_spec
import opener
import paths
import version
from finance import EXTS, SECTION_CN, Finance, parse_money


def _pick_log(msg: str) -> None:
    """选择文件夹对话框出问题时留证据。

    这类失败以前是**静默**的（异常被吞成一个前端不看的 ok:False），
    表现就是「点浏览没反应」，事后完全查不出原因。
    """
    paths.log_line("pick.log", msg)


# 系统「另存为」对话框的筛选器：格式 → file_types。
# ⚠ 字符串必须是 webview 认的 `说明 (*.ext)` 形式，否则 parse_file_type 直接抛
# ValueError（说明里的中文没问题，正则用的是 \w）。
EXPORT_FILTERS = {
    "xlsx": ("Excel 工作簿 (*.xlsx)",),
    "csv": ("CSV 文本 (*.csv)",),
    "json": ("JSON 文件 (*.json)",),
}


def _ensure_ext(path: str, ext: str) -> str:
    """让返回的路径「扩展名就是用户刚选的格式」。

    SaveFileDialog 会按筛选器补扩展名，但 pywebview 没设 DefaultExt，不保证；
    用户也可能自己敲一个别的（选了 Excel 却叫 `报表.txt`）。这里以**格式为准**
    换掉扩展名 —— 存出一个后缀是 .txt 的 xlsx，双击打不开还找不到原因。
    """
    root, e = os.path.splitext(path)
    return path if e.lower() == ext.lower() else root + ext


# "路径能不能用"的判断实现在 paths.py —— 引导页（boot.py）也要用同一套，
# 各写一份的话两边对"什么算可用"迟早会出现分歧。
_has_db = paths.has_db_file
_dir_writable = paths.dir_writable


class Api:
    def __init__(self, board, db, state: dict):
        self._board = board
        self._db = db
        self._state = state
        self._fin = Finance(db)

    # ---------- 系统对话框 ----------

    def _pick(self, dialog, start, default_dir, **kw):
        """弹一个系统选择对话框，返回 `{"ok":..,"path":..}`（取消 = path 空串）。

        `pick_dir` / `pick_db_file` 除了对话框类型和起始目录之外完全一样，
        以前是两段几乎逐行重复的代码 —— 其中一段修了 bug 另一段没跟上，
        就出现"选文件夹好使、选文件没反应"这种最难查的不一致。

        ⚠ pywebview 6.x 把 `create_file_dialog` 从模块级挪到了 Window 上，
        `webview.create_file_dialog` **已经不存在了**。老写法抛 AttributeError，
        异常又被吞成 `{"ok": False}` 而前端当时不看返回值 —— 表现就是
        「点浏览没反应」。所以这里走 Window 上的接口，并把失败原因报出去。
        """
        win = self._state.get("window")
        if win is None:
            _pick_log(f"窗口未就绪，{dialog} 被调用")
            return {"ok": False, "msg": "窗口还没就绪，稍后再试"}
        d = str(start or "").strip()
        if not d or not os.path.isdir(d):
            d = default_dir
        try:
            res = win.create_file_dialog(dialog, directory=d, **kw)
        except Exception as exc:
            _pick_log(f"create_file_dialog 失败：{exc!r}")
            return {"ok": False, "msg": "打不开选择窗口：" + str(exc)}
        if not res:
            return {"ok": True, "path": ""}          # 用户取消，不是错误
        first = res[0] if isinstance(res, (list, tuple)) else res
        return {"ok": True, "path": str(first)}

    def pick_dir(self, start=""):
        """选一个文件夹。没给有效起点就落到用户主目录。"""
        return self._pick(webview.FileDialog.FOLDER, start, os.path.expanduser("~"))

    def pick_db_file(self, start=""):
        """选一个库文件（.sqlite / .db）。"""
        d = os.path.dirname(os.path.abspath(self._db.path)) or os.path.expanduser("~")
        # 同目录下可能一个库都没有，退到上一级，别让对话框落在空目录里
        if not _has_db(os.path.dirname(os.path.abspath(self._db.path)) or "."):
            d = os.path.dirname(d) or d
        return self._pick(webview.FileDialog.OPEN, start, d,
                          allow_multiple=False, file_types=paths.DB_FILTER)

    # ---------- 数据 ----------

    def load(self, include_archived=0):
        """`include_archived` 由项目页的筛选菜单给：默认 0，归档项目不发往前端。"""
        data = self._board.load(include_archived=bool(include_archived))
        # 让前端知道要不要做一次自检巡视（--smoke 时）
        data["diag"] = bool(self._state.get("diag"))
        # 多机同时打开：前端要据此决定跑不跑"别人改过数据没"的轮询
        data["share_db"] = bool(paths.share_db())
        # 产品名与版本号（标题栏、顶栏、设置页的版本行都用它，
        # 前端一个版本号都不写死 —— 以后升版本只改 app/version.py）
        data["app"] = version.info()
        return data

    def add_project(self, title, category="", client=None, path="",
                    money=None):
        """新建项目，可顺手登记合同额与首款（money 是可选的 dict）。

        `client` 既可以是客户 id，也可以是**客户名**：名字在库里没有就自动建一个。
        这样新建项目时那个客户框直接敲新名字就能用，不用先跑去财务页建客户。

        金额校验放在建项目**之前**：填错了就整体拒绝，不会留下一个没有款项的
        半成品项目让人以为已经记上了。
        """
        t = str(title).strip()
        if not t:
            return {"ok": False, "msg": "名称不能为空"}
        try:
            cid = self._client_id(client)
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        m = money or {}
        try:
            contract = parse_money(m.get("contract"), "合同额")
            paid = parse_money(m.get("paid"), "首款到账")
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        ccy = str(m.get("currency") or "CNY").strip() or "CNY"
        tax = str(m.get("tax_rate") or "").strip()
        dt = str(m.get("date") or "").strip()

        pid = self._board.add_project(t, category, cid)
        if path:
            self._board.add_link("project", pid, "项目根目录", path)

        made = []
        if contract > 0:
            self._fin.add({"project_id": pid, "kind": "contract", "amount": contract,
                           "currency": ccy, "tax_rate": tax, "date": dt,
                           "status": "有效"})
            made.append("contract")
        if paid > 0:
            self._fin.add({"project_id": pid, "kind": "payment", "amount": paid,
                           "currency": ccy, "tax_rate": tax, "date": dt,
                           "status": "已收", "note": "首款"})
            made.append("payment")
        return {"ok": True, "id": pid, "money": made, "client_id": cid or 0}

    def _client_id(self, client) -> int | None:
        """把「客户 id / 客户名 / 空」统一成一个 id（名字不存在则新建）。"""
        if client is None:
            return None
        s = str(client).strip()
        if not s or s == "0":
            return None
        if s.isdigit():
            return int(s)
        return self._fin.client_find_or_create(s)

    def set_client(self, project_id, client):
        """改项目的客户；`client` 可以是 id、名字（不存在则新建）或空（摘掉客户）。"""
        try:
            cid = self._client_id(client)
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        self._board.set_field("project", int(project_id), "client_id", cid)
        return {"ok": True, "client_id": cid or 0}

    def add_node(self, project_id, parent_id, title):
        if not str(title).strip():
            return {"ok": False, "msg": "名称不能为空"}
        nid = self._board.add_node(int(project_id), parent_id or None, str(title).strip())
        return {"ok": True, "id": nid}

    def add_nodes(self, project_id, parent_id, spec):
        """批量新增子环节：`s001,s003A,s006-009` → 6 个环节。

        已有的同名子环节会被跳过（而不是再建一个重复的），结果一并回给前端。
        """
        if not str(spec or "").strip():
            return {"ok": False, "msg": "名称不能为空"}
        try:
            want = node_spec.names(spec)
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        have = {
            str(t).strip().casefold()
            for t in self._board.child_titles(project_id, parent_id)
        }
        todo = [t for t in want if t.casefold() not in have]
        skipped = [t for t in want if t.casefold() in have]
        if todo:
            self._board.add_nodes(int(project_id), parent_id or None, todo)
        return {"ok": True, "created": todo, "skipped": skipped, "count": len(todo)}

    def parse_nodes(self, spec):
        """给界面做实时预览：这行文字会变成哪些名字。"""
        return node_spec.preview(spec)

    def rename(self, kind, oid, title):
        if not str(title).strip():
            return {"ok": False, "msg": "名称不能为空"}
        self._board.rename(kind, int(oid), str(title).strip())
        return {"ok": True}

    def set_status(self, kind, oid, status):
        self._board.set_status(kind, int(oid), status)
        return {"ok": True}

    def set_done(self, oid, done):
        self._board.set_done(int(oid), int(done))
        return {"ok": True}

    def set_collapsed(self, kind, oid, val):
        self._board.set_collapsed(kind, int(oid), int(val))
        return {"ok": True}

    def collapse_all(self, mode="collapse"):
        """批量展开/折叠整棵树。mode: expand / collapse / toProject"""
        self._board.set_collapsed_all(str(mode))
        return {"ok": True}

    def move(self, kind, oid, target_id, pos="after"):
        """拖拽落位。参数校验失败要回给前端，不能让它静默失败。"""
        try:
            return self._board.move(kind, oid, target_id, pos)
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}

    def set_field(self, kind, oid, field, value):
        try:
            self._board.set_field(kind, int(oid), field, value)
        except ValueError as exc:
            # 字段名不在白名单时（前端和后端不同步）要把原因带出去，别静默失败
            return {"ok": False, "msg": str(exc)}
        return {"ok": True}

    def bulk_update(self, kind, ids, status=None, artist=None,
                    deadline=None, note=None):
        """多选批量改。目前只有环节支持（项目层用不着，也没有"环节制作人"那套）。

        四个字段一次都可以给多个，但界面上是一次改一样（菜单里选一个值）。
        """
        if str(kind) != "node":
            return {"ok": False, "done": 0, "skipped": 0, "msg": "只支持批量改环节"}
        return self._board.bulk_update(ids, status=status, artist=artist,
                                       deadline=deadline, note=note)

    def delete(self, kind, oid):
        self._board.delete(kind, int(oid))
        return {"ok": True}

    def add_link(self, owner_type, owner_id, label, path):
        self._board.add_link(owner_type, int(owner_id), str(label).strip(), str(path).strip())
        return {"ok": True}

    def delete_link(self, oid):
        self._board.delete_link(int(oid))
        return {"ok": True}

    # ---------- 分类（分组） ----------
    # 分类现在存在库里，可增删改（2026-09-30）。删是**只让删空筐** ——
    # 还有项目挂在上面就拒绝，并说明几个项目，别让一个手滑端掉一筐。

    def group_add(self, name):
        try:
            key = self._board.group_add(str(name or "").strip())
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True, "key": key}

    def group_rename(self, key, name):
        try:
            self._board.group_rename(str(key or ""), str(name or "").strip())
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True}

    def group_delete(self, key):
        try:
            self._board.group_delete(str(key or ""))
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True}

    def set_group(self, project_id, key):
        """把项目挪到另一个分类。"""
        try:
            self._board.set_group(int(project_id), str(key or ""))
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True}

    # ---------- 活跃与告警（M2） ----------

    def checkin(self, items):
        self._board.checkin(items or [])
        return {"ok": True}

    def activity(self, year=0):
        return self._board.activity(int(year) or None)

    def activity_logs(self, offset=0, limit=200):
        """全量流水翻页（活跃页「查看更多」那个弹窗）。"""
        return self._board.activity_logs(offset=offset, limit=limit)

    def alerts(self):
        return self._board.alerts()

    # ---------- 桥接健康（前端报到） ----------

    def boot_ok(self, report=""):
        """页面 JS 能调到这个方法，就说明 WebView2 的 JS 桥真的通了。

        为什么让前端主动报到：桥坏掉时 evaluate_js 会**卡死**而不是抛异常，
        拿它当探针会把看门狗自己挂住，永远发现不了问题。

        report 可选：--smoke 时前端把各页面的渲染统计从这条通道回传
        （Python → JS 的 evaluate_js 在部分环境里不触发 loaded 事件，不可靠）。
        """
        self._state["bridge_ok"] = True
        if report:
            self._state["boot_report"] = str(report)
        return {"ok": True}

    def diag_stage(self, stage):
        """自检巡视的阶段标记，卡住时用来定位停在哪一步。"""
        self._state.setdefault("diag_stages", []).append(str(stage))
        return {"ok": True}

    # ---------- 财务（M3） ----------

    def finance_data(self, year=0, client_id=0, currency="CNY"):
        return self._fin.data(int(year) or None, int(client_id) or None,
                              str(currency or "CNY"))

    def finance_add(self, payload):
        try:
            rid = self._fin.add(payload or {})
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True, "id": rid}

    def finance_update(self, rid, payload):
        try:
            self._fin.update(int(rid), payload or {})
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True}

    def finance_delete(self, rid):
        self._fin.delete(int(rid))
        return {"ok": True}

    def finance_export_plan(self, opts=None):
        """只算「这批选项会导出成什么」，**不开任何对话框**。

        界面拿它做文件名/条数预览；冒烟也走这条 —— 保存对话框是**模态框**，
        无人值守时弹出来会把整轮冒烟卡死，所以自动跑的地方一律不碰它。
        """
        try:
            o = self._fin.norm_opts(opts)
            rows = self._fin.pick_rows(o)
            return {
                "ok": True,
                "name": self._fin.build_name(o["fmt"], o),
                "range": self._fin.range_label(o),
                "sections": [SECTION_CN[s] for s in o["sections"]],
                "count": len(rows),
                "dir": self._fin.export_dir(),
            }
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

    def finance_export_save(self, opts=None):
        """导出到用户自己挑的位置和文件名 —— 位置、名字都在系统「另存为」里选。

        ⚠ 用户取消**不算失败**：返回 `cancelled: True`，界面安静地把选项弹窗留着
        让人重来。以前对话框类接口一律 `{"ok": False, "msg": ...}`，前端就会对
        "只是点了取消"弹一句"导出失败"，纯属吓人。
        """
        try:
            o = self._fin.norm_opts(opts)
            name = self._fin.build_name(o["fmt"], o)
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}

        # 起始目录：上次导出到哪儿就从哪儿开始（记不住也没关系，退到导出目录）
        start = self._db.get_setting("export_last_dir", "") or ""
        r = self._pick(webview.FileDialog.SAVE, start, self._fin.export_dir(),
                       save_filename=name, file_types=EXPORT_FILTERS[o["fmt"]])
        if not r.get("ok"):
            return r
        path = r.get("path") or ""
        if not path:
            return {"ok": True, "cancelled": True}

        path = _ensure_ext(path, "." + EXTS[o["fmt"]])
        try:
            self._fin.export_to(path, o["fmt"], o)
        except PermissionError as exc:
            return {"ok": False,
                    "msg": "这个文件正被别的程序占着（Excel 开着？），关掉再试："
                           + str(exc)}
        except OSError as exc:
            return {"ok": False, "msg": "写不进去：" + str(exc)}
        except Exception as exc:
            return {"ok": False, "msg": "导出失败：" + str(exc)}

        d = os.path.dirname(os.path.abspath(path))
        if d and d != start:
            try:
                self._db.set_setting("export_last_dir", d)
            except Exception:
                pass          # 记不住上次目录不是错误，文件已经写好了
        return {"ok": True, "path": path, "dir": d}

    def export_dir(self):
        return {"ok": True, "path": self._fin.export_dir()}

    def client_save(self, cid, payload):
        """cid 传 0 / None / "" 都当成"新建"。

        以前是 `int(cid) or None`：JS 侧传 null 过来时 `int(None)` 直接抛
        TypeError，被下面 except 吞成一个看不懂的错误串。
        """
        try:
            new_id = self._fin.client_save(int(cid or 0) or None, payload or {})
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True, "id": new_id}

    def client_delete(self, cid):
        try:
            cid = int(cid or 0)
            if not cid:
                return {"ok": False, "msg": "没指定要删哪个客户"}
            self._fin.client_delete(cid)
        except Exception as exc:
            return {"ok": False, "msg": str(exc)}
        return {"ok": True}

    # ---------- 目录 ----------

    def open_dir(self, path):
        err = opener.open_dir(path)
        return {"ok": not err, "msg": err}

    # ---------- 数据快照（M4） ----------

    def snapshot_list(self):
        return {"ok": True, "dir": str(self._db.backup_dir()), "items": self._db.snapshots()}

    def snapshot_now(self):
        r = self._db.snapshot_now()
        return {"ok": bool(r.get("ok")), "name": r.get("name") or ""}

    def snapshot_restore(self, name):
        """回滚到某个快照。失败原因（找不到 / 文件不像库）要说给用户听。"""
        try:
            return self._db.restore(str(name))
        except ValueError as exc:
            return {"ok": False, "msg": str(exc)}

    def open_backup_dir(self):
        d = str(self._db.backup_dir())
        if not os.path.isdir(d):
            return {"ok": False, "msg": "还没有备份目录，先点一次「立即备份」"}
        err = opener.open_dir(d)
        return {"ok": not err, "msg": err}

    # ---------- 配置 ----------

    def set_setting(self, key, value):
        self._db.set_setting(key, str(value))
        if key == "autostart":
            cb = self._state.get("set_autostart")
            if cb:
                cb(str(value) == "1")
        if key == "on_top":
            # 设置里的「启动即置顶」也要立刻作用到当前窗口，
            # 否则点了只是往库里写个值、界面毫无反应（老毛病：设置项看着像坏的）
            cb = self._state.get("set_top")
            if cb:
                cb(str(value) == "1")
        return {"ok": True}

    def set_top(self, on):
        """置顶开关。返回的是操作后窗口的**真实**状态，前端按钮以它为准。

        以前只回一个 ok，前端就拿「我刚才点的是什么」当结果 —— 一旦别处
        （托盘菜单、提醒拉前台）改过窗口层级，按钮亮灭就跟现实反着来。
        """
        cb = self._state.get("set_top")
        real = bool(cb and cb(bool(on)))
        self._db.set_setting("on_top", "1" if real else "0")
        return {"ok": bool(cb), "top": real}

    def get_top(self):
        """问窗口现在是不是真的置顶（给按钮和设置面板做同步用）。"""
        cb = self._state.get("is_top")
        return {"ok": bool(cb), "top": bool(cb and cb())}

    # ---------- 数据文件（issue #6：设置里要能改） ----------

    def db_info(self):
        """当前库是谁、来自哪（命令行 / 环境变量 / 设置里选的 / 内置默认）。"""
        cfg = str(paths.load_config().get("db_path") or "")
        env = os.environ.get("BOARD_DB", "")
        cur = os.path.abspath(self._db.path)
        src = "默认位置"
        if env and os.path.abspath(env) == cur:
            src = "环境变量 BOARD_DB"
        elif cfg and os.path.abspath(cfg) == cur:
            src = "设置中指定"
        try:
            holders = self._db.holders()
        except Exception:
            holders = []
        return {
            "ok": True, "path": cur, "from": src,
            "configured": cfg, "default": paths.default_db_path(),
            "writable": _dir_writable(os.path.dirname(cur) or "."),
            "exists": os.path.exists(cur),
            "size": (os.path.getsize(cur) if os.path.exists(cur) else 0),
            "share": bool(paths.share_db()),
            "holders": holders,
        }

    def set_db_file(self, path):
        """把「以后用哪个库」写进应用级配置，然后重启生效。

        校验走 `paths.check_db_target`（跟引导页同一套），
        **不动**用户选的那个文件里的内容；文件不存在就是"要新建一个库"。
        """
        p = str(path or "").strip().strip('"')
        if not p:
            return {"ok": False, "msg": "没选文件"}
        p = os.path.abspath(p)
        cur = os.path.abspath(self._db.path)
        if p == cur:
            return {"ok": False, "msg": "跟当前数据文件是同一个，没变化"}
        bad = paths.check_db_target(p)
        if bad:
            return {"ok": False, "msg": bad}
        err = paths.save_config({"db_path": p})
        if err:
            return {"ok": False, "msg": "写配置文件失败：" + err}
        return {"ok": True, "path": p, "restart": True}

    # ---------- 多机同时打开 ----------

    def sync_state(self):
        """多机共享状态：别人在不在用、有没有改过数据。

        前端按几十秒的节奏问一次。**changed 是"自上次询问以来别人改过"**，
        问一次就清掉 —— 不然每次轮询都提示"重新加载"，提示条永远关不掉。
        """
        try:
            changed = self._db.poll_external()
        except Exception:
            changed = False
        try:
            holders = self._db.holders()
        except Exception:
            holders = []
        return {
            "ok": True,
            "share": bool(paths.share_db()),
            "changed": changed,
            "holders": holders,
        }

    def set_share_db(self, on):
        """「多机同时打开」开关。

        存应用级配置（不是库里的 settings）：被别的机器占着的时候正是
        最需要读这个开关的时候，那会儿连不上库、读不到 settings。
        """
        want = bool(on)
        err = paths.save_config({"share_db": want})
        if err:
            return {"ok": False, "msg": "写配置文件失败：" + err}
        return {"ok": True, "share": want, "restart": True}

    def restart_app(self):
        """重启自己（改完数据文件后用）。"""
        try:
            subprocess.Popen(paths.restart_cmd(), cwd=paths.base_dir(), close_fds=True)
        except Exception as exc:
            return {"ok": False, "msg": "重启失败：" + str(exc)}

        def _later():
            # 给新进程一点时间起来，再让本进程退出（避免单实例互斥量撞车）
            time.sleep(1.5)
            cb = self._state.get("quit")
            if cb:
                cb()

        threading.Thread(target=_later, daemon=True).start()
        return {"ok": True}

    # ---------- 诊断辅助 ----------

    def pathinfo(self):
        """跑在源码里还是打包后的 exe 里、data 落在哪。

        设置页和冒烟都用得上：打包后最容易出的问题就是 data/ 落错地方
        （落到解包临时目录，重启即丢），这条能直接把它钉在回归里。
        """
        return paths.describe()
