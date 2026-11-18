/*
 * Apply the saved theme before the stylesheet paints so a reload does not
 * flash the default palette. This is a classic (non-module) script so it runs
 * synchronously in <head>, ahead of the first render.
 *
 * With no saved choice the theme follows the operating system: a High Contrast
 * variant when it asks for more contrast, otherwise Catppuccin Mocha on a dark
 * desktop and Catppuccin Latte on a light one.
 */
(function () {
  var theme;
  try {
    theme = window.localStorage.getItem("matstat-theme");
  } catch (error) {
    /* Storage can be blocked (private mode / strict settings); use the default. */
  }
  if (!theme) {
    var light =
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches;
    var moreContrast =
      window.matchMedia &&
      window.matchMedia("(prefers-contrast: more)").matches;
    if (moreContrast) {
      theme = light ? "high-contrast-light" : "high-contrast";
    } else {
      theme = light ? "catppuccin-latte" : "catppuccin-mocha";
    }
  }
  document.documentElement.dataset.theme = theme;
})();
