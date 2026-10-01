# 代码审查标准与流程（天在看 / TaskOV）

> 本文档定义本项目的代码审查标准、分层检查清单与执行流程。
> 目标：代码干净、整洁、简练、健壮、运行高效。

## 一、审查原则

1. **先实测、再下结论**：任何"疑似 bug / 死代码 / 技术债"都必须先在代码里定位落点、
   跑一遍证据（引用计数、回归、冒烟），禁止凭描述猜测。
2. **单一来源**：品牌名、版本号、路径判据、停滞判据、活跃度白名单等，各只有一个实现出口
   （`version.py` / `paths.py` / `models.stale_gap()` / `NODE_ACTIVE_STATUS`），改一处即改全部。
3. **改动可验证**：每次改动必须能跑对应的回归 / 冒烟，断言覆盖"屏幕上真画出来没有"，
   而不是只看"窗口打开了"。
4. **注释服务于人**：注释写"为什么"和"坑"，不重复"是什么"；简体中文，不混日文假名与英文整句。
5. **不碰红线**：NAS 文件操作绝不 rename（复制+删原）；`dist\` 与工程目录不做删除；
   冒烟串行跑；诊断库一律 `?mode=ro`。

## 二、审查范围（分层）

| 层 | 文件 | 关注点 |
| --- | --- | --- |
| 数据层 | `app/db.py` | 连接、锁、迁移、便捷方法是否有死代码 |
| 业务层 | `app/models.py` `app/finance.py` `app/node_spec.py` | 口径一致性、事务、空值防御 |
| API 层 | `app/api.py` | 异常是否回传、参数校验、桥序列化 |
| 路径/入口 | `app/paths.py` `app/version.py` `app/opener.py` `app/_boot.py` | 判据只用 `sys.frozen`、单一来源 |
| 主层 | `app/main.py` `app/boot.py` `app/remind.py` | 生命周期、ctypes 声明、通知身份 |
| 前端 | `app/web/app.js` `diag.js` `index.html` `boot.html` `style.css` | DOM 定位带 class/kind、无死代码 |
| 工具层 | `tools/*.py` | 构建/打包/图标/同步脚本 |
| 测试层 | `tests/*` | 断言有效、无一次性脚本入库 |

## 三、分层检查清单

### 数据层 `db.py`
- [ ] 每个 `def` 是否有外部调用者（用 `_audit_dead.py` 交叉验证）
- [ ] 便捷方法（`q`/`one`/`run`）是否被业务层统一使用，避免混用直连 `self.conn`
- [ ] 迁移（`ALTER TABLE` / 索引）是否写在对应建表之后（开库不会 `no such table`）
- [ ] 共享写是否走 `_retry_locked`；批量写是否 `BEGIN`/`COMMIT`

### 业务层
- [ ] 停滞判据只调 `models.stale_gap()`，不另写一套
- [ ] 到期文案后端 `_due_label` 与前端 `dueTag` 逐字一致
- [ ] 活跃度只按 `NODE_ACTIVE_STATUS` 白名单计分；项目层/勾完成/建项不计分
- [ ] 财务口径：总合同额、外包支出（已付）、实际收入=已收−外包

### API 层
- [ ] 失败原因必须回传，前端 `alert` 出来，禁止 `except Exception` 吞掉
- [ ] js_api 对象内部引用一律下划线前缀（防止不可序列化对象挂桥）

### 主层
- [ ] ctypes 显式声明 `argtypes`/`restype`（防 64 位句柄被截断）
- [ ] 无人值守/`--smoke` 禁止弹 `MessageBoxW`
- [ ] 通知身份（AUMID）在 `main()` 最早期设置

### 前端
- [ ] 同一容器多可点元素必须带明确 class（`.badge.st`、`.mini.menu` 等）
- [ ] DOM 定位带 kind（`rowOf("node", id)`，项目与环节 id 会撞车）
- [ ] 提示文字写 `title`，不插 `.hint` 占版面

### 测试层
- [ ] `_` 前缀脚本是一次性的，不提交；长期诊断脚本不带下划线前缀
- [ ] `smoke_fin_gui` 与 `smoke_exe` 重复断言改字段名要两处一起改

## 四、判定标准

| 类别 | 判定 | 处理 |
| --- | --- | --- |
| 死代码 | `def` 仅定义行自身引用，且非入口/测试用例 | 删除 |
| 冗余 | 同一逻辑两处实现、可抽公共 helper | 合并 |
| 技术债 | 魔法值、重复断言、隐式约定 | 抽常量 / 单一来源 |
| 健壮性 | 缺空值防御、异常被吞、句柄截断 | 补防御 / 回传 / 声明类型 |
| 不合理 | 命名误导、注释错误、口径不一致 | 改名 / 改注释 / 统一 |

## 五、执行流程

```
1. 静态审计   python tests/_diag/_audit_dead.py  → 人工复核（去误报）
2. 逐层审查   按上表顺序通读 + 定位落点
3. 修复       先删死代码，再做逻辑精简/健壮性
4. 回归       数据层 smoke_*.py → GUI smoke_fin_gui.py fallback
             → exe smoke_exe.py persist → smoke_boot.py → check_icon.py
5. 记录版本   python tools/ver.py save "说明"
6. 打包交付   tools/build_exe.py → tools/pack_zip.py → present_files
```

## 六、铁律（违反即返工）

- NAS：绝不用 rename（复制 + 删原）；诊断库 `?mode=ro`；绝不用真实库做写入探测。
- 删除：不在 `dist\` / 工程目录做删除；系统 TEMP 不受限。
- 冒烟：串行；GUI 冒烟用 `fallback` 档（沙箱拦 WebView2 渲染进程）；抖了就重跑一次。
- 打包：`--distpath` 指到 TEMP；add-data 源写 `app/res`；产物复制+删原，绝不 rename。
- 前端改动后必须让用户重启应用才生效。
