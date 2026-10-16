# 个人项目看板 — 设计文档 v0.1

自用单机工具。不做协作、不做云同步。核心三件事：**看清状态、一键打开文件、逼自己动起来**。

---

## 1. 需求基线（已确认）

| 项 | 决定 |
|---|---|
| 分类 | 商业项目 / 个人项目（根节点上区分） |
| 状态 | **全部手动更改**，不做自动流转判定 |
| 层级 | 项目下可无限层嵌套子环节，支持多层折叠 |
| 打开文件 | 每个节点可挂多个入口：文件 / 文件夹 / 命令 |
| 提醒 | 分级可调，**默认 L2**，可运行时切换"窗口始终最前" |
| 开机 | 开机自动启动并打开 |
| 财务 | 独立页面，主页不显示金额；支持汇总与导出 |
| 附加模块 | 子任务清单（已并入树）、客户与结算周期 |
| 界面布局 | 只保留顶部一栏，**无侧边栏**；商业 / 个人作为树的第一层分组节点 |
| 配色 | 暗色 / 亮色 / 跟随系统，设置里切换 |
| 数据文件 | 默认 `<挂载的共享盘>/T_T_Data/board.sqlite`，目录不存在自动创建，路径可在设置里改 |
| 财务页 | 顶栏页签直接进入，**无口令** |
| 不要 | 工时计时、每日打卡日记 |

---

## 2. 技术选型

- **壳**：`pywebview`（Windows 下 Edge WebView2 + WinForms 后端）
- **语言**：Python 3.13（本机 managed venv），前端原生 HTML/CSS/JS，**不引入构建工具**
- **存储**：SQLite 单文件，主库放 NAS 目录

依赖：`pywebview` `pystray` `Pillow` `openpyxl` `win11toast`（或 `plyer`）`apscheduler`（可选）

### 风险与对策

| 风险 | 对策 |
|---|---|
| pywebview 在 Python 3.13 上依赖 `pythonnet`，可能有兼容问题 | 先按 3.13 建 venv 试；不通就降到 3.12（pythonnet 支持最成熟） |
| 目标机缺 WebView2 Runtime（Win10 老版本） | 检测失败时提示下载 Evergreen Runtime；Win11 自带 |
| `window.on_top` 运行时切换在某些版本不生效 | 兜底用 `ctypes` 调 `SetWindowPos(HWND_TOPMOST)`，经 JS API 暴露 |
| pystray 托盘与 pywebview 主线程冲突 | 托盘跑独立线程，退出时 `window.destroy()` 后 join |
| 开机自启被杀软拦 | 开发期用 `shell:startup` 快捷方式；打包后写注册表 Run 键 |

---

## 3. 目录结构

> **当前事实（2026-10-01 核对）**。本文件第 11 节之后是开发期的实施流水账，
> 记录"当时为什么这么做"，里面的描述可能已经变了 —— 结构与表清单以本节和代码为准。

```
Task-Overview/
├── app/
│   ├── main.py       入口：建窗口、托盘、看门狗、调度器装配
│   ├── _boot.py      启动外壳：崩溃兜底写日志、孤儿锁清理、设 WebView2 profile
│   ├── boot.py       数据文件引导页（库打不开时先弹这个页让用户重选）
│   ├── db.py         SQLite 连接/迁移、NAS 单实例心跳锁、快照备份
│   ├── models.py     业务逻辑：树查询、汇总、活跃流水、告警、停滞判据
│   ├── api.py        暴露给前端的 JS 方法（js_api 的公开 callable）
│   ├── finance.py    金额汇总、CSV / Excel / JSON 导出
│   ├── node_spec.py  一行文本建一批子环节的**唯一**解析器
│   ├── opener.py     打开目录（是文件则在资源管理器里选中）；不做版本解析
│   ├── paths.py      **所有路径的唯一来源**（res_dir / base_dir / data_file …）
│   ├── remind.py     打卡巡检线程、停滞与到期告警、系统通知
│   ├── version.py    **品牌名与版本号的唯一来源**（标题栏 / 引导页 / 托盘 / exe 名）
│   └── web/
│       ├── index.html  主页（项目 / 财务 / 活跃三个页签）
│       ├── boot.html   数据文件引导页
│       ├── style.css
│       ├── app.js      UI 全部逻辑（经典脚本，非 ES module）
│       └── diag.js     --smoke 自检代码，按需动态加载，不进正常启动链路
├── tools/
│   ├── build_exe.py  打包 exe（onedir）
│   ├── make_icon.py  从 res/logo.svg 光栅化出 ico / png
│   ├── ver.py        git 包装：save / log / tag / back / undo
│   ├── sync_to_nas.py  E: → NAS 同步工程
│   └── nasfs.py      网络盘绕 POSIX 删除限制的工具
├── tests/
│   ├── smoke_*.py    **长期**回归（数据层 7 个）
│   └── _diag/        诊断脚本；`_xxx.py` 下划线开头的是一次性排查脚本，不进版本库
├── run.bat           双击启动（pythonw，无控制台）
├── run_debug.bat     排查模式（保留控制台 + 写 data\launch.log）
├── README.md         使用说明
└── DESIGN.md         本文件

不进版本库：data/（WebView2 profile、日志、本机运行状态）、dist/（出包产物，可再生）
```

---

## 4. 数据模型

### 4.1 `projects` — 项目根节点

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| title | TEXT | 项目名，如 `SANTI_OneDay EP01` |
| category | TEXT | → groups.gkey。出厂 `commercial` / `personal`，分类可在界面里增删改 |
| client_id | INTEGER NULL | → clients.id |
| status | TEXT | 筹备 / 进行中 / 阻塞 / **已交付** / 已结款 / 归档 / **中止**（2026-09-30 改版：撤掉「待交付」→退「进行中」，「搁置」→「中止」；既有的老库由 `db.PROJECT_STATUS_MIGRATE` 迁移） |
| priority | INTEGER | 0 普通，1 今日必做（主页置顶区） |
| deadline | DATE NULL | |
| settle_date | DATE NULL | **约定结款日**（2026-10-13 加，用户手填）：已交付项目的「结款逾期 N 天」**只认它**（`models.settle_gap`），没填就不报。上一版试过用交付日期（`delivered_at`，跟着状态自动打的时间戳）反推，被用户否掉 —— 那会凭空造出一个跟合同无关的"逾期"。老库由 `db._migrate_columns` 补列、值为空（不替用户猜日子）。界面上只有「项目设置」页一个入口（不给行内点改、不进批量改：填错比不填更误导） |
| pinned | INTEGER | 常驻顶部 |
| note | TEXT | |
| artist | TEXT | **老式裸文本制作人 —— 保留但不再读**（2026-10-16 起改用 `artist_id`）。SQLite 删列要重建整张表，不拿真库冒险；老库里的值由 `_migrate_vendors()` 一次性转成 `vendors` 条目再回填 id |
| artist_id | INTEGER NULL | → vendors.id。**制作人 = 外包商名录里的一条**（强关联存 id）。NULL = 未分配。显示名从 `vendors` JOIN 出来（对外仍发 `artist` 键，值是名字） |
| collapsed | INTEGER | UI 折叠状态持久化，**默认 1** |
| sort_order | INTEGER | |
| last_activity_at | DATETIME | 隐式刷新，用于停滞告警 |
| archived | INTEGER | 归档后默认从主页隐藏：`load()` 的 SQL 里就带 `p.archived=0`，**数据根本不下发**；要看得显式 `load(include_archived=True)` |
| created_at / updated_at | DATETIME | |

### 4.2 `nodes` — 子环节树（自引用，无限层）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| project_id | INTEGER | → projects.id |
| parent_id | INTEGER NULL | NULL = 顶层环节；否则指向父 node |
| title | TEXT | |
| status | TEXT | 待开始 / 等上游 / 制作中 / 暂停 / 取消 / 已提交 / 反馈 / 通过 / 交付 |
| done | INTEGER | 叶子节点勾选态 |
| collapsed | INTEGER | 折叠状态持久化，**默认 1** |
| sort_order | INTEGER | 同级排序 |
| deadline | DATE NULL | |
| note | TEXT | |
| artist | TEXT | **老式裸文本制作人 —— 保留但不再读**（跟 4.1 同一处置，老库一次性迁移） |
| artist_id | INTEGER NULL | → vendors.id。环节的制作人跟项目层**同一个口径**（同一张名录、同一套选人 / 批量改） |
| last_activity_at | DATETIME | |
| created_at / updated_at | DATETIME | |

**环节状态 = 镜头制作流转**（2026-09-26 改版）

主线：`待开始 → 等上游 → 制作中 → 已提交 → 反馈 → 通过 → 交付`
旁支：`暂停`（搁置等条件）、`取消`（这条不做了）

- 全部手动切换（点行上的状态徽章），不做自动流转
- **收工态** `通过 / 交付 / 取消`：不再计入到期提醒，也不再算作要推进的活
- 叶子节点的勾选框与状态联动：勾上 → `通过`，取消 → `制作中`
- 老库迁移在 `Db._connect` 里做，表定义 `db.NODE_STATUS_MIGRATE`，幂等：
  `未开始→待开始`、`进行中→制作中`、`阻塞→等上游`、`待确认→反馈`、`完成→通过`、`搁置→暂停`。
  快照回滚同样走 `_connect`，所以旧快照恢复回来也会被顺手迁移

**项目层状态**是独立的一套（见 4.1）：`筹备 / 进行中 / 阻塞 / 已交付 / 已结款 / 归档 / 中止`。
两层的词刻意不共用，界面按行类型取各自的表。
⚠ 两张表各用各的迁移表（`NODE_STATUS_MIGRATE` / `PROJECT_STATUS_MIGRATE`）——
环节的 `搁置→暂停` 落到项目行上会把项目改成环节的状态词，两边不能串。

**树的行为约定**

- 折叠状态写库，重开保持原样；**默认 `collapsed = 1`，即打开时只展开两个分组行**（老库由 `_migrate_collapse_default()` 一次性折起来，带标记位、可重入）
- 父节点显示子树自动汇总：`已完成叶子数 / 总叶子数`、进度条、子树最近截止日
  - 「完成」按**状态**判定（`NODE_DONE = 通过 / 交付`），不是按勾选框字段——早先只认勾选框，用户改状态后进度恒为 0
- 父节点的 `status` **始终手动设置**，不被子节点覆盖；只在旁边并列显示汇总进度
- 停滞判断取子树中**最新的** `last_activity_at`；到期判断取子树中**最早的** `deadline`
- **不算停滞的项目状态**（`models.STALE_SKIP`，前端 `app.js:isStale` 逐字对齐）：
  `筹备`（还没开工，谈不上停滞）、`已交付`（制作结束，改盯结款）、`已结款 / 归档`（收工）。
  三处口径（底栏「停滞 N」/ 告警名单 / 行上小标）必须同一个数
- **已交付走另一条到期线**：不报制作逾期，改报 **「结款逾期 N 天」**（`models.settle_gap`）——
  起算日是**用户手填的 `settle_date`（约定结款日）**，不是 `deadline`、也不再拿交付日期推算
  （2026-10-13 第二轮改的口径）；**没填就不报**，交付当天不报、从第 1 天起算。
  它跟制作逾期一样进底栏那个数和告警列表（`alerts()` 的条目带 `settle: true`，前端前缀写「结款 ·」）。

### 4.3 `links` — 目录入口

**范围收缩：只做「打开到目录」，不做软件版本指定、不做启动命令。**（涉及 Houdini 多版本/工程文件解析，暂不实现）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| owner_type | TEXT | `project` / `node` |
| owner_id | INTEGER | |
| label | TEXT | 显示名，如 `项目根目录` `缓存目录` `交付目录` |
| path | TEXT | 目录路径（支持 NAS 盘符路径） |
| sort_order | INTEGER | |

打开动作统一为 `explorer <path>`。预留字段（将来再开）：`action` `command` `app_version`。

### 4.4 `clients` — 客户与结算周期

id / name / contact / phone / email / settlement_cycle（月结 30 / 交付后 15 天 / 按镜头结算）/ default_currency / note

### 4.5 `vendors` — 外包商 / 制作人名录（2026-10-16）

id / name / kind（**个人 / 团队**两档）/ contact / note

它是**一个实体、两个角色**：同一条记录既是项目 / 环节的**制作人**（干活的那个），
也是财务**外包支出**的收款方（收钱的那个）。这就是「打通」的意义 —— 名录只维护一份。

- 字段刻意比 `clients` 精简（4 栏）：它是「给钱的对象」，不是「要维护关系的甲方」，
  所以第一批需求就否掉了计价信息一类的字段
- **强关联存 id**，不存名字：`projects.artist_id` / `nodes.artist_id` / `finance.vendor_id` 三处外键。
  按名字比的话，改一次名所有筛选条件会静默失配 —— 那等于白关联
- `kind` 写死两档，界面上也只画两档，免得改库塞进第三个值后前端分档渲染穷举不了
- **删除保护盯三处**：项目制作人 / 环节制作人 / 外包支出，理由要具体到「N 个项目、N 个环节、N 笔外包支出」
- **老库一次性迁移** `db._migrate_vendors()`（排在 `_migrate_columns()` 之后、带 `mig_vendors` 标记、幂等）：
  扫 `projects.artist` + `nodes.artist` 里非空且 `artist_id IS NULL` 的值 → 按 `casefold` 去重
  （SQLite 的 `COLLATE NOCASE` 只认 ASCII，中文得在 Python 里比）→ 建条目（`kind` 默认「个人」）→ 回填 id。
  **裸文本那两列保留但不再读**（跟 `delivered_at` 一个处置）：SQLite 删列要重建整张表，不拿真库冒险
- 对外**仍然发一个叫 `artist` 的键**（值是名字，从 `vendors` JOIN 出来）+ 新增 `artist_id` ——
  前端筛选、搜索、行上显示读的全是 `n.artist`，名字语义不变就一处都不用改

### 4.6 `finance` — 款项记录

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| project_id | INTEGER | |
| node_id | INTEGER NULL | 可选，挂到某个阶段/批次 |
| kind | TEXT | `contract` 合同额 / `invoice` 开票 / `payment` 到账 / `outsource` 外包支出 / `writeoff` 核销（2026-10-16 加） |
| amount | REAL | |
| currency | TEXT | 默认 CNY |
| tax_rate | REAL NULL | |
| date | DATE | |
| status | TEXT | 开票：未开 / 已开 / 已寄；到账：待收 / 已收；外包：应付 / 已付 / 作废；核销：已核销 / 作废 |
| invoice_no | TEXT NULL | |
| note | TEXT NULL | |
| vendor_id | INTEGER NULL | → vendors.id。**只有 `kind='outsource'` 有意义，别的类型恒为 NULL**（2026-10-16 加，老库 `ALTER TABLE` 补列）。`finance._vendor_id()` 里 `if kind != "outsource": return None`，并且**必填** + 存在性校验 |

