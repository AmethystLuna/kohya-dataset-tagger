/**
 * Headless behaviour harness for renderCaptionDiff (web/js/autotag.js).
 *
 * The point of the diff table is WHICH chip gets WHICH class. Asserting that
 * the strings "add"/"del" appear somewhere in autotag.js proves nothing about
 * that, so this drives the real function against a fake DOM and checks the
 * classes, the chip text and the separator.
 *
 * Usage: node diff_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node diff_harness.mjs <web dir>");

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.className = "";
    this.textContent = "";
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
}

globalThis.document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
};

const mod = await import(pathToFileURL(WEB + "/js/autotag.js").href);
if (typeof mod.renderCaptionDiff !== "function") throw new Error("renderCaptionDiff is not exported");

const chips = (cell) => cell.children.filter((node) => node.tagName === "SPAN");
const classes = (cell) => chips(cell).map((node) => node.className || null);
const texts = (cell) => chips(cell).map((node) => node.textContent);
const flat = (cell) => cell.children.map((node) => node.textContent).join("");

function render(before, after) {
  const cellBefore = new FakeNode("td");
  const cellAfter = new FakeNode("td");
  mod.renderCaptionDiff(cellBefore, cellAfter, before, after);
  return { cellBefore, cellAfter };
}

function expect(before, after, wantBefore, wantAfter) {
  const out = render(before, after);
  const gotBefore = classes(out.cellBefore);
  const gotAfter = classes(out.cellAfter);
  if (JSON.stringify(gotBefore) !== JSON.stringify(wantBefore)) {
    throw new Error("before classes for " + JSON.stringify(before) + ": " + JSON.stringify(gotBefore) + " expected " + JSON.stringify(wantBefore));
  }
  if (JSON.stringify(gotAfter) !== JSON.stringify(wantAfter)) {
    throw new Error("after classes for " + JSON.stringify(after) + ": " + JSON.stringify(gotAfter) + " expected " + JSON.stringify(wantAfter));
  }
  return out;
}

// 1) an added tag is green on the right; the shared tags stay unmarked
let out = expect("1girl, solo", "1girl, solo, smile", [null, null], [null, null, "add"]);
if (texts(out.cellAfter)[2] !== "smile") throw new Error("added chip text wrong");

// 2) a removed tag is red on the left and gone on the right
out = expect("1girl, solo, old_tag", "1girl, solo", [null, null, "del"], [null, null]);
if (texts(out.cellBefore)[2] !== "old_tag") throw new Error("removed chip text wrong");

// 3) both at once, in a predictable order
expect("a, b", "a, c", [null, "del"], [null, "add"]);

// 4) no change at all marks nothing
out = expect("1girl, solo", "1girl, solo", [null, null], [null, null]);
if (flat(out.cellAfter) !== "1girl, solo") throw new Error("separator wrong: " + JSON.stringify(flat(out.cellAfter)));

// 5) an empty side falls back to the shared wording instead of an empty cell
out = render("", "1girl");
if (out.cellBefore.textContent.length === 0) throw new Error("empty before cell rendered nothing");
if (classes(out.cellAfter).join(",") !== "add") throw new Error("first caption was not marked as added");

// 6) clearing a caption that exists boxes the "none" red on the right
out = render("1girl, solo", "");
if (JSON.stringify(classes(out.cellBefore)) !== JSON.stringify(["del", "del"])) {
  throw new Error("clearing did not mark the left column: " + JSON.stringify(classes(out.cellBefore)));
}
const cleared = out.cellAfter.children.filter((node) => node.tagName === "SPAN");
if (cleared.length !== 1 || cleared[0].className !== "cleared") {
  throw new Error("clearing did not box the right cell: " + JSON.stringify(out.cellAfter.children.map((n) => n.className)));
}

// 7) nothing before and nothing after is not a loss: no red box
out = render("", "");
if (out.cellAfter.children.some((node) => node.className === "cleared")) {
  throw new Error("an empty row was reported as a cleared caption");
}
if (out.cellBefore.children.length !== 0 || out.cellAfter.children.length !== 0) {
  throw new Error("empty cells should hold plain text, not chips");
}

console.log("diff harness OK " + JSON.stringify({ sample: flat(render("a, b", "a, b, c").cellAfter) }));
