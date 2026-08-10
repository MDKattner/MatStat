/**
 * MatStat SPA shell.
 *
 * Wires the tab bar, status bar, log dock, connection badge, and About dialog,
 * then mounts every tab into its `.tab-pane` section.
 */

import { connectWs, onWs, onWsStatus } from "./ws.js";
import { confirmModal, el, promptForm, showModal, showToast } from "./components.js";
import { mountAudit } from "./tabs/audit.js";
import { mountCombineClips } from "./tabs/combine_clips.js";
import { mountCompileStats } from "./tabs/compile_stats.js";
import { mountPca } from "./tabs/pca.js";
import { mountSearch } from "./tabs/search.js";
import { mountTagFilm } from "./tabs/tag_film.js";
import { mountTeamEval } from "./tabs/team_eval.js";

const MAX_LOG_LINES = 5000;

async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

// ---------- Authentication ----------

/**
 * Redirect to /login whenever an API call comes back 401 (e.g. the session
 * expired). Wraps the global fetch so every tab's requests inherit the
 * behavior without changes. The login page (a separate document) is excluded.
 */
function initAuthRedirect() {
  const origFetch = window.fetch.bind(window);
  window.fetch = async (url, options) => {
    const resp = await origFetch(url, options);
    if (resp.status === 401 && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
      return resp;
    }
    return resp;
  };
}

/** Bind the header logout button (present only when auth is enabled). */
function initLogout() {
  const btn = document.getElementById("logout-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  });
}

// ---------- Tabs ----------

function selectTab(tabId) {
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.setAttribute("aria-selected", String(btn.dataset.tab === tabId));
  }
  for (const pane of document.querySelectorAll(".tab-pane")) {
    pane.hidden = pane.dataset.tab !== tabId;
  }
}

function initTabs() {
  const bar = document.getElementById("tab-bar");
  bar.addEventListener("click", (event) => {
    const btn = event.target.closest(".tab-btn");
    if (btn) selectTab(btn.dataset.tab);
  });
}

// ---------- Log dock ----------

function appendLog(message, level = "INFO") {
  const view = document.getElementById("log-view");
  if (!view) return;
  const line = document.createElement("div");
  line.className = "log-line";
  line.dataset.level = level;
  line.textContent = message;
  view.appendChild(line);

  while (view.childElementCount > MAX_LOG_LINES) {
    view.removeChild(view.firstChild);
  }
  const autoScroll = view.closest("#log-dock") && !view.closest("#log-dock").hidden;
  if (autoScroll) view.scrollTop = view.scrollHeight;
}

function setLogVisible(visible) {
  const dock = document.getElementById("log-dock");
  const toggle = document.getElementById("log-toggle");
  if (!dock || !toggle) return;
  dock.hidden = !visible;
  toggle.setAttribute("aria-expanded", String(visible));
}

function initLogDock() {
  const toggle = document.getElementById("log-toggle");
  const dock = document.getElementById("log-dock");
  if (!toggle || !dock) return;
  toggle.addEventListener("click", () => {
    setLogVisible(dock.hidden);
  });
  document.getElementById("log-close").addEventListener("click", () => setLogVisible(false));
  document.getElementById("log-clear").addEventListener("click", () => {
    document.getElementById("log-view").replaceChildren();
  });

  onWs("log", (event) => appendLog(event.message, event.level));
  onWs("job_started", (event) => {
    appendLog(`[job ${event.kind}] started (${event.id})`, "INFO");
  });
  onWs("job_finished", (event) => {
    const status = event.status.toUpperCase();
    appendLog(`[job ${event.kind}] ${status}: ${event.message}`, status === "done" ? "INFO" : "WARNING");
  });
}

// ---------- Status bar / connection ----------

function initStatus() {
  const status = document.getElementById("status-text");
  const badge = document.getElementById("conn-badge");

  onWsStatus((state) => {
    badge.dataset.state = state;
    badge.textContent = state;
    if (state === "online") {
      status.textContent = "Ready";
      fetch("/api/health").catch(() => {});
    } else if (state === "offline") {
      status.textContent = "Reconnecting...";
    }
  });
}

function initAbout() {
  document.getElementById("about-btn").addEventListener("click", () => showModal("about-dialog"));
}

// ---------- Config editor ----------

/**
 * Generic single-string list editor (Moves / Ties / Wrestlers).
 * Renders `get()` into the list; Add/Edit/Delete use styled modal forms.
 * Returns `{ render }` so the caller can refresh after loading new data.
 */