汇总口径：`总合同额 = Σ contract`、`已收 = Σ payment(已收)`、`已核销 = Σ writeoff(已核销)`、
`外包支出 = Σ outsource(已付)`、`实际收入 = 已收 − 外包支出`、
`待收 = 合同额 − 已收 − 已核销`、`未开票 = 合同额 − Σ invoice(已开)`。

- 外包只有**「已付」**才冲减实际收入；`应付` / `作废` 一律 0（钱还没出去，不算自己的成本）
- **核销只冲待收**：不动合同额、不进实际收入（那不是现金流动，是放弃）。见 §28
- 收款率仍是 `已收 / 合同额` —— 15 万收 12 万就是 80%，**核销过的部分不该把分母改小**
  （那正是旧「结款」口径抹掉损失的地方）
- 「未开票」口径**只在导出里保留**，看板上已不展示开票两列（用户要求：开票与否不必上看板）
- **外包商汇总**（`finance._by_vendor()`，用户定的口径 = 已付 + 应付欠款）：
  只吃 `kind='outsource'` 且非「作废」的明细，`已付` / `应付` **分列** + `合计`；
  `vendor_id = 0` 的历史记录落「未指定外包商」那一行，**不静默丢掉**（丢了就没人知道该去补）
- `update()` 里 vendor 走 `keep()` 语义：不带这个键 = 沿用原值；类型从「外包支出」改成别的时
  会被 `_vendor_id()` 清成 NULL

### 4.7 `activity_log` — 活跃流水（热力图数据源）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| ts | DATETIME | 发生时间 |
| kind | TEXT | `checkin` 打卡 / `status` 状态变更 / `task` 完成任务 / `note` 备注 / `open` 打开目录 |
| weight | INTEGER | 计入热力图的权重：打卡、状态变更、完成任务 = 1；打开目录、备注 = 0 |
| project_id | INTEGER NULL | |
| node_id | INTEGER NULL | |
| detail | TEXT | 人类可读描述，如 `S001_C002 烟尘 → 进行中` |
| day | TEXT | `YYYY-MM-DD`，冗余字段，热力图按天聚合用 |

**什么算「做工」（2026-09-30 白名单口径，`models.NODE_ACTIVE_STATUS`）**：
只有**环节**流转到 `制作中 / 已提交 / 反馈 / 可优化 / 交付` 这 5 个状态，以及每日打卡，才 `weight = 1`。

- 项目层**任何**状态变更一律 `weight = 0`（用户口述的"项目状态"其实就是环节状态，这两层不能混）
- 勾 checkbox 也不计分——勾选落到的是「通过」，它在上面那 5 个之外；想要计入就改状态
- 新建项目 / 加环节同样不计分，`open`（打开目录）和 `note`（备注）永远是 0（否则刷格子）

⚠ **历史流水的 weight 不重算**（用户选的），所以改口径只影响以后新产生的流水。

### 4.8 `settings` — 键值配置

默认值见 `db.SETTINGS_DEFAULTS`（**以那里为准**，这张表只是给个索引）。

| key | 默认值 | 说明 |
|---|---|---|
| theme | `dark` | `dark` / `light` / `auto`（跟随系统 `prefers-color-scheme`） |
| db_path | NAS 上的 `board.sqlite` | 「这次读哪个库」的最高优先级来源其实是 `paths.resolve_db_path()`：<br>`--db` > 环境变量 `BOARD_DB` > **`data/config.json`** > settings 里的这个默认值。<br>库路径**不能只存 settings 表**——读表先得知道库在哪儿，那是死循环，所以真正的落地点是 `data/config.json` |
| autostart | 1 | 开机自启（写注册表 Run 键） |
| on_top | 1 | 启动即置顶，运行时可切 |
| remind_level | 2 | 1 / 2 / 3 |
| checkin_time | `09:00` | 每日打卡时间 |
| stale_days | `3` | 停滞阈值。**全库只有一个数**（2026-09-30 之前分 commercial / personal 两档，<br>分类改成可增删改之后，再绑两档就说不通了） |
| export_dir | `<db_dir>\export` | 最近导出目录 |
| nag_minutes | `30` | 没打卡时的重催间隔 |
| alert_interval | `180` | 告警的最小间隔（秒） |
| notify_enabled | `1` | 系统通知总开关 |
| nosandbox | `0` | 给 WebView2 加 `--no-sandbox`。看门狗判定沙箱被拦会自动置 1 |

主题实现：CSS 变量集中在 `:root` 与 `body.light` 两套，前端切 class 即时生效，值存 settings 表。

**多机共享开关 `share_db` 不在 settings 表里**，写在 `data/config.json` ——
库在 NAS 上时，settings 表是所有机器共享的，这个开关却只描述「这台机器要不要心跳锁」。

### 4.9 `groups` — 分类（分组）表

2026-09-30 加。早先分类是代码里写死的两个常量，现在可在界面里增删改。

| 字段 | 类型 | 说明 |
|---|---|---|
| gkey | TEXT PK | **稳定标识**（出厂 `commercial` / `personal`）。列名没叫 `key` 是不想跟 SQL 关键字打照面 |
| name | TEXT | 显示名，随便改 |
| sort_order | INTEGER | |

⚠ **这种分工是刻意的**：项目用 `projects.category` **挂键不挂名**，所以重命名分类不会波及任何项目。
`Board.groups()` 带孤儿键兜底——`category` 指向不存在的 gkey 时临时补一个筐，
免得项目凭空消失。「筐空了才能删，且至少留一个」是界面层的约束，不在库里。

---

## 5. 界面

### 5.1 主页（今日看板）

**只保留顶部一栏，不要侧边栏**，层级全部交给树表达。

顶栏：`[图标] │ [项目|财务|活跃] · 日期 · 刷新 ······ [置顶] [打卡] [设置] [＋ 项目]`

- **顶栏不再显示产品名**：以前左上角跟着一行「项目看板」，可窗口标题栏本来就写着品牌名，同一个信息占两块地方。现在只留产品图标（2026-10-01 用户口径）
- **日期与刷新排在页签右边**：它们说的是「当前这一页的数据」，紧跟着页签读起来才是「这一页的数据几点读的」；放在页签左边会被误读成页面标题的一部分
- **顶栏左侧只放页面页签，三个**：`项目`（树）、`财务`、`活跃`。分类（商业 / 个人）**不做筛选**，改成树里的分组标题行，一眼能看全
- 树工具栏只剩左边一个「筛选 ▾」（详见 §21.3）和右边一句拖拽提示：早先的「全部展开 / 全部折叠 / 只看项目」三个按钮已撤（用户要求）。默认折叠状态本身就是要的效果，留按钮只是第二套语义
- 主区是树，**第一层就是分类分组节点**：
  - `商业项目` / `个人项目` 作为分组行，可折叠，显示 项目数 · 进行中数 · 总进度条
  - 第二层是具体项目：加粗 + 状态徽章 + 客户 + 备注 + **制作人** + 截止 + `打开▾`，**不出现任何金额**
  - 第三层往下是环节与子任务，任意层可折叠
- **缩进规则**（用于和分组行错开，便于扫读）：分组行 8px → 项目 28px → 环节 48px → 再下一层 68px，每级 +20px
- 底栏：今日必做 x/y、进行中数、阻塞数、停滞告警、到期告警

可选开关：主页是否显示"有款项待收"的**纯图标标记**（不带数字），默认关闭。

### 5.2 财务页（顶栏页签）

- 入口：顶栏左侧页签 `财务`，与 `项目` 平级切换，**无口令**
- 汇总卡片 **6 张、两行三张**：
  - 第一行 `总合同额 / 已收 / 待收`
  - 第二行 `外包支出 / 实际收入 / 收款率`
  - （「本月到账」与开票口径都不上卡面；季度那行已写着「已到账」）
- 筛选：年份（**默认本年份**）、客户
- 明细表：项目 · 客户 · 合同额 · 已收 · 外包支出 · 实际收入 · 待收 · 最近到账日（**开票两列不上看板**，导出里保留）
- 展开看每笔款项流水
- 导出：CSV / Excel / JSON，导出到指定目录，文件名带日期

**记账入口共三处**（同一套口径，不分主次）：

| 入口 | 位置 | 适合 |
| --- | --- | --- |
| 建项目时一起填 | 顶栏「＋ 项目」弹窗：客户 / 合同额（币种、税率）/ 首款到账（日期） | 新项目刚谈定，合同额和首款一次录完 |
| 项目行上顺手记 | 树里项目行尾 ⋯ → **登记款项**（弹窗已预选该项目） | 日常回款，不想切到财务页 |
| 财务页 | 右上角「＋ 记一笔」 | 补记、跨项目批量录、改已有 |

### 5.3 打卡弹窗（L2 / L3）

列出今日必做项，勾选后关闭；未处理时按强度档决定是置顶小窗还是全屏遮挡。

### 5.4 活跃热力图（GitHub 小绿格）

顶栏页签「活跃」，与项目页、财务页平级切换。

- 年份切换（2026 / 2025）
- 格子图：53 周 × 7 天，按当天权重和分 5 档着色（0 / 1–2 / 3–4 / 5–7 / 8+），悬停显示日期与次数，未来日期降透明度
- 统计卡：全年活动次数、活跃天数、最长连续天数、当前连续天数
- 下方最近流水：类型 · 内容 · 时间

### 5.5 设置页

弹窗形式，改动即时生效并写入 settings 表：

界面配色（暗色 / 亮色 / 跟随系统）· 数据文件路径 + 更改按钮 · 开机自启 · 启动即置顶 · 提醒强度 L1/L2/L3 · 打卡时间 · 停滞阈值（商业 / 个人分开）· 财务页入口说明（无口令）

---

## 6. 强制督促机制

| 级别 | 行为 |
|---|---|
| L1 | 开机自启 + 托盘常驻 + 系统通知 |
| **L2（默认）** | 每日打卡时间弹出置顶窗口，未确认今日计划不关闭；可最小化但会周期性重新置顶 |
| L3 | 无边框全屏遮挡层，必须勾选完今日必做才解除 |
| L4 | 停滞告警（项目/节点 N 天无活动自动标红）与到期告警（D-7 / D-3 / D-1 / 逾期）自动升级提醒频率 |

窗口置顶是**运行时可切换**的独立开关，与提醒级别解耦——想让它一直最前就一直最前。

---

## 7. 实施分期

- **M1 骨架**：SQLite + 树模型 + 主页树形列表 + 折叠持久化 + 打开项目目录 + 置顶开关 + 开机自启 ✅
- **M2 督促 + 记录**：托盘、打卡弹窗 L2/L3、停滞与到期告警、系统通知、`activity_log` 与活跃热力图 ✅
- **M3 财务**：客户表、款项记录、汇总看板、CSV/Excel/JSON 导出 ✅
- **M4 打磨**：拖拽排序 ✅、批量展开/折叠 ✅、快捷键 ✅、快照回滚界面 ✅、打包 exe（待做）

---

## 8. 待确认

1. 是否需要在 S: 盘不可达时允许「本地暂存后合并」（建议 M4 再做）
2. 亮色配色色板是否合口味（原型里可实时切换查看）

---

## 9. 环境状态（2026-09-25 实测）

- S: 盘可达；`<挂载的共享盘>\T_T_Data` 已创建
- ~~原型文件 `prototype.html`~~ 已删（2026-10-01）：静态原型完成了它的历史任务，
  真实 UI（`app/web/index.html`）早已和它脱节，留着就是第二份真相 —— 需要看当时的
  交互设想就去 git 历史里翻（`git log --diff-filter=D -- prototype.html`）

---

## 10. M1 实施记录（2026-09-25）

代码位于 `app/`，启动器 `run.bat`，回归测试 `tests/smoke_db.py`（数据层全绿：建表、四层树、折叠持久化、统计、活跃流水、目录打开、级联删除、单实例锁、备份）。

**pywebview 5.x (Windows/EdgeChromium) 踩坑实录**——每一条都实测踩过：

1. `url` 本地路径不能拼 `?x=1` / `#x` 后缀：pywebview 用 `os.path.exists` 判断本地文件，带后缀被当远程 URL → 白屏
2. GUI 事件回调（shown/loaded）里调 `evaluate_js` → 死锁，窗口"未响应"；必须从后台线程调（`webview.start(func)`）
3. 给 `Window.on_top` 赋值不可靠（无 setter 时在 GUI 线程抛异常中断加载）；置顶统一走 `ctypes SetWindowPos(HWND_TOPMOST)`
4. js_api 对象的**公开属性**会被序列化进 JS 桥，挂 window/db 等对象会搞挂整条桥 → 内部引用一律下划线前缀
5. 强杀进程会污染 WebView2 用户数据目录 → 下次启动 `Main window failed to start` 或白屏；用 `WEBVIEW2_USER_DATA_FOLDER` 指向独立目录（`data/webview2`）隔离，且尽量正常退出
6. GUI 消息循环阻塞时 Python 定时器/线程可能拿不到 GIL，任何"兜底退出"要用 `kernel32.ExitProcess`

**M2/M3 待办**：托盘菜单精修、L2/L3 打卡调度（apscheduler）、停滞/到期系统通知、活跃热力图、财务汇总与导出、打包 exe（PyInstaller）。

---

## 11. M2 实施记录（2026-09-25）

### 已交付

- **`app/remind.py`**：调度器（独立后台线程，30 秒巡检）。到 `checkin_time` 且当天未打卡 → 按 `remind_level` 触发；L1 只发通知，L2 弹出打卡弹窗并每 `nag_minutes`（默认 30）重提醒，L3 弹全屏遮挡。启动 60 秒后开始扫告警，每 `alert_interval`（默认 180）发一次系统通知（停滞 + 临期 + 结款合并成一条）。
- **系统通知**：走 pystray 的 `icon.notify()`，不新增依赖；托盘未启动时静默降级。
  通知身份（2026-10-01）：启动最早期 `SetCurrentProcessExplicitAppUserModelID(AUMID)`
  并在 `HKCU\Software\Classes\AppUserModelId\<AUMID>` 幂等写 `DisplayName`/`IconUri`。
  不设的话 Win11 会生成 `NotifyIconGeneratedAumid_<哈希>`，通知显示「天在看.exe」+
  fallback 图标 —— 旧进程时代的兜底蓝方块就是这么被系统记住、托盘修好也刷不掉的。
  显式注册后显示名 =「天在看」、图标 = `res/icon.ico`。冒烟断言钉住：
  `pathinfo` 回读系统真实 AUMID（`GetCurrentProcessExplicitAppUserModelID`）必须等于常量。
