/**
 * Headless behaviour harness for web/js/picker.js (p0-spec section 4.12).
 *
 * The static contract tests can see that the picker is wired up, but not that
 * clicking a directory actually navigates, that "use this directory" resolves
 * the displayed path, or that Escape cancels. Those are exactly the
 * interactions the user complained about ("it shows the open directory but
 * clicking it does nothing"). A later round added the same complaint for the
 * export directory picker ("cannot type a path, cannot create a directory"),
 * so the manual path field and the new-directory row are exercised here too.
 *
 * Usage: node picker_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node picker_harness.mjs <web dir>");

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(name) { this.items.add(name); }
  remove(name) { this.items.delete(name); }
  contains(name) { return this.items.has(name); }
  toggle(name, on) { if (on) this.items.add(name); else this.items.delete(name); }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.textContent = "";
    this.className = "";
    this.title = "";
    this.type = "";
    this.value = "";
    this.placeholder = "";
    this.hidden = false;
    this.disabled = false;
    this.parentNode = null;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  // There is no layout engine here: the harness supplies the measurements
  // through `measure`, which is what lets navscroll.js run for real.
  getBoundingClientRect() { return measure(this); }
  focus() { globalThis.document.activeElement = this; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type, event) {
    for (const handler of this.listeners[type] || []) {
      handler(event || { preventDefault() {} });
    }
  }
}

/** Default: nothing is laid out, so navscroll.js reports "cannot measure". */
let measure = () => ({ top: 0, height: 0 });

const documentListeners = {};
const body = new FakeNode("body");
globalThis.document = {
  body,
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  addEventListener: (type, handler) => { (documentListeners[type] ||= []).push(handler); },
  removeEventListener: (type, handler) => {
    documentListeners[type] = (documentListeners[type] || []).filter((item) => item !== handler);
  },
};
globalThis.window = { setTimeout, clearTimeout };

function find(node, predicate, out = []) {
  if (predicate(node)) out.push(node);
  for (const child of node.children || []) {
    if (child && typeof child === "object") find(child, predicate, out);
  }
  return out;
}
// picker.js sets className as a string for initial elements and uses
// classList only for the transient is-busy flag, so check both.
const hasClass = (node, name) =>
  (node.classList && node.classList.contains(name)) ||
  String(node.className || "").split(/\s+/).includes(name);
const byClass = (root, name) => find(root, (node) => hasClass(node, name));
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const pickerMod = await import(pathToFileURL(WEB + "/js/picker.js").href);
const apiMod = await import(pathToFileURL(WEB + "/js/api.js").href);

const calls = [];
const TREE = {
  "/": { path: "/", parent: null, dirs: [{ name: "outside", path: "/outside" }], roots: ["/"], home: "/home/me" },
  "/outside": { path: "/outside", parent: "/", dirs: [{ name: "inner", path: "/outside/inner" }], roots: ["/"], home: "/home/me" },
  "/outside/inner": { path: "/outside/inner", parent: "/outside", dirs: [], roots: ["/"], home: "/home/me" },
};
apiMod.api.pick = async (path) => {
  calls.push(path);
  const found = TREE[path];
  if (!found) throw new Error("unexpected path " + path);
  return found;
};

// Section 4.12: creating a directory is a real write, so the harness records
// the exact (parent, name) pair and makes the created path listable.
const mkdirCalls = [];
apiMod.api.pickMkdir = async (parent, name) => {
  mkdirCalls.push([parent, name]);
  const path = parent.replace(/\/+$/, "") + "/" + name;
  TREE[path] = { path, parent, dirs: [], roots: ["/"], home: "/home/me" };
  // A real mkdir makes the new directory show up in its parent's listing, which
  // is what the "up" step below navigates back into.
  const host = TREE[parent];
  if (host && Array.isArray(host.dirs)) host.dirs.push({ name, path });
  return { path, created: true };
};

// The dialog owns the keyboard while it is open (open() moves focus into it), so
// every document-level key event comes from a node inside the dialog.
function dialogKey(documentListeners, event) {
  for (const handler of documentListeners.keydown || []) handler(event);
}
function escape(documentListeners, dialogNode) {
  // Loud rather than silent: a key event without a target inside the dialog is
  // exactly what the picker now ignores, so a missing argument would hang here.
  if (!dialogNode) throw new Error("escape() needs the dialog node");
  dialogKey(documentListeners, { key: "Escape", target: dialogNode, preventDefault() {} });
}

