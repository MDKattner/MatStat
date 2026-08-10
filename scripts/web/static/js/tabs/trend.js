/**
 * Trends tab — per-match points over a wrestler's season.
 *
 * Mounts into #trend-root. Pick a wrestler, a metric (net or adjusted points),
 * and a rolling-average window, then load the Plotly line chart. Rows without
 * a recorded match date are skipped and reported in the status line.
 */

import { el, showToast } from "../components.js";

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
 * into the document head and waits for it to load.
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
 * Mount the Trends tab into `root` (the #trend-root element).
 */
export function mountTrend(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = {
    wrestler: "",
    metric: "net",
    window: 5,
  };

  // ---------- Controls ----------

  const wrestlerSelect = el("select", { class: "combo-input" });
  const metricSelect = el("select", { class: "combo-input" }, [
    el("option", { value: "net", text: "Net points" }),
    el("option", { value: "adjusted", text: "Adjusted points" }),
  ]);
  const windowInput = el("input", {
    type: "number",
    class: "combo-input",
    min: "1",
    value: "5",
  });

  const wrestlerRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Wrestler:" }),
    wrestlerSelect,
  ]);
  const metricRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Metric:" }),
    metricSelect,
  ]);
  const windowRow = el("div", { class: "row" }, [
    el("label", { class: "field-label", text: "Rolling avg window:" }),
    windowInput,
  ]);

  const loadPlotBtn = el("button", { class: "btn", type: "button", text: "Load Chart" });
  const statusLabel = el("p", { class: "status-label", text: "Load a chart to begin." });
  const plotEl = el("div", { class: "pca-plot" });

  /**
   * Update the status label.
   */
  function setStatus(text) {
    statusLabel.textContent = text;
  }

  // ---------- Plot ----------

  /**
   * Fetch and render the trend figure for the current controls.
   */
  async function loadPlot() {
    if (!state.wrestler) {
      setStatus("Select a wrestler.");
      return;
    }
    let Plotly;
    try {
      Plotly = await ensurePlotly();
    } catch (err) {
      showToast("Plotly failed to load (vendored file missing and CDN unreachable).", "error");
      setStatus("Plotly failed to load.");
      return;
    }

    const windowValue = parseInt(windowInput.value, 10);
    const url =
      `/api/trend?wrestler=${encodeURIComponent(state.wrestler)}` +
      `&metric=${encodeURIComponent(state.metric)}` +
      `&window=${Number.isFinite(windowValue) ? Math.max(1, windowValue) : 1}`;

    setStatus("Loading chart...");
    const { ok, body } = await fetchJson(url);
    if (!ok) {
      const message = body.detail || "Could not load chart.";
      showToast(message, "error");
      setStatus(message);
      return;
    }

    const fig = JSON.parse(body.fig_json);
    Plotly.newPlot(plotEl, fig.data, fig.layout, { responsive: true });

    const skipped =
      body.skipped_rows > 0 ? ` (${body.skipped_rows} rows without a date skipped)` : "";
    setStatus(
      `${body.n_matches} matches plotted (${body.metric}, ${body.window}-match avg)${skipped}`
    );
  }

  // ---------- Wiring ----------

  wrestlerSelect.addEventListener("change", () => {
    state.wrestler = wrestlerSelect.value;
    loadPlot();
  });
  metricSelect.addEventListener("change", () => {
    state.metric = metricSelect.value;
    loadPlot();
  });
  windowInput.addEventListener("change", loadPlot);
  loadPlotBtn.addEventListener("click", loadPlot);

  /**
   * Populate the wrestler select from the compiled-data listing.
   */
  async function loadWrestlers() {
    const { ok, body } = await fetchJson("/api/search/wrestlers");
    if (!ok) return;
    wrestlerSelect.replaceChildren();
    for (const name of body.items || []) {
      wrestlerSelect.appendChild(el("option", { value: name, text: name }));
    }
    if (body.items && body.items.length > 0) {
      state.wrestler = body.items[0];
      wrestlerSelect.value = state.wrestler;
      loadPlot();
    }
  }

  // ---------- Layout ----------

  const controlsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Controls" }),
    wrestlerRow,
    metricRow,
    windowRow,
    el("div", { class: "row" }, [loadPlotBtn]),
    statusLabel,
  ]);
  const plotCard = el("section", { class: "tag-card" }, [plotEl]);
  const left = el("div", { class: "tag-left" }, [controlsCard]);
  const right = el("div", { class: "tag-right" }, [plotCard]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  window.addEventListener("configs-updated", loadWrestlers);
  loadWrestlers();
}
