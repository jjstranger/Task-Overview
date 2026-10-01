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
  const rep = { stages: [] };
  const step = async (s) => {
    rep.stages.push(s);
    await guard(call("diag_stage", s), 4000, "diag_stage:" + s);
  };
  let failed = "";

  const phase = async (name, ms, fn) => {
    if (failed) return;             // 前面挂了就不要再往下跑，免得连环报错
    try { await guard(fn(), ms, name); }
    catch (e) { failed = name; rep.err = String((e && e.message) || e); }
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
    };
  });

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

        /* ④ 制作人组：勾一个 → 反选 → 应当正好是其余的人 */
        if (nfMulti.hasWhoGroup) {
          const whoAll = subArtists(pBnow);
          const first = whoAll[0];
          nfMulti.whoAll = whoAll;
          const itemOfWho = () => {
            const p = document.querySelector(".popup");
            return p && Array.from(p.querySelectorAll("div"))
              .find((d) => !d.className
                && d.textContent.replace(/^[✓\u3000]+/, "") === first);
          };
          if (first) { const it = itemOfWho(); if (it) it.click(); }
          await wait(300);
          nfMulti.pickedWho = (nfCur().who || []).slice();
          await ensureActs(2);
          nfMulti.whoInvClicked = actClick(1, "反选");
          await wait(300);
          nfMulti.whoInv = (nfCur().who || []).slice();
          nfMulti.whoInvOk = nfMulti.whoInv.length === whoAll.length - 1
            && nfMulti.whoInv.indexOf(first) < 0;
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
    $("#nodesSpec").value = "s001,s003A,s006-009";
    await refreshNodesPrev();
    await wait(500);
    const prev = $("#nodesPrev").textContent || "";
    const want = ["s001", "s003A", "s006", "s007", "s008", "s009"];
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
      prevOk: prev.indexOf("将创建") >= 0,
      prevAll: want.every((t) => prev.indexOf(t) >= 0),
      titles: titles,
      n: titles.length,
      dupCreated: again && again.count,
      dupSkipped: ((again || {}).skipped || []).length,
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
  await phase("multi", 30000, async () => {
    await step("multi");
    setView("tree");
    selClear();
    ui.multi = 0; saveUI();
    await call("collapse_all", "expand");
    await reload();
    await wait(600);

    /* --- #3 多选：默认没有复选框 → 点「多选」才出现 --- */
    const mrep = {};
    mrep.pickBefore = n("#tree input.pick");
    mrep.barBefore = getComputedStyle($("#bulkBar")).display;
    $("#treeMulti").click();
    await wait(400);
    mrep.pickAfter = n("#tree input.pick");
    mrep.barAfter = getComputedStyle($("#bulkBar")).display;
    /* 批量条上该有的四颗按钮：改状态 / 改制作人 / 清空 / 退出。
       「全选本层」按用户要求撤掉了 —— 与其断言"某个不存在的元素不存在"（恒真、等于没测），
       不如把**实际有哪些按钮**采回来：既确认没多出全选，也能发现少了谁。 */
    mrep.bulkBtns = Array.from(document.querySelectorAll("#bulkBar button")).map((b) => b.textContent.trim());

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
        selClear(); repaintSelection(); syncBulkBar();
        await wait(200);
        /* 用真 MouseEvent（`.click()` 是合成调用，测不出 activation 回滚那类问题） */
        const realClick = (el) => {
          el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
          el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
          el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        };
        realClick(c0);
        await wait(250);
        mrep.pickClick1 = {
          id: id0, inSet: selNodes.has(id0), checked: c0.checked,
          cls: r0.classList.contains("picked"),
          /* 期望：进集合了、复选框当场就是勾的（老 bug 这里 checked=false） */
          ok: selNodes.has(id0) && c0.checked === true
              && r0.classList.contains("picked"),
        };
        realClick(c1);
        await wait(250);
        mrep.pickClick2 = {
          id: id1,
          firstStillChecked: c0.checked,
          firstInSet: selNodes.has(id0),
          secondInSet: selNodes.has(id1),
          secondChecked: c1.checked,
          /* 期望：两条都勾着，第一条**没有**被"延迟一拍" */
          ok: selNodes.has(id0) && c0.checked === true
              && selNodes.has(id1) && c1.checked === true,
        };
        /* 再点第一条 → 取消，复选框当场灭 */
        realClick(c0);
        await wait(250);
        mrep.pickClick3 = {
          outOfSet: !selNodes.has(id0), checked: c0.checked,
          secondStillChecked: c1.checked,
          ok: !selNodes.has(id0) && c0.checked === false && c1.checked === true,
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
        kidRow.click();                                    // 减选子项（连带它后代）
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

    /* 真改制作人：挑菜单里第一个具体的人名（跳过「清空」「手输」） */
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
             && d.textContent.indexOf("手输") < 0);
    mrep.artistPick = it2 ? it2.textContent : null;
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
    mrep.artistOk = mrep.artistPick && selCount() >= 0
      && Array.from(selNodes).every((i) => artistOf(i) === mrep.artistPick);

    /* 「清空」全部取消；「退出多选」复选框消失 */
    $("#bulkNone").click(); await wait(300);
    mrep.none = selCount();
    $("#bulkExit").click(); await wait(400);
    mrep.pickGone = n("#tree input.pick");
    mrep.barGone = getComputedStyle($("#bulkBar")).display;
    rep.multi = mrep;

    /* --- #6 数据文件：面板上要能改（不真重启，只验接口与校验） --- */
    const drep = {};
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
      $("#newOk").click();
      await wait(2200);
      const f2 = await call("finance_data", 0, 0, "CNY");
      const p = (f2.projects || []).find((x) => x.project === "冒烟-款项项目");
      rep.create = p
        ? { ok: true, contract: p.contract, received: p.received,
            pending: p.pending, client: p.client }
        : { ok: false, alert: window.__alert || null,
            projects: (f2.projects || []).map((x) => x.project) };

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

      /* ① 开面板 = 新建态：删除按钮必须是收着的
            （不然上次编辑过客户后再开面板，按钮还露着、editCli 还指着旧 id） */
      $("#finClientsBtn").click();
      await wait(250);
      crep.opened = getComputedStyle($("#ovCli")).display !== "none";
      crep.delHiddenFresh = $("#cliDel").style.display === "none";

      /* ② 点开列表里那行 → 表单填上、删除按钮露出来 */
      const pickRow = (name) => Array.from($("#cliList").querySelectorAll("[data-cli]"))
        .find((el) => {
          const nm = el.parentElement.querySelector(".nm");
          return nm && nm.textContent === name;
        });
      const row = pickRow("冒烟待删客户");
      crep.rowFound = !!row;
      if (row) {
        row.click();
        await wait(250);
        crep.delShownEdit = $("#cliDel").style.display !== "none";
        crep.nameFilled = $("#cliName").value === "冒烟待删客户";

        /* ③ 真点删除 → 客户从列表里消失、表单被清空、没有报错弹窗 */
        window.__alertDel = null;
        $("#cliDel").click();
        await wait(800);
        const after = await call("finance_data", 0, 0, "CNY");
        crep.deleted = !(after.clients || []).some((c) => c.name === "冒烟待删客户");
        crep.delAlert = window.__alertDel;
        crep.formCleared = $("#cliName").value === "";
        crep.delOk = crep.deleted && !crep.delAlert && crep.formCleared;
      }

      /* ④ 还有项目挂靠的客户必须删不掉，并把原因 alert 出来（不是静默失败） */
      await loadFinance();
      renderClients();
      const busy = (fin.clients || []).find((c) => c.name === "冒烟客户");
      crep.busyFound = !!busy;
      if (busy) {
        const row2 = Array.from($("#cliList").querySelectorAll("[data-cli]"))
          .find((el) => Number(el.dataset.cli) === Number(busy.id));
        if (row2) {
          row2.click();
          await wait(250);
          window.__alertDel = null;
          $("#cliDel").click();
          await wait(800);
          crep.busyMsg = window.__alertDel;
          crep.busyBlocked = !!window.__alertDel && /项目/.test(window.__alertDel);
          /* 再问一次后端：确认真的还在（没被偷偷删掉） */
          const still = await call("client_delete", busy.id);
          crep.stillRefused = still.ok === false;
        }
      }
      modal("#ovCli", false);
    } finally {
      window.confirm = realConfirm;
      window.alert = realAlert;
    }
    rep.clients = crep;
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

