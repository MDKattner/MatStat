/**
 * Tabulate & Report tab — combines the compile-stats (tabulation) and team
 * evaluation (report) workflows into one place.
 *
 * Mounts into #team-eval-root. Card 1 queues a compile_stats job via POST
 * /api/compile-stats and polls it, listing per-wrestler sequence counts and any
 * processing errors. Card 2 queues a team_eval job via POST /api/team-eval and
 * polls it, then exposes a download link for /api/reports/Team_Stats.xlsx.
 */

import { el, fetchJson, pollJob, showToast } from "../components.js";

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
        : `\u26A0 ${name}: 0 sequences (nothing stored)`;
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
      window.dispatchEvent(new CustomEvent("stats-updated"));
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
  const freshnessLabel = el("p", { class: "status-label", text: "" });
  const downloadLink = el("a", {
    class: "btn download-link",
    text: "Download latest report",
    hidden: "",
  });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function showLatest(info) {
    if (!info) {
      freshnessLabel.textContent = "No report generated yet.";
      downloadLink.hidden = true;
      return;
    }
    const stamp = info.generated_at || new Date(info.mtime * 1000).toISOString();
    const ruleset = info.active_ruleset || "unknown ruleset";
    freshnessLabel.textContent =
      `Generated ${stamp} \u00b7 ${info.wrestlers || 0} wrestlers \u00b7 ${ruleset}`;
    downloadLink.href = `/api/reports/${encodeURIComponent(info.latest || info.filename)}`;
    downloadLink.textContent = `Download ${info.filename}`;
    downloadLink.hidden = false;
  }

  async function refreshLatest() {
    try {
      const { ok, body } = await fetchJson("/api/reports/latest");
      showLatest(ok ? body : null);
    } catch {
      showLatest(null);
    }
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
      if (result.cancelled) {
        setStatus("Report generation cancelled; previous report kept.");
      } else if (result.partial || errors.length) {
        setStatus(
          `Report incomplete (${(result.failed_sheets || []).length || errors.length} ` +
          "failed sheet(s)); previous report kept."
        );
        showToast("Report incomplete; previous report kept.", "warning");
        downloadLink.hidden = true;
      } else {
        setStatus(`Report generated: ${result.output}`);
        downloadLink.href =
          `/api/reports/${encodeURIComponent(result.latest || result.output)}`;
        downloadLink.textContent = `Download ${result.output}`;
        downloadLink.hidden = false;
      }
      await refreshLatest();
    } catch (err) {
      showToast(err.message || "Report generation failed.", "error");
      setStatus("Report generation failed.");
    } finally {
      state.jobId = "";
      setRunning(false);
    }
  }

  generateBtn.addEventListener("click", start);
  refreshLatest();

  return el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Generate Report" }),
    el("p", { class: "shortcut-hint", text: "Build a timestamped Team_Stats workbook from the compiled sequence data." }),
    el("div", { class: "row" }, [generateBtn, downloadLink]),
    freshnessLabel,
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