- **活跃热力图**：`Board.activity(year)` 按天聚合权重 → 前端 53×7 格子（周一为首行）、5 档着色、年份切换、4 张统计卡（全年活动 / 活跃天数 / 最长连续 / 当前连续）、最近 80 条流水。
- **L2/L3 打卡**：`window.__openCheckin(level)` 由 Python 侧调度线程调用；L2 复用 `#ovCheck` 弹窗并显示重提醒提示，L3 用 `#ovBlock` 覆盖整个视口，必须勾选确认才解除。
- **托盘**：显示 / 今日打卡 / 置顶 / 取消置顶 / 打开数据目录 / 退出；tooltip 带今日必做数。
- **新增设置项**：`nag_minutes`、`alert_interval`、`notify_enabled`。
- **底栏可点**：停滞与临期数字点开看明细列表。
- 数据层测试扩展覆盖：打卡、`activity()`、停滞告警、逾期告警、调度时间判定。`tests/smoke_db.py` 全绿。

### 关键 Bug：SQLite 跨线程

**现象**：窗口能开，但树是空的，`window.__err` 报 `SQLite objects created in a thread can only be used in that same thread`。

**原因**：`sqlite3.connect` 默认 `check_same_thread=True`；连接在 `main()` 主线程创建，而 pywebview 的 JS 桥跑在内置 HTTP 服务线程、调度器又是另一个线程，两边都用同一个连接。M1 阶段就已存在，只是当时没有断言页面状态，没暴露出来。

**修复**：`check_same_thread=False` + `Db.mutex`（`threading.RLock`）把所有访问串行化；`models.py` 里直连 `self.db.conn.execute` 的地方全部改走 `db.q / db.one / db.run`。备份改用 `sqlite3` 的 `backup()` API，避免复制到写了一半的文件。

**教训**：pywebview 应用的自动化冒烟一定要断言**页面状态**（`window.__booted`、渲染行数、`window.__err`），只看"窗口有没有出来"会漏掉整类失败。这一步现在固化在 `main.py --smoke` 里。

### 冒烟结果

```
{"booted":true,"rows":5,"err":null,"title":"项目看板"}
heat={"cells":371,"cards":4,"years":1}
block=flex
```

---

## 12. M3 实施记录（2026-09-25）

### 需求确认

- 款项**可以记在项目整体，也可以记在环节 / 子任务上**（分批付款对应到阶段），两种都要进汇总
- 汇总看板形式由实现方推荐，最终做成：筛选条 + 6 张卡 + 项目款项表（可展开流水）+ 应收账龄 + 客户汇总 + 口径说明

### 已交付

- **`app/finance.py`**（新）：`Finance` 类
  - `KINDS` / `STATUS` / `DEFAULT_STATUS` / `AGE_BUCKETS` 常量化，前后端共用一套口径
  - `records()` 流水（带项目 / 客户 / 环节名）、`data()` 一次算完所有前端要的（避免多次跨桥）
  - `_counted()` 静态方法做**状态过滤**：合同作废记 0、开票未开记 0、到账待收记 0，但都仍显示在流水里
  - `_cards()` / `_quarter()` / `_by_project()`（内嵌 `records`）/ `_by_client()` / `_aging()`
  - `client_save()` / `client_delete()`（有项目挂着就拒绝删）
  - `export()` → `_write_csv()`（utf-8-sig）/ `_write_json()` / `_write_xlsx()`（4 张表）
  - `money()` 统一两位小数，避免浮点累加出现 `0.30000000000000004`
- **`app/api.py`**：`finance_data / finance_add / finance_update / finance_delete / finance_export_plan / finance_export_save / export_dir / client_save / client_delete`；写操作一律 `try/except → {"ok":False,"msg":...}`，前端 `alert` 出中文原因（导出改两步：`_plan` 只算文件名/笔数做预览、`_save` 弹系统另存为落地）
- **前端**：`renderFinance()`（年份 / 币种 / 客户三重筛选、卡片、可展开的项目表、账龄、客户汇总）、记一笔 / 编辑弹窗、客户弹窗、导出目录设置项
- **数据库**：`settings` 新增 `nosandbox`（见下）

### 跨桥调用需要每步超时

调试财务页时遇到一个典型的**桥半死**状态：`bridge_ok=True`（JS 能调 Python），但自检巡视卡在 `fin` 之后、`block` 之前，既不 resolve 也不 reject，冒烟只能干等 75 秒超时。

**处理**：

1. 给自检每一步包 `Promise.race` 超时（`guard`），把"卡死"变成"报错 + 报出停在哪一步"
2. 每个阶段 `try/catch` 记 `failed`，前面挂了就不往下跑
3. 无论成功失败**都要**把报告从 `boot_ok` 送回去（否则 Python 侧只能等超时）
4. 补 `unhandledrejection` 监听——未处理的 Promise 拒绝在 `error` 事件里看不到，会让页面静默失灵

改完之后确认：**财务页本身没有问题**，卡死是环境侧 WebView2 沙箱半死导致的。最终 `fallback` 与 `persist` 两种模式各 12 项断言全绿。

### WebView2 沙箱 + 自动降级

这台机器上，**开启沙箱的正常模式时好时坏**：有时页面正常，有时 `bridge_ok=False`（窗口在、页面不加载）。所以引入了看门狗 + 记忆：

- 看门狗等 15 秒前端报到（`api.boot_ok`），没等到就 `nosandbox=1` 落库、释放锁、带 `--no-sandbox` 重启自己
- 重启后成功 → 之后每次启动读库里的 `nosandbox=1`，直接走兼容模式，**不再白等 15 秒**
- 已经是兼容模式还失败 → 弹框提示修复 WebView2 Runtime，不再循环重启
- 设置里可手动开关「兼容模式」（改完重启生效）

端到端验证（`tests/_diag/verify_fallback.py`，全程不给任何提示性环境变量）：

```
t=  1s 第一批进程 [14252, 23884]
t= 13s 出现新进程 [34860, 36076] -> 触发降级重启
boot.log: 桥接 15 秒内未就绪，改用 --no-sandbox 重启
          启动于降级模式（--no-sandbox）
窗口存在: True
库里 nosandbox = 1
判定: PASS 降级链路 + 记忆都成立
```

### 验证矩阵

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层（M1/M2） | `tests/smoke_db.py` | PASS |
| 财务口径（M3） | `tests/smoke_finance.py` | PASS |
| 启动 / 单实例 / 孤儿锁 | `tests/_diag/verify_run.py` | PASS |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | 12/12 PASS |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | 12/12 PASS |
| 降级链路 + 记忆 | `tests/_diag/verify_fallback.py` | PASS |
| 语法 | `py_compile` 全部 + `node --check app.js` | PASS |

### 遗留

- 打包 exe（PyInstaller）留到 M4

---

## 13. M3 追加：建项目时就能登记款项（2026-09-25 晚）

### 起因

用户提的：「给添加项目的位置也设置一个能填写钱款的地方」。原设计里款项只能在财务页记，新项目刚谈定时要跳两处。

### 改法

**后端**

- `api.add_project(title, category, client_id, path, money=None)`：`money` 是可选的
  `{contract, paid, currency, tax_rate, date}`
  - 合同额 → 状态「有效」的合同记录；首款 → 状态「已收」的到账记录（备注「首款」）
  - **金额校验放在建项目之前**：填错就整体拒绝（`{"ok":False,"msg":...}`），
    绝不留下一个"以为记上了其实没记"的空项目——这是这个功能最容易出的坑
- `finance.parse_money(v, label)`（新）：严格解析手输金额，空 = 0，非数字**报错**，
  容忍千分位与「元」后缀。`add` / `update` 的金额与税率改走它
  - 为什么不复用 `_f`：`_f` 是宽容的（脏数据当 0），内部汇总这么干没问题，
    但拿它解析用户输入会把「1万」「6%」**静默变成 0**，钱就悄悄丢了
  - `_tax()` 额外容错 `6%` 这种写法

**前端**

- `#ovNew` 弹窗加三行：客户（下拉，打开时重填）/ 合同额（金额 + 币种 + 税率）/ 首款到账（金额 + 日期，默认今天），弹窗加宽到 520px
- 项目行 ⋯ 菜单加 **「登记款项」**：先 `loadFinance()`（弹窗要用财务侧的项目/客户/环节列表），
  再 `openFinDialog(null, n.id)` 预选该项目；项目若已归档（不在当前树里）会明确报错而不是静默落到别的项目上

### 踩到的坑：一个 WebView2 profile 只能有一个环境

改完后冒烟突然全挂，`bridge_ok=False`，窗口都起不来：

```
[pywebview] WebView2 initialization failed with exception:
  (0x8007139F): 组或资源的状态不是执行请求操作的正确状态。
  ...CoreWebView2Environment.<CreateCoreWebView2ControllerAsync>
Failed to unregister class Chrome_WidgetWin_0. Error = 1411
```

不是代码问题，是**19:14 起的那个真实看板一直开着**，占着 `data\webview2`；
同一个用户数据目录不能被两个 WebView2 环境同时用，第二个在建环境阶段就死。
症状和「沙箱把页面挡了」一模一样，很容易误诊。

处理：

1. 冒烟改用**独立 profile** `data\webview2_smoke`（仍在项目内，ACL 正常；
   系统 Temp 下新建目录 ACL 过宽会被沙箱拒，见 §12）
2. `verify_run.py` / `verify_fallback.py` 开跑前若检测到已有看板窗口 → 打印提示并 `exit 2`，
   不再傻跑出误导性结论
3. 这两个脚本的清理从 `taskkill /F /IM pythonw.exe` 改成**只杀本测试自己起的 pid**——
   原写法会把用户正开着的看板一起杀掉

顺带一个必须记住的副作用：**应用在跑就一直吃旧前端**，改了 HTML/JS 不重启看不到，要主动告知用户。

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 财务口径（M3） | `tests/smoke_finance.py` | PASS |
| 建项目带款项 | `tests/smoke_project_money.py` | PASS |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | 18/18 PASS |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | 18/18 PASS |
| 语法 | `py_compile` + `node --check app.js` | PASS |
| 启动 / 降级链路 | — | 本轮未复跑（未改启动相关代码，且看板开着跑不了） |

新增的 6 项断言里，最后两项是**真在 UI 上点了一次**「新建项目 + 合同额 1234 + 首款 234」，
再回读汇总确认 `contract/received/pending` 和客户归属都对——这是 DOM 取值 → 跨桥 → 落库整条链路的证据。

### 遗留

- 打包 exe（PyInstaller）留到 M4

---

## 14. M4 实施记录（2026-09-25 深夜）

### 已交付

- **拖拽排序**：`Board.move(kind, oid, target_id, pos)`，`pos ∈ before / after / inside`
  - projects：同分类 + 同 `pinned` 组内重排 `sort_order`
  - nodes：`before/after` 换位置，`inside` 改 `parent_id`；`target_id=None` 表示回到项目最外层
  - 前端 `#tree .row` 上挂 `draggable`，按鼠标在行内的相对高度决定落点，
    三种指示样式（上/下蓝线 = 插到前面/后面，整框虚线 = 放进去）
- **批量折叠**：`set_collapsed_all(mode)`，一条 UPDATE 落库；`expand / collapse / toProject` 三态
  - 三个界面按钮后来按用户要求撤掉了，能力**保留在 API 层**（回归测试仍在覆盖），不再有界面入口
- **快捷键**：`Ctrl+N / 1 / 2 / 3 / D / ,` 与 `Esc`，统一收进 `bindHotkeys()`
  （原来 Escape 那行散在启动段）
- **快照回滚**：`Db.snapshots() / snapshot_now() / restore()` + 设置页「数据快照」区块
- **补齐「常驻顶部」**：`pinned` 列原本是死列（只出现在 `ORDER BY` 里，没有任何写入入口），
  这次在项目行 ⋯ 里接通，和拖拽排序配套

### 设计上刻意为之的几处

1. **拖不动必须说原因**。跨分类、跨项目、拖进自己的后代、跨置顶线，四种情况都在 Python 侧
   `raise ValueError`，由 `Api.move` 转成 `{ok:false, msg}` 给前端弹提示。
   「默默不动」是这里最坏的结果——用户会以为是自己手抖，反复试。
2. **排序不算「有进展」**。`_move_node` 不调 `_touch()`，否则拖几下就能把「停滞 N 天」的告警
   刷掉，等于把 M2 的督促机制废掉。测试里有这条断言。
3. **成环防护**。`_is_descendant()` 沿 `parent_id` 上溯，带 512 次上限（防脏数据成环时空转）。
   `before/after` 也要查：把父节点排到自己子节点的「前面」同样会成环。

### 修掉的两个真 Bug

1. **`backup()` 会静默覆盖同名快照**。文件名精确到秒，而回滚时的「先存一份当前状态」紧跟
   手动备份，极易落在同一秒 → 被覆盖掉的偏偏是**回滚源文件**，恢复出来的是错数据。
   改成目标已存在就追加 `-1`、`-2`。
2. **`restore()` 验证快照时连接泄漏**。查询失败（假文件 / 空文件）会跳过 `chk.close()`，
   句柄一直占着那个文件，用户之后连删都删不掉（`WinError 32`）。
   改成 `try/finally` 强制关闭。这是跑失败路径用例时才暴露出来的。

### 踩到的坑：并行编辑同一文件会互相覆盖

一次性提交了两个 `Edit` 到同一个文件，两边都基于原始内容，后写的把先写的**整个覆盖**——
文件里只剩一个改动，但两次调用都返回成功。改文件必须串行，尤其一个文件多处改动时。

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 拖拽 / 折叠 / 快照（新） | `tests/smoke_m4.py` | PASS（exit 0） |
| 财务口径（M3） | `tests/smoke_finance.py` | PASS |
| 建项目带款项 | `tests/smoke_project_money.py` | PASS |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | 23/23 PASS |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | 23/23 PASS |
| 语法 | `py_compile` + `node --check app.js` | PASS |

GUI 新增 7 项断言，其中「拖拽排序真的落库」是**真调了一次 move**：把最后一个商业项目
拖到首位、回读确认顺序变了，再拖回原位确认还原——不是只检查 DOM 上有没有 `draggable` 属性。

### 遗留

- 打包 exe（PyInstaller）：要把 `app/web/` 静态资源、`_boot.py`、WebView2 参数都带进去，
  且 exe 路径下的 `data/` 与 `WEBVIEW2_USER_DATA_FOLDER` 都得重新验证一遍，单独一轮做

---

## 15. 顶栏改版：分类筛选 → 两个页面页签（2026-09-26 凌晨）

### 起因

用户提的：「顶部的项目分类页（全部 / 商业 / 个人）这块仅保留项目一个页面，另外将财务页面放在这里，
即保留两个可切换页面，一个项目，一个财务」。

原来顶栏左侧是**分类筛选**（全部 / 商业 / 个人），财务却挂在右侧和「活跃」「设置」混在一起做按钮，
两个概念挤在同一栏里，层级关系看不出来。

