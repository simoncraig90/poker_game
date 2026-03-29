// ═══════════════════════════════════════════════════════════════════════════
//  Poker Lab Browser Client — Phase 5
// ═══════════════════════════════════════════════════════════════════════════

let ws = null;
let state = null;
let msgId = 0;
let sessionId = null;
let resultBannerTimeout = null;

// ── WebSocket ──────────────────────────────────────────────────────────────

function connect() {
  ws = new WebSocket(`ws://${location.host}`);

  ws.onopen = () => setStatus("connected", "Connected");

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);

    if (msg.welcome) {
      sessionId = msg.sessionId;
      state = msg.state;
      logEvent({ type: "CONNECTED", detail: `session=${sessionId}, events=${msg.eventCount}` });
      render();
      return;
    }

    if (msg.broadcast) {
      for (const e of msg.events) logEvent(e);
      handleEvents(msg.events);
      refreshState();
      return;
    }

    if (msg.ok === false) {
      logEvent({ type: "ERROR", detail: msg.error });
      showError(msg.error);
      return;
    }

    if (msg.events && msg.events.length > 0) {
      for (const e of msg.events) logEvent(e);
      handleEvents(msg.events);
    }
    if (msg.state) {
      // Response to GET_STATE or GET_HAND_LIST
      state = msg.state.seats ? msg.state : state;
      if (msg.state.hands) renderHandList(msg.state.hands);
      render();
    } else {
      refreshState();
    }
  };

  ws.onclose = () => {
    setStatus("disconnected", "Disconnected");
    setTimeout(connect, 2000);
  };
  ws.onerror = () => {};
}

function send(cmd, payload, callback) {
  if (!ws || ws.readyState !== 1) return;
  const id = `msg-${++msgId}`;
  if (callback) {
    const handler = (evt) => {
      const m = JSON.parse(evt.data);
      if (m.id === id) { ws.removeEventListener("message", handler); callback(m); }
    };
    ws.addEventListener("message", handler);
  }
  ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
}

function refreshState() { send("GET_STATE"); }

// ── Event Handling ─────────────────────────────────────────────────────────

function handleEvents(events) {
  for (const e of events) {
    if (e.type === "HAND_RESULT") {
      showResultBanner(e);
    }
    if (e.type === "HAND_START") {
      clearResultBanner();
    }
  }
}

// ── Commands ───────────────────────────────────────────────────────────────

function sendStartHand() { send("START_HAND"); }

function sendAction(action) {
  if (!state || !state.hand) return;
  send("PLAYER_ACTION", { seat: state.hand.actionSeat, action });
}

function sendBet() {
  if (!state || !state.hand) return;
  const amount = parseInt(document.getElementById("bet-input").value);
  if (isNaN(amount)) return;
  send("PLAYER_ACTION", { seat: state.hand.actionSeat, action: "BET", amount });
}

function sendRaise() {
  if (!state || !state.hand) return;
  const amount = parseInt(document.getElementById("bet-input").value);
  if (isNaN(amount)) return;
  send("PLAYER_ACTION", { seat: state.hand.actionSeat, action: "RAISE", amount });
}

function seatClick(seatIndex) {
  if (!state) return;
  const seat = state.seats[seatIndex];
  if (seat.status !== "EMPTY") return;
  const name = prompt("Player name:");
  if (!name) return;
  const buyIn = parseInt(prompt("Buy-in (cents):", "1000"));
  if (isNaN(buyIn)) return;
  send("SEAT_PLAYER", { seat: seatIndex, name, buyIn, country: "XX" });
}

// ── Keyboard Shortcuts ─────────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  // Don't intercept when typing in an input
  if (e.target.tagName === "INPUT") return;

  const hand = state ? state.hand : null;
  const legal = hand ? (hand.legalActions ? hand.legalActions.actions : []) : [];

  switch (e.key.toLowerCase()) {
    case "f": if (legal.includes("FOLD")) sendAction("FOLD"); break;
    case "c": if (legal.includes("CALL")) sendAction("CALL"); break;
    case "x": if (legal.includes("CHECK")) sendAction("CHECK"); break;
    case "d":
    case "enter":
      if (!hand || hand.phase === "COMPLETE") sendStartHand();
      break;
  }
});

