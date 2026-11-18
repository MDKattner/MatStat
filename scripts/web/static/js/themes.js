/**
 * User-side appearance themes.
 *
 * A theme is a set of CSS custom properties in css/themes.css, selected with
 * `data-theme` on <html>. The choice is per browser (localStorage), never sent
 * to the server and never part of the shared server config.
 *
 * Until the user picks one, the theme follows the operating system: a High
 * Contrast variant when the desktop asks for more contrast, otherwise
 * Catppuccin Mocha for a dark desktop and Catppuccin Latte for a light one.
 */

export const THEMES = Object.freeze([
  { id: "catppuccin-mocha", label: "Catppuccin Mocha (default for dark mode)" },
  { id: "catppuccin-latte", label: "Catppuccin Latte (default for light mode)" },
  { id: "high-contrast", label: "High Contrast (dark)" },
  { id: "high-contrast-light", label: "High Contrast (light)" },
  { id: "neofusion", label: "Neofusion" },
  { id: "dracula", label: "Dracula" },
  { id: "nord", label: "Nord" },
  { id: "gruvbox-dark", label: "Gruvbox Dark" },
  { id: "tokyo-night", label: "Tokyo Night" },
  { id: "one-dark", label: "One Dark" },
  { id: "solarized-dark", label: "Solarized Dark" },
  { id: "github-dark", label: "GitHub Dark" },
]);

const STORAGE_KEY = "matstat-theme";
const DARK_DEFAULT = "catppuccin-mocha";
const LIGHT_DEFAULT = "catppuccin-latte";
const CONTRAST_DARK = "high-contrast";
const CONTRAST_LIGHT = "high-contrast-light";
const LIGHT_QUERY = "(prefers-color-scheme: light)";
const CONTRAST_QUERY = "(prefers-contrast: more)";

/** True when the operating system asks for a light colour scheme. */
function prefersLight() {
  return Boolean(window.matchMedia && window.matchMedia(LIGHT_QUERY).matches);
}

/** True when the operating system asks for more contrast. */
function prefersMoreContrast() {
  return Boolean(
    window.matchMedia && window.matchMedia(CONTRAST_QUERY).matches
  );
}

/** The theme used when this browser has no saved choice. */
export function defaultTheme() {
  const light = prefersLight();
  if (prefersMoreContrast()) {
    return light ? CONTRAST_LIGHT : CONTRAST_DARK;
  }
  return light ? LIGHT_DEFAULT : DARK_DEFAULT;
}

/** Return a known theme id, falling back to the OS-appropriate default. */
export function normalizeTheme(id) {
  return THEMES.some((theme) => theme.id === id) ? id : defaultTheme();
}

/** True when the user has explicitly chosen a theme in this browser. */
function hasSavedTheme() {
  try {
    return Boolean(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return false;
  }
}

/** The theme in effect: the saved choice, else the OS default. */
export function currentTheme() {
  try {
    return normalizeTheme(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return defaultTheme();
  }
}

/**
 * Apply and persist a theme. Safe to call with an unknown id: it falls back to
 * the OS default and tells listeners which theme actually won.
 */
export function applyTheme(id) {
  const theme = normalizeTheme(id);
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* Storage can be blocked; the theme still applies for this page. */
  }
  window.dispatchEvent(new CustomEvent("theme-changed", { detail: theme }));
  return theme;
}

/**
 * Apply the saved theme and keep tabs in sync when another tab changes it.
 * theme-init.js already set the attribute before paint; this validates it and
 * installs the cross-tab and system-preference listeners.
 */
export function initTheme() {
  document.documentElement.dataset.theme = currentTheme();

  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const theme = normalizeTheme(event.newValue);
    document.documentElement.dataset.theme = theme;
    window.dispatchEvent(new CustomEvent("theme-changed", { detail: theme }));
  });

  // A browser with no saved choice follows the OS when it flips dark/light or
  // toggles "increase contrast".
  const followSystem = () => {
    if (hasSavedTheme()) return;
    const theme = defaultTheme();
    document.documentElement.dataset.theme = theme;
    window.dispatchEvent(new CustomEvent("theme-changed", { detail: theme }));
  };
  if (window.matchMedia) {
    window.matchMedia(LIGHT_QUERY).addEventListener("change", followSystem);
    window.matchMedia(CONTRAST_QUERY).addEventListener("change", followSystem);
  }
}
