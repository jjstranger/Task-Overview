# AGENTS.md — 开工必读

**天在看（TaskOV）**：单人用的本地项目看板（项目进度 / 财务 / 活跃度）。
技术栈 Python 3.13 + pywebview 6.2.1 + pystray + SQLite；PyInstaller **onedir** 打包成单目录 exe。
**不是**多人协作 / SaaS，所有数据在本地或 NAS 上的单个 SQLite 文件里。

> 本文件是「AI / 新接手的人每轮开工先读」的作业指导，跟源码一起提交。
> 给人看的完整使用说明在 `README.md`；代码长期笔记在工程外的 `..\.workbuddy\memory\`。

## 环境（本机固定值）

| 项 | 值 |
|---|---|
| 工程根 | `E:\WORK\WorkBuddy_Workspace\2026-09-25-04-38-26\Task-Overview` |
| 解释器 | `<venv>\Scripts\python.exe` |
| 入口 | `app\_boot.py`（`run.bat` 用 pythonw 启动；根目录**没有** .py 入口） |
| 数据文件 | `<挂载的共享盘>\T_T_Data\board.sqlite`（可被 `--db` > `BOARD_DB` > `data\config.json` 覆盖） |
| 产物 | `dist\天在看\天在看.exe`（onedir）+ `dist\天在看_v1.1_<时间戳>.zip` |

## 常用命令

```bash
PY="<venv>/Scripts/python.exe"

"$PY" app/_boot.py                          # 开发机启动（排查请用 run_debug.bat 看控制台）

"$PY" tools/build_exe.py                    # 重建 exe  ← 先关掉运行中的看板！
"$PY" tools/build_exe.py --outdir dist/_verify   # dist 被占用时的临时出路
"$PY" tools/pack_zip.py                     # 打 zip ← 要在 exe 冒烟之前打

"$PY" tools/ver.py                          # 有无未提交改动（出包前必须干净）
"$PY" tools/ver.py save "这轮改了什么"        # 提交
"$PY" tools/ver.py tag v1.2 "里程碑说明"      # 打标签

# 数据层回归：改了 Python 必须全跑（8 个）
for t in db finance project_money m4 node_spec activity_weight bulk export; do "$PY" tests/smoke_$t.py; done

# GUI / 打包态冒烟（必须带 fallback；两者不能并行）
"$PY" tests/_diag/smoke_fin_gui.py fallback
"$PY" tests/_diag/smoke_exe.py persist
```

## 铁律（违反必踩坑）

1. **重建 exe 前必须先关掉运行中的看板** —— 否则 `dist\天在看` 被占用、报 **err 32**。
   脚本 `[0/4]` 会自己预检并直接拦下（**exit 3**，0.6 秒返回，不再白跑 30 秒 PyInstaller）；
   确实要硬来加 `--force`。
2. **改前端** → 热替换 `dist\天在看\_internal\web\`，让用户重启即生效；**改后端** → 必须重新 build。
3. **产物搬运一律「复制 + 删原」，绝不 rename** —— rename 会毒化对象（网络盘上永久毒化）。
4. **删 `dist\` / 工程目录里的东西别用 shell `rm`** —— 会撞删除兜底、整条命令被掐且无报错。走 `tools/nasfs.py`。
5. **打 zip 要在 exe 冒烟之前** —— 冒烟会往 `dist\` 落 `data\`、`tests\smoke_note.txt`，会被打进包。
6. **冒烟必须带 `fallback`** —— 沙箱会拦 WebView2（报 `E_ABORT` / `bridge_ok=False`）。
7. **提交前 `ver.py` 必须干净** —— 保证「某个 exe 对应哪版代码」永远对得上。

## 红线（别顺手改）

- **可改**：工程 / 仓库名 `Task-Overview`。
- **故意保留**：`ProjectBoard`（`db.py` 互斥体名、`main.py` 自启注册表值名）—— 改了会让老装机失效。
- **不要动**：`app/version.py` 里的 `APP_NAME` / `EXE_STEM` / `AUMID` —— 会让桌面快捷方式与 Win11 通知身份失效。
- **不提交**：`dist/`、`data/`（见 `.gitignore`）。

## 细节去哪读

- `README.md` —— 完整使用说明（版本追踪 / 启动 / 打包 / 界面 / 快捷键）
- `DESIGN.md` —— 设计说明；`CODE_REVIEW.md` —— 审查标准；`GITHUB_PUSH_STEPS.md` —— 发布步骤
- 发布走 `tools/publish_github.py`（推到**工程外**的 `github\Task-Overview` 独立仓库；本地源码仓库**故意不挂 remote**）
- **代码长期笔记**（工程外）：`..\.workbuddy\memory\`
  - `MEMORY.md` = 索引 + 铁律
  - `map\01~06` = 本机坑 / 路径打包品牌 / 数据口径 / 界面交互 / 网络盘性能 / 回归版本
  - `YYYY-MM-DD.md` = 当日日志，**开工只读最后一段**（末尾有交接条）
