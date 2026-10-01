/* 项目看板前端：树渲染、折叠、状态、目录、设置、打卡 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* 状态点颜色。环节（镜头）与项目各一套词，放同一个表里按名取。 */
const C = {
  /* 环节制作流转 */
  待开始: "var(--gray)", 等上游: "var(--amber)", 制作中: "var(--blue)",
  暂停: "var(--gray)", 中止: "var(--gray)",
  已提交: "var(--purple)", 反馈: "var(--red)", 可优化: "var(--purple)",
  通过: "var(--green)", 交付: "var(--green)",
  /* 项目层 */
  筹备: "var(--gray)", 进行中: "var(--blue)", 阻塞: "var(--amber)",
  已交付: "var(--green)", 已结款: "var(--green)",
  归档: "var(--gray)",
};

/* 「收工态」：不再显示到期倒计时（跟后端 models.CLOSED_ALL 对齐） */
const CLOSED = new Set(["通过", "交付", "中止", "已交付", "已结款", "归档"]);

/* 星期表只有这一份（顶栏日期、变更记录的分组头都用它） */
const WD_CN = "日一二三四五六";

/* 冒烟辅助：main.py --smoke 会在启动后读取这些状态 */
window.__err = null;
window.addEventListener("error", (e) => {
  window.__err = (e.message || "unknown") + " @" + (e.lineno || 0);
});
/* 未处理的 Promise 拒绝在 `error` 事件里看不到，但一样会让页面静默失灵 */
window.addEventListener("unhandledrejection", (e) => {
  const r = e.reason;
  window.__err = "reject: " + ((r && r.message) || String(r));
});

let D = null;
/* 顶栏三个页面页签：项目 / 活跃 / 财务。分类（商业/个人）不做筛选，
   改成树里的分组标题行，页签只负责切页面，所以这里没有 filter 状态。
   当前在哪一页由 `#segPage button.act` 的 DOM 状态表达（单一真相源），
   不再另存一个 JS 变量 —— 以前那个 `view` 只写不读，是死状态。 */
let onTop = false;
/* 新建项目时选中的分类键（空 = 还没初始化，打开弹窗时按 D.groups 定）。 */
let cat = "";

const ui = JSON.parse(localStorage.getItem("board.ui") || "{}");

function saveUI() { localStorage.setItem("board.ui", JSON.stringify(ui)); }

/* 归档开关的语义改过：早先是「显示归档项目」（ui.arch=1 才把归档一起要过来），
   现在是「隐藏归档项目」（ui.hideArch=1 表示不要归档），默认就是隐藏。
   老键必须丢掉 —— 在旧语义下开了归档的人（arch=1），换成新逻辑一读就是「不隐藏」，
   于是归档项目全冒出来，跟他上次设的正好反过来。
   注意只清一次：没写 hideArch 才补默认值，用户之后手动改成 0 不会被下次启动掰回去。 */
if ("arch" in ui || ui.hideArch === undefined) {
  delete ui.arch;
  if (ui.hideArch === undefined) ui.hideArch = 1;
  saveUI();
}

/* 客户列显示开关：1 = 显示（默认，跟老行为一致），0 = 收起来。
   项目一多，客户名 + 备注 + 制作人会把行撑得很长，所以给个开关自己收。 */
if (ui.showClient === undefined) { ui.showClient = 1; saveUI(); }

/* 子环节筛选从主菜单挪到了每行自己的筛选框：老的全局 nStat / nWho 两个键丢掉
   （留着也没人读，反而让人以为主菜单还能筛子环节）。 */
if ("nStat" in ui || "nWho" in ui) { delete ui.nStat; delete ui.nWho; saveUI(); }
if (!ui.nf || typeof ui.nf !== "object") { ui.nf = {}; saveUI(); }

/* 多选模式：0 = 关（默认）。这是"一次性动作"的模式，默认关着，
   开着的时候才在行首显示复选框、工具栏下面才出现批量操作条。 */
if (ui.multi === undefined) { ui.multi = 0; saveUI(); }

/* 状态 / 制作人筛选从**单选**改成**多选**（2026-09-30）：键名也跟着换 ——
   语义变了就换键名，老的单值在读的时候迁移一次，绝不原地改含义。
     单值 ui.pStat  →  数组 ui.pStatSel
     单值 nf.s / nf.w  →  数组 nf.st / nf.who
   迁移是一次性的：迁完删老键，下次启动不会再走这里。 */
(function migrateFilterSel() {
  let dirty = false;
  if (typeof ui.pStat === "string") {
    if (ui.pStat) ui.pStatSel = [ui.pStat];
    delete ui.pStat;
    dirty = true;
  }
  if (!Array.isArray(ui.pStatSel)) { ui.pStatSel = []; dirty = true; }
  Object.keys(ui.nf || {}).forEach((k) => {
    const f = ui.nf[k];
    if (!f || typeof f !== "object") return;
    if (typeof f.s === "string") { if (f.s) f.st = [f.s]; delete f.s; dirty = true; }
    if (typeof f.w === "string") { if (f.w) f.who = [f.w]; delete f.w; dirty = true; }
    /* 值必须是数组（老版本可能存过单个字符串以外的形态） */
    if (f.st !== undefined && !Array.isArray(f.st)) { f.st = [].concat(f.st || []); dirty = true; }
    if (f.who !== undefined && !Array.isArray(f.who)) { f.who = [].concat(f.who || []); dirty = true; }
  });
  if (dirty) saveUI();
})();

/* ---------- 项目树筛选 ----------
   两层，各管一段：
     ui.hideArch  = 1（默认）      不显示归档项目（后端压根不发过来）
     ui.pStatSel  = [项目状态…]    **多选**：只显示命中其中任一个的项目
                                   （空数组 = 不筛，全显示）

   多选（2026-09-30）：状态和制作人都能勾多个，菜单里配「反选 / 清空」。
   空集合和"全部勾上"在**结果**上等价，所以索性不给「全选」按钮 ——
   这就是它们等价的地方；但视觉上分得开（全勾上会一个个带 ✓）。

   子环节筛选**不进主菜单**：每个有子环节的行（项目、以及下面还有子环节的环节）
   各挂一个筛选框，条件记在 ui.nf 里，按行存：
     ui.nf["p12" / "n34"] = { t: 文字, st: [环节状态…], who: [制作人…] }
   每层只筛自己直属的那一层 —— 子环节下面还有子环节时，那一行自己也有筛选框，
   层层各筛各的，条件不用叠在一起算。

   为什么要留祖先：只保留命中行的话，筛出来是一堆看不出归属的孤行。
   所以自己命中、或**后代里有命中的**，都要留 —— 看得出它在哪一层。
   项目行不受子环节筛选影响：那是 pStatSel 那一组的事。 */
const pStatList = () => (Array.isArray(ui.pStatSel) ? ui.pStatSel : []);
const pStatHas = (s) => pStatList().indexOf(s) >= 0;
/* 本次加载该不该带归档项目。原先是 `hideArch && pStat !== "归档"` 的取反，
   现在"归档"只是多选集合里的一个词，判据改成「集合里有没有它」。 */
const needArchData = () => !ui.hideArch || pStatHas("归档");
/* 上次 load 有没有带归档数据：切换筛选时只有这一项变了才需要再问后端 */
let loadedArch = null;

function pStatLabel() {
  const sel = pStatList();
  if (!sel.length) return "全部状态";
  return sel.length > 3 ? sel.length + " 个状态" : sel.join(" / ");
}
function visibleProjects(list) {
  const sel = pStatList();
  if (!sel.length) return list;
  /* 筛「归档」时两条路都算：字段归档的项目状态不一定写着「归档」 */
  return list.filter((p) => sel.some((s) => (s === "归档" ? isArchived(p) : p.status === s)));
}
const nfKey = (kind, id) => (kind === "project" ? "p" : "n") + id;
function nfOf(kind, id) { return ((ui.nf || {})[nfKey(kind, id)]) || null; }
function nfSet(kind, id, patch) {
  if (!ui.nf) ui.nf = {};
  const k = nfKey(kind, id);
  const f = Object.assign({}, ui.nf[k] || {}, patch);
  /* 空串和空数组都算"没这个条件"，删掉 —— nfOn / nfLabel 才判得干净 */
  ["t", "st", "who"].forEach((x) => {
    const v = f[x];
    if (v === undefined || v === null || v === "" ||
        (Array.isArray(v) && !v.length)) delete f[x];
  });
  if (!f.t && !f.st && !f.who) delete ui.nf[k]; else ui.nf[k] = f;
  saveUI();
}
function nfClear(kind, id) { if (ui.nf) { delete ui.nf[nfKey(kind, id)]; saveUI(); } }
function nfOn(f) {
  return !!(f && (f.t || (f.st && f.st.length) || (f.who && f.who.length)));
}
/* 数组里有没有某个词（统一口，免得满屏 indexOf 写歪） */
const hasWord = (arr, v) => !!arr && arr.indexOf(v) >= 0;
/* 在数组里加/去一个词，返回**新数组**（不改原数组，方便直接喂给 nfSet） */
function flipWord(arr, v) {
  const a = (arr || []).slice();
  const i = a.indexOf(v);
  if (i >= 0) a.splice(i, 1); else a.push(v);
  return a;
}

/* 每层筛选里存的**状态词 / 制作人**自愈（跟 reload() 里给 ui.pStatSel 做的是同一件事）。
   状态词表改过版（环节那套 9 态）时，localStorage 里可能留着旧词，
   而那个词谁也匹配不上 → 这一层永远是空的，看着像「子环节全丢了」。
   判据用**这棵树里真实出现过的词**，比拿 D.status 更准：
   筛选本来就是「筛本层直属子级」，词在库里活不活才是重点。
   多选之后要**逐个**剔：集合里剩几个活的就留几个，别整组丢掉。 */
function nfHeal(node, kind) {
  if (!ui.nf || !node) return;
  const k = nfKey(kind, node.id);
  const f = ui.nf[k];
  if (!f || !nfOn(f)) return;
  const kids = node.children || [];
  if (!kids.length) return;
  let dirty = false;
  if (f.st && f.st.length) {
    const live = new Set(kids.map((c) => c.status));
    const keep = f.st.filter((s) => live.has(s));
    if (keep.length !== f.st.length) { if (keep.length) f.st = keep; else delete f.st; dirty = true; }
  }
  if (f.who && f.who.length) {
    const live = new Set(subArtists(node));
    const keep = f.who.filter((w) => live.has(w));
    if (keep.length !== f.who.length) { if (keep.length) f.who = keep; else delete f.who; dirty = true; }
  }
  if (!dirty) return;
  if (!f.t && !f.st && !f.who) delete ui.nf[k];
  saveUI();
}

function nfLabel(f) {
  if (!nfOn(f)) return "筛选";
  const p = [];
  if (f.t) p.push("“" + f.t + "”");
  /* 多选：只选了一个就报那个词，多了就报个数（行上塞不下） */
  if (f.st && f.st.length) p.push(f.st.length === 1 ? f.st[0] : f.st.length + " 个状态");
  if (f.who && f.who.length) p.push(f.who.length === 1 ? "@" + f.who[0] : f.who.length + " 个制作人");
  const s = p.join(" · ");
  /* 太长就把行撑爆了，超过 14 个字只报条件数 */
  return s.length > 14 ? "筛选 ·" + p.length : "筛选:" + s;
}
/* 文字搜索：标题 / 备注 / 制作人，任意一个包含就算命中（不区分大小写） */
function txtHit(n, t) {
  if (!t) return true;
  const q = String(t).toLowerCase();
  return [n.title, n.note, n.artist].some(
    (v) => v && String(v).toLowerCase().indexOf(q) >= 0);
}
function nodeMatch(n, f) {
  if (!nfOn(f)) return true;
  /* 多选 = 白名单：命中集合里任一个就算通过 */
  if (f.st && f.st.length && !hasWord(f.st, n.status)) return false;
  if (f.who && f.who.length && !hasWord(f.who, n.artist || "")) return false;
  if (f.t && !txtHit(n, f.t)) return false;
  return true;
}
function nodeKeep(n, f) {
  if (!nfOn(f)) return true;
  if (!n.children || !n.children.length) return nodeMatch(n, f);
  return nodeMatch(n, f) || n.children.some((c) => nodeKeep(c, f));
}
/* 制作人清单：本层往下摘（库里没单独存人），只列这棵子树里真有人在做的 */
function subArtists(n) {
  const set = new Set();
  const walk = (list) => (list || []).forEach((x) => {
    if (x.artist) set.add(x.artist);
    walk(x.children);
  });
  walk(n.children);
  return Array.from(set).sort();
}
function treeFilterText() {
  const t = [];
  const sel = pStatList();
  if (sel.length === 1) t.push(sel[0]);
  else if (sel.length > 1) t.push(sel.length + " 个状态");
  if (!ui.hideArch) t.push("含归档");   // 默认隐藏，所以只有「反着来」时才留痕
  return t.length ? "筛选：" + t.join(" · ") + " ▾" : "筛选 ▾";
}
function syncTreeFilter() {
  const b = $("#treeFilter");
  if (!b) return;
  b.textContent = treeFilterText();
  b.classList.toggle("on", !!(pStatList().length || !ui.hideArch));
}
/* 改主菜单那组项目状态（多选）。三件事按顺序做：
     ① 落 localStorage + 立刻把按钮文案和树重画（纯前端过滤，不用等后端）
     ② 菜单**不关**，重开一次把 ✓ 更新 —— 多选是一个个勾的，
        每勾一下关一次弹层根本没法用
     ③ 只有「要不要归档数据」变了才再问一次后端：每点一下都跨桥 reload
        会把滚动位置和展开状态全晃掉。 */
function applyPStats(sel, anchor) {
  ui.pStatSel = sel.slice();
  saveUI();
  syncTreeFilter();
  render();
  if (anchor) openTreeFilter(anchor);
  if (needArchData() !== loadedArch) reload();
}
function openTreeFilter(anchor) {
  const items = [
    { label: (ui.hideArch ? "✓ " : "　") + "隐藏归档项目", fn: async () => {
        ui.hideArch = ui.hideArch ? 0 : 1; saveUI(); syncTreeFilter(); await reload();
    } },
    { label: (ui.showClient ? "✓ " : "　") + "显示客户", fn: () => {
        ui.showClient = ui.showClient ? 0 : 1; saveUI(); render();
    } },
    { sep: true },
    { title: "项目状态（可多选）" },
  ];
  const all = ((D && D.status && D.status.project) || []);
  /* 两个动作共用一行，横着排 —— 竖着来会白占一行。
     **没有「全选」**：空集合 = 不筛 = 全选，所以「清空」就是回到全选
     （用户 2026-09-30 指出这一条，全选按钮纯属重复）。 */
  items.push({ acts: [
    { label: "反选", fn: () => applyPStats(all.filter((s) => !pStatHas(s)), anchor) },
    { label: "清空", fn: () => applyPStats([], anchor) },
  ] });
  all.forEach((s) => {
    items.push({
      label: (pStatHas(s) ? "✓ " : "　") + s,
      keep: true,
      fn: () => applyPStats(flipWord(pStatList(), s), anchor),
    });
  });
  /* 子环节那两组不在这里了 —— 挪到每行自己的筛选框（openNodeFilter），
     主菜单只留「这一页显示哪些项目」。 */
  popup(items, anchor);
}

/* 某一行的子环节筛选框：搜索框（标题 / 备注 / 制作人）+ 状态 + 制作人。
   状态和制作人都是**多选**（2026-09-30），配「反选 / 清空」两个动作
   （没有「全选」：空集合就等于不筛 = 全选）。
   只管这行直属的那一层；下面还有子环节的话，那一行自己也有一个。 */
