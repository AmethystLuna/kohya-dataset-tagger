---
name: batch-manual-order
description: 2026-10-13 the batch panel can reorder a scope's tags by hand - one container for a single image, the front/middle/back containers of §4.5 ③'s order_first/order_last for several - written through the same preview/backup/undo path as every other batch edit
metadata:
  type: topic
---

# The batch panel's manual tag order (2026-10-13)

The user's words: "add a manual tag reordering feature to the batch tag card, editing the common tags of
the selected scope: with a single selection, one container and drag to sort; with a multiple selection,
three containers, everything starting in the middle, moves inside the middle doing nothing, and the front
and end containers used to move a tag to the very front / the very end. Deleting and adding tags allowed."

Four questions were asked before any code was written, because the request was ambiguous in ways that
decide the whole design. The answers, verbatim in effect:

1. **single / multiple = how many images are in scope** (not how many tags are selected);
2. the result **is written into the captions**: a real batch edit, through the existing preview →
   backup → undo path;
3. **delete = remove the tag from every caption in scope, add = add it to every caption** (the existing
   `edit_tags` semantics, reused rather than reinvented);
4. **the common tags come first in the new order, and every other tag keeps its own relative order**
   after them.

## What changed

### 1. The rows ARE the order editor (no second list)

The common-tag section already had one editable input per tag (`commonTags` ∩ the scope, capped at
`COMMON_ROWS = 200`), where editing a line renames, clearing it (or the ×) deletes, and deleting a line
never shifts the lines after it. That editor is now also the order editor: each row gained a **grip**
(`⠿`) and the rows are painted into containers.

A separate "reorder" panel beside the editor was rejected on the project's own invariant: it would be a
second live copy of the same tags, and the two would drift the moment one of them was edited.

The containers are the backend's two order blocks plus a middle that means "leave these where they are",
so the middle is **never sent**. With a single image in scope the middle would be meaningless - that
caption's own order *is* the answer - so the same rows live in one container and the other two are closed
by CSS (`.batch-order.is-single`).

**A behaviour change worth naming**: with one image in scope the rows are now that caption's **own order**
(`info.lists[0]`), not the alphabetical intersection `commonTags()` returns. The single container is a
"here is the order on disk, drag it into the order you want" list; starting from an alphabetised copy would
have been a list of the same tags in an order nothing on disk has.

### 2. §4.5 ③ gains two fields

    {"op": "edit_tags", "order_first": ["girl", "smile"], "order_last": ["hat"]}

- the two blocks place their tags at the front / at the end **in the listed order**; every other tag keeps
  its **own relative order** between them;
- a tag no caption carries is skipped (the frontend sends tags the user moved, which `pairs` may have
  renamed in the same request), a duplicate counts once, and a tag in both blocks counts as front;
- **application order is `pairs` → order → `tags`**, deliberately: the addition's own `position`
  (`prepend`/`append`) promises "the very front / the very end", so an addition must land **outside** the
  blocks - ordering after the additions would quietly pull a freshly prepended tag back into the middle;
- **the middle is not sent**, and that is the whole reason for two blocks instead of one order: a batch is
  a set of captions that do **not** share one order. Listing every common tag would impose the first
  caption's sequence on all of them - exactly the kind of change the user never asked for and cannot see
  in the containers. A46 scores this over two files ordered differently.

### 3. Drag, and a keyboard path

- `dragstart` on the grip (the drag image is the whole row), `dragover`/`drop` on the **container**
  (`dragging` is the row index; row 0 is a real row, so nothing tests truthiness), and a drop that lands
  on a row inserts **before** it while a drop on the container's background appends. The rules live in the
  exported pure helper `moveRowIn`, so they are testable without a DOM;
- **a move inside the middle is not a move** (the middle has no order of its own to change, and the
  backend never sees it), while moving *into* or *out of* the middle is;
- **the keyboard is the same three containers**: ↑/↓ move a row inside its container, `Home` puts it at
  the very front and `End` at the very end - which in the single-container layout means the first/last row
  of that one container, never a hidden one. A control only a mouse can reach would be a new dead end for
  everyone else, and the repo's a11y criteria exist for exactly that;
