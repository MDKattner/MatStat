/**
 * Tag Film tab — the web port of scripts/qt_app/tag_film_widget.py.
 *
 * Mounts into #tag-film-root. Mirrors the Qt widget's flow: pick an untagged
 * video, preview it (transcoding on demand), tag sequences with move/score
 * selectors, and embed them as ffmpeg chapters via POST /api/tag (a
 * background job whose progress is polled).
 */

import {
  checkList,
  configCombo,
  el,
  ensurePreview,
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
    wrestler: "",
    sequences: [],
    tagging: false,
    playerPosMs: 0,
  };

  // ---------- Right column: video selector + upload + player ----------

  const videoFilter = el("input", {
    class: "filter-input",
    type: "text",
    placeholder: "Type to filter videos...",
  });
  const videoListEl = el("ul", { class: "check-list video-list" });
  let videoFiles = [];

  const uploadInput = el("input", {
    type: "file",
    accept: ".mkv,.mp4,.avi,.mov,.webm, video/*",
  });
  const uploadBtn = el("button", {
    class: "btn",
    type: "button",
    text: "Upload",
  });

  const player = videoPlayer({
    onPosition: (ms) => { state.playerPosMs = ms; },
  });

  function renderVideos() {
    const text = videoFilter.value.toLowerCase();
    videoListEl.replaceChildren();
    for (const file of videoFiles) {
      if (!file.toLowerCase().includes(text)) continue;
      const item = el("li", {}, [
        el("button", {
          class: "video-item" + (file === state.video ? " active" : ""),
          type: "button",
          text: file,
          onclick: () => selectVideo(file),
        }),
      ]);
      videoListEl.appendChild(item);
    }
  }

  async function loadVideos() {
    const { ok, body } = await fetchJson("/api/videos/untaged");
    if (!ok) {
      showToast("Could not load video list.", "error");
      return;
    }
    videoFiles = body.files || [];
    renderVideos();
  }

  async function selectVideo(fileName) {
    state.video = fileName;
    renderVideos();
    setStatus(`Selected video: ${fileName}`);
    try {
      const url = await ensurePreview("untaged", fileName, setStatus);
      player.load(url);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
    updateButtons();
  }

  async function handleUpload() {
    const file = uploadInput.files[0];
    if (!file) return;
    setStatus("Uploading...");
    const form = new FormData();
    form.append("file", file);
    const { ok, body } = await fetchJson("/api/videos", { method: "POST", body: form });
    if (!ok) {
      showToast(body.detail || "Upload failed.", "error");
      setStatus("Upload failed.");
      return;
    }
    showToast(`Uploaded ${body.file}`, "success");
    uploadInput.value = "";
    await loadVideos();
    selectVideo(body.file);
  }

  // ---------- Left column: controls ----------

  const wrestler = configCombo({ label: "Wrestler:", items: [] });
  const startTime = timeInput({ label: "Start:" });
  const endTime = timeInput({ label: "End:" });

  const attackRadio = el("input", {
    type: "radio", name: "tag-attack-defend", value: "attack", checked: "",
  });
  const defendRadio = el("input", {
    type: "radio", name: "tag-attack-defend", value: "defend",
  });
  const attackRow = el("div", { class: "row radio-row" }, [
    el("label", { class: "check-label", text: "Attacking" }, [attackRadio]),
    el("label", { class: "check-label", text: "Defending" }, [defendRadio]),
  ]);

  const tie = configCombo({ label: "Starting Tie/Position:", items: [] });
  const ourMoves = checkList({ label: "Your Wrestler's Moves:", items: [], counts: true });
  const oppMoves = checkList({ label: "Opponent's Moves:", items: [], counts: true });
  const ourScores = checkList({ label: "Your Wrestler's Scores:", items: [], counts: true });
  const oppScores = checkList({ label: "Opponent's Scores:", items: [], counts: true });

  const addBtn = el("button", { class: "btn", type: "button", text: "Add Sequence", disabled: "" });
  const finishBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Finish & Tag Video", disabled: "" });
  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Select a video and wrestler to begin." });

  const seqCount = el("span", { text: "0" });
  const seqListEl = el("ul", { class: "seq-list" });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function resetDetails() {
    attackRadio.checked = true;
    tie.clear();
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
    const names = ["Wrestlers.config", "Ties.config", "Moves.config", "Outcomes.config"];
    const results = await Promise.all(names.map(async (name) => {
      const { ok, body } = await fetchJson(`/api/configs/${name}`);
      return ok ? body.items : [];
    }));
    const [wrestlers, ties, moves, outcomes] = results;
    wrestler.setItems(wrestlers);
    tie.setItems(ties);
    ourMoves.setItems(moves);
    oppMoves.setItems(moves);
    ourScores.setItems(outcomes);
    oppScores.setItems(outcomes);
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
      team_moves: teamMoves,
      op_moves: oppMovesSel,
      team_scores: teamScores,
      op_scores: oppScoresSel,
    };
  }

  function prettyChapter(seq) {
    return [
      "Sequence Info",
      `Start Time\t: ${formatClock(seq.start_time)}`,
      `End Time\t: ${formatClock(seq.end_time)}`,
      `Attacking\t: ${seq.attack_defend}`,
      `Tie/Position\t: ${seq.tie_up}`,
      `Your moves\t: ${seq.team_moves.join(", ")}`,
      `Opponent moves\t: ${seq.op_moves.join(", ")}`,
      `Your scoring\t: ${seq.team_scores.join(", ")}`,
      `Opponent scoring\t: ${seq.op_scores.join(", ")}`,
    ].join("\n");
  }

  function commitSequence(seq) {
    state.sequences.push(seq);
    seqListEl.appendChild(el("li", {
      class: "seq-item",
      text: `[${formatClock(seq.start_time)} - ${formatClock(seq.end_time)}] ${seq.attack_defend ? "A" : "D"} | ${seq.tie_up}`,
    }));
    seqCount.textContent = String(state.sequences.length);
    resetDetails();
    setStatus(`Added sequence. ${state.sequences.length} sequences tagged.`);
    updateButtons();
  }

  function showChapterPreview(seq) {
    const dialog = el("dialog", { class: "modal chapter-modal" });
    dialog.appendChild(el("h3", { text: "Chapter Preview" }));
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
    setStatus("Tagging video...");
    try {
      const payload = {
        video: state.video,
        wrestler: state.wrestler,
        sequences: state.sequences,
      };
      const { ok, body } = await fetchJson("/api/tag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!ok) throw new Error(body.detail || "Could not start tagging");

      const job = await pollJob(body.job_id, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      setStatus(`Tagged: ${job.result.output}`);
      showToast(`Video tagged successfully! Output: ${job.result.output}`, "success");
      state.sequences = [];
      seqListEl.replaceChildren();
      seqCount.textContent = "0";
      state.video = "";
      await loadVideos();
    } catch (err) {
      showToast(err.message || "Tagging failed.", "error");
      setStatus("Tagging failed.");
    } finally {
      setTagging(false);
    }
  }

  // ---------- Wiring ----------

  videoFilter.addEventListener("input", renderVideos);
  uploadBtn.addEventListener("click", handleUpload);
  wrestler.input.addEventListener("input", () => {
    state.wrestler = wrestler.selected;
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

  // ---------- Layout ----------

  const videoCard = el("section", { class: "tag-card" }, [
    el("h3", { text: "Select Video" }),
    el("div", { class: "row upload-row" }, [uploadInput, uploadBtn]),
    videoFilter,
    videoListEl,
  ]);
  const playerCard = el("section", { class: "tag-card" }, [player.node]);
  const right = el("div", { class: "tag-right" }, [videoCard, playerCard]);

  const timingCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "2. Timing" }),
    startTime.node,
    endTime.node,
  ]);
  const detailsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "3. Sequence Details" }),
    attackRow,
    tie.node,
    ourMoves.node,
    oppMoves.node,
    ourScores.node,
    oppScores.node,
  ]);
  const actionsCard = el("fieldset", { class: "tag-card" }, [
    el("legend", { text: "Actions" }),
    el("div", { class: "row" }, [addBtn, finishBtn]),
    progress,
    statusLabel,
  ]);
  const listCard = el("fieldset", { class: "tag-card" }, [
    el("legend", {}, [el("span", { text: "Tagged Sequences: " }), seqCount]),
    seqListEl,
  ]);
  const left = el("div", { class: "tag-left" }, [
    wrestler.node,
    timingCard,
    detailsCard,
    actionsCard,
    listCard,
  ]);

  root.appendChild(el("div", { class: "tag-film-grid" }, [left, right]));

  // ---------- Boot ----------

  loadVideos();
  loadConfigs();
}
