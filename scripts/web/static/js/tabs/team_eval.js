/**
 * Team Evaluation tab — the web port of the original Qt team evaluation widget.
 *
 * Mounts into #team-eval-root. Queues a team_eval job via POST /api/team-eval
 * and polls it, listing per-wrestler data-load results and any errors. Once the
 * report is generated, a download link for /api/reports/Team_Stats.xlsx is shown.
 */

import { el, pollJob, showToast } from "../components.js";

async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

const REPORT_FILE = "Team_Stats.xlsx";

export function mountTeamEval(root) {
  if (!root) return;
  root.replaceChildren();

  const state = { running: false, jobId: "" };

  const generateBtn = el("button", { class: "btn", type: "button", text: "Generate Report" });
  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Ready" });
  const resultsList = el("ul", { class: "results-list" });
  const errorDisplay = el("pre", { class: "error-display", hidden: "" });
  const downloadLink = el("a", {
    class: "btn download-link",
    href: `/api/reports/${REPORT_FILE}`,
    text: `Download ${REPORT_FILE}`,
    hidden: "",
  });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function setRunning(active) {
    state.running = active;
    generateBtn.disabled = active;
    progress.hidden = !active;
    if (!active) progress.value = 0;
  }

  function renderResults(result) {
    resultsList.replaceChildren();
    for (const name of result.processed || []) {
      resultsList.appendChild(el("li", { class: "seq-item", text: `\u2713 ${name}: data loaded` }));
    }
    errorDisplay.replaceChildren();
    const errors = result.errors || [];
    if (errors.length) {
      errorDisplay.appendChild(el("div", { text: "=== Errors ===" }));
      for (const err of errors) errorDisplay.appendChild(el("div", { text: `  - ${err}` }));
      errorDisplay.hidden = false;
    } else {
      errorDisplay.hidden = true;
    }
  }

  async function start() {
    setRunning(true);
    setStatus("Starting report generation...");
    resultsList.replaceChildren();
    errorDisplay.hidden = true;
    downloadLink.hidden = true;
    try {
      const { ok, body } = await fetchJson("/api/team-eval", { method: "POST" });
      if (!ok) throw new Error(body.detail || "Could not start report generation");
      state.jobId = body.job_id;
      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      const result = job.result || {};
      renderResults(result);
      const errors = result.errors || [];
      if (errors.length) {
        setStatus(`Report generated with ${errors.length} issue(s).`);
        showToast(`${errors.length} issue(s) reported.`, "warning");
      } else {
        setStatus(`Report generated: ${result.output || REPORT_FILE}`);
      }
      downloadLink.hidden = false;
    } catch (err) {
      showToast(err.message || "Report generation failed.", "error");
      setStatus("Report generation failed.");
    } finally {
      state.jobId = "";
      setRunning(false);
    }
  }

  generateBtn.addEventListener("click", start);

  root.appendChild(el("div", { class: "compile-stats-card" }, [
    el("div", { class: "row" }, [generateBtn, downloadLink]),
    progress,
    statusLabel,
    el("h3", { class: "results-title", text: "Results" }),
    resultsList,
    errorDisplay,
  ]));
}