- the panel's `relabel()` now also repaints the rows' own names (the grip's "drag to reorder {tag}" and the
  × button's "remove"), which the rows' tooltips never did before.

### 4. Nothing to send is not a change

With several images and every row in the middle, pressing Preview says **"no changes detected"** instead of
sending a request that changes nothing - `orderEdit` returns an empty object unless a row really left the
middle, and for a single image unless the rows are no longer in their original sequence (a rename travels
in `pairs`). The harness relies on this: to observe "the middle is never sent" it has to rename a row
first, because otherwise there is no request at all.

### 5. The entrance (the same day, after the user asked "入口呢")

The first version hid the whole container host (`orderHost.hidden = tags.length === 0`) whenever the
scope had **no common tags** - which is the ordinary state, since a handful of images with different
captions usually share nothing. The feature was therefore invisible exactly when a user went looking for
it, and the report was the two-character question. What is on screen now:

- the container host is **always rendered**: with nothing to order it is an empty container plus the
  line that says *why* it is empty - an honest empty state instead of an absence;
- the always-visible hint (`batch.commonHint`) names the handle ("拖动一行左侧的手柄＝排序"), so the
  affordance is described even before a row exists;
- the grip itself is larger and reacts to hover (`cursor: grab` → `grabbing`, colour + a background on
  hover), because a dim 13px glyph at the start of a row does not read as "drag me";
- `batch_harness.mjs` section 15.6 scores it: with three images whose captions share no tag, all three
  containers are still in the DOM, the host is visible, the "why it is empty" line is visible, and the
  write button stays disabled. **Falsified**: putting `orderHost.hidden = tags.length === 0` back turns
  that criterion red.

### 6. "The containers start out without the common tags" (same day, the second report)

A real hole, one layer down from the entrance: **nothing ever read the captions of a scope that reaches
outside the directory on screen**. `ensureProbed()` probes `state.images` (the gallery's own listing),
which covers the selected / filtered / current-directory scopes - but not "current directory **and its
subdirectories**", and not a selection naming images the gallery has not read. For those, the panel sat
on its "reading caption…" note with empty containers **forever**.

The ask is now explicit and owned by the panel that needs it:

- `app.js` gained `probeScopeCaptions(paths)`: it skips the paths whose caption is already in
  `captionInfo` (or already in flight, tracked by an in-flight-only `scopeProbing` set) and probes the
  rest with the existing chunked `probeCaptions`, which reports each chunk through the normal
  `applyFilter()` - the very call that repaints the batch panel. It is handed to the panel as
  `requestCaptions`;
- `batch.js` asks for exactly the images it is looking at (`requestCaptions(info.paths)`) whenever its
  scope has captions it cannot see, and only while its tab is on screen;
- **the "already asked" memory is deliberately not a Set**: `captionInfo` is cleared on navigation and
  after a write, and a second set would go stale in exactly those moments and leave the panel empty
  again. The Set here only holds what is in flight;
- the empty state now says **which nothing** it is: `batch.commonNoneImages` for a scope with no images
  ("先在上面选好范围，或者到画廊里选几张图" - a next step) versus `batch.commonNone` for images that
  share no tag / have no caption. One key for both made the second read like a broken feature.

Criteria: `test_web_batch.py` (`test_the_panel_asks_for_the_captions_its_scope_needs`,
`test_the_empty_common_editor_says_which_nothing_it_is`) and the harness, which asserts the panel asks
for exactly the scope's paths when the scope holds an unread image. **Falsified**: removing the
`requestCaptions(info.paths)` call turns both red.

### 7. The container names, and that the containers are ordered (the third report)

- the three titles are the user's own words now: **前置 / 维持 / 后置** (`Prefix / Keep / Suffix`,
  `プレフィックス / 維持 / サフィックス`) instead of 最前 / 中间（保持原顺序）/ 最后; the English pair was
  `Prepend / Append` until 2026-10-13, when the user pointed out that `Prefix / Suffix` is the
  wording this domain actually uses - and the Japanese pack followed it (the tagger's own write modes
  keep `Append` / `Prepend`, because those are verbs about inserting tags, not places in a caption);
- **the front and end containers are ordered among themselves** - that was already true (the backend's
  `order_first` / `order_last` are placed *in the order they are listed*, and the multi-container hint
  now says so: "两个容器内部的顺序就是最终顺序，可以继续拖动"), but it was only asserted by the backend
  criterion and A46. The harness now drives it in the frontend too: a second row dragged into 前置
  lands after the first, `↑` reorders inside the container, and that order is what `order_first`
  carries. **Falsified twice**: making a background drop insert at the front turns the harness red at
  `End must move the row to the very end`, and making the front container un-reorderable turns it red
  at `ArrowDown did not move the row`.

### 8. Three bugs a screenshot exposed ("这对吗", same day)

The user sent a screenshot: **6 images selected**, scope 当前选中（6 张）, three containers on screen and
**all of them empty**, under the single-container title 标签顺序（拖动排序）, with the note "当前范围里没有
图片". Every one of those was wrong, and the real captions of that directory share **35 tags** (checked by
running `commonTags` over the six `.txt` files on disk - the data was never the problem):

