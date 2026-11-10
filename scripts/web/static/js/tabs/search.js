/**
 * Search tab — the web port of the original Qt search widget.
 *
 * Mounts into #search-root. Uses the shared queryPanel for filters and
 * sends parallel POST /api/search requests per selected wrestler. Matches
 * are listed with click-to-preview and can be exported as CSV.
 */

import {
  el,
  ensurePreview,
  fetchJson,
  formatClock,
  queryPanel,
  showToast,
  videoPlayer,
} from "../components.js";

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
    if (state.matches.length === 0) {
      resultsList.appendChild(
        el("li", { class: "empty-item", text: "No matches found yet." })
      );
      return;
    }
    for (const match of state.matches) {
      const moves = (match.team_moves || "").split(", ").slice(0, 3).join(", ");
      const pts = match.adjusted_net_points;
      const ptsStr = pts >= 0 ? `+${pts}` : String(pts);
      const result = match.match_result || "";
      const label = `${match.wrestler} | ${match.video} ` +
        `[${formatClock(match.start_time)}-${formatClock(match.end_time)}] ` +
        `${match.attacking} | ${match.tie_up} | ${moves} | ${ptsStr} pts | ${result}`;
      const item = el("button", { class: "video-item", type: "button", text: label });
      item.addEventListener("click", () => previewMatch(match));
      resultsList.appendChild(el("li", {}, [item]));
    }
  }

  async function previewMatch(match) {
    panel.setStatus(`Loading preview: ${match.video}...`);
    try {
      const url = await ensurePreview("taged", match.video);
      player.load(url, match.start_time);
      panel.setStatus(`Previewing: ${match.video} [${formatClock(match.start_time)}]`);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
  }

  // ---------- Search / Export ----------

  async function runSearch(query) {
    panel.setButtonsEnabled(false);
    panel.setStatus("Searching...");
    state.searching = true;
    try {
      const { ok, body } = await fetchJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(query),
      });
      if (!ok) throw new Error(body.detail || "Search failed.");
      state.matches = body.matches || [];
      renderMatches();
      panel.setStatus(
        state.matches.length === 0
          ? "No matches found."
          : `Found ${state.matches.length} match(es).`
      );
    } catch (err) {
      showToast(err.message || "Search failed.", "error");
      panel.setStatus("Search failed.");
    } finally {
      state.searching = false;
      panel.setButtonsEnabled(true);
    }
  }

  async function exportResults(query) {
    if (state.matches.length === 0) {
      showToast("Run a search first.", "error");
      return;
    }
    try {
      const resp = await fetch("/api/search/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(query),
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

  // ---------- Config ----------

  async function loadOptions() {
    try {
      const [wrestlerResp, configResp] = await Promise.all([
        fetchJson("/api/search/wrestlers"),
        fetchJson("/api/config"),
      ]);
      const names = wrestlerResp.ok ? wrestlerResp.body.items || [] : [];
      const config = configResp.ok ? configResp.body : {};
      panel.setItems({
        wrestlers: names,
        ties: config.ties || [],
        moves: config.moves || [],
        teams: config.teams || {},
      });
    } catch {
      showToast("Could not load search options (network error).", "error");
    }
  }

  // ---------- Panel ----------

  const panel = queryPanel({ onSearch: runSearch, onExport: exportResults });

  // ---------- Layout ----------

  const left = el("div", { class: "tag-left" }, [panel.node]);
  const playerCard = el("section", { class: "tag-card player-card" }, [player.node]);
  const matchesCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Matched Sequences" }),
    resultCount,
    resultsList,
  ]);
  const right = el("div", { class: "tag-right" }, [playerCard, matchesCard]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  window.addEventListener("configs-updated", loadOptions);
  loadOptions();
}
