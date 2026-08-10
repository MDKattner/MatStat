/**
 * Tag Film tab — the web port of the original Qt tag film widget.
 *
 * Mounts into #tag-film-root. Mirrors the Qt widget's flow: pick an untagged
 * video, preview it (transcoding on demand), tag sequences with move/score
 * selectors, and embed them as ffmpeg chapters via POST /api/tag (a
 * background job whose progress is polled).
 */

import {
  checkList,
  confirmModal,
  el,
  ensurePreview,
  filterList,
  pollJob,
  showToast,
  timeInput,
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

export function mountTagFilm(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = {
    video: "",
    videoConfirmed: false,
    wrestler: "",
    opponent: "",
    matchResult: "",
    matchDate: "",
    sequences: [],
    tagging: false,
    playerPosMs: 0,
  };
  let wrestlerNames = [];

  // ---------- Right column: video selector + player ----------

  const videoList = filterList({
    placeholder: "Type to filter videos...",
    onChange: (fileName) => {
      state.video = fileName;
      setStatus(`Selected video: ${fileName}`);
      updateButtons();
      updateVideoLock();
      loadPreview(fileName);
    },
  });

  const changeBtn = el("button", {
    class: "btn btn-ghost",
    type: "button",
    text: "Change Video",
    hidden: "",
  });

  // ---------- Upload ----------

  const uploadInput = el("input", { type: "file", multiple: "", class: "file-input" });
  const uploadBtn = el("button", {
    class: "btn btn-ghost",
    type: "button",
    text: "Upload",
    disabled: "",
  });
  const dropHint = el("span", { class: "drop-hint", text: "or drop video files here" });
  const uploadRow = el("div", { class: "row upload-row" }, [uploadInput, uploadBtn, dropHint]);

  async function uploadVideos(files) {
    const list = files || uploadInput.files;
    if (!list || list.length === 0) {
      showToast("Select videos to upload first.", "info");
      return;
    }
    uploadBtn.disabled = true;
    setStatus(`Uploading ${list.length} video(s)...`);
    const formData = new FormData();
    for (const file of list) formData.append("files", file, file.name);
    try {
      const { ok, body } = await fetchJson("/api/videos", { method: "POST", body: formData });
      if (!ok) throw new Error(body.detail || "Upload failed.");
      const results = body.results || [];
      const good = results.filter((r) => r.status === "ok").length;
      const conflicts = results.filter((r) => r.status === "conflict");
      const errors = results.filter((r) => r.status === "error");
      setStatus(`Uploaded ${good} of ${results.length} video(s).`);
      for (const r of conflicts) showToast(`${r.file}: already exists.`, "info");
      for (const r of errors) showToast(`${r.file}: ${r.detail || "failed."}`, "error");
      if (good > 0) showToast(`Uploaded ${good} video(s).`, "success");
      uploadInput.value = "";
      await loadVideos();
    } catch (err) {
      showToast(err.message || "Upload failed.", "error");
      setStatus("Upload failed.");
    } finally {
      uploadBtn.disabled = !(uploadInput.files && uploadInput.files.length > 0);
    }
  }

  let dragDepth = 0;
  uploadRow.addEventListener("dragover", (event) => {
    event.preventDefault();
    uploadRow.classList.add("dragover");
  });
  uploadRow.addEventListener("dragenter", (event) => {
    event.preventDefault();
    dragDepth++;
    uploadRow.classList.add("dragover");
  });
  uploadRow.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) uploadRow.classList.remove("dragover");
  });
  uploadRow.addEventListener("drop", (event) => {
    event.preventDefault();
    dragDepth = 0;
    uploadRow.classList.remove("dragover");
    const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
    if (files.length > 0) uploadVideos(files);
  });

  uploadInput.addEventListener("change", () => {
    uploadBtn.disabled = !(uploadInput.files && uploadInput.files.length > 0);
  });
  uploadBtn.addEventListener("click", () => uploadVideos());

  const player = videoPlayer({
    onPosition: (ms) => { state.playerPosMs = ms; },
  });

  async function loadVideos() {
    const { ok, body } = await fetchJson("/api/videos/untaged");
    if (!ok) {
      showToast("Could not load video list.", "error");
      return;
    }
    videoList.setItems(body.files || []);
  }

  async function loadPreview(fileName) {
    try {
      const url = await ensurePreview("untaged", fileName);
      player.load(url);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
  }

  function updateVideoLock() {
    const locked = state.videoConfirmed;
    changeBtn.hidden = !locked;
    videoList.setDisabled(locked);
  }

  // ---------- Wrestler selector (right column, next to video) ----------

  const wrestlerList = filterList({
    placeholder: "Type to filter wrestlers...",
    onChange: (name) => {
      state.wrestler = name;
      setStatus(`Selected wrestler: ${name}`);
      if (state.opponent === name) {
        state.opponent = "";
        opponentList.clear();
      }
      opponentList.setItems(wrestlerNames.filter((w) => w !== name));
      updateTitlePreview();
      updateButtons();
    },
  });

  const opponentList = filterList({
    placeholder: "Type to filter opponents...",
    onChange: (name) => {
      state.opponent = name;
      setStatus(state.opponent ? `Opponent: ${name}` : `Selected wrestler: ${state.wrestler}`);
      updateDualMode();
      updateButtons();
    },
  });

  // Match result: "No Result" (default / draw), Win, or Loss. Draws are skipped.
  const noResultRadio = el("input", { type: "radio", name: "tag-result", value: "", checked: "", hidden: "" });
  const winRadio = el("input", { type: "radio", name: "tag-result", value: "W", hidden: "" });
  const lossRadio = el("input", { type: "radio", name: "tag-result", value: "L", hidden: "" });
  const noResultBtn = el("button", { class: "seg-option active", type: "button", text: "No Result" });
  const winBtn = el("button", { class: "seg-option", type: "button", text: "Win" });
  const lossBtn = el("button", { class: "seg-option", type: "button", text: "Loss" });
  const matchDateInput = el("input", { type: "date", class: "time-input" });

  function setResult(value) {
    noResultRadio.checked = value === "";
    winRadio.checked = value === "W";
    lossRadio.checked = value === "L";
    noResultBtn.classList.toggle("active", value === "");
    winBtn.classList.toggle("active", value === "W");
    lossBtn.classList.toggle("active", value === "L");
    state.matchResult = value;
    updateTitlePreview();
  }

  noResultBtn.addEventListener("click", () => setResult(""));
  winBtn.addEventListener("click", () => setResult("W"));
  lossBtn.addEventListener("click", () => setResult("L"));
  const resultRow = el("div", { class: "seg-toggle" }, [
    noResultRadio, winRadio, lossRadio, noResultBtn, winBtn, lossBtn,
  ]);

  function buildTitle(wrestler, opponent, result) {
    if (!wrestler) return "";
    if (opponent) {
      if (result === "W") return `${wrestler} (W) / ${opponent}`;
      if (result === "L") return `${wrestler} / ${opponent} (W)`;
      return `${wrestler} / ${opponent}`;
    }
    if (result === "W") return `${wrestler} (W)`;
    if (result === "L") return `${wrestler} (L)`;
    return wrestler;
  }

  const titlePreview = el("p", { class: "status-label", text: "" });

  function updateTitlePreview() {
    titlePreview.textContent = state.wrestler
      ? `Will log as: ${buildTitle(state.wrestler, state.opponent, state.matchResult)}`
      : "";
  }

  const startTime = timeInput({ label: "Start:" });
  const endTime = timeInput({ label: "End:" });

  const attackRadio = el("input", {
    type: "radio", name: "tag-attack-defend", value: "attack", checked: "", hidden: "",
  });
  const defendRadio = el("input", {
    type: "radio", name: "tag-attack-defend", value: "defend", hidden: "",
  });
  const attackBtn = el("button", { class: "seg-option active", type: "button", text: "Attacking" });
  const defendBtn = el("button", { class: "seg-option", type: "button", text: "Defending" });
  attackBtn.addEventListener("click", () => {
    attackRadio.checked = true;
    attackBtn.classList.add("active");
    defendBtn.classList.remove("active");
  });
  defendBtn.addEventListener("click", () => {
    defendRadio.checked = true;
    defendBtn.classList.add("active");
    attackBtn.classList.remove("active");
  });
  const attackRow = el("div", { class: "seg-toggle" }, [attackRadio, defendRadio, attackBtn, defendBtn]);

  const tie = filterList({ label: "Starting Tie/Position:", items: [] });
  const oppTie = filterList({ label: "Opponent Tie/Position:", items: [] });
  oppTie.node.hidden = true;

  function updateDualMode() {
    const dual = Boolean(state.opponent);
    oppTie.node.hidden = !dual;
    if (!dual) oppTie.clear();
    updateTitlePreview();
  }
  const ourMoves = checkList({ label: "Main Wrestler Moves", items: [], counts: true });
  const oppMoves = checkList({ label: "Opp. Moves", items: [], counts: true });
  const ourScores = checkList({ label: "Main Wrestler Scores", items: [], counts: true });
  const oppScores = checkList({ label: "Opp. Scores", items: [], counts: true });

  const addBtn = el("button", { class: "btn", type: "button", text: "Add Sequence", disabled: "" });
  const finishBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Finish & Log Match", disabled: "" });
  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Select a video and wrestler to begin." });

  const seqCount = el("span", { text: "0" });
  const seqListEl = el("ul", { class: "seq-list" });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function resetDetails() {
    attackRadio.checked = true;
    attackBtn.classList.add("active");
    defendBtn.classList.remove("active");
    tie.clear();
    oppTie.clear();
    ourMoves.clear();
    oppMoves.clear();
    ourScores.clear();
    oppScores.clear();
  }

  function updateButtons() {
    const canTag = Boolean(state.video && state.wrestler && !state.tagging);
    addBtn.disabled = !canTag;
    finishBtn.disabled = !canTag || state.sequences.length === 0;
  }

  async function loadConfigs() {
    try {
      const { ok, body } = await fetchJson("/api/config");
      if (!ok) {
        throw new Error(`Config returned status ${body.status || "error"}`);
      }
      wrestlerNames = body.wrestlers || [];
      wrestlerList.setItems(wrestlerNames);
      wrestlerList.setTeams(body.teams || {});
      opponentList.setItems(wrestlerNames.filter((w) => w !== state.wrestler));
      opponentList.setTeams(body.teams || {});
      tie.setItems(body.ties || []);
      oppTie.setItems(body.ties || []);
      ourMoves.setItems(body.moves || []);
      oppMoves.setItems(body.moves || []);
      const ruleset = (body.rulesets || {})[body.active_ruleset] || {};
      const outcomes = Object.keys(ruleset.outcomes || {});
      ourScores.setItems(outcomes);
      oppScores.setItems(outcomes);
    } catch (err) {
      console.error("Failed to load configs", err);
      showToast(`Could not load config lists: ${err.message}`, "error");
    }
  }

  function currentSequence() {
    let teamMoves = ourMoves.selected();
    if (teamMoves.length === 0) teamMoves = ["nothing"];
    let oppMovesSel = oppMoves.selected();
    if (oppMovesSel.length === 0) oppMovesSel = ["nothing"];
    let teamScores = ourScores.selected();
    if (teamScores.length === 0) teamScores = ["None"];
    let oppScoresSel = oppScores.selected();
    if (oppScoresSel.length === 0) oppScoresSel = ["None"];

    return {
      start_time: startTime.getSeconds(),
      end_time: endTime.getSeconds(),
      attack_defend: attackRadio.checked,
      tie_up: tie.selected,
      opp_tie: state.opponent ? oppTie.selected : "",
      team_moves: teamMoves,
      op_moves: oppMovesSel,
      team_scores: teamScores,
      op_scores: oppScoresSel,
    };
  }

  function tieLabel(seq) {
    return seq.opp_tie ? `${seq.tie_up}:${seq.opp_tie}` : seq.tie_up;
  }

  function prettyChapter(seq) {
    return [
      "Sequence Info",
      `Start Time\t: ${formatClock(seq.start_time)}`,
      `End Time\t: ${formatClock(seq.end_time)}`,
      `Attacking\t: ${seq.attack_defend}`,
      `Tie/Position\t: ${tieLabel(seq)}`,
      `Main wrestler moves\t: ${seq.team_moves.join(", ")}`,
      `Opponent moves\t: ${seq.op_moves.join(", ")}`,
      `Main wrestler scoring\t: ${seq.team_scores.join(", ")}`,
      `Opponent scoring\t: ${seq.op_scores.join(", ")}`,
    ].join("\n");
  }

  function commitSequence(seq) {
    state.sequences.push(seq);
    seqListEl.appendChild(el("li", {
      class: "seq-item",
      title: prettyChapter(seq),
      text: `[${formatClock(seq.start_time)} - ${formatClock(seq.end_time)}] ${seq.attack_defend ? "A" : "D"} | ${tieLabel(seq)}`,
    }));
    seqCount.textContent = String(state.sequences.length);
    state.videoConfirmed = true;
    updateVideoLock();
    resetDetails();
    setStatus(`Added sequence. ${state.sequences.length} sequences logged.`);
    updateButtons();
  }

  function showChapterPreview(seq) {
    const dialog = el("dialog", { class: "modal chapter-modal" });
    dialog.appendChild(el("h3", { text: "Sequence Review" }));
    dialog.appendChild(el("pre", { class: "chapter-preview", text: prettyChapter(seq) }));
    const redoBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Re-do Sequence" });
    const newBtn = el("button", { class: "btn", type: "button", text: "New Sequence" });
    dialog.appendChild(el("div", { class: "row dialog-actions" }, [redoBtn, newBtn]));
    redoBtn.addEventListener("click", () => { dialog.close(); dialog.remove(); });
    newBtn.addEventListener("click", () => {
      dialog.close();
      dialog.remove();
      commitSequence(seq);
    });
    document.body.appendChild(dialog);
    dialog.showModal();
  }

  function addSequence() {
    const start = startTime.getSeconds();
    const end = endTime.getSeconds();
    if (start >= end) {
      showToast("Start time must be before end time.", "error");
      return;
    }
    const last = state.sequences[state.sequences.length - 1];
    if (last && start < last.end_time) {
      showToast("Start time must be at or after the previous sequence's end.", "error");
      return;
    }
    showChapterPreview(currentSequence());
  }

  // ---------- Finish & tag ----------

  function setTagging(active) {
    state.tagging = active;
    progress.hidden = !active;
    if (active) {
      progress.removeAttribute("value");
    } else {
      progress.value = 0;
    }
    updateVideoLock();
    updateButtons();
  }

  async function finishTagging() {
    if (state.sequences.length === 0) {
      showToast("No sequences were added.", "error");
      return;
    }
    if (!state.video || !state.wrestler) {
      showToast("Select a video and wrestler first.", "error");
      return;
    }

    setTagging(true);
    setStatus("Logging match...");
    try {
      const payload = {
        video: state.video,
        wrestler: state.wrestler,
        opponent: state.opponent,
        match_result: state.matchResult,
        match_date: matchDateInput.value,
        sequences: state.sequences,
      };
      const { ok, body } = await fetchJson("/api/tag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!ok) throw new Error(body.detail || "Could not start logging");

      const job = await pollJob(body.job_id, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      setStatus(`Logged: ${job.result.output}`);
      showToast(`Match logged successfully! Output: ${job.result.output}`, "success");
      state.sequences = [];
      seqListEl.replaceChildren();
      seqCount.textContent = "0";
      state.video = "";
      state.videoConfirmed = false;
      state.opponent = "";
      state.matchResult = "";
      matchDateInput.value = "";
      opponentList.clear();
      setResult("");
      updateVideoLock();
      await loadVideos();
    } catch (err) {
      showToast(err.message || "Logging failed.", "error");
      setStatus("Logging failed.");
    } finally {
      setTagging(false);
    }
  }

  // ---------- Wiring ----------

  changeBtn.addEventListener("click", async () => {
    if (state.tagging) return;
    if (state.sequences.length > 0) {
      const confirmed = await confirmModal({
        title: "Change video",
        message: "Changing the video clears the logged sequences. Continue?",
      });
      if (!confirmed) return;
    }
    state.videoConfirmed = false;
    state.sequences = [];
    seqListEl.replaceChildren();
    seqCount.textContent = "0";
    setStatus(`Selected video: ${state.video}`);
    updateVideoLock();
    updateButtons();
  });
  startTime.markBtn.addEventListener("click", () => {
    startTime.setFromSeconds(Math.floor(state.playerPosMs / 1000));
  });
  endTime.markBtn.addEventListener("click", () => {
    endTime.setFromSeconds(Math.floor(state.playerPosMs / 1000));
  });
  addBtn.addEventListener("click", addSequence);
  finishBtn.addEventListener("click", finishTagging);

  // Keyboard shortcuts: Space = play/pause, S = mark start, E = mark end,
  // Enter = add sequence. Ignored while typing in inputs and when this tab
  // is hidden (all tabs stay mounted).
  document.addEventListener("keydown", (event) => {
    if (state.tagging) return;
    const pane = root.closest(".tab-pane");
    if (!pane || pane.hidden) return;
    const target = event.target;
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" || target.isContentEditable)) return;
    if (event.key === " ") {
      event.preventDefault();
      player.togglePlay();
    } else if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      startTime.setFromSeconds(Math.floor(state.playerPosMs / 1000));
    } else if (event.key === "e" || event.key === "E") {
      event.preventDefault();
      endTime.setFromSeconds(Math.floor(state.playerPosMs / 1000));
    } else if (event.key === "Enter" && document.activeElement === document.body) {
      event.preventDefault();
      addSequence();
    }
  });

  // ---------- Layout ----------

  const videoCard = el("section", { class: "tag-card video-card" }, [
    el("h3", { text: "Select Video" }),
    uploadRow,
    videoList.node,
    changeBtn,
  ]);
  const wrestlerCard = el("section", { class: "tag-card wrestler-card" }, [
    el("h3", { text: "Main Wrestler" }),
    wrestlerList.node,
    el("h3", { class: "subhead", text: "Opponent (optional)" }),
    opponentList.node,
    el("div", { class: "row result-row" }, [
      el("label", { class: "field-label", text: "Result:" }),
      resultRow,
    ]),
    el("div", { class: "row result-row" }, [
      el("label", { class: "field-label", text: "Match date:" }),
      matchDateInput,
    ]),
    titlePreview,
  ]);
  const selectionRow = el("div", { class: "selection-row" }, [videoCard, wrestlerCard]);
  const playerCard = el("section", { class: "tag-card player-card" }, [player.node]);
  const right = el("div", { class: "tag-right" }, [playerCard, selectionRow]);

  const timingCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Timing" }),
    startTime.node,
    endTime.node,
    el("p", { class: "shortcut-hint", text: "Space play/pause \u00b7 S start \u00b7 E end \u00b7 Enter add" }),
  ]);
  const detailsCard = el("details", { class: "tag-card", open: "" }, [
    el("summary", { text: "Sequence Details" }),
    attackRow,
    el("div", { class: "row tie-row" }, [tie.node, oppTie.node]),
    el("div", { class: "details-grid" }, [
      ourMoves.node,
      oppMoves.node,
      ourScores.node,
      oppScores.node,
    ]),
  ]);
  const actionsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Actions" }),
    el("div", { class: "row" }, [addBtn, finishBtn]),
    progress,
    statusLabel,
  ]);
  const listCard = el("details", { class: "tag-card", open: "" }, [
    el("summary", {}, [el("span", { text: "Logged Sequences: " }), seqCount]),
    seqListEl,
  ]);
  const left = el("div", { class: "tag-left" }, [
    timingCard,
    detailsCard,
    actionsCard,
    listCard,
  ]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  window.addEventListener("configs-updated", loadConfigs);
  updateVideoLock();
  loadVideos();
  loadConfigs();
}