function openNodeFilter(n, kind, anchor) {
  /* ⚠ render() 会重建整棵树，调用方手上那颗 .flt 会变成**游离节点** ——
     游离节点的 getBoundingClientRect() 全是 0，弹层会跳到窗口左上角。
     所以重开时一律按 id 重新找一次（找不到才退回传进来的那个）。 */
  const reanchor = () => {
    const row = document.querySelector(
      '#tree .row[data-kind="' + kind + '"][data-id="' + n.id + '"]');
    return (row && row.querySelector(".ops .flt")) || anchor;
  };
  /* 条件每次现取现算：nfSet 之后旧的快照就过期了 */
  const cur = () => nfOf(kind, n.id) || {};
  /* 改条件 + 重画树 + **就地重开菜单**（多选要连着勾，不能勾一下就关） */
  const set = (patch) => { nfSet(kind, n.id, patch); render(); openNodeFilter(n, kind, reanchor()); };
  const f = cur();

  /* 打字即时筛：render() 只重建 #tree，弹层挂在 body 上不受影响，焦点也不丢 */
  const input = document.createElement("input");
  input.className = "psearch";
  input.placeholder = "搜标题 / 备注 / 制作人";
  input.value = f.t || "";
  input.oninput = () => { nfSet(kind, n.id, { t: input.value.trim() }); render(); };
  input.onkeydown = (e) => {
    if (e.key === "Escape") { closePopup(); render(); }
    if (e.key === "Enter") closePopup();
  };
  const items = [];
  if (nfOn(f)) {
    items.push({ label: "清除本层筛选", dim: true, fn: () => { nfClear(kind, n.id); render(); } });
    items.push({ sep: true });
  }
  const stats = (D && D.status && D.status.node) || [];
  items.push({ title: "状态（可多选）" });
  items.push({ acts: [
    { label: "反选", fn: () => set({ st: stats.filter((s) => !hasWord(cur().st, s)) }) },
    { label: "清空", fn: () => set({ st: [] }) },
  ] });
  stats.forEach((s) => {
    items.push({
      label: (hasWord(cur().st, s) ? "✓ " : "　") + s,
      keep: true,
      fn: () => set({ st: flipWord(cur().st, s) }),
    });
  });
  const who = subArtists(n);
  if (who.length) {
    items.push({ sep: true });
    items.push({ title: "制作人（可多选）" });
    items.push({ acts: [
      { label: "反选", fn: () => set({ who: who.filter((w) => !hasWord(cur().who, w)) }) },
      { label: "清空", fn: () => set({ who: [] }) },
    ] });
    who.forEach((w) => {
      items.push({
        label: (hasWord(cur().who, w) ? "✓ " : "　") + w,
        keep: true,
        fn: () => set({ who: flipWord(cur().who, w) }),
      });
    });
  }
  popup(items, anchor, input);
  setTimeout(() => input.focus(), 0);
}
function whenReady(cb) {
  if (window.pywebview && window.pywebview.api) cb();
  else window.addEventListener("pywebviewready", cb);
}
/* 统一的后端调用口。以前是裸 `window.pywebview.api[fn](...)`：
   桥没起来（页面加载早于 pywebviewready）或后端抛异常时，会静默产出
   `undefined` 或者甩出没人接的 Promise rejection —— 界面看着"点了没反应"，
   控制台之外查不到原因。现在统一兜底：缺桥/缺方法/抛异常都回
   `{ ok:false, err }`，并把原因打到控制台。
   ⚠ 成功时**原样返回后端结果**（不加包装）—— 调用点大多直接当数据用
   （`D = await call("load")`），包装过会全线破坏。 */
const call = async (fn, ...a) => {
  const api = window.pywebview && window.pywebview.api;
  if (!api || typeof api[fn] !== "function") {
    const err = "后端未就绪：" + fn;
    console.warn("[call]", err);
    return { ok: false, err: err };
  }
  try {
    return await api[fn](...a);
  } catch (e) {
    const err = String((e && e.message) || e);
    console.warn("[call]", fn, err);
    return { ok: false, err: err };
  }
};

/* ---------- 工具 ---------- */

function leaves(n) {
  if (!n.children || !n.children.length) return n.done !== undefined ? [n.done ? 1 : 0] : [];
  return n.children.flatMap(leaves);
}
function sumOf(nodes) {
  let a = 0, b = 0;
  nodes.forEach((n) => { const L = leaves(n); b += L.length; a += L.filter(Boolean).length; });
  return [a, b];
}
function deadlineOf(n) {
  let r = n.deadline || "";
  (n.children || []).forEach((c) => { const x = deadlineOf(c); if (x && (!r || x < r)) r = x; });
  return r;
}
function maxAct(n) {
  let r = n.last_activity_at || "";
  (n.children || []).forEach((c) => { const x = maxAct(c); if (x > r) r = x; });
  return r;
}

/* 把库里的日期串解析成 Date；**解不出来一律返回 null**。
   为什么必须挡：库里可能存着 `2026/1/1`、`1月1日` 这类非 ISO 的脏日期
   （早期手输、外部导入都有可能），`new Date("坏串")` 得到 Invalid Date，
   再做算术就是 NaN —— 界面上会真的显示成 "D-NaN"、停滞天数算成 NaN 天。
   一个安静的错误展示比报错更难查，所以统一在这里收口。 */
function parseDay(s) {
  const t = String(s || "").trim();
  if (!t) return null;
  const d = new Date(t.length >= 10 ? t.slice(0, 10) + "T00:00:00" : t + "T00:00:00");
  return isNaN(d.getTime()) ? null : d;
}

/* 「今天零点」一天之内不变，但 daysLeft 每画一行就调一次 ——
   每次都 new Date(...) 造两个对象，几千行时纯属白烧。按日期串缓存。 */
let _day0Key = "", _day0 = null;
function startOfToday() {
  const k = isoOf(new Date());
  if (k !== _day0Key) { _day0Key = k; _day0 = new Date(k + "T00:00:00"); }
  return _day0;
}

/* 距今还有几天（正数=未来，负数=已逾期）。日期坏了就当"没设期限"，返回 null。 */
function daysLeft(d) {
  const t = parseDay(d);
  if (!t) return null;
  return Math.round((t - startOfToday()) / 86400000);
}

/* 停滞阈值：全库一个数（2026-09-30 起不再分商业 / 个人 —— 分类都能自己加了，
   再绑两档说不通）。坏值当默认，别让一个手滑填的 0 把整棵树标成停滞。 */
function staleDays() {
  const v = parseInt(((D && D.settings) || {}).stale_days || "", 10);
  return (isFinite(v) && v > 0) ? v : 3;
}
function isStale(p) {
  const thr = staleDays();
  const la = maxAct(p);
  if (!la) return false;
  /* last_activity_at 是 "YYYY-MM-DD HH:MM:SS"，换成 ISO 的 T 分隔再解析 */
  const t = parseDay(String(la).replace(" ", "T"));
  if (!t) return false;                    // 时间戳坏了：算不出停滞，别误报
  const gap = (Date.now() - t.getTime()) / 86400000;
  /* 判据的唯一实现是后端 models.stale_gap()（含 STALE_SKIP 那两条收工状态）。
     这里只能逐字对齐 —— 底栏「停滞 N」、告警列表、行上这个小标必须同一个数，
     对不上用户第一反应就是"数据不对"。改后端那个元组时记得回来改这行。 */
  return gap >= thr && p.status !== "归档" && p.status !== "已结款";
}
/* 拼 HTML 时一律过这里。单引号也转 —— 目前属性都用双引号所以没炸过，
   但少一个字符就是给以后留一颗雷（`title='...'` 一写就破）。 */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ESC_MAP[c]); }

/* ---------- 弹层 ---------- */

function closePopup() {
  $$(".popup").forEach((p) => p.remove());
  document.removeEventListener("mousedown", onDown, true);
}
function onDown(e) { if (!e.target.closest(".popup")) closePopup(); }
function popup(items, anchor, head) {
  closePopup();
  const p = document.createElement("div");
  p.className = "popup";
  /* head 是插在最上面的自定义元素（比如子环节筛选的搜索框）；
     它在 .popup 里面，所以点它不会触发「点外面就关」 */
  if (head) p.appendChild(head);
  items.forEach((it) => {
    if (it.sep) { const s = document.createElement("div"); s.className = "sep"; p.appendChild(s); return; }
    /* 组标题：纯文字，不可点（「状态（可多选）」这类） */
    if (it.title) {
      const h = document.createElement("div");
      h.className = "ttl";
      h.textContent = it.title;
      p.appendChild(h);
      return;
    }
    /* 一组横向小动作（反选 / 清空）。多选菜单要它 ——
       两个按钮竖着排会把下面的状态列表推远。 */
    if (it.acts) {
      const row = document.createElement("div");
      row.className = "acts";
      it.acts.forEach((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = a.label;
        b.onclick = (ev) => { ev.stopPropagation(); a.fn(); };
        row.appendChild(b);
      });
      p.appendChild(row);
      return;
    }
    const d = document.createElement("div");
    d.textContent = it.label;
    if (it.dim) d.className = "dim";
    d.onclick = () => {
      /* keep = 点了别关弹层。多选（勾状态 / 勾制作人）是一个个勾的，
         每勾一下关一次、还得再点开一次没法用；fn 负责把菜单重开一遍。 */
      if (!it.keep) closePopup();
      if (it.fn) it.fn();
    };
    p.appendChild(d);
  });
  document.body.appendChild(p);
  const r = anchor.getBoundingClientRect();
  p.style.left = Math.max(6, Math.min(r.left, innerWidth - p.offsetWidth - 8)) + "px";
  p.style.top = Math.max(6, Math.min(r.bottom + 4, innerHeight - p.offsetHeight - 8)) + "px";
  setTimeout(() => document.addEventListener("mousedown", onDown, true), 0);
}
const modal = (id, on) => { $(id).style.display = on ? "flex" : "none"; };

/* 轻提示：不像 alert 那样打断操作，用于「拖到别处不行」这类即时反馈 */
function toast(msg) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  /* 后端返回的 msg 有时是 undefined（比如只回 ok:false 没带 msg），
     直接塞进 textContent 会显示成 "undefined"，看着像程序坏了 */
  el.textContent = (msg === undefined || msg === null) ? "" : String(msg);
  el.classList.add("on");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("on"), 2800);
}

/* ---------- 拖拽排序（M4） ---------- */

let drag = null;   // { kind, id, pid, el }

/* 鼠标在行的上缘 / 下缘 / 中间，决定是排到前面、后面还是放进去 */
function dropZone(e, el) {
  const b = el.getBoundingClientRect();
  const y = e.clientY - b.top;
  const h = b.height || 1;
  if (y < h * 0.28) return "before";
  if (y > h * 0.72) return "after";
  return "inside";
}

function clearDropMarks() {
  $$("#tree .row").forEach((el) => el.classList.remove("dz-before", "dz-after", "dz-inside"));
}

function bindDrag(r, n, kind) {
  r.dataset.kind = kind;
  r.dataset.id = n.id;
  r.draggable = true;

  r.ondragstart = (e) => {
    drag = { kind: kind, id: n.id, pid: kind === "project" ? n.id : n.pid, el: r };
    r.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Firefox/WebView 里没有 data 就不算有效拖拽
    try { e.dataTransfer.setData("text/plain", String(n.id)); } catch (_) { /* 忽略 */ }
  };

  r.ondragend = () => {
    r.classList.remove("dragging");
    clearDropMarks();
    drag = null;
  };

  r.ondragover = (e) => {
    if (!drag || drag.el === r) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const z = dropZone(e, r);
    clearDropMarks();
    r.classList.add("dz-" + z);
  };

  r.ondragleave = () => r.classList.remove("dz-before", "dz-after", "dz-inside");

  r.ondrop = async (e) => {
    if (!drag) return;
    e.preventDefault();
    let pos = dropZone(e, r);
    clearDropMarks();
    const from = drag;
    drag = null;
    if (from.el === r) return;

    let targetId = n.id;
    if (kind === "project") {
      if (from.kind === "project") {
        // 项目之间只分先后，没有「放进去」这回事
        if (pos === "inside") pos = "after";
      } else {
        // 环节拖到项目行 = 回到该项目的最外层
        if (from.pid !== n.id) { toast("暂不支持把环节移到别的项目"); return; }
        targetId = null;
        pos = "inside";
      }
    } else if (from.kind === "project") {
      return;   // 项目塞不进环节里
    }

    const res = await call("move", from.kind, from.id, targetId, pos);
    if (res && res.ok === false) { toast(res.msg || "移动失败"); return; }
    await reload();
  };
}

/* ---------- 渲染 ---------- */

function rowEl(cls) { const d = document.createElement("div"); d.className = "row " + (cls || ""); return d; }

/* ---------- 多选（issue #3：批量改子环节的状态和制作人） ----------

   选择集只活在前端（不进库）—— 多选是个瞬态操作，关了就该清干净，
   存起来反而会让人下次打开看到一个莫名其妙的"已选 7 条"。
   只收**环节**：项目层的状态是另一套语义，混在一起改没有意义。

   选择方式参照 Maya 大纲（用户点名要的）：
     * 直接点 → 选中这一条，**连带它的所有后代**一起选
     * Ctrl + 点 → 加选 / 减选（同样是连带后代）
     * Shift + 点 → 范围选择：从"上次点的那条"到"这条"之间（按屏幕顺序）
                    全部选中，中间的层级按可见顺序算
     * 取消一层 → 后代跟着取消（跟选中对称）
   "上次点的那条"叫**锚点**，Shift 一切都从它开始算。 */
const selNodes = new Set();
let selAnchor = null;          // {id, kind} 上次点过的那条，Shift 范围选择的起点

const selCount = () => selNodes.size;
function selClear() { selNodes.clear(); selAnchor = null; }

/* 选择集判存：`selNodes` 里存的**一律是 Number**，而 id 从 DOM / 后端来时
   可能是字符串。以前每个调用点各写各的 `Number()`（有的转有的忘），
   这里统一收口 —— 少一处漏转就是少一个「明明选中了却判没选中」的隐形 bug。 */
const selHas = (id) => selNodes.has(Number(id));

/* ---------- D 的索引（拿 D 的对象身份当缓存键） ----------
   每次 reload() 都把 D 换成**新对象**，所以缓存天然跟着失效，不用手动清。
   以前按 id 找一条得全树 eachNode（O(N)），而 render / repaintSelection
   每画一行都要问好几次 —— 几千条环节时就是实打实的 O(N²)。
   这里一次建好三张表：id→环节、id→项目、id→全部后代 id。 */
let _idxD = null, _idx = null;

function idxOf() {
  /* 两个条件都要判：D 还没加载时 `_idxD === D` 会是 null === null 成立，
     那时 _idx 还是 null，直接取 .nodes 就炸。 */
  if (_idx && _idxD === D) return _idx;
  _idxD = D;
  const nodes = new Map(), projects = new Map(), desc = new Map();
  /* ⚠ 后代清单用**对象本身**当键，不用 id：项目和环节的 id 各自自增，**会撞车**
     （见 rowOf 那段注释，实测出现过重复）。要是也按 id 存，
     同号的那个项目/环节会把对方的清单盖掉，级联选就会少选/多选一条。
     `nodes` 也**只收环节** —— `findNode` 的旧语义就是只找环节
     （老实现走 eachNode），混进项目会让 pickRows 上的 findNode 取错对象。 */
  const walk = (x, isNode) => {
    const ids = [];
    (x.children || []).forEach((c) => {
      walk(c, true);
      ids.push(Number(c.id));
      const sub = desc.get(c);
      if (sub && sub.length) ids.push.apply(ids, sub);
    });
    desc.set(x, ids);
    if (isNode) nodes.set(Number(x.id), x);
  };
  ((D && D.groups) || []).forEach((g) => (g.projects || []).forEach((p) => {
    projects.set(Number(p.id), p);
    walk(p, false);
  }));
  _idx = { nodes: nodes, projects: projects, desc: desc };
  return _idx;
}

/* 收集某条环节及其**全部后代**的 id（不管折叠、不管筛选 —— 折叠只是没画出来，
   不代表它不是它的子级；筛选掉的同理，用户点父级就是要连子级一起）。
   ⚠ 返回的是索引里那份**缓存数组本身**，调用方只读别改。 */
function descendantsOf(n) {
  return idxOf().desc.get(n) || [];
}

