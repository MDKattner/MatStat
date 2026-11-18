/**
 * Shared UI components for the MatStat SPA.
 *
 * Mirrors the original Qt-era reusable widgets:
 * - `el()`      — element factory helper.
 * - `showToast` — QMessageBox-style transient notifications.
 * - `timeInput` — mm:ss input with an optional "Mark" button (QTimeEdit).
 * - `configCombo` — editable input backed by a datalist (ConfigComboBox).
 * - `filterList` — single-select filterable list (videos, wrestlers, tie-ups).
 * - `checkList` — filterable multi-check list with optional count spins (MultiSelector).
 * - `videoPlayer` — <video> with play/pause, ±5s skip, position slider, time label
 *                   (VideoPreviewPanel + VideoPlayerControls).
 */

import { onWs } from "./ws.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Build a single-path stroke icon (24×24 grid) so UI glyphs match the type
 * weight instead of relying on platform emoji/entity rendering.
 */
function svgIcon(path, { size = 24, strokeWidth = 1.6 } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", String(strokeWidth));
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  const shape = document.createElementNS(SVG_NS, "path");
  shape.setAttribute("d", path);
  svg.appendChild(shape);
  return svg;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value);
  }
  for (const child of (children || []).flat()) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function showToast(message, kind = "info", timeoutMs = 4000) {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const toast = el("div", { class: `toast`, "data-kind": kind, text: message });
  root.appendChild(toast);
  setTimeout(() => toast.remove(), timeoutMs);
}

export function showModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.showModal();
}

/**
 * Styled confirm dialog. Resolves true on OK, false on Cancel/Esc.
 */
export function confirmModal({ title = "Confirm", message = "" } = {}) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "modal" });
    dialog.appendChild(el("h3", { text: title }));
    if (message) dialog.appendChild(el("p", { text: message }));
    const cancelBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Cancel" });
    const okBtn = el("button", { class: "btn", type: "button", text: "OK" });
    dialog.appendChild(el("div", { class: "row dialog-actions" }, [cancelBtn, okBtn]));
    const finish = (result) => {
      dialog.close();
      dialog.remove();
      resolve(result);
    };
    cancelBtn.addEventListener("click", () => finish(false));
    okBtn.addEventListener("click", () => finish(true));
    dialog.addEventListener("cancel", () => finish(false));
    document.body.appendChild(dialog);
    dialog.showModal();
    okBtn.focus();
  });
}

/**
 * Styled form dialog. `fields` is a list of:
 *   { name, label, type: "text"|"number"|"textarea"|"select", options?, value?, required?, validate? }
 * Resolves with `{ [name]: string }` on submit, or null on Cancel/Esc.
 * The OK button is disabled until required/validation checks pass.
 */
export function promptForm({ title = "", fields = [] } = {}) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "modal modal-form" });
    dialog.appendChild(el("h3", { text: title }));
    const form = el("form", { class: "modal-form" });
    const inputs = {};

    for (const field of fields) {
      const box = el("div", { class: "form-field" });
      if (field.label) box.appendChild(el("label", { class: "field-label", text: field.label }));
      let input;
      if (field.type === "textarea") {
        input = el("textarea", { class: "combo-input", rows: "3" });
      } else if (field.type === "select") {
        input = el("select", { class: "combo-input" });
        for (const option of field.options || []) {
          input.appendChild(el("option", { value: option, text: option }));
        }
      } else {
        input = el("input", { class: "combo-input", type: field.type || "text" });
      }
      if (field.value != null) input.value = field.value;
      inputs[field.name] = input;
      box.appendChild(input);
      if (field.validate || field.required) {
        const errorEl = el("p", { class: "form-error", text: "" });
        box.appendChild(errorEl);
      }
      form.appendChild(box);
    }

    const okBtn = el("button", { class: "btn", type: "submit", text: "OK" });
    const cancelBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Cancel" });
    form.appendChild(el("div", { class: "row dialog-actions" }, [cancelBtn, okBtn]));

    const checkValid = () => {
      let valid = true;
      for (const field of fields) {
        const input = inputs[field.name];
        const errorEl = input.parentElement.querySelector(".form-error");
        let message = "";
        if (field.required && !input.value.trim()) message = "This field is required.";
        else if (field.validate) message = field.validate(input.value) || "";
        if (errorEl) errorEl.textContent = message;
        if (message) valid = false;
      }
      okBtn.disabled = !valid;
    };

    for (const field of fields) {
      inputs[field.name].addEventListener("input", checkValid);
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (okBtn.disabled) return;
      const values = {};
      for (const field of fields) values[field.name] = inputs[field.name].value.trim();
      dialog.close();
      dialog.remove();
      resolve(values);
    });
    cancelBtn.addEventListener("click", () => {
      dialog.close();
      dialog.remove();
      resolve(null);
    });
    dialog.addEventListener("cancel", () => {
      dialog.remove();
      resolve(null);
    });

    dialog.appendChild(form);
    document.body.appendChild(dialog);
    dialog.showModal();
    const first = inputs[fields[0].name];
    if (first) first.focus();
    checkValid();
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Wait until a progressive HLS playlist lists at least one segment.
 * hls.js treats a fragment-less manifest as invalid, so we only hand it a
 * playlist once encoding has produced something playable (usually 1-4s in).
 */
