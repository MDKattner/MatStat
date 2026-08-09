/**
 * PCA tab — interactive PCA scatter of tagged sequences.
 *
 * Mounts into #pca-root. Pick a scope (all wrestlers, a team, or a single
 * wrestler) and a layout (per-match points vs. individual sequences), then
 * brush-select points on the Plotly scatter to preview or compile them into a
 * highlight reel via POST /api/pca/compile (polling the background job).
 */

import {
  el,
  ensurePreview,
  pollJob,
  showToast,
  videoPlayer,
} from "../components.js";

/**
 * Format seconds as m:ss for labels.
 */
function formatClock(totalSeconds) {
  const secs = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

/**
 * Fetch JSON and normalize the response into { ok, status, body }.
 */
async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

/**
 * Ensure the Plotly library is loaded, resolving with the Plotly object.
 *
 * Resolves immediately when window.Plotly is already present (the vendored
 * plotly.min.js referenced by index.html); otherwise injects the CDN build
 * into the document head and waits for it to load. Rejects after a 15s
 * timeout or if the CDN script fails to load.
 */
function ensurePlotly() {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://cdn.plot.ly/plotly-2.35.2.min.js";
    const onLoad = () => {
      clearTimeout(timer);
      resolve(window.Plotly);
    };
    const onError = () => {
      clearTimeout(timer);
      reject(new Error("Failed to load Plotly from the CDN."));
    };
    const timer = setTimeout(() => {
      script.removeEventListener("load", onLoad);
      script.removeEventListener("error", onError);
      reject(new Error("Timed out waiting for Plotly to load."));
    }, 15000);
    script.addEventListener("load", onLoad);
    script.addEventListener("error", onError);
    document.head.appendChild(script);
  });
}

/**
 * Mount the PCA tab into `root` (the #pca-root element).
 */