### 改法

- 顶栏左侧换成**页面页签** `#segPage`：`[项目] [财务]`，`data-v="tree|fin"`，比普通 `.seg` 大一圈
  （`padding:5px 20px`），一眼能看出是「换页」而不是「筛分类」
- **分类筛选整个撤掉**。商业 / 个人本来在树里就是分组标题行（`商业项目` / `个人项目`，
  带项目数 · 进行中数 · 进度条），筛选按钮是冗余的第二套表达；`render()` 里的 `filter` 逻辑一并删掉，
  顺带干掉一段永远走不到的 `if (!any)` 空态分支
- 右侧按钮组的 `[财务]` 撤掉（已移到左侧页签），保留 `[置顶] [打卡] [活跃] [设置] [＋ 项目]`
- **活跃（热力图）没进页签**。用户说的是两个页面，热力图更像附加视图，仍用 `[活跃]` 按钮开合；
  开着热力图时两个页签都不高亮，由按钮自身的高亮态说明状态。这条在 `setView()` 里写清楚了

### 一个让冒烟更硬的做法

新增 `switchPage(v)`：先找 `#segPage button[data-v=v]` 真点一下，找不到才退回 `setView(v)`。
自检巡视的 `fin` 阶段改用 `switchPage("fin")`，于是**「点页签切页面」这条真实链路**被纳入冒烟，
而不是只验「调 setView 后样式对不对」。

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | 27/27 PASS |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | 27/27 PASS |
| 语法 | `node --check app.js` | PASS |

新增 4 项断言：顶栏页签数 = 2、页签文案就是「项目 / 财务」、旧的 `#segFilter` 按钮数 = 0、
点页签切页后 `#fin` 显示 / `#tree` 收起 / 页签高亮落在 `fin` 上。

### 追加：活跃也并入页签（当晚笔一项修正）

用户看了效果后提：「将活跃也作为一个切换页，跟项目，财务放在一块」。

原来把热力图留成「叠加视图」是我按"用户说了两个页面"字面推断的，实测下来三个视图本来就是
**同级互斥**的（`setView` 里 `tree/heat/fin` 三选一，谁显示谁不显示是一组），留一个在按钮上
反而要把「当前在哪个视图」拆成两处状态（页签高亮 + 按钮高亮），是自找的复杂度。

- `#segPage` 变成三格：`[项目] [活跃] [财务]`，`data-v="tree|heat|fin"`，`padding` 收到 `5px 18px`
- 右侧按钮组的 `#btnHeat` 撤掉，`setView()` 里那句 `btnHeat.classList.toggle("on", ...)` 一并删，
  现在页签高亮是**唯一**的当前页指示
- 自检巡视的 `heat` 阶段也改用 `switchPage("heat")`，并回传 `tabAct`；
  M4 的快捷键用例加一项 `hotkeyTab`（`Ctrl+2` 切页后页签要跟着亮）

### 验证矩阵（追加轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | 29/29 PASS |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | 29/29 PASS |
| 语法 | `node --check app.js` | PASS |

页签断言改成 3 个 + 文案 `["项目","活跃","财务"]`，另加「点活跃页签后页签高亮 = heat」
与「`Ctrl+2` 切页后页签也亮」。

---

## 16. M4 收尾：PyInstaller 打包（2026-09-26 凌晨）

### 已交付

- `app/paths.py`（新）：把「读到哪、往哪写」集中成一个模块，源码运行与 frozen 两套布局在这里分叉
- `tools/build_exe.py`（新）：一条命令出包，含前置 `py_compile`、hook 参数、改名、体积统计
- `tests/_diag/smoke_exe.py`（新）：真的把 exe 跑起来做页面自检，断言渲染统计 + 路径落点
- `app/main.py` / `_boot.py` / `api.py` 全部改走 `paths`，源码模式行为**逐条对齐**（见下表）

### 两条根目录，不能混

| | 源码 | 打包后 |
| --- | --- | --- |
| 只读资源 `web/` | `app/web/` | `sys._MEIPASS/web/`（打进包） |
| 可写 `data/`、日志、WebView2 profile | `Task-Overview/data/` | **exe 同级** `data/` |

判据只用 `sys.frozen`，不用 `__file__` 猜。`data/` 落到解包临时目录是这类打包最典型的错：
WebView2 profile 每次启动都是新的，而且被强杀时污染的是没法清理的临时目录。

### 顺带修掉的几处 frozen 才暴露的问题

1. **降级重启命令**。原来看门狗拼 `[sys.executable, "_boot.py"]`；打包后 `_boot.py` 根本不在包里，
   exe 会把它当普通参数丢给 argparse。改成 `paths.restart_cmd()`：frozen → `[exe] + argv[1:]`。
2. **开机自启写注册表**的命令行同理，frozen 下只写 exe 路径。
3. **`--smoke` 下的模态框**。`_fatal` / 打开库失败 / 已被占用三处都弹 `MessageBoxW`，
   无人值守跑的时候会一直等点击，表现成"启动卡死"。`--smoke` 时一律只写日志。
4. **工作目录**。从快捷方式启动时 CWD 可能是 system32，frozen 下统一 `chdir` 到 exe 同级。

### 打包参数的取舍

- **默认 onedir**：pythonnet 要加载 `webview/lib` 里的 .NET 程序集，DLL 躺在 exe 旁边最稳；
  onefile 每次启动解包 40 MB 到临时目录，慢且更容易出幺蛾子
- pywebview 自带 PyInstaller hook（`webview/__pyinstaller/hook-webview.py`）会把
  `Microsoft.Web.WebView2.*.dll` / `WebView2Loader.dll` 收进来，`pyinstaller-hooks-contrib`
  覆盖 `clr` / `clr_loader` / `pystray`。仍然显式加 `--add-data app/web;web`、
  `--add-data app/res;res` 和四个
  `--hidden-import`（平台后端与托盘后端是运行时挑的，静态分析看不到）。
  ⚠ res 的 add-data 源必须是 `app/res` **不能写成 `paths.res_dir()`**（源码下它 = `app/`
  目录）—— 2026-10-01 踩过：整个 app/ 被塞进 `_internal/res/`，图标实际落在
  `_internal/res/res/icon.ico`，frozen 下 `icon_file()` 找不到 → 托盘永远显示
  兜底纯色方块。现在 build_exe 落位后自检 `res/icon.ico`、`res/logo.svg`、
  `web/index.html` 三处落点，smoke_exe 也钉住了
- **ASCII 名构建、出包后改名**：中文名喂给 `--name` 会在 build 里造一堆中文路径；
  onedir 的配套目录固定叫 `_internal`、不随 exe 名走，所以改名安全
- **打包脚本不删任何目录**：旧的改名成 `项目看板.prev-<时间戳>` 让位。
  批量删目录不可逆（本工程的沙箱也会直接拦），留给用户确认后自行清理

### 排错过程（值得记）

第一次 exe 冒烟卡在自检半路：`bridge_ok=True`，`stages` 只走到 `fin`（第二次更早，只到 `tree`）。
**每次停的位置都不一样**，这本身就说明不是某条 API 的问题，而是环境。对照源码冒烟一直只跑
`fallback` / `persist` 两档，正是因为这台机器的 WebView2 渲染进程沙箱时好时坏。
把 exe 冒烟也分成 persist / fallback / sandbox 三档后，persist 档**整个自检一次跑完**：

```
stages: tree heat fin block m4 create done      frozen=true
base/data: ...\dist\项目看板 和 ...\dist\项目看板\data
```

顺带确认：`data\webview2` 确实建在 exe 同级 —— 这条现在是断言，不再是"看起来对"。

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 打包 | `python tools/build_exe.py` | PASS（exe 5.9 MB / 目录 43.5 MB / 16~18s） |
| exe 自检 · persist | `tests/_diag/smoke_exe.py persist --exe <对照构建>` | 全阶段跑完，`frozen=true`、路径落点正确 |
| 源码 GUI 渲染 | `tests/_diag/smoke_fin_gui.py persist` | **本轮未复跑**（见遗留） |

### 遗留

- ~~`paths.py` 重构后，源码模式的 GUI 冒烟这一轮**没跑**~~ → **2026-09-26 已补跑**：
  `smoke_fin_gui.py fallback` 与 `persist` 各 41/41 PASS。
- `dist\项目看板\data\` 是第一次冒烟留下的 WebView2 profile，本人确认新版没问题后可自行清理。

---

## 17. 三处使用问题修复（2026-09-26）

用户实战反馈三条，全部是「功能在代码里看着有、用起来没有」那一类：

### 17.1 「浏览」按钮点了没反应 → pywebview 6.x 删掉了模块级接口

`api.pick_dir` 原来调 `webview.create_file_dialog(webview.FOLDER_DIALOG, ...)`。
**pywebview 6.x 把这个函数从模块级挪到了 `Window` 上**（`webview.create_file_dialog`
已经不存在，只有 `Window.create_file_dialog`），所以那次调用抛 `AttributeError`
→ 被 `except Exception` 吞成 `{"ok": False, "msg": ...}` → 前端当时写的是

```js
if (res.ok && res.path) $("#newPath").value = res.path;   // 失败时什么都不做
```

**两头一起把错误吃干净了**，症状就是「点浏览没反应、也没有任何提示」。

修法两层：

1. `win = state["window"]` → `win.create_file_dialog(webview.FileDialog.FOLDER, directory=...)`，
   失败原因写进 `data/pick.log` 并回给前端
2. 前端拿到 `ok: false` 就 `alert` 出来 —— **失败必须看得见**，否则下次还是同一个坑

单独加了 `tests/_diag/smoke_pick_dir.py`：真弹一次对话框，2 秒后从另一个线程
`EnumWindows` 找到它（class `#32770`、属于本进程）发 `WM_CLOSE`，
断言「对话框确实出现过」+ `pick_dir` 返回 `{"ok": true, "path": ""}`。
之所以要真弹：这个调用发生在 js_api 的**独立线程**上，纯单测永远是绿的。

### 17.2 一行文本建一批子环节

新增 `app/node_spec.py`（纯函数，无依赖），解析规则：

| 写法 | 结果 |
| --- | --- |
| `s001,s003A,s006-009` | s001 · s003A · s006 s007 s008 s009 |
| `C001-C003` / `EP01_S001_C001-C005` | 连续号（右边前缀是左边前缀的后缀即可） |
| `s006-9` | 右侧继承左侧前缀 → s006…s009 |
| `s09-s06` | 倒序也认 |
| `SANTI-OneDay` / `shot-001` / `s001_alpha` / `2026-09-25` | **字面量**（左边无数字尾巴 / 右边不是数字结尾 / 多个破折号） |
| `"phase1-3"` | 引号 = 强制字面量 |

- 分隔符：`,` `;` `，` `；` `、` 与各类空白
- 上限：单个连续号 ≤ 200 项、单次 ≤ 500 个（防 `1-99999` 手滑）
- **去重的两层**都在：提交内去重（不分大小写）、以及**库内同级已存在就跳过**，
  返回 `created` / `skipped` 两份清单，前端把「跳过了 N 个已存在的」讲出来

为什么把解析放 Python 而不是 JS：预览和落库必须用**同一个**解析器，
否则预览说有 6 个、实际建出别的数量，比没有预览更糟。
所以前端调 `api.parse_nodes(spec)` 拿结果做实时预览，`add_nodes` 走同一份代码。

界面上新增 `#ovNodes` 弹窗（textarea + 规则说明 + 预览区），
已存在的名字在预览里划掉、新名字标蓝，**拿不准的写法看一眼预览就行**。
`api.add_nodes` 在 `models` 层是**一次事务 + 一次 touch + 一条流水**，
不是循环调 `add_node`（几十个环节就是几十次跨桥往返 + 几十条日志）。
`add_node` 保留原样，仍给别处用。

### 17.3 客户填不了 → 从「只能选」改成「可选可填」

原来看起来是「客户项无法指定」：`#newClient` 是个 `<select>`，候选取自 `clients` 表。
**库里一个客户都没有的时候，这个下拉里只有「（不指定）」一项** ——
用户既选不了也填不了，得先去财务页建客户再回来，而那条路并不明显。

现在：

- `#newClient` 换成 `input + datalist`（组合框），候选来自 `D.clients`，**也可以直接敲新名字**
- `api.add_project` 第三参数接受**客户 id 或客户名**：`Finance.client_find_or_create(name)`
  按名字找（忽略大小写与首尾空白），没有就建。`api.set_client(project_id, client)` 同理
- 项目行 `⋯` 里加了「指定客户…」：列出全部客户（当前那个打勾）+「新建客户…」+「不挂客户」，
  老项目也能改，不用绕财务页

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 解析回归 | `tests/smoke_node_spec.py` | **OK（75 项断言）** |
| 数据层 | `smoke_db` / `smoke_finance` / `smoke_project_money` / `smoke_m4` | 全 OK |
| 文件夹对话框 | `tests/_diag/smoke_pick_dir.py fallback` | **PASS**（对话框出现过，`{"ok":true,"path":""}`） |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | **41/41 PASS** |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | **41/41 PASS** |
| 语法 | `node --check app.js` + `py_compile` | PASS |

GUI 断言从 29 加到 41：客户框是 `INPUT` + 有 `list`、候选里有种子客户、
「浏览」按钮已绑事件、客户框写新名字会自动建客户且客户数 1→2、
以及批量录入的 5 条（弹窗打开 / 预览报数 / 6 个名字都在 / 真建出 6 个 / 重复批次全跳过）。

### 顺带修掉的小问题

- `Esc` 现在真的关弹窗了（设置里那句提示早就这么写，但代码只关 popup 菜单，没关遮罩层）；
  `#ovBlock`（打卡拦截层）除外 —— 那个必须处理完才能走。
- 自检巡视的顺序：`nodes` 阶段放在 `create` 之前，`create` 仍然最后跑（新增数据不影响前面的渲染计数）。

## 18. 环节状态改版：制作流转 9 态（2026-09-26 凌晨）

### 改法

原 `未开始 / 进行中 / 阻塞 / 待确认 / 完成 / 搁置` → 新的制作流转：

```
主线  待开始 → 等上游 → 制作中 → 已提交 → 反馈 → 通过 → 交付
旁支  暂停（等条件） · 取消（这条不做了）
```

- **只改环节层**。项目层保持 `筹备 / 进行中 / 阻塞 / 待交付 / 已交付 / 已结款 / 归档 / 搁置`
  不动 —— 项目层的 `已结款 / 归档` 还牵着账龄与归档隐藏，"两层换成同一套词"会把这些联动打散
- **收工态** `通过 / 交付 / 取消` 统一成 `models.NODE_CLOSED`：到期提醒跳过、停滞不再算，
  前端 `CLOSED` 集合同步（`app.js` 里那张表要跟后端对齐）