/* 一条环节 + 它全部后代 */
function selfAndDescendants(n) {
  return [Number(n.id)].concat(descendantsOf(n));
}

/* 把"自己选中、但后代没全选"的残缺祖先从选择集里剔掉。
   为什么要剔：用户看到的是**派生态**（父项缺子项就显示未勾），
   而批量改状态/改制作人用的是 selNodes。两者必须一致 ——
   否则屏幕上明明没勾的父项，批量却会一起改掉（用户 2026-09-27 确认要剔除）。
   从下往上做：先把残缺的剔了，才轮得到它的祖先判断（祖先可能因此也残了）。 */
function prunePartialAncestors() {
  const all = [];
  eachNode((x) => all.push(x));
  /* 深的先算：按子孙数量降序，保证子节点先定案 */
  all.sort((a, b) => descendantsOf(b).length - descendantsOf(a).length);
  let changed = true;
  while (changed) {                        // 一轮可能又暴露出新的残缺祖先
    changed = false;
    all.forEach((n) => {
      const id = Number(n.id);
      if (!selNodes.has(id)) return;
      if (descendantsOf(n).every((i) => selNodes.has(i))) return;
      selNodes.delete(id);                 // 自己选中但后代不全 → 不算选中
      changed = true;
    });
  }
}

/* 按屏幕顺序列出当前树上**画出来的**环节（Shift 范围选择要用这个顺序）。
   折叠起来的分支不进去 —— 看不见的东西不该被范围选择捎带上。 */
function visibleNodeOrder() {
  const out = [];
  const walk = (p) => {
    const f0 = nfOf("project", p.id);
    if (p.collapsed) return;                       // 项目折着，里面一个都看不见
    const go = (arr, kind) => (arr || []).forEach((x) => {
      out.push({ id: Number(x.id), node: x, kind: kind });
      if (!x.collapsed) {
        const f = nfOf("node", x.id);
        go((x.children || []).filter((c) => nodeKeep(c, f)), "node");
      }
    });
    go((p.children || []).filter((c) => nodeKeep(c, f0)), "node");
  };
  D.groups.forEach((g) => visibleProjects(g.projects).forEach((p) => walk(p)));
  return out;
}

/* 选中单条：怎么处理后代由 withKids 决定（默认连带） */
function selOne(n, on, withKids = true) {
  const ids = withKids ? selfAndDescendants(n) : [Number(n.id)];
  if (on === undefined) {
    /* 没指定就取反。**必须按显示态（派生）取反**，不能按 selNodes ——
       否则会出现：勾主项 → 减选一个子项 → 主项显示未勾，
       但 selNodes 里主项还在，再点它反而变成"取消"，怎么点都点不亮。
       取显示态取反：没勾上（哪怕自己是残的）→ 点一下变成"整棵全选"。 */
    on = !nodeFullyOn(Number(n.id));
  }
  ids.forEach((i) => { on ? selNodes.add(i) : selNodes.delete(i); });
  /* 取消之后，祖先里可能出现"自己还选着、后代已经缺了"的残缺状态。
     剔掉它们，让 selNodes 跟屏幕上的勾保持同一套语义（批量范围才不会有幽灵）。 */
  if (!on) prunePartialAncestors();
  return on;
}

/* Shift 范围选择：锚点 → 目标之间的所有可见环节，整段设为 on。
   锚点不在了（被筛掉/删了/换了页）就退化成只选这一条。 */
function selRange(targetNode, on) {
  const order = visibleNodeOrder();
  const idx = (id) => order.findIndex((x) => x.id === Number(id));
  const to = idx(targetNode.id);
  if (to < 0) { selOne(targetNode, on); return; }
  const from = selAnchor === null ? -1 : idx(selAnchor);
  if (from < 0) { selOne(targetNode, on); return; }
  const lo = Math.min(from, to), hi = Math.max(from, to);
  for (let i = lo; i <= hi; i++) {
    const it = order[i];
    // 范围里每条都连带后代 —— 跟单击的语义保持一致，不然两种点法结果不一样
    selOne(it.node, on);
  }
}

/* 行上到底该不该显示"勾上" —— **不光看自己，还要看后代**。
   规则（用户 2026-09-27 定）：一条环节只有在**它自己和全部后代都选中**时
   才算"选中"；少任何一个子级，它自己就显示未选中。
   典型场景：勾了主项 → 全绿；再减选一个子项 → 主项应当**自动变成未勾**。
   直接用 selNodes.has(id) 当显示态是不对的 —— 那会让父项"明明缺子项还打着勾"。

   selNodes 仍是"用户显式点过的集合"（它决定批量改动的范围，语义不变）；
   这里只是**显示层**的派生。没有子级的叶子等价于看自己。 */
function nodeFullyOn(id) {
  if (!selHas(id)) return false;
  const n = findNode(Number(id));
  if (!n) return true;                     // 找不到（不在这三棵树上）就当它没子级
  return descendantsOf(n).every((i) => selNodes.has(i));
}

/* 按选择集刷新整个树里各行的选中外观。
   不重建 DOM —— 只是把 .picked / 复选框对齐到派生状态，
   所以滚动位置、输入焦点、展开状态全都不受影响。 */
function repaintSelection() {
  document.querySelectorAll("#tree .row").forEach((el) => {
    const cp = el.querySelector(".pick");
    if (!cp) return;                       // 非环节行（项目 / 分组）不参与
    const on = nodeFullyOn(Number(el.dataset.id));
    el.classList.toggle("picked", on);
    cp.checked = on;
  });
}

/* 批量条只在多选模式下露出来，省得平时占地方 */
function syncBulkBar() {
  const bar = $("#bulkBar");
  if (!bar) return;
  bar.style.display = ui.multi ? "flex" : "none";
  const c = selCount();
  const cnt = $("#bulkCnt");
  if (cnt) cnt.textContent = c ? "已选 " + c + " 条" : "还没选";
  const tm = $("#treeMulti");
  if (tm) tm.classList.toggle("on", !!ui.multi);
}

/* 归档有两条路：⋯ 菜单里的「归档」写 `archived` 字段；
   直接在状态徽章里选「归档」改的是 `status`。用户两条都在用（后者更顺手），
   所以徽章、压暗、取消归档、隐藏开关必须两条都认 ——
   只认字段的话，状态归档的项目照样冒出来，「隐藏归档项目」看着像坏了。 */
const isArchived = (n) => !!n.archived || n.status === "归档";

function render() {
  const box = $("#tree");
  box.innerHTML = "";
  if (!D.groups.length) {
    box.innerHTML = '<div class="empty">还没有分类，点右上角 ＋ 项目 新建</div>';
    /* 空树也要同步批量条 —— 以前这里直接 return，多选模式下退出多选
       （最后一条项目刚被删掉时）批量条会留在屏幕上。 */
    syncBulkBar();
    return;
  }
  D.groups.forEach((g) => {
    const open = !ui["g:" + g.key];
    const shown = visibleProjects(g.projects);
    /* 进度条和比例始终按**整个分组**算：它是「这个分类整体做到哪了」，
       不能因为筛了个状态就跟着跳；条数则显示筛后 / 总数。 */
    const [a, b] = sumOf(g.projects);
    const running = shown.filter((p) => p.status === "进行中").length;
    const cnt = pStatList().length
      ? shown.length + " 个项目（共 " + g.projects.length + "）"
      : g.projects.length + " 个项目";
    const r = rowEl("grp");
    r.style.paddingLeft = "8px";
    /* data-gk 给自检和 DOM 定位用（**别用 data-id**：那是项目和环节的地盘，
       两者 id 各自自增会撞车（定位用 rowOf(kind, id)，见 diag.js） */
    r.dataset.gk = g.key;
    r.innerHTML =
      '<div class="tw">' + (open ? "▼" : "▶") + "</div>" +
      '<div class="title" title="双击改分类名">' + esc(g.name) + "</div>" +
      '<div class="meta">' + cnt + " · " + running + " 进行中</div>" +
      '<div class="bar"><i style="width:' + (b ? a / b * 100 : 0) + '%"></i></div>' +
      '<div class="meta">' + a + "/" + b + "</div>" +
      /* 两个按钮各给一个明确的 class：⋯ = 分类操作菜单，＋ = 在这个分类下建项目
         （别只靠 .mini 定位 —— 自检和 bindGroupMenu 都要取到确定的那个） */
      '<div class="ops">' +
        '<button class="mini menu" title="分类操作（重命名 / 新建 / 删除）">⋯</button>' +
        '<button class="mini add" title="在「' + esc(g.name) + '」里新建项目">＋</button>' +
      "</div>";
    r.querySelector(".tw").onclick = () => { ui["g:" + g.key] = open ? 1 : 0; saveUI(); render(); };
    bindGroupRename(r, g);
    bindGroupMenu(r, g);
    /* ＋：直接开新建项目页，并且**默认就选中这个分类** —— 从哪一行点的就归哪儿 */
    r.querySelector(".mini.add").onclick = () => openNewDialog(g.key);
    box.appendChild(r);
    if (open) shown.forEach((p) => drawNode(p, box, 1, "project"));
  });
  /* 筛选筛空了要说明白，不然看着像项目丢了 */
  if (pStatList().length && !D.groups.some((g) => visibleProjects(g.projects).length)) {
    box.innerHTML += '<div class="empty">没有「' + esc(pStatLabel()) + "」的项目（点「筛选 ▾」可改）</div>";
  }
  /* 子环节筛空了的提示不在这儿 —— 每层各筛各的，提示画在那一行下面（drawNode） */
  syncBulkBar();
}

/* 构建一行的 HTML（纯字符串，不碰 DOM）。
   抽出来是因为 drawNode 原本 280 多行、把「拼 HTML / 绑事件 / 递归子级」全揉在一起，
   改一处要通读全篇。现在这块是纯函数：给数据出字符串，好读也好单独验证。 */
function rowHTML(n, kind, hasKids, open, a, b, tag, stale, f) {
  return (
    /* 多选模式：只有环节带复选框（项目不参与批量改）。
       复选框只是"选中了没"的指示，真正的点击热区是整行 ——
       跟 Maya 一样点哪儿都行，不用精准点到那个小方块。 */
    (ui.multi && kind === "node"
      ? '<input type="checkbox" class="pick"' + (nodeFullyOn(n.id) ? " checked" : "") + ">"
      : "") +
    '<div class="tw">' + (hasKids ? (open ? "▼" : "▶") : "·") + "</div>" +
    '<div class="dot" style="background:' + (C[n.status] || "var(--gray)") + '"' +
      (kind === "node" && !hasKids ? ' title="点击标记完成/取消"' : "") + "></div>" +
    '<div class="title">' + esc(n.title) + "</div>" +
    (kind === "project" && n.priority ? '<div class="badge" style="color:var(--amber);border-color:var(--amber)">今日必做</div>' : "") +
    (kind === "project" && n.pinned ? '<div class="badge" style="color:var(--blue);border-color:var(--blue)">置顶</div>' : "") +
    /* 状态徽章必须带 `.st`：一行上可能有「今日必做 / 置顶 / 状态 / 已归档」好几个
       badge，裸 `.badge` 取到的是**第一个** —— 今日必做的项目点「今日必做」
       才会弹状态下拉，点真正的状态反而没反应（2026-09-30 用户报的）。
       跟 `.mini.menu / .mini.add`、`rowOf(kind, id)` 是同一条教训。 */
    '<div class="badge st">' + esc(n.status) + "</div>" +
    (kind === "project" && isArchived(n) ? '<div class="badge arch">已归档</div>' : "") +
    (kind === "project" && ui.showClient !== 0 && n.client ? '<div class="meta">' + esc(n.client) + "</div>" : "") +
    (n.note ? '<div class="meta">' + esc(n.note) + "</div>" : "") +
    (n.artist ? '<div class="meta who">制作人 ' + esc(n.artist) + "</div>" : "") +
    (b ? '<div class="bar"><i style="width:' + (a / b * 100) + '%"></i></div><div class="meta">' + a + "/" + b + "</div>" : "") +
    tag +
    (stale ? '<div class="meta stale">停滞</div>' : "") +
    '<div class="ops">' +
      /* 子环节筛选：只给**有子环节**的行（项目、以及下面还有子环节的环节）。
         放在「目录 ▾」左边 —— 这俩都是针对这行的操作，挨着好找。 */
      (hasKids ? '<button class="flt' + (nfOn(f) ? " on" : "") + '">' + esc(nfLabel(f)) + "</button>" : "") +
      /* 目录：这条上挂着的文件夹（可挂多个），点开是菜单 */
      '<button class="open">目录 ▾</button>' +
      '<button class="mini more">⋯</button>' +
    "</div>"
  );
}

/* 「还剩几天」那颗小标。没有期限或日期坏了就空着。
   文案跟后端 `models._due_label()` 逐字一致 —— 「D-1」「今天」这种记号
   用户得先知道 D 是 deadline 才读得懂，一律写成整句话。 */
function dueTag(n) {
  const d = daysLeft(deadlineOf(n));
  if (d === null || CLOSED.has(n.status)) return "";
  const cls = 'meta due';
  if (d < 0) return '<div class="' + cls + '" style="color:var(--red)">逾期 ' + (-d) + " 天</div>";
  if (d === 0) return '<div class="' + cls + '" style="color:var(--amber)">今天到期</div>';
  if (d <= 3) return '<div class="' + cls + '" style="color:var(--amber)">' + d + " 天后到期</div>";
  return '<div class="' + cls + '">' + d + " 天后到期</div>";
}

function drawNode(n, box, dep, kind) {
  const L = leaves(n), a = L.filter(Boolean).length, b = L.length;
  const open = !n.collapsed;
  const hasKids = n.children && n.children.length;
  /* 这行自己的子环节筛选（可能为 null）。筛上了就把 .ops 露出来，
     否则鼠标一挪开就看不见「正在筛着」这回事 */
  const f = nfOf(kind, n.id);
  const r = rowEl((dep === 1 ? "prj" : "") + (kind === "project" && isArchived(n) ? " arch" : "")
    + (nfOn(f) && hasKids ? " filtered" : ""));
  r.style.paddingLeft = (8 + dep * 20) + "px";

  const stale = kind === "project" && isStale(n);
  r.innerHTML = rowHTML(n, kind, hasKids, open, a, b, dueTag(n), stale, f);

  bindDrag(r, n, kind);
  if (ui.multi && kind === "node") bindMulti(r, n);
  bindRowMenus(r, n, kind, hasKids, open);
  bindRename(r, n, kind);

  box.appendChild(r);
  drawKids(n, box, dep, kind, open, hasKids, f);
}

/* 递归画直属子级 + 「这层被筛空了」的提示 */
function drawKids(n, box, dep, kind, open, hasKids, f) {
  /* 子环节筛选只管这层往下（每层各筛各的），项目行始终在 —— 那是 pStatSel 的事。
     hasKids 仍按**真实结构**算 —— 不然后代被筛掉的环节会变成「可勾的末端节点」。
     进度条同理：a/b 走 leaves()，按整棵子树算，不跟着筛选跳。 */
  if (!hasKids || !open) return;
  const kept = (n.children || []).filter((c) => nodeKeep(c, f));
  if (!kept.length && nfOn(f)) {
    const hint = document.createElement("div");
    hint.className = "empty-node";
    hint.style.paddingLeft = (8 + (dep + 1) * 20) + "px";
    hint.textContent = "没有符合「" + nfLabel(f).replace(/^筛选:/, "") + "」的子环节";
    box.appendChild(hint);
  }
  kept.forEach((c) => {
    c.pid = kind === "project" ? n.id : n.pid;
    drawNode(c, box, dep + 1, "node");
  });
}

/* ---- 多选：整行可点，三种点法（参照 Maya 大纲） ----
   直接点     → 选它 + 全部后代（再点一次取消，同样连带后代）
   Ctrl + 点  → 加选 / 减选（连带后代）
   Shift + 点 → 从锚点到这条之间，屏幕顺序上全部选中
   只就地更新选中态和计数条，**不整树重绘**（重绘会丢滚动位置）。 */