1. **The panel only followed the *select*, not the images behind it.** `onSelectionChange` in `app.js`
   refreshed the tagger (`autotagPanel.refreshScopes()`) and the toolbar readout - and nothing else.
   Picking images (or 全选) does not move the scope select's value, so no `onChange` ever fired and the
   common-tag editor kept describing the selection it was last built for: at boot, *no images at all*.
   That is also what "the containers start out without the common tags" was. `batchPanel.refreshScope()`
   is now called there too, and the panel itself watches for a change in the providers rather than only
   in the select: `onPaint` compares the scope's paths with the signature it last painted and recomputes
   when they differ (guarded against re-entering `refreshScope`, which repaints the control). That second
   half also covers the recursive listing arriving after the scope was chosen.
2. **The mode copy was painted at construction.** The front container's name and the hint depend on
   whether the scope holds one image or several - runtime state - so a one-off painting left the
   single-container title above three containers. Both are now painted by `paintModeCopy()`, registered
   as a painter (language switches) and called from `layoutBuckets()` (mode changes).
3. **The empty-state wording was painted at construction too**, so it still said "当前范围里没有图片"
   under six selected images. It is painted by `paintEmptyNote()` from `paintCommonRows()` and from
   `relabel()` now, i.e. from the state it describes - and **the count is passed in** rather than read
   from `commonRows`, because the paint happens before the next rows are built: writing it the obvious
   way left the "nothing here" line on screen *above a full container*, which the new criterion
   ("the empty-state line is still on screen while the containers hold rows") caught immediately.

