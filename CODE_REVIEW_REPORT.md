# 代码审查报告（天在看 / Task-Overview）

> 最新一轮审查：2026-10-16 · 审查对象：9 天新增代码（b704e66 → b6521cf，14 个 commit，约 1 万行增量）
> 审查人视角：代码审查专家（正确性 > 安全性 > 可维护性 > 性能，不以风格偏好判分）
> 历史：2026-10-06 完成首轮全量审查（3 处 nit 已修复，见文末）

## 一、结论（TL;DR）

本轮审查覆盖 9 天里新增的结款核销、外包商名录、序列模板、批量删除、结款日期、
版式修复等 14 个 commit。**核心新功能实现扎实**：核销（writeoff）口径清晰、事务处理
严谨、幂等设计到位；外包商强关联的迁移不丢数据。发现的问题全部是 nit 级死代码，
无阻塞、无建议级问题：

- 🔴 **阻塞级问题：0 个**
- 🟡 **建议级问题：0 个**
- 💭 **nit 级问题：3 处**（已全部修复，其中 1 处含不可达代码 + 未定义变量的隐患）

## 二、发现与修复（本轮）

### 🔴→💭 nit-1：`models.bulk_delete` 末尾重复 return（已修复）

- **落点**：`app/models.py:749-750`
- **问题**：`bulk_delete` 方法末尾有**两行连续 return**。第二行是不可达死代码，
  且引用了本方法里不存在的变量 `keep`（那是 `reset_priority` 的参数）——若第一行
  return 被改动，第二行一旦执行会抛 `NameError`。这是复制粘贴遗留。
- **处理**：删除第二行死代码，保留正确的 `{"ok": True, "done", "skipped", "msg"}`。

### 💭 nit-2：前端 `catName` / `groupOf` 一对死函数（已删除）

- **落点**：`app/web/app.js:1380-1384`
- **证据**：`catName` 全工程零调用；`groupOf` 只被 `catName` 调用。二者是「通过
  key 反查分组名」的历史遗留，分组名显示早已改为遍历 `D.groups` 直接取 `g.name`
  （见 `app.js:938/946` 等）。
- **处理**：删除两个函数，精简对应注释。

### 💭 nit-3：`index.html` 三个死 id（已删除）

- **落点**：`app/web/index.html` 的 `#psDlPh` / `#psSettlePh` / `#dateInputPh`
- **证据**：日期占位符 `.dph` 靠 CSS 相邻选择器（`input ~ .dph`）和 `.dph` 类控制，
  JS 通过类/容器选择器操作，这三个 id 无任何引用。
- **处理**：删除三个 id，`.dph` 类和结构不动。

> 修复后 `_audit_dead.py` 重跑：死方法 0 / 未使用 import 0 / JS·HTML·CSS 死代码 0。
> 9 个数据层回归全绿（db / finance / project_money / m4 / node_spec /
> activity_weight / bulk / export / payoff）。

## 三、值得肯定的实现（本轮抽查）

- **核销（writeoff）口径严谨**（`finance.py`）：`待收 = 合同额 − 已收 − 已核销`，
  结款只动它自己那条 `结款核销` 记录，用户手记的核销不碰、合同额一条不改——
  收款率因此保持诚实（15 万收 12 万 = 80%，不是抹掉损失后的 100%）。幂等
  （差额 < 0.01 不写库）、一笔已收都没有时拒绝，都考虑到了。
- **外包商迁移不丢数据**（`db._migrate_vendors`）：老库裸文本 `artist` 按 casefold
  去重后一次性建成 vendors 条目并回填 `artist_id`，幂等、快照回滚也能重跑；
  文本列故意不删（SQLite 删列要重建表）。
- **`settle_gap` 结款逾期判据单一实现**，前端 `settleDays` 逐字对齐，冒烟钉着
  底栏/告警/行上红字三处口径一致——延续了项目「口径唯一出口」的一贯做法。
- **`bulk_delete` 显式 `BEGIN` + 级联去重**：多选常常祖先与后代同时在选，方法先把
  整棵子树展开去重再一条 DELETE，`done` 报真实消失条数，界面对得上。
- **注释质量**：几乎每条"为什么"都附踩坑日期和复现结论（如 `2026-10-10` autocommit
  下漏 BEGIN 留半拉子、`2026-10-13` 交付日期反推逾期不合理），可维护性极强。

## 四、历史审查记录

### 2026-10-06（首轮全量审查）

通读 45 个 Python 文件 + 6300 行前端（11,000+ 行），结论：阻塞 0 / 建议 0 / nit 3。
修复：① 删除死方法 `api.finance_export`（导出已重构为 `_plan` + `_save` 两步）；
② 删除 `tools/_desens.py` 未使用的 `import re`；③ 删除 `tests/_diag/_xcheck_ignored.py`
未使用的 `import sys`。

### 2026-10-07（敏感信息专项）

发现 `GITHUB_PUSH_STEPS.md` 含真实 SSH 公钥且已推上公开仓库，处置：作废旧 key +
重生新 key + 该手册移出 git 跟踪并从发布清单排除。详见工作日志 `2026-10-07.md`。

## 五、后续建议（非阻塞，长期有效）

1. **`paths.default_db_path` 与 `db.default_db_path` 重名**：刻意设计（延迟导入转发，
   避免循环引用），注释已写明，**不要合并**。
2. **`collapse_all` / `set_collapsed_all`**：顶栏按钮已撤，但方法仍被 `diag.js` 自检
   和 `smoke_m4.py` 程序化调用——是活的，**不要当死代码删**。
3. **`delivered_at` 列故意保留**：SQLite 删列要重建整表，代码已不读写，只剩注释。