function bindMulti(r, n) {
  // 初次渲染先按（派生）选择态把外观摆正（比如 Shift 选完之后 render 出来的行）
  r.classList.toggle("picked", nodeFullyOn(Number(n.id)));

  /* 复选框必须**自己接管点击**，而且**绝对不能 preventDefault**。
     踩过的坑（2026-09-27）：点小方块时
        1. 浏览器把 checked 翻过去（native activation）
        2. 我们的 handler 跑，按 selNodes 写 checked  → 这时是对的
        3. handler 里若调了 preventDefault()，Chromium 会在 handler **返回之后**
           回滚这次 activation → checked 被抹回旧值
     结果就是"勾了但没显示勾，点下一个时上一个才补上"（差一拍）。
     正解：不拦默认行为；handler 里只更新选择集，然后**下一帧**再把
     checked 对齐回 selNodes（那时浏览器的 activation 已经彻底结束）。 */
  const cp0 = r.querySelector(".pick");
  if (cp0) {
    cp0.onclick = (ev) => {
      ev.stopPropagation();     // 别再触发整行那套（一次点击只走一遍）
      /* 不传 on → 由 selOne 按**显示态**取反（见那里的注释：
         残缺的父项显示未勾时，点一下应当是"整棵全选"，不是"取消"）。 */
      selOne(n, undefined);
      selAnchor = Number(n.id);
      syncBulkBar();
      /* 关键：立刻对齐一次（覆盖 native 的结果），
         再在微/宏任务之后再对齐一次（覆盖浏览器可能发生的回滚）。 */
      repaintSelection();
      Promise.resolve().then(repaintSelection);
      setTimeout(repaintSelection, 0);
    };
  }

  r.onclick = (ev) => {
    /* 行内的操作按钮（目录 / ⋯）和折叠箭头各有各的活，别抢。
       **但按住 Ctrl / Shift 时必须让选择逻辑接管** —— 否则用户想
       "Shift 点到最右那条"，鼠标稍微偏到行尾的按钮上，整段范围选就被
       这一下 return 吞掉，表现就是"最后点的那条没被选中/没勾上"。
       所以只在**没按修饰键**时才让开。 */
    const mod = ev.ctrlKey || ev.metaKey || ev.shiftKey;
    if (!mod && (ev.target.closest(".ops") || ev.target.closest(".tw"))) return;
    /* 点在复选框上时它自己已经处理过了（见上面的 cp0.onclick），
       这里直接让开，免得一次点击走两遍把状态翻回去。 */
    if (ev.target.closest(".pick")) return;
    /* 修饰键 + 落在折叠箭头上：让 .tw 自己的 handler 也走一次折叠？
       不行 —— 那样 Shift 点箭头会既折叠又范围选，用户会懵。
       约定：**按住修饰键时箭头不折叠**，整行只当选择热区。 */
    if (mod && ev.target.closest(".tw")) ev.stopPropagation();
    /* 这里**不要** preventDefault —— 行内可能还有别的原生控件，
       拦掉默认行为会连带影响它们（复选框那次"勾了不显示"就是这么来的）。 */
    if (!mod) ev.stopPropagation();

    if (ev.shiftKey && selAnchor !== null) {
      /* 范围选择：整段"设"成同一个状态，而不是逐条取反 ——
         逐条取反的话，段里本来就选中的会被反选掉，结果乱七八糟。
         目标段该选还是该取消，看**点的那条**当前的显示态
         （用派生：残缺的父项算"没勾上"，此时 Shift 过去就是整段选上）。 */
      selRange(n, !nodeFullyOn(Number(n.id)));
    } else if (ev.ctrlKey || ev.metaKey) {
      selOne(n, undefined);               // 取反，连带后代
      selAnchor = Number(n.id);
    } else {
      selOne(n, undefined);               // 单选：它 + 后代
      selAnchor = Number(n.id);
    }
    // 只重画受影响的行外观，不整树重建
    repaintSelection();
    syncBulkBar();
  };
}

/* 折叠箭头 / 子环节筛选框 / 叶子状态点 */
function bindRowMenus(r, n, kind, hasKids, open) {
  /* 折叠：点击箭头切换展开 —— 多选态下按住修饰键时不折叠，
     让这一下专属于范围选择（见上面 r.onclick 的说明）。 */
  if (hasKids) r.querySelector(".tw").onclick = (ev) => {
    if (ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) return;
    n.collapsed = open ? 1 : 0;
    call("set_collapsed", kind, n.id, n.collapsed);
    render();
  };

  /* 子环节筛选框（搜索框 + 状态 + 制作人），只筛这行直属的那一层 */
  if (hasKids) r.querySelector(".flt").onclick = () => openNodeFilter(n, kind, r.querySelector(".flt"));

  /* 叶子节点：点状态点切换完成 */
  if (kind === "node" && !hasKids) {
    const dot = r.querySelector(".dot");
    dot.style.cursor = "pointer";
    dot.onclick = async () => {
      await call("set_done", n.id, n.done ? 0 : 1);
      await reload();
    };
  }
}

/* 双击标题就地改名 */
function bindRename(r, n, kind) {
  const t = r.querySelector(".title");
  t.ondblclick = () => {
    // draggable 会拦住鼠标划选，改名期间先摘掉，结束再装回去
    r.draggable = false;
    t.contentEditable = "true";
    t.focus();
    document.getSelection().selectAllChildren(t);
    const done = async () => {
      t.contentEditable = "false";
      r.draggable = true;
      const v = t.textContent.trim();
      if (v && v !== n.title) { await call("rename", kind, n.id, v); await reload(); }
      else render();
    };
    t.onblur = done;
    t.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); t.blur(); }
      if (e.key === "Escape") { t.textContent = n.title; t.blur(); }
    };
  };

  /* 状态：认准 `.badge.st` —— 一行上还有「今日必做 / 置顶 / 已归档」等别的 badge */
  r.querySelector(".badge.st").onclick = () => {
    const list = kind === "project" ? D.status.project : D.status.node;
    popup(list.map((s) => ({ label: s, fn: async () => { await call("set_status", kind, n.id, s); await reload(); } })), r.querySelector(".badge.st"));
  };

  /* 目录 */
  r.querySelector(".open").onclick = () => {
    const items = (n.links || []).map((l) => ({
      label: l.label,
      fn: async () => { await call("open_dir", l.path); },
    }));
    items.push({ sep: true });
    items.push({ label: "添加目录…", fn: async () => {
      const res = await call("pick_dir");
      if (!res || !res.ok || !res.path) { if (res && res.msg) toast(res.msg); return; }
      /* 取消（null）要**直接退出** —— 以前 `(prompt(...) || "")` 把取消变成空串，
         照样往库里写了一条没有名字的目录。 */
      const v = prompt("这个目录叫什么？", "目录");
      if (v === null) return;
      await call("add_link", kind, n.id, v.trim(), res.path);
      await reload();
    } });
    if (n.links && n.links.length) {
      items.push({ sep: true });
      (n.links).forEach((l) => items.push({
        label: "移除「" + l.label + "」", dim: true,
        fn: async () => { await call("delete_link", l.id); await reload(); },
      }));
    }
    popup(items, r.querySelector(".open"));
  };

  /* 操作菜单 */
  r.querySelector(".mini.more").onclick = () => {
    const items = [
      { label: "新增子环节", fn: () => {
          /* 一次可以填一整批：`s001,s003A,s006-009`，弹窗里有实时预览 */
          const prj = findProject(kind === "project" ? n.id : n.pid);
          const label = kind === "project"
            ? n.title
            : ((prj ? prj.title : "项目") + " › " + n.title);
          openNodesDialog(
            kind === "project" ? n.id : n.pid,
            kind === "project" ? null : n.id,
            label
          );
      } },
      { label: "编辑截止日期", fn: async () => {
          const v = prompt("截止日期 YYYY-MM-DD（留空清除）", n.deadline || "");
          if (v !== null) { await call("set_field", kind, n.id, "deadline", v.trim()); await reload(); }
      } },
      { label: "编辑备注", fn: async () => {
          const v = prompt("备注", n.note || "");
          if (v !== null) { await call("set_field", kind, n.id, "note", v.trim()); await reload(); }
      } },
      /* 制作人跟备注挨着：都是这条「人 + 说明」的补充信息 */
      { label: n.artist ? "编辑制作人（" + n.artist + "）" : "编辑制作人", fn: async () => {
          const v = prompt("制作人 / 谁在做（留空清除）", n.artist || "");
          if (v !== null) { await call("set_field", kind, n.id, "artist", v.trim()); await reload(); }
      } },
    ];
    if (kind === "project") {
      items.push({ label: n.client ? "改客户（" + n.client + "）" : "指定客户…",
        fn: () => pickClientFor(n, r.querySelector(".mini.more")) });
      /* 分类能改、能新建（新建完把这个项目直接挪过去）。 */
      items.push({ label: "改分类（" + catName(n.category) + "）",
        fn: () => pickGroupFor(n, r.querySelector(".mini.more")) });
      items.push({ label: "登记款项", fn: async () => {
        /* 弹窗里的项目/客户/环节列表来自 finance_data，所以先取一次 */
        await loadFinance();
        openFinDialog(null, n.id);
      } });
      items.push({ label: n.priority ? "取消今日必做" : "设为今日必做", fn: async () => {
        await call("set_field", "project", n.id, "priority", n.priority ? 0 : 1); await reload();
      } });
      /* 置顶的项目排在列表最前，和拖拽排序是配套的 */
      items.push({ label: n.pinned ? "取消常驻顶部" : "常驻顶部", fn: async () => {
        await call("set_field", "project", n.id, "pinned", n.pinned ? 0 : 1); await reload();
      } });
      items.push({ sep: true });
      /* 归档 = 收进抽屉（后端默认不发）。关掉「隐藏归档项目」时还能看见，
         所以这里必须有回来的路，否则归档完就再也捞不出来了。
         取消时两条路各自要还原：字段归档清字段、状态归档改回「已交付」。 */
      items.push(isArchived(n)
        ? { label: n.status === "归档" ? "取消归档（状态改回「已交付」）" : "取消归档",
            fn: async () => {
            if (n.archived) { await call("set_field", "project", n.id, "archived", 0); }
            if (n.status === "归档") { await call("set_status", "project", n.id, "已交付"); }
            await reload();
          } }
        : { label: "归档（关掉「隐藏归档」可看回）", dim: true, fn: async () => {
            await call("set_field", "project", n.id, "archived", 1); await reload();
          } });
    }
    items.push({ sep: true });
    items.push({ label: "删除", dim: true, fn: async () => {
      if (confirm("删除「" + n.title + "」" + (hasKids ? " 及其全部子环节" : "") + "？")) {
        await call("delete", kind, n.id); await reload();
      }
    } });
    popup(items, r.querySelector(".mini.more"));
  };
}

/* ---------- 分类（分组） ----------
   商业 / 个人这类顶层的筐现在是**库里的数据**（groups 表），可以新建、改名、删除。
   键（g.key）是稳定标识，项目用 category 挂它 —— 所以改名不会动到任何项目。
   代码里只保留两个兜底：找不到的键就显示成键本身（别显示空白）。 */
const groupOf = (key) => (D && D.groups ? D.groups.find((g) => g.key === key) : null) || null;
const catName = (key) => {
  const g = groupOf(key);
  return (g && g.name) || key || "未分类";
};

/* 双击分类名就地改（跟项目 / 环节的标题一套手感）。
   分组行不带 draggable，所以不用像 bindRename 那样先摘掉拖拽。
   改名只动 name，项目挂的是 g.key —— 所以改完没有任何项目会挪窝。 */
function bindGroupRename(r, g) {
  const t = r.querySelector(".title");
  t.ondblclick = () => {
    t.contentEditable = "true";
    t.focus();
    document.getSelection().selectAllChildren(t);
    const done = async () => {
      t.contentEditable = "false";
      const v = t.textContent.trim();
      if (v && v !== g.name) {
        const res = await call("group_rename", g.key, v);
        if (!res || !res.ok) alert((res && res.msg) || "改不了名");
      }
      /* 空名 / 同名 / 改名失败都要重画：把编辑态的痕迹抹掉，显示库里的真值 */
      await reload();
    };
    t.onblur = done;
    t.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); t.blur(); }
      /* Esc = 放弃：先把文字放回去，再 blur —— 否则 done 会拿着改了一半的名字去改名 */
      if (e.key === "Escape") { t.textContent = g.name; t.blur(); }
    };
  };
}

/* 分组行尾的 ⋯：重命名 / 新建 / 删除。
   删除只让删**空筐**（后端还会再挡一道：有项目挂靠就拒绝并说明几个）。 */
function bindGroupMenu(r, g) {
  r.querySelector(".mini.menu").onclick = () => {
    const items = [
      { label: "重命名分类…", fn: async () => {
          const v = prompt("分类名", g.name);
          if (v === null) return;
          const res = await call("group_rename", g.key, v.trim());
          if (!res || !res.ok) { alert((res && res.msg) || "改不了名"); return; }
          await reload();
      } },
      { label: "新建分类…", fn: async () => {
          const v = prompt("新分类叫什么？", "");
          if (v === null || !v.trim()) return;
          const res = await call("group_add", v.trim());
          if (!res || !res.ok) { alert((res && res.msg) || "建不了分类"); return; }
          await reload();
      } },
      { sep: true },
      { label: "删除这个分类", dim: true, fn: async () => {
          if (!confirm("删除分类「" + g.name + "」？\n\n" +
                       "里面还挂着项目的话删不掉，得先把项目挪到别的分类（或删掉）。")) return;
          const res = await call("group_delete", g.key);
          if (!res || !res.ok) { alert((res && res.msg) || "删不掉"); return; }
          await reload();
      } },
    ];
    popup(items, r.querySelector(".mini.menu"));
  };
}

/* 某个项目「改分类」。在项目行 ⋯ 菜单里用。
   顺带把「新建分类」放进来，并且建完**直接把这个项目挪过去** ——
   十有八九就是为它建的。 */
function pickGroupFor(n, anchor) {
  const items = (D.groups || []).map((g) => ({
    label: (g.key === n.category ? "✓ " : "　") + g.name,
    fn: async () => {
      if (g.key === n.category) return;
      const res = await call("set_group", n.id, g.key);
      if (!res || !res.ok) { alert((res && res.msg) || "没改成"); return; }
      await reload();
    },
  }));
  items.push({ sep: true });
  items.push({ label: "新建分类…", fn: async () => {
    const v = prompt("新分类叫什么？", "");
    if (v === null || !v.trim()) return;
    const res = await call("group_add", v.trim());
    if (!res || !res.ok) { alert((res && res.msg) || "建不了分类"); return; }
    const mv = await call("set_group", n.id, res.key);
    if (!mv || !mv.ok) alert((mv && mv.msg) || "分类建好了，但项目没挪过去");
    await reload();
  } });
  popup(items, anchor);
}

function renderFoot() {
  const foot = $("#foot");
  if (!foot) return;
  /* 后端刚改过库/加载失败时 stats 可能缺失；直接取字段会全是 undefined */
  const s = D.stats || {};
  const n = (v) => (v === 0 || v ? v : 0);
  foot.innerHTML =
    "今日必做 <b style='color:var(--tx)'>" + n(s.today) + "</b>" +
    " · 进行中 " + n(s.running) +
    " · <span style='color:var(--amber)'>阻塞 " + n(s.blocked) + "</span>" +
    (s.stale ? " · <span style='color:var(--red)'>停滞 " + s.stale + "</span>" : "") +
    (s.due ? " · <span style='color:var(--amber)'>临期/逾期 " + s.due + "</span>" : "") +
    " · <span style='color:var(--tx3)'>点此查看明细</span>";
  foot.style.cursor = "pointer";
  foot.onclick = showAlerts;
}

