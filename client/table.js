// ═══════════════════════════════════════════════════════════════════════════
//  Poker Lab Browser Client
//  Vanilla JS — connects to WS server, renders state, sends commands
// ═══════════════════════════════════════════════════════════════════════════

let ws = null;
let state = null;
let msgId = 0;
let sessionId = null;

// ── WebSocket Connection ───────────────────────────────────────────────────

function connect() {
  const url = `ws://${location.host}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setStatus("connected", "Connected");
  };

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
      refreshState();
      return;
    }

    // Response to our command
    if (msg.ok === false) {
      logEvent({ type: "ERROR", detail: msg.error });
      return;
    }

    if (msg.events) {
      for (const e of msg.events) logEvent(e);
    }
    if (msg.state) {
      state = msg.state;
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

function send(cmd, payload) {
  if (!ws || ws.readyState !== 1) return;
  const id = `msg-${++msgId}`;
  ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
}

function refreshState() {
  send("GET_STATE");
}

// ── Commands ───────────────────────────────────────────────────────────────

function sendStartHand() {
  send("START_HAND");
}

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

// ── Rendering ──────────────────────────────────────────────────────────────

function c$(v) {
  if (v == null) return "—";
  return Math.abs(v) >= 100 ? "$" + (v / 100).toFixed(2) : v + "c";
}

function cardHtml(card) {
  if (!card) return '<span class="board-card empty">?</span>';
  const isRed = card.includes("h") || card.includes("d");
  return `<span class="board-card${isRed ? " red" : ""}">${card}</span>`;
}

function render() {
  if (!state) return;

  // Header
  document.getElementById("table-info").textContent =
    `${state.tableName} | ${c$(state.sb)}/${c$(state.bb)} | Hands: ${state.handsPlayed}`;

  // Seats
  const felt = document.getElementById("table-felt");
  // Remove old seats (keep board-area)
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
      if (state.hand && state.hand.actionSeat === i) div.classList.add("active");

      const badges = [];
      if (state.button === i) badges.push("BTN");
      if (state.hand && state.hand.phase !== "COMPLETE") {
        if (i === state.seats[i]?.seat) {
          // Check SB/BB from blind info — approximate from hand events
        }
      }
      if (s.allIn) badges.push("ALL-IN");
      if (s.folded) badges.push("FOLD");

      const cardsHtml = s.holeCards
        ? `<div class="seat-cards">${s.holeCards.join(" ")}</div>`
        : (s.inHand && !s.folded ? '<div class="seat-cards" style="color:#666">[**]</div>' : "");

      const betHtml = s.bet > 0 ? `<div class="seat-bet">Bet: ${c$(s.bet)}</div>` : "";
      const badgeHtml = badges.length > 0 ? `<div class="seat-badge">${badges.join(" ")}</div>` : "";

      div.innerHTML =
        `<div class="seat-name">${s.player.name}</div>` +
        `<div class="seat-stack">${c$(s.stack)}</div>` +
        cardsHtml + betHtml + badgeHtml;
    }

    felt.appendChild(div);
  }

  // Board
  const boardEl = document.getElementById("board");
  const hand = state.hand;
  const board = hand ? hand.board : [];
  let boardHtml = "";
  for (let i = 0; i < 5; i++) {
    boardHtml += board[i] ? cardHtml(board[i]) : '<span class="board-card empty"></span>';
  }
  boardEl.innerHTML = boardHtml;

  // Pot
  const potEl = document.getElementById("pot");
  potEl.textContent = hand && hand.pot > 0 ? `Pot: ${c$(hand.pot)}` : "";

  // Phase
  const phaseEl = document.getElementById("phase");
  phaseEl.textContent = hand ? hand.phase : "Waiting";

  // Action buttons
  updateActionButtons();
}

function updateActionButtons() {
  const hand = state ? state.hand : null;
  const legal = hand ? hand.legalActions : null;
  const actions = legal ? legal.actions : [];

  const foldBtn = document.getElementById("fold-btn");
  const checkBtn = document.getElementById("check-btn");
  const callBtn = document.getElementById("call-btn");
  const betBtn = document.getElementById("bet-btn");
  const raiseBtn = document.getElementById("raise-btn");
  const betInput = document.getElementById("bet-input");
  const startBtn = document.getElementById("start-btn");

  foldBtn.disabled = !actions.includes("FOLD");
  checkBtn.disabled = !actions.includes("CHECK");
  callBtn.disabled = !actions.includes("CALL");
  betBtn.disabled = !actions.includes("BET");
  raiseBtn.disabled = !actions.includes("RAISE");

  // Update call button label with amount
  if (actions.includes("CALL") && legal) {
    callBtn.textContent = `Call ${c$(legal.callAmount)}`;
  } else {
    callBtn.textContent = "Call";
  }

  // Set bet input defaults
  if (actions.includes("BET") && legal) {
    betInput.value = legal.minBet;
    betInput.min = legal.minBet;
  } else if (actions.includes("RAISE") && legal) {
    betInput.value = legal.minRaise;
    betInput.min = legal.minRaise;
    betInput.max = legal.maxRaise;
  }

  // Start button: enabled when no hand active and 2+ players seated
  const occupied = state ? Object.values(state.seats).filter((s) => s.status === "OCCUPIED").length : 0;
  const handActive = hand && hand.phase !== "COMPLETE";
  startBtn.disabled = handActive || occupied < 2;
}

// ── Event Log Panel ────────────────────────────────────────────────────────

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

// ── Status ─────────────────────────────────────────────────────────────────

function setStatus(cls, text) {
  const el = document.getElementById("status");
  el.className = cls;
  el.textContent = text;
}

// ── Init ───────────────────────────────────────────────────────────────────

connect();