Criteria: the harness gained three sections (15.6 extended with the no-images wording, 15.7 for the mode
copy, 15.8 for a provider change with an unchanged select), and `test_web_batch.py` gained
`test_the_panel_follows_the_gallery_selection` for the `app.js` half plus a check that the empty-state
wording is painted from state. **Falsified four ways**: making the repaint skip the recompute turns
15.8 red ("a provider change did not reach the editor"), painting the mode copy only at construction
turns 15.7 red ("the single layout must name its one container the single way, got 前置"), dropping
`paintEmptyNote()` turns 15.6 red ("a scope with no images must say so"), and passing a zero row count
turns the new note-visibility criterion red ("the empty-state line is still on screen while the
containers hold rows").

**The lesson worth keeping**: a panel that reads a *provider* (the scope control) has two kinds of
change to follow - the control's value and the data behind it - and only the first one announces itself.
The copy that depends on runtime state is a third thing that must be painted where the state changes,
not where the panel is built.

### 9. The containers' layout ("drag 前置 and 后置 to the top", same day)

The three containers were a flat column in the order front / middle / back, and the middle starts out
holding **every** common tag (up to 200 rows). So reaching 后置 meant dragging past the whole list - the
user's own words: "把前置和后置拉到顶上，不然拖动操作起来会很折磨". Now:

- **前置 and 后置 share one row at the top**, each half the width; **维持 gets its own row below**, full
  width (`.batch-order-top` + the middle bucket as its sibling);
- that row is **sticky** (`position: sticky; top: 0`) with the panel's own background, so a long 维持
  list scrolls underneath instead of taking the drop targets off screen mid-drag. The panel is already a
  scroll box (`.batch-order { max-height: 40vh; overflow: auto }`), so this is the whole mechanism - no
  second scroll area;
- in the single-image layout the top row holds the one container and CSS still closes the other two, so
  nothing changes there.

**Measured in real headless Chrome** (`tools/measure_web_layout.py --root-images 0`, i.e. the empty
multi-image layout - the probe gained `.batch-order-top` and all three bucket selectors): the top row is
340×44.5 px at the top of the host, 前置 is the left half (910→1078) and 后置 the right half (1082→1250)
**at the same top**, and 维持 sits below them (top 491.5) at the full 340 px width.

Criteria: the harness pins the DOM structure (the top row holds exactly front and back; the middle is its
sibling below), `test_web_batch.py` pins the CSS (the shared row, the sticky rule) and the append split,
and the probe's selector list is checked by `test_measure_web_layout.py`. **Falsified twice**: appending
the containers flat again turns the harness red ("the top row must hold exactly 前置 and 后置, got []"),
and dropping `position: sticky` turns the static criterion red.

### 10. Borders, and a cap on the top row (same day, the next message)

Two more sentences from the user: "the containers need a border", and "the pinned row may take at most
half, otherwise a top full of tags leaves no room below".

- **a visible border** (`border: 1px solid var(--line)`) replaces the invisible dashed one that only
  appeared while a drag was over the container: three unoutlined groups of rows read as one flat list,
  and the whole point of the containers is that their contents mean different things;
- **the sticky top row is capped at half the editor's own height** (`max-height: 20vh` against
  `.batch-order`'s `40vh`) and scrolls inside itself, so dragging fifty tags into 前置 cannot eat the
  list you are dragging them out of;
- measured in real headless Chrome on the empty multi-image layout: the geometry is unchanged (top row
  340×44.5 px at the top of the host, 前置 the left half, 后置 the right half at the same top, 维持 below
  at the full width) - the cap only bites when the top row is tall, which the canned probe data cannot
  produce. **Honest limit**: the border is a CSS declaration verified by a static criterion, not a
  photograph - the probe measures geometry, and its `--shot` page stops at the boot state (the filter
  tab), so the committed tool cannot photograph the batch panel.

Criteria: `test_web_batch.py`'s container criterion gained both (the border declaration, and
`max-height: 20vh` alongside the editor's `40vh`). **Falsified twice**: the border back to
`1px dashed transparent`, and the cap removed - each turns that criterion red.

### 11. The three standalone ops go, and the containers get their own add row (same day)

Two more sentences: "追加/前置/移除 tag 三个功能已经被这一个给替代了吧，不需要了" and "前置和后置容器里可以加
一个常驻的新增的按钮".

- **the panel's op list shrank from nine to six**: 公共 tag 编辑 / 替换 / 正则替换（每个 tag）/ 正则替换
  （整个 caption）/ 去重并规范空白 / 按词表排序. The three dropped ops are covered by the common-tag
  editor - its container add rows add a tag at either end, clearing a row deletes a tag, and 替换 with
  an empty replacement removes a tag that is **not** common (the label has said 留空＝删除 all along);
- **the shared "new tag" field and the "put new tags at the beginning" checkbox are gone with them**:
  the containers now each carry a **permanent add row** (input + 添加) at the bottom of 前置 and 后置. The
  container *is* the position, which is why the checkbox had nothing left to say;
- additions are chips inside the container they belong to, and they render on the block's **outer** side
  (above the rows in 前置, below them in 后置) - the same place they land in the caption;
- **the contract gained two fields** (§4.5 ③): `tags_first` / `tags_last` add a tag at the outer end of
  its own block. `tags` + `position` could only say "the very front" or "the very end" - one answer per
  request - so two additions on opposite ends needed their own lists. A tag the caption already carries
  is left alone, a tag typed into the other container **moves** there instead of being listed twice, and
  a tag that is a common row is refused with a toast (`batch.addIsCommon`): it is not an addition, it is
  a row to drag;
- the two add inputs are **plain inputs, without the shared dropdown**, and that is the recorded reason
  rather than an oversight: they live inside `.batch-order`, whose `overflow: auto` (and the pinned top
  row's own) clips an absolutely positioned dropdown. It is the same reason the common rows have always
  been plain inputs, and why the old add field sat outside the scroll box;
- **the endpoint keeps the three ops** (`BATCH_OPS` is unchanged): they are the batch endpoint's
  primitive set, and the panel is the only consumer that stopped using them. The parity criterion is now
  one-directional - every op `api.js` names must exist in the backend (the 400 `bad_op` it guards), and
  the backend-only primitives are asserted by name - and the `BATCH_OP` docstring says exactly that.

Criteria: `test_web_batch.py`'s parity criterion rewritten as a subset check with the three named as
backend-only, a new criterion for the container add rows, and the harness's section 15.9 (chips land in
the container that was typed into, `tags_first` / `tags_last` reach the request, Enter works like the
button, a common row is refused with a warning, the same tag typed into the other container moves).
Acceptance **A46** gained the block additions (outer end of their own block, no duplicate).
**Falsified twice**: swapping the two blocks in `blockAdditions` turns the harness's `tags_first`
assertion red, and putting `append` back into `BATCH_OP` turns the parity criterion red.

### 12. The pinned row vanished - a flexbox shrink, not a layout choice (same day)

The user's screenshot: the 公共 tag section showed the 维持 container with the scope's rows and **no 前置
or 后置 at all** ("前置和后置的容器丢了"). The rows were in the middle, so this was the multi-image layout -
the top row was there, and it was **0 px high**.

**Why**: a flex item with `overflow: auto` loses its automatic minimum size (the `min-height: auto`
relief only applies while `overflow` is `visible`). `.batch-order` is a column flex box with
`max-height: 40vh`, and its middle item - the long list of common tags - cannot shrink below its content.
So the moment the editor overflowed, the only shrinkable item was the pinned row, and `flex-shrink: 1`
duly shrank it to nothing. Every case the committed layout probe can produce has two shared tags, so the
editor never overflowed there and the measurements stayed green.

**Fix**: `flex: 0 0 auto` on `.batch-order-top` (and, for the same reason, on the middle container) - the
two rows keep their content height and the group scrolls, which is what the sticky row and the 20vh cap
were always meant to do together. Single-image mode also drops the cap (`max-height: none`): there is no
second list to protect, and the cap would waste half the editor.

**Reproduced and fixed in real Chrome**, over the panel's own DOM shape with 48 rows at 1280x900: with
`flex: 1 1 auto` the top row measures **0 px** (the two containers, 6 px) and with `flex: 0 0 auto`
**97 px**. The scratch page was written outside the repository, used the same `find_chrome` /
`run_browser` the committed probe uses, and was deleted afterwards (the committed probe cannot reach this
state: its canned captions share two tags, so the editor never overflows). The static criterion pins
`flex: 0 0 auto` on both rules and carries the two measured numbers. **Falsified**: putting
`flex: 1 1 auto` back turns it red.

The same screenshot exposed one more thing: `batch.commonHint` still said "下面的输入框加新 tag" ("use the
field below to add"), pointing at the shared add field §11 had just removed. All three packs now name the
container's own add row instead, and the English one had to lose a clause to stay inside the 250-character
hint budget (`test_web_i18n.py`).

### 13. The add control becomes one row-shaped thing in two states (same day)

The user's shape for it: "新增 tag 的控件样式可以优化一下，和其他 tag 行同构的一个按钮，显示 + 号，点击变成 tag
编辑框的样式，留空然后失去焦点后恢复". So the `[input][添加]` pair is gone; each of the two containers ends
with **one control in two states**:

- **closed**: a `+`, dashed and quiet, occupying the row's whole editable width;
- **open** (click the +): a tag input - literally `textInput("batch-common-input batch-bucket-add-input")`,
  the same class a tag row's box has, so the two states are the same box;
- **commits** on Enter and on a blur that has text in it; a blur with **nothing** in it is not an edit and
  puts the + back; Escape abandons the box; a tag that is already a common row is refused with the same
  toast as before (it is a row to drag, not an addition).

**"同构" is a geometry claim, so it was measured.** The row is built from the tag row's *own elements*: a
hidden grip (`visibility: hidden`, so no width is hard-coded) and a hidden `×` of the same
`btn btn-mini batch-common-remove` the rows use, which keeps the + and the box exactly as wide as the
inputs above instead of running under where the `×` sits. In real Chrome over the panel's own DOM shape
and the real `app.css`: the tag rows' inputs and both states of the add control are **left 38, right 704,
width 666** (the + is 28px high, like the box it becomes - `min-height: 28px`, measured, or the row jumped
4px on click), and the real grip and its spacer are both 18x21. The committed probe now measures
`.batch-bucket-add` too (108.8x28 in the empty multi-image layout), and
`test_measure_web_layout.py` pins the selector.

Criteria: the harness's section 15.9 was rewritten around the two states (starts closed, the click opens
it, an empty blur reverts *without* adding, a blur with text adds and reverts, Escape abandons, Enter
adds, a common row is refused with a warning, the same tag typed into the other container moves it);
`test_web_batch.py` pins the shared input class, the `+`, the two spacers, the blur commit and the
empty guard. **Falsified**: dropping the revert turns the harness red ("an empty input that lost focus did
not go back to the +"). **Honest limit**: the probe cannot click, so the two states were driven in the
harness and the alignment measured in a scratch page outside the repository (same `find_chrome` /
`run_browser`), deleted afterwards.

### 14. The dashed box becomes a full-width `+` that appends an element (same day, the next message)

"虚线框最好占满整行，点击编辑的时候输入框左右两侧的控件也要在。然后这个虚线框可以常驻，点击的时候以新增一个元素作为
实现方法，留空时失焦自动删除" - three changes to §13's control, and the third one changes what it *is*:

- the dashed `+` is now the **whole row** (the row has no spacers any more). Measured in the committed
  probe's empty multi-image layout: **158px** wide inside a 168px container - exactly the inner width
  (168 - 2x1 border - 2x4 padding) - against 108.8px for the indented version;
- clicking it does **not** turn it into a box: it **appends an element** to that container - a row with
  the grip, the box and the `×`, the same shape as the tag rows above it ("输入框左右两侧的控件也要在" comes
  free, because it *is* a tag row). The box takes the focus;
- the element is an **addition**, not a common row: it travels as `tags_first` / `tags_last`, its `×`
  takes it away, an empty one that loses focus removes itself ("留空时失焦自动删除"), a tag naming a common
  row is refused with the same toast, and Escape abandons it. Additions are no longer chips - chips could
  not be edited, and the user asked for an editable element;
- the grip of an addition is the one move an addition has: **to the other end**. Dropping it on the other
  container re-ends it, and dropping it on the middle discards it (an addition cannot live in "leave these
  where they are"). Within one container the additions keep the order they were added in - the payload
  puts them at the outer end of their block, so the visual order is the caption's order.

Chips became rows, so `blockAdditions` still reads `[{tag, where}]` (an empty `tag` contributes nothing,
which is also what makes the self-removal harmless if it ever lagged a repaint).

Criteria: the harness's section 15.9 was rewritten again - the + is one full-width button, clicking it
appends a three-part element, an empty blur removes it, text survives the blur and reaches the request,
Escape and the `×` remove it, Enter keeps it, a common row is refused with a warning, and the grip moves
an addition between containers / discards it into the middle. `test_web_batch.py` pins the full-width
button, the append, the empty-element removal and the blur that settles it. **Falsified**: keeping an
empty element turns the harness red ("an empty addition row survived losing focus"). **Honest limit**: the
probe still cannot click, so the interaction lives in the harness and the width was measured in it.

### 15. Prefix / Suffix (same day)

"前置和后置在英文的翻译不太通行，我见的比较多的写法的 prefix 和 suffix，如果其他语言文本跟着两个词有出入也顺手
改了". The container titles are **Prefix / Keep / Suffix** in English now (they were `Prepend / Append`
until 2026-10-13), and the Japanese pack follows the pair with **プレフィックス / 維持 / サフィックス** (it
said 先頭へ / 末尾へ). Chinese keeps the user's own 前置 / 维持 / 后置.

Everything that spelled the containers out was updated with the titles: `batch.orderHintMulti` ("Only
Prefix and Suffix change anything"), `batch.orderHintSingle` ("this becomes the Prefix / Keep / Suffix
containers") and `batch.commonHint` ("the add row inside Prefix or Suffix"), plus the same three in
Japanese. The tagger's own write modes keep `Append` / `Prepend`: those are verbs about inserting tags
into a caption, not the two places a caption can hold them.

Criteria: a new criterion pins the **pair** in all three packs (the titles *and* the three hints that
name them, with the four stale words `Prepend` / `Append` / 先頭へ / 末尾へ rejected anywhere they used to
appear). **Falsified twice**: the English title back to `Prepend`, and one hint back to "Only Prepend and
Append change anything" - each turns it red.

Three more things the same round turned up, all of them real:

- `paintAttr(node, "aria-label", key)` **never worked**: it assigns `node["aria-label"] = ...`, and
  `aria-label` is not a property of an element, so the accessible name never reached the DOM (the label
  was a useless JS field). The + rows now use `setAttribute` and are repainted by a registered painter;
  the same mistake was in the addition row's grip and is fixed there too. `placeholder` *is* a real
  property, which is why the input keeps a plain assignment;
- the element the + appends is built **after** construction, so a `paintAttr` on it would only be painted
  at the next `paintLabels()` - the box had no placeholder until then. It is set immediately, and
- `relabel()` **reuses** an existing addition row and only re-labels it: rebuilding lost a half-typed tag
  (and the focus) when the language changed. The `panel_locale_harness.mjs` criterion caught both of the
  last two by holding a reference to the input across the switch.

### 16. One spacing for the whole editor (same day)

"这个新增的按钮和列表之间的间距要和列表元素间的间距一致". The `+` carried a `margin-top: 3px` of its own while
the rows were spaced by their list's `gap: 4px`, and the additions list and the rows list had no spacing
between them at all.

Fixed by making the *container* the thing that spaces its blocks: `.batch-order` declares
`--batch-row-gap: 4px` once, and the container, the rows list and the additions list all use it. The `+`
row's margin is gone, so the block above it decides the distance - and the container's `flex` column plus
that one gap is what keeps the four distances equal by construction instead of by coincidence.

**Measured in real Chrome** over the panel's own DOM shape (two tag rows, one addition, the `+`): every
gap - row/row, addition/row, additions-list/rows-list and rows-list/`+` - is exactly **4px**. The same
measurement exposed a second real defect: `.batch-bucket-rows` has an explicit `min-height: 22px` (so an
empty container stays a drop target) and an explicit min-height is also the floor a flex item may be
*shrunk to*, so a two-row list was being squashed into that 22px bar with its rows overflowing it
invisibly (`rowsBox` was 22px tall while its rows ran to 60px). Both lists are `flex: 0 0 auto` now and
keep their content height, with the pinned row's own `overflow: auto` doing the scrolling.

Criteria: the container criterion pins the single `--batch-row-gap`, its three uses, the absence of the
old margin and the two `flex: 0 0 auto` lists; the probe measures `.batch-bucket-rows` as well, so the
boxes are in its report. **Falsified twice**: the `3px` margin back, and the lists left shrinkable - each
turns the criterion red.

**One more hole, found while chasing a report that turned out to be a stale page** ("没事了，我没刷新"):
the rows list kept a `min-height: 22px` floor "so an empty container is droppable", so in the state the
screenshot showed - one addition row, an **empty** common list, the `+` - the gap above the `+` was
4 + 22 + 4 = **30px**, not the row gap. An empty list is not a block now (`.batch-bucket-rows:empty
{ display: none }`); the container is the drop target (it owns the border, the highlight and the `+`), so
nothing is lost. Measured in Chrome: with the list empty the gaps are title→additions 4, additions→`+` 4
(the list is `display: none`), and with two rows in it 4 as well. Criterion added, **falsified** by giving
the empty list its floor back.

## Criteria and falsification

| Criterion | Location |
|---|---|
| The two blocks: listed order, the rest between them, skip/duplicate/both-blocks, addition outside the blocks, preview == write | `test/test_api_e2e.py::test_batch_edit_tags_orders_the_front_and_the_end` |
| End to end over two differently ordered captions | acceptance **A46** |
| Containers built and painted from the order state; single-image list is the caption's own order; the order reaches the request; the "nothing changed" check counts a moved row but not an empty order | `test/test_web_batch.py` |
| Drag and keyboard wiring (grip draggable, drop lands on a row, Home/End, `End` never targets the hidden container) | `test/test_web_batch.py` |
| Behaviour in a fake DOM: single container = the caption's order, drag reorders, the request carries the whole order; multi = everything starts in the middle, a move inside it changes nothing and is not sent, the front/end blocks reach the request, a row dropped back into the middle leaves the blocks, a cleared row is a deletion and not an order entry | `test/fixtures/batch_harness.mjs` (sections 15 and the pure-helper block), run by `test_web_batch.py` |
| The pure rules (`orderEdit`, `moveRowIn`) | `test/fixtures/batch_harness.mjs` |
| Contract | [p0-spec](../p0-spec.md) §4.5 ③, §5 A46 |

Falsifications (each really went red, then was restored):

- **Ordering after the additions** in `_apply_edit_tags` (i.e. moving `_apply_tag_order` below the
  `fresh + out` return) → the "prepended tag stays at the very front" assertion goes red, and A46's step 4
  with it. This is the ordering decision the spec paragraph spells out.
- Making `_apply_tag_order` return `first + last + middle` (i.e. sending the end block ahead of the middle)
  → the A46 middle-order assertion goes red on both files.
- Dropping the `order_first`/`order_last` presence from the "needs pairs, tags or an order" guard →
  order-only requests become 400 and the new backend criterion goes red.
- Removing the `bucket === "middle"` early return from `moveRowIn` → the harness' "a move inside the middle
  changed the order" goes red.
- Making `orderEdit` ignore its `single` argument (reassigning the parameter) → the single-image
  payload loses its whole order, and the harness goes red at *"a single image whose rows were not moved
  sends no order"* (the opposite error: it sends an order that says nothing new).
- `End` targeting `"back"` unconditionally → a row disappears into the hidden container in the
  single-image layout (the harness' `End` assertion goes red).
- Hiding the container host again when the scope has no common tags → the harness' entrance criterion
  goes red (see §5 above; this is the one the user's own report produced).
- Removing `paintRowTools()` from `relabel()` → no criterion fails today (the row tooltips are only read by
  a screen reader or a hover); that is recorded as a gap below rather than papered over with a criterion
  that reads the source.

## Known limits

- **No real-browser drag was performed.** The behaviours are asserted by firing synthetic `dragstart` /
  `dragover` / `drop` events with an `event.target` in the fake DOM, and HTML5 drag and drop cannot be
  driven by `tools/measure_web_layout.py`, so "a human can actually drag a row" rests on the standard
  event wiring, not on a measurement. **The layout, however, was measured** in real headless Chrome (the
  probe gained `.batch-order`, `.batch-bucket[data-bucket="middle"]` and `.batch-grip`): with the canned
  one-image scope the container host is visible at 325×81.5 px, the middle container is `display:none`
  (`visible: false` - so the single-image layout really closes it), the grip renders at 15.8×21 px, and
  the page reported no errors.
- Rows beyond `COMMON_ROWS` (200) are not listed and therefore not ordered: with one image in scope they
  keep their positions *after* the listed ones. The preview shows the result before anything is written.
- The middle container can only be left by dragging or with `Home`/`End`; ↑/↓ inside it does nothing by
  design, which a keyboard user has to learn from the section's hint.
- Adding a tag while rows are arranged places it at the very front / the very end (the `prepend`
  checkbox), not inside the arrangement: the added tag is not a row.

## Related

- Contract: [p0-spec](../p0-spec.md) §4.5 ③, §5 A46
- The same panel's other halves: [tag-autocomplete](tag-autocomplete.md), [batch-performance](batch-performance.md),
  [batch-backup-parity](batch-backup-parity.md), [caption-restore](caption-restore.md)
- Why the editor is a row list at all: `test_web_batch.py`'s "a delete must not shift the rows after it"