function bindListEditor({ listId, addId, editId, delId, label, get, set }) {
  const list = document.getElementById(listId);

  const activeEntry = () => list.querySelector(".cfg-entry.active");

  function render() {
    list.replaceChildren();
    for (const item of get()) {
      list.appendChild(el("li", { class: "cfg-entry video-item", text: item }));
    }
  }

  document.getElementById(addId).addEventListener("click", async () => {
    const values = await promptForm({
      title: `New ${label}`,
      fields: [{ name: "value", label: `${label}:`, required: true }],
    });
    if (values && values.value) {
      set([...get(), values.value]);
      render();
    }
  });

  document.getElementById(editId).addEventListener("click", async () => {
    const active = activeEntry();
    if (!active) {
      showToast("Select an entry to edit.", "info");
      return;
    }
    const values = await promptForm({
      title: `Edit ${label}`,
      fields: [{ name: "value", label: `${label}:`, value: active.textContent, required: true }],
    });
    if (!values || !values.value) return;
    const idx = get().indexOf(active.textContent);
    if (idx !== -1) {
      const next = [...get()];
      next[idx] = values.value;
      set(next);
      render();
    }
  });

  document.getElementById(delId).addEventListener("click", async () => {
    const active = activeEntry();
    if (!active) {
      showToast("Select an entry to delete.", "info");
      return;
    }
    const confirmed = await confirmModal({
      title: `Delete ${label}`,
      message: `Delete '${active.textContent}'?`,
    });
    if (confirmed) {
      set(get().filter((item) => item !== active.textContent));
      render();
    }
  });

  list.addEventListener("click", (event) => {
    const entry = event.target.closest(".cfg-entry");
    if (!entry) return;
    for (const item of list.querySelectorAll(".cfg-entry")) item.classList.remove("active");
    entry.classList.add("active");
  });

  render();
  return { render };
}

/**
 * Team editor: team names with member lists. Members are edited via a
 * comma-separated modal textarea (normalized: trimmed, deduped, empties dropped).
 * Returns `{ render }`.
 */
function bindTeamsEditor({ listId, addId, renameId, delId, membersId, get, set }) {
  const list = document.getElementById(listId);

  const activeEntry = () => list.querySelector(".cfg-entry.active");
  const activeName = () => {
    const entry = activeEntry();
    return entry ? entry.dataset.team : "";
  };

  const normalizeMembers = (text) =>
    [...new Set(text.split(",").map((s) => s.trim()).filter(Boolean))];

  function render() {
    list.replaceChildren();
    for (const name of Object.keys(get())) {
      const members = get()[name] || [];
      list.appendChild(el("li", {
        class: "cfg-entry video-item",
        "data-team": name,
        text: `${name} (${members.length})`,
      }));
    }
  }

  document.getElementById(addId).addEventListener("click", async () => {
    const values = await promptForm({
      title: "New team",
      fields: [{ name: "value", label: "Team name:", required: true }],
    });
    const name = values && values.value;
    if (name && !(name in get())) {
      set({ ...get(), [name]: [] });
      render();
    }
  });

  document.getElementById(renameId).addEventListener("click", async () => {
    const oldName = activeName();
    if (!oldName) {
      showToast("Select a team to rename.", "info");
      return;
    }
    const values = await promptForm({
      title: "Rename team",
      fields: [{ name: "value", label: "Team name:", value: oldName, required: true }],
    });
    const name = values && values.value;
    if (!name || name === oldName) return;
    const next = { ...get() };
    next[name] = next[oldName];
    delete next[oldName];
    set(next);
    render();
  });

  document.getElementById(delId).addEventListener("click", async () => {
    const oldName = activeName();
    if (!oldName) {
      showToast("Select a team to delete.", "info");
      return;
    }
    const confirmed = await confirmModal({
      title: "Delete team",
      message: `Delete team '${oldName}'?`,
    });
    if (confirmed) {
      const next = { ...get() };
      delete next[oldName];
      set(next);
      render();
    }
  });

  document.getElementById(membersId).addEventListener("click", async () => {
    const oldName = activeName();
    if (!oldName) {
      showToast("Select a team to edit members.", "info");
      return;
    }
    const current = (get()[oldName] || []).join(", ");
    const values = await promptForm({
      title: `Members of '${oldName}'`,
      fields: [{
        name: "members",
        label: "Members (comma-separated):",
        type: "textarea",
        value: current,
      }],
    });
    if (values == null) return;
    set({ ...get(), [oldName]: normalizeMembers(values.members) });
    render();
  });

  list.addEventListener("click", (event) => {
    const entry = event.target.closest(".cfg-entry");
    if (!entry) return;
    for (const item of list.querySelectorAll(".cfg-entry")) item.classList.remove("active");
    entry.classList.add("active");
  });

  render();
  return { render };
}

