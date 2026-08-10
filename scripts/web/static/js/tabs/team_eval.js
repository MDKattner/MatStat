/**
 * Tabulate & Report tab — combines the compile-stats (tabulation) and team
 * evaluation (report) workflows into one place.
 *
 * Mounts into #team-eval-root. Card 1 queues a compile_stats job via POST
 * /api/compile-stats and polls it, listing per-wrestler sequence counts and any
 * processing errors. Card 2 queues a team_eval job via POST /api/team-eval and
 * polls it, then exposes a download link for /api/reports/Team_Stats.xlsx.
 */

import { el, pollJob, showToast } from "../components.js";

async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, body };
}

const REPORT_FILE = "Team_Stats.xlsx";

/**
 * Tabulate Sequences card — ported from the former compile-stats tab.
 */
function buildTabulateCard() {
  const state = { running: false, jobId: "" };

  const tabulateBtn = el("button", { class: "btn", type: "button", text: "Tabulate All Logged Matches" });
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
    tabulateBtn.disabled = active;
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
      errorDisplay.appendChild(el("div", { text: "=== Tabulation Errors ===" }));
      for (const err of errors) errorDisplay.appendChild(el("div", { text: `  - ${err}` }));
      errorDisplay.hidden = false;
    } else {
      errorDisplay.hidden = true;
    }
  }

  async function start() {
    setRunning(true);
    setStatus("Starting tabulation...");
    resultsList.replaceChildren();
    errorDisplay.hidden = true;
    try {
      const { ok, body } = await fetchJson("/api/compile-stats", { method: "POST" });
      if (!ok) throw new Error(body.detail || "Could not start tabulation");
      state.jobId = body.job_id;
      const job = await pollJob(state.jobId, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      const result = job.result || {};
      renderResults(result);
      const processed = result.videos_processed ?? 0;
      if (processed === 0) {
        setStatus("No logged matches found.");
      } else {
        setStatus(`Complete. ${processed} videos processed.`);
      }
      const errors = result.errors || [];
      if (errors.length) showToast(`${errors.length} issue(s) reported.`, "warning");
    } catch (err) {
      showToast(err.message || "Tabulation failed.", "error");
      setStatus("Tabulation failed.");
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

  tabulateBtn.addEventListener("click", start);
  cancelBtn.addEventListener("click", cancel);

  return el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Tabulate Sequences" }),
    el("p", { class: "shortcut-hint", text: "Compile every logged match into per-wrestler sequence data." }),
    el("div", { class: "row" }, [tabulateBtn, cancelBtn]),
    progress,
    statusLabel,
    el("h3", { class: "results-title", text: "Results" }),
    resultsList,
    errorDisplay,
  ]);
}

/**
 * Generate Report card — ported from the former team-evaluation tab.
 */
function buildReportCard() {
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

  return el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Generate Report" }),
    el("p", { class: "shortcut-hint", text: "Build the Team_Stats.xlsx workbook from the compiled sequence data." }),
    el("div", { class: "row" }, [generateBtn, downloadLink]),
    progress,
    statusLabel,
    el("h3", { class: "results-title", text: "Results" }),
    resultsList,
    errorDisplay,
  ]);
}

export function mountTeamEval(root) {
  if (!root) return;
  root.replaceChildren();
  root.appendChild(el("div", { class: "compile-stats-card" }, [
    buildTabulateCard(),
    buildReportCard(),
  ]));
}
