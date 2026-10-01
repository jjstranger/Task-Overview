"""把界面渲染成一张静态 HTML，专门用来肉眼核对版式（不启动 pywebview）。

为什么需要它：改版式（表格加列、行里加字段）时，pywebview 的冒烟只能断言
「元素在不在、类名对不对」，看不出**挤到一起了 / 串行了**。桌面窗口又没法截图。

做法：拿 app/web/index.html 当模板，把 `app.js` 前面的那一小段换成
「假桥 + 数据」，**样式和脚本内联进去**（于是预览页是自包含的，扔哪都能开），
于是渲染结果和真窗口一致。

用法：
    python tests/_diag/preview_gen.py            # 生成三张静态页
    # 直接用浏览器打开 _preview_*.html 就行。
    #
    # 想用无头浏览器截图也不是不行，但**这台机器上时好时坏**：第一次能出文件，
    # 之后连续调用常常一个都不产出（换独立 user-data-dir / 换输出目录 /
    # --headless=old 与 new 都不救）。而且 **别用 URL 上的 #hash 传视图**，
    # 一带 hash 就必定不出文件。所以这里一次生成三个文件，各自把视图写死；
    # 要截图就单独跑一张，别放在 for 循环里。

产出：
    _preview_tree.html       默认折叠态的项目页
    _preview_tree_open.html  项目行全展开（看环节行：进度条 / 制作人 / 备注）
    _preview_fin.html        财务页（9 列表格 + 7 张卡片）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

from db import Db  # noqa: E402
from finance import Finance  # noqa: E402
from models import Board  # noqa: E402
from smoke_fin_gui import seed  # noqa: E402  同一份样本数据，别再造一份

STUB = """
/* ---- 静态预览用的假桥：只喂数据，不做任何写操作 ---- */
const LOAD = __LOAD__;
const FIN = __FIN__;
const noop = () => Promise.resolve({ ok: true });
window.pywebview = { api: new Proxy({ load: () => LOAD, finance_data: () => FIN }, {
  get: (t, k) => (k in t ? t[k] : noop),
}) };
if (__OPEN__) {
  const open_ = (n) => { n.collapsed = 0; (n.children || []).forEach(open_); };
  LOAD.groups.forEach((g) => g.projects.forEach(open_));
}
window.addEventListener("load", () => setTimeout(() => {
  try { switchPage("__VIEW__"); } catch (e) { document.title = "ERR " + e.message; }
}, 200));
"""

VIEWS = [("tree", False), ("tree_open", True), ("fin", True)]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="board_preview_")
    path = os.path.join(tmp, "board.sqlite")
    seed(path)
    db = Db(path)
    db.open()
    payload = Board(db).load()
    payload["diag"] = False
    fin = Finance(db).data(None, None, "CNY")
    db.close()

    src = open(os.path.join(APP, "web", "index.html"), encoding="utf-8").read()
    css = open(os.path.join(APP, "web", "style.css"), encoding="utf-8").read()
    js = open(os.path.join(APP, "web", "app.js"), encoding="utf-8").read()
    # 样式和脚本**内联**进去，预览页就能脱离 app/web 独立打开（放哪都行，
    # 也能直接丢进 IDE 的内置预览面板），不用再操心相对路径。
    src = src.replace(
        '<link rel="stylesheet" href="style.css">',
        "<style>\n" + css + "\n</style>",
    )
    made = []
    for name, expand in VIEWS:
        stub = (STUB.replace("__LOAD__", json.dumps(payload, ensure_ascii=False))
                    .replace("__FIN__", json.dumps(fin, ensure_ascii=False))
                    .replace("__OPEN__", "true" if expand else "false")
                    .replace("__VIEW__", "fin" if name == "fin" else "tree"))
        html = src.replace(
            '<script src="app.js"></script>',
            "<script>\n" + stub + "\n</script>\n<script>\n" + js + "\n</script>",
        )
        out = os.path.join(HERE, f"_preview_{name}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        made.append(out)
    for p in made:
        print("preview:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
