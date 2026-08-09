/**
 * Combine Clips tab — the web port of the original Qt combine clips widget.
 *
 * Mounts into #combine-clips-root. Pick one or more wrestlers and a filter
 * (starting tie-up, move used, or move defended), preview the matching
 * sequences, then generate a highlight reel via POST /api/combine-clips (or a
 * batch of reels via /api/combine-clips/batch), polling the background job.
 */

import {
  checkList,
  el,
  ensurePreview,
  filterList,
  pollJob,
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

export function mountCombineClips(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = {
    filterType: "Starting Tie",
    filterItem: "",
    matches: [],
    running: false,
    jobId: "",
  };

  // ---------- Right column: player + matched sequences ----------

  const player = videoPlayer({});

  const matchesList = el("ul", { class: "results-list" });
  const matchesCount = el("span", { text: "0" });

  function renderMatches() {
    matchesCount.textContent = String(state.matches.length);
    matchesList.replaceChildren();
    for (const match of state.matches) {
      const moves = (match.moves || []).slice(0, 2).join(", ");
      const label = `${match.video}  [${formatClock(match.start_time)}-${formatClock(match.end_time)}]  ` +
        `${match.attacking ? "A" : "D"}  ${match.tie_up}  ${moves}`;
      const item = el("button", { class: "video-item", type: "button", text: label });
      item.addEventListener("click", () => previewMatch(match));
      matchesList.appendChild(el("li", {}, [item]));
    }
  }

  async function previewMatch(match) {
    setStatus(`Loading preview: ${match.video}...`);
    try {
      const url = await ensurePreview("taged", match.video);
      player.load(url, match.start_time);
      setStatus(`Previewing: ${match.video} [${formatClock(match.start_time)}]`);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
  }

  // ---------- Left column: controls ----------

  const wrestlers = checkList({
    label: "Wrestlers (check one or more for batch):",
    items: [],
    onChange: () => {
      state.matches = [];
      renderMatches();
      updateButtons();
    },
  });

  const tieRadio = el("input", { type: "radio", name: "clip-filter", value: "Starting Tie", checked: "" });
  const moveRadio = el("input", { type: "radio", name: "clip-filter", value: "Move Used" });
  const defendRadio = el("input", { type: "radio", name: "clip-filter", value: "Move Defended" });
  const filterRadios = [tieRadio, moveRadio, defendRadio];
  for (const radio of filterRadios) {
    radio.addEventListener("change", () => {
      state.filterType = radio.value;
      swapFilterItems();
    });
  }
  const filterRow = el("div", { class: "row radio-row" }, [
    el("label", { class: "check-label", text: "Starting Tie" }, [tieRadio]),
    el("label", { class: "check-label", text: "Move Used" }, [moveRadio]),
    el("label", { class: "check-label", text: "Move Defended" }, [defendRadio]),
  ]);

  const filterItem = filterList({
    label: "Filter Item:",
    items: [],
    onChange: () => {
      state.filterItem = filterItem.selected;
      state.matches = [];
      renderMatches();
      updateButtons();
    },
  });

  const streamCopy = el("input", { type: "checkbox", checked: "" });
  const streamCopyRow = el("label", { class: "check-label", text: "Fast extraction (stream copy)" }, [streamCopy]);

  const findBtn = el("button", { class: "btn", type: "button", text: "Find Clips", disabled: "" });
  const generateBtn = el("button", { class: "btn", type: "button", text: "Generate Reel", disabled: "" });
  const batchBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Batch Generate All", disabled: "" });

  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Select a wrestler and filter to begin." });
  const outputEl = el("pre", { class: "error-display", hidden: "" });
  const playReelBtn = el("button", { class: "btn", type: "button", text: "Play Reel", hidden: "" });
  const downloadLink = el("a", { class: "btn download-link", text: "Download", hidden: "" });
  const reelActions = el("div", { class: "row" }, [playReelBtn, downloadLink]);

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function setOutput(text) {
    outputEl.hidden = !text;
    outputEl.textContent = text;
  }

  function updateButtons() {
    const hasWrestlers = wrestlers.selected().length > 0;
    const hasItem = Boolean(state.filterItem);
    findBtn.disabled = !hasWrestlers || !hasItem || state.running;
    generateBtn.disabled = !hasWrestlers || !hasItem || state.matches.length === 0 || state.running;
    batchBtn.disabled = wrestlers.selected().length < 2 || !hasItem || state.running;
  }

  function setRunning(active) {
    state.running = active;
    progress.hidden = !active;
    if (!active) progress.value = 0;
    updateButtons();
  }

  async function loadConfigs() {
    const { ok, body } = await fetchJson("/api/config");
    const config = ok ? body : {};
    wrestlers.setItems(config.wrestlers || []);
    wrestlers.setTeams(config.teams || {});
    state.ties = config.ties || [];
    state.moves = config.moves || [];
    swapFilterItems();
  }

  function swapFilterItems() {
    const items = state.filterType === "Starting Tie" ? state.ties || [] : state.moves || [];
    filterItem.setItems(items);
    filterItem.clear();
    state.filterItem = "";
    state.matches = [];
    renderMatches();
    updateButtons();
  }

  async function findClips() {
    const selected = wrestlers.selected();
    if (selected.length === 0 || !state.filterItem) return;

    setStatus("Finding matching sequences...");
    const { ok, body } = await fetchJson("/api/clips/find", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wrestler: selected[0],
        filter_type: state.filterType,
        filter_item: state.filterItem,
      }),
    });
    if (!ok) {
      showToast(body.detail || "Could not find clips.", "error");
      setStatus(body.detail || "Find clips failed.");
      state.matches = [];
      renderMatches();
      updateButtons();
      return;
    }

    state.matches = body.matches || [];
    renderMatches();
    if (state.matches.length === 0) {
      setStatus(`No sequences found for '${selected[0]}' / ${state.filterType} = '${state.filterItem}'.`);
    } else {
      setStatus(`Found ${state.matches.length} matching sequences.`);
    }
    updateButtons();
  }

  async function generateReel() {
    const selected = wrestlers.selected();
    if (selected.length === 0 || state.matches.length === 0) return;

    setRunning(true);
    setOutput("");
    reelActions.hidden = true;
    setStatus("Generating clips...");
    try {
      const { ok, body } = await fetchJson("/api/combine-clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          wrestler: selected[0],
          filter_type: state.filterType,
          filter_item: state.filterItem,
          use_stream_copy: streamCopy.checked,
        }),
      });
      if (!ok) throw new Error(body.detail || "Could not start clip generation");
      state.jobId = body.job_id;
      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      const result = job.result || {};
      setStatus(`Highlight reel created: ${result.output}`);
      showToast(`Created: ${result.output}`, "success");
      setOutput(`${result.output}\n${result.segments} segments`);
      if (result.errors && result.errors.length) setOutput(`${result.output}\n${result.errors.join("\n")}`);
      downloadLink.href = `/api/clips/${encodeURIComponent(result.output)}`;
      reelActions.hidden = false;
    } catch (err) {
      showToast(err.message || "Clip generation failed.", "error");
      setStatus("Failed to create highlight reel.");
    } finally {
      state.jobId = "";
      setRunning(false);
    }
  }

  async function batchGenerate() {
    const selected = wrestlers.selected();
    if (selected.length < 2 || !state.filterItem) return;

    setRunning(true);
    setOutput("");
    setStatus(`Batch generating ${selected.length} reel(s)...`);
    try {
      const { ok, body } = await fetchJson("/api/combine-clips/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          wrestlers: selected,
          filter_type: state.filterType,
          filter_item: state.filterItem,
          use_stream_copy: streamCopy.checked,
        }),
      });
      if (!ok) throw new Error(body.detail || "Could not start batch generation");
      state.jobId = body.job_id;
      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      const result = job.result || {};
      const lines = (result.results || []).map((r) =>
        `${r.success ? "\u2713" : "\u2717"} ${r.wrestler}: ${r.message}`
      );
      setOutput(lines.join("\n"));
      setStatus(result.summary || "Batch complete.");
      showToast(result.summary || "Batch complete.", result.failures > 0 ? "warning" : "success");
    } catch (err) {
      showToast(err.message || "Batch generation failed.", "error");
      setStatus("Batch generation failed.");
    } finally {
      state.jobId = "";
      setRunning(false);
    }
  }

  // ---------- Wiring ----------

  playReelBtn.addEventListener("click", async () => {
    const file = downloadLink.getAttribute("href");
    if (!file) return;
    const name = decodeURIComponent(file.split("/").pop());
    try {
      const url = await ensurePreview("clips", name);
      player.load(url);
      player.play();
    } catch (err) {
      showToast(err.message || "Could not load reel.", "error");
    }
  });

  findBtn.addEventListener("click", findClips);
  generateBtn.addEventListener("click", generateReel);
  batchBtn.addEventListener("click", batchGenerate);

  // ---------- Layout ----------

  const controlsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Filters" }),
    wrestlers.node,
    el("div", { class: "row radio-row" }, [filterRow]),
    filterItem.node,
    streamCopyRow,
  ]);
  const actionsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Actions" }),
    el("div", { class: "row" }, [findBtn, generateBtn, batchBtn]),
    progress,
    statusLabel,
    outputEl,
    reelActions,
  ]);
  const left = el("div", { class: "tag-left" }, [controlsCard, actionsCard]);

  const playerCard = el("section", { class: "tag-card" }, [player.node]);
  const matchesCard = el("fieldset", { class: "tag-card" }, [
    el("legend", {}, [el("span", { text: "Matched Sequences: " }), matchesCount]),
    matchesList,
  ]);
  const right = el("div", { class: "tag-right" }, [playerCard, matchesCard]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  window.addEventListener("configs-updated", loadConfigs);
  loadConfigs();
}