// ── Rendering ──────────────────────────────────────────────────────────────

function c$(v) {
  if (v == null) return "--";
  return Math.abs(v) >= 100 ? "$" + (v / 100).toFixed(2) : v + "c";
}

function cardHtml(card) {
  if (!card) return '<span class="board-card empty"></span>';
  const isRed = card.includes("h") || card.includes("d");
  return `<span class="board-card${isRed ? " red" : ""}">${card}</span>`;
}

function render() {
  if (!state) return;

  // Header
  const hand = state.hand;
  const handActive = hand && hand.phase !== "COMPLETE";
  const handInfo = handActive ? `Hand #${hand.handId} | ${hand.phase}` : "Between hands";
  document.getElementById("table-info").textContent =
    `${state.tableName} | ${c$(state.sb)}/${c$(state.bb)} | ${handInfo} | Played: ${state.handsPlayed}`;

  // Seats
  const felt = document.getElementById("table-felt");
  felt.querySelectorAll(".seat").forEach((el) => el.remove());

  for (let i = 0; i < state.maxSeats; i++) {
    const s = state.seats[i];
    const div = document.createElement("div");
    div.className = "seat";
    div.setAttribute("data-seat", i);

    if (s.status === "EMPTY") {
      div.classList.add("empty");
      div.innerHTML = `<div class="seat-name">Seat ${i}</div><div class="seat-stack">Click to sit</div>`;
      div.onclick = () => seatClick(i);
    } else {
      if (s.folded) div.classList.add("folded");
      if (handActive && hand.actionSeat === i) div.classList.add("active");

      const badges = [];
      if (state.button === i) badges.push("BTN");
      if (s.allIn) badges.push("ALL-IN");
      if (s.folded) badges.push("FOLD");

      const cardsHtml = s.holeCards
        ? `<div class="seat-cards">${s.holeCards.join(" ")}</div>`
        : (s.inHand && !s.folded ? '<div class="seat-cards" style="color:#555">[**]</div>' : "");

      const betHtml = s.bet > 0 ? `<div class="seat-bet">Bet: ${c$(s.bet)}</div>` : "";
      const badgeHtml = badges.length > 0 ? `<div class="seat-badge">${badges.join(" | ")}</div>` : "";

      div.innerHTML =
        `<div class="seat-name">${s.player.name}</div>` +
        `<div class="seat-stack">${c$(s.stack)}</div>` +
        cardsHtml + betHtml + badgeHtml;
    }

    felt.appendChild(div);
  }

  // Board
  const boardEl = document.getElementById("board");
  const board = (handActive && hand) ? hand.board : [];
  let boardHtml = "";
  for (let i = 0; i < 5; i++) {
    boardHtml += board[i] ? cardHtml(board[i]) : '<span class="board-card empty"></span>';
  }
  boardEl.innerHTML = boardHtml;

  // Pot
  document.getElementById("pot").textContent = handActive && hand.pot > 0 ? `Pot: ${c$(hand.pot)}` : "";

  // Phase
  document.getElementById("phase").textContent = handActive ? hand.phase : "";

  updateActionButtons();
}