const errors = [];
const picker = pickerMod.createPicker({ notify: (message) => errors.push(String(message)) });
const backdrop = byClass(body, "picker-backdrop")[0];
if (!backdrop) throw new Error("createPicker did not build a backdrop");
if (!backdrop.hidden) throw new Error("backdrop must start hidden");
const dialogNode = byClass(backdrop, "picker-dialog")[0];
if (!dialogNode) throw new Error("the picker has no dialog element");

// 1) opening lists the directories of the start path
const opened = picker.open("/outside");
await tick(); await tick();
if (backdrop.hidden) throw new Error("open() did not show the dialog");
let dirs = byClass(backdrop, "picker-dir");
if (dirs.length !== 1) throw new Error("expected 1 directory row, got " + dirs.length);
if (dirs[0].children[0].textContent !== "inner") throw new Error("directory name not rendered");

// 2) clicking a directory navigates into it
dirs[0].fire("click");
await tick(); await tick();
if (calls[calls.length - 1] !== "/outside/inner") throw new Error("click did not navigate: " + calls.join(" > "));
dirs = byClass(backdrop, "picker-dir");
if (dirs.length !== 0) throw new Error("an empty directory still rendered rows");

// 3) "use this directory" resolves the displayed path and closes
byClass(backdrop, "picker-use")[0].fire("click");
const chosen = await opened;
if (chosen !== "/outside/inner") throw new Error("resolved " + chosen + ", expected /outside/inner");
if (!backdrop.hidden) throw new Error("dialog stayed open after choosing");

// 4) Escape cancels (resolves null)
const opened2 = picker.open("/outside");
await tick(); await tick();
escape(documentListeners, dialogNode);
const cancelled = await opened2;
if (cancelled !== null) throw new Error("Escape resolved " + cancelled + ", expected null");
if (!backdrop.hidden) throw new Error("dialog stayed open after Escape");

// 4b) opening moves focus into the dialog, and a key aimed at the page behind it
//     must not settle anything: otherwise an Enter meant for a button back there
//     resolves the picker and silently "uses" the directory on screen.
const openedStray = picker.open("/outside");
await tick(); await tick();
if (document.activeElement !== byClass(backdrop, "picker-path")[0]) {
  throw new Error("open() must move focus into the dialog, got " + String(document.activeElement && document.activeElement.className));
}
let straySettled = false;
openedStray.then(() => { straySettled = true; });
dialogKey(documentListeners, { key: "Enter", target: body, preventDefault() {} });
await tick();
if (straySettled) throw new Error("an Enter aimed at the page behind the dialog settled the picker");
escape(documentListeners, dialogNode);
await openedStray;

// 5) the up button is disabled at a filesystem root
const opened3 = picker.open("/");
await tick(); await tick();
if (!byClass(backdrop, "picker-up")[0].disabled) throw new Error("up button not disabled at the filesystem root");
escape(documentListeners, dialogNode);
await opened3;

// 6) the current path is an editable field: typing an absolute path and
//    pressing Enter navigates there (the manual route the user asked for)
const opened4 = picker.open("/outside");
await tick(); await tick();
const pathInput = byClass(backdrop, "picker-path")[0];
const nameInput = byClass(backdrop, "picker-newname")[0];
if (!pathInput || !nameInput) throw new Error("picker has no path / new-directory input");
if (pathInput.value !== "/outside") throw new Error("path field did not show the current path");
pathInput.value = "/";
pathInput.fire("keydown", { key: "Enter", target: pathInput, preventDefault() {}, stopPropagation() {} });
await tick(); await tick();
if (calls[calls.length - 1] !== "/") throw new Error("typed path did not navigate: " + calls.join(" > "));
if (pathInput.value !== "/") throw new Error("path field was not synced to the listed directory");
if (backdrop.hidden) throw new Error("Enter in the path field closed the dialog instead of navigating");

