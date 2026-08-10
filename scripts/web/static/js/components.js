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
    dialog.addEventListener("close", () => finish(false));
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
    dialog.addEventListener("close", () => {
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
export function checkList({ label = "", items = [], counts = false, onChange = () => {}, teams = null } = {}) {
  const filter = el("input", { class: "filter-input", type: "text", placeholder: "Type to filter..." });
  const listEl = el("ul", { class: "check-list" });
  let entries = [];
  let teamMap = teams || {};
  let teamFilter = "";

  const UNASSIGNED = "__unassigned__";

  function inAnyTeam(item) {
    for (const members of Object.values(teamMap)) {
      if (members.includes(item)) return true;
    }
    return false;
  }

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
    refresh,
    clear,
    setItems: rebuild,
    setTeams(data) { teamMap = data || {}; renderTeamSelect(); },
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

  const UNASSIGNED = "__unassigned__";

  function inAnyTeam(item) {
    for (const members of Object.values(teamMap)) {
      if (members.includes(item)) return true;
    }
    return false;
  }

  function select(item) {
    if (disabled) return;
    selectedValue = item;
    rebuild(itemValues);
    onChange(item);
  }

  function rebuild(newItems) {
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
    setSelected(value) { selectedValue = value; rebuild(itemValues); },
    clear() { selectedValue = ""; rebuild(itemValues); },
    setItems: rebuild,
    setDisabled,
    setTeams(data) { teamMap = data || {}; renderTeamSelect(); },
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
    seekIfReady();
  });
  video.addEventListener("timeupdate", updateTime);
  video.addEventListener("play", () => { playBtn.textContent = "Pause"; });
  video.addEventListener("pause", () => { playBtn.textContent = "Play"; });

  const controls = el("div", { class: "video-controls" }, [playBtn, backBtn, fwdBtn, slider, timeLabel]);
  const box = el("div", { class: "video-panel" }, [video, controls]);

  return {
    node: box,
    load(sourceUrl, startTime = 0) {
      destroyHls();
      video.pause();
      pendingStartTime = startTime;
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
          showToast("Preview stream failed to start.", "error");
          return;
        }
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
    pause() { video.pause(); },
    togglePlay() { if (video.paused) video.play(); else video.pause(); },
    get currentPositionMs() { return Math.floor(video.currentTime * 1000); },
  };
}