async function waitForPlayable(playlistUrl, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const resp = await fetch(playlistUrl, { cache: "no-store" });
      if (resp.ok) {
        const text = await resp.text();
        if (/#EXTINF|#EXT-X-ENDLIST/.test(text)) return true;
      }
    } catch {
      // Network blip; keep polling.
    }
    if (Date.now() > deadline) return false;
    await sleep(400);
  }
}

export async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

// Shared /api/config fetch. Every tab needs the same document, so boot used to
// issue one identical request per tab; they now await this single promise.
// `invalidateConfig()` drops the cache so "configs-updated" listeners re-fetch.
let configPromise = null;

export function loadConfig() {
  if (configPromise) return configPromise;
  configPromise = fetchJson("/api/config")
    .then(({ ok, body }) => {
      if (!ok) throw new Error(body.detail || "Could not load config.");
      return body;
    })
    .catch((err) => {
      configPromise = null;
      throw err;
    });
  return configPromise;
}

export function invalidateConfig() {
  configPromise = null;
}

/**
 * Poll a background job until it reaches a terminal state.
 * Resolves with the finished job; throws on failure/cancellation.
 * `onProgress(pct)` and `onStatus(message)` fire on each update.
 *
 * Progress arrives over the shared WebSocket, with a slow (5s) HTTP poll as a
 * fallback for a missed event or a dropped socket, so a long transcode no
 * longer generates ~150 requests per minute.
 */
export async function pollJob(jobId, { onProgress = () => {}, onStatus = () => {} } = {}) {
  const FALLBACK_MS = 5000;
  return new Promise((resolve, reject) => {
    let settled = false;
    let fallbackTimer = null;
    let unsubscribeProgress = null;
    let unsubscribeFinished = null;

    const cleanup = () => {
      if (unsubscribeProgress) unsubscribeProgress();
      if (unsubscribeFinished) unsubscribeFinished();
      if (fallbackTimer) clearTimeout(fallbackTimer);
    };
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    };
    const apply = (body) => {
      if (typeof body.progress === "number") onProgress(body.progress);
      if (body.message) onStatus(body.message);
      if (body.status === "done") settle(resolve, body);
      else if (body.status === "failed" || body.status === "cancelled") {
        settle(reject, new Error(body.message || "Job failed"));
      }
    };
    const scheduleFallback = () => {
      if (settled) return;
      if (fallbackTimer) clearTimeout(fallbackTimer);
      fallbackTimer = setTimeout(check, FALLBACK_MS);
    };
    const check = async () => {
      if (settled) return;
      try {
        const { ok, body } = await fetchJson(`/api/jobs/${jobId}`);
        if (!ok) {
          settle(reject, new Error("Job lost"));
          return;
        }
        apply(body);
      } catch {
        // Transient network failure; the fallback timer retries.
      }
      if (!settled) scheduleFallback();
    };

    unsubscribeProgress = onWs("job_progress", (event) => {
      if (settled || event.id !== jobId) return;
      if (typeof event.progress === "number") onProgress(event.progress);
      if (event.message) onStatus(event.message);
      scheduleFallback();
    });
    unsubscribeFinished = onWs("job_finished", (event) => {
      if (event.id === jobId) check();
    });

    check();
  });
}

/**
 * Return a playable preview URL for a source video, starting a progressive
 * HLS transcode if needed. The server responds as soon as the playlist can be
 * served, so playback begins without waiting for the video to finish encoding
 * (no job polling). Throws if the preview can't be made.
 */
