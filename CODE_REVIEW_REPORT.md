# 代码审查报告（天在看 / Task-Overview）

> 本轮审查日期：2026-10-06 · 审查对象：全部源码（app / tools / tests / 前端）
> 审查人视角：代码审查专家（正确性 > 安全性 > 可维护性 > 性能，不以风格偏好判分）

## 一、结论（TL;DR）

**这是一个已经高度打磨、维护纪律极好的工程。** 本轮从头到尾通读了
45 个 Python 文件 + 约 6300 行前端（app.js / diag.js / boot.html / index.html / style.css），
共 11,000+ 行代码。对照 `CODE_REVIEW.md` 既定的分层清单逐条核验后：

- 🔴 **阻塞级问题：0 个**
- 🟡 **建议级问题：0 个**（发现的全是 💭 nit 级死代码 / 冗余 import）
- 💭 **nit 级问题：3 处**（已全部修复）

这组数字本身说明问题：项目在「先实测再下结论」「单一来源」「改动可验证」
三条铁律上执行得极扎实，绝大多数注释写的是"为什么"和"坑"而不是"是什么"。

## 二、审查范围与方法

| 层 | 文件 | 规模 | 关注点 |
| --- | --- | --- | --- |
| 数据层 | `app/db.py` | 792 行 | 连接 / 锁 / 迁移 / 便捷方法 |
| 业务层 | `models.py` `finance.py` `node_spec.py` | 1915 行 | 口径一致性 / 事务 / 空值防御 |
| API 层 | `api.py` | 637 行 | 异常回传 / 参数校验 / 桥序列化 |
| 路径/入口 | `paths.py` `version.py` `opener.py` `_boot.py` | 690 行 | 判据单一来源 / sys.frozen |
| 主层 | `main.py` `boot.py` `remind.py` | 975 行 | 生命周期 / ctypes 声明 / 通知身份 |
| 前端 | `app/web/*` | 6359 行 | DOM 定位 / 死代码 / 口径对齐 |
| 工具层 | `tools/*.py` | 1725 行 | 构建 / 打包 / 图标 / 同步 |
| 测试层 | `tests/*.py` | 1753 行 | 断言有效性 |

方法：静态审计（`_audit_dead.py` AST + JS 引用计数）→ 逐层通读 + 定位落点 →
针对性 grep 交叉验证调用图 → 8 个数据层回归 + GUI/exe 冒烟基线核验。

## 三、发现与修复

### 💭 nit-1：`api.finance_export` 是死方法（已删除）

- **落点**：`app/api.py:373`
- **证据**：前端 `app.js` 无任何 `finance_export` 调用——导出早已重构为两步
  `finance_export_plan`（只算文件名/笔数做预览，不弹窗）+ `finance_export_save`
  （弹系统另存为落地）。`_audit_dead.py` 报它「引用数 0」，交叉 grep 确认仅
  `DESIGN.md` 文档里残留一句提及。
- **处理**：删除方法；`DESIGN.md` 的方法清单同步改为 `_plan / _save` 两步描述。

### 💭 nit-2：`tools/_desens.py` 未使用的 `import re`（已删除）

- **证据**：全文件无 `re.` 调用（脱敏脚本走的是字节层 `bytes.replace`）。

### 💭 nit-3：`tests/_diag/_xcheck_ignored.py` 未使用的 `import sys`（已删除）

- **证据**：全文件无 `sys.` 调用（用 `subprocess` 完成全部工作）。

> 修复后 `_audit_dead.py` 重跑：死方法 0 条 / 未使用 import 0 条 / JS·HTML·CSS 死代码 0 条。

## 四、值得肯定的实现（抽查亮点）

- **ctypes 句柄全部显式 `argtypes`/`restype` 且按指针宽度声明**（`db.py` 互斥量、
  `main.py` SetWindowPos/FindWindowW）—— 64 位 HWND 截断这个坑被系统性防住。
- **停滞 / 活跃度 / 到期文案三处口径各只有一个实现出口**，前端只能逐字对齐，
  且冒烟里钉着三处一致（`models.stale_gap` / `NODE_ACTIVE_STATUS` / `_due_label`）。
- **NAS 文件操作全项目贯彻「复制+删原，绝不 rename」**，毒化问题有专门模块
  `nasfs.py` 收口，且 `build_exe.py` 在构建前做了与让位失败同判据的 `[0/4]` 占用预检。
- **前端选择集判存统一 `Number(id)` 收口**、项目/环节 id 撞车用「对象当键」规避、
  多选派生显示态与批量范围严格分离——这类隐形 bug 都被注释点破了根因。
- **注释质量**：几乎每条"为什么"都附了踩坑日期和复现结论（如 `2026-09-27` 复选框
  preventDefault 回滚、`2026-10-04` 制作人反选漏判），可维护性极强。

## 五、后续建议（非阻塞）

1. **`models.py:10` 的 `today` 导入**：`from db import GROUPS_DEFAULT, now, today`
   里 `today` 仅在第 908 行 `checked_in_today()` 用到一次，`now` 大量使用——两者都真用了，
   不构成死代码；但 `today` 与 `date.today()` 语义易混，可在用到处改
   `today()`（已一致，无风险）。
2. **`paths.py:94 default_db_path` 与 `db.py:26 default_db_path` 重名**：这是
   刻意设计（`paths` 延迟导入 `db` 避免循环引用，`paths.default_db_path()` 只是
   转发器），注释已写明，**不要合并**。
3. **`collapse_all` / `set_collapsed_all`**：顶栏按钮已按用户要求撤掉，但方法仍被
   `diag.js` 自检和 `smoke_m4.py` 程序化调用——是活的，**不要当死代码删**。