async function reload() {
  /* 归档项目默认不要（后端就不发）；关掉「隐藏归档项目」才一次带回来。
     例外：多选里勾了「归档」这个状态时要放开 —— 不然筛了「归档」出来一棵空树，
     看着像项目丢了（见 needArchData）。 */
  const fresh = await call("load", needArchData() ? 1 : 0);
  /* load 失败（桥没起来/后端抛异常）会返回 {ok:false,err}。此时**保留上一版 D**、
     只提示一声 —— 直接把它当数据用会把整棵树清空，看着像"数据全没了"。 */
  if (!fresh || fresh.ok === false || !Array.isArray(fresh.groups)) {
    if (fresh && fresh.err) toast("加载失败：" + fresh.err);
    return;
  }
  D = fresh;
  loadedArch = needArchData();
  /* 状态词表改过版（比如环节那套 9 态），localStorage 里可能留着旧词。
     留着谁也匹配不上的状态，打开就是空树 —— 宁可自己把不认识的那些剔掉
     （多选：剩下几个认识的照留，不是整组丢掉）。 */
  const known = ((D.status || {}).project || []);
  const keepP = pStatList().filter((s) => known.indexOf(s) >= 0);
  if (keepP.length !== pStatList().length) { ui.pStatSel = keepP; saveUI(); }
  /* 每层筛选里那些状态词同理（pStatSel 管主菜单，nf 管各行自己的筛选框） */
  (D.groups || []).forEach((g) => (g.projects || []).forEach((p) => {
    nfHeal(p, "project");
    const walk = (list) => (list || []).forEach((n) => { nfHeal(n, "node"); walk(n.children); });
    walk(p.children);
  }));
  syncTreeFilter();
  render();
  renderFoot();
}

/* ---------- 主题与顶栏 ---------- */

/* 产品名 / 版本号：**一个都不在前端写死**，全部来自 `Api.load()` 里的
   `app`（源头是 app/version.py）。升版本只改那一个文件。
   顶栏只留图标不再写字，所以这里只需要管 `document.title` 和设置页的版本行 ——
   `document.title` 跟着写一遍是因为：pywebview 的窗口标题是 Python 给的，
   浏览器里直接打开 index.html 时才是这里生效，两边同源就不会打架。 */
function applyAppInfo() {
  const a = (D && D.app) || {};
  if (a.title) document.title = a.title;
  const ver = $("#setVer");
  if (ver) ver.textContent = a.version_line || "";
}

function applyTheme() {
  const th = D.settings.theme || "dark";
  const light = th === "light" || (th === "auto" && matchMedia("(prefers-color-scheme: light)").matches);
  document.body.classList.toggle("light", light);
  $$("#segTheme button").forEach((b) => b.classList.toggle("act", b.dataset.t === th));
}

/* 页面页签：项目 / 活跃 / 财务，三个平级，点页签整页切换 */
function setView(v) {
  ["tree", "heat", "fin"].forEach((k) => { $("#" + k).style.display = k === v ? "block" : "none"; });
  $("#treeBar").style.display = v === "tree" ? "flex" : "none";
  $$("#segPage button").forEach((b) => b.classList.toggle("act", b.dataset.v === v));
  if (v === "tree") render();
  if (v === "heat") loadHeat();
  if (v === "fin") loadFinance();
}

/* 置顶按钮的亮灭一律问窗口，不看库里的设置。
   库里那个 `on_top` 只是「下次启动要不要置顶」，窗口当前是什么状态还会被
   托盘菜单和提醒拉前台改掉。以前按钮只读设置：启动时窗口本来就置着顶、按钮也亮着，
   用户点一下其实是把置顶**关掉**了 —— 主观上就是「置顶按钮没用」。 */
async function syncTop() {
  let r;
  try { r = await call("get_top"); } catch (e) { return; }   // 拿不到就别把按钮闪来闪去
  onTop = !!r.top;
  $("#btnTop").classList.toggle("on", onTop);
}

/* 顶栏日期：应用可能开着过夜，所以刷新时要能重画（不然还显示昨天） */
function paintDate() {
  const d = new Date();
  const txt = (d.getMonth() + 1) + "-" + d.getDate() + " 周" + WD_CN[d.getDay()];
  const el = $("#date");
  el.textContent = txt;
  el.title = d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate() + "（点右边的箭头刷新）";
  return txt;
}

function bindHeader() {
  paintDate();

  /* 刷新按钮（顶栏日期右边那个环形箭头，内联 SVG，不依赖外部图片）：
     点一下 = ① 重画日期（可能已经跨天）② 重新读数据文件 ③ 按当前页补刷
     （财务表 / 活跃热力图是单独拉的，只刷树等于什么都没变 —— 交给 refreshAll）。
     图标转一圈当反馈，转完把 class 摘掉，下次点还能再转。 */
  $("#btnRefresh").onclick = async (e) => {
    const svg = e.currentTarget.querySelector("svg");
    if (svg) {
      svg.classList.remove("spin");
      void svg.getBoundingClientRect();      // 逼一次重排，动画才能重放
      svg.classList.add("spin");
    }
    paintDate();
    await refreshAll();
    toast("已重新读取数据文件");
  };

  /* 认 closest("button")：段控件两侧有内边距，点在按钮**边缘的容器**上
     e.target 就不是 button 了 —— 那一下会被静默吞掉（点了没反应）。 */
  $("#segPage").onclick = (e) => {
    const b = e.target.closest("button[data-v]");
    if (b) setView(b.dataset.v);
  };

  /* 变更记录：「查看更多」开全量弹窗，「加载更多」按天往回翻页 */
  $("#heatLogMore").onclick = () => openLogsDialog();
  $("#logClose").onclick = () => modal("#ovLogs", false);
  $("#logMore").onclick = () => loadMoreLogs();
  $("#btnTop").onclick = async () => {
    const res = await call("set_top", !onTop);
    if (!res.ok) { toast("改不了置顶：没找到看板窗口"); return; }
    /* 用返回的真实状态，别拿「我刚才点的是什么」当结果 */
    onTop = !!res.top;
    $("#btnTop").classList.toggle("on", onTop);
  };
  /* 被提醒拉前台会顺手置顶（服务器侧 set_top），回到窗口时把按钮纠正回来 */
  window.addEventListener("focus", syncTop);
  $("#btnSet").onclick = () => openSettings();
  $("#btnNew").onclick = () => openNewDialog();
  $("#btnCheck").onclick = () => openCheck(false);
  $("#treeFilter").onclick = (e) => openTreeFilter(e.currentTarget);
  bindBulkBar();
}

/* ---------- 多选批量的按钮绑定 ---------- */

function bindBulkBar() {
  $("#treeMulti").onclick = () => {
    ui.multi = ui.multi ? 0 : 1;
    if (!ui.multi) selNodes.clear();      // 退出就清干净，不留"尾巴选择"
    saveUI();
    render();
  };
  $("#bulkExit").onclick = () => { ui.multi = 0; selClear(); saveUI(); render(); };
  $("#bulkNone").onclick = () => { selClear(); repaintSelection(); syncBulkBar(); };
  /* 「全选本层」按用户要求去掉了 —— 现在点父级自带全选整棵子树，比按钮直接。 */
  $("#bulkStat").onclick = (e) => {
    if (!selCount()) { toast("先勾选要改的环节"); return; }
    const sts = (D.status || {}).node || [];
    const items = sts.map((s) => ({
      label: "改成「" + s + "」",
      fn: async () => {
        const r = await call("bulk_update", "node", Array.from(selNodes), s, null);
        if (!r.ok) { toast(r.msg || "改失败"); return; }
        await reload();
        toast("已改 " + r.done + " 条为「" + s + "」" + (r.skipped ? "，跳过 " + r.skipped + " 条" : ""));
      },
    }));
    popup(items, e.currentTarget);
  };
  $("#bulkArtist").onclick = (e) => {
    if (!selCount()) { toast("先勾选要改的环节"); return; }
    /* 制作人候选：库里出现过的（项目层 + 环节层） */
    const names = new Set();
    eachProject((p) => { if (p.artist) names.add(p.artist); });
    eachNode((n) => { if (n.artist) names.add(n.artist); });
    const items = Array.from(names).sort().map((nm) => ({
      label: nm,
      fn: async () => {
        const r = await call("bulk_update", "node", Array.from(selNodes), null, nm);
        if (!r.ok) { toast(r.msg || "改失败"); return; }
        await reload();
        toast("已把 " + r.done + " 条的制作人改成 " + nm);
      },
    }));
    items.push({
      label: "清空制作人",
      fn: async () => {
        const r = await call("bulk_update", "node", Array.from(selNodes), null, "");
        if (!r.ok) { toast(r.msg || "改失败"); return; }
        await reload();
        toast("已清空 " + r.done + " 条的制作人");
      },
    });
    items.push({
      label: "手输一个…",
      fn: () => {
        const v = prompt("把这些环节的制作人改成：", "");
        if (v === null) return;
        call("bulk_update", "node", Array.from(selNodes), null, String(v).trim()).then(async (r) => {
          if (!r.ok) { toast(r.msg || "改失败"); return; }
          await reload();
          toast("已改 " + r.done + " 条的制作人");
        });
      },
    });
    popup(items, e.currentTarget);
  };
}

/* ---------- 快捷键（M4） ---------- */

function bindHotkeys() {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closePopup();
      /* Esc 关弹窗（设置里是这么写的）。#ovBlock 例外：
         那是「没打卡不给用」的拦截层，必须处理完才能走。 */
      $$(".ov").forEach((o) => { if (o.id !== "ovBlock") o.style.display = "none"; });
      return;
    }
    if (!e.ctrlKey || e.altKey || e.metaKey) return;
    const k = (e.key || "").toLowerCase();
    const map = {
      n: () => openNewDialog(),
      "1": () => setView("tree"),
      "2": () => setView("heat"),
      "3": () => setView("fin"),
      d: () => openCheck(false),
      ",": () => openSettings(),
    };
    const fn = map[k];
    if (!fn) return;
    e.preventDefault();
    fn();
  });
}

/* ---------- 新建项目 ---------- */

/* 客户框：可输入的组合框（input + datalist）。
   以前是个 <select>，库里没客户时就只有「（不指定）」一项 —— 等于客户填不了。
   现在直接敲名字就行，库里没有的会在创建时自动建。 */
function fillNewClients(keep) {
  const list = D.clients || [];
  $("#clientOpts").innerHTML = list
    .map((c) => '<option value="' + esc(c.name) + '"></option>').join("");
  $("#newClient").value = keep ? String(keep) : "";
}

/* 分类选择器按库里的分类现画（商业 / 个人 只是出厂值，用户能加能删能改名）。
   `cat` 指向一个已经不存在的分类时（刚把那个分类删了）退到第一个，
   否则点「创建」会带着一个野键过去。 */
function fillCatSeg() {
  const seg = $("#segCat");
  const gs = (D && D.groups) || [];
  if (!gs.some((g) => g.key === cat)) cat = gs.length ? gs[0].key : "";
  seg.innerHTML = gs.map((g) =>
    '<button data-c="' + esc(g.key) + '"' + (g.key === cat ? ' class="act"' : "") +
    ">" + esc(g.name) + "</button>").join("");
}

/* 打开新建项目页。defaultCat 是从分组行右边的 ＋ 进来时带的分类键：
   点哪一行就默认归哪一类（键不存在就忽略，交给 fillCatSeg 兜底）。
   ★ 分类选择器每次都在这里现画 —— 分类的增删改会立刻反映到按钮上，
     所以「库里加了一个分类，弹窗里就有它」不需要额外刷新。 */
function openNewDialog(defaultCat) {
  if (defaultCat && (D.groups || []).some((g) => g.key === defaultCat)) cat = defaultCat;
  fillCatSeg();
  fillNewClients("");
  $("#newTitle").value = "";
  $("#newPath").value = "";
  $("#newDl").value = "";
  $("#newAmount").value = "";
  $("#newPaid").value = "";
  $("#newTax").value = "";
  $("#newPaidDate").value = isoOf(new Date());
  modal("#ovNew", true);
  $("#newTitle").focus();
}

function bindNew() {
  $("#segCat").onclick = (e) => {
    const b = e.target.closest("button[data-c]");
    if (!b) return;
    $$("#segCat button").forEach((x) => x.classList.remove("act"));
    b.classList.add("act");
    cat = b.dataset.c;
  };
  $("#btnPick").onclick = async () => {
    const res = await call("pick_dir");
    if (!res.ok) { alert(res.msg || "打不开选择窗口"); return; }
    if (res.path) $("#newPath").value = res.path;
  };
  $("#newCancel").onclick = () => modal("#ovNew", false);
  $("#newOk").onclick = async () => {
    const t = $("#newTitle").value.trim();
    if (!t) { $("#newTitle").focus(); return; }
    /* 款项一起提交：金额校验在 Python 侧做，填错就整体不建项目，
       免得留下一个「以为记上了其实没记」的空项目 */
    const money = {
      contract: $("#newAmount").value.trim(),
      paid: $("#newPaid").value.trim(),
      /* 只按人民币结算，币种不出现在界面上；字段留着是为了老库里的外币记录不被打乱 */
      currency: "CNY",
      tax_rate: $("#newTax").value.trim(),
      date: $("#newPaidDate").value || isoOf(new Date()),
    };
    /* 第三参数可以直接给客户名，Python 侧找不到就建一个 */
    const res = await call("add_project", t, cat, $("#newClient").value.trim() || 0,
                           $("#newPath").value.trim(), money);
    if (!res.ok) { alert(res.msg || "创建失败"); return; }
    if ($("#newDl").value) await call("set_field", "project", res.id, "deadline", $("#newDl").value);
    modal("#ovNew", false);
    await reload();
  };
}

/* ---------- 批量新增子环节 ---------- */

/* 当前挂靠目标：project_id 定项目，parent_id 为 null 表示挂在项目根上 */
let nodeTarget = null;

/* 遍历全部项目 / 环节（**新增代码一律走这里**，别再手写 `D.groups.forEach` ——
   那样容易漏掉 `|| []` 防御，`D.groups` 为空的瞬间就抛）。
   少数几处确实需要"分组"这个上下文（渲染分组行、按分类取项目）
   才直接遍历 D.groups，别的地方用这两个函数就够。 */
function eachProject(fn) {
  (D.groups || []).forEach((g) => (g.projects || []).forEach((p) => fn(p, g)));
}
function eachNode(fn) {
  const walk = (arr) => (arr || []).forEach((x) => { fn(x); walk(x.children); });
  eachProject((p) => walk(p.children));
}

/* 按 id 找项目 / 环节（不管钉在哪棵子树下）。走索引，O(1)。 */
const findProject = (id) => idxOf().projects.get(Number(id)) || null;
const findNode = (id) => idxOf().nodes.get(Number(id)) || null;

/* 同级已有的标题：预览里把它们划掉，一眼看出哪些会真的新建 */
function siblingTitles(projectId, parentId) {
  const prj = findProject(projectId);
  if (!prj) return [];
  if (!parentId) return (prj.children || []).map((c) => c.title);
  const find = (list) => {
    for (const n of list) {
      if (n.id === parentId) return n;
      const r = find(n.children || []);
      if (r) return r;
    }
    return null;
  };
  const p = find(prj.children || []);
  return p ? (p.children || []).map((c) => c.title) : [];
}

async function refreshNodesPrev() {
  const box = $("#nodesPrev");
  const spec = $("#nodesSpec").value;
  if (!spec.trim()) {
    box.className = "prev";
    box.innerHTML = "上面填一行，这里会实时显示将要创建的名字。";
    return;
  }
  let r = { ok: false, msg: "解析中…" };
  try { r = await call("parse_nodes", spec); } catch (e) { r = { ok: false, msg: String(e) }; }
  if (!r.ok) {
    box.className = "prev bad";
    box.innerHTML = "✕ " + esc(r.msg || "解析失败");
    return;
  }
  const have = new Set(siblingTitles(nodeTarget.project_id, nodeTarget.parent_id)
    .map((s) => String(s).trim().toLowerCase()));
  const tags = r.items.map((t) => {
    const dup = have.has(t.toLowerCase());
    return '<span class="tag ' + (dup ? "dup" : "new") + '" title="' +
      (dup ? "已存在，会跳过" : "将新建") + '">' + esc(t) + "</span>";
  }).join(" ");
  const fresh = r.items.filter((t) => !have.has(t.toLowerCase())).length;
  box.className = "prev";
  box.innerHTML = "<b>将创建 <span class='n'>" + fresh + "</span> 个</b>"
    + (fresh < r.count ? "（另有 " + (r.count - fresh) + " 个已存在，会跳过）" : "")
    + "<div style='margin-top:4px'>" + tags + "</div>";
}

