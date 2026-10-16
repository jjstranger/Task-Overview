/* 自检巡视（--smoke 专用，按需加载，正常使用不会下载/解析这个文件）
 *
 * 这块以前直接躺在 app.js 里，占 1750 行 —— 也就是主文件的一半。
 * 每次启动都要解析一份只给冒烟测试用的代码，改业务逻辑时还得划过它。
 * 现在由 app.js 的 loadDiag() 在 `D.diag` 为真时动态注入。
 *
 * 依赖：本文件是**经典脚本**（不是 module），注入后与 app.js 共享全局作用域，
 * 直接用那边的 D / $ / $$ / call / render / reload / setView 等。
 * 反向依赖只有 app.js 启动段那一个 loadDiag() 入口 —— 改函数名记得一起改。
 */

/* ---------- 仅自检使用的 DOM 定位 ---------- */

/* 按 id + 类型定位树里的行。
   **必须带 kind**：`bindDrag` 里项目和环节都写 `dataset.id`，
   两套 id 各自自增**会撞车**（实测出现过重复），光用 `[data-id]` 会取到
   第一个匹配（常常是项目行，而它没有 `.pick`）→ 断言假阳性，
   看着像"复选框没勾"。（2026-09-27 在这上面绕过弯路。） */
function rowOf(kind, id) {
  return document.querySelector(
    '#tree .row[data-kind="' + kind + '"][data-id="' + Number(id) + '"]');
}
const nodeRow = (id) => rowOf("node", id);

/* 找不到行时的现场快照。
   2026-10-16 加：multi 段后半 + date 段突然一起报「行找不到」，而数据（行数、
   选择条数）跟上一轮一模一样 —— 只记 has:false 根本分不清是**树折叠了**
   （行没渲染）、**id 对不上**、还是**压根不在这一页**。
   ⚠ 只在失败路径调用，正常跑不花时间。 */
const treeSnap = () => ({
  rows: Array.from(document.querySelectorAll("#tree .row"))
    .map((el) => (el.dataset.kind || "?") + ":" + (el.dataset.id || "?")),
  shut: Array.from(document.querySelectorAll("#tree .row .tw"))
    .filter((e) => e.textContent === "▶").length,
  open: Array.from(document.querySelectorAll("#tree .row .tw"))
    .filter((e) => e.textContent === "▼").length,
  page: (document.querySelector("#segPage button.act") || {}).textContent || null,
  /* 数据里到底有几个项目、各自什么状态、几个环节 ——
     跟 rows 一对，就能分辨"是筛选/隐藏把行滤掉了"还是"数据没了" */
  prj: (() => {
    try {
      return (D.groups || []).map((g) => g.key + ": " + (g.projects || [])
        .map((p) => "#" + p.id + "(" + p.status + "," + (p.children || []).length + "节)")
        .join(" "));
    } catch (e) { return String(e); }
  })(),
  /* collapsed 是**落库**的（`set_collapsed` / `collapse_all`），所以"行不见了"
     可能是"库里就折着"—— 光看 DOM 数不出这个，得从 D 里数。 */
  coll: (() => {
    try {
      let p = 0, nd = 0;
      (D.groups || []).forEach((g) => (g.projects || []).forEach((x) => {
        if (x.collapsed) p++;
        const walk = (arr) => (arr || []).forEach((y) => {
          if (y.collapsed) nd++;
          walk(y.children);
        });
        walk(x.children);
      }));
      return p + "项目/" + nd + "环节折着";
    } catch (e) { return String(e); }
  })(),
  bar: Array.from(document.querySelectorAll("#treeBar button")).map((b) => b.textContent),
});

/* ---------- 仅自检使用的版式量尺 ---------- */

/* 一段文字**实际画出来几行**。inline 元素每个 line box 一个 rect，
   所以 `getClientRects().length` 就是真实行数 —— 比 offsetHeight 稳
   （后者受 padding / 行高影响，且看不出折行）。

   2026-10-13 加：`preview_gen.py` 的注释里早写过「冒烟只能断言元素在不在、
   类名对不对，看不出挤到一起了」，而**金额 + 税率% + 税后提示**那一行正是
   这个盲区 —— 三个控件加一句中文提示挤在 520px 的弹窗里，放不下时
   「（税后 94,339.62 元）」会**静默折成两行**，看起来像排版坏了。
   以后凡是"一行里塞了好几个控件"的地方，都可以用它钉住。 */
const textLines = (el) => (el ? el.getClientRects().length : -1);

/* 一行的横向余量：正数 = 还空着多少 px；负数 = 内容已经溢出。
   ⚠ flex 行里子项会被压缩（不溢出但文字折行），所以判断折行**以 textLines 为准**，
   这个数用来看"还剩多少余量、要不要再收一点"。 */
const rowSlack = (el) => (el ? el.clientWidth - el.scrollWidth : null);

/* 一整行里**所有**带文字的元素各画了几行，取最大值。
   ⚠ 只盯某一个元素会被骗：2026-10-13 那次就是「税后提示没折、旁边的『税率%』
   被拆成两行」，只看 #finNet 永远看不见。input/select/textarea/button 不算
   （它们的文字不做折行，且 rect 语义不同）。 */
function rowMaxLines(rowEl) {
  if (!rowEl) return -1;
  const skip = { INPUT: 1, SELECT: 1, TEXTAREA: 1, BUTTON: 1 };
  let mx = 0;
  rowEl.querySelectorAll("*").forEach((el) => {
    if (skip[el.tagName]) return;
    if (!(el.textContent || "").trim()) return;
    mx = Math.max(mx, el.getClientRects().length);
  });
  return mx;
}

/* ---------- 仅自检使用的页面切换 ---------- */

/* 自检和快捷键都走这里，保证「点页签」和「调 setView」是同一条路径 */
function switchPage(v) {
  const b = document.querySelector('#segPage button[data-v="' + v + '"]');
  if (b) b.click(); else setView(v);
}

/* ---------- 自检巡视（--smoke 时 Python 侧要求） ----------
   走 JS→Python 这条通道上报，不用 evaluate_js：后者在部分环境下
   拿不到 loaded 事件，会报 "Main window failed to start" 或直接空等。 */
