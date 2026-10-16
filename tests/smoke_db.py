"""无界面冒烟测试：验证建表、树读写、状态、统计、目录、活跃流水。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from db import AlreadyRunning, Db, Locked  # noqa: E402
from models import Board, _due_label  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="board_smoke_")
    path = os.path.join(tmp, "board.sqlite")

    db = Db(path)
    db.open()
    board = Board(db)

    # 清掉种子数据，从干净状态开始
    db.run("DELETE FROM nodes")
    db.run("DELETE FROM projects")
    db.run("DELETE FROM activity_log")

    pid = board.add_project("SANTI_OneDay EP01", "commercial")
    board.add_link("project", pid, "项目根目录", tmp)
    stage = board.add_node(pid, None, "CFX 解算")
    shot = board.add_node(pid, stage, "S001_C001")
    leaf = board.add_node(pid, shot, "v001 初版")
    board.set_done(leaf, 1)
    board.set_status("node", shot, "通过")
    board.set_status("project", pid, "进行中")
    board.set_field("project", pid, "priority", 1)
    board.set_field("project", pid, "deadline", "2026-10-08")

    data = board.load()
    proj = data["groups"][0]["projects"][0]
    assert proj["title"] == "SANTI_OneDay EP01", proj["title"]
    assert proj["children"][0]["title"] == "CFX 解算"
    assert proj["children"][0]["children"][0]["children"][0]["done"] == 1

    # 层级深度：项目 -> 环节 -> 镜头 -> 任务
    depth = proj["children"][0]["children"][0]["children"]
    assert len(depth) == 1 and depth[0]["title"] == "v001 初版"

    # 折叠持久化
    board.set_collapsed("project", pid, 1)
    assert board.load()["groups"][0]["projects"][0]["collapsed"] == 1

    # 统计
    st = data["stats"]
    assert st["today"] == 1, st
    assert st["running"] == 1, st

    # 活跃流水：只有环节流转到 NODE_ACTIVE_STATUS 里的状态才计权重（2026-09-30 口径）。
    # 上面这一串（建项目 / 加环节 / 改项目整体状态 / 勾完成 / 改成「通过」）**全是只记流水**，
    # 所以先断言此刻权重求和是 0，再补一次白名单内的流转验正面。
    assert db.one("SELECT COALESCE(SUM(weight),0) AS w FROM activity_log")["w"] == 0
    board.set_status("node", shot, "反馈")
    pos = [dict(r) for r in db.q("SELECT kind,weight FROM activity_log WHERE weight>0")]
    assert len(pos) == 1 and pos[0]["kind"] == "status" and pos[0]["weight"] == 1, pos

    # 目录打开（这里指向临时目录，不应报错）
    import opener
    assert opener.open_dir(tmp) == ""
    assert opener.open_dir("Z:/不存在的路径") != ""

    # 删除级联
    board.delete("node", stage)
    assert board.load()["groups"][0]["projects"][0]["children"] == []

    # ---------- M2：打卡 / 热力图 / 告警 ----------
    import datetime as dt

    assert board.checked_in_today() is False
    board.checkin([{"id": pid, "title": "SANTI_OneDay EP01"}])
    assert board.checked_in_today() is True

    act = board.activity(dt.date.today().year)
    assert act["total"] >= 1, act
    assert act["active_days"] >= 1
    assert act["current"] >= 1
    assert act["longest"] >= 1
    assert act["days"].get(dt.date.today().isoformat(), 0) >= 1
    assert any(r["kind"] == "checkin" for r in act["recent"]), act["recent"]
    assert str(dt.date.today().year) in act["years"]

    # ---------- 打卡 = 重置「今日必做」（2026-10-09 用户口径）----------
    # 以前 priority 只加不减：打过一次卡就永久挂着，只能去 ⋯ 菜单手动取消。
    # 现在每次确认打卡，标记**恰好**等于本次勾选的这批。
    p2 = int(board.add_project("重置验证-B", "商业") or 0)
    p3 = int(board.add_project("重置验证-C", "商业") or 0)
    assert p2 and p3
    board.set_field("project", p2, "priority", 1)
    board.set_field("project", p3, "priority", 1)
    assert board.priority_count() == 3, board.priority_count()   # pid + p2 + p3

    # 只勾 pid 打卡 → p2/p3 的旧标记必须被清掉
    board.checkin([{"id": pid, "title": "只勾这一个"}])
    assert board.priority_count() == 1, board.priority_count()
    rows = {r["id"]: r["priority"] for r in board.db.q("SELECT id,priority FROM projects")}
    assert rows[pid] == 1, rows
    assert rows[p2] == 0 and rows[p3] == 0, rows

    # 校验「活跃时间 = 打卡那一刻」没被 reset 的 updated_at 覆盖
    assert board.set_field("project", p2, "priority", 1) is None
    board.set_field("project", p2, "priority", 0)

    # 一个都不勾 → 标记全清，但**仍要落一条流水**（2026-10-09 改）
    # `checked_in_today()` 认的是「今天有没有 kind='checkin' 的行」，没有这行
    # 提醒线程会一直催 —— 用户点完「确认打卡」还接着被弹，看着像坏了。
    def count_checkin() -> int:
        """打卡流水的条数 —— 用来验证「空勾选也落了一条」。"""
        return board.db.q(
            "SELECT COUNT(*) AS n FROM activity_log WHERE kind='checkin'")[0]["n"]

    before = count_checkin()
    board.checkin([])
    assert board.priority_count() == 0, board.priority_count()
    assert count_checkin() == before + 1, ("空勾选也要落一条 checkin 流水",
                                           before, count_checkin())
    last = board.db.q("SELECT kind,weight,detail FROM activity_log ORDER BY id DESC LIMIT 1")[0]
    # ⚠ 权重必须是 0：activity() 只 SUM(weight>0)，写 1 会给热力图白送一分活跃
    assert last["kind"] == "checkin" and last["weight"] == 0, last
    assert board.checked_in_today() is True

    # 空 items / 脏 id 走同一条路（后端 `items or []`）
    before = count_checkin()
    board.checkin([{"id": 0, "title": "脏数据"}])
    assert count_checkin() == before + 1, ("id 无效同样落到空流水分支",
                                           before, count_checkin())

    # 回填：后面的断言依赖 pid 带着 priority
    board.set_field("project", pid, "priority", 1)

    # ---------- 变更记录只取最近一周 + 翻页接口（2026-09-30）----------
    # 流水是只增不减的表。面板只该列最近 7 天，其余走 activity_logs 分页。
    db.run("DELETE FROM activity_log")
    d0 = dt.date.today()
    # 倒着插（先老后新）：id 单调递增才跟真实数据一致 —— 查询按 id 倒序，
    # 正着插的话「最新一天」会排到最后，把面板的顺序测反了
    for back in range(9, -1, -1):
        d = (d0 - dt.timedelta(days=back)).isoformat()
        for k in range(2):
            db.run(
                "INSERT INTO activity_log(ts,kind,weight,project_id,node_id,detail,day)"
                " VALUES(?,?,?,?,?,?,?)",
                (d + " 01:0%d:00" % k, "status", 1, pid, None, "流水%d-%d" % (back, k), d),
            )
    act = board.activity(d0.year)
    assert act["window_days"] == 7, act
    assert act["recent_since"] == (d0 - dt.timedelta(days=6)).isoformat(), act["recent_since"]
    assert act["recent_total"] == 14, act["recent_total"]        # 7 天 × 2 条
    assert act["log_total"] == 20, act["log_total"]
    assert len(act["recent"]) == 14 and act["recent_more"] == 0
    assert {r["day"] for r in act["recent"]} == {
        (d0 - dt.timedelta(days=i)).isoformat() for i in range(7)
    }, sorted({r["day"] for r in act["recent"]})
    assert all(r["day"] >= act["recent_since"] for r in act["recent"])

    # 分页：一次 6 条，翻到底 has_more 变 False，且不重不漏
    got, off = [], 0
    while True:
        page = board.activity_logs(off, 6)
        assert page["total"] == 20 and len(page["rows"]) <= 6
        got += [r["detail"] for r in page["rows"]]
        off += len(page["rows"])
        if not page["has_more"]:
            break
        assert off < 40, "翻页没收口"
    assert len(got) == 20 and len(set(got)) == 20, got
    assert got[0] == "流水0-1"                            # 新→旧
    assert board.activity_logs(0, 9999)["limit"] == 500   # 上限挡一下
    assert board.activity_logs(0, 0)["limit"] == 1        # 下限也别让它变 0（LIMIT 0 什么都读不到）
    # 按天查询有索引（老库开一次就补上；40 万行时实测省 ~70ms）
    idx = {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_log_day" in idx, idx

    # 停滞：把项目与环节的活动时间拨回过去
    old = "2020-01-01 00:00:00"
    db.run("UPDATE projects SET last_activity_at=? WHERE id=?", (old, pid))
    db.run("UPDATE nodes SET last_activity_at=? WHERE project_id=?", (old, pid))
    a = board.alerts()
    assert any(x["title"] == "SANTI_OneDay EP01" for x in a["stale"]), a

    # 到期：deadline 已过 → 逾期
    db.run("UPDATE projects SET deadline=? WHERE id=?", ("2020-01-05", pid))
    a = board.alerts()
    assert any(x["kind"] == "due" and x["days"] < 0 for x in a["due"]), a

    # 到期文案一律写整句话（2026-09-30 用户要求：「D-1」「今天」得像密码才读得懂）。
    # 前端 dueTag() 跟这里是同一套说法，两处对不上最容易让人怀疑数据不对。
    # 2026-10-13：逾期那条改叫「制作逾期」—— 已交付项目报的是「结款逾期」，
    # 两个"逾期"并排时得一眼看出催的是活儿还是钱。
    assert _due_label(-2) == "制作逾期 2 天", _due_label(-2)
    assert _due_label(0) == "今天到期", _due_label(0)
    assert _due_label(1) == "1 天后到期", _due_label(1)
    assert _due_label(30) == "30 天后到期", _due_label(30)
    db.run("UPDATE projects SET deadline=? WHERE id=?", (dt.date.today().isoformat(), pid))
    a = board.alerts()
    assert any(x.get("owner") == "project" and x["id"] == pid
               and x["days"] == 0 and x["detail"] == "今天到期" for x in a["due"]), a["due"]
    db.run("UPDATE projects SET deadline=? WHERE id=?", ("2020-01-05", pid))

    # ---------- 环节状态表：制作流转 10 态 + 老库迁移 ----------
    # 2026-09-30：「取消」改叫「中止」，新增「可优化」（能交了但还想再打磨）
    need = ["待开始", "等上游", "制作中", "暂停", "中止",
            "已提交", "反馈", "可优化", "通过", "交付"]
    assert board.load()["status"]["node"] == need, board.load()["status"]["node"]

    # 项目层：去掉「待交付」，「搁置」改叫「中止」
    pj = board.load()["status"]["project"]
    assert pj == ["筹备", "进行中", "阻塞", "已交付", "已结款", "归档", "中止"], pj

    from db import NODE_STATUS_MIGRATE, PROJECT_STATUS_MIGRATE
    assert set(NODE_STATUS_MIGRATE) == {"未开始", "进行中", "阻塞", "待确认", "完成", "搁置", "取消"}
    assert all(v in need for v in NODE_STATUS_MIGRATE.values())
    assert set(PROJECT_STATUS_MIGRATE) == {"搁置", "待交付"}
    assert all(v in pj for v in PROJECT_STATUS_MIGRATE.values())

    # 「可优化」既不是收工态、也不算完成 —— 还想改的活儿不能进交付量
    from models import NODE_CLOSED, NODE_DONE
    assert "可优化" not in NODE_CLOSED and "可优化" not in NODE_DONE
    assert "中止" in NODE_CLOSED

    # 新节点默认「待开始」；勾选框联动 通过 / 制作中
    fresh = board.add_node(pid, None, "状态测试节点")
    assert db.one("SELECT status FROM nodes WHERE id=?", fresh)["status"] == "待开始"
    board.set_done(fresh, 1)
    assert db.one("SELECT status FROM nodes WHERE id=?", fresh)["status"] == "通过"
    board.set_done(fresh, 0)
    assert db.one("SELECT status FROM nodes WHERE id=?", fresh)["status"] == "制作中"

    # 老词写回 → 开库迁移成新词；再跑一遍幂等，不报错也不变样
    db.run("UPDATE nodes SET status='阻塞' WHERE id=?", fresh)
    db._migrate_statuses()
    assert db.one("SELECT status FROM nodes WHERE id=?", fresh)["status"] == "等上游"
    db._migrate_statuses()
    assert db.one("SELECT status FROM nodes WHERE id=?", fresh)["status"] == "等上游"

    # 项目层也走同一套：搁置→中止、待交付→进行中。
    # ⚠ 关键是**别串表** —— 环节层的「搁置→暂停」不能落到项目行上。
    db.run("UPDATE projects SET status='搁置' WHERE id=?", pid)
    db.run("UPDATE nodes SET status='搁置' WHERE id=?", fresh)
    db._migrate_statuses()
    assert db.one("SELECT status FROM projects WHERE id=?", pid)["status"] == "中止"
    assert db.one("SELECT status FROM nodes WHERE id=?", fresh)["status"] == "暂停"
    db.run("UPDATE projects SET status='待交付' WHERE id=?", pid)
    db._migrate_statuses()
    assert db.one("SELECT status FROM projects WHERE id=?", pid)["status"] == "进行中"

    # 到期提醒看状态：同为逾期，「制作中」要报、「中止」不报。
    # 注意项目和节点的 id 各自自增，光比 id 会撞号，必须带 owner 一起判。
    def _due_node():
        return [
            x for x in board.alerts()["due"]
            if x.get("owner") == "node" and x.get("id") == fresh
        ]

    db.run("UPDATE nodes SET deadline=?, status='制作中' WHERE id=?", ("2020-01-02", fresh))
    assert _due_node(), board.alerts()["due"]
    db.run("UPDATE nodes SET status='中止' WHERE id=?", fresh)
    assert not _due_node(), board.alerts()["due"]

    board.delete("node", fresh)

    # 打卡时间与调度判定
    import remind
    assert remind.Reminder._past("00:00") is True
    assert remind.Reminder._past("23:59") is False

    # 本机单实例：互斥量被自己持有时，再开一个 Db 应报 AlreadyRunning
    db2 = Db(path)
    try:
        db2.open()
        raise AssertionError("本机第二个实例应当被互斥量挡住")
    except AlreadyRunning:
        pass

    # 跨机占用：锁文件里是别人的 hostname 且心跳新鲜 -> Locked
    # （本机已在上面被互斥量拦下，这里用一个没被占用的新库来验证跨机分支）
    from pathlib import Path
    import shutil
    import sqlite3
    import time
    cross = str(Path(path).with_name(Path(path).stem + "_cross.sqlite"))
    Path(cross).with_suffix(".lock").write_text(
        f"OTHER-PC:99999|{time.time()}", encoding="utf-8"
    )
    db3 = Db(cross)
    hit = None
    try:
        db3.open()
    except Locked as exc:
        hit = str(exc)
    finally:
        if hit is None:
            db3.close()
        for f in (cross, cross[:-7] + ".lock"):
            if os.path.exists(f):
                os.unlink(f)
    assert hit and "使用" in hit, hit

    # ---------- 多机同时打开：锁变成"每台机器一行"的登记表 ----------
    #
    # 两件事必须同时成立：
    #   ① 别人的行不能被我抹掉（早先整个文件覆盖，两边心跳互相打飞）
    #   ② 共享模式下不再因为"别人在用"拒绝启动
    sh_path = str(Path(path).with_name(Path(path).stem + "_share.sqlite"))
    sh_lock = Path(sh_path).with_suffix(".lock")
    sh_lock.write_text(f"OTHER-PC:4242|{time.time()}\n", encoding="utf-8")
    db4 = Db(sh_path, share=True)
    opened = True
    try:
        db4.open()
    except Exception as exc:                       # noqa: BLE001 —— 就是为了把真因带出来
        opened = False
        raise AssertionError(f"共享模式不该被跨机锁挡住：{exc!r}")
    finally:
        if not opened:
            db4.close()
            for f in (sh_path, str(sh_lock)):
                if os.path.exists(f):
                    os.unlink(f)
    reg = sh_lock.read_text(encoding="utf-8")
    assert "OTHER-PC:4242|" in reg, ("别人的登记行被抹掉了", reg)
    assert f"{db4.me}|" in reg, ("自己没登记进去", reg)
    assert [h["host"] for h in db4.holders()] == ["OTHER-PC"], db4.holders()

    # 心跳只更新自己那一行
    db4.heartbeat()
    lines = [x for x in sh_lock.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2, lines
    assert any(x.startswith("OTHER-PC:4242|") for x in lines), lines

    # 别人改过没有：单机模式永远 False；共享模式下"报一次就清掉"
    assert db.poll_external() is False, "没开共享就不该有'别人改过'这回事"
    assert db4.poll_external() is False, "刚开库时不该报别人改过"
    db4.run("INSERT OR REPLACE INTO settings(key,value) VALUES('own_probe','1')")
    assert db4.poll_external() is False, "自己写的不能算成别人改过"
    ext = sqlite3.connect(sh_path)
    ext.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('ext_probe','1')")
    ext.commit()
    ext.close()
    assert db4.poll_external() is True, "别人改过要能发现"
    assert db4.poll_external() is False, "报过一次就该复位，不能一直提示"

    # 共享模式的命门：对面正占着写锁时，我们这一笔要**等**它，而不是立刻抛
    # "database is locked" 让用户看到一次莫名其妙的失败。
    # ⚠ 这条光靠 busy_timeout 是过不去的：autocommit 下一条 INSERT 先拿 SHARED
    # 再升级成 RESERVED，正好撞上 SQLite "可能死锁就绕开 busy handler" 那条规则，
    # 会立刻返回 SQLITE_BUSY —— 所以 db._retry_locked 才是必需的。这个用例就是钉它。
    import threading

    other = sqlite3.connect(sh_path, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")              # 冒充另一台机器占住写锁

    def release():
        time.sleep(1.0)
        try:
            other.execute("COMMIT")
        finally:
            other.close()

    threading.Thread(target=release, daemon=True).start()
    t0 = time.time()
    db4.run("INSERT OR REPLACE INTO settings(key,value) VALUES('wait_probe','1')")
    waited = time.time() - t0
    row = db4.one("SELECT value FROM settings WHERE key='wait_probe'")
    assert row and row["value"] == "1", "等完还是没写进去"
    assert waited >= 0.8, ("没等对面释放就写进去了？", waited)

    # 退出只划掉自己那行，别人的还在
    db4.close()
    rest = sh_lock.read_text(encoding="utf-8") if sh_lock.exists() else ""
    assert rest.strip().startswith("OTHER-PC:4242|"), ("自己退出却把别人的登记删了", rest)
    for f in (sh_path, str(sh_lock)):
        if os.path.exists(f):
            os.unlink(f)

    # ---------- 数据文件路径可用性（引导页和设置页共用这一套判断） ----------
    import paths

    pdir = tempfile.mkdtemp(prefix="board_paths_")
    fresh = os.path.join(pdir, "board.sqlite")
    assert paths.check_db_target(fresh) is None, "空目录里新建库应当放行"
    missing = os.path.join(pdir, "nope", "board.sqlite")
    assert paths.check_db_target(missing), "父目录不存在要拦下"
    assert paths.check_db_target(missing, allow_new_parent=True) is None, \
        "引导页'新建到某个目录'要能顺手把目录建出来"
    fake = os.path.join(pdir, "fake.sqlite")
    Path(fake).write_text("这不是数据库", encoding="utf-8")
    assert "不是 SQLite" in (paths.check_db_target(fake) or ""), "假库要被拦下"
    assert paths.check_db_target(fake, allow_new_parent=True), \
        "allow_new_parent 只该放行'目录不存在'，不该放行'内容不是数据库'"

    # ---------- 引导页只有一个路径框：路径要怎么归一 ----------
    # 用户指一个目录，我们要自己判断"里面已经有数据了没有"—— 判断错了就是
    # 要么莫名其妙新建一个空库（数据看着全没了），要么把别人目录里的杂项 db 当数据打开。
    empty_dir = tempfile.mkdtemp(prefix="board_resolve_empty_")
    got = paths.resolve_target(empty_dir)
    assert got == os.path.join(empty_dir, "board.sqlite"), ("空目录该落到新建 board.sqlite", got)

    # 目录里有个真的 SQLite → 直接用那个，不新建
    withdir = tempfile.mkdtemp(prefix="board_resolve_have_")
    real = os.path.join(withdir, "myboard.sqlite")
    sqlite3.connect(real).execute("CREATE TABLE t(x)")
    got = paths.resolve_target(withdir)
    assert got == real, ("目录里已有库就该直接用", got)
    # 带斜杠结尾的写法也要认成目录
    assert paths.resolve_target(withdir + os.sep) == real

    # 目录里有 0 字节 / 假货 .db → 不能当数据文件（用户挑的很可能是下载目录）
    junk = tempfile.mkdtemp(prefix="board_resolve_junk_")
    Path(os.path.join(junk, "cache.db")).write_text("不是数据库", encoding="utf-8")
    open(os.path.join(junk, "empty.sqlite"), "w").close()
    got = paths.resolve_target(junk)
    assert got == os.path.join(junk, "board.sqlite"), ("杂项 db 不能被当成数据文件", got)

    # board.sqlite 优先：目录里既有别的库又有它，认它
    prio = tempfile.mkdtemp(prefix="board_resolve_prio_")
    sqlite3.connect(os.path.join(prio, "aaa.sqlite")).execute("CREATE TABLE t(x)")
    sqlite3.connect(os.path.join(prio, "board.sqlite")).execute("CREATE TABLE t(x)")
    assert paths.resolve_target(prio) == os.path.join(prio, "board.sqlite"), "board.sqlite 该优先"

    # 具体文件路径 / 不存在的目录 / 空串
    assert paths.resolve_target(real) == real, "给了文件路径就用它本身"
    newdir = os.path.join(prio, "sub", "deep")
    assert paths.resolve_target(newdir) == os.path.join(newdir, "board.sqlite"), \
        "不存在的目录也要当目录（而不是猜成无扩展名的文件）"
    assert paths.resolve_target("") == "", "空路径返回空串，交给上层报错"
    for d in (empty_dir, withdir, junk, prio):
        shutil.rmtree(d, ignore_errors=True)

    # ---------- 进度：状态走到「通过 / 交付」也算完成 ----------
    # 界面上的进度条读的就是 load() 里的 done。早先只认勾选框，用户是改状态的
    # →「进度条永远是 0」。这里把三种情形钉住。
    prog_p = board.add_project("进度测试", "commercial")
    k1 = board.add_node(prog_p, None, "A")
    k2 = board.add_node(prog_p, None, "B")

    def prog_kids():
        for pp in board.load()["groups"][0]["projects"]:
            if pp["id"] == prog_p:
                return [(c["status"], c["done"]) for c in pp["children"]]
        return []

    board.set_status("node", k1, "通过")
    board.set_status("node", k2, "交付")
    assert prog_kids() == [("通过", 1), ("交付", 1)], prog_kids()
    board.set_status("node", k2, "制作中")
    assert prog_kids() == [("通过", 1), ("制作中", 0)], prog_kids()
    # 「取消」是收工态，但没做完，不该算进进度
    board.set_status("node", k2, "取消")
    assert prog_kids() == [("通过", 1), ("取消", 0)], prog_kids()

    # ---------- 制作人（2026-10-16 起强关联到 vendors） ----------
    # 直接插两条外包商：vendors 的写接口在 finance 那边（跟 clients 一处），
    # 数据层测试不绕过去调它，这里只验 Board 侧的读与回填口径。
    v_wang = db.run("INSERT INTO vendors(name,kind) VALUES(?,?)", ("老王", "个人"))
    v_li = db.run("INSERT INTO vendors(name,kind) VALUES(?,?)", ("小李", "团队"))
    board.set_field("project", prog_p, "artist_id", v_wang)
    board.set_field("node", k1, "artist_id", v_li)
    d_art = [pp for pp in board.load()["groups"][0]["projects"] if pp["id"] == prog_p][0]
    assert d_art["artist"] == "老王", d_art          # 对外仍然发名字
    assert d_art["artist_id"] == v_wang, d_art       # 同时发 id（未分配判据用它）
    assert [c["artist"] for c in d_art["children"]] == ["小李", ""], d_art["children"]
    assert [c["artist_id"] for c in d_art["children"]] == [v_li, None], d_art["children"]
    # 清空 = 未分配（None 直接落到 NULL 上）
    board.set_field("project", prog_p, "artist_id", None)
    d_art = [pp for pp in board.load()["groups"][0]["projects"] if pp["id"] == prog_p][0]
    assert d_art["artist"] == "" and d_art["artist_id"] is None, d_art
    board.set_field("project", prog_p, "artist_id", v_wang)   # 还原，后面还要用
    # 裸文本列已不可写（白名单撤掉了它）—— 写它就是"绕过 vendors 直接塞名字"
    try:
        board.set_field("project", prog_p, "artist", "老王")
        raise AssertionError("裸文本 artist 不该还能写")
    except ValueError:
        pass
    try:
        board.set_field("project", prog_p, "artistx", "x")
        raise AssertionError("不认识字段不该被放行")
    except ValueError:
        pass
    # 批量改制作人：参数语义已改成 id（传 0 → 清空成未分配）
    k3 = board.add_node(prog_p, None, "C")
    r_bulk = board.bulk_update([k3], artist=v_li)
    assert r_bulk["ok"] and r_bulk["done"] == 1, r_bulk
    assert db.one("SELECT artist_id FROM nodes WHERE id=?", k3)["artist_id"] == v_li
    board.bulk_update([k3], artist=0)
    assert db.one("SELECT artist_id FROM nodes WHERE id=?", k3)["artist_id"] is None
    board.delete("node", k3)

    # ---------- 默认折叠：新建就是折着的 ----------
    assert db.one("SELECT collapsed FROM projects WHERE id=?", prog_p)["collapsed"] == 1
    assert db.one("SELECT collapsed FROM nodes WHERE id=?", k1)["collapsed"] == 1

    # 一次性迁移：老库（全展开）开库时折起来；但只跑一次，
    # 之后用户自己点开的不会被下一次启动重新折回去。
    db.run("UPDATE projects SET collapsed=0")
    db.run("UPDATE nodes SET collapsed=0")
    db.set_setting("mig_collapse_default", "")
    db._migrate_collapse_default()
    assert db.one("SELECT SUM(collapsed) AS n FROM projects")["n"] == \
        db.one("SELECT COUNT(*) AS n FROM projects")["n"], "第一次迁移该把整棵树折起来"
    db.run("UPDATE projects SET collapsed=0 WHERE id=?", prog_p)
    db._migrate_collapse_default()          # 标记已置位 → 不该再动
    assert db.one("SELECT collapsed FROM projects WHERE id=?", prog_p)["collapsed"] == 0, \
        "用户点开的状态必须留住"

    # ---------- 老库补列：artist / artist_id / settle_date / vendor_id 都是后加的 ----------
    import sqlite3
    from pathlib import Path as _P
    legacy = str(_P(path).with_name("legacy.sqlite"))
    con = sqlite3.connect(legacy)
    con.executescript(
        "CREATE TABLE projects(id INTEGER PRIMARY KEY, title TEXT, category TEXT,"
        " client_id INTEGER, status TEXT, priority INTEGER, deadline TEXT, pinned INTEGER,"
        " note TEXT, collapsed INTEGER, sort_order INTEGER, last_activity_at TEXT,"
        " archived INTEGER, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE nodes(id INTEGER PRIMARY KEY, project_id INTEGER, parent_id INTEGER,"
        " title TEXT, status TEXT, done INTEGER, collapsed INTEGER, sort_order INTEGER,"
        " deadline TEXT, note TEXT, last_activity_at TEXT, created_at TEXT, updated_at TEXT);"
        "INSERT INTO projects(title,category,status,collapsed) VALUES('老项目','commercial','进行中',0);"
    )
    con.commit()
    con.close()
    db_l = Db(legacy)
    db_l.open()
    l_p = {r["name"] for r in db_l.q("PRAGMA table_info(projects)")}
    l_n = {r["name"] for r in db_l.q("PRAGMA table_info(nodes)")}
    assert "artist" in l_p and "artist" in l_n
    # 2026-10-16 制作人强关联：裸文本列旁边还得补出 artist_id（值是空的）
    assert "artist_id" in l_p and "artist_id" in l_n
    assert "settle_date" in l_p
    assert "vendor_id" in {r["name"] for r in db_l.q("PRAGMA table_info(finance)")}
    assert db_l.one("SELECT name FROM sqlite_master WHERE type='table' AND name='vendors'"), \
        "老库也要建出 vendors 表"
    # 老库里没有任何制作人文本 → 迁移不该凭空造条目
    assert db_l.one("SELECT COUNT(*) AS n FROM vendors")["n"] == 0
    assert db_l.one("SELECT collapsed FROM projects")["collapsed"] == 1, "老库也该被折起来"
    db_l.close()

    # 归档：默认**根本不发往前端**，要显式要一次才回来（项目页「筛选 ▾」里的开关）
    arch = board.add_project("归档项目", "personal")
    board.set_field("project", arch, "archived", 1)
    ids = [p["id"] for g in board.load()["groups"] for p in g["projects"]]
    assert arch not in ids, "归档项目默认不该出现在 load() 里"
    got = [p for g in board.load(include_archived=True)["groups"]
           for p in g["projects"] if p["id"] == arch]
    assert len(got) == 1 and got[0]["archived"] == 1, "要了归档就得带 archived 标记回来"
    assert all("archived" in p for g in board.load(include_archived=True)["groups"]
               for p in g["projects"]), "每个项目都要有 archived 字段"

    # 归档的**另一条路**：直接把状态改成「归档」。用户就是这么干的（真实库里那两条
    # archived 字段都还是 0）—— 只看字段的话「隐藏归档项目」对他毫无作用。
    st = board.add_project("状态归档项目", "personal")
    board.set_status("project", st, "归档")
    ids2 = [p["id"] for g in board.load()["groups"] for p in g["projects"]]
    assert st not in ids2, "状态是「归档」的项目同样默认不该出现"
    got2 = [p for g in board.load(include_archived=True)["groups"]
            for p in g["projects"] if p["id"] == st]
    assert len(got2) == 1 and got2[0]["archived"] == 0 and got2[0]["status"] == "归档", \
        "要了归档时状态归档的项目也要回来（archived 字段仍是 0）"

    # ---------- 分类（分组）：可增删改（2026-09-30） ----------
    # 顶层那几个筐原来写死在 models.GROUPS 里，现在存在库里的 groups 表。
    gs = board.groups()
    assert [g["key"] for g in gs][:2] == ["commercial", "personal"], gs
    assert gs[0]["name"] == "商业项目", gs

    k = board.group_add("客户活儿")
    assert [g["key"] for g in board.groups()][-1] == k, "新分类排在最末"
    g2 = board.load()["groups"]
    assert g2[-1]["name"] == "客户活儿" and g2[-1]["projects"] == [], g2

    # 改名只动 name：键是项目挂靠的依据，不能跟着变（否则项目会集体失踪）
    board.group_rename(k, "客户活儿（改名后）")
    assert board.groups()[-1]["key"] == k and board.groups()[-1]["name"] == "客户活儿（改名后）"

    # 挪项目：挪过去真出现在那组里；挪到一个不存在的分类要被挡住
    mv = board.add_project("挪来挪去的项目", "commercial")
    board.set_group(mv, k)
    hit = [g for g in board.load()["groups"] if g["key"] == k][0]
    assert [p["id"] for p in hit["projects"]] == [mv], hit["projects"]
    try:
        board.set_group(mv, "根本没有这个分类")
        raise AssertionError("挪到不存在的分类应当报错")
    except ValueError:
        pass

    # 非空不让删，而且要说清几个项目（跟删客户一个规矩）
    try:
        board.group_delete(k)
        raise AssertionError("还有项目挂着的分类不该让删")
    except ValueError as exc:
        assert "1 个项目" in str(exc), exc
    board.set_group(mv, "commercial")
    board.group_delete(k)
    assert k not in [g["key"] for g in board.groups()], "腾空之后就该能删了"

    # 最后一个分类不让删 —— 这个库里项目太多腾不空，拿一个干净的库试
    db2 = Db(os.path.join(tmp, "empty_groups.sqlite"))
    db2.open()
    b2 = Board(db2)
    db2.run("DELETE FROM nodes")
    db2.run("DELETE FROM projects")
    ks = [g["key"] for g in b2.groups()]
    assert len(ks) == 2, ks
    b2.group_delete(ks[1])
    assert [g["key"] for g in b2.groups()] == [ks[0]], b2.groups()
    try:
        b2.group_delete(ks[0])
        raise AssertionError("最后一个分类不该让删")
    except ValueError as exc:
        assert "至少" in str(exc), exc
    db2.close()

    # 兜底：项目的 category 指向一个不存在的键（手工改库 / 老快照）时，
    # 项目不能凭空消失 —— 得给它一个临时筐
    db.run("UPDATE projects SET category='orphan_x' WHERE id=?", mv)
    og = [g for g in board.load()["groups"] if g["key"] == "orphan_x"]
    assert len(og) == 1 and og[0]["name"] == "orphan_x", board.load()["groups"]
    assert [p["id"] for p in og[0]["projects"]] == [mv], "孤立分类里的项目也得在"
    db.run("UPDATE projects SET category='commercial' WHERE id=?", mv)

    # 建项目时给一个不存在的分类键 → 落到第一个分类（别造出谁也点不到的野项目）
    fallback = board.add_project("野分类项目", "no_such_group")
    first = board.groups()[0]["key"]
    where = [g["key"] for g in board.load()["groups"]
             if any(p["id"] == fallback for p in g["projects"])]
    assert where == [first], where

    # ---------- 停滞阈值：统一成一个数（2026-09-30） ----------
    from models import stale_days
    assert stale_days(db) == 3, stale_days(db)
    db.set_setting("stale_days", "0")
    assert stale_days(db) == 3, "填 0 这种坏值要退回默认，不能把整棵树标成停滞"
    db.set_setting("stale_days", "abc")
    assert stale_days(db) == 3, "填了非数字也要退回默认"

    # 真的按这一个阈值算：把一条项目的时间戳拨旧
    db.run("UPDATE projects SET last_activity_at='2020-01-01 09:00:00' WHERE id=?",
           fallback)
    db.set_setting("stale_days", "3")
    assert Board(db).stats()["stale"] >= 1, "阈值 3 天时这条 2020 年的项目该算停滞"
    assert Board(db).alerts()["stale"], "alerts 用的是同一个阈值"
    db.set_setting("stale_days", "3000")
    assert Board(db).stats()["stale"] == 0, "阈值拉到 3000 天就不该有停滞"
    assert not Board(db).alerts()["stale"], "alerts 也得跟着变"
    db.set_setting("stale_days", "3")

    # ---------- 筹备不算停滞 / 已交付报结款逾期（2026-10-13 用户定的） ----------
    from models import STALE_SKIP, settle_gap, _settle_label
    assert set(STALE_SKIP) == {"筹备", "已交付", "已结款", "归档"}, STALE_SKIP

    # 筹备：活动时间拨到 2020 年也**不算**停滞 —— 而且底栏那个数和告警名单必须一致
    db.run("UPDATE projects SET status='筹备', last_activity_at=? WHERE id=?",
           ("2020-01-01 09:00:00", fallback))
    assert Board(db).stats()["stale"] == 0, "筹备不算停滞（还没开工，谈不上停滞）"
    assert not [x for x in Board(db).alerts()["stale"] if x["project_id"] == fallback], \
        "筹备也不能出现在告警名单里（三处口径必须是一个数）"

    # 结款逾期**只认用户填的结款日期**（settle_date，2026-10-13 用户定的）：
    # 上一版拿交付日期反推，结果老项目会凭空冒出一个跟合同无关的"逾期"。
    # 结款当天不算（0 天）—— 当天就催太吵，从第 1 天起算。
    assert settle_gap(dt.date.today().isoformat()) is None, "结款当天不报"
    assert settle_gap((dt.date.today() - dt.timedelta(days=1)).isoformat()) == 1
    assert settle_gap("") is None, "没填结款日期就报不出来，别瞎算"
    assert settle_gap(None) is None
    assert settle_gap("坏日期") is None
    assert _settle_label(12) == "结款逾期 12 天"

    # 已交付 + 没填结款日期 → 什么都不报：不算停滞、不算结款逾期，也不算制作逾期
    # （deadline 早炸了也不报 —— 制作这条线已经结束了）
    db.run("UPDATE projects SET status='已交付', last_activity_at=?, deadline='2020-01-05'"
           " WHERE id=?", ("2020-01-01 09:00:00", fallback))
    assert not db.one("SELECT settle_date FROM projects WHERE id=?", fallback)["settle_date"]
    assert Board(db).stats()["stale"] == 0, "已交付不算停滞"
    assert not [x for x in Board(db).alerts()["due"] if x["id"] == fallback], \
        "没填结款日期就不该有这条告警（不许拿交付日期 / 截止日期凑）"

    # 填上结款日期 → 走结款逾期那条线，并进底栏那个数
    board.set_field("project", fallback, "settle_date", "2020-01-05")
    due = [x for x in Board(db).alerts()["due"]
           if x.get("owner") == "project" and x["id"] == fallback]
    assert due and due[0]["settle"] is True, due
    want = (dt.date.today() - dt.date(2020, 1, 5)).days
    assert due[0]["detail"] == "结款逾期 %d 天" % want, due[0]
    assert Board(db).stats()["due"] >= 1, "结款逾期要进底栏那个数，否则行上报红、底栏是 0"
    # 已交付就算 deadline 早就过期，也只报结款逾期、不报制作逾期
    assert not [x for x in Board(db).alerts()["due"]
                if x.get("settle") is False and x.get("id") == fallback], due
    # load() 必须把 settle_date 发到前端 —— 少发一眼就是"行上那颗红字永远是空的"
    pj = [p for g in Board(db).load()["groups"]
          for p in g["projects"] if p["id"] == fallback]
    assert pj and pj[0]["settle_date"] == "2020-01-05", pj
    # 结款日还没到 / 后来清空 → 都不报，而且是立刻不报
    board.set_field("project", fallback, "settle_date", "2099-01-01")
    assert not [x for x in Board(db).alerts()["due"] if x["id"] == fallback]
    board.set_field("project", fallback, "settle_date", "")
    assert not [x for x in Board(db).alerts()["due"] if x["id"] == fallback]

    # 上一版那个 delivered_at 已经彻底不认了：白名单撤掉之后连写都写不进去
    # （留着能写就成了暗门 —— 冒烟造数据时写错字段会静默"成功"）
    try:
        board.set_field("project", fallback, "delivered_at", "2026-01-01")
        raise AssertionError("delivered_at 不该再受理（结款逾期不再看它）")
    except ValueError:
        pass

    # set_status：进「已交付」不再顺手打交付日期（2026-10-13 撤掉那个自动时间戳）。
    # 状态本身照旧要落库，重复切同一个状态也不能出错。
    p2 = board.add_project("交付状态测试项目", "")
    board.set_status("project", p2, "已交付")
    assert db.one("SELECT status FROM projects WHERE id=?", p2)["status"] == "已交付"
    assert not db.one("SELECT settle_date FROM projects WHERE id=?", p2)["settle_date"], \
        "切状态不许凭空生出一个结款日期（那是用户跟客户约的，不是系统猜的）"
    board.set_status("project", p2, "已交付")
    board.delete("project", p2)

    # 老库迁移：开库时把 settle_date 列补上，但**不许**给老项目凭空填一个结款日
    # （上一版那个 delivered_at 兜底就是这么把"逾期"造出来的）。
    # ⚠ 这是唯一会碰用户真实数据的一段，冒烟里造不出来，必须单独钉住。
    p3 = board.add_project("老库已交付项目", "")
    db.run("UPDATE projects SET status='已交付', last_activity_at=?"
           " WHERE id=?", ("2026-08-01 10:20:30", p3))
    db.conn.execute("ALTER TABLE projects DROP COLUMN settle_date")   # 伪装成老库
    assert "settle_date" not in {r["name"] for r in db.conn.execute("PRAGMA table_info(projects)")}
    db._migrate_columns()                                            # 再开一次库时跑的那步
    assert "settle_date" in {r["name"] for r in db.conn.execute("PRAGMA table_info(projects)")}, \
        "老库缺的列要补上，否则一查就报 no such column"
    assert not db.one("SELECT settle_date FROM projects WHERE id=?", p3)["settle_date"]
    assert not [x for x in Board(db).alerts()["due"] if x["id"] == p3], \
        "老库的已交付项目不许被补一个结款日，也就不该有告警（宁可静默）"
    db._migrate_columns()                                            # 幂等：跑第二遍不变样
    assert not db.one("SELECT settle_date FROM projects WHERE id=?", p3)["settle_date"]
    board.delete("project", p3)

    db.backup()
    db.close()
    print("smoke db ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