- 勾选框语义跟着状态走：叶子节点勾上 → `通过`，取消 → `制作中`
  （不然会出现「勾上了状态还写着制作中」的自相矛盾）

### 老库迁移

`db.NODE_STATUS_MIGRATE`（旧词 → 新词），在 `Db._connect` 里每次开库跑一遍：

| 旧 | 新 |
| --- | --- |
| 未开始 | 待开始 |
| 进行中 | 制作中 |
| 阻塞 | 等上游 |
| 待确认 | 反馈 |
| 完成 | 通过 |
| 搁置 | 暂停 |

幂等（改完就不再命中），且**快照回滚也走 `_connect`** —— 旧快照恢复回来会被顺手迁移，
不会把老状态词带回界面。放数据层而不是 `models.py` 是为了避开 `models → db → models` 的循环引用。

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层（含 9 态 / 勾选联动 / 迁移幂等 / 收工态不报到期） | `tests/smoke_db.py` | OK |
| 其余数据层 | `smoke_finance` / `smoke_project_money` / `smoke_m4` / `smoke_node_spec` | 全 OK |
| GUI 渲染 · fallback | `tests/_diag/smoke_fin_gui.py fallback` | **42/42 PASS** |
| 语法 | `node --check app.js` + `py_compile` | PASS |

GUI 新增一条：**真点一次环节行的状态徽章**，读弹层里 9 项文案，断言就是新词表
—— 只读 `D.status.node` 证明不了界面真接的是它。

踩到的坑：新加的到期断言用 `x["id"] == fresh` 判节点，撞号了（项目和节点的 id 各自自增），
必须带 `owner` 一起判。`smoke_m4.py` 里踩过同一个坑，这次又踩一遍。

---

## 19. 实战反馈四项（2026-09-26）

用户用了一轮之后提的四条，都是"用起来疼"的地方。

### 19.1 撤掉展开折叠按钮，改「默认两个分组行展开、项目收起」

原 `#treeBar` 三个按钮（全部展开 / 全部折叠 / 只看项目）整个撤掉，工具栏只剩右侧那句拖拽提示。

- `projects.collapsed` / `nodes.collapsed` 的 **schema 默认值改成 `1`**
- 老库靠 `Db._migrate_collapse_default()` 一次性折起来：`settings.mig_collapse_default` 当标记位，
  跑过一次就不再动（用户后来手动展开的项目不会被下次启动又折回去）
- **`set_collapsed_all()` 保留在 API 层**（回归测试遮着），只是不再有界面入口

为什么不是"直接把按钮删了、默认值不管"：默认展开时打开看板是十几个项目全摊开，
几百个环节一起涌出来，第一眼是噪声不是信息。用户要的是"先看到有哪些项目"。

### 19.2 外包支出：不计入个人实际收入

新增第四种款项 `outsource`（外包支出），状态 `应付 / 已付 / 作废`。

- **只有「已付」冲减**：`实际收入 = 已收 − Σ 外包(已付)`。`应付` 先不扣 —— 钱还没出去，
  提前扣会把自己的收入看低；`作废` 同理不参与
- 汇总卡从 6 张变 7 张：`总合同额 / 已收 / 外包支出 / 实际收入 / 待收 / 本月到账 / 收款率`
- 项目款项表、客户表都改成 `… 已收 / 外包支出 / 实际收入 / 待收 …`（网格列宽同步调成 9 列）
- `_counted()` 里外包走独立分支，`_accumulate()` 的累加器加 `outsource` 字段 —— 
  口径的口子只开在 `finance.py` 一处

### 19.3 看板上不再展示开票口径

用户原话「开票与否的金额可不在看板上展示」。

- 汇总卡去掉「未开票」，明细表去掉「已开 / 未开票」两列
- **`uninvoiced` 字段和导出里的列全部保留**：口径没变，只是不占看板版面了。
  数据完整性不能因为"暂时不看"就砍掉 —— Excel 的「项目汇总」「客户汇总」两表仍然带开票列

### 19.4 进度条恒为 0（真 Bug）

**症状**：项目行的进度条和 `0/1` 一直是 0，但用户明明把环节改成了「通过」。

**根因**：进度只认 `nodes.done` 这个勾选框字段。而用户的实际习惯是**点状态徽章改状态**
（`通过 / 交付`），从来不碰那个小圆点，于是 `done` 永远是 0。

**修法**：把「完成」的判定从字段搬到状态上，读数据时派生：

```python
NODE_DONE = ("通过", "交付")           # models.py
"done": 1 if (r["done"] or r["status"] in NODE_DONE) else 0,   # Board.load()
```

- **不动数据库里的 `done` 列**，只在 `load()` 里派生 —— 老数据不用迁移，
  勾选框与状态两条路都能得到"完成"
- `NODE_DONE` 与 `NODE_CLOSED`（`通过/交付/取消`）**刻意分开**：`取消` 是收工但**没做完**，
  算进进度会虚高
- `set_done()` 仍然写状态（勾上 → `通过`），所以两边天然一致，不会出现"勾了但进度没动"

### 19.5 新增「制作人」字段

- `projects.artist` / `nodes.artist` 两列，`Db._migrate_columns()` 开库时 `ALTER TABLE` 补上（幂等）
- `set_field` 白名单放行 `"artist"`；`load()` 返回；行内**挨着备注**显示成 `制作人 老王`
- 行尾 ⋯ 加「编辑制作人（当前值）」，`prompt` 改、留空清除；项目行和环节行都能用
- `.meta.who` 样式：细边框小胶囊，跟纯文本备注区分开

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层（含 artist 补列 / 默认折叠迁移 / 9 态） | `tests/smoke_db.py` | OK |
| 财务口径（含外包只扣已付、导出保留开票列） | `tests/smoke_finance.py` | OK |
| 建项目带款项 / 拖拽快照 / 解析 | `smoke_project_money` · `smoke_m4` · `smoke_node_spec` | 全 OK |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | **54/54 PASS** |
| 打包 | `python tools/build_exe.py` | PASS（exe 5.9 MB / 目录 43.6 MB / 31.6s） |
| exe 自检 · persist | `tests/_diag/smoke_exe.py persist` | **22/22 PASS**，`frozen=true` |
| 语法 | `node --check app.js` + `py_compile` | PASS |

exe 冒烟新增/改写的断言：默认折叠态行数 5（不是 8）、展开折叠按钮已撤、
财务 7 卡、外包/实际收入口径、制作人显示+菜单、进度条按状态算（`1/1` + `barW=100%`）。

---

## 20. 财务页筛选栏收敛（2026-09-26）

用户原话：「财务页面，只有人民币结算，所以去掉 cny 按钮，年份做成下拉菜单，
导出相关的命令也做成一个下拉菜单，以保持页面简洁」。

### 改前的筛选栏长什么样

```
[全部年份][2026] [CNY] [客户▾]              [客户] [导出目录] [导出 Excel][CSV][JSON] [＋ 记一笔]
```

一行里塞了 **8 个可点元素**，其中：

- 年份是一排按钮 —— 年份一多就横向溢出（`flex-wrap` 之后换行，两行更乱）
- `[CNY]` 是数据驱动的币种切换 —— 只有人民币结算时它就是一个恒亮的占位按钮
- 导出相关的命令**被拆成四处**：「导出目录」一个独立按钮 + 三个格式按钮横排。
  它们其实是同一件事（把当前视图落成文件）的四种做法，摊开占掉半条栏

### 改后

```
[全部年份▾] [全部客户▾]                    [客户] [导出 ▾] [＋ 记一笔]
```

- **年份改 `<select>`**：`#finYear` 从 `.seg`（动态生成按钮）改成原生 select，
  选项 `全部年份` + `YYYY 年`。选项由 `f.years` 驱动，逻辑不变，只是表达方式收敛
- **币种筛选整组删除**：`#finCcy` 的 DOM、生成按钮的那段循环、以及「切币种重新取数」的
  交互全部去掉。`finCcy` 变量保留并固定为 `"CNY"` —— `finance_data(year, client, currency)`
  的参数、`money()` 的前缀符号都还用它，**改 API 没必要，界面不暴露就够了**
- **导出收成一个下拉菜单**：`#finExport` 从 `.seg` 改成普通按钮，点击走现成的
  `popup(items, anchor)`（状态徽章、行 `⋯`、底栏告警都在用同一个），菜单四项：
  导出 Excel / 导出 CSV / 导出 JSON /（分隔线）/ 打开导出目录。`#finDir` 独立按钮删除

### 顺带：弹窗里的币种输入也撤了

既然「只有人民币结算」，两处币种输入框（新建项目弹窗的 `#newCcy`、记一笔弹窗的 `#finCcyIn`）
一并去掉，位置换成灰色的「元」提示。

**但老库里的外币记录不能被悄悄改掉**：新增 `finEditCcy`，打开弹窗时记下这笔原本的币种
（`rec.currency`），保存时原样写回；只有**新建**才用 `"CNY"`。
界面收掉的是「选择」，不是「数据」——同一个道理，`f.currencies` / `uninvoiced` 这些
字段和导出里的列都留着。

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | **62/62 PASS** |
| 打包 | `python tools/build_exe.py` | PASS（exe 5.9 MB / 目录 43.6 MB / 18.6s） |
| exe 自检 · persist | `tests/_diag/smoke_exe.py persist` | **24/24 PASS**，`frozen=true` |
| 语法 | `node --check app.js` + `py_compile` | PASS |

GUI 新增 8 条：年份是 `SELECT`、年份选项 = `["全部年份", "2026 年"]`、客户也是 `SELECT`、
`#finCcy` 已不存在、`#finDir` 已不存在、`#finExport` 是 `BUTTON`、
点开导出菜单恰好是那四项、新建项目弹窗里 `#newCcy` 已不存在。

其中「点开导出菜单看四项」是**真点一次**再读 `.popup` 的 `children`
—— 只看按钮存在证明不了菜单接的是这几条命令。

> 计数修正：上一轮（§19）GUI 断言实际是 **54 项**，我按老数字写成 42，
> 本轮补齐为 62。以后加断言**顺手跑一次 `grep -c "^  PASS"`**，别沿用记忆里的数。

## 21. 财务默认本年 + 卡片两行 + 项目页筛选（2026-09-26）

用户原话：「财务页 默认展示本年份，钱款展示分两行，第一行展示总额，已收，待收。
第二行展示外包，实际，收款率即可。本月到账就不需要展示了。项目页内添加筛选展示菜单。
可按项目状态，筛选展示项目，尤其需要控制归档项目的展示与否。」

### 21.1 财务：默认本年份

`let finYear = new Date().getFullYear()`。原来那句
`if (!fin.years.includes(finYear)) finYear = 0` 是坑：**库里今年还没有任何记录时，
切一次「全部年份」就再也回不到本年**（本年不在 `f.years` 里，会被重置成 0）。
改成下拉选项 = `f.years ∪ {本年份}` 排序去重，本年永远在列表里，也就不需要这句复位了。

### 21.2 汇总卡两行三张

```
第一行  总合同额   已收      待收      ← 生意多大 / 进来多少 / 还欠多少
第二行  外包支出   实际收入   收款率     ← 成本 / 真正落袋 / 回收进度
```

- 结构从「一个 `.hcards` 装 7 张」改成 `#finCards` 里两行 `.hcards`（各 3 张）；
  `#finCards .hcards{margin-bottom:9px}` 把行距收一点，不然两行之间空得发虚
- 去掉「本月到账」：季度那行本来就写着「已到账」，卡片上再放一张是重复信息
- 卡片数 7 → 6，列宽/顺序不动

### 21.3 项目页筛选菜单

工具栏加了 `#treeFilter`（「筛选 ▾」），菜单 = 归档开关 + 分隔线 + 全部状态 + 8 个项目状态。

**归档的过滤放在后端**，不是前端藏：

```python
# models.Board.load(include_archived=False)
"WHERE (p.archived=0 OR ?)"   # 参数 0/1
```

```python
# api.Api.load(include_archived=0)  →  前端的 reload() 用 ui.hideArch ? 0 : 1
```

理由：归档是「收进抽屉」，默认就不该发往前端；前端藏只是视觉上的，数据还在手里。
代价是归档行需要一条回程路 —— 所以 `⋯` 菜单在 `n.archived` 时变成「取消归档」，
否则归档完就再也捞不出来（早先只有单向的「归档」）。

**开关的措辞后来反转过**（用户要求「将显示归档项目 改为隐藏归档项目」）：
`ui.arch`（1 = 把归档要过来）改成 `ui.hideArch`（1 = 不要归档），**默认就是 1**。
反着写的好处是默认态与菜单文案一致 —— 菜单上是个**已经勾上的「隐藏归档项目」**，
而不是一个没勾的「显示归档项目」让人怀疑归档到底藏没藏。

- 换键名时**必须丢掉老键**：旧语义下开了归档的人（`arch=1`）用新逻辑一读就是「不隐藏」，
  归档项目全冒出来，跟他上次设的正好相反。初始化时 `delete ui.arch` 并补 `hideArch=1`，
  且**只在缺失时补**，用户手动改成 0 之后不会被下次启动掰回去
- 「隐藏」是默认态，所以按钮只在**关掉隐藏**时才点亮、才写「含归档」：
  一进来就高亮会让人以为开了什么非默认的筛选

**状态筛选留在前端**（`visibleProjects()`），纯展示、省一次跨桥往返。
项目数据里新增 `archived` 字段供前端标徽章（`.badge.arch` 虚框 + 行压暗 `.row.arch`）。

两个刻意的设计点：

- 分组行的**进度条和比例仍按整个分组算**（`sumOf(g.projects)`），只有条数写「筛后（共 N）」。
  条是「这个分类整体做到哪了」，筛个状态就跟着跳会让人以为项目变少了
- `reload()` 里校验 `ui.pStat` 是否还在 `D.status.project` 里：状态词表改过版（比如环节那套 9 态），
  localStorage 里可能留着旧词，**留着一个谁都匹配不上的状态，打开就是空树**

### 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层 5 个回归 | `tests/smoke_{db,finance,project_money,m4,node_spec}.py` | 全 OK |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | **73/73 PASS** |
| 打包 | `python tools/build_exe.py` | PASS（exe 5.9 MB / 目录 43.6 MB / 19.8s） |
| exe 自检 · persist | `tests/_diag/smoke_exe.py persist` | **27/27 PASS**，`frozen=true` |
| 语法 | `node --check app.js` + `py_compile` | PASS |

GUI 新增 11 条（62 → 73）：卡片 6 张 / 两行 `[3,3]` / 两行的标签顺序 /
卡面上不再有「开票」「本月」/ 年份默认 `str(date.today().year)` /
工具栏只剩「筛选 ▾」/ 菜单首项是归档开关 / 菜单共 11 项含归档与搁置 /
真点一次筛「搁置」只剩 1 行且就是那条 / 按钮上写着当前筛的 / 切回后行数与按钮都复位 /
归档默认看不到 / 开了归档能看到且带徽章。

