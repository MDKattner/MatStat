/**
 * Search tab — the web port of scripts/qt_app/search_widget.py.
 *
 * Mounts into #search-root. Loads every wrestler's compiled CSV server-side
 * (GET /api/search/wrestlers) and filters it by wrestler, attack/defense mode,
 * starting tie-up, team/opponent move substrings, and a net points range.
 * Matches are listed with click-to-preview, and can be exported as CSV via
 * POST /api/search/export.
 */

import {
  configCombo,
  el,
  ensurePreview,
  showToast,
  videoPlayer,
} from "../components.js";

function formatClock(totalSeconds) {
  const secs = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

export function mountSearch(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = { matches: [], searching: false };

  // ---------- Right column: results ----------

  const player = videoPlayer({});
  const resultCount = el("h3", { class: "results-title", text: "Results: 0" });
  const resultsList = el("ul", { class: "results-list" });

  function renderMatches() {
    resultCount.textContent = `Results: ${state.matches.length}`;
    resultsList.replaceChildren();
    for (const match of state.matches) {
      const label = `${match.wrestler} | ${match.video} [${formatClock(match.start_time)}-${formatClock(match.end_time)}] ` +
        `${match.attacking} | ${match.tie_up} | ${match.team_moves}`;
      const item = el("button", { class: "video-item", type: "button", text: label });
      item.addEventListener("click", () => previewMatch(match));
      resultsList.appendChild(el("li", {}, [item]));
    }
  }

  async function previewMatch(match) {
    setStatus(`Loading preview: ${match.video}...`);
    try {
      const url = await ensurePreview("taged", match.video, setStatus);
      player.load(url, match.start_time);
      setStatus(`Previewing: ${match.video} [${formatClock(match.start_time)}]`);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
  }

  // ---------- Left column: filters ----------

  const wrestler = configCombo({ label: "Wrestler:", items: ["All"] });
  wrestler.setSelected("All");

  const attackMode = el("select", { class: "combo-input" }, [
    el("option", { value: "All", text: "All" }),
    el("option", { value: "Attacking", text: "Attacking" }),
    el("option", { value: "Defending", text: "Defending" }),
  ]);

  const tie = configCombo({ label: "Tie Up:", items: [] });
  const teamMove = el("input", { class: "combo-input", type: "text", placeholder: "e.g. high crotch" });
  const oppMove = el("input", { class: "combo-input", type: "text", placeholder: "e.g. sprawl" });

  const minPts = el("input", { class: "combo-input pts-input", type: "number", value: "-20", min: "-20", max: "20" });
  const maxPts = el("input", { class: "combo-input pts-input", type: "number", value: "20", min: "-20", max: "20" });

  const searchBtn = el("button", { class: "btn", type: "button", text: "Search" });
  const exportBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Export Results as CSV" });

  const statusLabel = el("p", { class: "status-label", text: "Run a search to begin." });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function setSearching(active) {
    state.searching = active;
    searchBtn.disabled = active;
    exportBtn.disabled = active;
  }

  function currentQuery() {
    return {
      wrestler: wrestler.selected,
      attack_mode: attackMode.value,
      tie_up: tie.selected,
      team_move: teamMove.value,
      opp_move: oppMove.value,
      min_points: parseInt(minPts.value, 10) || -20,
      max_points: parseInt(maxPts.value, 10) || 20,
    };
  }

  async function runSearch() {
    setSearching(true);
    setStatus("Searching...");
    try {
      const { ok, body } = await fetchJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentQuery()),
      });
      if (!ok) throw new Error(body.detail || "Search failed");
      state.matches = body.matches || [];
      renderMatches();
      setStatus(state.matches.length === 0 ? "No matches found." : `Found ${state.matches.length} match(es).`);
    } catch (err) {
      showToast(err.message || "Search failed.", "error");
      setStatus("Search failed.");
    } finally {
      setSearching(false);
    }
  }

  async function exportResults() {
    if (state.matches.length === 0) {
      showToast("Run a search first.", "error");
      return;
    }
    try {
      const resp = await fetch("/api/search/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentQuery()),
      });
      if (!resp.ok) throw new Error("Export failed");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const link = el("a", { href: url, download: "search_results.csv" });
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showToast(`Exported ${state.matches.length} result(s).`, "success");
    } catch (err) {
      showToast(err.message || "Export failed.", "error");
    }
  }

  async function loadOptions() {
    const [wrestlerItems, tieItems] = await Promise.all([
      fetchJson("/api/search/wrestlers"),
      fetchJson("/api/configs/Ties.config"),
    ]);
    const names = wrestlerItems.ok ? wrestlerItems.body.items || [] : [];
    wrestler.setItems(["All", ...names]);
    tie.setItems(tieItems.ok ? tieItems.body.items || [] : []);
  }

  searchBtn.addEventListener("click", runSearch);
  exportBtn.addEventListener("click", exportResults);

  // ---------- Layout ----------

  const filtersCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Filters" }),
    wrestler.node,
    el("div", { class: "row filter-row" }, [
      el("label", { class: "field-label", text: "Attack/Defense:" }, [attackMode]),
    ]),
    tie.node,
    el("label", { class: "field-label", text: "Team Move (contains):" }, [teamMove]),
    el("label", { class: "field-label", text: "Opponent Move (contains):" }, [oppMove]),
    el("div", { class: "row filter-row" }, [
      el("label", { class: "field-label", text: "Min Net Points:" }, [minPts]),
      el("label", { class: "field-label", text: "Max Net Points:" }, [maxPts]),
    ]),
    el("div", { class: "row" }, [searchBtn, exportBtn]),
    statusLabel,
  ]);
  const left = el("div", { class: "tag-left" }, [filtersCard]);

  const playerCard = el("section", { class: "tag-card" }, [player.node]);
  const matchesCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Matched Sequences" }),
    resultCount,
    resultsList,
  ]);
  const right = el("div", { class: "tag-right" }, [playerCard, matchesCard]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  loadOptions();
}
