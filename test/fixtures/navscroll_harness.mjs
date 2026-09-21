/**
 * Headless behaviour harness for web/js/navscroll.js.
 *
 * The module is pure path logic plus one scroll write, so this runs the real
 * file against a minimal fake scroller - no jsdom, no browser. It pins the three
 * things the callers rely on:
 *
 *   childOnPath  - which folder a navigation up (or to an ancestor) came out of,
 *                  including the segment-prefix trap ("/data" vs "/datax") and
 *                  Windows separators / drive-letter case;
 *   scroll memory - recall() answers null for an unknown directory (0 is a real
 *                  position) and the map stays bounded;
 *   revealRow    - centres a row inside one scroller, clamps at both ends and
 *                  reports false when there is no layout to measure.
 *
 * Usage: node navscroll_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node navscroll_harness.mjs <web dir>");

const nav = await import(pathToFileURL(WEB + "/js/navscroll.js").href);

function eq(actual, expected, what) {
  if (actual !== expected) {
    throw new Error(what + ": got " + JSON.stringify(actual) + ", expected " + JSON.stringify(expected));
  }
}

// --- childOnPath ------------------------------------------------------------
const child = nav.childOnPath;
eq(child("/data", "/data/set"), "/data/set", "direct child");
eq(child("/data", "/data/a/b/c"), "/data/a", "grandchild: the FIRST segment under the parent");
eq(child("/data/", "/data/a/b"), "/data/a", "trailing separator on the parent");
eq(child("/", "/a/b"), "/a", "filesystem root as the parent");
eq(child("F:\\sets", "F:\\sets\\a\\b"), "F:\\sets\\a", "windows separators survive");
eq(child("F:\\", "F:\\a"), "F:\\a", "windows drive root as the parent");
eq(child("F:\\Sets", "f:\\sets\\one"), "F:\\Sets\\one", "drive/path case is ignored");
eq(child("F:\\sets", "F:/sets/a"), "F:\\sets\\a", "mixed separators still match");
eq(child("/data", "/data"), null, "a directory is not below itself");
eq(child("/data", "/datax"), null, "a name that only starts with the parent");
eq(child("/data", "/datax/y"), null, "segment-prefix trap, one level deeper");
eq(child("/data", "/other"), null, "siblings");
eq(child("/data", "/"), null, "an ancestor is not a child");
eq(child("/data", ""), null, "no child path");
eq(child("", "/data"), null, "no parent path");
eq(child(null, "/data"), null, "null parent");
eq(child("/data", null), null, "null child");

// --- samePath ---------------------------------------------------------------
eq(nav.samePath("/data/a", "/data/a"), true, "identical");
eq(nav.samePath("/data/a/", "/data/a"), true, "trailing separator");
eq(nav.samePath("F:\\Sets\\a", "f:/sets/a"), true, "case and separator");
eq(nav.samePath("/data/a", "/data/b"), false, "different names");
eq(nav.samePath("/data/a", "/data/a/b"), false, "different depths");
eq(nav.samePath("", ""), false, "two empty paths are not the same directory");

// --- scroll memory ----------------------------------------------------------
const memory = nav.createScrollMemory(2);
eq(memory.recall("/a"), null, "an unknown directory has no position (null, not 0)");
eq(memory.size(), 0, "empty to start with");

memory.remember("/a", 120);
eq(memory.recall("/a"), 120, "remembered position");
memory.remember("/a", 300);
eq(memory.recall("/a"), 300, "the later position wins");
eq(memory.size(), 1, "re-remembering does not add an entry");

eq(memory.remember("", 5), false, "an empty path is not stored");
eq(memory.size(), 1, "the rejected entry is not in the map");

memory.remember("/b", 7);
memory.remember("/c", 9);
eq(memory.size(), 2, "the map stays bounded");
eq(memory.recall("/a"), null, "the oldest entry is the one dropped");
eq(memory.recall("/b"), 7, "the newer entries survive");
eq(memory.recall("/c"), 9, "the newest entry survives");

memory.remember("/zero", null);
eq(memory.recall("/zero"), 0, "a null position is 0, which recall() still reports");

memory.clear();
eq(memory.recall("/c"), null, "clear() drops everything");
eq(memory.size(), 0, "clear() empties the map");

// --- revealRow --------------------------------------------------------------
function scroller(options) {
  const opts = options || {};
  return {
    scrollTop: opts.scrollTop || 0,
    scrollHeight: opts.scrollHeight === undefined ? 1000 : opts.scrollHeight,
    clientHeight: opts.clientHeight === undefined ? 100 : opts.clientHeight,
    getBoundingClientRect() { return { top: 0, height: this.clientHeight }; },
  };
}

function row(top, height) {
  return { getBoundingClientRect: () => ({ top, height }) };
}

// A 100px viewport over content that is 1000px tall: the row at 400..420 is
// centred by moving the scroller to 400 - (100 - 20) / 2 = 360.
let view = scroller();
eq(nav.revealRow(view, row(400, 20)), true, "a laid out row is revealed");
eq(view.scrollTop, 360, "centred row position");

// Centring never scrolls above the first row.
view = scroller();
nav.revealRow(view, row(5, 20));
eq(view.scrollTop, 0, "clamped at the top");

// ... nor past the last screen.
view = scroller();
nav.revealRow(view, row(990, 20));
eq(view.scrollTop, 900, "clamped at the bottom (scrollHeight - clientHeight)");

// "nearest" leaves an already visible row alone.
view = scroller();
eq(nav.revealRow(view, row(10, 20), { align: "nearest" }), true, "visible row reported");
eq(view.scrollTop, 0, "a visible row is not moved");

// ... and moves the scroller as little as possible for one that is not visible:
// below the fold -> bottom aligned (400 - (100 - 20) = 320), above it -> top.
view = scroller();
nav.revealRow(view, row(400, 20), { align: "nearest" });
eq(view.scrollTop, 320, "a row below the fold is aligned to the bottom edge");
view = scroller({ scrollTop: 300 });
nav.revealRow(view, row(-50, 20), { align: "nearest" });
eq(view.scrollTop, 250, "a row above the fold is aligned to the top edge");

// No layout to measure: report false so the caller restores its own position.
view = scroller({ clientHeight: 0, scrollTop: 42 });
eq(nav.revealRow(view, row(400, 20)), false, "a scroller without layout is not touched");
eq(view.scrollTop, 42, "the position is left alone");
eq(nav.revealRow(scroller(), row(400, 0)), false, "a row without height is not revealed");
eq(nav.revealRow(scroller(), { getBoundingClientRect: () => ({ top: 1, height: 5 }) }), true, "a plain box works");
eq(nav.revealRow(scroller(), null), false, "no row");
eq(nav.revealRow(null, row(1, 1)), false, "no scroller");
eq(nav.revealRow({}, row(1, 1)), false, "an element without geometry is not revealed");

console.log("navscroll harness OK");