function openNodesDialog(projectId, parentId, label) {
  nodeTarget = { project_id: projectId, parent_id: parentId || null, label: label || "" };
  $("#nodesTo").textContent = "挂到：" + (label || "（当前项目）");
  $("#nodesSpec").value = "";
  refreshNodesPrev();
  modal("#ovNodes", true);
  $("#nodesSpec").focus();
}

function bindNodes() {
  let t = null;
  $("#nodesSpec").oninput = () => { clearTimeout(t); t = setTimeout(refreshNodesPrev, 160); };
  $("#nodesSpec").onkeydown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#nodesOk").click(); }
  };
  $("#nodesCancel").onclick = () => modal("#ovNodes", false);
  $("#nodesOk").onclick = async () => {
    const spec = $("#nodesSpec").value.trim();
    if (!spec) { $("#nodesSpec").focus(); return; }
    const res = await call("add_nodes", nodeTarget.project_id, nodeTarget.parent_id, spec);
    if (!res.ok) { toast(res.msg || "创建失败"); return; }
    modal("#ovNodes", false);
    const sk = (res.skipped || []).length;
    toast("已创建 " + res.count + " 个环节" + (sk ? "，跳过 " + sk + " 个已存在的" : ""));
    await reload();
  };
}

/* 项目行菜单：选客户（可现场新建） */
function pickClientFor(n, anchor) {
  const items = (D.clients || []).map((c) => ({
    label: c.name + (c.id === n.client_id ? "　✓" : ""),
    fn: async () => { await call("set_client", n.id, c.id); await reload(); },
  }));
  if (!items.length) items.push({ label: "（还没有客户，往下新建）", dim: true, fn: () => {} });
  if (n.client_id) items.push({ label: "不挂客户", dim: true,
    fn: async () => { await call("set_client", n.id, 0); await reload(); } });
  items.push({ sep: true });
  items.push({ label: "新建客户…", fn: async () => {
    const v = prompt("客户名", n.client || "");
    if (v === null || !v.trim()) return;
    const r = await call("set_client", n.id, v.trim());
    if (r && r.ok === false) { alert(r.msg || "设置失败"); return; }
    await reload();
  } });
  popup(items, anchor);
}

/* ---------- 设置 ---------- */

async function loadDbInfo() {
  /* 数据文件的来路（默认位置 / 设置里选的 / 环境变量）。
     以前写成标题边上一小行灰字，太占地方且每行都拖着尾巴 —— 现在整行都收进
     「数据文件」这四个字的 title 里：鼠标停上去才出来。 */
  const r = await call("db_info");
  const lb = $("#setPathLb");
  if (lb) {
    if (!r || !r.ok) lb.title = "当前读取的数据文件";
    else {
      const bits = ["来源：" + r.from];
      if (r.from !== "默认位置") bits.push("默认：" + r.default);
      if (!r.writable) bits.push("⚠ 目录不可写");
      else if (!r.exists) bits.push("⚠ 文件还不存在，启动时新建");
      lb.title = bits.join("\n");
    }
  }
  /* 多机开关存在应用级配置里（不在库里），所以跟着 db_info 一起回来 */
  const sb = $("#setShare");
  if (sb && r && r.ok) {
    sb.classList.toggle("on", !!r.share);
    sb.textContent = r.share ? "已开启" : "已关闭";
  }
}

/* ---------- 多机同时打开：别人改过没有 ---------- */

/* 这条提示要一直挂着，直到用户真的重新加载 —— 所以不用 toast（两秒就没了），
   用顶部一条常驻提示。开着"多机同时打开"才会出现。 */
function showSyncBar(text, reloadable, calm) {
  const bar = $("#syncBar");
  if (!bar) return;
  $("#syncTxt").textContent = text;
  bar.classList.toggle("calm", !calm);
  bar.classList.toggle("on", true);
  const rb = $("#syncReload");
  if (rb) rb.style.display = reloadable ? "" : "none";
}

function hideSyncBar() {
  const bar = $("#syncBar");
  if (bar) bar.classList.remove("on");
}

/* 重新拉数据。注意别只调 reload()：当前停在财务/活跃页时，
   那张表的数据是单独拉的，只刷树等于什么都没变。 */
async function refreshAll() {
  await reload();
  if ($("#fin") && $("#fin").style.display === "block") await loadFinance();
  if ($("#heat") && $("#heat").style.display === "block") await loadHeat();
  hideSyncBar();
}

async function syncTick() {
  if (!D || !D.share_db) { hideSyncBar(); return; }
  const r = await call("sync_state");
  if (!r || !r.ok || !r.share) { hideSyncBar(); return; }
  const hosts = (r.holders || []).map((h) => h && h.host).filter(Boolean);
  if (r.changed) {
    showSyncBar("另一台机器刚改过数据，这边显示的可能已经不是最新的。", true, false);
  } else if (hosts.length) {
    showSyncBar("多机共享中 · " + hosts.join("、") + " 也在用这个数据文件。", false, true);
  } else {
    hideSyncBar();
  }
}

function openSettings() {
  const s = D.settings;
  $("#setPath").textContent = s.db_path || "";
  $("#setExport").textContent = s.export_dir || "（未设置，默认与数据文件同目录的 export/）";
  loadDbInfo();
  const auto = s.autostart === "1";
  $("#setAuto").textContent = auto ? "已开启" : "已关闭";
  $("#setAuto").classList.toggle("on", auto);
  const top = s.on_top === "1";
  $("#setTop").textContent = top ? "已开启" : "已关闭";
  $("#setTop").classList.toggle("on", top);
  /* 兼容模式：看门狗降级过就会自动置 1，平时显示关闭 */
  const nsb = s.nosandbox === "1";
  $("#setSandbox").textContent = nsb ? "已开启" : "已关闭";
  $("#setSandbox").classList.toggle("on", nsb);
  $$("#segLv button").forEach((b) => b.classList.toggle("act", b.dataset.l === (s.remind_level || "2")));
  $("#setTime").value = s.checkin_time || "09:00";
  $("#setStale").value = s.stale_days || "3";
  const nt = s.notify_enabled !== "0";
  $("#setNotify").textContent = nt ? "已开启" : "已关闭";
  $("#setNotify").classList.toggle("on", nt);
  $("#setNag").value = s.nag_minutes || "30";
  $("#setAlert").value = s.alert_interval || "180";
  applyTheme();
  loadSnaps();
  modal("#ovSet", true);
}

/* ---------- 数据快照（M4） ---------- */

async function loadSnaps() {
  const r = await call("snapshot_list");
  $("#setSnapDir").textContent = r.dir || "";
  const items = r.items || [];
  $("#snapList").innerHTML = items.length
    ? items.map((s) => '<option value="' + esc(s.name) + '">' + esc(s.mtime) +
        " · " + Math.max(1, Math.round(s.size / 1024)) + " KB</option>").join("")
    : '<option value="">（还没有快照，先点「立即备份」）</option>';
  return items;
}

function bindSettings() {
  $("#segTheme").onclick = async (e) => {
    const b = e.target.closest("button[data-t]");
    if (!b) return;
    await call("set_setting", "theme", b.dataset.t);
    D.settings.theme = b.dataset.t;
    applyTheme();
  };
  const tg = async (id, key, label) => {
    const b = $(id);
    b.onclick = async () => {
      const on = !b.classList.contains("on");
      b.classList.toggle("on", on);
      b.textContent = on ? "已开启" : "已关闭";
      await call("set_setting", key, on ? "1" : "0");
      D.settings[key] = on ? "1" : "0";
    };
  };
  tg("#setAuto", "autostart");
  tg("#setTop", "on_top");
  tg("#setNotify", "notify_enabled");
  tg("#setSandbox", "nosandbox");
  /* 多机同时打开不能用 tg：它走的是库里的 settings，而这个开关恰恰要在
     "连不上库"（被别的机器占着）的时候也能读到 —— 所以放应用级配置，改完重启。 */
  const sh = $("#setShare");
  if (sh) sh.onclick = async () => {
    const on = !sh.classList.contains("on");
    const tip = on
      ? "打开「多机同时打开」？\n\n" +
        "两台机器共用同一个数据文件，写操作由 SQLite 自己排队（会互相等一会儿）。\n" +
        "另一边改完数据，这边会弹一条提示让你重新加载 —— 别在提示出现前两边同时大改。\n\n" +
        "改完要重启看板才生效。"
      : "关掉「多机同时打开」？\n\n" +
        "关掉后同一时间只允许一台机器打开这个数据文件，第二台会被挡住。\n\n" +
        "改完要重启看板才生效。";
    if (!confirm(tip)) return;
    const r = await call("set_share_db", on);
    if (!r || !r.ok) { toast((r && r.msg) || "没改成"); return; }
    sh.classList.toggle("on", on);
    sh.textContent = on ? "已开启" : "已关闭";
    if (D) D.share_db = on;
    hideSyncBar();
    if (confirm("已记下，重启后生效。\n\n现在重启看板吗？")) {
      const rr = await call("restart_app");
      if (!rr || !rr.ok) toast((rr && rr.msg) || "重启失败，请手动重开程序");
    }
  };
  $("#setNag").onchange = async () => {
    const v = $("#setNag").value || "30";
    await call("set_setting", "nag_minutes", v);
    D.settings.nag_minutes = v;
  };
  $("#setAlert").onchange = async () => {
    const v = $("#setAlert").value || "180";
    await call("set_setting", "alert_interval", v);
    D.settings.alert_interval = v;
  };
  $("#segLv").onclick = async (e) => {
    const b = e.target.closest("button[data-l]");
    if (!b) return;
    $$("#segLv button").forEach((x) => x.classList.remove("act"));
    b.classList.add("act");
    await call("set_setting", "remind_level", b.dataset.l);
    D.settings.remind_level = b.dataset.l;      // 不同步的话重开面板回显旧值
  };
  $("#setTime").onchange = async () => {
    await call("set_setting", "checkin_time", $("#setTime").value);
    D.settings.checkin_time = $("#setTime").value;
  };
  $("#setExportPick").onclick = async () => {
    const res = await call("pick_dir");
    if (res.ok && res.path) {
      await call("set_setting", "export_dir", res.path);
      D.settings.export_dir = res.path;
      $("#setExport").textContent = res.path;
    }
  };
  /* ---- 数据文件：改的是"以后用哪个库"，所以走应用级配置 + 重启 ---- */
  const afterDbChange = async (r) => {
    if (!r || !r.ok) { toast(r && r.msg ? r.msg : "没换成"); return; }
    const go = confirm(
      "数据文件已改为：\n" + r.path + "\n\n" +
      "需要重启才能生效（当前窗口还开着旧库）。现在重启吗？"
    );
    if (go) {
      const rr = await call("restart_app");
      if (!rr.ok) toast(rr.msg || "重启失败，请手动重开程序");
    } else {
      toast("已记下，下次启动用新数据文件");
      await loadDbInfo();
    }
  };
  $("#setPathPick").onclick = async () => {
    const cur = D.settings.db_path || "";
    const res = await call("pick_db_file", cur ? (cur.replace(/[\\/][^\\/]*$/, "")) : "");
    if (!res.ok) { toast(res.msg || "打不开选择窗口"); return; }
    if (!res.path) return;                       // 取消
    await afterDbChange(await call("set_db_file", res.path));
  };
  $("#setPathDefault").onclick = async () => {
    const info = await call("db_info");
    if (!info || !info.ok) { toast("读不到默认位置"); return; }
    if (info.from === "默认位置") { toast("当前就在默认位置"); return; }
    const ok = confirm("把数据文件恢复成默认位置？\n\n" + info.default +
      "\n\n（不会搬动数据，只是改「下次启动读哪个文件」）");
    if (!ok) return;
    await afterDbChange(await call("set_db_file", info.default));
  };
  $("#setStale").onchange = async () => {
    const v = $("#setStale").value || "3";
    await call("set_setting", "stale_days", v);
    D.settings.stale_days = v;
    await reload();          // 阈值一变，行上的「停滞」标记要跟着重画
  };
  $("#setClose").onclick = () => modal("#ovSet", false);

  $("#snapNow").onclick = async () => {
    const r = await call("snapshot_now");
    if (!r.ok) { toast("备份失败，检查数据目录写入权限"); return; }
    await loadSnaps();
    toast("已备份：" + r.name);
  };
  $("#snapOpen").onclick = async () => {
    const r = await call("open_backup_dir");
    if (!r.ok && r.msg) toast(r.msg);
  };
  $("#snapRestore").onclick = async () => {
    const name = $("#snapList").value;
    if (!name) { toast("还没有可恢复的快照"); return; }
    const ok = confirm(
      "用「" + name + "」覆盖当前数据？\n\n" +
      "当前状态会先自动备份一份，但回滚之后新做改动就不在当前数据里了。"
    );
    if (!ok) return;
    const r = await call("snapshot_restore", name);
    if (!r.ok) { toast(r.msg || "恢复失败"); return; }
    /* 财务 / 热力图缓存还是回滚前的，必须丢掉，否则界面显示的是旧数据 */
    fin = null; finExpanded = {}; heatData = null;
    modal("#ovSet", false);
    await reload();
    toast("已恢复；回滚前的状态也留了一份快照");
  };
}

/* ---------- 打卡（L2 弹窗 / L3 全屏遮挡） ---------- */

function activeProjects() {
  const ps = [];
  eachProject((p) => {
    if (p.status !== "归档" && p.status !== "已结款") ps.push(p);
  });
  return ps;
}

function buildCheckList(box, ps) {
  box.innerHTML = "";
  if (!ps.length) {
    box.innerHTML = '<div class="empty">还没有进行中的项目</div>';
    return;
  }
  ps.forEach((p) => {
    const l = document.createElement("label");
    l.style.cssText = "display:flex;gap:8px;align-items:center;padding:7px 0;border-top:1px solid var(--line);font-size:12px;cursor:pointer";
    l.innerHTML = '<input type="checkbox" ' + (p.priority || p.status === "进行中" ? "checked" : "")
      + ' style="width:auto" data-id="' + p.id + '"><span>' + esc(p.title) + '</span>';
    box.appendChild(l);
  });
}

function openCheck(forced) {
  buildCheckList($("#checkList"), activeProjects());
  const note = $("#checkNote");
  if (forced) {
    note.style.display = "block";
    note.textContent = "今天还没打卡。不确认的话每 " + (D.settings.nag_minutes || "30") + " 分钟会再提醒一次。";
  } else {
    note.style.display = "none";
  }
  modal("#ovCheck", true);
}

function openBlock() {
  buildCheckList($("#blockList"), activeProjects());
  $("#blockNag").textContent = "提醒强度 L3：勾选后点「确认开始」才会解除遮挡。";
  $("#ovBlock").style.display = "flex";
}

function closeBlock() { $("#ovBlock").style.display = "none"; }

/* Python 侧调度器（remind.py）到点后从这里进来 */
window.__openCheckin = function (level) {
  /* 以前这里存取一个模块级 checkLevel，但它在两次调用之间毫无记忆
     （每次都被覆盖）→ 纯属多余全局，直接当局部用。 */
  const lv = level || 2;
  if (lv >= 3) openBlock();
  else openCheck(true);
};

async function submitCheckin(sel, done) {
  const picked = $$(sel).map((i) => ({
    kind: "project", id: parseInt(i.dataset.id, 10),
    title: i.parentElement.querySelector("span").textContent,
  }));
  if (picked.length) {
    await call("checkin", picked);
    for (const p of picked) await call("set_field", "project", p.id, "priority", 1);
  }
  done();
  await reload();
}

function bindCheck() {
  $("#checkCancel").onclick = () => modal("#ovCheck", false);
  $("#checkOk").onclick = () => submitCheckin("#checkList input:checked", () => modal("#ovCheck", false));
  $("#blockOk").onclick = () => submitCheckin("#blockList input:checked", closeBlock);
}