function initConfigEditor() {
  const dialog = document.getElementById("config-editor-dialog");
  const rulesetSelect = document.getElementById("cfg-ruleset-select");
  const activeRulesetSelect = document.getElementById("cfg-active-ruleset");
  const outcomesList = document.getElementById("cfg-outcomes");
  const pinPointsInput = document.getElementById("cfg-pin-points");

  let config = {
    active_ruleset: "",
    moves: [],
    ties: [],
    rulesets: {},
    wrestlers: [],
    teams: {},
  };

  const outcomeLabel = (code, outcome) =>
    `${code} = ${outcome.points} (${outcome.description})${outcome.counts_as_pin ? " [pin]" : ""}`;

  function renderRulesetSelects() {
    const names = Object.keys(config.rulesets);
    if (!names.includes(config.active_ruleset)) config.active_ruleset = names[0] || "";
    for (const select of [rulesetSelect, activeRulesetSelect]) {
      select.replaceChildren();
      for (const name of names) select.appendChild(el("option", { value: name, text: name }));
    }
    rulesetSelect.value = config.active_ruleset;
    activeRulesetSelect.value = config.active_ruleset;
  }

  function renderOutcomes() {
    outcomesList.replaceChildren();
    const ruleset = config.rulesets[rulesetSelect.value];
    if (!ruleset) return;
    for (const [code, outcome] of Object.entries(ruleset.outcomes || {})) {
      outcomesList.appendChild(el("li", {
        class: "cfg-entry video-item",
        "data-code": code,
        text: outcomeLabel(code, outcome),
      }));
    }
    pinPointsInput.value = String(ruleset.pin_points ?? 0);
  }

  async function promptOutcome(existing) {
    const values = await promptForm({
      title: existing ? "Edit Outcome" : "Add Outcome",
      fields: [
        { name: "code", label: "Code:", value: existing ? existing.code : "", required: true },
        {
          name: "points", label: "Points:",
          value: existing ? String(existing.points) : "",
          required: true,
          validate: (v) => (Number.isNaN(parseInt(v, 10)) ? "Points must be a number." : ""),
        },
        { name: "description", label: "Description:", value: existing ? existing.description : "" },
        {
          name: "counts_as_pin", label: "Counts as pin:", type: "select",
          options: ["no", "yes"],
          value: existing ? (existing.counts_as_pin ? "yes" : "no") : "no",
        },
      ],
    });
    if (!values) return null;
    const points = parseInt(values.points, 10);
    if (Number.isNaN(points)) return null;
    return {
      code: values.code,
      points,
      description: values.description,
      counts_as_pin: values.counts_as_pin === "yes",
    };
  }

  const movesEditor = bindListEditor({
    listId: "cfg-moves", addId: "cfg-moves-add", editId: "cfg-moves-edit", delId: "cfg-moves-del",
    label: "move", get: () => config.moves, set: (value) => { config.moves = value; },
  });
  const tiesEditor = bindListEditor({
    listId: "cfg-ties", addId: "cfg-ties-add", editId: "cfg-ties-edit", delId: "cfg-ties-del",
    label: "tie", get: () => config.ties, set: (value) => { config.ties = value; },
  });
  const wrestlersEditor = bindListEditor({
    listId: "cfg-wrestlers", addId: "cfg-wrestlers-add", editId: "cfg-wrestlers-edit", delId: "cfg-wrestlers-del",
    label: "wrestler", get: () => config.wrestlers, set: (value) => { config.wrestlers = value; },
  });
  const teamsEditor = bindTeamsEditor({
    listId: "cfg-teams", addId: "cfg-teams-add", renameId: "cfg-teams-rename",
    delId: "cfg-teams-del", membersId: "cfg-teams-members",
    get: () => config.teams, set: (value) => { config.teams = value; },
  });

  const renderAll = () => {
    movesEditor.render();
    tiesEditor.render();
    wrestlersEditor.render();
    teamsEditor.render();
    renderRulesetSelects();
    renderOutcomes();
  };

  rulesetSelect.addEventListener("change", renderOutcomes);
  pinPointsInput.addEventListener("change", () => {
    const ruleset = config.rulesets[rulesetSelect.value];
    if (!ruleset) return;
    const value = parseInt(pinPointsInput.value, 10);
    ruleset.pin_points = Number.isNaN(value) ? 0 : value;
  });
  activeRulesetSelect.addEventListener("change", () => {
    config.active_ruleset = activeRulesetSelect.value;
  });

  document.getElementById("cfg-outcome-add").addEventListener("click", async () => {
    const ruleset = config.rulesets[rulesetSelect.value];
    if (!ruleset) {
      showToast("No ruleset selected.", "info");
      return;
    }
    const outcome = await promptOutcome(null);
    if (!outcome) return;
    ruleset.outcomes = ruleset.outcomes || {};
    if (outcome.code in ruleset.outcomes) {
      showToast(`Outcome '${outcome.code}' already exists.`, "info");
      return;
    }
    ruleset.outcomes[outcome.code] = {
      points: outcome.points,
      description: outcome.description,
      counts_as_pin: outcome.counts_as_pin,
    };
    renderOutcomes();
  });

  document.getElementById("cfg-outcome-edit").addEventListener("click", async () => {
    const ruleset = config.rulesets[rulesetSelect.value];
    const active = outcomesList.querySelector(".cfg-entry.active");
    if (!ruleset || !active) {
      showToast("Select an outcome to edit.", "info");
      return;
    }
    const code = active.dataset.code;
    const existing = { code, ...(ruleset.outcomes[code] || {}) };
    const outcome = await promptOutcome(existing);
    if (!outcome) return;
    delete ruleset.outcomes[code];
    ruleset.outcomes[outcome.code] = {
      points: outcome.points,
      description: outcome.description,
      counts_as_pin: outcome.counts_as_pin,
    };
    renderOutcomes();
  });

  document.getElementById("cfg-outcome-del").addEventListener("click", async () => {
    const ruleset = config.rulesets[rulesetSelect.value];
    const active = outcomesList.querySelector(".cfg-entry.active");
    if (!ruleset || !active) {
      showToast("Select an outcome to delete.", "info");
      return;
    }
    const code = active.dataset.code;
    const confirmed = await confirmModal({
      title: "Delete outcome",
      message: `Delete outcome '${code}'?`,
    });
    if (confirmed) {
      delete ruleset.outcomes[code];
      renderOutcomes();
    }
  });

  outcomesList.addEventListener("click", (event) => {
    const entry = event.target.closest(".cfg-entry");
    if (!entry) return;
    for (const item of outcomesList.querySelectorAll(".cfg-entry")) item.classList.remove("active");
    entry.classList.add("active");
  });

  document.getElementById("cfg-cancel").addEventListener("click", () => dialog.close());

  document.getElementById("cfg-save").addEventListener("click", async () => {
    const { ok, body } = await fetchJson("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active_ruleset: activeRulesetSelect.value,
        moves: config.moves,
        ties: config.ties,
        rulesets: config.rulesets,
      }),
    });
    if (!ok) {
      showToast(body.detail || "Could not save config.", "error");
      return;
    }
    const wr = await fetchJson("/api/config/wrestlers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wrestlers: config.wrestlers, teams: config.teams }),
    });
    if (!wr.ok) {
      showToast(wr.body.detail || "Could not save wrestlers.", "error");
      return;
    }
    // Reflect server-side normalization (unassigned team members are dropped).
    config.wrestlers = wr.body.wrestlers || config.wrestlers;
    config.teams = wr.body.teams || config.teams;
    dialog.close();
    showToast("Config saved.", "success");
    window.dispatchEvent(new CustomEvent("configs-updated"));
  });

  document.getElementById("settings-btn").addEventListener("click", async () => {
    const { ok, body } = await fetchJson("/api/config");
    if (!ok) {
      showToast("Could not load config.", "error");
      return;
    }
    config = {
      active_ruleset: body.active_ruleset || "",
      moves: body.moves || [],
      ties: body.ties || [],
      rulesets: body.rulesets || {},
      wrestlers: body.wrestlers || [],
      teams: body.teams || {},
    };
    renderAll();
    dialog.showModal();
  });
}

// ---------- Boot ----------

function boot() {
  const safe = (name, fn) => {
    try {
      fn();
    } catch (err) {
      console.error(`init ${name} failed:`, err);
    }
  };
  safe("initTabs", initTabs);
  safe("initAuthRedirect", initAuthRedirect);
  safe("initLogout", initLogout);
  safe("initLogDock", initLogDock);
  safe("initStatus", initStatus);
  safe("initAbout", initAbout);
  safe("initConfigEditor", initConfigEditor);
  safe("mountAudit", () => mountAudit(document.getElementById("audit-root")));
  safe("mountCombineClips", () => mountCombineClips(document.getElementById("combine-clips-root")));
  safe("mountCompileStats", () => mountCompileStats(document.getElementById("compile-stats-root")));
  safe("mountPca", () => mountPca(document.getElementById("pca-root")));
  safe("mountSearch", () => mountSearch(document.getElementById("search-root")));
  safe("mountTagFilm", () => mountTagFilm(document.getElementById("tag-film-root")));
  safe("mountTeamEval", () => mountTeamEval(document.getElementById("team-eval-root")));
  connectWs();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
