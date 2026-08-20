/**
 * Combine Clips tab — the web port of the original Qt combine clips widget.
 *
 * Mounts into #combine-clips-root. Uses the shared queryPanel for filters.
 * Pick one or more wrestlers and filters, preview matching sequences, then
 * generate a highlight reel via POST /api/combine-clips (or batch), polling
 * the background job.
 */

import {
  el,
  ensurePreview,
  fetchJson,
  filterList,
  formatClock,
  pollJob,
  queryPanel,
  showToast,
  videoPlayer,
} from "../components.js";

export function mountCombineClips(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = {
    matches: [],
    running: false,
    finding: false,
    jobId: "",
    ties: [],
    moves: [],
  };

  // ---------- Right column: player + matched sequences ----------

  const player = videoPlayer({});
  const matchesList = el("ul", { class: "results-list" });
  const matchesCount = el("span", { text: "0" });

  function renderMatches() {
    matchesCount.textContent = String(state.matches.length);
    matchesList.replaceChildren();
    if (state.matches.length === 0) {
      matchesList.appendChild(
        el("li", { class: "empty-item", text: "No matching sequences yet." })
      );
      return;
    }
    for (const match of state.matches) {
      const moves = (match.moves || []).slice(0, 3).join(", ");
      const who = match.wrestler ? `${match.wrestler} | ` : "";
      const atk = match.attacking ? "A" : "D";
      const pts = match.adjusted_net_points ?? 0;
      const ptsStr = pts >= 0 ? `+${pts}` : String(pts);
      const result = match.match_result || "";
      const label = `${who}${match.video} ` +
        `[${formatClock(match.start_time)}-${formatClock(match.end_time)}] ` +
        `${atk} | ${match.tie_up} | ${moves} | ${ptsStr} pts | ${result}`;
      const item = el("button", { class: "video-item", type: "button", text: label });
      item.addEventListener("click", () => previewMatch(match));
      matchesList.appendChild(el("li", {}, [item]));
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

  // ---------- Find Clips ----------

  async function findClips(query) {
    const selected = query.wrestlers;
    if (selected.length === 0) {
      showToast("Select at least one wrestler.", "error");
      return;
    }

    state.finding = true;
    panel.setButtonsEnabled(false);
    panel.setStatus(`Finding matching sequences for ${selected.length} wrestler(s)...`);
    try {
      const responses = await Promise.all(selected.map((wrestler) =>
        fetchJson("/api/clips/find", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            wrestler,
            attack_mode: query.attack_mode,
            tie_up: query.tie_up,
            team_move: query.team_move,
            opp_move: query.opp_move,
            min_points: query.min_points,
            max_points: query.max_points,
          }),
        })
      ));

      const merged = [];
      for (let i = 0; i < selected.length; i++) {
        const { ok, body } = responses[i];
        if (!ok) {
          showToast(body.detail || `Could not find clips for '${selected[i]}'.`, "error");
          continue;
        }
        for (const match of body.matches || []) {
          merged.push({ ...match, wrestler: selected[i] });
        }
      }

      state.matches = merged;
      renderMatches();
      if (merged.length === 0) {
        const who = selected.length > 1 ? "the selected wrestlers" : `'${selected[0]}'`;
        panel.setStatus(`No sequences found for ${who}.`);
      } else {
        panel.setStatus(`Found ${merged.length} matching sequence(s).`);
      }
    } catch {
      showToast("Could not find clips (network error).", "error");
      panel.setStatus("Could not find clips.");
    } finally {
      state.finding = false;
      panel.setButtonsEnabled(true);
    }
  }

  // ---------- Generate Reel ----------

  function deriveFilter(query) {
    if (query.tie_up) return { filter_type: "Starting Tie", filter_item: query.tie_up };
    if (query.team_move) return { filter_type: "Move Used", filter_item: query.team_move };
    if (query.opp_move) return { filter_type: "Move Defended", filter_item: query.opp_move };
    return { filter_type: "Starting Tie", filter_item: "" };
  }

  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const outputEl = el("pre", { class: "error-display", hidden: "" });
  const playReelBtn = el("button", { class: "btn", type: "button", text: "Play Reel", hidden: "" });
  const downloadLink = el("a", { class: "btn download-link", text: "Download", hidden: "" });
  const reelActions = el("div", { class: "row" }, [playReelBtn, downloadLink]);

  function setOutput(text) {
    outputEl.hidden = !text;
    outputEl.textContent = text;
  }

  async function generateReel(query) {
    const selected = query.wrestlers;
    if (selected.length === 0 || state.matches.length === 0) return;

    const { filter_type, filter_item } = deriveFilter(query);
    if (!filter_item) {
      showToast("Set a Tie Up, Team Move, or Opponent Move to generate a reel.", "error");
      return;
    }

    const isBatch = selected.length > 1;
    state.running = true;
    progress.hidden = false;
    progress.value = 0;
    panel.setButtonsEnabled(false);
    setOutput("");
    reelActions.hidden = true;
    panel.setStatus(isBatch ? `Batch generating ${selected.length} reel(s)...` : "Generating clips...");

    try {
      const payload = { filter_type, filter_item };
      if (isBatch) payload.wrestlers = selected;
      else payload.wrestler = selected[0];

      const { ok, body } = await fetchJson(
        isBatch ? "/api/combine-clips/batch" : "/api/combine-clips",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      if (!ok) throw new Error(body.detail || "Could not start clip generation");
      state.jobId = body.job_id;

      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: (msg) => panel.setStatus(msg),
      });
      const result = job.result || {};

      if (isBatch) {
        const lines = (result.results || []).map((r) =>
          `${r.success ? "\u2713" : "\u2717"} ${r.wrestler}: ${r.message}`
        );
        setOutput(lines.join("\n"));
        panel.setStatus(result.summary || "Batch complete.");
        showToast(result.summary || "Batch complete.", result.failures > 0 ? "warning" : "success");
      } else {
        panel.setStatus(`Highlight reel created: ${result.output}`);
        showToast(`Created: ${result.output}`, "success");
        setOutput(`${result.output}\n${result.segments} segments`);
        if (result.errors?.length) setOutput(`${result.output}\n${result.errors.join("\n")}`);
        downloadLink.href = `/api/clips/${encodeURIComponent(result.output)}`;
        reelActions.hidden = false;
      }
    } catch (err) {
      showToast(err.message || "Clip generation failed.", "error");
      panel.setStatus("Failed to create highlight reel.");
    } finally {
      state.jobId = "";
      state.running = false;
      progress.hidden = true;
      progress.value = 0;
      panel.setButtonsEnabled(true);
    }
  }

  // ---------- Wiring ----------

  playReelBtn.addEventListener("click", async () => {
    const href = downloadLink.getAttribute("href");
    if (!href) return;
    const name = decodeURIComponent(href.split("/").pop());
    try {
      const url = await ensurePreview("clips", name);
      player.load(url);
      player.play();
    } catch (err) {
      showToast(err.message || "Could not load reel.", "error");
    }
  });

  // ---------- Panel ----------

  const panel = queryPanel({ onFindClips: findClips, onGenerateReel: generateReel });

  // ---------- Layout ----------

  const left = el("div", { class: "tag-left" }, [panel.node]);

  const playerCard = el("section", { class: "tag-card player-card" }, [player.node]);
  const matchesCard = el("fieldset", { class: "tag-card" }, [
    el("legend", {}, [el("span", { text: "Matched Sequences: " }), matchesCount]),
    matchesList,
  ]);
  const right = el("div", { class: "tag-right" }, [playerCard, matchesCard]);

  const bottomRow = el("div", { class: "tag-left" }, [
    el("fieldset", { class: "tag-card" }, [
      el("legend", { text: "Reel Output" }),
      progress,
      outputEl,
      reelActions,
    ]),
  ]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));
  root.appendChild(bottomRow);

  // ---------- Boot ----------

  async function loadConfigs() {
    try {
      const { ok, body } = await fetchJson("/api/config");
      const config = ok ? body : {};
      state.ties = config.ties || [];
      state.moves = config.moves || [];
      panel.setItems({
        wrestlers: config.wrestlers || [],
        ties: state.ties,
        moves: state.moves,
        teams: config.teams || {},
      });
    } catch {
      showToast("Could not load config (moves/ties/wrestlers).", "error");
    }
  }

  window.addEventListener("configs-updated", loadConfigs);
  loadConfigs();
}
