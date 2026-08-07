/**
 * Shared UI components for the MatStat SPA.
 *
 * Mirrors the reusable Qt widgets from scripts/qt_app/widgets.py:
 * - `el()`      — element factory helper.
 * - `showToast` — QMessageBox-style transient notifications.
 * - `timeInput` — mm:ss input with an optional "Mark" button (QTimeEdit).
 * - `configCombo` — editable input backed by a datalist (ConfigComboBox).
 * - `checkList` — filterable multi-check list with optional count spins (MultiSelector).
 * - `videoPlayer` — <video> with play/pause, ±5s skip, position slider, time label
 *                   (VideoPreviewPanel + VideoPlayerControls).
 */

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

/**
 * Poll a background job until it reaches a terminal state.
 * Resolves with the finished job; throws on failure/cancellation.
 * `onProgress(pct)` and `onStatus(message)` fire on each poll.
 */
export async function pollJob(jobId, { onProgress = () => {}, onStatus = () => {} } = {}) {
  for (;;) {
    await sleep(400);
    const { ok, body } = await fetchJson(`/api/jobs/${jobId}`);
    if (!ok) throw new Error("Job lost");
    if (body.status === "done") return body;
    if (body.status === "failed" || body.status === "cancelled") {
      throw new Error(body.message || "Job failed");
    }
    if (typeof body.progress === "number") onProgress(body.progress);
    if (body.message) onStatus(body.message);
  }
}

/**
 * Return a browser-playable preview URL for a source video, transcoding on
 * demand (mirrors the Qt preview cache). Throws if the preview can't be made.
 */
export async function ensurePreview(dirName, fileName, onStatus = () => {}) {
  const url = `/api/preview/${dirName}/${encodeURIComponent(fileName)}`;
  const resp = await fetch(url);
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.startsWith("video/")) return url;

  let body;
  try {
    body = await resp.json();
  } catch {
    throw new Error("Preview unavailable");
  }
  if (body.status !== "transcoding") throw new Error("Preview unavailable");
  onStatus("Preparing preview...");
  await pollJob(body.job_id);
  return url;
}

function formatClock(totalSeconds) {
  const secs = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

function parseClock(text) {
  const match = /^(\d+):([0-5]\d)$/.exec(text.trim());
  if (!match) return null;
  return parseInt(match[1], 10) * 60 + parseInt(match[2], 10);
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
    clear() { input.value = ""; },
    setItems(items) { rebuild(items); },
  };
}

/**
 * Filterable multi-check list. When `counts` is true each item gets a
 * 1-99 count spinner (MultiSelector semantics: `selected` returns the item
 * label repeated `count` times). Otherwise plain checkboxes (deduplicated).
 */
export function checkList({ label = "", items = [], counts = false, onChange = () => {} } = {}) {
  const filter = el("input", { class: "filter-input", type: "text", placeholder: "Type to filter..." });
  const listEl = el("ul", { class: "check-list" });
  let entries = [];

  function rebuild(newItems) {
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
      const li = el("li", { class: "check-item" }, [labelEl, spin]);
      return { item, cb, spin, li };
    });
    listEl.replaceChildren();
    for (const entry of entries) listEl.appendChild(entry.li);
    applyFilter();
  }

  function applyFilter() {
    const text = filter.value.toLowerCase();
    for (const entry of entries) {
      entry.li.hidden = !entry.item.toLowerCase().includes(text);
    }
  }

  const refresh = () => onChange(selected());

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
    for (const entry of entries) entry.cb.checked = false;
  }

  filter.addEventListener("input", applyFilter);
  rebuild(items);

  const box = el("div", { class: "check-group" });
  if (label) box.appendChild(el("label", { class: "field-label", text: label }));
  box.appendChild(filter);
  box.appendChild(listEl);

  return { node: box, selected, refresh, clear, setItems: rebuild };
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

  let duration = 0;

  const updateTime = () => {
    timeLabel.textContent = `${formatClock(video.currentTime)} / ${formatClock(duration)}`;
    if (document.activeElement !== slider) slider.value = String(video.currentTime * 1000);
    onPosition(Math.floor(video.currentTime * 1000));
  };

  playBtn.addEventListener("click", () => {
    if (video.paused) video.play(); else video.pause();
  });
  backBtn.addEventListener("click", () => { video.currentTime = Math.max(0, video.currentTime - 5); });
  fwdBtn.addEventListener("click", () => { video.currentTime = Math.min(duration, video.currentTime + 5); });
  slider.addEventListener("input", () => { video.currentTime = parseInt(slider.value, 10) / 1000; });

  video.addEventListener("loadedmetadata", () => {
    duration = Number.isFinite(video.duration) ? video.duration : 0;
    slider.max = String(duration * 1000);
    updateTime();
  });
  video.addEventListener("timeupdate", updateTime);
  video.addEventListener("play", () => { playBtn.textContent = "Pause"; });
  video.addEventListener("pause", () => { playBtn.textContent = "Play"; });

  const controls = el("div", { class: "video-controls" }, [playBtn, backBtn, fwdBtn, slider, timeLabel]);
  const box = el("div", { class: "video-panel" }, [video, controls]);

  return {
    node: box,
    load(sourceUrl, startTime = 0) {
      video.src = sourceUrl;
      video.pause();
      if (startTime > 0) {
        video.addEventListener("loadedmetadata", () => {
          video.currentTime = startTime;
        }, { once: true });
      }
    },
    play() { video.play(); },
    get currentPositionMs() { return Math.floor(video.currentTime * 1000); },
  };
}