/* ---------- 活跃热力图 ---------- */

let heatYear = 0;
let heatData = null;

const KIND_CN = { checkin: "打卡", status: "状态", task: "任务", note: "备注", open: "打开" };

function isoOf(d) {
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

async function loadHeat() {
  const r = await call("activity", heatYear || 0);
  /* 桥没起来时回的是 {ok:false,err}，直接喂给 renderHeat 会在 d.years.forEach 上抛 */
  if (!r || r.ok === false || !Array.isArray(r.years)) { toast("活跃数据没读出来"); return; }
  heatData = r;
  renderHeat();
}

function renderHeat() {
  const d = heatData;
  const sy = $("#segYear");
  sy.innerHTML = "";
  d.years.forEach((y) => {
    const b = document.createElement("button");
    b.textContent = y;
    b.className = String(y) === String(d.year) ? "act" : "";
    b.onclick = async () => { heatYear = y; await loadHeat(); };
    sy.appendChild(b);
  });

  $("#heatCards").innerHTML = [
    [d.total + " 次", "全年活动"],
    [d.active_days + " 天", "活跃天数"],
    [d.longest + " 天", "最长连续"],
    [d.current + " 天", "当前连续"],
  ].map((c) => '<div class="hcard"><div class="n">' + c[0] + '</div><div class="l">' + c[1] + "</div></div>").join("");

  $("#heatWd").innerHTML = ["一", "二", "三", "四", "五", "六", "日"]
    .map((w, i) => "<div>" + (i % 2 === 0 ? w : "") + "</div>").join("");

  /* 年份非法时 `new Date("NaN-01-01")` 是 Invalid Date，`cur > last` 永远为 false，
     下面这个循环就**永远不会退出**（setDate 对 Invalid Date 无效）→ 整页卡死。
     所以除了正常终止条件，再挂一个硬上界（一年最多 54 周 × 7 天，留够余量）。 */
  const start = new Date(d.year + "-01-01T00:00:00");
  const last = new Date(d.year + "-12-31T00:00:00");
  if (isNaN(start.getTime()) || isNaN(last.getTime())) {
    $("#heatGrid").innerHTML = '<div class="empty">这个年份的数据读不出来</div>';
    $("#heatLogs").innerHTML = "";
    $("#heatTip").textContent = "";
    return;
  }
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));   // 对齐到周一
  const todayD = new Date();
  const cells = [];
  let cur = new Date(start);
  const MAX_CELLS = 400;                       // 硬上界，防脏数据把页面卡死
  while (cells.length < MAX_CELLS) {
    cells.push({ d: isoOf(cur), n: d.days[isoOf(cur)] || 0, fut: cur > todayD });
    cur.setDate(cur.getDate() + 1);
    if (cur > last && (cur.getDay() + 6) % 7 === 0) break;
  }
  $("#heatGrid").innerHTML = cells.map((c) => {
    const lv = c.n === 0 ? "" : c.n <= 2 ? "l1" : c.n <= 4 ? "l2" : c.n <= 7 ? "l3" : "l4";
    return '<i class="' + lv + (c.fut ? " fut" : "") + '" title="'
      + c.d + " · " + (c.n ? c.n + " 次" : "无活动") + '"></i>';
  }).join("");

  renderLogs();

  /* 口径文案必须跟着 models.NODE_ACTIVE_STATUS 一起改 —— 提示写错了
     比没提示更糟：用户会照着一个错的规则判断热力图为什么不动。
     措辞按用户口径（2026-09-30）：打卡 **或** 环节变成那 5 个状态之一才算。 */
  $("#heatTip").textContent =
    "打卡 或环节变更为 制作中/已提交/反馈/可优化/交付 才会计入活跃。";
}

/* ---------- 变更记录：按天分组 + 可折叠（2026-09-30） ----------
   流水是只增不减的表，几年后能到几万行。所以面板上只列**最近一周**、
   最多 LOG_PANEL_CAP 条；更早的走「查看更多」弹窗翻页看。
   分组按 `day` 走 —— 行本身是 id 倒序，天然就是「天降序 + 天内时间降序」。 */

/* 「9-30 周三」，今天额外标一下（跨天开着应用时一眼能看出哪组是今天） */
function dayHead(day) {
  const d = parseDay(day);
  if (!d) return day || "—";
  const now = new Date();
  const same = isoOf(d) === isoOf(now);
  return (d.getMonth() + 1) + "-" + d.getDate() + " 周" + WD_CN[d.getDay()]
    + (same ? " · 今天" : "");
}

function groupByDay(rows) {
  const out = [], idx = {};
  (rows || []).forEach((r) => {
    const k = r.day || "";
    if (!idx[k]) { idx[k] = { day: k, rows: [] }; out.push(idx[k]); }
    idx[k].rows.push(r);
  });
  return out;
}

function logRowsHTML(rows) {
  return (rows || []).map((r) =>
    '<div class="log"><span class="kt">' + esc(KIND_CN[r.kind] || r.kind || "") + "</span>" +
    '<span class="dt">' + esc(r.detail || "—") + "</span>" +
    '<span class="pt">' + esc(r.project || "") + "</span>" +
    '<span class="ts">' + esc((r.ts || "").slice(5)) + "</span></div>").join("");
}

function dayGroupsHTML(rows, isOpen) {
  return groupByDay(rows).map((g, i) => {
    const open = isOpen(g.day, i);
    return '<div class="dgrp' + (open ? " open" : "") + '" data-day="' + esc(g.day) + '">' +
      '<div class="dgh"><span class="tw">' + (open ? "▼" : "▶") + "</span>" +
      '<span class="dgday">' + esc(dayHead(g.day)) + "</span>" +
      '<span class="dgn">' + g.rows.length + " 条</span></div>" +
      (open ? '<div class="dgb">' + logRowsHTML(g.rows) + "</div>" : "") +
      "</div>";
  }).join("");
}

/* 点日期头折叠 / 展开。面板和弹窗各用各的折叠表（在同一天上不用互相迁就）。 */
function bindDayGroups(box, fold, rerender) {
  box.querySelectorAll(".dgh").forEach((h) => {
    h.onclick = () => {
      const w = h.parentNode;
      fold[w.dataset.day] = w.classList.contains("open") ? 0 : 1;
      saveUI();
      rerender();
    };
  });
}

function renderLogs() {
  const d = heatData || {};
  const box = $("#heatLogs");
  const hint = $("#heatLogHint");
  const more = $("#heatLogMore");
  const rows = d.recent || [];
  const win = d.window_days || 7;
  if (hint) {
    hint.textContent = "（最近 " + win + " 天"
      + (d.recent_more ? "，面板只列前 " + rows.length + " 条" : "") + "）";
  }
  if (more) more.style.display = (d.log_total || 0) > 0 ? "" : "none";

  if (!rows.length) {
    box.innerHTML = '<div class="empty">最近 ' + win + " 天还没有记录</div>";
    return;
  }
  const fold = ui.logFold || (ui.logFold = {});
  /* 默认只展开最近那天：一进活跃页先看到"今天干了什么"，往前的要看再点开 */
  box.innerHTML = dayGroupsHTML(rows, (day, i) =>
    fold[day] === undefined ? i === 0 : !!fold[day]);
  bindDayGroups(box, fold, renderLogs);
}

/* ---------- 全部变更记录（弹窗，按天分组翻页） ---------- */

const LOG_PAGE = 200;
let allLogs = { rows: [], offset: 0, total: 0, has_more: false, busy: false };

function renderAllLogs() {
  const box = $("#allLogs");
  if (!allLogs.rows.length) {
    box.innerHTML = '<div class="empty">还没有记录</div>';
  } else {
    const fold = ui.logFoldAll || (ui.logFoldAll = {});
    /* 弹窗是"专门来翻的"，默认全展开（要收自己点） */
    box.innerHTML = dayGroupsHTML(allLogs.rows, (day) =>
      fold[day] === undefined ? true : !!fold[day]);
    bindDayGroups(box, fold, renderAllLogs);
  }
  $("#logTotal").textContent = "（共 " + allLogs.total + " 条，已列 "
    + allLogs.rows.length + " 条）";
  const mb = $("#logMore");
  mb.style.display = allLogs.has_more ? "" : "none";
  mb.textContent = allLogs.busy ? "加载中…" : "加载更多";
  mb.disabled = allLogs.busy;
}

async function loadMoreLogs() {
  if (allLogs.busy) return;
  allLogs.busy = true;
  renderAllLogs();
  const r = await call("activity_logs", allLogs.offset, LOG_PAGE);
  allLogs.busy = false;
  if (!r || !r.rows) { toast("读不到变更记录"); renderAllLogs(); return; }
  allLogs.rows = allLogs.rows.concat(r.rows);
  allLogs.offset += r.rows.length;
  allLogs.total = r.total || allLogs.rows.length;
  allLogs.has_more = !!r.has_more;
  renderAllLogs();
}

function openLogsDialog() {
  allLogs = { rows: [], offset: 0, total: 0, has_more: false, busy: false };
  $("#allLogs").innerHTML = '<div class="empty">加载中…</div>';
  $("#ovLogs").style.display = "flex";
  loadMoreLogs();
}

/* ---------- 财务（M3） ---------- */

let fin = null;               // 最近一次 finance_data 结果
/* 默认看**本年份**：钱是按年结的，一进来先给当年；下拉里仍然保留「全部年份」 */
let finYear = new Date().getFullYear();
const finCcy = "CNY";         // 只按人民币结算，界面上没有切换入口，是常量不是状态
let finClient = 0;
let finExpanded = {};         // project_id -> 是否展开流水
let finEditId = 0;            // 正在编辑的款项 id，0 = 新增
let finEditCcy = "CNY";       // 正在编辑那笔的币种：新记录一律 CNY，
                              // 老库里的外币记录**保持原样**，不要在保存时被悄悄改成 CNY

const SYM = { CNY: "¥", USD: "$", EUR: "€", GBP: "£", JPY: "¥" };