async function diagReport() {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const n = (sel) => document.querySelectorAll(sel).length;
  /* 单步超时：跨桥调用偶尔既不 resolve 也不 reject，
     没有超时的话自检会静默卡死，连"卡在哪一步"都拿不到。 */
  const guard = (p, ms, label) => Promise.race([
    Promise.resolve(p),
    new Promise((_, rj) => setTimeout(() => rj(new Error("超时 " + ms + "ms @ " + label)), ms)),
  ]);
  const rep = { stages: [], timings: {} };
  const step = async (s) => {
    rep.stages.push(s);
    await guard(call("diag_stage", s), 4000, "diag_stage:" + s);
  };
  let failed = "";

  const phase = async (name, ms, fn) => {
    if (failed) return;             // 前面挂了就不要再往下跑，免得连环报错
    /* 记每段真实耗时（秒，一位小数）。2026-10-13 加：那阵子多段开始超时，
       光看"哪段超时"分不清"这段本来就慢"还是"这台机器整体变慢" ——
       有了它一眼就能看出是某段贴着预算，还是每段都翻倍。 */
    const t0 = Date.now();
    try { await guard(fn(), ms, name); }
    catch (e) { failed = name; rep.err = String((e && e.message) || e); }
    rep.timings[name] = Math.round((Date.now() - t0) / 100) / 10;
  };

  await phase("tree", 8000, async () => {
    await step("tree");
    /* 运行模式：源码 / 打包后的 exe，以及 data 落在哪（打包最容易错的就是这个） */
    let pinfo = {};
    try { pinfo = (await call("pathinfo")) || {}; } catch (e) { pinfo = {}; }
    rep.tree = {
      rows: n("#tree .row"), groups: n("#tree .row.grp"),
      /* 分组行尾的两颗按钮：⋯（改名 / 新建 / 删分类）、＋（在这个分类下建项目） */
      grpMenus: n("#tree .row.grp .ops .mini.menu"),
      grpAdds: n("#tree .row.grp .ops .mini.add"),
      /* 默认只展开「商业 / 个人」两个分组标题行，项目行与环节都折着 */
      prjRows: n("#tree .row.prj"),
      nodeRows: n("#tree .row:not(.prj):not(.grp)"),
      shutArrows: Array.from(document.querySelectorAll("#tree .row .tw"))
        .filter((e) => e.textContent === "▶").length,
      /* 顶栏应该只有三个页面页签（项目 / 活跃 / 财务），旧的分类筛选按钮应已不存在 */
      pages: n("#segPage button"),
      cats: n("#segFilter button"),
      /* 展开折叠那三个按钮按用户要求撤掉了，这里只剩「筛选 ▾」 */
      treeBtns: n("#treeBar button"),
      filterText: ($("#treeFilter") || {}).textContent || "",
      /* 后端 load() 带的 archived 字段要真的到前端（归档开关靠它） */
      archField: D.groups.every((g) => g.projects.every((p) => p.archived !== undefined)),
      btns: Array.from(document.querySelectorAll("#segPage button")).map((b) => b.textContent),
      frozen: pinfo.frozen === true,
      base: pinfo.base || "",
      data: pinfo.data || "",
      /* 通知身份（AUMID）：pathinfo 回读的是系统里真实的值，不是常量 */
      aumid: pinfo.aumid || "",
      /* 幻影滚动检查：内容装得下时 main 不许有滚动间隙
         （旧 bug 是 height:100% 强制撑满，加上筛选栏恒高 ~30px，短内容也出滚动条） */
      gap: (() => { const m = document.querySelector("main"); return m.scrollHeight - m.clientHeight; })(),
      slack: (() => {
        const m = document.querySelector("main").getBoundingClientRect();
        return Math.round(m.bottom - document.querySelector("#tree").getBoundingClientRect().bottom);
      })(),
      /* 到期小标：文案必须是整句话（「3 天后到期」/「今天到期」）。
         用户说「D-1」「今天」像密码，得先知道 D 是 deadline 才读得懂。 */
      dueTexts: Array.from(document.querySelectorAll("#tree .row .meta.due"))
        .map((e) => e.textContent),
      /* 状态徽章恒带 `.st`；一行上还有「今日必做 / 置顶 / 已归档」等别的 badge */
      stBadges: n("#tree .row .badge.st"),
      rawBadges: n("#tree .row .badge"),
      /* 品牌：**不显示名字**（2026-10-01 用户口径）—— 名字已经在窗口标题栏上，
         顶栏再写一遍是同一个信息占两块地方。所以这里钉的是「图标还在、文字没了」：
         以前这一行会产出「项目看板」四个字，断言反着说「要么是空的」。 */
      brandText: (($("#logoBox") || {}).textContent || "").trim(),
      brandImg: document.querySelectorAll("#logoBox img").length,
      /* 顶栏的排列顺序：**图标 → 页签 → 日期 → 刷新**。日期说的是「当前这一页
         的数据几点读的」，所以跟着页签走；放回图标边上会被读成页面标题的一部分。
         记的是 id 序列（spacer 只是撑开的空位），冒烟拿去比先后。 */
      headerOrder: Array.from(document.querySelectorAll("header > *"))
        .map((e) => e.id || (e.classList.contains("spacer") ? "spacer" : e.tagName.toLowerCase())),
      docTitle: document.title,
      /* 设置页末尾的版本行：版本号 + 发布日期 */
      verLine: (($("#setVer") || {}).textContent || ""),
      /* 行尾 ⋯ 也钉了明确 class（裸 `.mini` 取第一个匹配，同 .badge.st 那条教训） */
      moreBtns: n("#tree .row .ops .mini.more"),
      /* 只有状态徽章该有"能点"的样子：别的徽章是纯标记 */
      rawCursor: (() => {
        const b = document.querySelector("#tree .row .badge:not(.st)");
        return b ? getComputedStyle(b).cursor : "";
      })(),
      stCursor: (() => {
        const b = document.querySelector("#tree .row .badge.st");
        return b ? getComputedStyle(b).cursor : "";
      })(),
    };
    /* 「点状态徽章才出状态菜单」—— 用户报的是「今日必做」的**文字**上：
       那种项目行一行有两个 badge，裸 `.badge` 取到的是「今日必做」，
       于是点状态没反应、点「今日必做」反倒弹出状态菜单。挑这一行真点两下。 */
    const twoBadgeRow = Array.from(document.querySelectorAll("#tree .row.prj"))
      .find((el) => el.querySelector(".badge") !== el.querySelector(".badge.st"));
    rep.tree.stClick = null;
    if (twoBadgeRow) {
      const first = twoBadgeRow.querySelector(".badge");
      const st = twoBadgeRow.querySelector(".badge.st");
      rep.tree.stClick = { firstText: first.textContent, stText: st.textContent };
      st.click();
      await wait(160);
      const pop = document.querySelector(".popup");
      rep.tree.stClick.menu = pop
        ? Array.from(pop.children).map((d) => d.textContent) : null;
      closePopup();
      await wait(60);
      /* 反证：点「今日必做」那颗**不该**弹任何菜单 */
      first.click();
      await wait(160);
      rep.tree.stClick.firstPops = !!document.querySelector(".popup");
      closePopup();
    }
    /* 新建项目弹窗：客户框要等弹窗打开才填，所以真开一次再看 */
    openNewDialog();
    await wait(200);
    const cli = document.querySelector("#newClient");
    rep.neww = {
      flds: n("#ovNew .fld"),
      clients: n("#clientOpts option"),
      /* 客户框必须是「可选可填」的组合框，不是一个空下拉 */
      clientTag: cli ? cli.tagName : "",
      clientList: cli ? !!cli.getAttribute("list") : false,
      /* 「浏览」按钮得挂上 handler，pick_dir 那条链路才走得通 */
      pickBound: typeof ($("#btnPick") || {}).onclick === "function",
      /* 币种输入框已撤（只按人民币结算），这几个是剩下的金额相关字段 */
      money: ["#newAmount", "#newPaid", "#newTax", "#newPaidDate"]
        .every((s) => !!document.querySelector(s)),
      ccyGone: !document.querySelector("#newCcy"),
      /* 分类选择器：按库里的分类现画（商业 / 个人只是出厂值） */
      catBtns: n("#segCat button"),
      catTexts: Array.from(document.querySelectorAll("#segCat button")).map((b) => b.textContent),
      catKeys: Array.from(document.querySelectorAll("#segCat button")).map((b) => b.dataset.c),
      catAct: n("#segCat button.act"),
      date: ($("#newPaidDate").value || "").length,
      /* 截止日期：**原生 date 框**（要日历选择器）+ 空值时自绘的 YYYY/MM/DD。
         ⚠ 原生框空值时画的是「yyyy/mm/日」，页面改不了（lang 属性实测无效、placeholder 无效），
           只能把那段 mask 设成透明、自己顶一个占位 —— 所以这里查的是**自绘那行**
           （#newDlPh），不是 input 的 placeholder（那属性对 date 框本来就是摆设）。
         ⚠ "透明真生效"在这里验不了：外链样式表在 file:// 下读 cssRules 抛 SecurityError，
           而 getComputedStyle(el,"::-webkit-datetime-edit") 返回的是**元素自身**样式（实测），
           永远"看着对"。那条只能靠截图验，见 tests/_diag/check_datefield.py。 */
      dueType: ($("#newDl") || {}).type || "",
      duePh: ($("#newDlPh") || {}).textContent || "",
      duePhShown: (() => {
        const e = $("#newDlPh");
        return !!e && getComputedStyle(e).display !== "none";
      })(),
      /* 结款日期（2026-10-13 新增）跟截止日期是同一套：原生 date 框 + 自绘占位。
         它是「结款逾期 N 天」的**唯一**依据，所以框本身在不在必须钉住。 */
      settleType: ($("#newSettle") || {}).type || "",
      settlePh: ($("#newSettlePh") || {}).textContent || "",
      settlePhShown: (() => {
        const e = $("#newSettlePh");
        return !!e && getComputedStyle(e).display !== "none";
      })(),
      /* 自绘占位盖在 input 上，少了 pointer-events:none 就点不开日历 —— 占位中心点上
         必须是 input 自己（elementFromPoint 是唯一能逮住"被挡住"的判据） */
      duePhHit: (() => {
        const e = $("#newDlPh");
        if (!e) return null;
        const b = e.getBoundingClientRect();
        if (!b.width) return null;
        const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
        return hit ? (hit.id || hit.className || hit.tagName) : null;
      })(),
      /* 精简版面（2026-09-30 用户要求）：新建页那句说明撤了，客户栏的提示也撤了，
         名称框的占位符换成「输入项目名，可用中文。」 */
      subGone: !document.querySelector("#ovNew .sub"),
      titlePh: ($("#newTitle") || {}).placeholder || "",
      noCliHint: document.querySelector("#ovNew").textContent.indexOf("库里没有") < 0,
      /* 设置页：提示文字一个都不留（.hint 恒 0），全部收成悬停的 title */
      setHints: n("#ovSet .hint"),
      setTips: n("#ovSet .lb[title]"),
      setTipKeys: Array.from(document.querySelectorAll("#ovSet .lb[title]"))
        .map((e) => e.textContent),
      /* 顶栏刷新按钮：环形箭头必须是**内联 SVG**（不引用外部图片） */
      refreshIcon: n("#btnRefresh svg"),
      refreshImg: n("#btnRefresh img, #btnRefresh svg image"),
    };
    modal("#ovNew", false);

    /* 制作人：行内要挨着备注显示，行菜单里要能改（跟备注是一套） */
    const prow = document.querySelector("#tree .row.prj");
    const mini = prow ? prow.querySelector(".mini.more") : null;
    let menu = null;
    if (mini) {
      mini.click();
      await wait(200);
      const pop = document.querySelector(".popup");
      menu = pop ? Array.from(pop.children).map((d) => d.textContent) : null;
      closePopup();
    }
    rep.artist = {
      tags: n("#tree .row .meta.who"),
      text: (document.querySelector("#tree .row .meta.who") || {}).textContent || "",
      menu: menu,
    };
  });

  /* 顶栏刷新按钮：点一下要能 ① 重画日期（应用可能开着过夜）② 重新读数据文件并更新显示。
     不靠"点了有反应"糊弄 —— 先让后端的值变掉、界面还停在旧值，再点按钮看它有没有跟上。 */
  await phase("refresh", 20000, async () => {
    await step("refresh");
    switchPage("tree");
    await wait(300);
    const rrep = {};
    rrep.icon = n("#btnRefresh svg");          // 内联 SVG，不是外部图片
    const el = $("#date");
    const want = (() => {
      const d = new Date();
      return (d.getMonth() + 1) + "-" + d.getDate() + " 周" + "日一二三四五六"[d.getDay()];
    })();
    el.textContent = "1-1 周一";               // 假装过了夜、还显示着昨天
    rrep.fake = el.textContent;

    /* 绕开前端直接改库里的项目名（模拟"另一处/另一台机器改了数据"）。
       此刻界面**不该**跟上 —— 跟上了说明它自己会刷，那这个按钮就测不出东西了。 */
    const prj0 = ((D.groups || [])[0] || {}).projects || [];
    const pid = prj0[0] ? prj0[0].id : 0;
    const oldTitle = prj0[0] ? prj0[0].title : "";
    rrep.pid = pid;
    let picked = false;
    if (pid) {
      await call("rename", "project", pid, "冒烟刷新测试");
      await wait(200);
      rrep.staleBefore = document.querySelector("#tree").textContent.indexOf("冒烟刷新测试") >= 0;

      $("#btnRefresh").click();
      await wait(1500);
      rrep.spin = !!document.querySelector("#btnRefresh svg.spin");
      rrep.date = el.textContent;
      rrep.dateOk = el.textContent === want;
      picked = document.querySelector("#tree").textContent.indexOf("冒烟刷新测试") >= 0;
      rrep.pickedUp = picked;

      /* 收尾：名字改回去，别把后面的阶段带脏 */
      await call("rename", "project", pid, oldTitle);
      await reload(); await wait(300);
      rrep.restored = document.querySelector("#tree").textContent.indexOf(oldTitle) >= 0;
    }
    rep.refresh = rrep;
  });

  /* 分类（分组）：新建 / 改名 / 删除三条路都真走一遍（2026-09-30）。
     顶层那几个筐从代码里写死的常量变成库里的数据（groups 表）。
     键是稳定标识、名字可改 —— 所以「改名不丢项目」是这里最该盯的一条。 */
  await phase("groups", 20000, async () => {
    await step("groups");
    switchPage("tree");
    await wait(300);
    const greps = {};
    const gRow = (key) => document.querySelector('#tree .row.grp[data-gk="' + key + '"]');
    const gBtn = (key) => {
      const r = gRow(key);
      return r ? r.querySelector(".ops .mini.menu") : null;
    };
    const gAdd = (key) => {
      const r = gRow(key);
      return r ? r.querySelector(".ops .mini.add") : null;
    };
    const gTitle = (key) => {
      const r = gRow(key);
      return r ? ((r.querySelector(".title") || {}).textContent || "") : null;
    };

    greps.before = (D.groups || []).map((g) => g.key);
    greps.menus = n("#tree .row.grp .ops .mini.menu");
    greps.adds = n("#tree .row.grp .ops .mini.add");
    /* 分组行 ⋯ 里那三项 */
    const mb = gBtn(greps.before[0]);
    greps.menuItems = null;
    if (mb) {
      mb.click();
      await wait(200);
      const pop = document.querySelector(".popup");
      greps.menuItems = pop ? Array.from(pop.children).map((d) => d.textContent) : null;
      closePopup();
    }

    /* ① 新建 */
    const added = await call("group_add", "冒烟分类");
    greps.addOk = !!(added && added.ok);
    greps.newKey = (added && added.key) || "";
    await reload(); await wait(300);
    greps.rowAdded = !!gBtn(greps.newKey);
    greps.rowAddedAdd = !!gAdd(greps.newKey);
    greps.titleAdded = gTitle(greps.newKey);

    /* ② 行尾的 ＋：开的就是同一个「新建项目」页，而且**默认选中这一行那个分类**。
       顺带证明「库里新加的分类会出现在弹窗里」—— 这颗按钮就是这个新分类。 */
    const pid = (((D.groups || [])[0] || {}).projects || [])[0]
      ? D.groups[0].projects[0].id : 0;
    greps.pid = pid;
    const ab = gAdd(greps.newKey);
    greps.addOpen = false;
    if (ab) {
      ab.click();
      await wait(300);
      const ov = document.querySelector("#ovNew");
      greps.addOpen = !!ov && getComputedStyle(ov).display !== "none";
      greps.addSel = ((document.querySelector("#segCat button.act") || {}).dataset || {}).c || "";
      greps.addFlds = n("#ovNew .fld");
      greps.addHasTitle = !!document.querySelector("#newTitle");
      modal("#ovNew", false);
      await wait(150);
    }

    /* ③ 双击分类名就地改（用户要的：文字处双击可编辑）。
       这里真发 dblclick + 改文字 + 回车，不直接调 group_rename —— 走用户那条路。 */
    const gtr = gRow(greps.newKey).querySelector(".title");
    gtr.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    await wait(150);
    greps.dblEdit = gtr.contentEditable === "true";
    gtr.textContent = "冒烟分类B";
    /* ⚠ blur 只有在元素**确实是 activeElement** 时才触发 onblur（done() 在那儿）。
       handler 里的 focus() 偶尔会被上一步关弹窗的焦点恢复抢走 —— 于是改名压根
       没提交、那行一直停在编辑态（2026-10-01 实测偶发过一次 202/203）。
       所以：先自己 focus 确保焦点在位，再发 Enter 键 —— 跟用户按回车是同一条路。 */
    gtr.focus();
    gtr.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    gtr.blur();
    await wait(900);                       // done() 里还有一次 await call + await reload
    /* ⚠ 名字要从**库里**读：DOM 上那行是我们自己刚改过 textContent 的，
       拿它当"改名成功"的证据等于自欺 —— 旧写法就是这样，改名没提交也照样绿。 */
    greps.renamed = ((D.groups || []).find((g) => g.key === greps.newKey) || {}).name
      === "冒烟分类B";
    greps.renamedDom = gTitle(greps.newKey) === "冒烟分类B";
    greps.renamedKeyKept = (D.groups || []).some((g) => g.key === greps.newKey);
    const tr2 = gRow(greps.newKey);
    greps.dblOff = !tr2 || (tr2.querySelector(".title") || {}).contentEditable !== "true";
    greps.dblFocus = document.activeElement === document.body
      || !tr2 || !tr2.contains(document.activeElement);

    /* ④ 项目挪过去：它应当紧跟在那个分组行后面（还在原来的分组里就是错的） */
    await call("set_group", pid, greps.newKey);
    await reload(); await wait(300);
    const rowsNow = Array.from(document.querySelectorAll("#tree .row"));
    const gi = rowsNow.findIndex((r) => r.dataset.gk === greps.newKey);
    const pi = rowsNow.findIndex((r) =>
      r.dataset.kind === "project" && String(r.dataset.id) === String(pid));
    greps.moved = gi >= 0 && pi === gi + 1;

    /* ⑤ 非空分类删不掉，而且要说清几个项目 */
    const del1 = await call("group_delete", greps.newKey);
    greps.delBusyMsg = (del1 && del1.msg) || "";
    greps.delBusyOk = !!(del1 && !del1.ok && /1 个项目/.test(greps.delBusyMsg));

    /* ⑥ 挪回原分类 → 空筐就能删；树里那一行也没了 */
    await call("set_group", pid, greps.before[0]);
    const del2 = await call("group_delete", greps.newKey);
    greps.delOk = !!(del2 && del2.ok);
    await reload(); await wait(300);
    greps.rowGone = !gRow(greps.newKey);
    greps.after = (D.groups || []).map((g) => g.key);
    greps.restored = greps.after.join(",") === greps.before.join(",");

    /* ⑦ 分类**删掉之后**再开新建项目页：按钮要跟着变少，
       而且不能还停在一个已经删掉的键上（那样点「创建」就带着野键过去了）。 */
    openNewDialog();
    await wait(250);
    const ck = Array.from(document.querySelectorAll("#segCat button")).map((b) => b.dataset.c);
    greps.catFollow = {
      keys: ck,
      n: ck.length,
      gone: ck.indexOf(greps.newKey) < 0,
      act: n("#segCat button.act"),
      actKey: ((document.querySelector("#segCat button.act") || {}).dataset || {}).c || "",
    };
    greps.catFollow.ok = greps.catFollow.n === greps.before.length
      && greps.catFollow.gone && greps.catFollow.act === 1
      && greps.before.indexOf(greps.catFollow.actKey) >= 0;
    modal("#ovNew", false);
    await wait(150);

    rep.groups = greps;
  });

  await phase("heat", 15000, async () => {
    await step("heat");
    switchPage("heat");
    await wait(1200);
    rep.heat = { cells: n("#heatGrid i"), cards: n("#heatCards .hcard"),
                 years: n("#segYear button"),
                 /* 口径文案：跟 models.NODE_ACTIVE_STATUS 同一句，逐字断言 */
                 tip: (($("#heatTip") || {}).textContent || ""),
                 tabAct: (document.querySelector("#segPage button.act") || { dataset: {} }).dataset.v || null,
                 /* 幻影滚动检查（同项目页）：gap=滚动间隙，slack=内容底到 main 底的余量 */
                 gap: (() => { const m = document.querySelector("main"); return m.scrollHeight - m.clientHeight; })(),
                 slack: (() => {
                   const m = document.querySelector("main").getBoundingClientRect();
                   return Math.round(m.bottom - document.querySelector("#heat").getBoundingClientRect().bottom);
                 })() };
  });

  /* 变更记录（2026-09-30）：面板只列最近一周、按天分组可折叠，
     更早的走「查看更多」弹窗翻页。种子跨 10 天各铺 2 条，
     所以「面板 7 天 / 弹窗里还多出 3 天」是能被真验出来的（不是看代码推断）。 */
  await phase("logs", 25000, async () => {
    await step("logs");            // 接着 heat 阶段，页面还在活跃页上
    const box = $("#heatLogs");
    const grps = () => Array.from(box.querySelectorAll(".dgrp"));
    const clickHead = async (i) => {
      const g = grps()[i];
      if (!g) return false;
      g.querySelector(".dgh").click();
      await wait(180);             // 每次点完都整段重画，节点是新的一批
      return true;
    };
    const lg = {
      hint: ($("#heatLogHint").textContent || ""),
      days: grps().length,
      rows: box.querySelectorAll(".log").length,
      headText: grps().length ? grps()[0].querySelector(".dgh").textContent : "",
      /* 每一组都有日期头（空组不可能出现，但那是渲染 bug，这里顺手钉一下） */
      everyHasHead: grps().every((g) => !!g.querySelector(".dgh")),
      firstOpen: grps().length ? grps()[0].classList.contains("open") : false,
      restClosed: grps().slice(1).every((g) => !g.classList.contains("open")),
    };
    /* 面板里最老的一天不许早于后端给的窗口起点 */
    const since = (heatData || {}).recent_since || "";
    lg.since = since;
    lg.oldestOk = grps().every((g) => String(g.dataset.day) >= since);
    lg.logTotal = (heatData || {}).log_total;
    lg.windowDays = (heatData || {}).window_days;

    await clickHead(0);
    lg.collapsed = grps().length ? !grps()[0].classList.contains("open") : false;
    lg.collapsedRows = box.querySelectorAll(".log").length;
    await clickHead(0);
    lg.reopened = grps().length ? grps()[0].classList.contains("open") : false;

    /* 「查看更多」→ 全量记录弹窗 */
    lg.moreVisible = getComputedStyle($("#heatLogMore")).display !== "none";
    $("#heatLogMore").click();
    await wait(200);
    lg.dlgOpen = getComputedStyle($("#ovLogs")).display;
    for (let i = 0; i < 25 && !n("#allLogs .dgrp"); i++) await wait(200);
    lg.allDays = n("#allLogs .dgrp");
    lg.allRows = n("#allLogs .log");
    lg.totalText = ($("#logTotal").textContent || "");
    /* 弹窗里的条数要对得上前端拿到的 log_total（两边口径别悄悄跑偏） */
    lg.totalOk = !!lg.logTotal
      && lg.totalText.indexOf("共 " + lg.logTotal + " 条") >= 0;
    /* 弹窗里能看到比面板更早的天 —— 那几天只有这儿翻得到 */
    lg.allDaysGt = lg.allDays > lg.days;
    if (n("#allLogs .dgrp")) {
      document.querySelector("#allLogs .dgrp .dgh").click();
      await wait(180);
      lg.allCollapsed =
        !document.querySelector("#allLogs .dgrp").classList.contains("open");
    }
    /* 「加载更多」：一次 200 条，种子只有几十条 → 应该自己藏起来 */
    lg.moreShown = getComputedStyle($("#logMore")).display !== "none";
    $("#logClose").click();
    await wait(180);
    lg.dlgClosed = getComputedStyle($("#ovLogs")).display;
    rep.logs = lg;
  });

  await phase("fin", 20000, async () => {
    await step("fin");
    /* 点真实的页签按钮，不走 setView，验证「点页签切页面」整条链路 */
    switchPage("fin");
    await wait(1600);
    rep.fin = {
      panel: getComputedStyle($("#fin")).display,
      tree: getComputedStyle($("#tree")).display,
      tabAct: (document.querySelector("#segPage button.act") || { dataset: {} }).dataset.v || null,
      cards: n("#finCards .hcard"),
      /* 卡片分两行各三张（用户定的：先看总额/已收/待收，再看外包/实际/收款率） */
      cardRows: Array.from(document.querySelectorAll("#finCards .hcards"))
        .map((r) => r.querySelectorAll(".hcard").length),
      cardLabels: Array.from(document.querySelectorAll("#finCards .hcard .l")).map((e) => e.textContent),
      /* 默认年份必须是本年份，不是「全部年份」 */
      yearVal: ($("#finYear") || {}).value || "",
      cols: n("#finTable .ftrow.head > div"),
      head: Array.from(document.querySelectorAll("#finTable .ftrow.head > div")).map((e) => e.textContent),
      rows: n("#finTable .ftrow.body"),
      aging: n("#finAging .abk"),
      clients: n("#finClientSum .ftrow"),
      /* 外包商汇总（2026-10-16 打通外包商 ↔ 外包支出）。
         行数要算**数据行**，不算表头 —— 直接数 .ftrow 会把 head 也算进去，
         那正好是最容易写成假绿的地方（没有数据时 head 也不画，所以 ≥1 会绿）。 */
      vendorRows: n("#finVendorSum .ftrow.body"),
      vendorHead: Array.from(document.querySelectorAll("#finVendorSum .ftrow.head > div"))
        .map((e) => e.textContent),
      /* 样本：冒烟工作室（团队）名下一笔已付 20000 + 一笔应付 5000 */
      vendorRow: (() => {
        const el = Array.from(document.querySelectorAll("#finVendorSum .ftrow.body"))
          .find((r) => (r.querySelector(".nm") || {}).textContent === "冒烟工作室");
        if (!el) return null;
        return {
          cells: Array.from(el.children).map((c) => c.textContent),
          type: (el.children[1] || {}).textContent || "",
        };
      })(),
      /* 账面上的那两笔也必须带上外包商名字（明细里"钱付给了谁"） */
      outsourceVendor: (fin.records || [])
        .filter((r) => r.kind === "outsource").map((r) => r.vendor || ""),
      /* 口径：外包支出要能从卡片的数里对上（已收 − 外包 = 实际收入） */
      money: {
        received: (fin.cards || {}).received,
        outsource: (fin.cards || {}).outsource,
        net: (fin.cards || {}).net,
      },
      proj: ((fin.projects || [])[0] || {}),
      tip: ($("#finTip").textContent || "").slice(0, 60),
      /* 幻影滚动检查（同项目页）：内容装得下时不许有滚动间隙 */
      gap: (() => { const m = document.querySelector("main"); return m.scrollHeight - m.clientHeight; })(),
      slack: (() => {
        const m = document.querySelector("main").getBoundingClientRect();
        return Math.round(m.bottom - document.querySelector("#fin").getBoundingClientRect().bottom);
      })(),
      /* 筛选栏：年份 / 客户都是下拉，币种那一组必须不存在（只按人民币结算） */
      yearTag: ($("#finYear") || {}).tagName || "",
      yearOpts: Array.from(document.querySelectorAll("#finYear option")).map((o) => o.textContent),
      ccySeg: n("#finCcy"),
      clientTag: ($("#finClient") || {}).tagName || "",
      dirBtn: n("#finDir"),
      /* 导出：一个按钮 + 一个 popup，四条命令都在里面 */
      exportTag: ($("#finExport") || {}).tagName || "",
      exportMenu: (() => {
        const b = $("#finExport");
        if (!b || b.tagName !== "BUTTON") return null;
        b.click();
        const pop = document.querySelector(".popup");
        const items = pop ? Array.from(pop.children).map((d) => d.textContent) : null;
        closePopup();
        return items;
      })(),
      /* 四组数据的标签页（2026-10-16）：顺序 + 默认「项目款项」+ 只露一个。
         ⚠ 表格 DOM 一直都在（切 tab 只改 display），所以光数行数看不出 tab 有没有work ——
         必须真的点一下再读 display。
         ⚠ 键名带 finTab 前缀：`rep.fin.tabAct` 已经被上面的**页面页签**占了
           （项目 / 财务 / 活跃），同名会把那个覆盖掉。 */
      finTabBtns: Array.from(document.querySelectorAll("#finTabs button")).map((b) => b.textContent),
      finTabKeys: Array.from(document.querySelectorAll("#finTabs button")).map((b) => b.dataset.t),
      finTabAct: (document.querySelector("#finTabs button.act") || { dataset: {} }).dataset.t || null,
      paneVis: ["#fsPrj", "#fsCli", "#fsVdr", "#fsAge"].map((s) => {
        const el = document.querySelector(s);
        return el ? getComputedStyle(el).display !== "none" : null;
      }),
    };

    /* 切到「应收账龄」再切回「项目款项」：验证切换真换表、且切回来没问题。
       （放在 rep.fin 赋值之后 —— 赋值那一刻要的是"默认态"的快照。） */
    const finTabClick = async (t) => {
      const b = document.querySelector('#finTabs button[data-t="' + t + '"]');
      if (b) { b.click(); await wait(180); }
    };
    await finTabClick("age");
    rep.fin.ageAct = (document.querySelector("#finTabs button.act") || { dataset: {} }).dataset.t || null;
    rep.fin.ageVis = ["#fsPrj", "#fsCli", "#fsVdr", "#fsAge"].map((s) => {
      const el = document.querySelector(s);
      return el ? getComputedStyle(el).display !== "none" : null;
    });
    rep.fin.ageTable = n("#finAging .abk");           // 切过去后账龄表还在、有格子
    await finTabClick("prj");
    rep.fin.backVis = getComputedStyle($("#fsPrj")).display !== "none";
  });

  /* 税后提示 + 「结款（核销待收）」（2026-10-13 新增，2026-10-16 改口径）。
     ⚠ 必须**走完整点击路径**（建样本 → 点按钮 → 点确认 → 回查库）：
        只断言"按钮画出来了"是假绿 —— 2026-10-10 那个删除按钮就是这么一路绿灯
        而功能是坏的（绑了 ReferenceError，点了没反应）。

     2026-10-16 改了什么：结款从"把合同额改成实收、多余的合同额记录合并删除"
     改成"核销差额"。所以这里最要紧的两条断言是「**合同额一条没动**」和
     「**多出一条核销记录**」—— 旧口径下这两条必挂，正好把改动钉死。 */
  await phase("payoff", 30000, async () => {
    await step("payoff");
    const pp = {};
    const errBefore = window.__err;
    const realAlert = window.alert;

    /* 样本建在**临时项目**上（跑完删干净），不碰冒烟主数据 */
    const made = await call("add_project", "结款冒烟样本", "commercial", 0, "",
                            { contract: "100000" });
    const pid = made && made.id;
    pp.made = !!pid;
    const today = isoOf(new Date());
    await call("finance_add", { kind: "payment", project_id: pid, amount: "30000",
                                date: today, status: "已收" });
    await call("finance_add", { kind: "payment", project_id: pid, amount: "5000",
                                date: today, status: "待收" });   // 没到手，不该计入
    await reload();
    await wait(400);

    /* 打开「记一笔」并选到这个项目 */
    openFinDialog(null, pid);
    await wait(500);
    pp.dlgOpen = getComputedStyle($("#ovFin")).display !== "none";
    pp.prjOk = String($("#finPrj").value) === String(pid);
    pp.btnText = (($("#finPayoff") || {}).textContent || "").trim();
    pp.btnCls = ($("#finPayoff") || {}).className || "";
    pp.visWhenPayment = getComputedStyle($("#finPayoffRow")).display !== "none";
    /* 「核销」这颗类型按钮得真的画出来（2026-10-16 新增的第五种款项） */
    pp.kinds = Array.from(document.querySelectorAll("#finKind button"))
      .map((b) => b.dataset.k);

    /* ---- 税后提示：填了金额 + 税率才出现，算式是 金额 ÷ (1+税率%) ----
       ⚠ 用真的 input 事件，别直接调 syncFinNet —— 那测的是函数不是**绑定**。 */
    const fire = (sel, v) => {
      const el = $(sel);
      el.value = v;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    fire("#finAmount", "100000");
    fire("#finTax", "6");
    await wait(150);
    pp.netText = ($("#finNet").textContent || "").trim();
    fire("#finTax", "");
    await wait(120);
    pp.netEmptyNoTax = ($("#finNet").textContent || "").trim() === "";
    fire("#finTax", "6%");                       // 「6%」带百分号也要认
    await wait(120);
    pp.netPercent = ($("#finNet").textContent || "").trim();

    /* ---- 版式：这行**必须一行画完**（2026-10-13 用户反馈"文字换行、难看"）----
       挑两个代表性样本：100000（常规）和 1234567.89（七位数，接近合同额量级）。
       ⚠ 判据是 rowMaxLines（整行所有文字的最大行数），**不是**只看税后提示 ——
       这次真正被挤折的是旁边的「税率%」，只看 #finNet 永远看不见。 */
    fire("#finAmount", "100000");
    fire("#finTax", "6");
    await wait(150);
    pp.finNetText = (($("#finNet").textContent) || "").trim();
    pp.finRowLines = rowMaxLines($("#finNet").closest(".fld"));
    fire("#finAmount", "1234567.89");
    fire("#finTax", "13");
    await wait(150);
    pp.finNetTextBig = (($("#finNet").textContent) || "").trim();
    pp.finRowLinesBig = rowMaxLines($("#finNet").closest(".fld"));
    pp.finRow = (() => {
      const f = $("#finNet").closest(".fld");
      return { h: f.offsetHeight, slack: rowSlack(f), boxW: $("#ovFin .ovbox").clientWidth };
    })();

    /* ---- 最严苛的一档：弹窗**出现纵向滚动条**时（窗口矮了就出）。 ----
       `.ovbox` 是 max-height:86% + overflow:auto，滚动条白吃 15px ——
       加宽之前这行的余量正好是 0，一吃就挤折（整行 50px → 67px）。
       这里人工把 overflow-y 设成 scroll 把这 15px 扣掉，钉住"有滚动条也不折"。 */
    (() => {
      const box = $("#ovFin .ovbox");
      const before = box.clientWidth;
      box.style.overflowY = "scroll";
      pp.finScrollCost = before - box.clientWidth;
    })();
    await wait(150);
    pp.finRowLinesScroll = rowMaxLines($("#finNet").closest(".fld"));
    pp.finRowHScroll = $("#finNet").closest(".fld").offsetHeight;
    $("#ovFin .ovbox").style.overflowY = "";

    fire("#finAmount", "abc");                   // 金额乱填 → 提示必须消失，不能瞎算
    await wait(120);
    pp.netEmptyBadAmount = ($("#finNet").textContent || "").trim() === "";
    fire("#finAmount", "");
    fire("#finTax", "");

    /* 换成「核销」→ 外包商 / 发票号 / 结款三条行都收起（它只跟各自的类型绑），
       状态档位也只有「已核销 / 作废」两档 —— 撤销核销就是把它改作废。 */
    const kbtn = (k) => Array.from(document.querySelectorAll("#finKind button"))
      .find((b) => b.dataset.k === k);
    kbtn("writeoff").click();
    await wait(280);
    pp.woHides = getComputedStyle($("#finVendorRow")).display === "none"
      && getComputedStyle($("#finInvRow")).display === "none"
      && getComputedStyle($("#finPayoffRow")).display === "none";
    pp.woStatus = $("#finStatus").value;
    pp.woStatusList = Array.from($("#finStatus").options).map((o) => o.value);

    /* 换类型 → 结款按钮必须收起来（它只跟「到账」有关），切回来还得在 */
    kbtn("contract").click();
    await wait(250);
    pp.btnHiddenWhenContract = getComputedStyle($("#finPayoffRow")).display === "none";
    kbtn("invoice").click();
    await wait(200);
    pp.btnHiddenWhenInvoice = getComputedStyle($("#finPayoffRow")).display === "none";
    kbtn("payment").click();
    await wait(250);
    pp.btnBackWhenPayment = getComputedStyle($("#finPayoffRow")).display !== "none";

    /* ---- 点「结款（核销待收）」 → 确认弹层 ---- */
    $("#finPayoff").click();
    await wait(900);
    pp.confirmOpen = getComputedStyle($("#ovPayoff")).display !== "none";
    pp.head = (($("#payHead") || {}).textContent || "").trim();
    pp.listRows = $("#payList").querySelectorAll("div").length;
    pp.listText = (($("#payList") || {}).textContent || "").trim().slice(0, 140);
    pp.foot = (($("#payFoot") || {}).textContent || "").trim();

    /* ---- 点「确认结款」—— 到这一步才真的写库 ---- */
    $("#payOk").click();
    await wait(1400);
    pp.confirmClosed = getComputedStyle($("#ovPayoff")).display === "none";
    pp.formClosed = getComputedStyle($("#ovFin")).display === "none";

    const fd = await call("finance_data", 0, 0, "CNY");
    const pr = ((fd && fd.projects) || []).find((x) => x.project_id === pid) || {};
    pp.contractAfter = pr.contract;
    pp.receivedAfter = pr.received;
    pp.pendingAfter = pr.pending;
    pp.writeoffAfter = pr.writeoff;
    pp.settled = pr.settled === true;
    const pre = ((fd && fd.records) || []).filter((r) => r.project_id === pid);
    pp.recCount = pre.length;
    pp.contractRecs = pre.filter((r) => r.kind === "contract").length;
    pp.woRecs = pre.filter((r) => r.kind === "writeoff")
      .map((r) => [r.amount, r.status, r.note]);
    pp.errNew = window.__err === errBefore ? null : window.__err;

    /* ---- 幂等：再点一次「结款」→ 必须明说"已经结清了"，且一个字节都不改 ---- */
    window.alert = (m) => { window.__alertPo = String(m); };
    window.__alertPo = "";
    openFinDialog(null, pid);
    await wait(400);
    $("#finPayoff").click();
    await wait(900);
    pp.againAlert = window.__alertPo;
    /* 没待收可核销就别开确认弹层（开了让人点完发现啥也没发生最恼人） */
    pp.againNoDlg = getComputedStyle($("#ovPayoff")).display === "none";
    const pre2 = ((await call("finance_data", 0, 0, "CNY")).records || [])
      .filter((r) => r.project_id === pid);
    pp.againSameCount = pre2.length === pp.recCount;
    window.alert = realAlert;
    modal("#ovFin", false);
    await wait(200);

    /* ---- 建项目弹窗那条通道也得验（绑定是分开写的，只测一边就是漏） ---- */
    modal("#ovFin", false);
    openNewDialog();
    await wait(350);
    fire("#newAmount", "100000");
    fire("#newTax", "6");
    await wait(150);
    pp.newNetText = (($("#newNet") || {}).textContent || "").trim();
    /* 新建项目页那行结构完全一样，一起钉（2026-10-13） */
    pp.newRowLines = rowMaxLines($("#newNet").closest(".fld"));
    fire("#newAmount", "1234567.89");
    fire("#newTax", "13");
    await wait(150);
    pp.newNetTextBig = (($("#newNet") || {}).textContent || "").trim();
    pp.newRowLinesBig = rowMaxLines($("#newNet").closest(".fld"));
    pp.newRow = (() => {
      const f = $("#newNet").closest(".fld");
      return { h: f.offsetHeight, slack: rowSlack(f), boxW: $("#ovNew .ovbox").clientWidth };
    })();
    modal("#ovNew", false);
    await wait(200);

    /* 收拾干净：样本的款项 + 项目全删掉，别影响后面的 phase */
    const recs = ((fd && fd.records) || []).filter((r) => r.project_id === pid);
    for (const r of recs) await call("finance_delete", r.id);
    await call("delete", "project", pid);
    await reload();
    await wait(300);
    const fd2 = await call("finance_data", 0, 0, "CNY");
    pp.gone = !(((fd2 && fd2.projects) || []).some((x) => x.project_id === pid));

    pp.ok = pp.made && pp.dlgOpen && pp.prjOk
      && pp.kinds.indexOf("writeoff") >= 0
      && pp.woHides && pp.woStatus === "已核销"
      && pp.woStatusList.join() === "已核销,作废"
      && pp.visWhenPayment && pp.btnHiddenWhenContract && pp.btnHiddenWhenInvoice
      && pp.btnBackWhenPayment
      && pp.confirmOpen && pp.listRows === 2
      /* 样本里只有 30000 是「已收」（另 5000 是待收，不该计入）。
         要是把待收也算进来就会变成 35000 —— 这条同时钉住了"待收不算"。 */
      && pp.receivedAfter === 30000 && pp.pendingAfter === 0
      /* 2026-10-16 改口径的两条命门：合同额一条没动（还是 100000、还是那 1 条），
         凭空多出一条核销 = 70000（100000 − 30000）。旧口径下这里会是
         contractAfter === 30000 且没有核销记录 —— 正好把改动钉死。 */
      && pp.contractAfter === 100000 && pp.contractRecs === 1
      && pp.writeoffAfter === 70000 && pp.woRecs.length === 1
      && pp.woRecs[0][0] === 70000 && pp.woRecs[0][1] === "已核销"
      && pp.woRecs[0][2] === "结款核销"
      && pp.recCount === 4                       // 合同 1 + 到账 2 + 核销 1
      && pp.settled === true
      /* 幂等：再点一次只说"已经结清了"，不开确认弹层、不加记录 */
      && /已经结清/.test(pp.againAlert) && pp.againNoDlg && pp.againSameCount
      && pp.confirmClosed && pp.formClosed && !pp.errNew && pp.gone;
    rep.payoff = pp;
  });

  /* 逾期文案三条（2026-10-13 用户定的口径）：
       ① 筹备   → 不算停滞
       ② 已交付 → 不算停滞，改报「结款逾期 N 天」。起算日是**用户手填的结款日期**
                  （settle_date）—— 没填就不报。上一版拿交付日期（delivered_at）
                  反推，2026-10-13 第二轮被用户否掉：那会凭空冒出一个跟合同无关的"逾期"。
       ③ 其余   → 「制作逾期 N 天」（原先叫「逾期 N 天」）
     样本建在**临时项目**上、跑完删干净，不碰冒烟主数据。
     ⚠ 必须真写库再重新 load（load → 行渲染）—— 只改前端内存里的数字断言渲染，
        测的是拼字符串、不是这条通路（2026-10-10 那个删除按钮就这么假绿过去的）。
     ⚠ ①「不显示停滞」和 staleB/C 那两条是**弱断言**：新建项目刚 _touch 过，
        本来就不会停滞。能钉住口径的强断言在数据层 tests/smoke_db.py
        （那边能把 last_activity_at 拨到 2020 年）。 */
  await phase("settle", 25000, async () => {
    await step("settle");
    const sv = {};
    const errBefore = window.__err;
    const ago = (k) => isoOf(new Date(Date.now() - k * 86400000));
    const rowOf = (pid) => document.querySelector(
      '#tree .row[data-kind="project"][data-id="' + pid + '"]');
    const dueOf = (pid) => {
      const r = rowOf(pid);
      return r ? ((r.querySelector(".due") || {}).textContent || "") : null;
    };
    const staleOf = (pid) => {
      const r = rowOf(pid);
      return r ? !!r.querySelector(".stale") : null;
    };

    /* ① 筹备 */
    const pB = (await call("add_project", "逾期冒烟B", "commercial", 0, "", {})).id;
    await call("set_status", "project", pB, "筹备");
    /* ② 进行中 + 截止日过了 5 天 */
    const pA = (await call("add_project", "逾期冒烟A", "commercial", 0, "", {})).id;
    await call("set_field", "project", pA, "deadline", ago(5));
    /* ③ 已交付：截止日故意拨到 30 天前 —— 证明已交付**不**报制作逾期 */
    const pC = (await call("add_project", "逾期冒烟C", "commercial", 0, "", {})).id;
    await call("set_field", "project", pC, "deadline", ago(30));
    await call("set_status", "project", pC, "已交付");

    /* 没填结款日期 → 一颗红字都不该有（这就是"不拿交付日期推算"的现场证据：
       这项目 30 天前"交付"过、截止日也早炸了，照样什么都不报） */
    await reload();
    await wait(500);
    sv.noSettleDate = dueOf(pC);
    sv.rowP = rowOf(pC) ? 1 : 0;

    /* 填上 12 天前的结款日期 → 才报「结款逾期 12 天」 */
    await call("set_field", "project", pC, "settle_date", ago(12));
    await reload();
    await wait(500);
    const nowP = D.groups.reduce((acc, g) => acc.concat(g.projects), []);
    const rowC = nowP.find((p) => p.id === pC) || {};
    sv.settleDate = rowC.settle_date || "";
    sv.oldFieldGone = !("delivered_at" in rowC);   // 这个字段前端已经收不到了
    sv.dueA = dueOf(pA);                        // 「制作逾期 5 天」
    sv.dueC = dueOf(pC);                        // 「结款逾期 12 天」
    sv.staleB = staleOf(pB);
    sv.staleC = staleOf(pC);
    /* 底栏那个数要把结款逾期算进去，否则行上报红、底栏还是 0 */
    sv.statsDue = (D.stats || {}).due;

    /* 告警面板：结款那条带 settle 标记，制作那条不带 */
    const al = await call("alerts");
    const cD = ((al && al.due) || []).filter((x) => x.owner === "project" && x.id === pC)[0] || {};
    const aD = ((al && al.due) || []).filter((x) => x.owner === "project" && x.id === pA)[0] || {};
    sv.alertsSettle = cD.settle === true && cD.detail === "结款逾期 12 天";
    sv.alertsNormal = aD.settle === false && aD.detail === "制作逾期 5 天";
    sv.inStaleList = ((al && al.stale) || [])
      .some((x) => x.project_id === pB || x.project_id === pC);

    /* 收拾干净：三个样本全删，别影响后面的 phase */
    for (const pid of [pA, pB, pC]) await call("delete", "project", pid);
    await reload();
    await wait(300);
    const left = (await call("load", 1)).groups.reduce((acc, g) => acc.concat(g.projects), []);
    sv.gone = !left.some((p) => [pA, pB, pC].indexOf(p.id) >= 0);
    sv.errNew = window.__err === errBefore ? null : window.__err;

    sv.ok = sv.noSettleDate === "" && sv.rowP === 1
      && sv.settleDate === ago(12) && sv.oldFieldGone
      && sv.dueA === "制作逾期 5 天"
      && sv.dueC === "结款逾期 12 天"
      && sv.staleB === false && sv.staleC === false
      && sv.statsDue >= 2
      && sv.alertsSettle && sv.alertsNormal && !sv.inStaleList
      && sv.gone && !sv.errNew;
    rep.settle = sv;
  });

  /* 项目设置页（2026-10-13 新增）：⋯ 菜单里原来散着「编辑截止日期 / 改客户 /
     改分类」，现在合成一页（版面照抄新建项目）。这一段真走点击路径：
       ⋯ → 断旧入口没了、新入口在 → 点「项目设置…」→ 改 6 个字段 → 点「保存」→ 断落库。
     ⚠ 必须**点按钮**，不能直调 call("set_field")：否则"菜单项没接上 / 弹窗没打开 /
        保存没绑事件"这类问题全靠肉眼（2026-10-10 那个删除按钮就是这么假绿过去的）。
     ⚠ 客户只用**已有的种子客户**，不新建 —— create phase 里钉着客户计数（1 → 2），
        这里多造一个客户会让那条断言莫名其妙地红。
     ⚠ 样本项目跑完全删，不给冒烟主数据留痕。 */
  await phase("prjset", 25000, async () => {
    await step("prjset");
    const pv = {};
    const errBefore = window.__err;
    const fut = isoOf(new Date(Date.now() + 30 * 86400000));
    const past = isoOf(new Date(Date.now() - 7 * 86400000));
    const pid = (await call("add_project", "设置页冒烟P", "commercial", 0, "", {})).id;
    const seed = (D.clients || [])[0] || {};
    /* 分类要换一个**别的**筐（库里只有一个分类时就跳过这一项，别把断言做虚） */
    const gs = D.groups || [];
    const other = gs.filter((g) => g.key !== "commercial")[0] || null;
    const rowOf_ = (id) => {
      const r = document.querySelector(
        '#tree .row[data-kind="project"][data-id="' + id + '"]');
      return r ? ((r.querySelector(".title") || {}).textContent || "") : null;
    };
    await reload();
    await wait(400);

    const row = document.querySelector(
      '#tree .row[data-kind="project"][data-id="' + pid + '"]');
    pv.rowOk = !!row;
    /* ① 菜单：项目设置… 在，旧的三条没了（编辑截止日期 / 改客户 / 改分类） */
    row.querySelector(".mini.more").click();
    await wait(250);
    const pop = document.querySelector(".popup");
    const labels = pop ? Array.from(pop.children).map((d) => d.textContent || "") : [];
    pv.menu = labels;
    pv.hasSet = labels.some((x) => x.indexOf("项目设置") === 0);
    pv.oldGone = !labels.some((x) => x.indexOf("编辑截止日期") === 0
      || x.indexOf("改客户") === 0 || x.indexOf("改分类") === 0);
    pv.keepNote = labels.some((x) => x.indexOf("编辑备注") === 0);
    /* ② 点「项目设置…」→ 弹窗要开，且预填的是这个项目现在的值 */
    const item = pop ? Array.from(pop.children)
      .find((d) => (d.textContent || "").indexOf("项目设置") === 0) : null;
    pv.itemHit = !!item;
    if (item) { item.click(); await wait(400); }
    pv.dlgOpen = getComputedStyle($("#ovPrjSet")).display !== "none";
    pv.preTitle = $("#psTitle").value;
    pv.prePath = $("#psPath").value;
    pv.preSettle = $("#psSettle").value;
    pv.catBtns = Array.from(document.querySelectorAll("#psSegCat button")).map((b) => b.dataset.c);
    pv.catAct = (document.querySelector("#psSegCat button.act") || { dataset: {} }).dataset.c || "";
    /* 制作人（2026-10-16 起是外包商名录的下拉）：预填必须是**名字**，
       候选里得真有那几条（#vendorOpts 是文档级 datalist，两处共用）。
       预填成 id 或者候选空着，界面上都看不出来 —— 只有真读一次才露馅。 */
    pv.artistPre = $("#psArtist").value;
    pv.artistList = ($("#psArtist").getAttribute("list") || "");
    pv.artistOpts = Array.from(document.querySelectorAll("#vendorOpts option"))
      .map((o) => o.value);
    pv.vendorsInData = (D.vendors || []).map((v) => v.name);

    /* ③ 改一整套字段（都走界面输入框，不碰 call()） */
    $("#psTitle").value = "设置页冒烟P改";
    $("#psDl").value = fut;
    $("#psSettle").value = past;
    $("#psClient").value = seed.name || "";
    $("#psPath").value = "/冒烟/设置页目录";
    /* 制作人填一个**名录里没有的**新名字 —— 走的是 ensureVendor 现建现挂那条路。
       ⚠ 这条是本次改动最容易假绿的地方：填了名字存下去，"界面看着有名字"，
       但那可能是前端自己拼的、库里其实还是个空 id。所以断言要落到 load() 的
       artist + artist_id 两个键上。 */
    const newVn = "设置页冒烟V";
    const vBefore = (D.vendors || []).map((v) => v.name);
    $("#psArtist").value = newVn;
    const wantCat = other ? other.key : "commercial";
    pv.wantCat = wantCat;
    pv.seedClient = seed.name || null;
    const cb = document.querySelector('#psSegCat button[data-c="' + wantCat + '"]');
    pv.catClicked = !!cb;
    if (cb) cb.click();
    $("#psOk").click();
    await wait(1800);

    pv.dlgClosed = getComputedStyle($("#ovPrjSet")).display === "none";
    const after = (await call("load", 1)).groups
      .reduce((acc, g) => acc.concat(g.projects), []);
    const p = after.find((x) => x.id === pid) || {};
    pv.title = p.title || null;
    pv.dl = p.deadline || null;
    pv.settle = p.settle_date || null;
    pv.client = p.client || null;
    pv.cat = p.category || null;
    const lk = (p.links || []).filter((l) => l.label === "项目根目录");
    pv.path = lk.length ? lk[0].path : null;
    pv.links = (p.links || []).length;
    /* 制作人：① 名字从 vendors JOIN 出来 ② artist_id 真落库了 ③ 条目是**现建**的
       （列表明明没有这个名字，存完却在库里 —— 说明 ensureVendor 那段真跑了）。 */
    pv.artist = p.artist || null;
    pv.artistId = p.artist_id || 0;
    const vAfter = ((await call("load", 1)).vendors || []);
    pv.vendorMade = vAfter.some((v) => v.name === newVn) && !vBefore.includes(newVn);
    pv.vendorMadeId = (vAfter.find((v) => v.name === newVn) || {}).id || 0;
    pv.artistLinked = !!pv.artistId && pv.artistId === pv.vendorMadeId;
    /* 行上也要跟着变（改了标题却还画着旧名 = 保存后没重画） */
    pv.rowTitle = rowOf_(pid);

    /* ④ 撤销路径：把结款日期 / 制作人清空，看它们能不能真的清掉（不是"改了不动"） */
    openProjSet(p);
    await wait(300);
    pv.artistPre2 = $("#psArtist").value;      // 重开时得把那名字带回来
    $("#psSettle").value = "";
    $("#psArtist").value = "";
    $("#psOk").click();
    await wait(1600);
    const after2 = (await call("load", 1)).groups
      .reduce((acc, g) => acc.concat(g.projects), []);
    const p2 = after2.find((x) => x.id === pid) || {};
    pv.settleCleared = !(p2.settle_date || "");
    pv.artistCleared = !(p2.artist_id || 0) && !(p2.artist || "");
    /* 清掉挂靠 ≠ 删条目：名录里那个人得还在（不然"清空制作人"会顺手删掉别人的档案） */
    pv.vendorKept = ((await call("load", 1)).vendors || [])
      .some((v) => v.name === newVn);

    /* 收拾 */
    modal("#ovPrjSet", false);
    await call("delete", "project", pid);
    await reload();
    await wait(300);
    const left = (await call("load", 1)).groups.reduce((acc, g) => acc.concat(g.projects), []);
    pv.gone = !left.some((x) => x.id === pid);
    /* 那个现建的条目也要带走，别给后面的阶段留一个游离的外包商 */
    if (pv.vendorMadeId) { await call("vendor_delete", pv.vendorMadeId); }
    pv.vendorGone = !((await call("load", 1)).vendors || [])
      .some((v) => v.name === newVn);
    pv.errNew = window.__err === errBefore ? null : window.__err;

    pv.ok = pv.rowOk && pv.hasSet && pv.oldGone && pv.keepNote && pv.itemHit
      && pv.dlgOpen && pv.preTitle === "设置页冒烟P" && pv.preSettle === ""
      && pv.catAct === "commercial" && pv.prePath === ""
      && pv.catClicked && pv.dlgClosed
      && pv.title === "设置页冒烟P改" && pv.dl === fut && pv.settle === past
      && pv.client === (seed.name || null) && pv.cat === wantCat
      && pv.path === "/冒烟/设置页目录" && pv.links === 1
      && pv.rowTitle === "设置页冒烟P改"
      && pv.artistPre === "" && pv.artistList === "vendorOpts"
      && pv.artistOpts.indexOf("冒烟制作人") >= 0
      && pv.artist === newVn && pv.artistLinked && pv.vendorMade
      && pv.artistPre2 === newVn
      && pv.settleCleared && pv.artistCleared && pv.vendorKept
      && pv.gone && pv.vendorGone && !pv.errNew;
    rep.prjset = pv;
  });

  /* 导出选项弹窗（#ovExport）：格式 / 时间范围 / 项目 / 内容 + 文件名预览。
     ⚠ 只验界面，**绝不点「导出…」（#exOk）** —— 它会弹系统模态对话框，
     无人值守时整轮冒烟会一直卡在等点击。要验那条路只能另开线程去关窗口
     （tests/_diag/smoke_export_save.py）。 */
  await phase("export", 15000, async () => {
    await step("export");
    openExport();
    await wait(600);

    const rep0 = $("#exPrev").dataset || {};
    const out = {
      dlg: getComputedStyle($("#ovExport")).display !== "none",
      fmtBtns: Array.from(document.querySelectorAll("#exFmt button")).map((b) => b.dataset.f),
      fmtAct: (document.querySelector("#exFmt button.act") || { dataset: {} }).dataset.f || "",
      secs: Array.from(document.querySelectorAll("#exSecs input")).map((el) => el.dataset.s),
      secsOn: Array.from(document.querySelectorAll("#exSecs input"))
        .filter((el) => el.checked).map((el) => el.dataset.s),
      rangeOpts: Array.from(document.querySelectorAll("#exRange option")).map((o) => o.textContent),
      /* 默认「同当前筛选」得把年份写出来，不能只有"当前"两个字 */
      curLabel: (document.querySelector('#exRange option[value="cur"]') || {}).textContent || "",
      planName: rep0.name || "",
      planCount: rep0.count || "",
      prjClosed: getComputedStyle($("#exPrjBox")).display === "none",
      prjN: n("#exPrjList input"),
    };

    /* 自定义范围：两个日期框才出现 */
    $("#exRange").value = "custom";
    $("#exRange").dispatchEvent(new Event("change"));
    await wait(300);
    out.datesShown = getComputedStyle($("#exFrom")).display !== "none" &&
                     getComputedStyle($("#exTo")).display !== "none";

    /* 指定项目：复选列表展开、默认全勾 */
    $("#exPrj").value = "1";
    $("#exPrj").dispatchEvent(new Event("change"));
    await wait(300);
    out.prjShown = getComputedStyle($("#exPrjBox")).display !== "none";
    out.prjOn = Array.from(document.querySelectorAll("#exPrjList input"))
      .filter((el) => el.checked).length;

    /* 切 CSV：内容固定成「款项明细」，其余三项灰掉（后端也会这么兜） */
    document.querySelector('#exFmt button[data-f="csv"]').click();
    await wait(600);
    out.csvOn = Array.from(document.querySelectorAll("#exSecs input"))
      .filter((el) => el.checked).map((el) => el.dataset.s);
    out.csvOff = Array.from(document.querySelectorAll("#exSecs input"))
      .filter((el) => el.disabled).length;
    out.csvName = ($("#exPrev").dataset || {}).name || "";

    modal("#ovExport", false);
    await wait(200);
    out.closed = getComputedStyle($("#ovExport")).display === "none";
    rep.export = out;
  });

  /* 打卡 = 重置「今日必做」（2026-10-09 用户口径）。
     只验**界面表现**，真重置走数据层断言（tests/smoke_db.py）。
     ⚠ 绝不点 #checkOk：会真写库、还会把 remind 的打卡状态改掉。 */
  await phase("checkin", 10000, async () => {
    await step("checkin");
    const out = {};

    /* 弹窗里的候选项目 + 默认勾选 */
    const readList = () => Array.from(document.querySelectorAll("#checkList input"))
      .map((i) => ({
        id: i.dataset.id,
        checked: i.checked,
        title: (i.parentElement.querySelector("span") || {}).textContent || "",
      }));

    /* 先手动给一个项目挂上「今日必做」，看它是不是"残留态"*/
    const first = Array.from(document.querySelectorAll("#tree .row"))
      .find((r) => r.dataset.kind === "project");
    out.hasProj = !!first;
    if (first) {
      const pid = first.dataset.id;
      await call("set_field", "project", pid, "priority", 1);
      await reload();
      await wait(400);
      const row = document.querySelector('#tree .row[data-kind="project"][data-id="' + pid + '"]');
      out.badgeBefore = !!(row && row.querySelector(".badge:not(.st):not(.arch)"));
      out.statsToday = (D.stats || {}).today;

      openCheck(false);
      await wait(600);
      out.dlgOpen = getComputedStyle($("#ovCheck")).display !== "none";
      const items = readList();
      out.n = items.length;
      const mine = items.find((x) => x.id === String(pid));
      out.defaultChecked = !!(mine && mine.checked);   // priority=1 → 默认勾上
      out.checkedCount = items.filter((x) => x.checked).length;

      modal("#ovCheck", false);
      await wait(200);
      out.dlgClosed = getComputedStyle($("#ovCheck")).display === "none";

      /* 收尾：把这个手动的标记清掉，别让后续 phase 看到脏状态 */
      await call("set_field", "project", pid, "priority", 0);
      await reload();
      await wait(300);
    }
    rep.checkin = out;
  });

  /* 真走「⋯ → 删除」这条点击路径（2026-10-10 补）
     ---------------------------------------------------------------
     教训：以前这里只测 `call("delete", "node", id)` —— 直调后端 API，
     把 `⋯` 菜单、confirm、handler 全绕过去了。于是「删除项引用了不存在
     的变量、点下去抛 ReferenceError」这种 bug，255 条断言一路全绿。
     用户实测点不动，代码里却查不出任何红灯 —— 典型的假绿。

     所以这里必须是**真点击**：找行 → 点 .mini.more → 在弹层里按文案找
     「删除」→ 断言它是 danger（红）→ .click() → 断言 confirm 被问过、
     后端真的删掉、且 window.__err 里没有异常。
     样本用自己临时建的环节（不碰冒烟项目的主数据），末尾也会清掉。 */
  const deldlg = { checked: false };
  await phase("deldlg", 15000, async () => {
    await step("deldlg");
    setView("tree");
    await wait(300);
    await call("collapse_all", "expand");
    await reload();
    await wait(300);

    const pid = ((D.groups.find((g) => g.key === "commercial") || {}).projects || [])
      .map((p) => p.id)[0];
    const prjRow = document.querySelector('#tree .row.prj[data-id="' + pid + '"]');
    if (!prjRow) { deldlg.err = "找不到样本项目行"; return; }

    /* 在它下面挂一个一次性的环节，待会儿就拿它开刀 */
    const mk = await call("add_node", pid, null, "删除冒烟样本");
    await reload();
    await wait(300);
    const victimId = mk && mk.id;
    if (!victimId) { deldlg.err = "样本环节没建出来"; return; }
    deldlg.made = true;

    const vrow = document.querySelector('#tree .row[data-id="' + victimId + '"]');
    deldlg.rowFound = !!vrow;
    if (!vrow) { deldlg.err = "样本环节行没渲染出来"; return; }

    /* confirm 换成替身：原生弹窗会卡住 JS 线程，收尾的 boot_ok 就发不出去
       （diag.js 里删除客户那段的既有做法，照抄） */
    const realConfirm = window.confirm, realErr = window.__err;
    let asked = 0, askedMsg = "";
    window.confirm = (m) => { asked++; askedMsg = String(m || ""); return true; };
    try {
      vrow.querySelector(".mini.more").click();
      await wait(200);
      const p = document.querySelector(".popup");
      deldlg.popupOpen = !!p;
      const items = p ? Array.from(p.querySelectorAll("div")) : [];
      const del = items.find((d) => (d.textContent || "").trim() === "删除");
      deldlg.delFound = !!del;
      if (!del) { deldlg.labels = items.map((d) => (d.textContent || "").trim()); return; }

      /* 颜色：danger → 红。dim（灰）是"别指望点它"的意思，用在删除上是误导。
         不猜色值：直接读 CSS 变量（--red / --tx3）拿真值，
         拿不到变量才退回"红分量明显高于绿蓝"的粗判。主题有明暗两套，
         写死 #e05c5c / #c0392b 会在换主题那天变成假红。 */
      const cssVar = (k) => getComputedStyle(document.documentElement)
        .getPropertyValue(k).trim();
      const rgbOf = (el) => getComputedStyle(el).color;
      const toRGB = (c) => {
        const m = String(c).match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const p = m[1].split(",").map((x) => parseFloat(x));
        return p.length >= 3 ? p : null;
      };
      /* 变量值可能是 hex，也可能已经是 rgb() —— 借一个元素让浏览器算 */
      const varRGB = (k) => {
        const v = cssVar(k);
        if (!v) return null;
        const probe = document.createElement("div");
        probe.style.color = v;
        document.body.appendChild(probe);
        const out = toRGB(rgbOf(probe));
        probe.remove();
        return out;
      };
      deldlg.delClass = del.className;
      deldlg.delColor = rgbOf(del);
      const wantRed = varRGB("--red"), grayTx3 = varRGB("--tx3");
      deldlg.wantRed = wantRed ? "rgb(" + wantRed.join(", ") + ")" : "";
      deldlg.dimGray = grayTx3 ? "rgb(" + grayTx3.join(", ") + ")" : "";
      const got = toRGB(deldlg.delColor);
      deldlg.isRed = !!got && !!wantRed && got.slice(0, 3).join(",") === wantRed.slice(0, 3).join(",");
      deldlg.isGray = !!got && !!grayTx3 && got.slice(0, 3).join(",") === grayTx3.slice(0, 3).join(",");

      del.click();
      await wait(400);
      deldlg.asked = asked;
      deldlg.askedMsg = askedMsg;
      /* 异常有没有被 popup() 的兜底吞成 alert 之外的静默失败 —— 查 __err */
      deldlg.errAfterClick = window.__err || null;

      const gone = ((await call("load")).groups
        .find((g) => g.key === "commercial") || {}).projects || [];
      const find = (arr) => {
        for (const pp of arr) {
          if ((pp.children || []).some((c) => String(c.id) === String(victimId))) return true;
        }
        return false;
      };
      deldlg.stillThere = find(gone);
    } finally {
      window.confirm = realConfirm;
      window.__err = realErr;   // 别把自己触发的假错留在全局
    }
    deldlg.checked = deldlg.asked > 0 && deldlg.stillThere === false;
  });
  rep.deldlg = deldlg;

  /* L3 全屏遮挡能否拉起来 */
  await phase("block", 8000, async () => {
    await step("block");
    if (window.__openCheckin) window.__openCheckin(3);
    await wait(300);
    rep.block = getComputedStyle($("#ovBlock")).display;
    $("#ovBlock").style.display = "none";
  });

  /* M4 打磨：拖拽排序 / 快捷键 / 快照 / 进度条 */
  await phase("m4", 25000, async () => {
    await step("m4");
    setView("tree");
    await wait(400);
    /* 默认是折着的，拖拽要挑行、得先把树撑开 */
    await call("collapse_all", "expand");
    await reload();
    await wait(300);

    /* 行必须是可拖的（分组标题行不可拖） */
    const draggable = n('#tree .row[draggable="true"]');

    /* 真调一次 move：把最后一个商业项目拖到第一位，再拖回原位。
       只看"有没有 draggable 属性"证明不了排序真的落库。 */
    const catList = () => (D.groups.find((g) => g.key === "commercial") || {}).projects || [];
    const list = catList().map((p) => p.id);
    let moved = false, restored = false, moveMsg = null;
    if (list.length >= 2) {
      const first = list[0], last = list[list.length - 1];
      const r1 = await call("move", "project", last, first, "before");
      moveMsg = (r1 && r1.msg) || null;
      const g2 = ((await call("load")).groups.find((g) => g.key === "commercial") || {}).projects || [];
      moved = !!(r1 && r1.ok) && g2.length === list.length && g2[0].id === last;
      await call("move", "project", last, first, "after");
      const g3 = ((await call("load")).groups.find((g) => g.key === "commercial") || {}).projects || [];
      restored = g3.map((p) => p.id).join(",") === list.join(",");
    }

    /* 批量折叠要真的落库 */
    await call("collapse_all", "collapse");
    const dc = await call("load");
    const allShut = ((dc.groups.find((g) => g.key === "commercial") || {}).projects || [])
      .every((p) => !!p.collapsed);
    await call("collapse_all", "expand");

    /* 快捷键：Ctrl+2 应该切到活跃页 */
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "2", ctrlKey: true }));
    await wait(500);
    const hotkey = $("#heat").style.display === "block";
    const hotkeyTab = (document.querySelector("#segPage button.act") || { dataset: {} }).dataset.v || null;

    /* 快照：打一份，列表里要能看到它 */
    const sn = await call("snapshot_now");
    const names = ((await call("snapshot_list")).items || []).map((x) => x.name);

    /* 环节的状态菜单：点徽章弹出来的必须是新的制作流转 9 态。
       光读 D.status.node 证明不了界面上真接的是它，所以真点一次、读弹层。 */
    let nodeStatus = null;
    const nrow = document.querySelector("#tree .row:not(.prj):not(.grp)");
    if (nrow) {
      nrow.querySelector(".badge.st").click();
      await wait(200);
      const pop = document.querySelector(".popup");
      nodeStatus = pop ? Array.from(pop.children).map((d) => d.textContent) : null;
      closePopup();
    }

    /* 进度条：完成与否看状态（通过 / 交付），不能只认勾选框 ——
       平时大家是把环节改成「通过」的，早先进度只认 done 标记，
       于是项目行的进度条和完成比例永远是 0。这里真改一条环节再看数字。 */
    const firstLeaf = (ns) => {
      for (const x of ns) {
        if (!(x.children || []).length) return x;
        const r = firstLeaf(x.children);
        if (r) return r;
      }
      return null;
    };
    const pA = ((D.groups.find((g) => g.key === "commercial") || {}).projects || [])
      .find((p) => (p.children || []).length);
    let prog = null;
    if (pA) {
      const lf = firstLeaf(pA.children);
      const before = leaves(pA);
      if (lf) await call("set_status", "node", lf.id, "通过");
      await reload();
      await wait(250);
      const p2 = findProject(pA.id);
      const after = p2 ? leaves(p2) : [];
      let rowEl = null;
      document.querySelectorAll("#tree .row.prj").forEach((el) => {
        const tt = el.querySelector(".title");
        if (tt && tt.textContent === pA.title) rowEl = el;
      });
      const barI = rowEl ? rowEl.querySelector(".bar i") : null;
      prog = {
        before: [before.filter(Boolean).length, before.length],
        after: [after.filter(Boolean).length, after.length],
        barW: barI ? barI.style.width : "",
      };
    }

    /* 项目页筛选菜单：状态筛选 + 归档开关都要**真点一次**。
       归档走的是后端 `load(include_archived)`，所以这里也断言「归档后默认真的看不到」。
       状态筛选拿一条项目临时改成「中止」再筛 —— 种子里全是进行中，不改的话筛了跟没筛一样。 */
    const grpOf = (k) => (D.groups || []).find((g) => g.key === k) || {};
    const personalList = grpOf("personal").projects || [];
    const fallbackList = ((D.groups || [])[0] || {}).projects || [];
    const archTarget = personalList.length ? personalList[0] : fallbackList[0];
    const totalPrj = (D.groups || []).reduce((a, g) => a + g.projects.length, 0);

    const fbtn = $("#treeFilter");
    const filterText0 = fbtn ? fbtn.textContent : null;
    let filterItems = null, afterFilter = null, filterBack = null;
    let archOff = null, archOn = null, archText = null;
    let archStatOff = null, archStatOn = null, archStatFilter = null;
    let pf = null;                        // 多选 / 反选那一段的结果
    if (fbtn && archTarget) {
      const projStats = ((D.status || {}).project || []);
      const statCount = (s) => (D.groups || []).reduce((acc, g) =>
        acc + g.projects.filter((p) => (s === "归档" ? isArchived(p) : p.status === s)).length, 0);
      /* 行上有好几个徽章（今日必做 / 置顶 / 状态 / 已归档），状态那颗带 `.st` */
      const rowStat = (el) => {
        const b = el.querySelector(".badge.st");
        return b && projStats.indexOf(b.textContent) >= 0 ? b.textContent : "";
      };
      const prjRows = () => Array.from(document.querySelectorAll("#tree .row.prj"));
      const popNow = () => document.querySelector(".popup");
      const actBtn = (kw) => {
        const p = popNow();
        const b = p && Array.from(p.querySelectorAll(".acts button"))
          .find((x) => x.textContent === kw);
        if (!b) return false;
        b.click();
        return true;
      };
      /* 菜单里的某个状态项（className 为空的是普通可点项；acts / ttl / sep 都有类） */
      const statItem = (s) => {
        const p = popNow();
        return p && Array.from(p.querySelectorAll("div"))
          .find((d) => !d.className
            && d.textContent.replace(/^[✓\u3000]+/, "") === s);
      };

      await call("set_status", "project", archTarget.id, "中止");
      await reload();
      await wait(200);
      fbtn.click();
      await wait(200);
      const fp = popNow();
      filterItems = fp ? Array.from(fp.children).map((d) => d.textContent.trim()) : null;
      const pick = fp ? Array.from(fp.children)
        .find((d) => d.textContent.indexOf("中止") >= 0) : null;
      if (pick) pick.click();
      await wait(400);   // 切状态现在要过一次 reload（跨桥），250ms 不够稳
      const rowsNow = prjRows();
      afterFilter = {
        stats: (ui.pStatSel || []).slice(),
        rows: rowsNow.length,
        title: rowsNow.length ? (rowsNow[0].querySelector(".title") || {}).textContent : null,
        text: fbtn.textContent,
        /* 多选菜单点了不关：这颗状态项点完弹层还在 */
        stayedOpen: !!popNow(),
      };

      /* ---- 多选 / 反选（2026-09-30）----
         动作是横排按钮（**只有 反选 / 清空**，没有「全选」——
         空集合就等于不筛 = 全选），状态项一个个勾。 */
      pf = {};
      pf.actLabels = (() => {
        const p = popNow();
        return p ? Array.from(p.querySelectorAll(".acts button")).map((b) => b.textContent) : null;
      })();
      pf.hasTitle = !!document.querySelector(".popup .ttl");
      pf.noAllBtn = !actBtn("全选");

      /* ① 清空 → 没有筛选，项目全回来 */
      pf.clrClicked = actBtn("清空");
      await wait(250);
      pf.selAfterClear = (ui.pStatSel || []).slice();
      pf.rowsCleared = prjRows().length;

      /* ② 多选：连勾两个状态（每点一下菜单都不关、就地重开）。
         第二个故意选一个**没有项目的状态** —— 不然筛出来的行数跟不筛一样，
         看不出筛选到底干没干活（"归档"排除掉：勾它会顺带触发一次带归档的 reload） */
      const present = projStats.filter((s) => statCount(s) > 0);
      const absent = projStats.filter((s) => statCount(s) === 0 && s !== "归档");
      const picks = Array.from(new Set([present[0], absent[0] || present[1]]
        .filter(Boolean)));
      pf.picks = picks;
      pf.menuAlive = [];
      picks.forEach((s) => {
        const it = statItem(s);
        if (it) it.click();
        pf.menuAlive.push(!!popNow());
      });
      await wait(400);
      pf.sel = (ui.pStatSel || []).slice();
      const rowsMulti = prjRows();
      pf.rows = rowsMulti.length;
      pf.expectRows = picks.reduce((a, s) => a + statCount(s), 0);
      pf.onlyPicked = rowsMulti.length > 0
        && rowsMulti.every((el) => picks.indexOf(rowStat(el)) >= 0);

      /* ③ 反选：选中集合应当正好是补集 */
      pf.invClicked = actBtn("反选");
      await wait(400);
      pf.invSel = (ui.pStatSel || []).slice();
      pf.invOk = pf.invSel.length === projStats.length - picks.length
        && picks.every((s) => pf.invSel.indexOf(s) < 0);

      /* ④ 不变量：反选两次 = 回到原来那组（没有全选按钮，反选是唯一的"取补"入口） */
      pf.invClicked2 = actBtn("反选");
      await wait(400);
      pf.invBack = (ui.pStatSel || []).slice();
      pf.invBackOk = pf.invBack.length === picks.length
        && picks.every((s) => pf.invBack.indexOf(s) >= 0);

      /* 复位：清空（并把可能带回来的归档数据 reload 掉） */
      actBtn("清空");
      await wait(300);
      await call("set_status", "project", archTarget.id, "进行中");
      await reload();
      await wait(250);
      filterBack = { rows: n("#tree .row.prj"), total: totalPrj, text: fbtn.textContent };
      pf.textBack = fbtn.textContent;
    }

    if (archTarget) {
      await call("set_field", "project", archTarget.id, "archived", 1);
      ui.hideArch = 1; saveUI();          // 默认档：隐藏归档 → 看不到
      await reload();
      await wait(200);
      archOff = !findProject(archTarget.id);
      ui.hideArch = 0; saveUI();          // 关掉隐藏 → 归档项目带徽章回来
      await reload();
      await wait(250);
      const row = document.querySelector("#tree .row.prj.arch");
      archOn = !!findProject(archTarget.id) && !!row &&
        !!row.querySelector(".badge.arch");
      archText = fbtn ? fbtn.textContent : null;
      /* 收尾：别把种子里这条留在归档态，后面财务页还要用 */
      await call("set_field", "project", archTarget.id, "archived", 0);
      ui.hideArch = 1; saveUI();
      await reload();
      await wait(200);
    }

    /* 归档的**另一条路**：直接把状态改成「归档」。用户就是这么干的 ——
       真实库里那两条归档项目状态都是「归档」而 archived 字段还是 0，
       开关只看字段的话点了毫无反应。这条必须单独测。 */
    if (archTarget) {
      await call("set_status", "project", archTarget.id, "归档");
      ui.hideArch = 1; saveUI();
      await reload();
      await wait(200);
      archStatOff = !findProject(archTarget.id);
      ui.hideArch = 0; saveUI();
      await reload();
      await wait(250);
      archStatOn = !!findProject(archTarget.id);
      /* 筛「归档」状态时，即使默认隐藏也得看得到 —— 否则点了「归档」出来一棵空树 */
      ui.hideArch = 1; saveUI();
      ui.pStatSel = ["归档"]; saveUI();
      await reload();
      await wait(250);
      archStatFilter = !!findProject(archTarget.id);
      /* 收尾 */
      ui.pStatSel = []; saveUI();
      await call("set_status", "project", archTarget.id, "进行中");
      ui.hideArch = 1; saveUI();
      await reload();
      await wait(200);
    }

    rep.m4 = {
      bar: n("#treeBar button"),
      filterText0: filterText0,
      hideArchDefault: ui.hideArch,
      filterExpect: archTarget ? archTarget.title : null,
      filterItems: filterItems,
      afterFilter: afterFilter,
      projFilter: pf,
      filterBack: filterBack,
      archOff: archOff,
      archOn: archOn,
      archText: archText,
      archStatOff: archStatOff,
      archStatOn: archStatOn,
      archStatFilter: archStatFilter,
      draggable: draggable,
      projects: list.length,
      moved: moved,
      moveMsg: moveMsg,
      restored: restored,
      allShut: allShut,
      hotkey: hotkey,
      hotkeyTab: hotkeyTab,
      snapOk: !!(sn && sn.ok),
      snapInList: names.indexOf(sn && sn.name) >= 0,
      snapList: names.length,
      nodeStatus: nodeStatus,
      prog: prog,
    };
    setView("tree");
  });

  /* 本轮四项修复的回归点。**必须真的点一遍**：只断言「页面渲染出来了」的冒烟
     会一路绿灯而功能是坏的（打包后尤其容易漏 —— 新加的 API 没被真调过）。 */
  await phase("fix", 25000, async () => {
    await step("fix");
    setView("tree");
    await wait(400);

    /* --- #1 置顶：问得到真实状态，且 set_top 返回的就是真实结果 --- */
    const t0 = await call("get_top");
    const r1 = await call("set_top", true);
    const t1 = await call("get_top");
    const r2 = await call("set_top", false);
    const t2 = await call("get_top");
    /* 再真点一次工具栏按钮：按钮亮灭必须跟窗口一致（老毛病就是两边反着来） */
    $("#btnTop").click();
    await wait(600);
    const real = await call("get_top");
    const btnOn = $("#btnTop").classList.contains("on");
    await call("set_top", !!t0.top);            // 还原到进来时那样
    await wait(200);
    /* 提醒巡检（remind.py）启动 60s 后首次巡检会 do_front() → set_top(True)，
       正好可能落在这几步上，表现为「点了关其实又开着」的假失败。
       冒烟 seed 里已经关掉提醒（notify_enabled=0 / remind_level=1），
       所以这里补一次带等待的确认，避免启动初期那一次巡检撞上来。 */
    let offReal = !(r2 && r2.top) && !t2.top;
    if (!offReal) {
      await wait(1200);
      const r2b = await call("set_top", false);
      const t2b = await call("get_top");
      offReal = !(r2b && r2b.top) && !t2b.top;
      rep.fixTopRetry = true;
      await call("set_top", !!t0.top);
    }
    rep.fix = {
      top: {
        queried: !!(t0 && t0.ok),
        onReal: !!(r1 && r1.top) && !!t1.top,   // 点了开 → 窗口真的开着
        offReal: offReal,                       // 点了关 → 窗口真的关了
        btnSync: btnOn === !!real.top,          // 按钮亮灭 == 窗口真实状态
      },
    };

    /* --- #2 客户显示开关：点一次，项目行上的客户名要真的消失 --- */
    const clientOf = () => {
      let hit = 0;
      document.querySelectorAll("#tree .row.prj").forEach((el) => {
        const ms = Array.from(el.querySelectorAll(".meta")).map((m) => m.textContent);
        const p = findProject(Number(el.dataset.id));
        if (p && p.client && ms.indexOf(p.client) >= 0) hit += 1;
      });
      return hit;
    };
    const fbtn2 = $("#treeFilter");
    const pickItem = async (kw) => {
      fbtn2.click();
      await wait(200);
      const pop = document.querySelector(".popup");
      const it = pop && Array.from(pop.children).find((d) => d.textContent.indexOf(kw) >= 0);
      if (!it) return false;
      it.click();
      await wait(300);
      return true;
    };
    const cBefore = clientOf();
    const toggled = await pickItem("显示客户");
    const cAfter = clientOf();
    await pickItem("显示客户");               // 还原
    const cBack = clientOf();
    rep.fix.client = { before: cBefore, after: cAfter, back: cBack, toggled: toggled };

    /* --- #4 子环节筛选：每个有子环节的行各挂一个筛选框（搜索框 + 状态 + 制作人） --- */
    await call("collapse_all", "expand");
    await reload();
    await wait(400);
    /* 先把某条环节改成「反馈」，保证筛的不是空集 */
    const leafOf = (ns) => {
      for (const x of ns || []) {
        if (!(x.children || []).length) return x;
        const r = leafOf(x.children);
        if (r) return r;
      }
      return null;
    };
    const pB = ((D.groups.find((g) => g.key === "commercial") || {}).projects || [])
      .find((p) => (p.children || []).length);
    let nodeFilter = null;
    if (pB) {
      const lf = leafOf(pB.children);
      if (lf) await call("set_status", "node", lf.id, "反馈");
      /* 再挂一条不相干的环节：不这样的话「第一环节」凭后代留下、子任务自己命中，
         筛完一行都没少，看不出筛选到底干没干活 */
      const tmp = await call("add_node", pB.id, null, "临时环节X");
      await reload();
      await wait(300);
      /* reload 之后 D 整份换过了，pB 那份是旧快照（子任务的状态还是改之前的），
         拿它去判「子树里有没有反馈」必错 —— 重新按 id 捞一份 */
      const pBnow = ((D.groups.find((g) => g.key === "commercial") || {}).projects || [])
        .find((p) => p.id === pB.id) || pB;

      /* render() 会重建整棵树，所以每次都重新找按钮，不能攥着旧节点 */
      const fltOf = () => {
        const row = Array.from(document.querySelectorAll("#tree .row.prj"))
          .find((el) => Number(el.dataset.id) === pB.id);
        return row && row.querySelector(".ops .flt");
      };
      /* 这个项目下面连着的环节行（到下一个项目/分组行为止） */
      const rowsOfB = () => {
        const rows = Array.from(document.querySelectorAll("#tree .row"));
        const i = rows.findIndex((el) => el.classList.contains("prj")
          && Number(el.dataset.id) === pB.id);
        if (i < 0) return [];
        const out = [];
        for (let j = i + 1; j < rows.length; j++) {
          if (rows[j].classList.contains("prj") || rows[j].classList.contains("grp")) break;
          out.push(rows[j]);
        }
        return out;
      };
      const bAll = rowsOfB().length;
      const hasBtn = !!fltOf();
      let hasInput = false, txtShown = 0, txtHit = false, txtTrimmed = false;
      let txtRows = [], txtNoLeak = null, txtExpN = 0;
      /* 1) 搜索框：打标题里的两个字，这一层要真的少掉几行，且目标还在 */
      if (hasBtn) {
        const want = (pBnow.children[0] || {}).title || "";
        fltOf().click();
        await wait(250);
        const pop0 = document.querySelector(".popup");
        const inp = pop0 ? pop0.querySelector("input") : null;
        hasInput = !!inp;
        if (inp && want) {
          inp.value = want.slice(0, 2);
          inp.dispatchEvent(new Event("input"));
          await wait(300);
          const rows = rowsOfB();
          txtShown = rows.length;
          txtHit = rows.some((el) => ((el.querySelector(".title") || {}).textContent || "") === want);
          txtTrimmed = rows.length > 0 && rows.length < bAll;
          txtRows = rows.map((el) => ((el.querySelector(".title") || {}).textContent || ""));
          /* ⚠ 条件必须压到**深层**：只命中「第一环节」时，「子任务」不许跟着露出来
             （它自己不含关键字、也没有含关键字的后代）。选中判断沿用后端那套
             「自己命中 or 子孙有命中」—— 父环节要留着当路径，别的都该走。 */
          const q = want.slice(0, 2).toLowerCase();
          const deepHit = (x) => String((x || {}).title || "").toLowerCase().indexOf(q) >= 0
            || ((x || {}).children || []).some(deepHit);
          const cnt = (ns) => (ns || []).forEach((x) => { if (deepHit(x)) txtExpN += 1; cnt(x.children); });
          cnt(pBnow.children);
          txtNoLeak = rows.length === txtExpN;
          inp.value = "";                      // 还原
          inp.dispatchEvent(new Event("input"));
          await wait(250);
        }
        closePopup();
      }
      /* 2) 菜单里的状态：挑「反馈」，父环节要留着（不然看不出归属） */
      let statPicked = false, statRows = 0, statKeepOk = true, statTrimmed = false;
      let feedbackRows = 0, label = "", cleared = false, back = 0, btnAlways = false;
      if (hasBtn) {
        fltOf().click();
        await wait(250);
        const pop = document.querySelector(".popup");
        const it = pop && Array.from(pop.children)
          .find((d) => (d.textContent || "").indexOf("反馈") >= 0);
        if (it) { it.click(); statPicked = true; }
        await wait(350);
        const byId = {};
        const index = (ns) => (ns || []).forEach((x) => { byId[x.id] = x; index(x.children); });
        (pBnow.children || []).forEach((x) => index([x]));
        const hasFB = (x) => !!x && (x.status === "反馈" || (x.children || []).some(hasFB));
        const rows = rowsOfB();
        statRows = rows.length;
        feedbackRows = rows.filter((el) =>
          ((el.querySelector(".badge.st") || {}).textContent || "") === "反馈").length;
        statKeepOk = rows.every((el) => {
          const b = (el.querySelector(".badge.st") || {}).textContent || "";
          return b === "反馈" || hasFB(byId[Number(el.dataset.id)]);
        });
        statTrimmed = rows.length > 0 && rows.length < bAll;
        label = ((fltOf() || {}).textContent || "");
        /* 筛上了按钮就得一直露着（.ops 默认 hover 才显示） */
        const pr = Array.from(document.querySelectorAll("#tree .row.prj"))
          .find((el) => Number(el.dataset.id) === pB.id);
        btnAlways = !!(pr && pr.classList.contains("filtered")
          && getComputedStyle(pr.querySelector(".ops")).opacity === "1");
        /* 3) 清除：行要回来 */
        const b2 = fltOf();
        if (b2) {
          b2.click();
          await wait(250);
          const p2 = document.querySelector(".popup");
          const clr = p2 && Array.from(p2.children)
            .find((d) => (d.textContent || "").indexOf("清除本层筛选") >= 0);
          if (clr) { clr.click(); await wait(300); }
        }
        back = rowsOfB().length;
        cleared = back > statRows;
      }
      /* 4) 多选 / 反选（2026-09-30）：状态一组、制作人一组，
            每组配「反选 / 清空」两个横向按钮，项可以一个个勾。
            这里把三件事都真点一遍 —— 只断言"菜单里有这几个字"证明不了逻辑。 */
      const nfMulti = {};
      if (hasBtn) {
        const ntStats = ((D.status || {}).node || []);
        const nfCur = () => nfOf("project", pB.id) || {};
        const actRows = () => {
          const p = document.querySelector(".popup");
          return p ? Array.from(p.querySelectorAll("div.acts")) : [];
        };
        const actClick = (gi, kw) => {
          const r = actRows()[gi];
          if (!r) return false;
          const b = Array.from(r.querySelectorAll("button")).find((x) => x.textContent === kw);
          if (!b) return false;
          b.click();
          return true;
        };
        /* 每点一下动作，弹层都会就地重开（keep）；偶发没重开成功时 actRows() 会少一行，
           后面的 actClick 就点了个空气 —— 但断言只会报"反选没生效"，看着像功能坏了。
           这里兜一下：行数不够就再点一次筛选按钮把弹层叫回来（onclick 恒是"打开"，
           重开会把旧的那个顶掉，不会叠出两层）。 */
        const ensureActs = async (need) => {
          if (actRows().length >= need) return true;
          const f = fltOf();
          if (!f) return false;
          f.click();
          await wait(250);
          return actRows().length >= need;
        };
        fltOf().click();
        await wait(250);
        nfMulti.actRows = actRows()
          .map((r) => Array.from(r.querySelectorAll("button")).map((b) => b.textContent));
        nfMulti.hasWhoGroup = actRows().length >= 2;
        /* 起点先摆正：这一组清空，只勾「反馈」，后面反选才有确定的补集 */
        const itemOfStat = (s) => {
          const p = document.querySelector(".popup");
          return p && Array.from(p.querySelectorAll("div"))
            .find((d) => !d.className
              && d.textContent.replace(/^[✓\u3000]+/, "") === s);
        };
        actClick(0, "清空");
        await wait(250);
        const fbi = itemOfStat("反馈");
        nfMulti.pickFB = !!fbi;
        if (fbi) fbi.click();
        await wait(250);
        nfMulti.stStart = (nfCur().st || []).slice();

        /* ① 反选：当前只勾着「反馈」→ 应当变成其余 8 个 */
        nfMulti.invClicked = actClick(0, "反选");
        await wait(300);
        nfMulti.invLen = (nfCur().st || []).length;
        nfMulti.invNoFB = !hasWord(nfCur().st, "反馈");
        nfMulti.invRows = rowsOfB().length;

        /* ② 没有「全选」按钮（用户 2026-09-30 要去掉：空集合就等于全选）。
              不变量顶上：反选两次 = 回到「只勾反馈」那一组。 */
        nfMulti.allExpect = ntStats.length;
        await ensureActs(2);
        nfMulti.noAllBtn = !actClick(0, "全选");
        await ensureActs(2);
        nfMulti.invClicked2 = actClick(0, "反选");
        await wait(300);
        nfMulti.invBack = (nfCur().st || []).slice();
        nfMulti.invBackOk = nfMulti.invBack.length === 1
          && nfMulti.invBack[0] === "反馈";

        /* ③ 清空：这一组条件整个消失 */
        await ensureActs(2);
        nfMulti.clrClicked = actClick(0, "清空");
        await wait(300);
        nfMulti.afterClr = (nfCur().st || null);

        /* ④ 制作人组：勾一个 → 反选 → 补集里**必须带上「未分配」**。
              用户 2026-10-04 报的：反选完，所有还没派人的环节一起被吞了 ——
              因为"未分配"以前根本不是可选项，补集只从真制作人里凑得出来。
              这里两头都查：数组对 ≠ 屏幕上对，行没变多照样是坏的。 */
        if (nfMulti.hasWhoGroup) {
          const whoAll = subArtists(pBnow);     // 存的是 vendors.id（字符串），2026-10-16 起
          const first = whoAll[0];
          /* ⚠ 菜单上画的是**名字**，数组里存的是 **id** —— 拿 id 去比 label
             会一个都点不着，而症状只是"反选结果不对"，看着像功能坏了。 */
          const firstLabel = first ? vendorName(first) : null;
          nfMulti.whoAll = whoAll;
          nfMulti.whoFirstLabel = firstLabel;
          const titleOf = (el) => ((el.querySelector(".title") || {}).textContent || "");
          const itemOfWho = () => {
            const p = document.querySelector(".popup");
            return p && Array.from(p.querySelectorAll("div"))
              .find((d) => !d.className
                && d.textContent.replace(/^[✓\u3000]+/, "") === firstLabel);
          };
          if (first) { const it = itemOfWho(); if (it) it.click(); }
          await wait(300);
          nfMulti.pickedWho = (nfCur().who || []).slice();
          await ensureActs(2);
          nfMulti.whoInvClicked = actClick(1, "反选");
          await wait(300);
          nfMulti.whoInv = (nfCur().who || []).slice();
          /* 期望的补集 = 真制作人里除 first 外的人 +「未分配」。
             ⚠ 必须在**反选之前**算出这层有没有"没人认领"的环节，
             反选之后 rowsOfB 已经被筛过了，从行上反推会绕回同一个坑。 */
          const wantInv = whoAll.filter((w) => w !== first).concat([WHO_NONE]);
          nfMulti.noneInLayer = subHasNone(pBnow);
          nfMulti.whoInvWant = wantInv;
          nfMulti.whoInvOk = wantInv.every((w) => (nfCur().who || []).indexOf(w) >= 0)
            && (nfCur().who || []).every((w) => wantInv.indexOf(w) >= 0);
          const rw = rowsOfB().map(titleOf);
          nfMulti.whoInvRows = rw;
          nfMulti.noneKept = rw.indexOf("第一环节") >= 0;     // 没人派工的样本行还得在
          nfMulti.whoDropped = rw.indexOf("子任务") < 0;      // 派了工的那条才该走
          const p2 = document.querySelector(".popup");
          const nt = p2 && Array.from(p2.querySelectorAll("div")).find(
            (d) => !d.className && (d.textContent || "").indexOf("（未分配）") >= 0);
          nfMulti.noneRowShown = !!nt;
          nfMulti.noneRowTicked = !!(nt && nt.textContent.indexOf("✓") >= 0);
          await ensureActs(2);
          actClick(1, "清空");                  // 收尾
          await wait(250);
          nfMulti.afterWhoClr = (nfCur().who || null);
        }
      }
      /* 收尾：把临时那条删掉，别污染后面的阶段 */
      if (tmp && tmp.ok) { await call("delete", "node", tmp.id); await reload(); await wait(200); }
      nodeFilter = {
        hasBtn: hasBtn, hasInput: hasInput,
        bAll: bAll, txtShown: txtShown, txtHit: txtHit, txtTrimmed: txtTrimmed,
        txtRows: txtRows, txtExpN: txtExpN, txtNoLeak: txtNoLeak,
        statPicked: statPicked, statRows: statRows, feedbackRows: feedbackRows,
        statKeepOk: statKeepOk, statTrimmed: statTrimmed,
        label: label, btnAlways: btnAlways, cleared: cleared, back: back,
        multi: nfMulti,
      };
    }
    rep.fix.nodeFilter = nodeFilter;
  });

  /* 批量新增子环节：走真实入口（弹窗 → 实时预览 → 创建），并验证重复会被跳过 */
  await phase("nodes", 20000, async () => {
    await step("nodes");
    setView("tree");
    await wait(400);
    const prj = (D.groups.find((g) => g.key === "commercial") || {}).projects || [];
    const p0 = prj.find((p) => p.title === "冒烟商业项目") || prj[0];
    const stage = (p0.children || [])[0];
    openNodesDialog(p0.id, stage ? stage.id : null, p0.title);
    await wait(250);
    const dlg = getComputedStyle($("#ovNodes")).display;
    /* 弹窗里这几句是用户逐句定过的措辞，谁改回去都该被逮住 ——
       所以除了"新文案在不在"，还要**反向**钉住老文案没回来。 */
    const toSay = ($("#nodesTo") || {}).textContent || "";
    const phSay = ($("#nodesSpec") || {}).placeholder || "";
    const hintSay = (($("#ovNodes .hint") || {}).textContent || "").replace(/\s+/g, " ");
    /* ⚠ 空态那句话必须在**灌数据之前**读：下面一填值它就变成"将创建 N 个"了，
       放在后面读会永远读到结果态，断言看着是绿的其实什么都没测到。 */
    const emptySay = ($("#nodesPrev") || {}).textContent || "";
    $("#nodesSpec").value = "s001,s003A,s006-009";
    await refreshNodesPrev();
    await wait(500);
    const prev = $("#nodesPrev").textContent || "";
    const want = ["s001", "s003A", "s006", "s007", "s008", "s009"];
    /* 序列模板（2026-10-16）：`WD####(12-20)` 要摊成 WD0012…WD0020。两个读法：
       - seqPv 走 parse_nodes，纯解析、不掺库里已有的同名，才能精确数出 9 个
       - seqPrev 走真·界面刷新，证明这条语法**真的接在预览框上**了
       ⚠ 试完必须把值**放回去**再点创建 —— 不然下面 titles 会凭空多出 9 个。 */
    const seqPv = await call("parse_nodes", "WD####(12-20)");
    const seqBadPv = await call("parse_nodes", "WD####(12)");
    /* ⚠ 界面只动两次、每次 250ms：原来每步 400ms × 3，把本段结束时间推后了
       1.2 秒。冒烟是**串行**跑在同一个 app 实例上的，多推这一点就够让后面的段
       踩上偶发的时序抖动（2026-10-16 那次假红，multi 段 pickAfter 从 9 变 1）。
       能少等就少等 —— 要证明的事（语法接在预览框上）已经由这两次拿到了。 */
    $("#nodesSpec").value = "WD####(12-20)";
    await refreshNodesPrev();
    await wait(250);
    const seqPrev = $("#nodesPrev").textContent || "";
    $("#nodesSpec").value = "WD####(12)";
    await refreshNodesPrev();
    await wait(250);
    const seqBadPrev = $("#nodesPrev").textContent || "";
    $("#nodesSpec").value = "s001,s003A,s006-009";
    await refreshNodesPrev();
    $("#nodesOk").click();
    await wait(2200);
    const d2 = await call("load");
    const p2 = ((d2.groups.find((g) => g.key === "commercial") || {}).projects || [])
      .find((x) => x.id === p0.id);
    const st2 = ((p2 || {}).children || []).find((c) => c.id === (stage || {}).id);
    const titles = ((st2 || {}).children || []).map((c) => c.title);
    /* 同一批再提交一次：应当全部跳过，不产生重复 */
    const again = await call("add_nodes", p0.id, stage ? stage.id : null,
                             "s001,s003A,s006-009");
    rep.nodes = {
      dlg: dlg,
      toSay: toSay, phSay: phSay, hintSay: hintSay, emptySay: emptySay,
      prevOk: prev.indexOf("将创建") >= 0,
      prevAll: want.every((t) => prev.indexOf(t) >= 0),
      titles: titles,
      n: titles.length,
      dupCreated: again && again.count,
      dupSkipped: ((again || {}).skipped || []).length,
      /* 序列模板：正确写法要摊开、写歪的要当场报错（两处都验） */
      seqPvOk: seqPv && seqPv.ok,
      seqPvCount: seqPv && seqPv.count,
      seqPvFirst: ((seqPv || {}).items || [])[0],
      seqPvLast: ((seqPv || {}).items || [])[8],
      seqBadOk: seqBadPv && seqBadPv.ok,
      seqPrevOk: seqPrev.indexOf("将创建") >= 0
        && seqPrev.indexOf("WD0012") >= 0 && seqPrev.indexOf("WD0020") >= 0,
      seqPrevRed: seqBadPrev.indexOf("✕") >= 0,
    };
  });

  /* 设置面板：逐项**真改一遍**再回读，专治「看着像没生效」。
     用户报的原始问题（issue #5）就是"设置里多个设置项无效，需逐一测定"，
     光断言"控件在页面上"证明不了任何事 —— 必须写进去、读回来、再重开面板看回显。 */
  await phase("settings", 30000, async () => {
    await step("settings");
    setView("tree");
    await wait(300);
    const S = () => D.settings;                 // 前端缓存
    const RE = async (k) => (await call("load")).settings[k];   // 真·库里的值
    const srep = {};

    openSettings();
    await wait(300);

    /* --- 提醒强度（用户点名的就是这个）：三档挨个点，库里得跟着变 --- */
    const lvSet = async (lv) => {
      const b = $$("#segLv button").find((x) => x.dataset.l === lv);
      if (!b) return null;
      b.click();
      await wait(500);
      return { act: b.classList.contains("act"), db: await RE("remind_level") };
    };
    const lv1 = await lvSet("1"), lv3 = await lvSet("3"), lv2 = await lvSet("2");
    srep.level = { lv1: lv1, lv3: lv3, lv2: lv2,
      ok: !!(lv1 && lv1.act && lv1.db === "1" && lv3 && lv3.act && lv3.db === "3"
             && lv2 && lv2.act && lv2.db === "2") };

    /* --- 打卡时间 --- */
    const tm = $("#setTime");
    tm.value = "07:15";
    tm.dispatchEvent(new Event("change"));
    await wait(500);
    srep.time = { db: await RE("checkin_time"), ok: (await RE("checkin_time")) === "07:15" };

    /* --- 停滞阈值：**一项**（2026-09-30 起不再分商业 / 个人）--- */
    const sc = $("#setStale");
    sc.value = "5"; sc.dispatchEvent(new Event("change"));
    await wait(900);
    srep.stale = { v: await RE("stale_days"), fields: $$("#ovSet .fld")
        .filter((f) => /停滞/.test((f.querySelector(".lb") || {}).textContent || "")).length,
      ok: (await RE("stale_days")) === "5" };

    /* --- 重提醒间隔 --- */
    const ng = $("#setNag"), al = $("#setAlert");
    ng.value = "45"; ng.dispatchEvent(new Event("change"));
    await wait(500);
    al.value = "99"; al.dispatchEvent(new Event("change"));
    await wait(500);
    srep.nag = { nag: await RE("nag_minutes"), alert: await RE("alert_interval"),
      ok: (await RE("nag_minutes")) === "45" && (await RE("alert_interval")) === "99" };

    /* --- 开关四项：点一次 → 库里翻转 → 再点回来必须回到原值 --- */
    const flip = async (id, key) => {
      const b = $(id);
      const before = await RE(key);
      b.click(); await wait(600);
      const mid = await RE(key);
      const txtMid = b.textContent;
      b.click(); await wait(600);
      const back = await RE(key);
      return { before: before, mid: mid, back: back, txt: txtMid,
               ok: mid === (before === "1" ? "0" : "1") && back === before };
    };
    srep.tg = {
      notify: await flip("#setNotify", "notify_enabled"),
      sandbox: await flip("#setSandbox", "nosandbox"),
    };

    /* --- 主题：点「亮色」body 要真的变亮，再切回暗色 --- */
    const thBtn = (t) => $$("#segTheme button").find((x) => x.dataset.t === t);
    thBtn("light").click(); await wait(500);
    const lightOn = document.body.classList.contains("light");
    const thLightDb = await RE("theme");
    thBtn("dark").click(); await wait(500);
    const darkOn = !document.body.classList.contains("light");
    srep.theme = { lightOn: lightOn, lightDb: thLightDb, darkOn: darkOn,
      ok: lightOn && thLightDb === "light" && darkOn && (await RE("theme")) === "dark" };

    /* --- 回显：攒了这么多改动，重开面板看控件是不是显示**库里**的值 ——
           老 bug 就是改完不同步 D.settings，重开面板一律显示旧值。 --- */
    openSettings();
    await wait(400);
    const actLv = (document.querySelector("#segLv button.act") || { dataset: {} }).dataset.l || null;
    srep.echo = {
      time: $("#setTime").value,
      stale: $("#setStale").value,
      nag: $("#setNag").value,
      alert: $("#setAlert").value,
      level: actLv,
      notifyTxt: $("#setNotify").textContent,
    };
    srep.echoOk = srep.echo.time === "07:15" && srep.echo.stale === "5"
      && srep.echo.nag === "45" && srep.echo.alert === "99"
      && srep.echo.level === "2";

    /* --- 还原成默认，别把冒烟库的配置带歪 --- */
    for (const [k, v] of [["checkin_time", "09:00"], ["stale_days", "3"],
                          ["nag_minutes", "30"],
                          ["alert_interval", "180"], ["remind_level", "2"]]) {
      await call("set_setting", k, v);
      D.settings[k] = v;
    }
    openSettings(); await wait(300);
    srep.echoAfterReset = { time: $("#setTime").value, level: (document.querySelector("#segLv button.act") || { dataset: {} }).dataset.l || null };
    modal("#ovSet", false);

    /* 哪些子项自己带了 ok 且为 false —— 只挑真正判过的那些，
       别把 tg / echo 这种"汇总容器"当成失败项（它们本来就没有 ok）。 */
    const bad = [];
    const scan = (o, path) => Object.keys(o).forEach((k) => {
      const v = o[k];
      if (!v || typeof v !== "object") return;
      const p = path ? path + "." + k : k;
      if ("ok" in v) { if (!v.ok) bad.push(p); return; }
      if (k === "tg") Object.keys(v).forEach((t) => { if (!v[t].ok) bad.push(p + "." + t); });
    });
    scan(srep, "");
    srep.bad = bad;
    srep.allOk = bad.length === 0 && srep.echoOk;
    rep.settings = srep;
  });

  /* 多选批量（#3）+ 数据文件可设定（#6）：都是"真的点一遍" */
  /* 多选（2026-10-13 拆出后半段的「数据文件」，并把预算按实测放大）。
     ⚠ 这一段本来是 multi + 数据文件 + 客户弹窗 三块塞在一个 30 秒预算里。
     2026-10-13 实测：除它以外的每一段都在 9 秒内（settle 2.6 / prjset 5.5），
     就它顶着预算超时 —— 三块不相干的功能共用一个预算，谁多一点就全红。
     拆开之后单看它自己：段内 **68 次 `await wait()` 合计 26.4 秒**，再加 13 次
     跨桥调用和 9 次 reload，本机实测 30~46 秒 —— 所以预算先给到 60 秒。
     ⚠ 当晚又超了一次（那次整机明显变慢：全程 3m31s，平时 1m46s），
     症状是"multi 后面所有段整片 FAIL" —— 后面每段都因为 `failed` 被跳过，
     看着像功能坏了。**这一段是最重、也是波动最大的一段**，预算再放到 90 秒：
     它只影响"容忍多久"，断言一条没少，真死锁照样会报。（这些 sleep 是
     "等界面/桥稳定"的，不敢一刀切砍短：短了会变成偶发假红。）
     拆开后 report 里的键（rep.multi / rep.dbFile）一个没变。 */
  await phase("multi", 90000, async () => {
    await step("multi");
    setView("tree");
    selClear();
    ui.multi = 0; saveUI();
    await call("collapse_all", "expand");
    await reload();
    await wait(600);

    /* --- #3 多选：默认没有复选框 → 点「多选」才出现 --- */
    const mrep = {};
    /* ⚠ 一开始就挂到 rep 上：mrep 是边测边填的，只在段尾赋值的话，
       一段超时（哪怕是最后一条卡住）整个对象都是空的 —— 报出来只有
       "某段超时"，连"卡在第几步"都没有。2026-10-13 就是被这个坑了一轮。 */
    rep.multi = mrep;
    mrep.pickBefore = n("#tree input.pick");
    mrep.barBefore = getComputedStyle($("#bulkBar")).display;
    $("#treeMulti").click();
    await wait(400);
    mrep.pickAfter = n("#tree input.pick");
    mrep.barAfter = getComputedStyle($("#bulkBar")).display;
    /* 批量条上该有的七颗按钮：改状态 / 改制作人 / 改截止日期 / 改备注 / 删除 / 清空 / 退出
       （2026-10-03 加了后两个：能批量改制作人，日期和备注没道理不行；
        2026-10-10 加「删除」）。
       「全选本层」按用户要求撤掉了 —— 与其断言"某个不存在的元素不存在"（恒真、等于没测），
       不如把**实际有哪些按钮**采回来：既确认没多出全选，也能发现少了谁。 */
    mrep.bulkBtns = Array.from(document.querySelectorAll("#bulkBar button")).map((b) => b.textContent.trim());
    /* 批量条最右边那颗必须是「退出多选」：破坏性的「删除」不能贴在边角，
       手指划过去容易误触；而且它必须离「清空」远一点（两个都是"减法"按钮，
       挨在一起点错就是删数据）。 */
    mrep.bulkDelBtn = (() => {
      const b = $("#bulkDel");
      if (!b) return { found: false };
      const bs = Array.from(document.querySelectorAll("#bulkBar button"));
      const none = $("#bulkNone");
      const exit = $("#bulkExit");
      const box = (el) => el.getBoundingClientRect().left;
      return {
        found: true,
        text: b.textContent.trim(),
        cls: b.className,
        color: getComputedStyle(b).color,
        /* 顺序：删除 在 清空 左边、退出 在最右 */
        delBeforeClear: box(b) < box(none),
        exitRightmost: bs[bs.length - 1] === exit,
        bar: bs.map((x) => x.textContent.trim()),
      };
    })();

    /* --- 批量条必须粘顶：树往下滑多远，它都留在内容区顶部 ---
       2026-10-03 用户报：多选后往下滑，批量条跟着滚出窗口，改个状态还得先滑回去。
       判据四条缺一不可：
       ① position 真算出来是 sticky（写了没生效的情况很常见）；
       ② **滚动真的发生了**（scrollable 小的话这条就是在测空气）；
       ③ 滚完还在 main 顶部、且在视口内；
       ④ 取批量条正中间那一点做命中测试 —— 落在它自己身上才算数。
          z-index 忘给的话，sticky 照样成立、位置照样对，但会被后面的行盖住，
          实际表现是"按钮点不动 / 点到背后的行"，光看前三条永远发现不了。 */
    {
      const mainEl = document.querySelector("main");
      const barEl = $("#bulkBar");
      const hintEl = $("#bulkHint");
      /* ⚠ 内容不够高时 main 根本没有滚动条（第一次跑就是 room=0，等于在测空气）。
         所以先往 main 末尾垫一块占位 -- 只加兄弟节点，不碰 #tree 的 HTML，
         （render 会整块重画，往里塞东西会被冲掉），测完立刻拆。 */
      const spacer = document.createElement("div");
      spacer.id = "diagSpacer";
      spacer.style.height = (mainEl.clientHeight * 2) + "px";
      mainEl.appendChild(spacer);
      await wait(200);
      const top0 = barEl.getBoundingClientRect().top;
      const room = Math.round(mainEl.scrollHeight - mainEl.clientHeight);
      mainEl.scrollTop = Math.min(room, 500);
      await wait(300);
      const rr = barEl.getBoundingClientRect();
      const mainTop = mainEl.getBoundingClientRect().top;
      const hit = document.elementFromPoint(
        Math.round(rr.left + Math.min(rr.width - 8, 30)),
        Math.round(rr.top + rr.height / 2));
      const hintH = hintEl ? Math.round(hintEl.getBoundingClientRect().height) : null;
      const posOk = getComputedStyle(barEl).position === "sticky";
      const scrolled = Math.round(mainEl.scrollTop);
      const pastStick = scrolled > Math.round(top0 - mainTop) + 10;   // 确实滚过了"该粘住"的临界点
      const pinned = Math.abs(rr.top - Math.round(mainTop)) <= 2;     // 停在**可视区**上沿
      const inView = rr.bottom <= (window.innerHeight || 1e9);        // 整条都在视口里
      const notCovered = !!(hit && barEl.contains(hit));              // 没被后面的行盖住
      /* 条上方那一条（main 的 padding-top）不许漏出内容：
         sticky 停的是内容盒顶部，留了缝的话滚动的行会从缝里溜到按钮上方 */
      const above = document.elementFromPoint(
        Math.round(rr.left + Math.min(rr.width - 8, 30)), Math.round(mainTop + 3));
      const leakAbove = !!(above && (above.closest("#tree") || above.closest("#heat")));
      const hintOneLine = hintH !== null && hintH <= 24;             // 提示只占一行
      mrep.barSticky = {
        pos: getComputedStyle(barEl).position,
        room: room, scrolled: scrolled,
        topBefore: Math.round(top0), topAfter: Math.round(rr.top),
        mainTop: Math.round(mainTop), hintH: hintH,
        hitInBar: notCovered, leakAbove: leakAbove,
        above: above ? (above.id || String(above.className) || above.tagName) : null,
        hit: hit ? (hit.id || String(hit.className) || hit.tagName) : null,
        ok: posOk && room > 20 && pastStick
            && pinned && inView && notCovered && !leakAbove && hintOneLine,
      };
      mainEl.scrollTop = 0;
      if (spacer.parentNode) { spacer.parentNode.removeChild(spacer); }
      await wait(200);
    }

    /* --- 批量条的排布：所有东西必须在**同一行**，且「退出多选」紧挨在「清空」右边 ---
       用户报「退出多选掉到第二行左边」。根因在 hint 的 flex-basis 上（见 style.css）；
       这条断言把排布钉死，免得以后谁再动 #bulkBar 的 flex 又把它挤下去。 */
    {
      const kids = Array.from($("#bulkBar").children);
      /* ⚠ 判「有没有换行」要看**垂直中心**，不能看 offsetTop：
         批量条是 align-items:center，高矮不齐的元素本来就不共顶边 ——
         空的计数 span 高度是 0（top 正好落在中线上）、提示 16px、按钮 24px，
         量出来是三种 top，其实全在同一行。只看 top 会永远误判成"换行了"。 */
      const cen = kids.map((el) => {
        const b = el.getBoundingClientRect();
        return b.top + b.height / 2;
      });
      const spread = Math.round(Math.max.apply(null, cen) - Math.min.apply(null, cen));
      const oneRow = spread <= 3;
      const nb = $("#bulkNone").getBoundingClientRect();
      const eb = $("#bulkExit").getBoundingClientRect();
      /* 这一步还没选任何东西 → 计数必须整段留白（写「还没选」是老毛病：占版面还要读一遍） */
      const cntTxt = ($("#bulkCnt") || { textContent: null }).textContent;
      /* kids = 计数 span + 提示 span + 7 颗按钮 = 9
         （2026-10-10 加「删除」之前是 8 —— 别再把数字写死在这儿，
         改一次按钮要改两个地方：这里和 smoke_fin_gui 的按钮清单断言） */
      const wantKids = 2 + Array.from(document.querySelectorAll("#bulkBar button")).length;
      mrep.barLayout = {
        kids: kids.length, wantKids: wantKids, oneRow: oneRow, spread: spread,
        sameRow: Math.abs(nb.top - eb.top) <= 2,
        exitRight: Math.round(eb.left) > Math.round(nb.left),
        cntWhenNone: cntTxt,
        hint: ($("#bulkHint") || {}).textContent,
        /* 排布全在同一行 + 退出在清空右边 + 计数留白。
           kids 用"实际按钮数 + 2"推 —— 加按钮时这条不会假红。 */
        ok: oneRow && kids.length === wantKids
            && Math.abs(nb.top - eb.top) <= 2
            && Math.round(eb.left) > Math.round(nb.left)
            && cntTxt === "",
      };
    }

    /* --- #3 状态色块：竖条、真的比老圆点醒目（老版 7px 圆点，扫不出来） --- */
    const dotEl = document.querySelector("#tree .row .dot");
    if (dotEl) {
      const cs = getComputedStyle(dotEl);
      const b = dotEl.getBoundingClientRect();
      mrep.dotW = Math.round(b.width);
      mrep.dotH = Math.round(b.height);
      mrep.dotBg = cs.backgroundColor;
      /* 竖条：明显比宽高都 7 的圆点高，且宽窄合适 */
      mrep.dotOk = b.height >= 12 && b.width >= 4 && b.width <= 9
        && cs.backgroundColor !== "rgba(0, 0, 0, 0)";
    }

    /* --- 真·用户手势：点**复选框本身** ---
       用户报"勾第一项复选框不显示勾，勾第二项时第一项才显示勾" ——
       就是点小方块这条路径（差一拍）。必须单独测：
       点完立刻看 checked 跟选择集是否一致，而不是等下一下点击才补上。

       选两条**互不包含**的行：级联会把后代一起选进去，
       要是挑了"父 + 它自己的子"，第二条一点就变成取消 —— 那是正确行为，
       不是 bug（我第一次就踩了这个，误以为是没修好）。 */
    const rowsAll = Array.from(document.querySelectorAll("#tree .row"));
    const pickRows = rowsAll.filter((el) => el.querySelector(".pick"));
    {
      /* 找一对：r1 的子树里不含 r0，也不含 r1 自己 */
      const r0 = pickRows[0];
      const id0 = Number(r0.dataset.id);
      const sub0 = new Set(selfAndDescendants(findNode(id0)));
      const r1 = pickRows.find((el) => {
        const i = Number(el.dataset.id);
        if (i === id0 || sub0.has(i)) return false;
        /* 反向也查一下：r0 不能在 r1 的子树里 */
        return !selfAndDescendants(findNode(i)).includes(id0);
      });
      const c0 = r0.querySelector(".pick");
      mrep.pickPairOk = !!r1;
      if (r1) {
        const c1 = r1.querySelector(".pick");
        const id1 = Number(r1.dataset.id);
        const realClick = (el, opt) => {
          const o = Object.assign({ bubbles: true, cancelable: true }, opt || {});
          /* 用真 MouseEvent（`.click()` 是合成调用，测不出 activation 回滚那类问题） */
          ["mousedown", "mouseup", "click"].forEach(
            (t) => el.dispatchEvent(new MouseEvent(t, o)));
        };
        selClear(); repaintSelection(); syncBulkBar();
        await wait(200);

        /* ① 点第一条 → 选中它（连带后代），复选框当场就是勾的
           （老 bug 这里 checked=false，要再点一次才补上） */
        realClick(c0);
        await wait(250);
        mrep.pickClick1 = {
          id: id0, inSet: selNodes.has(id0), checked: c0.checked,
          cls: r0.classList.contains("picked"),
          ok: selNodes.has(id0) && c0.checked === true
              && r0.classList.contains("picked"),
        };

        /* ② **不按任何键直接点 = 加选**（用户 2026-10-10 定的）。
           历史来回走了两趟，别再搞反：
             * 最初是"取反" → 累加，用户报「现在是加减选模式」；
             * 2026-10-02 改成"先清空再选"（真单选）；
             * 2026-10-10 用户又要求回到加选 —— 现在的口径是
               「无修饰键 = 加选、Shift = 范围选」，两种键各管一头。
           所以这里钉的是：第一条**还在**，第二条也进了集合。 */
        realClick(c1);
        await wait(250);
        mrep.pickClick2 = {
          id: id1,
          firstKept: selNodes.has(id0),
          firstStillChecked: c0.checked === true,
          secondInSet: selNodes.has(id1),
          secondChecked: c1.checked === true,
          bothIn: selNodes.has(id0) && selNodes.has(id1),
          ok: selNodes.has(id0) && c0.checked === true
              && selNodes.has(id1) && c1.checked === true,
        };

        /* ③ 再点同一条 → **只撤它自己**（加选的对称操作）。
           这时候 c0 还选着，所以"选择集清空"是错的 ——
           钉的是"点谁撤谁、别人不动"。 */
        realClick(c1);
        await wait(250);
        mrep.pickClick3 = {
          secondGone: !selNodes.has(id1),
          secondUnchecked: c1.checked === false,
          firstKept: selNodes.has(id0),
          firstStillChecked: c0.checked === true,
          ok: !selNodes.has(id1) && c1.checked === false
              && selNodes.has(id0) && c0.checked === true,
        };

        /* ④ Ctrl 加选仍然叠加（跟无修饰键现在是同一个效果，但键位得各测一遍）
           ⚠ 必须先 selClear()：③ 结束时 c0 还选着，直接 realClick(c0)
           会把它**取消**掉（加选的本质是取反），beforeCtrl 就落成 0 了 ——
           2026-10-10 改语义时就是这么红了一条。 */
        selClear(); repaintSelection(); syncBulkBar();
        await wait(150);
        realClick(c0); await wait(250);
        const beforeCtrl = selCount();
        realClick(c1, { ctrlKey: true });
        await wait(250);
        mrep.pickCtrl = {
          before: beforeCtrl, after: selCount(),
          bothIn: selNodes.has(id0) && selNodes.has(id1),
          ok: beforeCtrl >= 1 && selCount() > beforeCtrl
              && selNodes.has(id0) && selNodes.has(id1),
        };

        /* ⑤ **Shift 点复选框**也要能范围选 —— 老 bug：只有点"整行"才认 Shift，
           而多选模式每行左边都有个小方块，用户点的恰恰是它。 */
        selClear(); repaintSelection(); syncBulkBar();
        await wait(150);
        const fc = pickRows[0], lc = pickRows[pickRows.length - 1];
        const fId = Number(fc.dataset.id), lId = Number(lc.dataset.id);
        realClick(fc.querySelector(".pick"));
        await wait(200);
        realClick(lc.querySelector(".pick"), { shiftKey: true });
        await wait(400);
        const ord0 = visibleNodeOrder().map((x) => x.id);
        const p1 = ord0.indexOf(fId), p2 = ord0.indexOf(lId);
        const q1 = Math.min(p1, p2), q2 = Math.max(p1, p2);
        const lRow = nodeRow(lId);
        mrep.shiftCp = {
          span: q2 - q1 + 1, sel: selCount(),
          all: q1 >= 0 && ord0.slice(q1, q2 + 1).every((i) => selNodes.has(i)),
          lastChecked: lRow ? lRow.querySelector(".pick").checked : null,
          ok: q1 >= 0 && q2 - q1 + 1 >= 2
              && ord0.slice(q1, q2 + 1).every((i) => selNodes.has(i))
              && !!lRow && lRow.querySelector(".pick").checked === true,
        };
        selClear(); repaintSelection(); syncBulkBar();
        await wait(150);
      }
    }

    /* --- 级联：点父环节 → 它 + 全部后代一起选中 --- */
    /* 找一个有子环节的环节（冒烟种子里「第一环节」下面挂着「子任务」） */
    const parentRow = pickRows.find((el) => {
      const f = findNode(Number(el.dataset.id));
      return f && f.children && f.children.length;
    });
    mrep.hasParent = !!parentRow;
    if (parentRow) {
      const pNode = findNode(Number(parentRow.dataset.id));
      const want = selfAndDescendants(pNode).length;      // 它 + 后代
      parentRow.click();
      await wait(300);
      mrep.cascadeSel = selCount();
      mrep.cascadeWant = want;
      mrep.cascadeAllIn = selfAndDescendants(pNode).every((i) => selNodes.has(i));
      /* 再点一次 → 取消，后代也要跟着取消 */
      parentRow.click();
      await wait(300);
      mrep.cascadeOff = selCount();
      mrep.cascadeNoneIn = selfAndDescendants(pNode).every((i) => !selNodes.has(i));

      /* --- 用户报的 bug（2026-09-27）：勾主项后再减选一个子项，
             主项必须**当场变成未勾**（自己是残的就不算选中）。
             判定要同时看三样：父行复选框、父行 .picked、父项还在不在选择集里。 */
      parentRow.click();                                   // 主项全选
      await wait(250);
      const kid = (pNode.children || [])[0];
      const kidRow = nodeRow(kid.id);
      mrep.partial = { kidId: Number(kid.id), kidRow: !!kidRow };
      if (kidRow) {
        /* 减选一条：`selOne` 是"按显示态取反"，所以对**已选中**的子项
           再点一下就是把它撤掉。这里用 Ctrl 点表达"我是在做加减选"，
           语义上跟无修饰键点击（2026-10-10 起也是加选/撤选）等价 ——
           钉的是"减选后父项要跟着变未勾"，不是键位本身。 */
        kidRow.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
        await wait(300);
        const pRow2 = nodeRow(pNode.id);
        const pCp = pRow2 ? pRow2.querySelector(".pick") : null;
        mrep.partial.kidInSet = selHas(kid.id);
        mrep.partial.parentInSet = selHas(pNode.id);
        mrep.partial.parentDomOn = pRow2 ? pRow2.classList.contains("picked") : null;
        mrep.partial.parentCpOn = pCp ? pCp.checked : null;
        /* 期望：子项被剔掉、父项**也从选择集里剔除**、父行显示未勾 */
        mrep.partial.ok = !selHas(kid.id)
          && !selHas(pNode.id)
          && !!pRow2 && !pRow2.classList.contains("picked")
          && !!pCp && pCp.checked === false;
        /* 再点主项应当"整棵全选"（显示态取反），不是取消 */
        pRow2.click();
        await wait(300);
        mrep.partial.reclickAllIn = selfAndDescendants(pNode).every((i) => selHas(i));
        const pRow3 = nodeRow(pNode.id);
        const pCp3 = pRow3 ? pRow3.querySelector(".pick") : null;
        mrep.partial.reclickDomOn = pCp3 ? pCp3.checked : null;
      }
      selClear(); repaintSelection(); syncBulkBar();
    }

    /* --- Ctrl 点：加选（跟单击同级）；再 Ctrl 点一次减选 ---
       坑（2026-09-27 又踩一次）：**两条必须互相不包含**。
       挑到父子关系的话，"Ctrl 点子项"实际是"减选已经级联选上的子项"，
       而减选会把残缺的父项一起 prune 掉 → 看起来像"加选变小了"。
       用 selfAndDescendants 双向排查，确保 id2 不在 id1 的子树里、反之亦然。 */
    let r1 = null, r2 = null;
    for (let a = 0; a < pickRows.length && !r2; a++) {
      for (let b = a + 1; b < pickRows.length; b++) {
        const A = findNode(Number(pickRows[a].dataset.id));
        const B = findNode(Number(pickRows[b].dataset.id));
        if (!A || !B) continue;
        const as = selfAndDescendants(A), bs = selfAndDescendants(B);
        const A2 = as.map(Number), B2 = bs.map(Number);
        if (A2.includes(Number(B.id)) || B2.includes(Number(A.id))) continue;  // 有包含关系，跳过
        r1 = pickRows[a]; r2 = pickRows[b]; break;
      }
    }
    mrep.ctrlPair = { has: !!r2 };
    if (r1 && r2) {
      const id1 = Number(r1.dataset.id), id2 = Number(r2.dataset.id);
      selClear(); repaintSelection(); syncBulkBar();
      r1.click(); await wait(200);
      const afterSingle = selCount();
      r2.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
      await wait(300);
      mrep.ctrlAdd = { single: afterSingle, after: selCount(), has2: selNodes.has(id2) };
      /* 减选：Ctrl 再点 r1 */
      r1.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
      await wait(300);
      mrep.ctrlSub = { gone1: !selNodes.has(id1), left: selCount() };
    }

    /* --- Shift 范围选：点第一条，再 Shift 点最后一条 → 中间全中 --- */
    selClear(); repaintSelection(); syncBulkBar();
    const first = pickRows[0], last = pickRows[pickRows.length - 1];
    const fId = Number(first.dataset.id), lId = Number(last.dataset.id);
    first.click(); await wait(200);
    last.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: true }));
    await wait(400);
    const order = visibleNodeOrder().map((x) => x.id);
    const i1 = order.indexOf(fId), i2 = order.indexOf(lId);
    const lo = Math.min(i1, i2), hi = Math.max(i1, i2);
    /* 范围里每一条（连带后代）都该在选中集里 */
    const spanAll = lo >= 0 && hi >= 0 && order.slice(lo, hi + 1)
      .every((i) => selNodes.has(i));
    mrep.shift = { from: i1, to: i2, span: hi - lo + 1, all: spanAll, sel: selCount() };

    /* 光看 selNodes 不够 —— 界面真的画出来没有才是用户看到的东西。
       末条（Shift 落点）必须自己 .picked 且复选框 checked。
       注意：**项目行和环节行的 id 会撞车**（`bindDrag` 里两者都写 dataset.id），
       所以定位必须带 kind，光用 [data-id] 会取到项目行（它没有 .pick）→ 假阳性。
       这里直接用模块级的 nodeRow(id)（它已经带 kind="node"）——
       以前这里又局部 `const nodeRow` 了一遍，而 JS 的 const 在**整个函数体**里
       都会造成 TDZ：同函数里更早的 nodeRow 调用（上面 partial 那段）全炸，
       报 "Cannot access 'nodeRow' before initialization"。 */
    const domOn = (el) => !!el && el.classList.contains("picked")
      && !!el.querySelector(".pick") && el.querySelector(".pick").checked;
    mrep.shiftDom = {
      first: domOn(first),
      last: domOn(last),
      /* 选中集里每一条，只要它在 DOM 里画着，就必须是勾上的 */
      allDrawn: Array.from(selNodes)
        .filter((i) => nodeRow(i))
        .every((i) => domOn(nodeRow(i))),
      lastId: lId,
      lastInSet: selNodes.has(lId),
      /* 不勾的单独抓出来（只算画出来了的） */
      bad: Array.from(selNodes).filter((i) => nodeRow(i) && !domOn(nodeRow(i))),
      /* 选了但看不见的（折叠分支里的子级）—— 用来解释"计数比勾多" */
      hidden: Array.from(selNodes).filter((i) => !nodeRow(i)).length,
    };

    /* --- Shift 取消：目标已选时，整段应当被取消 --- */
    last.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: true }));
    await wait(300);
    mrep.shiftOff = { sel: selCount() };
    mrep.shiftOffDom = { last: domOn(last) };

    /* --- 落点在折叠箭头 .tw 上时，Shift 也要生效 ---
       老 bug：r.onclick 开头 `if (closest(".ops") || closest(".tw")) return;`
       会把带修饰键的点击一起吞掉 —— 用户 Shift 点"最后一条"时鼠标偏到
       行首箭头 / 行尾按钮上，整段范围选就没发生，看着就是"最后点的那条没勾"。 */
    selClear(); repaintSelection(); syncBulkBar();
    const a2 = pickRows[0], t2 = pickRows[pickRows.length - 1];
    const twEl = t2.querySelector(".tw");
    a2.click(); await wait(200);
    const beforeTw = selCount();
    /* 断言"范围真的生效了"：Shift 点箭头后，落点那行必须也进选择集
       且界面上是勾上的 —— 老代码在这里会一动不动。 */
    const t2Id = Number(t2.dataset.id);
    if (twEl) {
      twEl.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: true,
                                                    cancelable: true }));
      await wait(300);
    }
    mrep.shiftTw = {
      before: beforeTw, after: selCount(),
      targetIn: selNodes.has(t2Id),
      targetDrawn: domOn(nodeRow(t2Id)),
      ok: !!twEl && selNodes.has(t2Id) && domOn(nodeRow(t2Id)),
    };
    /* 清干净，别影响后面的断言 */
    selClear(); repaintSelection(); syncBulkBar();

    /* 真改状态：走批量菜单挑「反馈」 */
    selClear(); repaintSelection();
    /* 挑两条来改，别用整段（种子里环节数量会变） */
    const two = pickRows.slice(0, 2).map((el) => Number(el.dataset.id));
    two.forEach((i) => selNodes.add(i));
    repaintSelection(); syncBulkBar();
    await wait(200);
    const ids = Array.from(selNodes);
    $("#bulkStat").click();
    await wait(300);
    const pop = document.querySelector(".popup");
    const it = pop && Array.from(pop.children).find((d) => d.textContent.indexOf("反馈") >= 0);
    mrep.hasMenu = !!it;
    if (it) { it.click(); await wait(1400); }
    const d2 = await call("load");
    const statOf = (i) => {
      let hit = null;
      (d2.groups || []).forEach((g) => (g.projects || []).forEach((p) => {
        const walk = (arr) => (arr || []).forEach((x) => {
          if (Number(x.id) === Number(i)) hit = x.status;
          walk(x.children);
        });
        walk(p.children);
      }));
      return hit;
    };
    mrep.statOk = ids.length > 0 && ids.every((i) => statOf(i) === "反馈");
    mrep.afterStat = ids.map(statOf);

    /* 真改制作人：挑菜单里第一个具体的人名（跳过「清空」「新建一个外包商…」）。
       ⚠ 候选行的文案是「名字 + （团队）」，而落库后读回来的是**纯名字** ——
       不剥掉后缀，这条会一直红着，而且看着像"批量改没生效"。 */
    selClear();
    await reload(); await wait(500);
    const pk2 = Array.from(document.querySelectorAll("#tree input.pick")).slice(0, 2);
    pk2.forEach((p, k) => {
      if (k === 0) p.parentElement.click();
      else p.parentElement.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
    });
    await wait(300);
    $("#bulkArtist").click();
    await wait(300);
    const pop2 = document.querySelector(".popup");
    const it2 = pop2 && Array.from(pop2.children).find(
      (d) => d.textContent && d.textContent.indexOf("清空") < 0
             && d.textContent.indexOf("新建一个外包商") < 0);
    mrep.artistPick = it2 ? it2.textContent : null;
    const pickName = (mrep.artistPick || "").replace(/（团队）$/, "");
    mrep.artistName = pickName;
    if (it2) { it2.click(); await wait(1400); }
    const d3 = await call("load");
    const artistOf = (i) => {
      let hit = null;
      (d3.groups || []).forEach((g) => (g.projects || []).forEach((p) => {
        const walk = (arr) => (arr || []).forEach((x) => {
          if (Number(x.id) === Number(i)) hit = x.artist;
          walk(x.children);
        });
        walk(p.children);
      }));
      return hit;
    };
    mrep.artistOk = !!mrep.artistPick && selCount() >= 0
      && Array.from(selNodes).every((i) => artistOf(i) === pickName);

    /* --- 2026-10-02 需求③：多选后改**任意一条选中项**的状态 / 制作人，
           其它选中项一起改（以前只有批量条上那两个按钮能改一批） --- */
    selClear(); await reload(); await wait(500);
    mrep.snapIn = treeSnap();
    {
      const rows3 = Array.from(document.querySelectorAll("#tree .row"))
        .filter((el) => el.querySelector(".pick"));
      /* 挑两条**互不包含**的：挑到父子的话，Ctrl 加选会被当成减选（坑见上面） */
      let ra = null, rb = null;
      for (let a = 0; a < rows3.length && !rb; a++) {
        for (let b = a + 1; b < rows3.length; b++) {
          const A = findNode(Number(rows3[a].dataset.id));
          const B = findNode(Number(rows3[b].dataset.id));
          if (!A || !B) continue;
          const as = selfAndDescendants(A).map(Number);
          const bs = selfAndDescendants(B).map(Number);
          if (as.includes(Number(B.id)) || bs.includes(Number(A.id))) continue;
          ra = rows3[a]; rb = rows3[b]; break;
        }
      }
      mrep.rowPair = { has: !!rb };
      if (rb) {
        const ia = Number(ra.dataset.id), ib = Number(rb.dataset.id);
        const readBy = (fn) => async () => {
          const d = await call("load");
          const get = (i) => {
            let h = null;
            (d.groups || []).forEach((g) => (g.projects || []).forEach((p) => {
              const w = (arr) => (arr || []).forEach((x) => {
                if (Number(x.id) === Number(i)) h = fn(x);
                w(x.children);
              });
              w(p.children);
            }));
            return h;
          };
          return get;
        };

        /* ① 改状态：点其中一条的状态徽章 → 选中的全都跟着改 */
        selClear(); repaintSelection(); syncBulkBar(); await wait(150);
        ra.click(); await wait(200);
        rb.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
        await wait(300);
        const idsRow = Array.from(selNodes);
        const stBefore = await readBy((x) => x.status)();
        const list = ((await call("load")).status || {}).node || [];
        /* 挑一个跟现在不一样的：都是同一个状态的话，改了也看不出区别 */
        const wantSt = list.indexOf("反馈") >= 0 && stBefore(ia) !== "反馈"
          ? "反馈" : (list.find((s) => s !== stBefore(ia)) || null);
        ra.querySelector(".badge.st").click();
        await wait(350);
        /* 2026-10-03 用户报的 bug：**点状态徽章时选择集被清空**。
           徽章那一下会冒泡到整行，被 multiClick 当成"一次选择点击"→
           选择集只剩这一条；而 ids 是冒泡前就抓好的（多条），于是出现
           "确实改了一批、屏幕上却只剩一个勾着"。徽章里补了 stopPropagation，
           这里钉住：菜单还开着的时候，选择集必须还是原来那几条。
           （无修饰键点击的语义 2026-10-10 从"单选"改成了"加选"，
           这条断言两种语义下都成立 —— 它要的是"没被改掉"，不是"被清空"。） */
        mrep.rowStatKeep = {
          sel: selCount(), want: idsRow.length,
          ok: idsRow.length >= 2 && selCount() === idsRow.length,
        };
        const popS = document.querySelector(".popup");
        const itS = popS && wantSt
          ? Array.from(popS.children).find((d) => d.textContent.indexOf(wantSt) >= 0) : null;
        if (itS) { itS.click(); await wait(1500); }
        const stAfter = await readBy((x) => x.status)();
        /* 光"所有选中项都等于目标状态"不够 —— 它们本来就全是那个状态的话也成立。
           所以还要一条：**除了被点的那条，至少还有一条真的从别的值改过来**。 */
        const others = idsRow.filter((i) => i !== ia);
        mrep.rowStat = {
          menuOk: !!itS, want: wantSt, ids: idsRow,
          before: idsRow.map(stBefore), got: idsRow.map(stAfter),
          someoneElseMoved: others.some((i) => stBefore(i) !== wantSt),
          ok: !!itS && !!wantSt && idsRow.length >= 2
              && idsRow.every((i) => stAfter(i) === wantSt)
              && others.some((i) => stBefore(i) !== wantSt),
        };

        /* ② 改制作人：直接点行上那块「制作人 X」。
           两步准备，让断言不至于"本来就成立"：
             a) 先给 ra 一个制作人 → 行上才会渲染出 `.meta.who`（没制作人的行不画这块）；
             b) 菜单里的候选现在来自**外包商名录**（全局的，2026-10-16 起强关联），
                不再依赖"库里已经有人用过这个名字"。所以现造两条临时条目 ——
                跑完这一段立刻删掉，别给后面的外包商断言留游离条目。
           另外 ⋯ 菜单那条路仍然要试（`via` 会记下来），两条路都得通。 */
        const others3 = Array.from(document.querySelectorAll("#tree .row"))
          .filter((el) => el.querySelector(".pick"))
          .map((el) => Number(el.dataset.id))
          .filter((i) => i !== ia && i !== ib);
        const vOld = await call("vendor_save", 0, { name: "甲原始人" });
        const vPick = await call("vendor_save", 0, { name: "甲" });
        mrep.vOldId = (vOld || {}).id || 0;
        mrep.vPickId = (vPick || {}).id || 0;
        await call("set_field", "node", ia, "artist_id", mrep.vOldId);
        await reload(); await wait(500);
        let idsA = [];
        const ra2 = nodeRow(ia), rb2 = nodeRow(ib);
        mrep.rowArtist = { has: !!(ra2 && rb2) };
        if (ra2 && rb2) {
          selClear(); repaintSelection(); syncBulkBar(); await wait(150);
          ra2.click(); await wait(200);
          rb2.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
          await wait(300);
          idsA = Array.from(selNodes);
          const whoAfter0 = await readBy((x) => x.artist)();
          const beforeA = idsA.map(whoAfter0);
          const whoEl = ra2.querySelector(".meta.who");
          mrep.rowArtist.via = whoEl ? "meta.who" : "more-menu";
          if (whoEl) {
            whoEl.click();
          } else {
            ra2.querySelector(".mini.more").click();
            await wait(350);
            const pm = document.querySelector(".popup");
            const mi = pm && Array.from(pm.children).find(
              (d) => d.textContent.indexOf("编辑制作人") >= 0);
            mrep.rowArtist.menuItem = !!mi;
            if (mi) { mi.click(); await wait(400); }
          }
          await wait(350);
          const popA = document.querySelector(".popup");
          /* 挑「甲」这个候选（跳过「未分配（清空）」「新建一个外包商…」）。
             ⚠ 候选行现在是「✓ 名字」/「　名字」，trim 会把全角空格一起去掉
             （U+3000 属于空格类，String.trim 认它）。 */
          mrep.menuA = popA ? Array.from(popA.children).map((d) => d.textContent) : null;
          const itA = popA ? Array.from(popA.children).find(
            (d) => d.textContent && d.textContent.trim() === "甲") : null;
          mrep.hasNewVendor = !!(popA && Array.from(popA.children).some(
            (d) => (d.textContent || "").indexOf("新建一个外包商") >= 0));
          if (itA) { itA.click(); await wait(1500); }
          await reload(); await wait(400);
          const whoAfter = await readBy((x) => x.artist)();
          const gotA = idsA.map(whoAfter);
          const othersA = idsA.filter((i) => i !== ia);
          mrep.rowArtist.pick = "甲";
          mrep.rowArtist.ids = idsA;
          mrep.rowArtist.before = beforeA;
          mrep.rowArtist.got = gotA;
          mrep.rowArtist.someoneElseMoved = othersA.some(
            (i) => beforeA[idsA.indexOf(i)] !== "甲");
          mrep.rowArtist.ok = !!itA && idsA.length >= 2
            && gotA.every((v) => v === "甲")
            && othersA.some((i) => beforeA[idsA.indexOf(i)] !== "甲");
        }
        /* 收拾：把这一段现造的两个外包商带走。
           先把挂上去的清掉 —— 还有环节挂着的话会被"还有 N 个环节"拦下。 */
        {
          const touched = Array.from(new Set([ia].concat(idsA)));
          for (const i of touched) await call("set_field", "node", i, "artist_id", 0);
          if (mrep.vOldId) await call("vendor_delete", mrep.vOldId);
          if (mrep.vPickId) await call("vendor_delete", mrep.vPickId);
          mrep.vendorsCleaned = !((await call("load", 1)).vendors || [])
            .some((v) => v.name === "甲" || v.name === "甲原始人");
        }

        /* ③④ 截止日期 / 备注：跟制作人**完全同构**，所以抽一段共用流程。
           两步准备（跟上面 ② 一个道理）：
             a) 给 ia 一个值 → 行上才会画出可点的那一块（没值的行不画那块）；
             b) 把目标值 seed 到一条**不在本次选择里**的环节 → 菜单里有这个候选，
                而且它是别人都没有的，选中项被改成它就一定是真改动。
           `inMenu` 单独记下来：候选菜单有取条数上限，万一 seed 的值没进菜单，
           红灯能看到是"候选没进去"还是"改没生效"。 */
        const runBulkField = async (field, cls, menuLabel, seedMine, pickValue) => {
          const out = { field: field, pick: pickValue };
          selClear(); await reload(); await wait(400);
          await call("set_field", "node", ia, field, seedMine);
          if (others3.length) await call("set_field", "node", others3[0], field, pickValue);
          await reload(); await wait(400);
          const r1 = nodeRow(ia), r2 = nodeRow(ib);
          out.has = !!(r1 && r2);
          out.snapIn = treeSnap();      // 每步开工时的树形态（哪一步开始变的一眼可见）
          if (!r1 || !r2) {
            out.miss = { ia: ia, ib: ib, r1: !!r1, r2: !!r2, snap: treeSnap() };
            return out;
          }
          out.inMenu = fieldValues(field).indexOf(pickValue) >= 0;
          selClear(); repaintSelection(); syncBulkBar(); await wait(150);
          r1.click(); await wait(200);
          r2.dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true }));
          await wait(300);
          const ids = Array.from(selNodes);
          const beforeF = await readBy((x) => x[field])();
          const before = ids.map(beforeF);
          const el = r1.querySelector(cls);
          out.via = el ? cls : "more-menu";
          if (el) {
            el.click();
          } else {
            r1.querySelector(".mini.more").click(); await wait(350);
            const pm = document.querySelector(".popup");
            const mi = pm && Array.from(pm.children).find(
              (d) => d.textContent.indexOf(menuLabel) >= 0);
            out.menuItem = !!mi;
            if (mi) { mi.click(); await wait(400); }
          }
          await wait(350);
          const pop = document.querySelector(".popup");
          const it = pop ? Array.from(pop.children).find(
            (d) => d.textContent && d.textContent.trim() === pickValue) : null;
          out.menuOk = !!it;
          if (it) { it.click(); await wait(1500); }
          await reload(); await wait(400);
          const afterF = await readBy((x) => x[field])();
          const got = ids.map(afterF);
          const others = ids.filter((i) => i !== ia);
          out.ids = ids; out.before = before; out.got = got;
          out.someoneElseMoved = others.some((i) => before[ids.indexOf(i)] !== pickValue);
          out.ok = !!it && ids.length >= 2 && got.every((v) => v === pickValue)
                   && out.someoneElseMoved;
          return out;
        };
        mrep.rowDue = await runBulkField("deadline", ".meta.due", "编辑截止日期",
                                         "2026-11-11", "2027-12-31");
        mrep.rowNote = await runBulkField("note", ".meta.note", "编辑备注",
                                          "甲备注原始", "批量备注A");
      }
    }
    selClear(); repaintSelection(); syncBulkBar(); await wait(150);

    /* --- #3c 批量删除（2026-10-10）---
       跟单条删除一样，**必须真点按钮**，不能直调 call("bulk_delete")：
       否则"按钮没绑上 / 确认框没弹 / 计数报错"这类问题又全靠肉眼。
       样本自己建（一条父 + 两个子，故意让勾父项就级联带走子项），
       专门用来验「确认框说的条数」和「后端真删了几条」对得上。 */
    {
      const drepDel = {};
      const prjDel = ((D.groups.find((g) => g.key === "commercial") || {}).projects || [])
        .map((p) => p.id)[0];
      const mkP = await call("add_node", prjDel, null, "批删样本父");
      const pid = mkP && mkP.id;
      const mkC1 = pid ? await call("add_node", prjDel, pid, "批删样本子1") : null;
      const mkC2 = pid ? await call("add_node", prjDel, pid, "批删样本子2") : null;
      await reload();
      await wait(400);
      drepDel.made = !!(pid && mkC1 && mkC1.ok && mkC2 && mkC2.ok);
      drepDel.snapIn = treeSnap();

      if (drepDel.made) {
        /* 勾父项 → 级联带上两个子项，选择集 3 条 */
        const prow = nodeRow(Number(pid));
        drepDel.rowFound = !!prow && !!prow.querySelector(".pick");
        if (!drepDel.rowFound) drepDel.miss = { pid: pid, snap: treeSnap() };
        if (drepDel.rowFound) {
          selClear();
          prow.querySelector(".pick").dispatchEvent(
            new MouseEvent("click", { bubbles: true, cancelable: true }));
          await wait(300);
          drepDel.sel = selCount();
          /* 后端算的"会删掉几条"：叶子是 3（父+2子），
             但祖先已进集合，所以根只有 1 个 —— 界面上说的是选中数 */
          drepDel.descendants = descendantsCountFor(Array.from(selNodes));

          /* confirm 换成替身：原生弹窗会卡死 JS 线程、收尾的 boot_ok 就发不出去 */
          const realConfirm = window.confirm, realErr = window.__err;
          let asked = 0, askedMsg = "";
          window.confirm = (m) => { asked++; askedMsg = String(m || ""); return true; };
          try {
            $("#bulkDel").click();
            await wait(900);                    // 等跨桥往返 + reload
          } finally {
            window.confirm = realConfirm;
            window.__err = realErr;
          }
          drepDel.asked = asked;
          drepDel.askedMsg = askedMsg;
          drepDel.errAfter = null;             // 下面从 DOM 复核，不靠 __err

          /* 复核：三个环节都该从库里没了 */
          const after = await call("load");
          const gone = (pid) => {
            for (const g of after.groups || []) {
              for (const p of g.projects || []) {
                const walk = (arr) => (arr || []).some(
                  (x) => Number(x.id) === Number(pid) || walk(x.children));
                if (walk(p.children)) return false;
              }
            }
            return true;
          };
          drepDel.parentGone = gone(pid);
          drepDel.kid1Gone = gone(mkC1.id);
          drepDel.kid2Gone = gone(mkC2.id);
          /* 删完选择集要清空、批量条不能挂着幽灵计数 */
          drepDel.selAfter = selCount();
          drepDel.cntAfter = ($("#bulkCnt") || {}).textContent || "";
          drepDel.ok = asked === 1 && drepDel.parentGone
            && drepDel.kid1Gone && drepDel.kid2Gone && selCount() === 0;
        }
      }
      mrep.bulkDel = drepDel;
      selClear(); repaintSelection(); syncBulkBar(); await wait(150);
    }

    /* 「清空」全部取消；「退出多选」复选框消失 */
    $("#bulkNone").click(); await wait(300);
    mrep.none = selCount();
    $("#bulkExit").click(); await wait(400);
    mrep.pickGone = n("#tree input.pick");
    mrep.barGone = getComputedStyle($("#bulkBar")).display;
    rep.multi = mrep;

  });

  /* --- #6 数据文件 / 多机开关：只验接口与校验，不真重启（2026-10-13 从 multi 里拆出来）---
     原本它跟多选共用一个 30 秒预算，两者都偏重，实测就顶着预算超时。 */
  await phase("files", 30000, async () => {
    await step("files");
    /* --- #6 数据文件：面板上要能改（不真重启，只验接口与校验） --- */
    const drep = {};
    rep.dbFile = drep;
    const info = await call("db_info");
    drep.from = info.from;
    drep.pathOk = !!info.path;
    drep.btn = n("#setPathPick");
    drep.btnDefault = n("#setPathDefault");
    /* 同一个文件 → 明确拒绝 */
    const same = await call("set_db_file", info.path);
    drep.sameRejected = same.ok === false && !!(same.msg);
    /* 不存在的目录 → 拒绝。
       ⚠ 这里**必须拼本机路径**，别写死 `Z:\...` 这种不存在的盘符：
       本机若真把 Z: 映射到一个**已断开**的网络盘，`os.path.isdir` 会触发
       Windows 重连等待（实测 21 秒），自检的 75 秒预算当场被吃光 ——
       表现是"页面卡死在某个阶段"，查半天其实跟被测功能毫无关系（2026-09-30 踩）。 */
    const curDir = String(info.path || "").replace(/[\\/][^\\/]*$/, "");
    const badDirPath = curDir ? curDir + "\\__no_such_dir__\\x.sqlite" : "";
    const badDir = badDirPath ? await call("set_db_file", badDirPath) : { ok: false, msg: "(跳过)" };
    drep.badDirRejected = badDir.ok === false && !!(badDir.msg);
    /* 空路径 → 拒绝（别把配置写成一个空串，那样下次启动会掉回默认库） */
    const empty = await call("set_db_file", "   ");
    drep.emptyRejected = empty.ok === false && !!(empty.msg);
    /* 注意：**不要**在这里真去 set_db_file 一个合法的新路径 —— 那会写进
       data/config.json，下次启动这台机器就读到别的库了。冒烟只验"拒绝"那一半。 */

    /* --- 多机同时打开：开关在、状态接口结构齐 --- */
    drep.shareBtn = n("#setShare");
    const before = !!info.share;
    /* 把**当前值**写回去：接口能通就行，真实配置的取值不变 */
    const sset = await call("set_share_db", before);
    drep.setShare = !!(sset && sset.ok && sset.share === before);
    const ss = await call("sync_state");
    drep.sync = !!(ss && ss.ok && ss.share === before
      && typeof ss.changed === "boolean" && Array.isArray(ss.holders));
    drep.syncBar = n("#syncBar");
    rep.dbFile = drep;
  });

  /* 真点一次「新建项目 + 合同额 + 首款」，验证 DOM 取值 → API → 落库整条链路。
     放在最后，免得新增数据影响前面的渲染计数。--smoke 用的是临时库，动不到真数据。 */
  await phase("create", 15000, async () => {
    await step("create");
    /* 万一走的是 alert 分支，弹窗会阻塞 JS 线程让收尾的 boot_ok 也发不出去，
       所以这一小段把 alert 换成记录 */
    const realAlert = window.alert;
    window.alert = (m) => { window.__alert = String(m); };
    try {
      openNewDialog();
      await wait(200);
      $("#newTitle").value = "冒烟-款项项目";
      $("#newClient").value = "冒烟客户";        // 已有客户：按名字挂上
      $("#newAmount").value = "1234";
      $("#newPaid").value = "234";
      /* 截止日期是原生 date 框：直接置 ISO 就行，库里该是 2026-12-31。
         顺带验"有值时自绘占位必须让位"—— 那个 YYYY/MM/DD 只在空值时顶在框里
         （有值时原生自己画的 2026/12/31 正是要的格式，不能拿占位去盖）。 */
      $("#newDl").value = "2026-12-31";
      syncDph($("#newDl"));
      const duePhWithVal = getComputedStyle($("#newDlPh")).display;
      /* 结款日期（2026-10-13 新增）走同一套：填了就得落库、占位要让位 */
      $("#newSettle").value = "2027-01-15";
      syncDph($("#newSettle"));
      const settlePhWithVal = getComputedStyle($("#newSettlePh")).display;
      $("#newOk").click();
      await wait(2200);
      const f2 = await call("finance_data", 0, 0, "CNY");
      const p = (f2.projects || []).find((x) => x.project === "冒烟-款项项目");
      rep.create = p
        ? { ok: true, contract: p.contract, received: p.received,
            pending: p.pending, client: p.client }
        : { ok: false, alert: window.__alert || null,
            projects: (f2.projects || []).map((x) => x.project) };
      const dDl = await call("load");
      let pDl = null;
      (dDl.groups || []).forEach((g) => (g.projects || []).forEach((x) => {
        if (x.title === "冒烟-款项项目") pDl = x;
      }));
      rep.create.dueIn = "2026-12-31";
      rep.create.dueStored = pDl ? pDl.deadline : null;
      rep.create.duePhWithVal = duePhWithVal;
      /* 结款日期也得真落库 —— 只在新建页填了、没写进去的话，行上永远不报结款逾期，
         而这种"少发一条 set_field"的问题肉眼根本看不出来 */
      rep.create.settleIn = "2027-01-15";
      rep.create.settleStored = pDl ? pDl.settle_date : null;
      rep.create.settlePhWithVal = settlePhWithVal;

      /* 原生 date 框**天然写不进非法日期**：塞「下周三」「2026/12/31」进去 value 都会变成
         空串（只有 ISO 收得下）。所以手写框那套"格式写错就拦下"的校验连同 normDay 一起删了 ——
         这种失败在原生框上根本发生不了。
         顺带：重开弹窗后自绘占位该回来（openNewDialog 里 value="" + syncDph()）。 */
      openNewDialog();
      await wait(200);
      rep.create.duePhBack = getComputedStyle($("#newDlPh")).display;
      /* 上一个项目的结款日期不能留在框里 —— 新建页 reset 漏清这一个，
         下个项目就会莫名其妙带上前一个的结款日 */
      rep.create.settlePhBack = getComputedStyle($("#newSettlePh")).display;
      rep.create.settleLeft = $("#newSettle").value || "";
      const badEl = $("#newDl");
      badEl.value = "下周三"; rep.create.rejectCn = badEl.value;
      badEl.value = "2026/12/31"; rep.create.rejectSlash = badEl.value;
      badEl.value = "2026-12-31"; rep.create.acceptIso = badEl.value;

      /* 再建一个：客户框里写一个库里没有的名字，应当自动建客户并挂上。
         注意这里要看 load 而不是 finance_data —— 财务列表只列有款项的项目，
         没记钱的项目不在里面。 */
      openNewDialog();
      await wait(200);
      $("#newTitle").value = "冒烟-新客户项目";
      $("#newClient").value = "冒烟新客户X";
      $("#newOk").click();
      await wait(2200);
      const d3 = await call("load");
      let p3 = null;
      (d3.groups || []).forEach((g) => (g.projects || []).forEach((x) => {
        if (x.title === "冒烟-新客户项目") p3 = x;
      }));
      rep.create.newClient = p3 ? p3.client : null;
      rep.create.clientCount = (d3.clients || []).length;
    } finally {
      window.alert = realAlert;
      modal("#ovNew", false);
    }
  });

  /* 客户删除：面板里要有删除入口，且"还有项目挂着"时会被后端挡住并把原因说出来。
     这个 phase 净增/净减为 0（自己造一个客户、再删掉），不影响上面 create 的客户计数。 */
  await phase("clients", 20000, async () => {
    await step("clients");
    const crep = {};
    /* confirm/alert 在这个 phase 里换成不阻塞的：原生弹窗会卡住 JS 线程，
       收尾的 boot_ok 就永远发不出去（Python 侧只能干等超时）。 */
    const realConfirm = window.confirm, realAlert = window.alert;
    window.confirm = () => true;
    window.alert = (m) => { window.__alertDel = String(m); };
    try {
      /* 造一个没人挂靠的客户，专门拿来删（cid 传 0 = 新建） */
      const mk = await call("client_save", 0, { name: "冒烟待删客户", settlement_cycle: "月结" });
      crep.made = mk && mk.ok === true;
      await loadFinance();
      renderClients();

      /* ① 开面板。删除入口已从弹窗底部挪到列表行的 ⋯ 菜单里，
            所以这里反过来钉两件事：底部那颗按钮**不在了**、结算周期那句提示也撤了。 */
      $("#finClientsBtn").click();
      await wait(250);
      crep.opened = getComputedStyle($("#ovCli")).display !== "none";
      crep.delBtnGone = !document.querySelector("#cliDel");
      crep.subGone = !document.querySelector("#ovCli .sub");
      /* 列表里每一行右边都得有那颗 ⋯（少了就等于删不掉那个客户） */
      crep.ops = document.querySelectorAll("#cliList [data-cli]").length;
      crep.rows = document.querySelectorAll("#cliList .ftrow.body").length;

      const pickRow = (name) => Array.from($("#cliList").querySelectorAll("[data-cli]"))
        .find((el) => {
          const nm = el.parentElement.querySelector(".nm");
          return nm && nm.textContent === name;
        });
      /* ⚠ 每次都**按名字重捞**行再点：上一轮里 __cliEdit 会把表单填上，
         而删客户之后 renderClients 会整块重画，旧节点成了脱离 DOM 的快照，
         拿旧引用再点一下就点了个寂寞（菜单不弹，断言看着"没反应"）。 */
      const menuOf = async (name) => {
        const r = pickRow(name);
        if (!r) return null;
        r.click();
        await wait(220);
        const pop = document.querySelector(".popup");
        return pop ? Array.from(pop.children) : null;
      };
      const item = (kids, txt) => (kids || []).find((d) => d.textContent === txt);

      /* ② ⋯ 菜单里必须有两个入口：编辑客户 / 删除客户 */
      const kids = await menuOf("冒烟待删客户");
      crep.rowFound = !!pickRow("冒烟待删客户");
      crep.menu = kids ? kids.map((d) => d.textContent) : null;
      crep.hasEdit = !!item(kids, "编辑客户");
      crep.hasDel = !!item(kids, "删除客户");
      /* 弹窗是 z-index 200 的遮罩，菜单得画在它上面才点得到（.popup 必须是 300）。
         光看"菜单在 DOM 里"没用 —— 老值 100 时菜单照样建出来，只是人看不见、点不着。 */
      const pp = document.querySelector(".popup");
      crep.popZ = pp ? parseInt(getComputedStyle(pp).zIndex, 10) : 0;
      crep.popTop = pp ? pp.getBoundingClientRect().top : 0;
      /* 破坏性动作必须染红：跟旁边「编辑客户」一个颜色的话，手快点下去就没了。
         比"两行颜色不同 + 红通道最大"而不是钉死色值 —— 明暗两套主题的 --red 不一样。 */
      const dEl = item(kids, "删除客户"), eEl = item(kids, "编辑客户");
      crep.delColor = dEl ? getComputedStyle(dEl).color : "";
      const _rgb = (crep.delColor.match(/\d+/g) || [0, 0, 0]).map(Number);
      crep.delRed = !!dEl && !!eEl
        && crep.delColor !== getComputedStyle(eEl).color
        && _rgb[0] > _rgb[1] && _rgb[0] > _rgb[2];

      /* ③ 「编辑客户」这条老路不能因为多了一层菜单就丢 */
      const ed = item(kids, "编辑客户");
      if (ed) { ed.click(); await wait(250); }
      crep.nameFilled = $("#cliName").value === "冒烟待删客户";
      crep.saveTxt = $("#cliSave").textContent;

      /* ④ 重开菜单点「删除客户」→ 客户从列表消失、表单清空、没有报错弹窗 */
      window.__alertDel = null;
      const kids2 = await menuOf("冒烟待删客户");
      const del2 = item(kids2, "删除客户");
      crep.menu2 = kids2 ? kids2.map((d) => d.textContent) : null;
      if (del2) {
        del2.click();
        await wait(800);
        const after = await call("finance_data", 0, 0, "CNY");
        crep.deleted = !(after.clients || []).some((c) => c.name === "冒烟待删客户");
        crep.delAlert = window.__alertDel;
        crep.formCleared = $("#cliName").value === "";
        crep.delOk = crep.deleted && !crep.delAlert && crep.formCleared;
      }

      /* ⑤ 还有项目挂靠的客户必须删不掉，并把原因 alert 出来（不是静默失败）。
            走的是同一个入口 —— 菜单里照样给"删除客户"，被拒的话落到 alert 上。 */
      await loadFinance();
      renderClients();
      const busy = (fin.clients || []).find((c) => c.name === "冒烟客户");
      crep.busyFound = !!busy;
      if (busy) {
        window.__alertDel = null;
        const kids3 = await menuOf("冒烟客户");
        const del3 = item(kids3, "删除客户");
        crep.busyHasDel = !!del3;
        if (del3) { del3.click(); await wait(800); }
        crep.busyMsg = window.__alertDel;
        crep.busyBlocked = !!window.__alertDel && /项目/.test(window.__alertDel);
        /* 再问一次后端：确认真的还在（没被偷偷删掉） */
        const still = await call("client_delete", busy.id);
        crep.stillRefused = still.ok === false;
      }
      modal("#ovCli", false);
    } finally {
      window.confirm = realConfirm;
      window.alert = realAlert;
    }
    rep.clients = crep;
  });

  /* 外包商 / 制作人名录（2026-10-16）。跟客户并排、同一套形态，所以自检也照着写。
     净增/净减为 0（自己造一个、再删掉），不影响前面 create 阶段的客户计数。
     ⚠ 三条都真点按钮 / 真落库：
       ① 列表面板的 ⋯ 菜单（编辑 / 删除）—— 只有真点才测得出"菜单没接上"；
       ② 项目设置页填一个新名字 → ensureVendor **现建现挂** ——
          这是本轮最容易假绿的一处（界面看着有名字，库里可能还是个空 id）；
       ③ 「记一笔」选到「外包支出」时，外包商行要出现、且空着不给存。 */
  await phase("vendor", 25000, async () => {
    await step("vendor");
    const vr = {};
    const errBefore = window.__err;
    /* 原生 confirm/alert 会卡住 JS 线程，收尾的 boot_ok 就永远发不出去 */
    const realConfirm = window.confirm, realAlert = window.alert;
    window.confirm = () => true;
    window.alert = (m) => { window.__alertVdr = String(m); };
    try {
      const vlist = async () => ((await call("load", 1)).vendors || []);
      const pickRow = (name) => Array.from($("#vdrList").querySelectorAll("[data-vdr]"))
        .find((el) => {
          const nm = el.parentElement.querySelector(".nm");
          return nm && nm.textContent === name;
        });
      /* ⚠ 每次都**按名字重捞**行再点：删/存之后列表整块重画，
         拿旧引用点一下是点了个脱离 DOM 的快照（菜单不弹，看着像"没反应"）。 */
      const menuOf = async (name) => {
        const r = pickRow(name);
        if (!r) return null;
        r.click();
        await wait(220);
        const pop = document.querySelector(".popup");
        return pop ? Array.from(pop.children) : null;
      };
      const item = (kids, txt) => (kids || []).find((d) => d.textContent === txt);
      /* 用真的 input 事件改金额 —— 直接设 value 不触发 syncFinNet 那些绑定 */
      const fire = (sel, v) => {
        const el = $(sel);
        el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      };

      /* 冒昧先记两个只读事实：种子里的三个外包商在不在、备选里有没有 */
      vr.seeded = (await vlist()).map((v) => v.name);
      vr.optsHasSeed = Array.from(document.querySelectorAll("#vendorOpts option"))
        .map((o) => o.value);

      /* ① 造一个没人挂靠的外包商，专门拿来删（vid 传 0 = 新建） */
      const mk = await call("vendor_save", 0, { name: "冒烟待删外包商", kind: "团队" });
      vr.made = !!(mk && mk.ok);
      const throwId = (mk || {}).id || 0;
      await loadFinance();

      /* ② 点财务页头那颗「外包商」（不是直调函数）→ 面板要开、列表要有行 */
      $("#finVendorsBtn").click();
      await wait(320);
      vr.opened = getComputedStyle($("#ovVendor")).display !== "none";
      vr.ops = document.querySelectorAll("#vdrList [data-vdr]").length;
      vr.rows = document.querySelectorAll("#vdrList .ftrow.body").length;
      vr.head = Array.from(document.querySelectorAll("#vdrList .ftrow.head > div"))
        .map((e) => e.textContent);
      /* 「挂靠」那列 2026-10-16 起**只显示挂靠项目数**（用户要求精简，去掉「N 环节 · K 笔款」）。
         ⚠ 显示精简了删除保护没松：后端 vendor_delete 仍盯项目 / 环节 / 款项三处。
         ⚠ pickRow 返回的是那颗 `⋯`（`[data-vdr]`），不是整行 ——
         要取第 3 个格子得先 parentElement 回到行上（直接 `r.children[2]` 永远是空，
         而症状只是"这条断言红着"，看着像挂靠数列没画）。 */
      const rowCells = () => Array.from(document.querySelectorAll("#vdrList .ftrow.body"))
        .map((r) => Array.from(r.children).map((c) => c.textContent));
      vr.listCells = rowCells();
      vr.hangCells = rowCells().map((c) => c[2]);       // 每行第 3 格（整列）
      /* 挂了项目的那个（冒烟制作人 →「1 项目」） */
      vr.hangCell = (() => {
        const r = pickRow("冒烟制作人");
        const row = r ? r.parentElement : null;
        return row && row.children[2] ? row.children[2].textContent : null;
      })();
      /* 兜底口径：万一按名字捞不着，直接从行数组里找那一行（下次红的时候能看出
         "是没渲染出来" 还是 "名字对不上"）。 */
      vr.hangCell2 = (() => {
        const row = rowCells().find((c) => c[0] === "冒烟制作人");
        return row ? row[2] : null;
      })();
      /* 0 项目的那个（冒烟工作室有 2 笔款、没挂项目）→ 现在是「—」，
         正好证明「笔款 / 环节」不再混进这一列。 */
      vr.hangCellNone = (() => {
        const row = rowCells().find((c) => c[0] === "冒烟工作室");
        return row ? row[2] : null;
      })();

      /* ③ ⋯ 菜单：编辑 / 删除都要在，删除必须染红 */
      const kids = await menuOf("冒烟待删外包商");
      vr.rowFound = !!pickRow("冒烟待删外包商");
      vr.menu = kids ? kids.map((d) => d.textContent) : null;
      vr.hasEdit = !!item(kids, "编辑外包商");
      vr.hasDel = !!item(kids, "删除外包商");
      vr.popZ = (() => {
        const pp = document.querySelector(".popup");
        return pp ? parseInt(getComputedStyle(pp).zIndex, 10) : 0;
      })();
      const dEl = item(kids, "删除外包商"), eEl = item(kids, "编辑外包商");
      vr.delColor = dEl ? getComputedStyle(dEl).color : "";
      const _rgb = (vr.delColor.match(/\d+/g) || [0, 0, 0]).map(Number);
      vr.delRed = !!dEl && !!eEl && vr.delColor !== getComputedStyle(eEl).color
        && _rgb[0] > _rgb[1] && _rgb[0] > _rgb[2];

      /* ④ 「编辑外包商」→ 四栏回填、按钮文案变「更新」、两档能切 */
      const ed = item(kids, "编辑外包商");
      if (ed) { ed.click(); await wait(280); }
      vr.nameFilled = $("#vdrName").value;
      vr.kindFilled = (document.querySelector("#vdrKind button.act") || { dataset: {} }).dataset.k;
      vr.saveTxt = $("#vdrSave").textContent;
      vr.contactFilled = $("#vdrContact").value;
      vr.noteFilled = $("#vdrNote").value;
      /* 另挑一个有联系方式的（冒烟工作室写了 wx:smoke），确认那两栏也真回填 */
      const kidsW = await menuOf("冒烟工作室");
      const edW = item(kidsW, "编辑外包商");
      if (edW) { edW.click(); await wait(280); }
      vr.contactW = $("#vdrContact").value;
      vr.noteW = $("#vdrNote").value;
      /* 类型只有两档：点「个人」得能换过去 */
      const kb = document.querySelector('#vdrKind button[data-k="个人"]');
      if (kb) { kb.click(); await wait(150); }
      vr.kindSwitch = (document.querySelector("#vdrKind button.act") || { dataset: {} }).dataset.k;
      /* ⚠ 刚把冒烟工作室点成「个人」了但**没点保存** ——
         不改回来会让⑤之后的断言以为它还是团队。清一下表单即可。 */
      $("#vdrReset").click();
      await wait(150);
      vr.resetCleared = $("#vdrName").value === "" && $("#vdrSave").textContent === "保存外包商";

      /* ⑤ 改个名字：列表要刷新、库里要变，而**挂靠关系不能断**
             （改名断关联 = 关联白做，正是本轮改动最该防的那个坑）。
         ⚠ 先自己挂一处上去 —— 前面的多选阶段把环节的制作人都清空了，
           靠种子里那一处去捞会捞到空（而且"捞不到"会伪装成"改名断了关联"）。
           这里挂在**项目**上：项目行永远画着，`artist` 名字看得见。 */
      const xg = (await vlist()).find((v) => v.name === "冒烟小工") || {};
      vr.xgId = xg.id || 0;
      const flatP = async () => (await call("load", 1)).groups
        .reduce((a, g) => a.concat(g.projects), []);
      const prjId = ((await flatP())[0] || {}).id || 0;
      vr.prjId = prjId;
      if (prjId && vr.xgId) {
        await call("set_field", "project", prjId, "artist_id", vr.xgId);
        await reload(); await wait(400);
      }
      const p0 = (await flatP()).find((p) => Number(p.id) === Number(prjId)) || {};
      vr.nodeBeforeId = p0.artist_id || 0;
      vr.nodeBeforeName = p0.artist || null;

      const kids2 = await menuOf("冒烟小工");
      const ed2 = item(kids2, "编辑外包商");
      if (ed2) { ed2.click(); await wait(280); }
      $("#vdrName").value = "冒烟小工V2";
      $("#vdrSave").click();
      await wait(1500);
      const vl2 = await vlist();
      vr.renamed = vl2.some((v) => v.name === "冒烟小工V2")
        && !vl2.some((v) => v.name === "冒烟小工");
      vr.renameKeptId = (vl2.find((v) => v.name === "冒烟小工V2") || {}).id || 0;
      vr.renameSameId = vr.renameKeptId === vr.nodeBeforeId;
      /* 项目行上的「制作人」也要跟着改（保存后没有 reload 就会留着旧名） */
      const p1 = (await flatP()).find((p) => Number(p.id) === Number(prjId)) || {};
      vr.renameTreeName = p1.artist || null;
      vr.renameKeptLink = (p1.artist_id || 0) === (p0.artist_id || 0);
      vr.renameRowTxt = (() => {
        const r = document.querySelector(
          '#tree .row[data-kind="project"][data-id="' + Number(prjId) + '"]');
        const w = r ? r.querySelector(".meta.who") : null;
        return w ? w.textContent : null;
      })();
      vr.dlgStillOpen = getComputedStyle($("#ovVendor")).display !== "none";

      /* ⑥ 删除保护：还有外包支出挂着的删不掉，原因要说到点上 */
      window.__alertVdr = null;
      const kids3 = await menuOf("冒烟工作室");
      const del3 = item(kids3, "删除外包商");
      vr.busyHasDel = !!del3;
      if (del3) { del3.click(); await wait(900); }
      vr.busyMsg = window.__alertVdr;
      vr.busyBlocked = !!window.__alertVdr && /外包支出/.test(window.__alertVdr);
      vr.busyStill = (await vlist()).some((v) => v.name === "冒烟工作室");

      /* ⑦ 删得掉的那个：从列表消失、表单清空、没有报错弹窗 */
      window.__alertVdr = null;
      const kids4 = await menuOf("冒烟待删外包商");
      const del4 = item(kids4, "删除外包商");
      if (del4) { del4.click(); await wait(900); }
      vr.deleted = !(await vlist()).some((v) => v.name === "冒烟待删外包商");
      vr.delAlert = window.__alertVdr;
      vr.formCleared = $("#vdrName").value === "";
      modal("#ovVendor", false);
      await wait(150);

      /* ⑧ 「记一笔」：外包商行只跟「外包支出」绑在一起。
            原来"打开弹窗"和"切类型"两处各写了一遍显隐，加这行必然漏一处 ——
            所以这里把三个类型都切一遍。 */
      openFinDialog(null);
      await wait(500);
      const vis = () => getComputedStyle($("#finVendorRow")).display !== "none";
      vr.vdrRowOnPayment = vis();
      document.querySelector('#finKind button[data-k="outsource"]').click();
      await wait(200);
      vr.vdrRowOnOutsource = vis();
      document.querySelector('#finKind button[data-k="invoice"]').click();
      await wait(200);
      vr.vdrRowOnInvoice = vis();
      document.querySelector('#finKind button[data-k="outsource"]').click();
      await wait(200);
      vr.vdrRowBack = vis();

      /* ⑨ 空着不给存：提示要出现、弹窗不关、库里也不能多出记录 */
      const recsBefore = ((await call("finance_data", 0, 0, "CNY")).records || []).length;
      fire("#finAmount", "777");
      $("#finVendor").value = "";
      $("#finOk").click();
      await wait(700);
      vr.emptyToast = ($("#toast") || {}).textContent || "";
      vr.emptyKept = getComputedStyle($("#ovFin")).display !== "none";
      vr.emptyNoRec = ((await call("finance_data", 0, 0, "CNY")).records || []).length === recsBefore;

      /* ⑩ 框里填一个**名录里没有的**新名字 → 前端现建条目再挂上去。
             这条是「记一笔」侧的 ensureVendor，和项目设置页那条是同一段逻辑，
             但入口不同、值来源不同（一个是名字框、一个是 datalist），都得真跑一次。
             状态改成「应付」：验的是"刚记下的欠款立刻进汇总的应付那一列"
             （种子里那笔 5000 应付是直接写库的，没走界面）。 */
      $("#finVendor").value = "冒烟外协新人";
      $("#finStatus").value = "应付";
      $("#finOk").click();
      await wait(1800);
      vr.newMade = (await vlist()).some((v) => v.name === "冒烟外协新人");
      const fd = await call("finance_data", 0, 0, "CNY");
      /* ⚠ 按**名字**找我们刚记的那笔，不按 kind 取第一条 ——
         种子里还有两笔挂在「冒烟工作室」名下的，取第一条会断言到别人头上。 */
      const nrec = (fd.records || []).find((r) => r.vendor === "冒烟外协新人") || null;
      vr.newGot = !!nrec;
      vr.newLinked = !!nrec && nrec.vendor_kind === "个人";
      vr.newAmount = nrec ? nrec.amount : null;
      vr.newStatus = nrec ? nrec.status : null;
      /* ⑪ 汇总口径：这个新人名下一笔「应付 777」→ 已付 0 / 应付 777 / 合计 777 */
      const vsum = (fd.vendor_summary || []).find((v) => v.vendor === "冒烟外协新人") || {};
      vr.sumPaid = vsum.paid;
      vr.sumPending = vsum.pending;
      vr.sumTotal = vsum.total;
      vr.sumCount = vsum.count;
      vr.sumProjects = vsum.projects;
      /* 种子里那两笔（同一团队：一笔已付 20000 + 一笔应付 5000）必须合成**一行** */
      const vSeed = (fd.vendor_summary || []).find((v) => v.vendor === "冒烟工作室") || {};
      vr.seedPaid = vSeed.paid;
      vr.seedPending = vSeed.pending;
      vr.seedCount = vSeed.count;

      /* 收拾：把样本记录和样本外包商都带走 */
      if (nrec) { await call("finance_delete", nrec.id); }
      const nv = (await vlist()).find((v) => v.name === "冒烟外协新人");
      if (nv) { await call("vendor_delete", nv.id); }
      /* 冒烟小工改回去，别给后面留一个改了名的样本 */
      const cur = (await vlist()).find((v) => v.name === "冒烟小工V2");
      if (cur) { await call("vendor_save", cur.id, { name: "冒烟小工", kind: "个人" }); }
      /* 把项目上的制作人还回种子那个（⑤ 里借用了它） */
      const zb = (await vlist()).find((v) => v.name === "冒烟制作人");
      if (prjId && zb) await call("set_field", "project", prjId, "artist_id", zb.id);
      await reload();
      await wait(300);
      const vlEnd = await vlist();
      vr.cleaned = !vlEnd.some((v) => ["冒烟外协新人", "冒烟小工V2", "冒烟待删外包商"]
        .includes(v.name));
      /* 收尾时名录必须回到种子那三个（多一个少一个都是收拾没干净） */
      vr.backTo = vlEnd.map((v) => v.name).sort();
      vr.errNew = window.__err === errBefore ? null : window.__err;
    } finally {
      window.confirm = realConfirm;
      window.alert = realAlert;
    }
    rep.vendor = vr;
  });

  /* 日期选择弹窗（2026-10-16 新增）：环节 ⋯ 菜单的「编辑截止日期」从 prompt 手输
     换成**日历控件**（原生 date 框）。
     ⚠ 必须走真实入口（⋯ → 编辑截止日期）并**真点保存**、再回查库 ——
        只断言"弹窗画出来了"是假绿：2026-10-10 那个删除按钮绑了 ReferenceError，
        弹窗照样画、点了没反应，一路绿灯。 */
  await phase("date", 20000, async () => {
    await step("date");
    const dp = {};
    const errBefore = window.__err;
    const grp = D.groups.find((g) => (g.projects || []).length) || D.groups[0] || {};
    const p0 = (grp.projects || [])[0] || {};
    const mk = p0.id ? await call("add_node", p0.id, null, "日期弹窗样本") : null;
    dp.made = !!(mk && mk.id);
    if (!dp.made) { rep.date = dp; return; }
    const nid = Number(mk.id);
    await reload(); await wait(500);
    dp.snapIn = treeSnap();

    const readDl = async () => {
      const d = await call("load");
      let h = null;
      (d.groups || []).forEach((g) => (g.projects || []).forEach((p) => {
        const w = (arr) => (arr || []).forEach((x) => {
          if (Number(x.id) === nid) h = x.deadline || "";
          w(x.children);
        });
        w(p.children);
      }));
      return h;
    };
    dp.before = await readDl();

    /* 真点 ⋯ 菜单里那一条 —— 不是直调 openDateDialog（那测的是函数不是绑定） */
    const openViaMenu = async () => {
      const row = nodeRow(nid);
      if (!row) { dp.miss = { nid: nid, snap: treeSnap() }; return false; }
      const more = row.querySelector(".mini.more");
      dp.hasMore = !!more;
      if (!more) return false;
      more.click(); await wait(350);
      const pm = document.querySelector(".popup");
      dp.menu = pm ? Array.from(pm.children).map((d) => d.textContent) : null;
      const mi = pm && Array.from(pm.children).find(
        (d) => (d.textContent || "").indexOf("编辑截止日期") >= 0);
      dp.menuHas = !!mi;
      if (!mi) return false;
      mi.click(); await wait(400);
      return true;
    };

    /* ① 打开弹窗：标题 / 副标题 / 是个 date 框 / 空值时不给「清空」 */
    dp.opened = await openViaMenu();
    dp.dlgOpen = getComputedStyle($("#ovDate")).display !== "none";
    dp.title = ($("#dateTitle").textContent || "").trim();
    dp.sub = ($("#dateSub").textContent || "").trim();
    dp.inputType = ($("#dateInput") || {}).type || "";
    dp.clearHiddenWhenEmpty = getComputedStyle($("#dateClear")).display === "none";
    dp.hasPh = !!document.querySelector("#ovDate .dph");
    /* 自绘占位只在「空值**且未聚焦**」时露出（style.css: `:not(.hav):not(:focus) ~ .dph`）。
       打开弹窗时输入框是聚焦的 —— 所以这里要分两态读：
         聚焦 → 让位给原生 mask（用户得看得见自己敲的数字）；
         失焦 → 才露出 YYYY/MM/DD。
       ⚠⚠ 但**「聚焦让位」不能只看渲染结果**：`:focus` 到底匹不匹配，取决于窗口
       是不是系统前台窗口。冒烟是无人值守跑的，窗口经常在后台，那时
       `activeElement` 明明是输入框、`:focus` 却不生效 → 占位照旧露着，
       于是断言在"代码一点没坏"的情况下红（2026-10-16 实测：同一份代码，
       窗口在前台时读 false、在后台时读 true，来回翻）。
       所以拆三层钉，只有第一层与窗口前台无关：
         ① activeIsInput —— focus 有没有真的给到输入框（纯 DOM 状态）
         ② focusRule    —— 样式表里那条 :not(:focus) ~ .dph 规则在不在（机制在不在）
         ③ 失焦后露出   —— 无条件成立
       渲染态（phVisibleWhileFocused）+ docHasFocus 只作诊断：前台时冒烟会
       额外严格要求它必须让位，后台时这一项自动降级为"跳过渲染断言"。 */
    dp.docHasFocus = document.hasFocus();
    dp.activeIsInput = document.activeElement === $("#dateInput");
    dp.focusRule = (() => {
      for (const ss of Array.from(document.styleSheets)) {
        let rules = null;
        try { rules = ss.cssRules; } catch (e) { continue; }   // 跨域样式表读不了，跳过
        for (const r of Array.from(rules || [])) {
          const sel = r.selectorText || "";
          if (sel.indexOf(".dph") >= 0 && sel.indexOf(":focus") >= 0) return sel;
        }
      }
      return null;
    })();
    dp.phVisibleWhileFocused = (() => {
      const ph = document.querySelector("#ovDate .dph");
      return ph ? getComputedStyle(ph).display !== "none" : null;
    })();
    $("#dateInput").blur();
    await wait(150);
    dp.phVisibleWhenEmpty = (() => {
      const ph = document.querySelector("#ovDate .dph");
      return ph ? getComputedStyle(ph).display !== "none" : null;
    })();

    /* ② 选个日期 → 保存 → 库里真有 */
    $("#dateInput").value = "2027-03-15";
    $("#dateInput").dispatchEvent(new Event("input", { bubbles: true }));
    await wait(150);
    dp.hav = $("#dateInput").classList.contains("hav");    // 有值 → 自绘占位该让位
    dp.phHiddenWhenHas = (() => {
      const ph = document.querySelector("#ovDate .dph");
      return ph ? getComputedStyle(ph).display === "none" : null;
    })();
    $("#dateOk").click();
    await wait(1400);
    dp.closed = getComputedStyle($("#ovDate")).display === "none";
    dp.saved = await readDl();

    /* ③ 再打开（这回有旧值：回填要对、占位要让位、「清空」该露头）→ 清空 → 库里空 */
    await openViaMenu();
    dp.reopenVal = ($("#dateInput") || {}).value || "";
    dp.clearShownWhenHas = getComputedStyle($("#dateClear")).display !== "none";
    $("#dateClear").click();
    await wait(1400);
    dp.afterClear = await readDl();

    /* 收拾：样本节点带走 */
    await call("delete", "node", nid);
    await reload(); await wait(300);
    dp.cleaned = !nodeRow(nid);
    dp.errNew = window.__err === errBefore ? null : window.__err;
    rep.date = dp;
  });

  try { await step("done"); } catch (e) { /* 收尾标记丢了不算失败 */ }
  try { setView("tree"); } catch (e) { /* 收尾失败不影响报告 */ }
  rep.failed = failed || null;
  rep.err = rep.err || window.__err || null;
  rep.title = document.title;
  /* 无论如何都要把报告送出去，否则 Python 侧只能干等超时 */
  try { await guard(call("boot_ok", JSON.stringify(rep)), 6000, "boot_ok"); }
  catch (e) { window.__err = window.__err || String((e && e.message) || e); }
}