export function mountPca(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = {
    scope: "All",
    team: "",
    wrestler: "",
    layout: "Matches",
    items: [],
    running: false,
    jobId: "",
    handlersBound: false,
  };

  // ---------- Right column: player + plot ----------

  const player = videoPlayer({});

  const plotEl = el("div", { class: "pca-plot" });

  // ---------- Left column: controls ----------

  const scopeSelect = el("select", { class: "combo-input" }, [
    el("option", { value: "All", text: "All" }),
    el("option", { value: "Team", text: "Team" }),
    el("option", { value: "Wrestler", text: "Wrestler" }),
  ]);
  const teamSelect = el("select", { class: "combo-input" });
  const wrestlerSelect = el("select", { class: "combo-input" });
  const layoutSelect = el("select", { class: "combo-input" }, [
    el("option", { value: "Matches", text: "Matches" }),
    el("option", { value: "Sequences", text: "Sequences" }),
  ]);

  const scopeRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Scope:" }),
    scopeSelect,
  ]);
  const teamRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Team:" }),
    teamSelect,
  ]);
  const wrestlerRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Wrestler:" }),
    wrestlerSelect,
  ]);
  const layoutRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Layout:" }),
    layoutSelect,
  ]);

  const streamCopy = el("input", { type: "checkbox", checked: "" });
  const streamCopyRow = el("label", { class: "check-label", text: "Fast extraction (stream copy)" }, [streamCopy]);

  const loadPlotBtn = el("button", { class: "btn", type: "button", text: "Load Plot" });
  const compileBtn = el("button", { class: "btn", type: "button", text: "Compile Reel", disabled: "" });

  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Load a plot to begin." });
  const outputEl = el("pre", { class: "error-display", hidden: "" });
  const playReelBtn = el("button", { class: "btn", type: "button", text: "Play Reel", hidden: "" });
  const downloadLink = el("a", { class: "btn download-link", text: "Download", hidden: "" });
  const reelActions = el("div", { class: "row" }, [playReelBtn, downloadLink]);

  const selectionList = el("ul", { class: "results-list" });
  const selectionCount = el("span", { text: "0" });

  /**
   * Update the status label.
   */
  function setStatus(text) {
    statusLabel.textContent = text;
  }

  /**
   * Show/hide the output pre with the given text.
   */
  function setOutput(text) {
    outputEl.hidden = !text;
    outputEl.textContent = text;
  }

  /**
   * Enable/disable buttons based on selection + running state.
   */
  function updateButtons() {
    compileBtn.disabled = state.items.length === 0 || state.running;
  }

  /**
   * Toggle the running state (progress bar + buttons).
   */
  function setRunning(active) {
    state.running = active;
    progress.hidden = !active;
    if (!active) progress.value = 0;
    updateButtons();
  }

  // ---------- Selection ----------

  /**
   * Re-render the selection list and count from state.items.
   */
  function renderSelection() {
    selectionCount.textContent = String(state.items.length);
    selectionList.replaceChildren();
    for (const item of state.items) {
      const label = `${item.video}  [${formatClock(item.start_time)}-${formatClock(item.end_time)}]`;
      const btn = el("button", { class: "video-item", type: "button", text: label });
      btn.addEventListener("click", () => previewItem(item));
      selectionList.appendChild(el("li", {}, [btn]));
    }
    updateButtons();
  }

  /**
   * Load a preview of the given item's video and seek to its start time.
   */
  async function previewItem(item) {
    setStatus(`Loading preview: ${item.video}...`);
    try {
      const url = await ensurePreview("taged", item.video);
      player.load(url, item.start_time);
      setStatus(`Previewing: ${item.video} [${formatClock(item.start_time)}]`);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
  }

  // ---------- Plot ----------

  /**
   * Show/hide the team/wrestler selects based on the current scope.
   */
  function updateScopeVisibility() {
    teamRow.hidden = state.scope !== "Team";
    wrestlerRow.hidden = state.scope !== "Wrestler";
  }

  /**
   * The optional name query param for the current scope.
   */
  function scopeName() {
    if (state.scope === "Team") return state.team;
    if (state.scope === "Wrestler") return state.wrestler;
    return "";
  }

  /**
   * Fetch and render the PCA plot for the current controls.
   */
  async function loadPlot() {
    let Plotly;
    try {
      Plotly = await ensurePlotly();
    } catch (err) {
      showToast("Plotly failed to load (vendored file missing and CDN unreachable).", "error");
      setStatus("Plotly failed to load.");
      return;
    }

    let url = `/api/pca?scope=${encodeURIComponent(state.scope.toLowerCase())}`;
    const name = scopeName();
    if (name) url += `&name=${encodeURIComponent(name)}`;
    url += `&layout=${encodeURIComponent(state.layout.toLowerCase())}`;

    setStatus("Loading plot...");
    const { ok, body } = await fetchJson(url);
    if (!ok) {
      const message = body.detail || "Could not load plot.";
      showToast(message, "error");
      setStatus(message);
      return;
    }

    const fig = JSON.parse(body.fig_json);
    Plotly.newPlot(plotEl, fig.data, fig.layout, { responsive: true });

    if (!state.handlersBound) {
      plotEl.on("plotly_selected", (event) => {
        state.items = (event.points || [])
          .filter((p) => p.customdata)
          .map((p) => ({
            wrestler: p.customdata[0],
            video: p.customdata[1],
            start_time: p.customdata[2],
            end_time: p.customdata[3],
          }));
        renderSelection();
      });

      plotEl.on("plotly_click", (event) => {
        const p = (event.points || [])[0];
        if (!p || !p.customdata) return;
        previewItem({
          wrestler: p.customdata[0],
          video: p.customdata[1],
          start_time: p.customdata[2],
          end_time: p.customdata[3],
        });
      });

      plotEl.on("plotly_deselect", () => {
        state.items = [];
        renderSelection();
      });

      state.handlersBound = true;
    }

    setStatus(
      `Loaded ${body.n_points} points (off. ${(body.off_variance * 100).toFixed(1)}% / def. ${(body.def_variance * 100).toFixed(1)}%)`
    );
    state.items = [];
    renderSelection();
  }

  // ---------- Actions ----------

  /**
   * Compile the selected points into a highlight reel via /api/pca/compile.
   */
  async function compileReel() {
    if (state.items.length === 0) return;

    setRunning(true);
    setOutput("");
    reelActions.hidden = true;
    setStatus("Compiling reel...");
    try {
      const { ok, body } = await fetchJson("/api/pca/compile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `${state.scope}-${state.layout}`.toLowerCase(),
          items: state.items,
          use_stream_copy: streamCopy.checked,
        }),
      });
      if (!ok) throw new Error(body.detail || "Could not start reel compilation");
      state.jobId = body.job_id;
      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      const result = job.result || {};
      setStatus(`Reel created: ${result.output}`);
      showToast(`Created: ${result.output}`, "success");
      setOutput(`${result.output}\n${result.segments} segments`);
      if (result.errors && result.errors.length) setOutput(`${result.output}\n${result.errors.join("\n")}`);
      downloadLink.href = `/api/clips/${encodeURIComponent(result.output)}`;
      reelActions.hidden = false;
    } catch (err) {
      showToast(err.message || "Reel compilation failed.", "error");
      setStatus("Failed to create reel.");
    } finally {
      state.jobId = "";
      setRunning(false);
    }
  }

  // ---------- Wiring ----------

  scopeSelect.addEventListener("change", () => {
    state.scope = scopeSelect.value;
    updateScopeVisibility();
    loadPlot();
  });
  teamSelect.addEventListener("change", () => {
    state.team = teamSelect.value;
    loadPlot();
  });
  wrestlerSelect.addEventListener("change", () => {
    state.wrestler = wrestlerSelect.value;
    loadPlot();
  });
  layoutSelect.addEventListener("change", () => {
    state.layout = layoutSelect.value;
    loadPlot();
  });
  loadPlotBtn.addEventListener("click", loadPlot);
  compileBtn.addEventListener("click", compileReel);

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

  /**
   * Populate the team/wrestler selects from the shared config.
   */
  async function loadConfigs() {
    const { ok, body } = await fetchJson("/api/config");
    if (!ok) return;
    teamSelect.replaceChildren();
    for (const name of Object.keys(body.teams || {})) {
      teamSelect.appendChild(el("option", { value: name, text: name }));
    }
    wrestlerSelect.replaceChildren();
    for (const name of body.wrestlers || []) {
      wrestlerSelect.appendChild(el("option", { value: name, text: name }));
    }
  }

  // ---------- Layout ----------

  const controlsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Controls" }),
    scopeRow,
    teamRow,
    wrestlerRow,
    layoutRow,
    streamCopyRow,
    el("div", { class: "row" }, [loadPlotBtn]),
  ]);
  const selectionCard = el("fieldset", { class: "tag-card" }, [
    el("legend", {}, [el("span", { text: "Selected: " }), selectionCount]),
    selectionList,
  ]);
  const actionsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Actions" }),
    el("div", { class: "row" }, [compileBtn]),
    progress,
    statusLabel,
    outputEl,
    reelActions,
  ]);
  const left = el("div", { class: "tag-left" }, [controlsCard, selectionCard, actionsCard]);

  const playerCard = el("section", { class: "tag-card" }, [player.node]);
  const plotCard = el("section", { class: "tag-card" }, [plotEl]);
  const right = el("div", { class: "tag-right" }, [playerCard, plotCard]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  updateScopeVisibility();
  window.addEventListener("configs-updated", loadConfigs);
  loadConfigs();
  loadPlot();
}
