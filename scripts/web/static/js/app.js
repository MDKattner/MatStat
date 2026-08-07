/**
 * MatStat SPA shell.
 *
 * Wires the tab bar, status bar, log dock, connection badge, and About dialog.
 * Ports of the individual tabs will mount into their `.tab-pane` sections as
 * they are completed (Phases 2-6); the placeholder panes live in index.html.
 */

import { connectWs, onWs, onWsStatus } from "./ws.js";
import { showModal } from "./components.js";
import { mountCombineClips } from "./tabs/combine_clips.js";
import { mountCompileStats } from "./tabs/compile_stats.js";
import { mountSearch } from "./tabs/search.js";
import { mountTagFilm } from "./tabs/tag_film.js";
import { mountTeamEval } from "./tabs/team_eval.js";

const MAX_LOG_LINES = 5000;

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
  dock.hidden = !visible;
  toggle.setAttribute("aria-expanded", String(visible));
}

function initLogDock() {
  document.getElementById("log-toggle").addEventListener("click", () => {
    setLogVisible(document.getElementById("log-dock").hidden);
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
  document.getElementById("settings-btn").addEventListener("click", () => showModal("about-dialog"));
}

// ---------- Boot ----------

function boot() {
  initTabs();
  initLogDock();
  initStatus();
  initAbout();
  mountCombineClips(document.getElementById("combine-clips-root"));
  mountCompileStats(document.getElementById("compile-stats-root"));
  mountSearch(document.getElementById("search-root"));
  mountTagFilm(document.getElementById("tag-film-root"));
  mountTeamEval(document.getElementById("team-eval-root"));
  connectWs();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
