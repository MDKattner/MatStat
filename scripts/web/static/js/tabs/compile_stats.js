/**
 * Compile Stats tab — the web port of the original Qt compile stats widget.
 *
 * Mounts into #compile-stats-root. Queues a compile_stats job via POST
 * /api/compile-stats and polls it, listing per-wrestler sequence counts and
 * any processing errors.
 */

import { el, pollJob, showToast } from "../components.js";

async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

export function mountCompileStats(root) {
  if (!root) return;
  root.replaceChildren();

  const state = { running: false, jobId: "" };

  const processBtn = el("button", { class: "btn", type: "button", text: "Process All Tagged Videos" });
  const cancelBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Cancel", disabled: "" });
  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Ready" });
  const resultsList = el("ul", { class: "results-list" });
  const errorDisplay = el("pre", { class: "error-display", hidden: "" });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function setRunning(active) {
    state.running = active;
    processBtn.disabled = active;
    cancelBtn.disabled = !active;
    progress.hidden = !active;
    if (!active) progress.value = 0;
  }

  function renderResults(result) {
    resultsList.replaceChildren();
    const wrestlers = result.wrestlers || {};
    for (const [name, count] of Object.entries(wrestlers)) {
      const text = count > 0
        ? `\u2713 ${name}: ${count} sequences`
        : `\u26A0 ${name}: 0 sequences (no file created)`;
      resultsList.appendChild(el("li", { class: "seq-item", text }));
    }
    errorDisplay.replaceChildren();
    const errors = result.errors || [];
    if (errors.length) {
      errorDisplay.appendChild(el("div", { text: "=== Processing Errors ===" }));
      for (const err of errors) errorDisplay.appendChild(el("div", { text: `  - ${err}` }));
      errorDisplay.hidden = false;
    } else {
      errorDisplay.hidden = true;
    }
  }

  async function start() {
    setRunning(true);
    setStatus("Starting compilation...");
    resultsList.replaceChildren();
    errorDisplay.hidden = true;
    try {
      const { ok, body } = await fetchJson("/api/compile-stats", { method: "POST" });
      if (!ok) throw new Error(body.detail || "Could not start compilation");
      state.jobId = body.job_id;
      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      const result = job.result || {};
      renderResults(result);
      const processed = result.videos_processed ?? 0;
      if (processed === 0) {
        setStatus("No tagged videos found.");
      } else {
        setStatus(`Complete. ${processed} videos processed.`);
      }
      const errors = result.errors || [];
      if (errors.length) showToast(`${errors.length} issue(s) reported.`, "warning");
    } catch (err) {
      showToast(err.message || "Compile failed.", "error");
      setStatus("Compile failed.");
    } finally {
      state.jobId = "";
      setRunning(false);
    }
  }

  async function cancel() {
    if (!state.jobId) return;
    const { ok, body } = await fetchJson(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    if (!ok) showToast(body.detail || "Could not cancel job.", "error");
  }

  processBtn.addEventListener("click", start);
  cancelBtn.addEventListener("click", cancel);

  const btnRow = el("div", { class: "row" }, [processBtn, cancelBtn]);

  root.appendChild(el("div", { class: "compile-stats-card" }, [
    btnRow,
    progress,
    statusLabel,
    el("h3", { class: "results-title", text: "Results" }),
    resultsList,
    errorDisplay,
  ]));
}
