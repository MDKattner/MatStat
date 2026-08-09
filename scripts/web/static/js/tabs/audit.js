/**
 * Auditing tab — review and re-tag already-tagged videos.
 *
 * Mounts into #audit-root. Lists every tagged video with its embedded
 * sequences (read back from the file metadata via GET /api/audit), previews
 * the tagged video, and lets the user edit/add/remove sequences and re-tag the
 * video in place (POST /api/audit/{file}/retag), which also recompiles stats.
 */

import {
  checkList,
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

export function mountAudit(root) {
  if (!root) return;
  root.replaceChildren();

  // ---------- State ----------

  const state = {
    videos: [],
    data: null, // selected video: { name, wrestler, sequences }
    editingIndex: -1,
    retagging: false,
    playerPosMs: 0,
  };
  let wrestlerNames = [];

  // ---------- Components ----------

  const videoList = filterList({
    placeholder: "Type to filter tagged videos...",
    onChange: (fileName) => {
      const data = state.videos.find((v) => v.name === fileName);
      if (!data) return;
      state.data = data;
      state.editingIndex = -1;
      wrestlerList.setSelected(data.wrestler || "");
      opponentList.setItems(wrestlerNames.filter((w) => w !== (data.wrestler || "")));
      opponentList.setSelected(data.opponent || "");
      oppTie.node.hidden = !data.opponent;
      if (!data.opponent) oppTie.clear();
      setResult(data.result || "");
      renderSequences();
      resetDetails();
      setStatus(`Selected video: ${fileName}`);
      updateButtons();
      updateTitlePreview();
      loadPreview(fileName);
    },
  });

  const player = videoPlayer({
    onPosition: (ms) => { state.playerPosMs = ms; },
  });

  const wrestlerList = filterList({
    placeholder: "Type to filter wrestlers...",
    onChange: (name) => {
      if (name && name === opponentList.selected) opponentList.clear();
      opponentList.setItems(wrestlerNames.filter((w) => w !== name));
      updateTitlePreview();
      updateButtons();
    },
  });

  const opponentList = filterList({
    placeholder: "Type to filter opponents...",
    onChange: () => {
      if (!opponentList.selected) oppTie.clear();
      oppTie.node.hidden = !opponentList.selected;
      updateTitlePreview();
      updateButtons();
    },
  });

  // Match result: "No Result" (default / draw), Win, or Loss. Draws are skipped.
  const noResultRadio = el("input", { type: "radio", name: "audit-result", value: "", checked: "", hidden: "" });
  const winRadio = el("input", { type: "radio", name: "audit-result", value: "W", hidden: "" });
  const lossRadio = el("input", { type: "radio", name: "audit-result", value: "L", hidden: "" });
  const noResultBtn = el("button", { class: "seg-option active", type: "button", text: "No Result" });
  const winBtn = el("button", { class: "seg-option", type: "button", text: "Win" });
  const lossBtn = el("button", { class: "seg-option", type: "button", text: "Loss" });

  let matchResultValue = "";

  function setResult(value) {
    matchResultValue = value;
    noResultRadio.checked = value === "";
    winRadio.checked = value === "W";
    lossRadio.checked = value === "L";
    noResultBtn.classList.toggle("active", value === "");
    winBtn.classList.toggle("active", value === "W");
    lossBtn.classList.toggle("active", value === "L");
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
    const wrestler = wrestlerList.selected;
    titlePreview.textContent = wrestler
      ? `Will tag as: ${buildTitle(wrestler, opponentList.selected, matchResultValue)}`
      : "";
  }

  const startTime = timeInput({ label: "Start:" });
  const endTime = timeInput({ label: "End:" });

  const attackRadio = el("input", {
    type: "radio", name: "audit-attack-defend", value: "attack", checked: "", hidden: "",
  });
  const defendRadio = el("input", {
    type: "radio", name: "audit-attack-defend", value: "defend", hidden: "",
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
  const ourMoves = checkList({ label: "Your Moves", items: [], counts: true });
  const oppMoves = checkList({ label: "Opp. Moves", items: [], counts: true });
  const ourScores = checkList({ label: "Your Scores", items: [], counts: true });
  const oppScores = checkList({ label: "Opp. Scores", items: [], counts: true });

  const addBtn = el("button", { class: "btn", type: "button", text: "Add Sequence", disabled: "" });
  const updateBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Update Sequence", hidden: "" });
  const deleteBtn = el("button", { class: "btn btn-ghost danger", type: "button", text: "Delete Sequence", hidden: "" });
  const retagBtn = el("button", { class: "btn btn-ghost", type: "button", text: "Re-tag Video", disabled: "" });
  const progress = el("progress", { class: "progress", max: "100", value: "0", hidden: "" });
  const statusLabel = el("p", { class: "status-label", text: "Select a tagged video to audit." });

  const seqCount = el("span", { text: "0" });
  const seqListEl = el("ul", { class: "seq-list" });

  function setStatus(text) {
    statusLabel.textContent = text;
  }

  function resetDetails() {
    startTime.setFromSeconds(0);
    endTime.setFromSeconds(0);
    attackRadio.checked = true;
    attackBtn.classList.add("active");
    defendBtn.classList.remove("active");
    tie.clear();
    oppTie.clear();
    ourMoves.clear();
    oppMoves.clear();
    ourScores.clear();
    oppScores.clear();
    state.editingIndex = -1;
  }

  function setEditingMode(editing) {
    addBtn.hidden = editing;
    updateBtn.hidden = !editing;
    deleteBtn.hidden = !editing;
    addBtn.disabled = !state.data || state.retagging;
  }

  function updateButtons() {
    const hasData = Boolean(state.data && !state.retagging);
    retagBtn.disabled = !hasData || state.data.sequences.length === 0;
    setEditingMode(state.editingIndex >= 0);
  }

  // ---------- Sequence editing ----------

  function fillControls(seq) {
    startTime.setFromSeconds(seq.start_time);
    endTime.setFromSeconds(seq.end_time);
    if (seq.attack_defend) {
      attackRadio.checked = true;
      attackBtn.classList.add("active");
      defendBtn.classList.remove("active");
    } else {
      defendRadio.checked = true;
      defendBtn.classList.add("active");
      attackBtn.classList.remove("active");
    }
    tie.setSelected(seq.tie_up || "");
    oppTie.setSelected(seq.opp_tie || "");
    ourMoves.clear();
    oppMoves.clear();
    ourScores.clear();
    oppScores.clear();
    for (const move of seq.team_moves || []) {
      if (move !== "nothing") setChecked(ourMoves, move);
    }
    for (const move of seq.op_moves || []) {
      if (move !== "nothing") setChecked(oppMoves, move);
    }
    for (const score of seq.team_scores || []) {
      if (score !== "None") setChecked(ourScores, score);
    }
    for (const score of seq.op_scores || []) {
      if (score !== "None") setChecked(oppScores, score);
    }
  }

  function setChecked(list, value) {
    const label = list.node.querySelector(`label.check-label:has(input[value="${CSS.escape(value)}"])`);
    const box = label && label.querySelector("input[type=checkbox]");
    if (box) box.checked = true;
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
      opp_tie: opponentList.selected ? oppTie.selected : "",
      team_moves: teamMoves,
      op_moves: oppMovesSel,
      team_scores: teamScores,
      op_scores: oppScoresSel,
    };
  }

  function tieLabel(seq) {
    return seq.opp_tie ? `${seq.tie_up}:${seq.opp_tie}` : seq.tie_up;
  }

  function seqLabel(seq, index) {
    return `[${formatClock(seq.start_time)} - ${formatClock(seq.end_time)}] ${seq.attack_defend ? "A" : "D"} | ${tieLabel(seq)}`;
  }

  function prettyChapter(seq) {
    return [
      "Sequence Info",
      `Start Time\t: ${formatClock(seq.start_time)}`,
      `End Time\t: ${formatClock(seq.end_time)}`,
      `Attacking\t: ${seq.attack_defend}`,
      `Tie/Position\t: ${tieLabel(seq)}`,
      `Your moves\t: ${seq.team_moves.join(", ")}`,
      `Opponent moves\t: ${seq.op_moves.join(", ")}`,
      `Your scoring\t: ${seq.team_scores.join(", ")}`,
      `Opponent scoring\t: ${seq.op_scores.join(", ")}`,
    ].join("\n");
  }

  function commitSequence(seq) {
    state.data.sequences.push(seq);
    state.data.sequences.sort((a, b) => a.start_time - b.start_time);
    resetDetails();
    renderSequences();
    setStatus(`Added sequence. ${state.data.sequences.length} sequences.`);
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

  function renderSequences() {
    const sequences = state.data ? state.data.sequences : [];
    seqListEl.replaceChildren();
    sequences.forEach((seq, index) => {
      const item = el("li", {
        class: "seq-item" + (index === state.editingIndex ? " active" : ""),
        text: seqLabel(seq, index),
        onclick: () => {
          state.editingIndex = index;
          fillControls(seq);
          renderSequences();
          setEditingMode(true);
          setStatus(`Editing sequence ${index + 1} of ${sequences.length}.`);
        },
      });
      seqListEl.appendChild(item);
    });
    seqCount.textContent = String(sequences.length);
  }

  function addSequence() {
    if (!state.data || state.retagging) return;
    const seq = currentSequence();
    if (seq.start_time >= seq.end_time) {
      showToast("Start time must be before end time.", "error");
      return;
    }
    const sorted = [...state.data.sequences].sort((a, b) => a.start_time - b.start_time);
    const overlap = sorted.some((other) =>
      seq.start_time < other.end_time && other.start_time < seq.end_time
    );
    if (overlap) {
      showToast("Sequence overlaps an existing sequence.", "error");
      return;
    }
    showChapterPreview(seq);
  }

  function updateSequence() {
    if (!state.data || state.editingIndex < 0) return;
    const seq = currentSequence();
    if (seq.start_time >= seq.end_time) {
      showToast("Start time must be before end time.", "error");
      return;
    }
    const others = state.data.sequences
      .map((s, i) => ({ s, i }))
      .filter(({ i }) => i !== state.editingIndex)
      .map(({ s }) => s);
    const overlap = others.some((other) =>
      seq.start_time < other.end_time && other.start_time < seq.end_time
    );
    if (overlap) {
      showToast("Sequence overlaps another sequence.", "error");
      return;
    }
    state.data.sequences[state.editingIndex] = seq;
    state.data.sequences.sort((a, b) => a.start_time - b.start_time);
    state.editingIndex = -1;
    resetDetails();
    renderSequences();
    setStatus("Sequence updated.");
    updateButtons();
  }

  function deleteSequence() {
    if (!state.data || state.editingIndex < 0) return;
    if (!confirm("Delete this sequence?")) return;
    state.data.sequences.splice(state.editingIndex, 1);
    state.editingIndex = -1;
    resetDetails();
    renderSequences();
    setStatus("Sequence deleted.");
    updateButtons();
  }

  // ---------- Preview + re-tag ----------

  async function loadVideos() {
    const { ok, body } = await fetchJson("/api/audit");
    if (!ok) {
      showToast("Could not load tagged videos.", "error");
      return;
    }
    state.videos = body.videos || [];
    videoList.setItems(state.videos.map((v) => v.name));
    if (state.data) {
      const fresh = state.videos.find((v) => v.name === state.data.name);
      state.data = fresh || null;
      if (state.data) renderSequences();
    }
    setStatus(`${state.videos.length} tagged video(s).`);
  }

  async function loadPreview(fileName) {
    try {
      const url = await ensurePreview("taged", fileName, setStatus);
      player.load(url);
    } catch (err) {
      showToast(err.message || "Could not load preview.", "error");
    }
  }

  async function loadConfigs() {
    try {
      const { ok, body } = await fetchJson("/api/config");
      if (!ok) throw new Error(`Config returned status ${body.status || "error"}`);
      wrestlerNames = body.wrestlers || [];
      wrestlerList.setItems(wrestlerNames);
      wrestlerList.setTeams(body.teams || {});
      opponentList.setItems(body.wrestlers || []);
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

  function setRetagging(active) {
    state.retagging = active;
    progress.hidden = !active;
    if (active) {
      progress.removeAttribute("value");
    } else {
      progress.value = 0;
    }
    updateButtons();
  }

  async function retagVideo() {
    if (!state.data || state.retagging) return;
    if (state.data.sequences.length === 0) {
      showToast("Add at least one sequence.", "error");
      return;
    }
    const wrestler = wrestlerList.selected;
    if (!wrestler) {
      showToast("Select a wrestler first.", "error");
      return;
    }

    setRetagging(true);
    setStatus("Re-tagging video...");
    try {
      const name = state.data.name;
      const payload = {
        wrestler,
        opponent: opponentList.selected,
        match_result: matchResultValue,
        sequences: state.data.sequences,
      };
      const { ok, body } = await fetchJson(`/api/audit/${encodeURIComponent(name)}/retag`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!ok) throw new Error(body.detail || "Could not start re-tagging");

      const job = await pollJob(body.job_id, {
        onProgress: (p) => { progress.value = p; },
        onStatus: setStatus,
      });
      setStatus(`Re-tagged: ${job.result.output} (stats recompiled)`);
      showToast("Video re-tagged and stats recompiled.", "success");
      state.data = null;
      opponentList.clear();
      oppTie.clear();
      setResult("");
      oppTie.node.hidden = true;
      videoList.clear();
      await loadVideos();
    } catch (err) {
      showToast(err.message || "Re-tagging failed.", "error");
      setStatus("Re-tagging failed.");
    } finally {
      setRetagging(false);
    }
  }

  // ---------- Wiring ----------

  addBtn.addEventListener("click", addSequence);
  updateBtn.addEventListener("click", updateSequence);
  deleteBtn.addEventListener("click", deleteSequence);
  retagBtn.addEventListener("click", retagVideo);
  startTime.markBtn.addEventListener("click", () => {
    startTime.setFromSeconds(Math.floor(state.playerPosMs / 1000));
  });
  endTime.markBtn.addEventListener("click", () => {
    endTime.setFromSeconds(Math.floor(state.playerPosMs / 1000));
  });

  // ---------- Layout ----------

  const videoCard = el("section", { class: "tag-card video-card" }, [
    el("h3", { text: "Tagged Videos" }),
    videoList.node,
  ]);
  const wrestlerCard = el("section", { class: "tag-card wrestler-card" }, [
    el("h3", { text: "Wrestler" }),
    wrestlerList.node,
    el("h3", { class: "subhead", text: "Opponent (optional)" }),
    opponentList.node,
    el("div", { class: "row result-row" }, [
      el("label", { class: "field-label", text: "Result:" }),
      resultRow,
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
    el("div", { class: "row" }, [addBtn, updateBtn, deleteBtn]),
    el("div", { class: "row" }, [retagBtn]),
    progress,
    statusLabel,
  ]);
  const listCard = el("details", { class: "tag-card", open: "" }, [
    el("summary", {}, [el("span", { text: "Tagged Sequences: " }), seqCount]),
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
  updateButtons();
  loadVideos();
  loadConfigs();
}