function updateActionButtons() {
  const hand = state ? state.hand : null;
  const legal = hand ? hand.legalActions : null;
  const actions = legal ? legal.actions : [];

  document.getElementById("fold-btn").disabled = !actions.includes("FOLD");
  document.getElementById("check-btn").disabled = !actions.includes("CHECK");
  document.getElementById("call-btn").disabled = !actions.includes("CALL");
  document.getElementById("bet-btn").disabled = !actions.includes("BET");
  document.getElementById("raise-btn").disabled = !actions.includes("RAISE");

  const callBtn = document.getElementById("call-btn");
  callBtn.innerHTML = actions.includes("CALL") && legal
    ? `Call ${c$(legal.callAmount)} <span class="key-hint">[C]</span>`
    : 'Call <span class="key-hint">[C]</span>';

  const betInput = document.getElementById("bet-input");
  if (actions.includes("BET") && legal) {
    betInput.value = legal.minBet;
    betInput.min = legal.minBet;
  } else if (actions.includes("RAISE") && legal) {
    betInput.value = legal.minRaise;
    betInput.min = legal.minRaise;
    betInput.max = legal.maxRaise;
  }

  const occupied = state ? Object.values(state.seats).filter((s) => s.status === "OCCUPIED").length : 0;
  const handActive = hand && hand.phase !== "COMPLETE";
  document.getElementById("start-btn").disabled = handActive || occupied < 2;
}

// ── Result Banner ──────────────────────────────────────────────────────────

function showResultBanner(resultEvent) {
  const banner = document.getElementById("result-banner");
  const lines = [];
  for (const r of resultEvent.results || []) {
    if (r.won) lines.push(`${r.player} wins ${c$(r.amount)}`);
  }
  for (const r of resultEvent.results || []) {
    if (r.text && r.won) lines.push(r.text);
  }
  banner.textContent = lines.join(" | ") || "";

  clearTimeout(resultBannerTimeout);
  resultBannerTimeout = setTimeout(() => { banner.textContent = ""; }, 5000);
}

function clearResultBanner() {
  clearTimeout(resultBannerTimeout);
  document.getElementById("result-banner").textContent = "";
}

// ── Error Toast ────────────────────────────────────────────────────────────

function showError(msg) {
  const toast = document.getElementById("error-toast");
  toast.textContent = msg;
  toast.style.display = "block";
  setTimeout(() => { toast.style.display = "none"; }, 3000);
}

// ── Event Log ──────────────────────────────────────────────────────────────