**筛状态要挑得出差别的词**：种子里三条项目全是「进行中」，
直接筛「进行中」筛前筛后都是 3 行，断言等于没断言 ——
所以用例里先把一条改成「搁置」，筛完再改回来（`filterExpect` 把预期标题一起回传）。

数据层也加了 2 组：`load()` 默认不含归档、`load(include_archived=True)` 带回 `archived=1` 且
**每个项目都带 `archived` 字段**（前端徽章靠它，缺字段不会报错、只会静默不显示）。

---

## 22. 归档开关措辞反转（显示 → 隐藏）

用户一句话：「项目筛选处 将显示归档项目 改为隐藏归档项目」。

只动了一个开关的方向，但牵连到 localStorage 里已经存着的值，所以不是改个文案的事：

| 旧 | 新 |
| --- | --- |
| `ui.arch`，1 = 把归档项目一起要过来 | `ui.hideArch`，1 = 不要归档 |
| 默认 `ui.arch = 0`（不显示） | 默认 `ui.hideArch = 1`（隐藏） |
| 菜单写「显示归档项目」，默认未勾 | 菜单写「隐藏归档项目」，默认**已勾** |
| `reload()` 传 `ui.arch ? 1 : 0` | `reload()` 传 `ui.hideArch ? 0 : 1` |
| 开着归档时按钮写「含归档」 | **关掉隐藏**时按钮才写「含归档」 |

行为本身没变（默认都是不显示归档），变的是表述与「勾上 = 干什么」。

**换键名而不是复用 `arch`**：旧值在新语义下是反的 —— 之前在旧开关里勾了「显示归档」的人
（`arch=1`），新逻辑读到会当成「不隐藏」，一升级归档项目全冒出来。所以初始化时
`delete ui.arch`，缺 `hideArch` 才补 1（判据 `("arch" in ui || ui.hideArch === undefined)`，
**只在缺失时补**，用户手动关掉之后不会被下次启动掰回去）。

「隐藏」是默认态，所以按钮默认**不点亮、不加字样** —— 一进来就高亮会让人以为开了什么非默认筛选。
只有真的关掉隐藏、把归档拉出来看时，按钮才变成「筛选：含归档 ▾」。

**归档其实有两条路**（这条是真踩出来的，见 §23）：

```sql
-- models.Board.load()：两条都要认，只认字段的话状态归档的项目照样冒出来
WHERE ((p.archived=0 AND p.status<>'归档') OR ?)
```

- ① 行尾 ⋯ 里的「归档」→ 写 `archived` 字段
- ② 直接在状态徽章里选「归档」→ 改 `status`，**`archived` 字段仍然是 0**

用户实际用的是 ②（真实库里两条归档项目 `archived` 全是 0），所以开关只认字段时对他毫无作用。
前端统一走 `isArchived(n) = !!n.archived || n.status === "归档"`（徽章 / 压暗 / 取消归档都用它），
取消时两条各自还原：清字段 + 状态改回「已交付」。

筛选菜单里也有「归档」这个状态项 —— 隐藏开着时它会筛出空树，所以
`reload()` 传 `(ui.hideArch && ui.pStat !== "归档") ? 0 : 1`，
且切状态改成走 `reload()`（原来只 `render()`，手上没数据）。

### 22.1 验证

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层 5 个回归 | `tests/smoke_{db,finance,project_money,m4,node_spec}.py` | 全 OK |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | **74/74 PASS** |
| 打包 | `python tools/build_exe.py` | PASS（exe 5.9 MB / 目录 43.6 MB / 18.7s） |
| exe 自检 · persist | `tests/_diag/smoke_exe.py persist` | **28/28 PASS**，`frozen=true` |
| 语法 | `node --check app.js` + `py_compile` | PASS |

GUI 新增 1 条、改写 1 条：「归档开关默认关闭归档显示、按钮上不挂额外字样」
（`hideArchDefault == 1` 且 `filterText0 == "筛选 ▾"`）、
「关掉后能看到且带徽章、按钮上留痕」（`archText` 含「含归档」）。
exe 冒烟也加了一条，确认新文案**真的进了包**。

自检里如实回读了 `ui.hideArch` 初值、菜单首项文案、以及关掉隐藏那一刻的按钮文字
—— 只看「菜单里有没有这几个字」证明不了开关真的接到了 `load(include_archived)` 上。

> §21 里那张矩阵（73/73、27/27）是那一轮的记录，本轮条数已变，数字以本节为准。

---

## 23. 归档的两条路（「隐藏归档项目」点了没反应的真因）

用户报「还是无法单独控制隐藏归档项目」。开关本身没坏 ——
**是它只认 `archived` 字段，而用户的项目根本没走那条路。**

只读查了一下真实库（`<挂载的共享盘>/board.sqlite`）：

```
archived=1        : 0 条
status='归档'     : 2 条   ← 项目甲 / 项目乙
```

- 路 ①：行尾 ⋯ →「归档」，写 `archived` 字段
- 路 ②：直接在**状态徽章**里选「归档」，改 `status`，`archived` 字段仍是 0

用户用的显然是 ② —— 状态徽章就在项目行上，点两下就完事，比翻 ⋯ 菜单顺手得多。
于是 `WHERE p.archived=0` 对他一条都过滤不掉，开关怎么点都没反应。

### 23.1 修法

```sql
-- models.Board.load()
WHERE ((p.archived=0 AND p.status<>'归档') OR ?)
```

前端统一一个判断，徽章 / 压暗 / 取消归档全部走它：

```js
const isArchived = (n) => !!n.archived || n.status === "归档";
```

取消归档时两条各自还原（状态归档的改回「已交付」—— 归档一般发生在交付之后，
菜单上写明「取消归档（状态改回「已交付」）」，别悄悄改）。

### 23.2 连带的两处

- 筛选菜单里有「归档」这个状态项。隐藏开着时后端不发数据 → 筛出来是空树。
  所以 `reload()` 传 `(ui.hideArch && ui.pStat !== "归档") ? 0 : 1`
- 切状态的菜单项**必须走 `reload()`**（原来只 `render()`）：手上没数据，光重绘没用

## 24. 页签换位 + 撤提示 + 滚动条修复（2026-09-26）

三条纯界面收敛，都是用户一句话定的：

### 24.1 页签顺序：项目 / 财务 / 活跃

用户更常看财务，调到活跃前面。**只换了按钮 DOM 顺序，`data-v` 没动**；
Ctrl+1/2/3 的映射也没动（2=活跃、3=财务），设置里那行快捷键说明仍准确。

### 24.2 撤掉「筛选」旁的提示文字

`#treeTip`（「打开时只展开……拖到行中间可放进它里面」）整行删掉。
app.js 里没有任何引用（grep 验证过），删 div 即可。

### 24.3 项目页短内容也有滚动空间的根因

```css
/* 旧 */ #tree,#heat,#fin{height:100%}
```

三个页面容器被强制撑满 `main` 的内容高度，而 `#tree` 上面还有一行「筛选」栏，
加起来**永远比可视区高约 30px** —— 内容再短 `main` 也出滚动条。
修法：整条规则删掉（`main{overflow:auto}` 本来就是滚动容器，内容多高就多高）。
热力 / 财务两页同样的隐患一并消掉。

### 23.3 验证

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层 | `tests/smoke_db.py` | OK（新增：状态归档默认不出现 / 要了归档时回来且 `archived` 仍是 0） |
| GUI 渲染 · persist | `tests/_diag/smoke_fin_gui.py persist` | **77/77 PASS** |
| 打包 · 备用目录 | `tools/build_exe.py --outdir dist/_verify` | PASS |
| exe 自检 · persist | `tests/_diag/smoke_exe.py persist --exe ...` | 见下 |

GUI 新增 3 条：状态「归档」默认看不到 / 关掉隐藏看得到 / 筛「归档」状态不是空树。

> **排查手法**：GUI 冒烟全绿但用户说没用 → 直接**只读连他的真实库**看数据长什么样
> （`sqlite3.connect('file:...?mode=ro', uri=True)`）。测试用的是自己造的种子，
> 种子走的是「正确用法」，用户走的是「顺手的那条路」，两者可以完全不重合。
>
> **打包被占用**：用户开着 `dist\项目看板\项目看板.exe` 时，`move_aside()` 会撞
> `PermissionError: [WinError 32]`。用 `--outdir dist/_verify --workpath build/_verify`
> 出一份对照构建先验证，别去动他正在跑的那份。

## 25. 结款日期 + 项目设置页（2026-10-13）

### 25.1 「结款逾期」改认用户手填的结款日期

同一天早些时候给项目加过 `delivered_at`：状态切到「已交付」时自动打一个时间戳，
拿它当结款逾期的起算日。用户当轮就否掉了 —— **「什么时候交的货」和「合同说好什么时候
结款」本来就是两件事**，拿前者推后者会凭空造出一个跟合同无关的"逾期"。

现在只用 `projects.settle_date`（约定结款日，用户手填）：

- 判据仍是唯一一处 `models.settle_gap(settle_date)`；**没填就返回 `None`** ——
  行上不报、底栏不数、告警名单不列。
- 只在「已交付」之后生效（未交付的项目只报制作逾期：一行一个提醒，两个"逾期"并排会打架）。
- 入口只有「项目设置」页。`set_field` 白名单里给了 `settle_date`，但界面不给行内点改、
  也不进批量改 —— 它决定"哪天真算逾期"，填错比不填更误导。
- `delivered_at` 整条链路删掉：`models.set_status` 不再写、`load` 不再发、
  `set_field` 白名单撤掉、`db._migrate_delivered_at` 删除。**列本身留在老库里不删**
  （SQLite 删列要重建整张表，拿用户真库冒这个险不值当）—— 代码不再读它。

**代价（跟用户讲清楚）**：老库所有项目 `settle_date` 都是空的 → 之前靠交付日期算出来的
那条「结款逾期 N 天」会消失。真库副本实测：`ZZ-1840` 从「结款逾期 17 天」变成**不报**。
这是用户明确选的口径（"只用结款日期，没填就不报"），不是回归。

### 25.2 项目设置页

「编辑截止日期 / 改客户 / 改分类」原本散在项目行 `⋯` 菜单里，名称更惨 —— 只能双击标题。
现在合成一页 `#ovPrjSet`，版面照抄新建项目：
**名称 / 分类 / 客户 / 截止日期 / 结款日期 / 项目根目录** + 「保存」。

- 保存时**只下发真的改了的字段**，逐条走原有 API（`rename` / `set_group` / `set_client` /
  `set_field` / `add_link`）—— 不新开后端入口，改法仍然只有一份，冒烟也照旧钉得住。
  好处之二：打开设置页看一眼再保存，不会动 `last_activity_at`（那是活跃度的输入）。
- 目录链接没有 upsert（`add_link` 只有 INSERT），换路径 = 删旧条 + 建新条，顺序不能倒。
- `⋯` 里那三条撤掉；**环节**的「编辑截止日期」保留（环节没有设置页）。
  `pickClientFor` / `pickGroupFor` 随之成了死代码，一起删 —— 分类的增删改名仍在
  分组行的 `⋯`（`bindGroupMenu`），能力一点没丢。
- 日期框那套「原生 date + 自绘占位」按 `.dwrap` 泛化（`app.js:syncDph/bindDph`），
  四个日期框共用一套 —— 原来只钉 `#newDl`，再加一个框就得复制一遍 CSS。

### 25.3 `--smoke` 等待上限 120 → 240 秒

自检用例又长了（这轮两段真点真写库），本机实测全程 110~130 秒，又贴上了上限，
症状是**"没写出 smoke_note.txt"** —— 看着像被测功能坏了。这个上限只在 `--smoke`
这条自检路径上用（`main.py` 等 `boot_report`），正常启动不经过它。

### 25.4 验证矩阵（本轮）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 数据层（9 个） | `tests/smoke_{db,finance,project_money,m4,node_spec,activity_weight,bulk,export,payoff}.py` | 全绿 |
| GUI 渲染 | `tests/_diag/smoke_fin_gui.py fallback` | 见提交记录 |
| 打包 | `tools/build_exe.py` | 见提交记录 |
| exe 自检 | `tests/_diag/smoke_exe.py persist` | 见提交记录 |
| 真实老库迁移 | 真库**副本**上开一次库 | `settle_date` 列补上、全为 NULL（不替用户猜日子） |

> **真库副本探针**（这轮用的）：
> `shutil.copy2(<挂载的共享盘>/T_T_Data/board.sqlite, %TEMP%\probe_board2.sqlite)` →
> `Db(副本).open()` → 看 `PRAGMA table_info(projects)` 与 `Board.alerts()`。
> 老库迁移是唯一会碰用户真实数据的那段代码，冒烟里造不出"缺列的旧表"（新库直接是
> 最新 SCHEMA），所以要么单测里 `ALTER TABLE ... DROP COLUMN` 伪装成老库
> （`tests/smoke_db.py` 就是这么钉的），要么拿真库副本跑一遍 —— 两个都做了。

## 26. 金额 / 税率那行的排版（2026-10-13）

### 26.1 反馈，以及"测不出来"的原因

用户反馈：**「款项页面里金额和税率这行文字换行，不好看」**。

先在冒烟里量了一遍，结果看着是好的：`#finNet`（税后提示）只有 1 行，七位数金额也不折。
但同一行的高度是 50px，而这行的横向余量（`clientWidth - scrollWidth`）**正好是 0**。

真凶藏在 `.ovbox` 上：它是 `max-height:86%` + `overflow:auto`，内容一超框就出纵向滚动条。
人工把 `overflow-y` 设成 `scroll` 复现后 —— **滚动条白吃 15px，整行高度从 50px 涨到 67px**。
折的是「税率%」那几个小字（被拆成「税率」和「%」两行），而**税后提示本身并不折**：
只盯 `#finNet` 永远看不见。

> 为什么用户能看到、测试看不到：冒烟跑的是默认窗口（`main.py` 里 `height=760`），
> 弹窗高度够、从不出滚动条；用户把窗口拖矮就会出现。**"只在某个窗口尺寸下才犯"的
> 版式问题，靠固定尺寸的冒烟是逮不住的** —— 得能人为构造那个条件（见 26.3）。

### 26.2 改法

1. **两个弹窗加宽**：`#ovNew` / `#ovFin` 的 `width` 520 → **580px**（可用宽 578，
   即使再被滚动条吃掉 15px 也还有富余）。
2. **这行的小字不许折**：新增 `.fld.amtrow` 类（挂到新建页的「合同额」行、记一笔的
   「金额」行），配 `.fld.amtrow .lb, .fld.amtrow .hint { white-space:nowrap }`。
