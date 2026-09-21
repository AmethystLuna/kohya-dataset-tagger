/**
 * In-app confirmation dialog, replacing window.confirm().
 *
 * Why not the browser dialog: its buttons come from the browser UI language (a
 * Chinese interface gets "OK / Cancel"), the body cannot name the action that is
 * about to happen, and a destructive confirmation looks exactly like a harmless
 * one. Every call site here passes the label of the action button, so the button
 * says "Rebuild" or "Write captions" instead of "OK".
 *
 * The dialog is built per call and thrown away afterwards, so it always reads the
 * current locale: there is nothing to register with the relabel contract.
 */
import { t } from "./strings.js";

let nextId = 0;

export function createConfirm() {
  let pending = null;

  function close(result) {
    if (!pending) return;
    const { backdrop, onKeyDown, restoreFocusTo, resolve } = pending;
    pending = null;
    document.removeEventListener("keydown", onKeyDown);
    backdrop.remove();
    // Hand the keyboard back where it came from: a dialog that swallows focus
    // leaves the user at the top of the page.
    if (restoreFocusTo && typeof restoreFocusTo.focus === "function") restoreFocusTo.focus();
    resolve(result);
  }

  /**
   * Ask the user to confirm something. Resolves true only when they pressed the
   * action button: Escape, the cancel button and a click on the backdrop all mean
   * no, because a confirmation that defaults to yes is not a confirmation.
   */
  function ask(spec) {
    const options = spec || {};
    // One at a time: a second question supersedes the first (which is a no).
    if (pending) close(false);

    const backdrop = document.createElement("div");
    backdrop.className = "dialog-backdrop confirm-backdrop";
    const dialog = document.createElement("div");
    dialog.className = "dialog confirm-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const titleId = "confirm-title-" + (nextId += 1);
    dialog.setAttribute("aria-labelledby", titleId);
    dialog.setAttribute("aria-describedby", titleId + "-body");

    const head = document.createElement("div");
    head.className = "dialog-head";
    const title = document.createElement("h2");
    title.className = "panel-title";
    title.id = titleId;
    // The action names the dialog: a generic "Confirm" title tells a screen
    // reader nothing about what is about to happen.
    title.textContent = options.title || options.confirmLabel || t("confirm.title");
    head.append(title);

    const body = document.createElement("p");
    body.className = "hint";
    body.id = titleId + "-body";
    body.textContent = String(options.body === undefined ? "" : options.body);

    const actions = document.createElement("div");
    actions.className = "row confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn";
    cancel.textContent = options.cancelLabel || t("common.cancel");
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = options.danger ? "btn btn-danger" : "btn btn-primary";
    confirm.textContent = options.confirmLabel || t("common.confirm");
    actions.append(cancel, confirm);

    dialog.append(head, body, actions);
    backdrop.append(dialog);
    document.body.append(backdrop);

    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
      }
    };
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close(false);
    });
    cancel.addEventListener("click", () => close(false));
    confirm.addEventListener("click", () => close(true));
    document.addEventListener("keydown", onKeyDown);

    const restoreFocusTo = document.activeElement || null;
    // Destructive actions focus Cancel: an accidental Enter must not delete
    // something. Everything else focuses the action it is asking about.
    (options.danger ? cancel : confirm).focus();

    return new Promise((resolve) => {
      pending = { backdrop, onKeyDown, restoreFocusTo, resolve };
    });
  }

  return { ask };
}
