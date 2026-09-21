---
name: picker-manual-input-and-mkdir
description: 2026-09-19 directory picker gains manual input and create-directory (§4.12 adds POST /api/fs/pick/mkdir)
metadata:
  type: topic
---

# The picker's manual input and create-directory (2026-09-19)

The user's words: "the export directory picker is not complete enough — I can neither type a path nor create a folder".

The previous round ([ui-picker-and-zoom](ui-picker-and-zoom.md)) made it "clickable", but the picker
**could only be clicked with a mouse**: the current position was a line of **read-only text**, the path could
not be edited in a box, and worse, the directory it selected had to **already exist**, while "create a new
dataset directory" is the most common starting point. The scale/export panel's "export directory" row reuses
this same picker, so this feedback lands directly on the export flow.

## What changed

### 1. Manual input (frontend only)

- The picker's "current position" changes from read-only text to an **editable input**: type an absolute path and press Enter to go there
  (still `GET /api/fs/pick`, no new read endpoint); a "Go" button next to it does the same thing;
- "Use this directory" also honors the value in the box — **typing something and not pressing Enter must not be silently ignored**;
  but a failed navigation never falls back to the old path to close the deal (treating a wrongly chosen directory as the right one is more dangerous than an error);
- Enter in the input belongs to the input and **does not also close the dialog** (check `event.target` first in `onKeyDown`).

### 2. Create directory (one write endpoint)

`POST /api/fs/pick/mkdir  {parent, name} → 200 {path, created}`

- **Sibling of** `GET /api/fs/pick` (`api/pick.py`): the allowlist does not apply (a new dataset directory is necessarily still outside it),
  but the **loopback gate** does. It is the only **write** step in this module, so it shares the picker's one gate —
  gating the picker while leaving this alone would be leaving a back door;
- `name` allows only a **single path component**: empty, `.`, `..`, containing `/ \ < > : " | ? *`, leading/trailing whitespace, or ending with a dot
  are all 400 `bad_name`. **Creating a directory must not become a write path that escapes the parent**;
- It creates **one level only** (not `mkdir -p`); if a **directory** of the same name exists it is idempotent `created:false`
  (the user wants "go to that directory", so it should not error if it is already there), while a same-named **file** is 409 `already_exists` and is never overwritten;
- A missing `parent` is 404 `not_found` / not a directory is 400 `not_a_directory` / unwritable is 403 `unwritable`.

### 3. Frontend controls

An inline directory-name input + a "Create directory" button, **not `window.prompt`**: prompt is untranslatable,
cannot be tested headlessly, and also blocks the modal. On success it clears the name and **enters the new directory**,
so the user can then click "Use this directory". The modal is still **not in the relabel list** (a deliberate boundary in
[i18n](../i18n.md): it covers the top bar, so the user cannot reach the language switch), and the new copy is fetched at
construction time with `t()` exactly like the old copy.

## Criteria and falsification

| Criterion | Location |
|---|---|
| Endpoint behavior: writes to disk, one level, idempotent, 409, bad name, missing parent | `test/test_pick_api.py`, acceptance **A33d** |
| The loopback gate grows from five to **six** (including mkdir, and a rejected request creates no directory) | `test/test_pick_api.py`, acceptance **A33b/A33c** |
| Frontend wiring: path box, create-directory control, `api.pickMkdir(` | `test/test_web_picker.py` |
| Headless behavior: path box navigates on Enter, Enter does not close the box, create-directory calls the endpoint and enters it, an empty name sends no request | `test/fixtures/picker_harness.mjs` |

Falsification (all really went red, then were restored):

- Change `_bad_name_reason`'s return to always `None` → A33d's `../escaped` and the outer `not (tmp_path / "escaped").exists()`
  go red (showing that it really guards "must not escape the parent", not just the status code);
- Move mkdir from after `path_config_denied()` to before it → A33b's 403 and "no directory was created" go red;
- Remove the `api.pickMkdir(` call from `createDir` in `picker.js` → the headless harness exits 1 at "create-directory did not call the endpoint".

## Related

- Contract: [p0-spec](../p0-spec.md) §4 routing table, §4.12, §5's A33b/A33c/A33d
- Previous picker round: [ui-picker-and-zoom](ui-picker-and-zoom.md)