3. **`.ovbox` 加 `flex:none; max-width:92vw`**：`.ov` 是 flex 容器，少了 `flex:none`
   窗口变窄时**弹窗自己会被压扁**（里面的行跟着挤）；`max-width` 给极窄窗口兜底。

结果：整行回到 46px（单行高度），常规 / 七位数 / **带滚动条** 三种情况都是 1 行。

### 26.3 顺手补的"版式量尺"（防同类问题）

"元素挤到一起 / 文字折行"原本是冒烟的盲区（`preview_gen.py` 的注释里早写过这句）。
这轮给 `diag.js` 加了两个量尺，**并留成常驻断言**：

- `textLines(el)` —— 元素**实际画了几行**（inline 的 `getClientRects().length`）。
- `rowMaxLines(rowEl)` —— **一整行里所有文字元素**的最大行数。
  ⚠ 必须用这个：这次真凶是旁边的小字，只量目标元素会被骗（第一版就漏了）。

断言落在 `payoff` 段（那里刚好同时填了财务页和新建页的金额/税率），并在最严苛的一档
**人工把弹窗设成出滚动条**再量一遍 —— 老写法在这一档下必红。

### 26.4 验证矩阵（本轮）

| 项 | 结果 |
| --- | --- |
| GUI 冒烟 | **OK / 0 FAIL**（新增 4 条版式断言） |
| 排版实测 | 常规 1 行 / 七位数 1 行 / **带滚动条 1 行**；行高 46px（修前带滚动条 67px） |
| 弹窗可用宽 | 518 → **578** |
| 多选（`multi`）段 | 偶发撞预算（那次整机 3m31s，平时 1m46s），预算 60 → **90 秒** |

> 只改了前端（`index.html` / `style.css` / `diag.js`）→ **不必重建 exe**，
> 热替换 `dist\天在看\_internal\web\` 后重启即可（见 `AGENTS.md` 铁律 2）。


---

## 27. 外包商 / 制作人名录 + 打通外包支出（2026-10-16）

用户原话：「添加一个外包商 数据，像客户这种，可有个人和团队。并将数据关联到项目页的制作人。」
紧接着追加：「需要打通外包商和外包支出」。

### 27.1 拍板的口径（用户逐条选的，别随手改）

| 问题 | 结论 |
| --- | --- |
| 制作人跟外包商怎么关联 | **强关联（存 id）**，不做"候选池" |
| 覆盖到哪一层 | **项目层 + 环节层**（环节能多选批量改） |
| 外包商记哪些字段 | **精简四栏**：名称 / 类型 / 联系方式 / 备注（不抄客户那套 7 字段、不加计价信息） |
| 类型分几档 | **个人 / 团队**（不加"内部"档） |
| 新建项目页要不要也加「制作人」 | **不加**（保持 8 字段，制作人从「项目设置」页改） |
| 外包支出要不要必填外包商 | **必填** |
| 外包商汇总算哪些钱 | **已付 + 应付欠款**（分列 + 合计） |

### 27.2 数据层

- 新表 `vendors`（见 4.5）；`projects.artist_id` / `nodes.artist_id` / `finance.vendor_id` 三处外键
- `_migrate_vendors()` 一次性搬运老库的裸文本制作人 → 条目 + 回填 id（幂等、有标记位、排在 `_migrate_columns()` 之后）
- 裸文本 `projects.artist` / `nodes.artist` **保留但不读** —— SQLite 删列要重建整张表，不拿真库冒险

### 27.3 口径层（`models.py`）

- `load()` 两处 JOIN：`LEFT JOIN vendors v ON v.id=<table>.artist_id`，输出 `v.name AS vendor`
- **对外仍然发一个叫 `artist` 的键（值是名字）** + 新增 `artist_id`。
  这是本轮最省事的一个决定：前端筛选、搜索、行上显示读的全是 `n.artist`，
  名字语义不变 → **一处都不用改**。要改的只有判"未分配"的地方（`!n.artist` → `!n.artist_id`）
- `set_field` 白名单把 `artist` 换成 `artist_id`（0 / "" / None 一律落 NULL）
- `bulk_update` 的 `artist` 参数**语义变成 vendors.id**，参数名没改是为了不动桥接口

### 27.4 财务层（`finance.py`）

- `vendor_options()` 顺带算三个挂靠数（`np / nn / nf`）—— 删除保护就看这三个
- `vendor_save` / `vendor_find_or_create` / `vendor_delete`（盯三处牵连、理由具体到数量）
- `_vendor_id(raw, kind)`：只对 `outsource` 有意义，**必填** + 存在性校验
- `_by_vendor()` 外包商汇总：只吃 `outsource` 且非「作废」；`vendor_id=0` 落「未指定外包商」**不丢**
- 导出：`SECTIONS` 加一项（CSV 明细 11 → **12 列**、Excel 四张 → **五张表**）

### 27.5 前端

- 财务页头「客户 / **外包商**」并排两颗按钮；客户汇总下面加对称的「外包商汇总」节
- 「记一笔」在「挂到」后加**外包商行**（`#finVendorRow`），只跟「外包支出」绑在一起
- 项目设置页加「制作人」行（`input + datalist#vendorOpts`，跟客户框一个待遇）
- `ensureVendor(name)`：名录里有就用现成的，没有就现建，**就地补进 `D.vendors` + 重填候选，
  不整个 `reload()`**（那会把正在编辑的弹窗状态一起翻新）
- `syncFinKindRows(kind)` 收口：原来"打开弹窗"和"切换类型"两处各写一遍显隐逻辑，
  加外包商行必然漏一处 —— 而那种漏法的表现是"刚打开正常、切一下类型就少一行"，最难查

### 27.6 踩到的坑（写下来免得下次再踩）

1. **`.op` 不是行**：自检里 `pickRow()` 返回的是那颗 `⋯`（`[data-vdr]` 元素），
   想取整行第 N 个格子得先 `parentElement`。写错时症状只是"某条断言红着"，
   看着像功能坏了 —— 而真正的功能是好的（`menuOf()` 用同一个 pickRow 一路都通）。
2. **不能拿 id 去比菜单文案**：制作人候选现在画的是**名字**（`vendorName(id)`），
   而筛选状态里存的是 **id**。子环节筛选那段自检原来拿 `first`（id）直接比 label，
   改完之后一个都点不着，症状是"反选结果不对"。
3. **批量候选不再来自"库里已出现过的值"**：`popupField` 对 `artist` 走**名录**（全局），
   所以自检里"先把目标值 seed 到另一条环节上"那招失效了，得改成现造条目；
   造完**必须收拾干净**，否则后面"名录刚好这三条"的断言会红。
4. **`x or -1` 会把 0 吃掉**：断言"已付 0"时用 `float(v or -1)`，0 是**正确值**却被兜成 -1，
   于是这条永远红着、看着像汇总算错了。用显式取值（`num()`）+ `is None` 判空。
5. 前端菜单 `who.forEach` 里直接把 id 拼进 label（`"✓ " + w`）→ 界面上会变成一串数字。
   这种"改完能跑、只是难看"的坑，只有真跑一遍界面才看得见。

### 27.7 验证矩阵（本轮）

| 项 | 结果 |
| --- | --- |
| 迁移探针 `tests/_diag/_vendor_probe.py` | **17 条全绿**（含"伪装老库 → 重开"这条真实路径） |
| 数据层回归 9 个 | 全绿（`db` / `finance` / `project_money` / `m4` / `node_spec` / `activity_weight` / `bulk` / `export` / `payoff`） |
| GUI 冒烟（`smoke_fin_gui.py fallback`） | **OK / 0 FAIL**（新增 `vendor` 段 + `prjset` 段制作人断言） |
| exe 冒烟（`smoke_exe.py persist`） | **OK / 0 FAIL**（该轮已出包交付；§28 复测同一份产物仍 OK，见 28.8） |
| 分段耗时 | 新增的 `vendor` 段 **11 秒**（预算 25 秒，余量充足） |

---

## 28. 结款改口径：核销待收，合同额一条不动（2026-10-16）

### 28.1 为什么改

起因是问「结款时把多余的合同额记录删掉，是否改成不删记录的版本」。追下去发现
真正的问题不在"删不删"，而在**结款的语义本身**。用户给了两个真实场景：

1. 合同款登记完后，实际付款会有税或手续费之类的小额差
2. 有些项目确定无法收齐款项，但项目还是要做一个结束处理

两个都是「**合同没变，但钱收不齐了**」。而旧做法（把合同额改成实收、多余的合同额
记录合并删除）能把待收归零，却**把损失从账面上抹掉了**：

```
结款前：合同额 150,000  已收 120,000  待收 30,000  收款率 80%
结款后：合同额 120,000  已收 120,000  待收   0     收款率 100%   ← 那 3 万去哪了？
```

以后翻客户表会以为这个客户每笔都付清了。**哪笔项目亏了、亏多少，账上查不到。**

### 28.2 新口径

引入第五种款项 `writeoff`（核销），待收改成 **`合同额 − 已收 − 已核销`**。

```
合同额 150,000  ← 原合同 10 万 + 增补 5 万，两条都原封不动留着
已收   120,000
核销    30,000  ← 新增：一条带日期、带备注的记录
────────────────
待收         0  ← 归零，项目可以收尾
收款率     80%  ← 诚实
```

**「合同额变了」和「钱收不齐」就此分成两件事**：前者去改 / 加合同额记录（补充协议
确认就是 12 万），后者走结款核销。旧口径把两件事塞进一个按钮里，这才是"删记录"的根源。

### 28.3 `payoff_apply` 的规则

结款 = 让「核销」这一项等于 `max(0, 合同额 − 已收)`，由**它自己那条**记录
（备注正好是 `结款核销`）承载：

- 该有 > 0 且没有那条 → **新增**一条；已有 → **改它的金额**（id 不变，关联不断）
- 该有 = 0（后来收齐了）→ 把它**作废**（账自己回来），不是删掉
- 已经平了（差额 < 0.01）→ **什么都不做**，直接返回 `changed=False` + 一句
  「已经结清了，没有待收要核销」。连点两次不会多出记录（幂等）
- 一笔「已收」都没有 → **拒绝**：那等于把整份合同一次性核销掉，不是"结款"该默默干的事
- **用户手记的核销不碰**（备注原样保留），但算进"已经核销了多少"；
  结款自己承担的是差额。所以「先手记一条银行手续费 100，再点结款」→ 只补 29900

用备注 `结款核销` 做标识（而不是加一列 `source`）是刻意的：**它对"用户把备注改掉了"
也是鲁棒的** —— 那条就被当成用户手记的，`自己该承担的 = 该有总额 − 手记部分` 会自然算出 0，
结款不会再去动它，账一样对得上。

### 28.4 `settled`（已结清）判据收敛

`_by_project` 里原来有一条没写理由的隐式条件 `terminal`（"没有任何 payment 记录还挂在
「待收」"，初始提交就在）。它跟核销**打架**：核销把待收冲成 0 之后，只要项目里还留着一条
状态是「待收」的到账记录（很常见：客户说要分三期付，后来没付齐），项目就永远标不出
「已结清」—— 用户看到待收 0 却没有结清标记，只会以为结款失败。

**改成只认「待收 ≤ 0 且有合同额」**。副作用是良性的：那笔钱后来真到了，记成「已收」→
待收变负 → 再点结款会把核销自动撤掉，账自己走回来。

### 28.5 前端

- 财务页「记一笔」加第五颗类型按钮**「核销」**（垫在最后，它是收尾动作不是日常记账）；
  选它时**外包商 / 发票号 / 结款**三条行都收起（`syncFinKindRows` 一份逻辑管到底）
- 结款按钮从「结款并修改项目总款」改成**「结款（核销待收）」**，提示语同步
- 确认弹层**把账摊开写**：`合同额 − 已收 = 待收`，脚注说明会核销多少、
  **合同额不动**、收款率是多少、合同额记录一条都不会被改
- 项目行的标记分两种：**「已结清」**（收齐了）/ **「已核销结清」**（有核销的收尾）；
  还没结清但核销过 → 「已核销 ¥N」
- **卡片刻意没动**（还是 6 张、顺序没变）：已核销是项目级的收尾数字，
  行上有标记、展开能看到记录、导出里有列，不值得为它把卡片挤乱

### 28.6 导出

- 项目汇总 / 客户汇总各多一列**「已核销」**（插在「待收」后面，不动已有的列序）
- 款项明细的「类型」列自然出「核销」（走 `KIND_CN`），CSV 仍是 12 列平表

### 28.7 一个必须知道的遗留

**以前结过款的项目，历史找不回来了** —— 合同额已经被改小、增补记录已经被删。
往后走的项目才是完整的。这正是当初"不该删"的代价。

### 28.8 验证矩阵（本轮）

| 项 | 结果 |
| --- | --- |
| 数据层回归 9 个 | 全绿（含重写的 `smoke_payoff.py`：54 条断言） |
| GUI 冒烟（`smoke_fin_gui.py fallback`） | OK（0 FAIL；payoff 段 8.6s，断言含 `writeoff` 类型按钮、`writeoffAfter==70000`、`contractAfter==100000`、`woRecs==[[70000,"已核销","结款核销"]]`、`recCount==4`、幂等） |
| exe 冒烟（`smoke_exe.py persist`） | OK（113 PASS / 0 FAIL；打包态同一份 `diag.js`，结款落库后合同额仍 100000、多出一条核销 70000、待收归零） |

---

## 29. 四条界面反馈（2026-10-16 晚）

来源：`<NAS 名>\kanban_issue.txt`（用户手写，四条）。四条都是**界面层**的调整，
后端唯一动到的是 `vendor_options()` 的 docstring（口径没变）。

### 29.1 外包商「挂靠」列只显示挂靠项目数

用户原话：「外包商不要显示统计挂靠环节数目，仅显示挂靠项目数」。

- 改的是 `renderVendors()` 用的 `hangLabel(v)`：原来拼
  `N 项目 · M 环节 · K 笔款`，现在**只出** `N 项目`（没项目挂就 `—`）。
- ⚠ **显示精简了，删除保护没松**：`vendor_delete` 仍然盯项目 / 环节 / 款项三处，
  有环节或款项挂着照样删不掉、照样把数量说出来。所以这一列出现 `—`
  只代表"没有项目挂他"，**不代表能删** —— 真要知道能不能删，点 `⋯` → 删除，后端会答。
  这条差别写进了 `hangLabel()` 的注释，免得后人（或下一个 AI）看到 `—` 就把
  `vendor_delete` 的三处判断也一起"精简"掉。
- `finance.py` 的 `vendor_options()` **三个数仍照发**（`projects` / `nodes` / `records`）——
  前端只是不显示 `nodes` / `records`，删除分支还要靠它们说明原因。