function money(v, ccy) {
  const s = SYM[ccy || finCcy] || "";
  const n = Number(v || 0);
  return s + n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* 项目下拉：带分类前缀，方便区分商业 / 个人 */
function allProjects() {
  const out = [];
  eachProject((p, g) => out.push(Object.assign({}, p, { group: g.name })));
  return out;
}

/* 环节下拉：把树拍平成带缩进的路径 */
function nodeOptions(projectId) {
  const prj = allProjects().find((p) => p.id === projectId);
  const out = [];
  if (!prj) return out;
  const walk = (ns, dep) => (ns || []).forEach((n) => {
    out.push({ id: n.id, label: "　".repeat(dep) + (dep ? "└ " : "") + n.title });
    walk(n.children, dep + 1);
  });
  walk(prj.children, 0);
  return out;
}

async function loadFinance() {
  const r = await call("finance_data", finYear, finClient, finCcy);
  /* 同上：失败时别把 {ok:false,err} 当数据用，否则 renderFinance 读 f.years 就抛 */
  if (!r || r.ok === false || !Array.isArray(r.years)) { toast("财务数据没读出来"); return; }
  fin = r;
  renderFinance();
}

function renderFinance() {
  const f = fin;
  /* 筛选：年份下拉 + 客户下拉。
     - 币种筛选整组撤掉：只按人民币结算，留一个「CNY」按钮等于占位
     - 年份从一排按钮改下拉：年份多了以后按钮会挤成一片，下拉只占一格 */
  const ys = $("#finYear");
  /* 默认年份是本年份 —— 库里今年还没记录时，本年也不能从下拉里消失，
     否则用户切过去一次就再也回不来了 */
  const years = Array.from(new Set([...f.years, new Date().getFullYear()]))
    .sort((a, b) => b - a);
  ys.innerHTML = '<option value="0">全部年份</option>' +
    years.map((y) => '<option value="' + y + '">' + y + " 年</option>").join("");
  ys.value = String(finYear);
  ys.onchange = async () => { finYear = parseInt(ys.value, 10) || 0; await loadFinance(); };

  const sel = $("#finClient");
  sel.innerHTML = '<option value="0">全部客户</option>' +
    f.clients.map((c) => '<option value="' + c.id + '">' + esc(c.name) + "</option>").join("");
  sel.value = String(finClient);
  sel.onchange = async () => { finClient = parseInt(sel.value, 10) || 0; await loadFinance(); };

  /* 汇总卡片分两行，一张卡一个关注点（用户定的顺序，别随手重排）：
       第一行  总合同额 / 已收 / 待收   —— 这笔生意有多大、进来了多少、还欠多少
       第二行  外包支出 / 实际收入 / 收款率 —— 成本、真正落袋的、回收进度
     开票口径（已开票 / 未开票）与「本月到账」都不上卡面，开票情况在导出的表里。 */
  const c = f.cards;
  const cardHtml = (x) =>
    '<div class="hcard"><div class="n" style="' +
    (x[2] === "pend" ? "color:var(--amber)"
      : x[2] === "recv" ? "color:var(--green)"
      : x[2] === "out" ? "color:var(--amber)" : "") +
    '">' + x[0] + '</div><div class="l">' + x[1] + "</div></div>";
  const cardRows = [
    [
      [money(c.contract), "总合同额", ""],
      [money(c.received), "已收", "recv"],
      [money(c.pending), "待收", "pend"],
    ],
    [
      [money(c.outsource), "外包支出", "out"],
      [money(c.net), "实际收入", "recv"],
      [c.rate + "%", "收款率", ""],
    ],
  ];
  $("#finCards").innerHTML = cardRows.map((r) =>
    '<div class="hcards">' + r.map(cardHtml).join("") + "</div>").join("");
  $("#finQ").textContent = f.quarter.name + " 已到账 " + money(f.quarter.received) +
    " · 外包 " + money(f.quarter.outsource);

  /* 项目明细 */
  const box = $("#finTable");
  if (!f.projects.length) {
    box.innerHTML = '<div class="empty">还没有款项记录，点右上角「＋ 记一笔」开始</div>';
  } else {
    const head = '<div class="ftrow head"><div>项目</div><div>客户</div>' +
      '<div class="num">合同额</div><div class="num">已收</div><div class="num">外包</div>' +
      '<div class="num">实际收入</div><div class="num">待收</div>' +
      '<div class="num">最近到账</div><div></div></div>';
    const body = f.projects.map((p) => {
      const open = !!finExpanded[p.project_id];
      const pend = p.pending > 0.009;
      const rows = '<div class="ftrow body" data-pid="' + p.project_id + '">' +
        '<div class="nm">' + esc(p.project) +
          (p.node_records ? '<span class="tag">含 ' + p.node_records + " 笔环节款</span>" : "") +
          (p.settled ? '<span class="tag" style="color:var(--green);border-color:var(--green)">已结清</span>' : "") +
        "</div>" +
        '<div class="nm">' + esc(p.client || "—") + "</div>" +
        '<div class="num">' + money(p.contract) + "</div>" +
        '<div class="num ' + (p.received ? "recv" : "zero") + '">' + money(p.received) + "</div>" +
        '<div class="num ' + (p.outsource ? "out" : "zero") + '">' + money(p.outsource) + "</div>" +
        '<div class="num ' + (p.net > 0.009 ? "recv" : "zero") + '">' + money(p.net) + "</div>" +
        '<div class="num ' + (pend ? "pend" : "zero") + '">' + money(p.pending) + "</div>" +
        '<div class="num">' + esc(p.last_payment || "—") + "</div>" +
        '<div class="tw">' + (open ? "▼" : "▶") + "</div>" +
      "</div>";
      if (!open) return rows;
      const sub = p.records.map((r) =>
        '<div class="subrow">' +
          '<div>' + esc(r.date || "—") + "</div>" +
          '<div>' + esc(r.kind_cn) + " · " + esc(r.status) + "</div>" +
          '<div class="nm">' + esc(r.target) + "</div>" +
          '<div class="num">' + money(r.amount, r.currency) + "</div>" +
          '<div class="num">' + esc(r.currency) + "</div>" +
          '<div class="nm">' + esc(r.note || r.invoice_no || "") + "</div>" +
          '<div class="op" data-edit="' + r.id + '" title="编辑">⋯</div>' +
        "</div>").join("");
      return rows + '<div class="sub">' + sub + "</div>";
    }).join("");
    box.innerHTML = head + body;
    box.querySelectorAll(".ftrow.body").forEach((el) => {
      el.onclick = (e) => {
        if (e.target.dataset.edit) return;
        const pid = parseInt(el.dataset.pid, 10);
        finExpanded[pid] = !finExpanded[pid];
        renderFinance();
      };
    });
    box.querySelectorAll("[data-edit]").forEach((el) => {
      el.onclick = (e) => {
        e.stopPropagation();
        const rid = parseInt(el.dataset.edit, 10);
        const rec = f.records.find((r) => r.id === rid);
        if (rec) openFinDialog(rec);
      };
    });
  }

  /* 账龄 */
  const ag = f.aging;
  $("#finAging").innerHTML = ag.buckets.map((b, i) => {
    const cls = i >= 3 && b.amount > 0 ? " bad" : (i >= 2 && b.amount > 0 ? " warn" : "");
    const detail = b.items.length
      ? '<div class="agingdt">' + b.items.slice(0, 6).map((x) =>
          '<span class="x">' + esc(x.client || "未指定") + " · " + esc(x.project) +
          " · " + money(x.pending) + " · " + x.age + " 天</span>").join("") +
        (b.items.length > 6 ? "… 共 " + b.items.length + " 个项目" : "") + "</div>"
      : "";
    return '<div class="abk' + cls + '"><div class="n">' + money(b.amount) +
      '</div><div class="l">' + b.label + "</div>" + detail + "</div>";
  }).join("");

  /* 客户汇总 */
  $("#finClientSum").innerHTML = f.client_summary.length
    ? '<div class="ftrow head"><div>客户</div><div>项目数</div>' +
      '<div class="num">合同额</div><div class="num">已收</div><div class="num">外包</div>' +
      '<div class="num">实际收入</div><div class="num">待收</div>' +
      '<div class="num">收款率</div><div></div></div>' +
      f.client_summary.map((c2) =>
        '<div class="ftrow body" style="cursor:default">' +
        '<div class="nm">' + esc(c2.client) + "</div>" +
        '<div class="num">' + c2.projects + "</div>" +
        '<div class="num">' + money(c2.contract) + "</div>" +
        '<div class="num recv">' + money(c2.received) + "</div>" +
        '<div class="num ' + (c2.outsource ? "out" : "zero") + '">' + money(c2.outsource) + "</div>" +
        '<div class="num ' + (c2.net > 0.009 ? "recv" : "zero") + '">' + money(c2.net) + "</div>" +
        '<div class="num ' + (c2.pending > 0.009 ? "pend" : "zero") + '">' + money(c2.pending) + "</div>" +
        '<div class="num">' + c2.rate + "%</div><div></div></div>").join("")
    : '<div class="empty">还没有客户数据</div>';

  $("#finTip").textContent = "待收 = 合同额 − 已收；实际收入 = 已收 − 外包支出。" +
    " 开票金额只在导出的表格里，看板不展示。" +
    (f.other_currency ? " 另有 " + f.other_currency + " 笔非 " + finCcy + " 记录未计入当前视图。" : "") +
    " 导出目录：" + (f.setting_export_dir || "（首次导出时自动创建）");
}

/* ---- 记一笔 / 编辑 ---- */

function fillFinStatus(kind, keep) {
  const sel = $("#finStatus");
  const list = (fin.meta.status[kind] || []).slice();
  sel.innerHTML = list.map((s) => '<option value="' + esc(s) + '">' + esc(s) + "</option>").join("");
  sel.value = list.includes(keep) ? keep : fin.meta.default_status[kind];
}

function fillFinNode(pid, keep) {
  const sel = $("#finNode");
  sel.innerHTML = '<option value="">项目整体</option>' +
    nodeOptions(pid).map((n) => '<option value="' + n.id + '">' + esc(n.label) + "</option>").join("");
  sel.value = keep ? String(keep) : "";
}

/* presetProject：从项目行 ⋯ 进来时预选该项目，省得再挑一遍 */
function openFinDialog(rec, presetProject) {
  if (!fin) return;
  finEditId = rec ? rec.id : 0;
  const prjs = allProjects();
  $("#finPrj").innerHTML = prjs.map((p) =>
    '<option value="' + p.id + '">' + esc(p.group + " · " + p.title) + "</option>").join("");
  if (!prjs.length) { alert("还没有项目，先在建一个项目再记账"); return; }

  const want = rec ? rec.project_id : (presetProject || prjs[0].id);
  if (!prjs.some((p) => p.id === want)) {
    alert("这个项目不在当前列表里（可能已归档），请到「财务」页里选项目");
    return;
  }

  const kind = rec ? rec.kind : "payment";
  $$("#finKind button").forEach((b) => b.classList.toggle("act", b.dataset.k === kind));
  $("#finTitle").textContent = rec ? "编辑款项" : "记一笔";
  $("#finPrj").value = String(want);
  fillFinNode(want, rec ? rec.node_id : null);
  fillFinStatus(kind, rec ? rec.status : "");
  $("#finAmount").value = rec ? rec.amount : "";
  finEditCcy = (rec && rec.currency) || "CNY";   // 界面不收币种了，这里记住原值（见 finEditCcy 的声明注释）
  $("#finTax").value = rec && rec.tax_rate != null ? rec.tax_rate : "";
  $("#finDate").value = rec ? rec.date : isoOf(new Date());
  $("#finInvNo").value = rec ? rec.invoice_no : "";
  $("#finNote").value = rec ? rec.note : "";
  $("#finDel").style.display = rec ? "block" : "none";
  $("#finInvRow").style.display = kind === "invoice" ? "flex" : "none";
  modal("#ovFin", true);
}

/* 导出：菜单里点一下就导出，过程中把按钮改字当进度提示（原来挂在被点的那个按钮上，
   现在按钮只剩一个「导出 ▾」，所以进度落在它身上） */
async function doExport(fmt) {
  const btn = $("#finExport");
  const old = "导出 ▾";
  btn.textContent = "导出中…";
  const r = await call("finance_export", fmt, finYear);
  btn.textContent = old;
  if (!r.ok) { alert("导出失败：" + (r.msg || "")); return; }
  if (confirm("已导出到：\n" + r.path + "\n\n打开目录？")) await call("open_dir", r.dir);
}

function bindFinance() {
  $("#finAdd").onclick = () => openFinDialog(null);
  $("#finCancel").onclick = () => modal("#ovFin", false);
  /* 每次开面板都先回到「新建」态：editCli 归零、表单清空、删除按钮收起。
     不然上次编辑过某个客户后关掉再打开，删除按钮还露着、editCli 还指着旧 id，
     一按就把那个看不见的客户删了。 */
  $("#finClientsBtn").onclick = () => {
    window.__cliEdit(0);
    renderClients();
    modal("#ovCli", true);
  };

  /* 导出相关的命令收进一个下拉菜单（导出 Excel / CSV / JSON / 打开导出目录）。
     原来「导出目录」单独一个按钮 + 三个导出按钮横排，四个东西占了半条筛选栏，
     而它们其实是一件事的四种做法。 */
  $("#finExport").onclick = () => {
    popup([
      { label: "导出 Excel（四张表）", fn: () => doExport("xlsx") },
      { label: "导出 CSV", fn: () => doExport("csv") },
      { label: "导出 JSON", fn: () => doExport("json") },
      { sep: true },
      { label: "打开导出目录", fn: async () => {
          const r = await call("export_dir");
          if (r.ok) await call("open_dir", r.path);
          else alert("打不开导出目录：" + (r.msg || ""));
      } },
    ], $("#finExport"));
  };

  $("#finKind").onclick = (e) => {
    const b = e.target.closest("button[data-k]");
    if (!b) return;
    const k = b.dataset.k;
    $$("#finKind button").forEach((x) => x.classList.toggle("act", x.dataset.k === k));
    fillFinStatus(k, "");
    $("#finInvRow").style.display = k === "invoice" ? "flex" : "none";
  };
  $("#finPrj").onchange = () => fillFinNode(parseInt($("#finPrj").value, 10), null);

  $("#finOk").onclick = async () => {
    const kind = ($("#finKind button.act") || {}).dataset?.k || "payment";
    const payload = {
      kind,
      project_id: parseInt($("#finPrj").value, 10),
      node_id: $("#finNode").value || null,
      amount: $("#finAmount").value,
      currency: finEditCcy,
      tax_rate: $("#finTax").value.trim(),
      date: $("#finDate").value,
      status: $("#finStatus").value,
      invoice_no: $("#finInvNo").value.trim(),
      note: $("#finNote").value.trim(),
    };
    const r = finEditId
      ? await call("finance_update", finEditId, payload)
      : await call("finance_add", payload);
    if (!r.ok) { alert(r.msg || "保存失败"); return; }
    modal("#ovFin", false);
    await loadFinance();
  };

  $("#finDel").onclick = async () => {
    if (!finEditId || !confirm("删除这笔款项记录？")) return;
    await call("finance_delete", finEditId);
    modal("#ovFin", false);
    await loadFinance();
  };

  /* ---- 客户 ---- */
  let editCli = 0;
  window.__cliEdit = (id) => {
    const c = (fin.clients || []).find((x) => x.id === id);
    editCli = c ? c.id : 0;
    $("#cliName").value = c ? c.name : "";
    $("#cliContact").value = c ? c.contact : "";
    $("#cliPhone").value = c ? c.phone : "";
    $("#cliEmail").value = c ? c.email : "";
    $("#cliCycle").value = c ? c.settlement_cycle : "";
    $("#cliCcy").value = c ? c.currency : "CNY";
    $("#cliSave").textContent = c ? "更新客户" : "保存客户";
    /* 删除只在编辑既有客户时露出来（新建时没东西可删） */
    const del = $("#cliDel");
    if (del) del.style.display = c ? "" : "none";
  };
  $("#cliReset").onclick = () => window.__cliEdit(0);
  $("#cliClose").onclick = () => modal("#ovCli", false);
  $("#cliDel").onclick = async () => {
    if (!editCli) return;
    const c = (fin.clients || []).find((x) => x.id === editCli);
    const name = c ? c.name : "这个客户";
    /* 后端会挡住"还有项目挂在上面"的情况并说明原因 —— 把原因原样带出来，
       别吞成一个泛泛的"删除失败"。 */
    if (!confirm("删除客户「" + name + "」？\n\n已经记过的款项和项目都不会被删。\n"
                 + "如果还有项目挂在这个客户上，会先让你把项目的客户改掉。")) return;
    const r = await call("client_delete", editCli);
    if (!r.ok) { alert(r.msg || "删除失败"); return; }
    window.__cliEdit(0);
    await loadFinance();
    renderClients();
  };
  $("#cliSave").onclick = async () => {
    const payload = {
      name: $("#cliName").value.trim(),
      contact: $("#cliContact").value.trim(),
      phone: $("#cliPhone").value.trim(),
      email: $("#cliEmail").value.trim(),
      settlement_cycle: $("#cliCycle").value.trim(),
      currency: $("#cliCcy").value.trim() || "CNY",
    };
    if (!payload.name) { $("#cliName").focus(); return; }
    const r = await call("client_save", editCli, payload);
    if (!r.ok) { alert(r.msg || "保存失败"); return; }
    window.__cliEdit(0);
    await loadFinance();
    renderClients();
  };
}

function renderClients() {
  if (!fin) return;
  $("#cliList").innerHTML = fin.clients.length
    ? fin.clients.map((c) =>
        '<div class="ftrow body" style="grid-template-columns:1.4fr 1fr 1fr 22px">' +
        '<div class="nm">' + esc(c.name) + "</div>" +
        '<div class="nm">' + esc(c.contact || "—") + "</div>" +
        '<div class="nm">' + esc(c.settlement_cycle || "—") + "</div>" +
        '<div class="op" data-cli="' + c.id + '" title="编辑">⋯</div></div>').join("")
    : '<div class="empty">还没有客户</div>';
  $("#cliList").querySelectorAll("[data-cli]").forEach((el) => {
    el.onclick = () => window.__cliEdit(parseInt(el.dataset.cli, 10));
  });
}

/* ---------- 告警 ---------- */

async function showAlerts() {
  const a = await call("alerts");
  if (!a.total) { popup([{ label: "没有停滞或临期的项目", dim: true }], $("#foot")); return; }
  const items = [];
  a.stale.forEach((x) => items.push({ label: "停滞 · " + x.title + "（" + x.detail + "）", dim: true }));
  a.due.forEach((x) => items.push({ label: "临期 · " + x.title + "（" + x.detail + "）", dim: true }));
  if (a.stale.length && a.due.length) items.splice(a.stale.length, 0, { sep: true });
  popup(items, $("#foot"));
}

/* ---------- 自检巡视（--smoke 专用） ----------
   自检代码整个搬去了 diag.js（1750 行，正常使用一行都不跑），这里只留按需加载。
   加载/执行失败也必须回一份报告：Python 侧会一直等到 120 秒超时，
   干等看不出是「自检自己坏了」还是「被测功能坏了」。 */
function loadDiag() {
  const s = document.createElement("script");
  s.src = "diag.js";
  s.onload = function () {
    try { diagReport().catch(failDiag); } catch (e) { failDiag(e); }
  };
  s.onerror = function () { failDiag(new Error("diag.js 加载失败")); };
  document.head.appendChild(s);
}

function failDiag(e) {
  const err = String((e && e.message) || e);
  window.__err = window.__err || ("diag: " + err);
  try { call("boot_ok", JSON.stringify({ failed: "diag", err: err })); } catch (_e) {}
}
/* ---------- 启动 ---------- */

/* 多机共享那条提示条的按钮。只绑定一次，别塞进 bindSettings（重开设置面板会重绑）。 */
function bindSyncBar() {
  const rb = $("#syncReload");
  if (rb) rb.onclick = () => { refreshAll(); };
  const hb = $("#syncHide");
  if (hb) hb.onclick = () => hideSyncBar();
}

whenReady(async () => {
  try {
  // 先报到：告诉 Python 侧 JS 桥真的通了（看门狗用它判断要不要降级重启）
  call("boot_ok");
  D = await call("load");
  /* 桥没起来 / 后端抛异常时 call() 回的是 `{ok:false,err}`，**不是**数据。
     以前不判就往下走：applyTheme 读 `D.settings.theme` 当场抛，
     再被外层那个 catch 一吞 —— 用户看到的是一片空白，连"出错了"都没有。
     这里显式挡住，把原因画到屏幕上，并回一份报告让 --smoke 立刻失败（别干等 120 秒）。 */
  if (!D || D.ok === false || !Array.isArray(D.groups)) {
    const why = (D && D.err) || "后端没有回应";
    window.__err = "boot: load 失败 — " + why;
    $("#tree").innerHTML = '<div class="empty">读不到数据：' + esc(why) +
      '<br>数据文件若在网络盘上，确认那台机器开着，再点顶栏的刷新按钮。</div>';
    try { call("boot_ok", JSON.stringify({ failed: "load", err: why })); } catch (_e) { /* 桥也坏了就只能靠 __err */ }
    return;
  }
  applyAppInfo();
  await syncTop();
  applyTheme();
  bindHeader();
  bindNew();
  bindNodes();
  bindSettings();
  bindCheck();
  bindFinance();
  bindHotkeys();
  bindSyncBar();
  render();
  renderFoot();

  /* 多机共享：20 秒问一次"另一边改过没有"。
     间隔比心跳（20s）长一点没关系 —— 这条提示要的是"别拿旧数据改完写回去"，
     不是实时同步。默认关着，syncTick 自己会早退。 */
  setInterval(() => { syncTick().catch(() => {}); }, 20000);
  syncTick().catch(() => {});

  // --smoke：main.py 会等待 diagReport 从 JS 通道回传状态后自行退出
  window.__booted = true;
  if (D.diag) loadDiag();
  } catch (err) {
    window.__err = "boot: " + ((err && err.message) || String(err));
  }
});
