/*
 * Theme boot - the one thing that has to happen before the first paint.
 *
 * This is a CLASSIC script on purpose, NOT a module. Measured in headless Chrome over this page's
 * real module graph (27 modules, a paint-timing probe): a deferred module - which is what
 * type="module" means - evaluated at 275 ms cold / 50 ms warm, while first paint happened at
 * 116 ms cold / 36 ms warm. A synchronous <head> script ran at 87 ms cold / 13 ms warm, always
 * before the paint. So a module cannot apply a stored theme before the page is painted, and a user
 * who chose dark would get a light flash (about 160 ms cold, one frame warm) on every load.
 *
 * It cannot import js/theme.js - importing is what would make it a module, which is the thing that
 * is too late. So the storage key is written out a second time here, and
 * test/test_web_theme.py::test_the_boot_script_and_the_module_agree_on_the_storage_key is what keeps
 * the two copies from drifting. Everything else about the theme (the switch, the writing, the
 * relabelling) lives in that module.
 */
(function () {
  var stored = null;
  try {
    stored = window.localStorage.getItem("kdt.theme");
  } catch (err) {
    stored = null; // a storage that throws (private mode / disabled) is not an error: it is "no choice"
  }
  // Anything that is not the stored "dark" is light: the default is light for everyone, on every OS,
  // and there is deliberately no prefers-color-scheme branch anywhere in this app.
  document.documentElement.setAttribute("data-theme", stored === "dark" ? "dark" : "light");
})();