// 7) "new directory" calls the mkdir endpoint for the listed directory and
//    enters the created directory; the name field is cleared
nameInput.value = "fresh";
byClass(backdrop, "picker-newdir")[0].fire("click");
await tick(); await tick();
if (mkdirCalls.length !== 1) throw new Error("new-directory did not call the endpoint: " + JSON.stringify(mkdirCalls));
if (mkdirCalls[0][0] !== "/" || mkdirCalls[0][1] !== "fresh") {
  throw new Error("new-directory sent the wrong pair: " + JSON.stringify(mkdirCalls));
}
if (calls[calls.length - 1] !== "/fresh") throw new Error("new-directory did not enter the created directory");
if (nameInput.value !== "") throw new Error("the name stayed in the field after creating");

// 8) an empty name is a no-op - no request, no navigation
nameInput.value = "   ";
const callsBefore = calls.length;
byClass(backdrop, "picker-newdir")[0].fire("click");
await tick(); await tick();
if (calls.length !== callsBefore || mkdirCalls.length !== 1) {
  throw new Error("an empty directory name still called the API");
}

// ---------------------------------------------------------------------------
// Navigation scroll (navscroll.js): the listing is rebuilt on every step, so
// stepping up has to bring the directory it came out of back into view instead
// of dropping the user at the top of the parent listing.
// ---------------------------------------------------------------------------

// Layout, handed to navscroll.js through FakeNode.getBoundingClientRect: the
// listing is a 100px window and every row is 30px tall, starting at content
// offset 500. The coordinates a browser reports are viewport ones, so the
// scrolled distance is subtracted - and the fake keeps its scrollTop when a
// listing is replaced (a browser would reset it), so any change here is a write
// navscroll.js made.
const listEl = byClass(backdrop, "picker-list")[0];
if (!listEl) throw new Error("picker has no listing element");
listEl.scrollHeight = 1000;
listEl.clientHeight = 100;
measure = (node) => {
  if (node === listEl) return { top: 0, height: 100 };
  const rows = byClass(backdrop, "picker-dir");
  const index = rows.indexOf(node);
  if (index < 0) return { top: 0, height: 0 };
  return { top: 500 + index * 30 - listEl.scrollTop, height: 30 };
};
const rowOf = (name) => byClass(backdrop, "picker-dir").find(
  (row) => row.children.some((child) => child.textContent === name),
);

// 9) "up" from /fresh lists "/" and scrolls to the fresh directory, marked as the
//    one the step came out of (top 530, height 30 in a 100px window -> 495)
listEl.scrollTop = 700;
byClass(backdrop, "picker-up")[0].fire("click");
await tick(); await tick();
if (calls[calls.length - 1] !== "/") throw new Error("up did not navigate to the parent: " + calls.join(" > "));
const fresh = rowOf("fresh");
if (!fresh) throw new Error("the parent listing does not show the directory we came from");
if (!hasClass(fresh, "is-revealed")) throw new Error("the directory we came from is not marked");
if (listEl.scrollTop !== 495) throw new Error("the listing did not scroll to it: scrollTop " + listEl.scrollTop);
if (hasClass(rowOf("outside"), "is-revealed")) throw new Error("a sibling row was marked as the one we came from");

// 10) a jump that is NOT upwards restores the position that directory was left
//     at - here "/outside" was left at the top in step 6, and the current 700
//     must not follow the user there
pathInput.value = "/outside";
pathInput.fire("keydown", { key: "Enter", target: pathInput, preventDefault() {}, stopPropagation() {} });
await tick(); await tick();
if (calls[calls.length - 1] !== "/outside") throw new Error("typed jump failed: " + calls.join(" > "));
if (listEl.scrollTop !== 0) throw new Error("a jump restored the wrong position: " + listEl.scrollTop);

// 11) leaving a directory remembers where it stood, and stepping back up centres
//     the directory that was entered
listEl.scrollTop = 411;
byClass(backdrop, "picker-dir")[0].fire("click");
await tick(); await tick();
if (calls[calls.length - 1] !== "/outside/inner") throw new Error("click did not descend: " + calls.join(" > "));
byClass(backdrop, "picker-up")[0].fire("click");
await tick(); await tick();
if (listEl.scrollTop !== 465) throw new Error("stepping up did not centre the directory entered: " + listEl.scrollTop);
if (!hasClass(rowOf("inner"), "is-revealed")) throw new Error("the directory entered is not marked after stepping up");

escape(documentListeners, dialogNode);
await opened4;

console.log("picker harness OK " + JSON.stringify({ calls, mkdirCalls }));
