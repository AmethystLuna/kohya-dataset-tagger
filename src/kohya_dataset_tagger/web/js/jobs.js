/**
 * The job shelf: one place that knows what this app is currently doing.
 *
 * Four panels start long jobs - a WD14 run, a batch caption write, a cache rebuild, an image export - and
 * each of them painted its progress **inside its own tab**. Switch tabs and the running job disappeared:
 * the app was rewriting captions with nothing on screen saying so, and the only way to stop it was to
 * remember which tab had started it. Two jobs can run at once (nothing serialises them), so "which tab is
 * busy" was not always a single answer either.
 *
 * A panel does **not** hand its state over: it registers the job it already owns and pushes the same
 * numbers it paints locally. One source of truth per job (the panel), one place that shows them all - a
 * shelf that kept its own copy of the progress would be a second thing to keep in sync, and it would be
 * the wrong one.
 *
 * A row is a button (it goes to the tab that owns the job, so "where do I look" has an answer) plus the
 * progress text plus a cancel that calls the panel's own cancel path.
 */
let nextId = 0;

export function createJobShelf(options) {
  const config = options || {};
  const host = config.host;
  const onOpen = config.onOpen || (() => {});
  //: Told when the *set* of running jobs changes (a job starting or ending), never on a progress
  //: tick: the command palette repaints from this, and rebuilding a list under the reader's cursor
  //: once a second is not a repaint, it is a moving target.
  const onChange = typeof config.onChange === "function" ? config.onChange : null;
  const rows = new Map();

  function reportChange() {
    if (onChange) onChange();
  }

  function paintRow(row) {
    const box = document.createElement("div");
    box.className = "job-row";
    box.dataset.job = row.id;

    const label = document.createElement("button");
    label.type = "button";
    label.className = "job-label";
    label.textContent = row.label;
    label.title = row.label;
    label.addEventListener("click", () => onOpen(row.kind));

    const text = document.createElement("span");
    text.className = "hint job-progress";
    const total = row.total > 0 ? row.total : "?";
    text.textContent = row.current
      ? row.done + "/" + total + " · " + row.current
      : row.done + "/" + total;

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn-mini";
    cancel.textContent = row.cancelLabel;
    // A second click would fire a second request; the panel's own button is disabled the same way.
    cancel.disabled = row.cancelRequested;
    cancel.addEventListener("click", () => {
      // Repainting replaces this button, so the disabled state is what stops a real second click; the
      // guard is what stops a second call arriving by any other route (an idempotent cancel).
      if (row.cancelRequested) return;
      row.cancelRequested = true;
      paint();
      row.cancel();
    });

    box.append(label, text, cancel);
    return box;
  }

  function paint() {
    host.replaceChildren();
    for (const row of rows.values()) host.append(paintRow(row));
    host.hidden = rows.size === 0;
  }

  return {
    /**
     * Register a job that is starting. `spec`: {kind, label, cancelLabel, total, cancel}.
     * The handle pushes progress and closes the row - it never owns the job itself.
     */
    start(spec) {
      const source = spec || {};
      const id = "job-" + (nextId += 1);
      const row = {
        id,
        kind: source.kind || "",
        label: source.label || "",
        cancelLabel: source.cancelLabel || "",
        total: Number(source.total) || 0,
        done: 0,
        current: "",
        cancelRequested: false,
        cancel: typeof source.cancel === "function" ? source.cancel : (() => {}),
      };
      rows.set(id, row);
      paint();
      reportChange();
      return {
        id,
        update(next) {
          const progress = next || {};
          if (Number.isFinite(Number(progress.done))) row.done = Number(progress.done);
          if (Number.isFinite(Number(progress.total)) && Number(progress.total) > 0) {
            row.total = Number(progress.total);
          }
          if (progress.current !== undefined) row.current = String(progress.current || "");
          paint();
        },
        finish() {
          rows.delete(id);
          paint();
          reportChange();
        },
      };
    },
    /** What is on the shelf right now (a copy: the caller does not get the live rows). */
    active() {
      return Array.from(rows.values(), (row) => ({
        id: row.id,
        kind: row.kind,
        label: row.label,
        done: row.done,
        total: row.total,
        current: row.current,
        cancelRequested: row.cancelRequested,
      }));
    },
  };
}