export async function ensurePreview(dirName, fileName) {
  const url = `/api/preview/${dirName}/${encodeURIComponent(fileName)}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("Preview unavailable");

  let body;
  try {
    body = await resp.json();
  } catch {
    throw new Error("Preview unavailable");
  }
  if (body.status !== "ready" || !body.playlist) throw new Error("Preview unavailable");
  return body.playlist;
}

export function formatClock(totalSeconds) {
  const secs = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

function parseClock(text) {
  const match = /^(\d+):([0-5]\d)$/.exec(text.trim());
  if (!match) return null;
  return parseInt(match[1], 10) * 60 + parseInt(match[2], 10);
}

const PLOTLY_VENDOR = "/static/vendor/plotly.min.js";
const PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js";
const HLS_VENDOR = "/static/vendor/hls.min.js";

/**
 * Inject a classic script tag and resolve once it has loaded.
 * Rejects on error or after a 15s timeout.
 */
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    const cleanup = () => {
      clearTimeout(timer);
      script.removeEventListener("load", onLoad);
      script.removeEventListener("error", onError);
    };
    const onLoad = () => { cleanup(); resolve(); };
    const onError = () => {
      cleanup();
      reject(new Error(`Failed to load script: ${src}`));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`Timed out loading script: ${src}`));
    }, 15000);
    script.addEventListener("load", onLoad);
    script.addEventListener("error", onError);
    document.head.appendChild(script);
  });
}

let plotlyPromise = null;

/**
 * Ensure the Plotly library is loaded, resolving with the Plotly object.
 *
 * Plotly is only needed by the PCA and Trends tabs, so it is no longer loaded
 * eagerly at boot. The first plot request loads the vendored copy and falls
 * back to the CDN if the file is missing.
 */
export function ensurePlotly() {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  if (!plotlyPromise) {
    plotlyPromise = (async () => {
      try {
        await loadScript(PLOTLY_VENDOR);
      } catch {
        await loadScript(PLOTLY_CDN);
      }
      if (!window.Plotly) throw new Error("Plotly failed to load.");
      return window.Plotly;
    })().catch((err) => {
      plotlyPromise = null;
      throw err;
    });
  }
  return plotlyPromise;
}

/**
 * Recolour a Plotly layout for the active theme. The backend builds charts with
 * the light template, so paper/plot backgrounds, fonts, grid lines, and the
 * trace colourway are overridden here from the CSS custom properties.
 */
export function stylePlotLayout(layout = {}) {
  const styles = getComputedStyle(document.documentElement);
  const cssVar = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
  const text = cssVar("--text", "#e0d9c7");
  const dim = cssVar("--text-dim", "#8fa3b8");
  const grid = cssVar("--border", "#2f516c");
  const accent = cssVar("--accent", "#5db2f8");
  const accentHover = cssVar("--accent-hover", "#86dbf5");
  const error = cssVar("--error", "#ea6847");
  const warning = cssVar("--warning", "#d943a8");
  const success = cssVar("--success", "#5db2f8");

  const themedAxis = (axis) => {
    if (!axis) return axis;
    const title = axis.title;
    const themedTitle = title && typeof title === "object"
      ? { ...title, font: { ...(title.font || {}), color: text } }
      : title;
    return {
      ...axis,
      gridcolor: grid,
      zerolinecolor: grid,
      linecolor: grid,
      tickcolor: grid,
      tickfont: { ...(axis.tickfont || {}), color: dim },
      title: themedTitle,
    };
  };

  return {
    ...layout,
    paper_bgcolor: "rgba(0, 0, 0, 0)",
    plot_bgcolor: "rgba(0, 0, 0, 0)",
    font: { ...(layout.font || {}), color: text },
    title: layout.title
      ? { ...layout.title, font: { ...((layout.title || {}).font || {}), color: text } }
      : layout.title,
    colorway: [accent, error, success, warning, accentHover, dim],
    xaxis: themedAxis(layout.xaxis),
    yaxis: themedAxis(layout.yaxis),
    legend: {
      ...(layout.legend || {}),
      font: { ...((layout.legend || {}).font || {}), color: text },
    },
  };
}

let hlsPromise = null;

/**
 * Ensure hls.js is loaded (Chromium cannot play .m3u8 natively).
 * Loaded on first preview so the ~500 KB library is not part of app boot.
 */
function ensureHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  if (!hlsPromise) {
    hlsPromise = loadScript(HLS_VENDOR)
      .then(() => {
        if (!window.Hls) throw new Error("hls.js failed to load.");
        return window.Hls;
      })
      .catch((err) => {
        hlsPromise = null;
        throw err;
      });
  }
  return hlsPromise;
}

/**
 * mm:ss time input with an optional "Mark" button.
 * `onMark` receives a callback so callers can set the value from the player.
 */
export function timeInput({ label = "", markable = true } = {}) {
  const input = el("input", {
    class: "time-input",
    type: "text",
    inputmode: "numeric",
    placeholder: "m:ss",
    value: "0:00",
  });

  const setFromSeconds = (seconds) => {
    input.value = formatClock(seconds);
  };

  const getSeconds = () => parseClock(input.value) ?? 0;

  const row = el("div", { class: "row time-row" });
  if (label) row.appendChild(el("label", { class: "field-label", text: label }));
  row.appendChild(input);

  const markBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Mark" });
  markBtn.disabled = !markable;
  row.appendChild(markBtn);

  return { node: row, input, markBtn, setFromSeconds, getSeconds };
}

/**
 * Editable combo backed by a list of options (autocomplete via datalist).
 * `onChange(item)` fires with the current text.
 */
export function configCombo({ label = "", items = [] } = {}) {
  const id = `datalist-${Math.random().toString(36).slice(2, 8)}`;
  const list = el("datalist", { id });

  function rebuild(newItems) {
    list.replaceChildren();
    for (const item of newItems) list.appendChild(el("option", { value: item }));
  }
  rebuild(items);

  const input = el("input", { class: "combo-input", type: "text", list: id, autocomplete: "off" });

  const row = el("div", { class: "row" });
  if (label) row.appendChild(el("label", { class: "field-label", text: label }));
  row.appendChild(input);
  row.appendChild(list);

  return {
    node: row,
    input,
    get selected() { return input.value.trim(); },
    setSelected(value) { input.value = value; },
    setItems(items) { rebuild(items); },
  };
}

/**
 * Filterable multi-check list. When `counts` is true each item gets a
 * 1-99 count spinner (MultiSelector semantics: `selected` returns the item
 * label repeated `count` times). Otherwise plain checkboxes (deduplicated).
 */
export function checkList({ label = "", items = [], counts = false, onChange = () => {}, teams = null } = {}) {
  const filter = el("input", { class: "filter-input", type: "text", placeholder: "Type to filter..." });
  const listEl = el("ul", { class: "check-list" });
  let entries = [];
  let teamMap = teams || {};
  let teamFilter = "";
  let assignedMembers = new Set();

  const UNASSIGNED = "__unassigned__";

  function refreshAssigned() {
    const assigned = new Set();
    for (const members of Object.values(teamMap)) {
      for (const item of members) assigned.add(item);
    }
    assignedMembers = assigned;
  }

  function inAnyTeam(item) {
    return assignedMembers.has(item);
  }

  function sameItems(current, next) {
    if (current.length !== next.length) return false;
    for (let i = 0; i < current.length; i++) {
      if (current[i] !== next[i]) return false;
    }
    return true;
  }

  refreshAssigned();

  function rebuild(newItems) {
    if (
      entries.length === newItems.length &&
      sameItems(entries.map((entry) => entry.item), newItems)
    ) {
      return;
    }
    entries = newItems.map((item) => {
      const cb = el("input", { type: "checkbox", value: item });
      const labelEl = el("label", { class: "check-label", text: item });
      labelEl.prepend(cb);
      let spin = null;
      if (counts) {
        spin = el("input", {
          type: "number", min: "1", max: "99", value: "1",
          class: "count-spin", disabled: "true",
        });
        cb.addEventListener("change", () => { spin.disabled = !cb.checked; });
      }
      cb.addEventListener("change", () => { onChange(item); });
      const li = el("li", { class: "check-item" }, [labelEl, spin]);
      return { item, cb, spin, li };
    });
    listEl.replaceChildren();
    for (const entry of entries) listEl.appendChild(entry.li);
    applyFilter();
  }

  function applyFilter() {
    const text = filter.value.toLowerCase();
    const members = teamFilter === UNASSIGNED
      ? null
      : teamFilter
        ? (teamMap[teamFilter] || [])
        : null;
    for (const entry of entries) {
      let visible = entry.item.toLowerCase().includes(text);
      if (visible && teamFilter) {
        visible = teamFilter === UNASSIGNED ? !inAnyTeam(entry.item) : members.includes(entry.item);
      }
      entry.li.hidden = !visible;
    }
  }

  function selected() {
    const result = [];
    for (const entry of entries) {
      if (entry.cb.checked) {
        const count = entry.spin ? parseInt(entry.spin.value, 10) || 1 : 1;
        for (let i = 0; i < count; i++) result.push(entry.item);
      }
    }
    return result;
  }

  function clear() {
    for (const entry of entries) {
      entry.cb.checked = false;
      if (entry.spin) {
        entry.spin.value = "1";
        entry.spin.disabled = true;
      }
    }
  }

  // Check an item and set its count spinner (used when loading a sequence
  // into an editor so duplicate entries keep their multiplicity).
  function setCount(item, count) {
    const entry = entries.find((e) => e.item === item);
    if (!entry || !entry.spin) return;
    entry.cb.checked = true;
    entry.spin.value = String(count);
    entry.spin.disabled = false;
  }

  filter.addEventListener("input", applyFilter);

  const box = el("div", { class: "check-group" });
  if (label) box.appendChild(el("label", { class: "field-label", text: label }));
  const teamSelect = el("select", { class: "combo-input team-select" });
  box.appendChild(teamSelect);
  box.appendChild(filter);
  box.appendChild(listEl);

  function renderTeamSelect() {
    teamSelect.replaceChildren();
    teamSelect.appendChild(el("option", { value: "", text: "All wrestlers" }));
    for (const name of Object.keys(teamMap)) {
      teamSelect.appendChild(el("option", { value: name, text: name }));
    }
    teamSelect.appendChild(el("option", { value: UNASSIGNED, text: "Unassigned" }));
    teamSelect.hidden = Object.keys(teamMap).length === 0;
    teamFilter = teamSelect.value;
    applyFilter();
  }

  teamSelect.addEventListener("change", () => {
    teamFilter = teamSelect.value;
    applyFilter();
  });

  rebuild(items);
  renderTeamSelect();

  return {
    node: box,
    selected,
    clear,
    setCount,
    setItems: rebuild,
    setTeams(data) { teamMap = data || {}; refreshAssigned(); renderTeamSelect(); },
  };
}

/**
 * Single-select filterable list (videos, wrestlers, tie-ups, filter items).
 * One item is highlighted `.active` at a time. `onChange(item)` fires when the
 * user picks an item.
 */
export function filterList({ label = "", items = [], placeholder = "Type to filter...", onChange = () => {}, teams = null } = {}) {
  const filter = el("input", { class: "filter-input", type: "text", placeholder });
  const listEl = el("ul", { class: "check-list" });
  let entries = [];
  let itemValues = [];
  let selectedValue = "";
  let disabled = false;
  let teamMap = teams || {};
  let teamFilter = "";
  let assignedMembers = new Set();

  const UNASSIGNED = "__unassigned__";

  function refreshAssigned() {
    const assigned = new Set();
    for (const members of Object.values(teamMap)) {
      for (const item of members) assigned.add(item);
    }
    assignedMembers = assigned;
  }

  function inAnyTeam(item) {
    return assignedMembers.has(item);
  }

  function sameItems(current, next) {
    if (current.length !== next.length) return false;
    for (let i = 0; i < current.length; i++) {
      if (current[i] !== next[i]) return false;
    }
    return true;
  }

  refreshAssigned();

  function updateActive() {
    for (const entry of entries) {
      entry.btn.classList.toggle("active", entry.item === selectedValue);
    }
  }

  function select(item) {
    if (disabled) return;
    selectedValue = item;
    updateActive();
    onChange(item);
  }

  function rebuild(newItems) {
    if (sameItems(itemValues, newItems)) return;
    itemValues = [...newItems];
    entries = newItems.map((item) => {
      const attrs = {
        class: "video-item" + (item === selectedValue ? " active" : ""),
        type: "button",
        text: item,
        onclick: () => select(item),
      };
      if (disabled) attrs.disabled = "";
      const btn = el("button", attrs);
      return { item, btn, li: el("li", {}, [btn]) };
    });
    listEl.replaceChildren();
    for (const entry of entries) listEl.appendChild(entry.li);
    applyFilter();
  }

  function setDisabled(value) {
    disabled = value;
    filter.disabled = value;
    for (const entry of entries) entry.btn.disabled = value;
  }

  function applyFilter() {
    const text = filter.value.toLowerCase();
    const members = teamFilter === UNASSIGNED
      ? null
      : teamFilter
        ? (teamMap[teamFilter] || [])
        : null;
    for (const entry of entries) {
      let visible = entry.item.toLowerCase().includes(text);
      if (visible && teamFilter) {
        visible = teamFilter === UNASSIGNED ? !inAnyTeam(entry.item) : members.includes(entry.item);
      }
      entry.li.hidden = !visible;
    }
  }

  filter.addEventListener("input", applyFilter);

  const box = el("div", { class: "filter-group" });
  if (label) box.appendChild(el("label", { class: "field-label", text: label }));
  const teamSelect = el("select", { class: "combo-input team-select" });
  box.appendChild(teamSelect);
  box.appendChild(filter);
  box.appendChild(listEl);

  function renderTeamSelect() {
    teamSelect.replaceChildren();
    teamSelect.appendChild(el("option", { value: "", text: "All wrestlers" }));
    for (const name of Object.keys(teamMap)) {
      teamSelect.appendChild(el("option", { value: name, text: name }));
    }
    teamSelect.appendChild(el("option", { value: UNASSIGNED, text: "Unassigned" }));
    teamSelect.hidden = Object.keys(teamMap).length === 0;
    teamFilter = teamSelect.value;
    applyFilter();
  }

  teamSelect.addEventListener("change", () => {
    teamFilter = teamSelect.value;
    applyFilter();
  });

  rebuild(items);
  renderTeamSelect();

  return {
    node: box,
    get selected() { return selectedValue; },
    setSelected(value) { selectedValue = value; updateActive(); },
    clear() { selectedValue = ""; updateActive(); },
    setItems: rebuild,
    setDisabled,
    setTeams(data) { teamMap = data || {}; refreshAssigned(); renderTeamSelect(); },
  };
}

/**
 * Reusable video player with controls, mirroring VideoPreviewPanel.
 * `onPosition` fires with the current time in ms (used by the tag-film Mark buttons).
 */
export function videoPlayer({ onPosition = () => {} } = {}) {
  const video = el("video", { class: "video", preload: "metadata" });
  const playBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Play" });
  const backBtn = el("button", { class: "btn btn-ghost", type: "button", text: "-5s" });
  const fwdBtn = el("button", { class: "btn btn-ghost", type: "button", text: "+5s" });
  const slider = el("input", { type: "range", min: "0", max: "0", value: "0", class: "pos-slider" });
  const timeLabel = el("span", { class: "time-label", text: "0:00 / 0:00" });

  // Placeholder painted over the (always black) frame: an empty player used to
  // be a large void with only the control strip at the bottom.
  const overlayLabel = el("span", { text: "Select a video to preview" });
  const overlay = el("div", { class: "video-overlay", "data-state": "empty" }, [
    svgIcon("M3 5h18v14H3zM10 9.5l5 2.5-5 2.5z"),
    overlayLabel,
  ]);

  /**
   * Tell the overlay what the player is doing. `ready` hides it so the video
   * shows through; every other state keeps a short explanation in the frame.
   */
  const setOverlay = (state, text = "") => {
    overlay.dataset.state = state;
    overlay.hidden = state === "ready";
    if (text) overlayLabel.textContent = text;
  };

  let duration = 0;
  let hls = null;
  let pendingStartTime = 0;
  let loadToken = 0;

  const seekIfReady = () => {
    if (pendingStartTime <= 0) return;
    try {
      video.currentTime = pendingStartTime;
      pendingStartTime = 0;
    } catch {
      // Not buffered yet (HLS segments still arriving); retry on the next event.
    }
  };

  const destroyHls = () => {
    if (hls) {
      hls.destroy();
      hls = null;
    }
  };

  const updateTime = () => {
    timeLabel.textContent = `${formatClock(video.currentTime)} / ${formatClock(duration)}`;
    if (document.activeElement !== slider) slider.value = String(video.currentTime * 1000);
    onPosition(Math.floor(video.currentTime * 1000));
  };

  const mediaDuration = () => {
    let total = Number.isFinite(video.duration) ? video.duration : 0;
    // Progressive/live HLS may report Infinity or a lagging total until the
    // playlist is complete; the seekable range is the best known length then.
    const seekable = video.seekable;
    if (seekable && seekable.length > 0) {
      total = Math.max(total, seekable.end(seekable.length - 1));
    }
    return total;
  };

  const syncDuration = () => {
    const next = mediaDuration();
    if (next === duration) return;
    duration = next;
    slider.max = String(duration * 1000);
    updateTime();
  };

  playBtn.addEventListener("click", () => {
    if (video.paused) video.play(); else video.pause();
  });
  backBtn.addEventListener("click", () => { video.currentTime = Math.max(0, video.currentTime - 5); });
  fwdBtn.addEventListener("click", () => { video.currentTime = Math.min(duration, video.currentTime + 5); });
  slider.addEventListener("input", () => { video.currentTime = parseInt(slider.value, 10) / 1000; });

  // Duration is not final when metadata first loads: HLS appends segments (and
  // only then a complete duration) over time, so keep the total in sync.
  video.addEventListener("loadedmetadata", () => {
    syncDuration();
    seekIfReady();
    setOverlay("ready");
  });
  video.addEventListener("canplay", () => setOverlay("ready"));
  video.addEventListener("durationchange", syncDuration);
  video.addEventListener("progress", syncDuration);
  video.addEventListener("seeked", syncDuration);
  video.addEventListener("timeupdate", updateTime);
  video.addEventListener("play", () => { playBtn.textContent = "Pause"; });
  video.addEventListener("pause", () => { playBtn.textContent = "Play"; });
  video.addEventListener("error", () => {
    setOverlay("error", "Preview failed to load.");
  });

  const controls = el("div", { class: "video-controls" }, [playBtn, backBtn, fwdBtn, slider, timeLabel]);
  const box = el("div", { class: "video-panel" }, [video, overlay, controls]);

  return {
    node: box,
    load(sourceUrl, startTime = 0) {
      destroyHls();
      video.pause();
      duration = 0;
      slider.max = "0";
      slider.value = "0";
      timeLabel.textContent = "0:00 / 0:00";
      pendingStartTime = startTime;
      setOverlay("loading", "Loading preview\u2026");
      if (!/\.m3u8($|\?)/.test(sourceUrl)) {
        video.src = sourceUrl;
        video.load();
        return;
      }
      // Progressive HLS: the playlist may be an empty stub for a moment, so
      // wait until a segment exists before starting hls.js/native playback.
      const token = ++loadToken;
      (async () => {
        const playable = await waitForPlayable(sourceUrl);
        if (token !== loadToken) return;
        if (!playable) {
          setOverlay("error", "Preview stream failed to start.");
          showToast("Preview stream failed to start.", "error");
          return;
        }
        try {
          await ensureHls();
        } catch {
          // Fall back to native playback (Safari) below.
        }
        if (token !== loadToken) return;
        if (window.Hls && Hls.isSupported()) {
          const config = { synchronizeToLiveEdge: false };
          if (startTime > 0) config.startPosition = startTime;
          hls = new Hls(config);
          hls.loadSource(sourceUrl);
          hls.attachMedia(video);
          hls.on(Hls.Events.MANIFEST_PARSED, () => seekIfReady());
          hls.on(Hls.Events.ERROR, (_event, data) => {
            if (data && data.fatal) {
              console.error("HLS error", data);
              setOverlay("error", "Video stream failed to load.");
              showToast("Video stream failed to load.", "error");
              destroyHls();
            }
          });
        } else {
          video.src = sourceUrl;
          video.load();
        }
      })();
    },
    play() { video.play(); },
    togglePlay() { if (video.paused) video.play(); else video.pause(); },
  };
}

/**
 * Unified query panel used by both Search and Combine Clips tabs.
 *
 * Provides a shared filter UI (wrestlers, attack mode, tie, team/opp moves,
 * adjusted net points range) and renders only the action buttons whose
 * callbacks are supplied.
 *
 * @param {Object} opts
 * @param {Function} [opts.onSearch]   - Search button callback (receives query).
 * @param {Function} [opts.onExport]   - Export CSV button callback (receives query).
 * @param {Function} [opts.onFindClips] - Find Clips button callback (receives query).
 * @param {Function} [opts.onGenerateReel] - Generate Reel button callback (receives query).
 */
export function queryPanel({
  onSearch = null,
  onExport = null,
  onFindClips = null,
  onGenerateReel = null,
} = {}) {
  // ---------- Wrestlers ----------

  const wrestlers = checkList({
    label: "Wrestlers:",
    items: [],
    onChange: () => {},
  });

  // ---------- Attack mode ----------

  const attackMode = el("select", { class: "combo-input" }, [
    el("option", { value: "All", text: "All" }),
    el("option", { value: "Attacking", text: "Attacking" }),
    el("option", { value: "Defending", text: "Defending" }),
  ]);

  // ---------- Filter lists ----------

  const tie = filterList({ label: "Tie Up:", items: [] });
  const teamMove = filterList({ label: "Team Move:", items: [] });
  const oppMove = filterList({ label: "Opponent Move:", items: [] });

  // ---------- Points range ----------

  const minPts = el("input", {
    class: "combo-input pts-input",
    type: "number",
    placeholder: "No min",
  });
  const maxPts = el("input", {
    class: "combo-input pts-input",
    type: "number",
    placeholder: "No max",
  });

  // ---------- Status ----------

  const statusLabel = el("p", { class: "status-label", text: "Select filters and run a query." });

  // ---------- Action buttons ----------

  function pointsValue(input) {
    const raw = input.value.trim();
    if (raw === "") return null;
    const n = parseInt(raw, 10);
    return Number.isNaN(n) ? null : n;
  }

  function buildQuery() {
    return {
      wrestlers: wrestlers.selected(),
      attack_mode: attackMode.value,
      tie_up: tie.selected,
      team_move: teamMove.selected,
      opp_move: oppMove.selected,
      min_points: pointsValue(minPts),
      max_points: pointsValue(maxPts),
    };
  }

  const actionRow = el("div", { class: "row" });
  let searchBtn = null;
  let exportBtn = null;
  let findClipsBtn = null;
  let generateReelBtn = null;

  if (onSearch) {
    searchBtn = el("button", { class: "btn", type: "button", text: "Search" });
    searchBtn.addEventListener("click", () => onSearch(buildQuery()));
    actionRow.appendChild(searchBtn);
  }
  if (onExport) {
    exportBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Export CSV" });
    exportBtn.addEventListener("click", () => onExport(buildQuery()));
    actionRow.appendChild(exportBtn);
  }
  if (onFindClips) {
    findClipsBtn = el("button", { class: "btn", type: "button", text: "Find Clips" });
    findClipsBtn.addEventListener("click", () => onFindClips(buildQuery()));
    actionRow.appendChild(findClipsBtn);
  }
  if (onGenerateReel) {
    generateReelBtn = el("button", { class: "btn", type: "button", text: "Generate Reel" });
    generateReelBtn.addEventListener("click", () => onGenerateReel(buildQuery()));
    actionRow.appendChild(generateReelBtn);
  }

  // ---------- Layout ----------

  const filters = el("div", { class: "query-grid" }, [
    wrestlers.node,
    tie.node,
    teamMove.node,
    oppMove.node,
  ]);

  const fieldset = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Filters" }),
    el("div", { class: "row filter-row" }, [
      el("label", { class: "field-label", text: "Attack/Defense:" }, [attackMode]),
    ]),
    filters,
    el("div", { class: "row filter-row" }, [
      el("label", { class: "field-label", text: "Min Adj. Pts:" }, [minPts]),
      el("label", { class: "field-label", text: "Max Adj. Pts:" }, [maxPts]),
    ]),
    actionRow,
    statusLabel,
  ]);

  return {
    node: fieldset,
    query: buildQuery,
    setStatus(text) { statusLabel.textContent = text; },
    setButtonsEnabled(enabled) {
      if (searchBtn) searchBtn.disabled = !enabled;
      if (exportBtn) exportBtn.disabled = !enabled;
      if (findClipsBtn) findClipsBtn.disabled = !enabled;
      if (generateReelBtn) generateReelBtn.disabled = !enabled;
    },
    setItems({ wrestlers: w, ties, moves, teams }) {
      if (w) wrestlers.setItems(w);
      wrestlers.setTeams(teams || {});
      if (ties) tie.setItems(ties);
      if (moves) {
        teamMove.setItems(moves);
        oppMove.setItems(moves);
      }
    },
    wrestlers,
  };
}
