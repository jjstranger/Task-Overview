/* 只把 app.js 的**顶层**跑一遍，看有没有立即执行的异常。
   DOM / localStorage 全用桩顶着 —— 目的不是验证功能，而是抓
   "脚本还没跑到 whenReady 就抛了" 这类会让整页白屏的错误。 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const code = fs.readFileSync(path.join(__dirname, "..", "..", "app", "web", "app.js"), "utf8");

const el = () => ({
  style: {}, dataset: {}, className: "", textContent: "", innerHTML: "", value: "",
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  appendChild() {}, removeChild() {}, remove() {}, setAttribute() {}, focus() {},
  addEventListener() {}, removeEventListener() {}, querySelector: () => el(),
  querySelectorAll: () => [], getBoundingClientRect: () => ({ top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0 }),
  closest: () => null, children: [], offsetWidth: 0, offsetHeight: 0,
});

const store = {};
const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Promise, JSON, Math, Date, Number, String, Boolean, Array, Object, RegExp, Error, Set, Map,
  isNaN, parseInt, parseFloat,
  innerWidth: 1200, innerHeight: 800, alert() {}, confirm: () => false, prompt: () => "",
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  },
  document: {
    addEventListener() {}, removeEventListener() {},
    querySelector: () => el(), querySelectorAll: () => [],
    createElement: () => el(), title: "",
    body: el(), documentElement: el(),
  },
  navigator: { userAgent: "node" },
};
sandbox.window = sandbox;
sandbox.window.addEventListener = () => {};
sandbox.window.matchMedia = () => ({ matches: false });
sandbox.globalThis = sandbox;

try {
  vm.runInNewContext(code, sandbox, { filename: "app.js" });
  console.log("TOPLEVEL OK");
} catch (e) {
  console.log("TOPLEVEL THREW:", e && e.stack ? e.stack.split("\n").slice(0, 6).join("\n") : e);
  process.exit(1);
}