function logEvent(e) {
  const log = document.getElementById("log");
  const div = document.createElement("div");
  div.className = "log-entry";

  let detail = "";
  if (e.player) detail += e.player + " ";
  if (e.action) detail += e.action + " ";
  if (e.amount != null && e.amount > 0) detail += c$(e.amount) + " ";
  if (e.blindType) detail += e.blindType + " ";
  if (e.cards) detail += e.cards.join(" ") + " ";
  if (e.newCards) detail += e.newCards.join(" ") + " ";
  if (e.street && e.type === "DEAL_COMMUNITY") detail += "(" + e.street + ") ";
  if (e.awards) detail += e.awards.map((a) => a.player + " " + c$(a.amount)).join(", ") + " ";
  if (e.detail) detail += e.detail + " ";
  if (e.error) detail += e.error + " ";

  div.innerHTML = `<span class="type">${e.type || "?"}</span> <span class="detail">${detail.trim()}</span>`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ── Panel Tabs ─────────────────────────────────────────────────────────────

function switchTab(tab) {
  document.querySelectorAll(".panel-tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".panel-content").forEach((p) => p.classList.remove("active"));

  if (tab === "events") {
    document.querySelector('.panel-tab:nth-child(1)').classList.add("active");
    document.getElementById("events-panel").classList.add("active");
  } else {
    document.querySelector('.panel-tab:nth-child(2)').classList.add("active");
    document.getElementById("history-panel").classList.add("active");
    loadHandList();
  }
}

// ── Hand History ───────────────────────────────────────────────────────────

function loadHandList() {
  send("GET_HAND_LIST", {}, (resp) => {
    if (resp.ok && resp.state && resp.state.hands) {
      renderHandList(resp.state.hands);
    }
  });
}

function renderHandList(hands) {
  const el = document.getElementById("hand-list");
  if (!hands || hands.length === 0) {
    el.innerHTML = '<div style="color:#555;padding:8px">No completed hands yet</div>';
    return;
  }
  el.innerHTML = hands.map((h) =>
    `<div class="hand-row" onclick="loadHandDetail('${h.handId}')">` +
    `<span class="hid">#${h.handId}</span> ` +
    `<span class="hwinner">${h.winner}</span> ` +
    `<span class="hpot">${c$(h.pot)}</span>` +
    `</div>`
  ).join("");
  el.style.display = "block";
  document.getElementById("hand-detail").style.display = "none";
  document.getElementById("hand-detail-back").style.display = "none";
}

function loadHandDetail(handId) {
  send("GET_HAND_EVENTS", { handId }, (resp) => {
    if (resp.ok && resp.events) {
      renderHandDetail(resp.events);
    }
  });
}

function renderHandDetail(events) {
  const lines = formatTimeline(events);
  document.getElementById("hand-detail").textContent = lines.join("\n");
  document.getElementById("hand-detail").style.display = "block";
  document.getElementById("hand-list").style.display = "none";
  document.getElementById("hand-detail-back").style.display = "block";
}

function showHandList() {
  document.getElementById("hand-detail").style.display = "none";
  document.getElementById("hand-detail-back").style.display = "none";
  document.getElementById("hand-list").style.display = "block";
}

// ── Timeline Formatter (browser version of replay-normalized-hand.js) ─────

function formatTimeline(events) {
  const lines = [];
  let players = {};
  let board = [];

  for (const e of events) {
    switch (e.type) {
      case "HAND_START":
        lines.push(`Hand #${e.handId} | Button: Seat ${e.button}`);
        players = e.players || {};
        const stacks = Object.entries(players).map(([s, p]) => `${p.name} ${c$(p.stack)}`);
        lines.push(`Stacks: ${stacks.join(" | ")}`);
        lines.push("");
        break;

      case "BLIND_POST":
        lines.push(`${e.player} posts ${e.blindType} ${c$(e.amount)}`);
        break;

      case "HERO_CARDS":
        lines.push(`[${e.seat}] cards: ${e.cards.join(" ")}`);
        break;

      case "PLAYER_ACTION": {
        const inf = e.inferred ? " {inferred}" : "";
        if (e.action === "FOLD") lines.push(`${e.player} folds${inf}`);
        else if (e.action === "CHECK") lines.push(`${e.player} checks${inf}`);
        else if (e.action === "CALL") lines.push(`${e.player} calls ${c$(e.totalBet)}${inf}`);
        else if (e.action === "BET") lines.push(`${e.player} bets ${c$(e.totalBet)}${inf}`);
        else if (e.action === "RAISE") lines.push(`${e.player} raises to ${c$(e.totalBet)}${inf}`);
        else lines.push(`${e.player} ${e.action} ${c$(e.totalBet)}${inf}`);
        break;
      }

      case "BET_RETURN":
        lines.push(`${e.player} returned ${c$(e.amount)}`);
        break;

      case "DEAL_COMMUNITY":
        board = e.board || [];
        lines.push("");
        lines.push(`--- ${e.street} [${board.join(" ")}] ---`);
        break;

      case "POT_AWARD":
        lines.push("");
        for (const a of e.awards || []) {
          lines.push(`** ${a.player} wins ${c$(a.amount)} **`);
        }
        break;

      case "HAND_SUMMARY":
        lines.push(`Result: ${e.winPlayer} wins ${c$(e.totalPot)} (${e.showdown ? "showdown" : "no showdown"})`);
        if (board.length > 0) lines.push(`Board: ${board.join(" ")}`);
        break;

      case "HAND_RESULT":
        lines.push("");
        for (const r of e.results || []) {
          lines.push(`${r.player}: ${r.text}`);
        }
        break;
    }
  }

  return lines;
}

// ── Status ─────────────────────────────────────────────────────────────────

function setStatus(cls, text) {
  const el = document.getElementById("status");
  el.className = cls;
  el.textContent = text;
}

// ── Init ───────────────────────────────────────────────────────────────────

connect();