- 自检：`diag.js` 除原有的 `hangCell`（改取**挂项目那个**「冒烟制作人」）外，
  新增 `hangCells`（整列）与 `hangCellNone`（冒烟工作室：0 项目但有 2 笔款 → 该是 `—`）。
  断言从"含「笔款」字样"翻成"以「项目」结尾 + 整列不含「环节 / 笔款」+ 没项目的写 `—`"。

### 29.2 精简四处提示文字

用户逐条指定，四处全是"文案减法"，没有逻辑变化：

| 位置 | 改前 | 改后 |
|---|---|---|
| 记一笔弹窗 `p.sub` | `合同额 / 到账 / 外包支出 / 核销分开记，汇总口径：…` | `汇总口径：…`（砍掉前半句） |
| 到账页「结款」行的 hint | `把收不齐的待收核销掉，合同额不动` | `项目结款，并核销掉差额。` |
| 外包支出页「外包商」行的 hint | `付给谁；敲新名字会自动建条目` | **整句删** |
| 「挂到」栏的 hint | `可选，比如分批款对应某个环节` | **整句删** |

### 29.3 环节截止日期改成日历控件

用户原话：「项目页面，环节的编辑截止日期，添加一个日历选择日期的控件」。

- 原来两处都是 `prompt()` 手打 `YYYY-MM-DD`：单条 `editFieldOne()`（⋯ → 编辑截止日期）、
  批量 `popupField()` 的「手输一个…」。
- 现在统一走新的 **`#ovDate`** 弹窗（`openDateDialog` / `saveDateDialog` / `bindDateDialog`）：
  - 单条：`editFieldOne()` 遇到 `meta.date` 直接开弹窗（写 `set_field`）
  - 批量：`popupField()` 把「手输一个…」换成**「选一个日期…」**（写 `applyBulkField` → `bulk_update`）
  - 弹窗里有**「清空日期」**（原来 prompt 的"留空清除"语义保留）
- **为什么用原生 `<input type="date">`**：它自带日历弹窗，而且**根本输不进非法格式**
  （赋值 `2026/12/31`、`下周三` 会被它自己丢成空串）—— 比 prompt + 事后 `isDate()` 校验可靠。
  这跟新建页 / 设置页那四个日期框是**同一套 `.dwrap` + `.dph`**（自绘 `YYYY/MM/DD` 占位），
  没另写一份。
- ⚠ **自绘占位有两态**，自检必须分开读：CSS 是 `.dwrap input:not(.hav):not(:focus) ~ .dph`，
  弹窗打开时输入框是**聚焦**的 → 占位让位给原生 mask（用户得看得见自己敲的数字）；
  失焦后才露出 `YYYY/MM/DD`。第一版自检只读了一态，红了一条，改法是在读之前 `blur()`。
- 批量那条"候选列表"路径**没动**（`fieldValues("deadline")` 仍列最近 10 个日期），
  所以 `diag.js` 里 `runBulkField("deadline", …)` 那段老自检不用改。

### 29.4 财务页四组数据做成标签页

用户原话：「项目款项，应收账龄，客户汇总，外包商汇总，这四组数据可以做成可切换标签页以节省空间，
默认显示项目款项并将应收账龄放最后」。

- 结构：四组从四个平级的 `.fsec` 收进**一个** `.fsec`，里面一个 `#finTabs` 标签条 +
  四个 pane（`#fsPrj` / `#fsCli` / `#fsVdr` / `#fsAge`）。
- 顺序按用户定的：**项目款项（默认）/ 客户汇总 / 外包商汇总 / 应收账龄**。
- `finTab` 是**模块变量**，`loadFinance()` / `renderFinance()` 都不碰它 ——
  所以切年份 / 切客户后用户还停在原来那一页，不会被弹回「项目款项」。
- 切 tab **只改 `display`、不重渲染**（表早就画好了，重画纯属浪费）。
- ⚠ **表格 DOM 一直都在**（只是藏起来），所以"数行数"看不出 tab 有没有工作 ——
  自检必须**真点一下**再读 `display`。`diag.js` 的 fin 段加了 `finTabBtns` / `finTabAct` /
  `paneVis`，以及"点「应收账龄」→ 只露它 → 再点回「项目款项」"两步。
- ⚠ 键名必须带 `finTab` 前缀：`rep.fin.tabAct` 已经被**页面页签**（项目 / 财务 / 活跃）占了，
  同名会把那个覆盖掉 —— 第一次写就踩了，`smoke_fin_gui` 那条"点页签切页后财务页签高亮"会红。
- 样式 `.ftabs` 用**下划线**而不是 `.seg` 那种填充块：它切的是"看哪张表"（页签性质），
  不是"选哪个值"（开关性质），两种语义不该长得一样。

### 29.5 验证矩阵（本轮）

| 项 | 结果 |
| --- | --- |
| 数据层回归 9 个 | 全绿（后端只动 docstring，没碰逻辑） |
| GUI 冒烟（`smoke_fin_gui.py fallback`） | OK（0 FAIL / 407 PASS；新增 `date` 段 5.6s，预算 20s） |
| exe 冒烟（`smoke_exe.py persist`） | OK（116 PASS / 0 FAIL；打包态同一份 `diag.js`，标签页顺序 / 默认 / 切换与日期弹窗保存清空都对） |
| 前端改动 | 只改前端 + 一处 docstring；按铁律仍然重建 exe（docstring 也在包里） |

## 30. 子环节序列模板：`前缀####(起始-结束)`（2026-10-16 深夜）

用户原话：

> 新增子环节里添加一种填写连续序列的方式 如填写"WD####(12-20)"可创建 WD0012 WD0013 直到 WD0020 的子环节

### 30.1 为什么加

老写法要**两边都把前导零打满**：`WD0012-WD0020`。位数一多就容易数错（`WD001-020`
出来是三位混四位），而这个位数**本来就是"我想要几位编号"这个意图本身**。模板把它
显式写出来 —— 几个 `#` 就是几位 —— 范围里只写数字（`12-20`），不用数零。

⚠ 注意：老写法**并没有坏**，`WD0012-WD0020` 照样能用（两边都是"前缀 + 数字"，
右前缀是左前缀的后缀）。这轮是**加一种更省心的写法**，不是替换。

### 30.2 语法与边界

```
前缀 + N 个 # + ( 起始 - 结束 )
```

| 写法 | 结果 | 规则 |
| --- | --- | --- |
| `WD####(12-20)` | `WD0012` … `WD0020` | `#` 的个数 = 补零宽度 |
| `shot##(8-10)` | `shot08` … `shot10` | 前缀任意长；也可以为空（`#(1-3)` → `1 2 3`） |
| `WD####（12-20）` | 同上 | 全角括号也认 |
| `WD###（1~3）` | `WD001` `WD002` `WD003` | 波浪号（半/全角）当范围符 |
| `WD#### ( 12 - 20 )` | 同上 | **模板内部的空白不算分隔符** |
| `WD####(20-12)` | `WD0020` … `WD0012` | 倒序也认 |
| `WD####(12-12)` | `WD0012` | 单值 → 就建那一个 |
| `WD##(8-105)` | `WD008` … `WD105` | **宽度只加宽、不截短** |

三条刻意的决定：

1. **宽度只加宽不截短**。`WD##(8-105)` 按 3 位出（`WD008…WD105`），不是
   `WD98 WD99 WD100`。写少了 `#` 得到"长短不齐"比"整体宽一位"糟得多 ——
   而预览里看得见实际结果，用户一眼就能发现。
2. **模板内部的空白不算分隔符**。`WD#### ( 12 - 20 )` 在用户眼里是一句话，
   而 `SEP` 会把空格当分隔符撕成四段。所以进 `names()` 之前先跑一遍
   `_tidy()`，把模板里的空白抹平（外面正常的空格分隔不受影响）。
3. **宽度以 `max(#个数, 两端数字位数)` 为准**，所以 `WD####(012-020)` 同样是 4 位。

### 30.3 写歪了必须报错，不许静默当字面量

老逻辑的口径是"认不出来就当字面名"（保护 `SANTI-OneDay` / `shot-001` 这些）。
但对**同时含 `#` 和括号**的写法，这条口径会害人：`WD####(12)` 会**悄悄建出一个
叫 `WD####(12)` 的环节**，用户以为自己填对了。

所以加了一条窄规则：**token 里同时有 `#` 和括号 → 必须写成完整模板，否则报错**，
错误消息里带上他自己写的那串 + 正确写法：

```
这个序列写法看不懂：WD####(12)
要写成 前缀####(起始-结束)，例：WD####(12-20) → WD0012…WD0020
```

这符合本项目一贯的口径「**失败原因必须回传并让用户看见**」（见 §23 那段
`create_file_dialog` 的教训）。不带括号的 `Phase#01` / `#tag` / `WD####` 仍然
原样保留成字面量 —— 规则只吃"两个特征同时出现"的写法。

### 30.4 实现落点

| 位置 | 改动 |
| --- | --- |
| `app/node_spec.py` | 新增 `HASHSEQ`（模板正则）、`HASHSEQ_TIDY`（抹空白）、`_tidy()`、`_looks_like_seq()`、`_seq()`；`expand()` 里插在破折号统一之后、老连续号逻辑之前；`names()` 先 `_tidy()` |
| `app/web/index.html` | `#ovNodes` 的 `.hint` 增一行：`WD####(12-20)` → WD0012 … WD0020（几个 `#` 就是几位） |
| `app/web/app.js` | 「新增子环节」入口的注释同步（一行注释） |
| `app/web/diag.js` | `nodes` 段新增 4 项自检（见 30.6） |
| `tests/smoke_node_spec.py` | 新增第 4 节「序列模板」（含真落库） |

**没动的地方，以及为什么**：

- `api.add_nodes` / `parse_nodes` **一个字没改** —— 它们都只调 `node_spec.names()`，
  预览与落库**共用同一条解析路**。这也意味着"预览对了但建出来不一样"是不可能的。
- 新建项目页那个 `#nodesSpec` 与行内「新增子环节」共用**同一个弹窗**，所以两个入口
  一起就有了新写法，不需要分别改。

### 30.5 顺手修掉的一个冒烟陷阱：`:focus` 取决于窗口是否前台

这轮改完 diag 之后，GUI 冒烟开始**间歇性**报十几条红（`multi` 段 `pickAfter` 从 9
变成 1、`bulkDel`/`date` 双双"找不到行"）。排查过程与结论：

1. **先看是不是我改出来的**：分段耗时逐秒对比，除了 `nodes` 段 +1.2s（我加的等待）
   之外完全一致 → 不是机器变慢。
2. **在失败路径上加现场快照**（`treeSnap()`），拿到决定性证据：
   `rows: ["?:?","?:?","project:3","node:3"]` —— 树里只剩 1 个项目。而数据里
   3 个项目、行数类数字（`pickAfter` / `collapseSel` / `allExpect`）与上一轮完全一致。
3. **把 `nodes` 段我引入的额外等待从 1.2s 压到 0.5s**，随后两次连跑都只剩 **1 条
   确定性的红** —— 与刚才那些闪烁的红不是同一件事。
4. 那条红的根因：**「聚焦时自绘占位让位」的渲染结果取决于窗口是不是系统前台窗口**。
   冒烟无人值守跑，窗口在后台，`:focus` 不匹配 → 占位照旧露着。诊断字段直接给出铁证：

   ```
   docHasFocus: false        ← 窗口在后台
   activeIsInput: true       ← 但 focus() 确实把焦点给了输入框
   focusRule: ".dwrap input[type=\"date\"]:not(.hav):not(:focus) ~ .dph"   ← 规则也在
   phVisibleWhileFocused: true   ← 只有渲染态读不准
   ```

**修法**（不玩"读不到就当过"）：判据拆三层，**只有第一层与窗口前台无关** ——
① `activeElement` 是不是输入框（纯 DOM 状态）② 样式表里有没有那条 `:not(:focus) ~ .dph`
规则（走 CSSOM，证明机制在）③ 失焦后露出（无条件成立）；**窗口真在前台时自动升级为
严格模式**，额外要求渲染态必须让位。所以跑在有人值守的机器上时，这条断言是全强度的。

**留下的两个诊断设施**（都在 `tests/_diag/`，不影响交付）：

- `diag.js` 的 `treeSnap()`：行找不到时把"树里有哪些行 / 折叠数 / 项目清单 /
  每组项目的状态与环节数 / 筛选栏文案"一起记下来。下次再遇上"行不见了"，
  一眼就能分出是**折叠**还是**筛选**还是**数据真没了**，不用再猜。
- `_rm.py <路径>...`：只走 `tools/nasfs.py` 的 Win32 直删（网络盘没回收站，
  `shutil` 那套会 FAIL），目录递归、不存在就跳过。留着是因为冒烟前后清残留
  是常规动作，每次现写一个临时脚本太啰嗦。

### 30.6 验证矩阵（本轮）

| 项 | 结果 |
| --- | --- |
| 数据层回归 9 个 | 全绿（`smoke_node_spec.py` **72 项**，从 45 项涨上来；含真落库 9 个 `WD0012…WD0020`） |
| GUI 冒烟（`smoke_fin_gui.py fallback`） | **OK（0 FAIL / 411 PASS）**；`nodes` 段 3.9s |
| exe 冒烟（`smoke_exe.py persist`） | **OK（117 PASS / 0 FAIL）**。新增的那条「序列模板随包发出且真能解析」PASS —— 这就是"包里的 `node_spec.py` 是新的"的证据（改的是后端，光看界面文案会被 hot-swap 过的 `web/` 骗过去）。⚠ **第一遍其实跑出 3 条红**（`prjset` 段 `dlgClosed=false` + 两段"收拾干净"读不到），清掉上一次冒烟留下的 `dist\天在看\data` / `tests` 后重跑就全绿 —— **exe 冒烟和 GUI 冒烟一样有偶发抖动：红的时候先清残留重跑一遍，再判断是不是真坏** |
| 前端 4 文件 md5 | 与 `dist\...\_internal\web\` 一致 |

`nodes` 段的 4 项新自检：`seqPvOk`/`seqPvCount`/`seqPvFirst`/`seqPvLast`（走 `parse_nodes`
纯解析，能精确数出 9 个）、`seqBadOk`（写歪的 `ok=false`）、`seqPrevOk`（**真·界面刷新**后
预览框里出现 `将创建` + `WD0012` + `WD0020`）、`seqPrevRed`（写歪的预览框出 `✕`）。

⚠ 这一段刻意只用 **2 次**界面刷新、每次 250ms：原来我写成 3 次 × 400ms，等于把整段结束
时间推后 1.2 秒。冒烟是**串行**跑在同一个 app 实例上的，多推这一点就够让后面的段踩上
偶发的时序抖动。**"能少等就少等"是冒烟里的硬规矩**（要证明的事已经由这两次拿到了）。


