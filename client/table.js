// ═══════════════════════════════════════════════════════════════════════════
//  Poker Lab Browser Client — Phase 8
// ═══════════════════════════════════════════════════════════════════════════

let ws = null;
let state = null;
let msgId = 0;
let sessionId = null;
let voidedHandIds = new Set();
let resultBannerTimeout = null;
let recoveryBannerTimeout = null;
let showdownReveals = null;     // current hand's SHOWDOWN_REVEAL data
let pendingResults = [];        // accumulated HAND_RESULT events for current hand
let showdownBoard = null;       // board cards cached at showdown (persists until next HAND_START)

// ── BB/Hour tracking ──────────────────────────────────────────────────────
const BB_CENTS = 10;  // $0.10
const heroStats = { startTime: Date.now(), initialBuyIn: 0, totalBuyIn: 0, hands: 0, prevStack: 0 };

// ── WebSocket ──────────────────────────────────────────────────────────────

function connect() {
  // Support ?table=N for multi-table
  const urlParams = new URLSearchParams(window.location.search);
  const tableId = urlParams.get("table") || "1";
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const apiKey = localStorage.getItem("pokerlab-api-key") || "";
  const keyParam = apiKey ? `&key=${encodeURIComponent(apiKey)}` : "";
  ws = new WebSocket(`${proto}//${location.host}?table=${tableId}${keyParam}`);

  ws.onopen = () => setStatus("connected", "Connected");

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);

    // Auth failure from server
    if (msg.code === "AUTH_FAILED") {
      const key = prompt("API key required to connect:");
      if (key) {
        localStorage.setItem("pokerlab-api-key", key);
        setTimeout(connect, 500);
      }
      return;
    }

    if (msg.welcome) {
      sessionId = msg.sessionId;
      state = msg.state;
      voidedHandIds = new Set(msg.voidedHands || []);

      logEvent({ type: "CONNECTED", detail: `session=${sessionId}, events=${msg.eventCount}` });

      if (msg.recovered) {
        logEvent({ type: "RECOVERY", detail: `Session recovered from disk (${msg.eventCount} events)` });
        if (msg.voidedHands && msg.voidedHands.length > 0) {
          logEvent({ type: "RECOVERY", detail: `Voided hands: ${msg.voidedHands.join(", ")}` });
        }
        showRecoveryBanner(msg);
      }

      // Init bb tracking if hero already seated
      if (state.seats[0] && state.seats[0].status === "OCCUPIED") {
        heroStats.startTime = Date.now();
        heroStats.initialBuyIn = state.seats[0].stack;
        heroStats.totalBuyIn = state.seats[0].stack;
        heroStats.prevStack = state.seats[0].stack;
        heroStats.hands = 0;
      }
      render();
      // Auto-seat players if table is empty
      autoSeatIfEmpty();
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
      state = msg.state.seats ? msg.state : state;
      if (msg.state.hands) renderHandList(msg.state.hands);
      if (msg.state.sessions) renderSessionsList(msg.state.sessions);
      render();
    } else {
      refreshState();
    }
  };

  ws.onclose = (evt) => {
    setStatus("disconnected", "Disconnected");
    // Don't auto-reconnect on auth failure (4001) — handled in onmessage
    if (evt.code !== 4001) setTimeout(connect, 2000);
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

function showActionBubble(seat, action, amount) {
  const seatEl = document.querySelector(`.seat[data-seat="${seat}"]`);
  if (!seatEl) return;
  // Remove any existing bubble on this seat
  const old = seatEl.querySelector('.action-bubble');
  if (old) old.remove();

  let text = action;
  if (action === "CALL" && amount) text = `Call $${(amount/100).toFixed(2)}`;
  else if (action === "RAISE" && amount) text = `Raise $${(amount/100).toFixed(2)}`;
  else if (action === "BET" && amount) text = `Bet $${(amount/100).toFixed(2)}`;
  else if (action === "FOLD") text = "Fold";
  else if (action === "CHECK") text = "Check";

  const bubble = document.createElement('div');
  bubble.className = `action-bubble ${action.toLowerCase()}`;
  bubble.textContent = text;
  seatEl.appendChild(bubble);
  // Remove after animation
  setTimeout(() => bubble.remove(), 2100);
}

function handleEvents(events) {
  for (const e of events) {
    if (e.type === "PLAYER_ACTION") {
      showActionBubble(e.seat, e.action, e.totalBet);
    }
    if (e.type === "HAND_START") {
      clearResultBanner();
      showdownReveals = null;
      showdownBoard = null;
      pendingResults = [];
    }
    if (e.type === "SHOWDOWN_REVEAL") {
      showdownReveals = e.reveals || [];
      // Snapshot the board at showdown time so render() can hold it
      if (state && state.hand && state.hand.board) {
        showdownBoard = state.hand.board.slice();
      }
    }
    if (e.type === "HAND_RESULT") {
      pendingResults.push(e);
    }
    if (e.type === "HAND_END") {
      showResultBannerMulti(pendingResults, showdownReveals);
      // Track bb/hour for hero
      if (state && state.seats[0] && state.seats[0].status === "OCCUPIED") {
        heroStats.hands++;
        const stack = state.seats[0].stack;
        // Detect rebuy: stack jumped way above previous
        if (heroStats.prevStack > 0 && stack > heroStats.prevStack + 500) {
          heroStats.totalBuyIn += (stack - heroStats.prevStack);
        }
        heroStats.prevStack = stack;
        updateBBHud();
      }
      // Auto-rebuy if hero (seat 0) is short-stacked
      setTimeout(() => {
        if (state && state.seats[0] && state.seats[0].status === "OCCUPIED" && state.seats[0].stack < 20) {
          const rebuyAmount = 1000;
          heroStats.totalBuyIn += rebuyAmount;
          // Leave and rejoin with full buy-in
          send("LEAVE_TABLE", { seat: 0 }, () => {
            send("SEAT_PLAYER", { seat: 0, name: "Skurj_poker", buyIn: rebuyAmount, country: "GB" });
          });
        }
      }, 1000);
      // Auto-deal next hand after 3 seconds (like PokerStars)
      setTimeout(() => {
        if (state && !state.hand || (state.hand && state.hand.phase === "COMPLETE")) {
          send("START_HAND");
        }
      }, 3000);
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
  if (state.seats[seatIndex].status !== "EMPTY") return;

  // Auto-seat with default name and $10 buy-in (like PS)
  heroStats.startTime = Date.now();
  heroStats.initialBuyIn = 1000;
  heroStats.totalBuyIn = 1000;
  heroStats.hands = 0;
  heroStats.prevStack = 1000;
  send("SEAT_PLAYER", { seat: seatIndex, name: "Skurj_poker", buyIn: 1000, country: "GB" }, () => {
    // Auto-deal after sitting down if enough players
    setTimeout(() => {
      send("GET_STATE", {}, () => {
        const occupied = Object.values(state.seats).filter(s => s.status === "OCCUPIED").length;
        if (occupied >= 2 && (!state.hand || state.hand.phase === "COMPLETE")) {
          send("START_HAND");
        }
      });
    }, 1000);
  });
}

function autoSeatIfEmpty() {
  if (!state) return;
  const occupied = Object.values(state.seats).filter((s) => s.status === "OCCUPIED").length;
  if (occupied > 0) return;
  if (state.handsPlayed > 0) return; // don't re-seat after a hand
  const players = [
    { seat: 0, name: "Skurj_poker", buyIn: 800 },
    { seat: 1, name: "m_skeet27", buyIn: 1000 },
    { seat: 2, name: "MyCVVis911", buyIn: 1000 },
    { seat: 3, name: "drumpfen85", buyIn: 1050 },
    { seat: 4, name: "UBaHbl4_641", buyIn: 1000 },
    { seat: 5, name: "KAELO_08", buyIn: 1000 },
  ];
  let seated = 0;
  for (const p of players) {
    send("SEAT_PLAYER", { seat: p.seat, name: p.name, buyIn: p.buyIn, country: "XX" }, () => {
      seated++;
      if (seated === players.length) {
        // All seated — refresh state then deal
        send("GET_STATE", {}, () => {
          send("START_HAND");
        });
      }
    });
  }
}

function updateBBHud() {
  if (!state || !state.seats[0] || state.seats[0].status === "EMPTY") return;
  const stack = state.seats[0].stack;
  const profitCents = stack - heroStats.totalBuyIn;
  const profitBB = profitCents / BB_CENTS;
  const hoursElapsed = (Date.now() - heroStats.startTime) / 3600000;
  const bbPerHour = hoursElapsed > 0.001 ? profitBB / hoursElapsed : 0;
  const sign = profitBB >= 0 ? "+" : "";
  const el = document.getElementById("bb-hud");
  if (el) {
    el.textContent = `${sign}${profitBB.toFixed(1)} bb  (${sign}${bbPerHour.toFixed(1)} bb/hr)  ${heroStats.hands} hands`;
    el.style.color = profitBB >= 0 ? "#4caf50" : "#ef5350";
  }
}

function archiveSession() {
  if (!confirm("Archive current session and start a new one?")) return;
  send("ARCHIVE_SESSION");
}

// ── Auto-Actions ──────────────────────────────────────────────────────────

function onAutoActionChange() {
  // Mutual exclusion: only one auto-action at a time
  const clicked = event.target;
  if (clicked.checked) {
    document.querySelectorAll('#auto-actions input[type="checkbox"]').forEach(cb => {
      if (cb !== clicked) cb.checked = false;
    });
  }
  // Try to execute immediately if it's our turn
  tryAutoAction();
}

function tryAutoAction() {
  const hand = state ? state.hand : null;
  if (!hand || hand.actionSeat !== 0 || hand.phase === "COMPLETE") return false;
  const legal = hand.legalActions;
  if (!legal) return false;
  const actions = legal.actions;

  if (document.getElementById("auto-fold").checked) {
    if (actions.includes("FOLD")) { sendAction("FOLD"); clearAutoActions(); return true; }
    if (actions.includes("CHECK")) { sendAction("CHECK"); clearAutoActions(); return true; }
  }
  if (document.getElementById("auto-checkfold").checked) {
    if (actions.includes("CHECK")) { sendAction("CHECK"); clearAutoActions(); return true; }
    if (actions.includes("FOLD")) { sendAction("FOLD"); clearAutoActions(); return true; }
  }
  if (document.getElementById("auto-call").checked) {
    if (actions.includes("CALL")) { sendAction("CALL"); clearAutoActions(); return true; }
    if (actions.includes("CHECK")) { sendAction("CHECK"); clearAutoActions(); return true; }
  }
  return false;
}

function clearAutoActions() {
  document.querySelectorAll('#auto-actions input[type="checkbox"]').forEach(cb => cb.checked = false);
}

// ── Bet Sizing Slider ─────────────────────────────────────────────────────

let sliderMin = 0, sliderMax = 0;

function updateSizingBar() {
  const hand = state ? state.hand : null;
  const legal = hand ? hand.legalActions : null;
  const actions = legal ? legal.actions : [];
  const isHeroTurn = hand && hand.actionSeat === 0 && hand.phase !== "COMPLETE";
  const showSlider = isHeroTurn && (actions.includes("BET") || actions.includes("RAISE"));

  document.getElementById("sizing-bar").classList.toggle("visible", showSlider);
  if (!showSlider || !legal) return;

  if (actions.includes("BET")) {
    sliderMin = legal.minBet;
    sliderMax = state.seats[0] ? state.seats[0].stack : legal.minBet;
  } else {
    sliderMin = legal.minRaise;
    sliderMax = legal.maxRaise || sliderMin;
  }

  const slider = document.getElementById("bet-slider");
  slider.min = sliderMin;
  slider.max = sliderMax;
  const pot = hand.pot || 0;
  const defaultVal = Math.max(sliderMin, Math.min(Math.round(pot * 0.5), sliderMax));
  slider.value = defaultVal;
  document.getElementById("bet-input").value = defaultVal;
  updateSliderDisplay(defaultVal);
}

function onSliderChange() {
  const val = parseInt(document.getElementById("bet-slider").value);
  document.getElementById("bet-input").value = val;
  updateSliderDisplay(val);
  const betBtn = document.getElementById("bet-btn");
  const raiseBtn = document.getElementById("raise-btn");
  if (!betBtn.disabled) betBtn.innerHTML = `Bet ${c$(val)}`;
  if (!raiseBtn.disabled) raiseBtn.innerHTML = `Raise to ${c$(val)}`;
}

function updateSliderDisplay(val) {
  document.getElementById("slider-value").textContent = c$(val);
}

function setSizingFraction(frac) {
  const hand = state ? state.hand : null;
  if (!hand) return;
  const pot = hand.pot || 0;
  const amount = Math.max(sliderMin, Math.min(Math.round(pot * frac), sliderMax));
  document.getElementById("bet-slider").value = amount;
  document.getElementById("bet-input").value = amount;
  updateSliderDisplay(amount);
  onSliderChange();
}

function setSizingAllIn() {
  document.getElementById("bet-slider").value = sliderMax;
  document.getElementById("bet-input").value = sliderMax;
  updateSliderDisplay(sliderMax);
  onSliderChange();
}

// ── Turn Timer ────────────────────────────────────────────────────────────

let timerInterval = null;
const TURN_TIME = 30;

function startTurnTimer(seat) {
  stopTurnTimer();
  const seatEl = document.querySelector(`.seat[data-seat="${seat}"]`);
  if (!seatEl) return;

  let timerEl = seatEl.querySelector('.seat-timer');
  if (!timerEl) {
    timerEl = document.createElement('div');
    timerEl.className = 'seat-timer';
    timerEl.innerHTML = '<div class="seat-timer-fill" style="width:100%"></div>';
    seatEl.appendChild(timerEl);
  }
  const fill = timerEl.querySelector('.seat-timer-fill');
  fill.style.width = '100%';
  timerEl.style.display = '';

  const startTime = Date.now();
  timerInterval = setInterval(() => {
    const elapsed = (Date.now() - startTime) / 1000;
    const pct = Math.max(0, 100 - (elapsed / TURN_TIME) * 100);
    fill.style.width = pct + '%';
    if (pct <= 0) stopTurnTimer();
  }, 200);
}

function stopTurnTimer() {
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  document.querySelectorAll('.seat-timer').forEach(el => el.style.display = 'none');
}

// ── Keyboard Shortcuts ────────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;

  // Replay keyboard shortcuts
  if (replayFrames.length > 0 && studyQueueIndex >= 0) {
    // Queue navigation: Shift+Arrow
    if (e.shiftKey && e.key === "ArrowLeft") { e.preventDefault(); queuePrev(); return; }
    if (e.shiftKey && e.key === "ArrowRight") { e.preventDefault(); queueNext(); return; }
    // Next unanswered hero decision: N (quiz mode only)
    if (e.key.toLowerCase() === "n" && replayQuizMode) { e.preventDefault(); jumpNextUnanswered(); return; }
  }

  const hand = state ? state.hand : null;
  const legal = hand ? (hand.legalActions ? hand.legalActions.actions : []) : [];
  switch (e.key.toLowerCase()) {
    case "f": if (legal.includes("FOLD")) sendAction("FOLD"); break;
    case "c": if (legal.includes("CALL")) sendAction("CALL"); break;
    case "x": if (legal.includes("CHECK")) sendAction("CHECK"); break;
    case "d": case "enter": if (!hand || hand.phase === "COMPLETE") sendStartHand(); break;
  }
});

// ── Rendering ──────────────────────────────────────────────────────────────

function c$(v) {
  if (v == null) return "--";
  return "$" + (v / 100).toFixed(2);
}

const SUIT_SYMBOLS = { s: "\u2660", h: "\u2665", d: "\u2666", c: "\u2663" };

// ── Card sprite sheet mapping ──
// cards.png: 1898x204, 26 columns x 2 rows, each card 73x102
// Row 0: 2c,2h,3c,3h,4c,4h,5c,5h,6c,6h,7c,7h,8c,8h,9c,9h,Ac,Ah,Jc,Jh,Kc,Kh,Qc,Qh,10c,10h
// Row 1: 2s,2d,3s,3d,4s,4d,5s,5d,6s,6d,7s,7d,8s,8d,9s,9d,As,Ad,Js,Jd,Ks,Kd,Qs,Qd,10s,10d
const CARD_W = 73, CARD_H = 102, SPRITE_COLS = 26;
const ROW0_SUITS = ['c','h'], ROW1_SUITS = ['s','d'];
const RANK_ORDER = ['2','3','4','5','6','7','8','9','A','J','K','Q','T'];

function cardSpritePos(card) {
  const rank = card.slice(0, -1);
  const suit = card.slice(-1);
  const rankIdx = RANK_ORDER.indexOf(rank);
  if (rankIdx < 0) return { x: 0, y: 0 };
  let row, suitOffset;
  if (suit === 'c' || suit === 'h') {
    row = 0;
    suitOffset = suit === 'h' ? 0 : 1;  // hearts first, then clubs
  } else {
    row = 1;
    suitOffset = suit === 'd' ? 0 : 1;  // diamonds first, then spades
  }
  const col = rankIdx * 2 + suitOffset;
  return { x: col * CARD_W, y: row * CARD_H };
}

function cardHtml(card) {
  if (!card) return '<span class="card empty-slot"></span>';
  const pos = cardSpritePos(card);
  // Board card display: match PS proportions
  const displayW = 66, displayH = 93;
  const scaleX = displayW / CARD_W;
  const scaleY = displayH / CARD_H;
  const bgW = SPRITE_COLS * CARD_W * scaleX;
  const bgH = 2 * CARD_H * scaleY;
  const bgX = -(pos.x * scaleX);
  const bgY = -(pos.y * scaleY);
  return `<span class="card" style="width:${displayW}px;height:${displayH}px;background-size:${bgW}px ${bgH}px;background-position:${bgX}px ${bgY}px"></span>`;
}

function facedownCardHtml() {
  return '<span class="card facedown"></span>';
}

function seatCardHtml(card) {
  if (!card) return '';
  const pos = cardSpritePos(card);
  const displayW = 28, displayH = 39;
  const scaleX = displayW / CARD_W;
  const scaleY = displayH / CARD_H;
  const bgW = SPRITE_COLS * CARD_W * scaleX;
  const bgH = 2 * CARD_H * scaleY;
  const bgX = -(pos.x * scaleX);
  const bgY = -(pos.y * scaleY);
  return `<span class="card sm" style="width:${displayW}px;height:${displayH}px;background-size:${bgW}px ${bgH}px;background-position:${bgX}px ${bgY}px"></span>`;
}

function heroCardHtml(card) {
  if (!card) return '';
  const pos = cardSpritePos(card);
  const displayW = 139, displayH = 197; // Exact PS hero card pixel size
  const scaleX = displayW / CARD_W;
  const scaleY = displayH / CARD_H;
  const bgW = SPRITE_COLS * CARD_W * scaleX;
  const bgH = 2 * CARD_H * scaleY;
  const bgX = -(pos.x * scaleX);
  const bgY = -(pos.y * scaleY);
  return `<span class="card hero-card" style="width:${displayW}px;height:${displayH}px;background-size:${bgW}px ${bgH}px;background-position:${bgX}px ${bgY}px"></span>`;
}

function render() {
  if (!state) return;

  const hand = state.hand;
  const handActive = hand && hand.phase !== "COMPLETE";
  const handInfo = handActive ? `Hand #${hand.handId} | ${hand.phase}` : "Between hands";
  const sidShort = sessionId ? sessionId.slice(-10) : "";
  document.getElementById("table-info").textContent =
    `${state.tableName} | ${c$(state.sb)}/${c$(state.bb)} | ${handInfo} | Played: ${state.handsPlayed} | ${sidShort}`;

  // Header table name (PS-style)
  const headerName = document.getElementById("header-table-name");
  if (headerName) headerName.textContent = state.tableName || "Pamina III";

  // Felt info text (like PS table name + stakes)
  const feltInfo = document.getElementById("felt-info");
  if (feltInfo) {
    feltInfo.innerHTML = `${state.tableName} - No Limit Hold'em<br>${c$(state.sb)}/${c$(state.bb)}`;
  }

  // Game status message (PS-style "Wait for Big Blind" etc.)
  const statusText = document.getElementById("status-text");
  if (statusText) {
    if (!handActive && state.seats[0] && state.seats[0].status === "OCCUPIED") {
      statusText.textContent = "Wait for Big Blind";
    } else if (handActive && hand.actionSeat === 0) {
      statusText.textContent = "Your turn";
    } else if (handActive) {
      const actingPlayer = state.seats[hand.actionSeat];
      statusText.textContent = actingPlayer ? `${actingPlayer.player.name}'s turn` : "";
    } else {
      statusText.textContent = "";
    }
  }

  // Seats
  const felt = document.getElementById("table-felt");
  felt.querySelectorAll(".seat").forEach((el) => el.remove());
  felt.querySelectorAll(".hero-cards").forEach((el) => el.remove());
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
      if (s.stack === 0 && !s.allIn) div.classList.add("zero-stack");
      if (handActive && hand.actionSeat === i) div.classList.add("active");
      // Status handled inline now (dealer button, ALL IN, FOLD)

      // Check if this seat has a showdown reveal to display
      const reveal = showdownReveals ? showdownReveals.find((r) => r.seat === i) : null;
      const isHero = (i === 0); // seat 0 is always the hero/bottom seat
      let cardsHtml = "";
      let heroCardsHtml = "";
      let handNameHtml = "";
      if (reveal) {
        cardsHtml = `<div class="seat-cards">${reveal.cards.map(seatCardHtml).join("")}</div>`;
        handNameHtml = `<div style="font-size:9px;color:#8ecae6;margin-top:2px;">${reveal.handName}</div>`;
      } else if (s.holeCards && isHero) {
        // Hero: show cards large, below the seat
        heroCardsHtml = `<div class="hero-cards">${s.holeCards.map(heroCardHtml).join("")}</div>`;
      } else if (s.holeCards && !isHero) {
        // Opponent: show facedown card backs
        cardsHtml = `<div class="seat-cards">${facedownCardHtml()}${facedownCardHtml()}</div>`;
      } else if (s.inHand && !s.folded) {
        cardsHtml = `<div class="seat-cards">${facedownCardHtml()}${facedownCardHtml()}</div>`;
      }

      const statusBadge = s.allIn ? '<div style="color:#ff6b6b;font-size:10px;font-weight:bold;margin-top:1px">ALL IN</div>' : (s.folded ? '<div style="color:#666;font-size:10px;margin-top:1px">FOLD</div>' : "");
      div.innerHTML = `<div class="seat-avatar"></div><div class="seat-name">${s.player.name}</div><div class="seat-stack">${c$(s.stack)}</div>${cardsHtml}${handNameHtml}${statusBadge}`;

      // Dealer button — positioned outside the seat panel
      if (state.button === i) {
        const btn = document.createElement("div");
        btn.className = "dealer-btn";
        btn.textContent = "";
        const btnPositions = {
          0: "top:-12px;right:-12px",
          1: "top:-8px;right:-12px",
          2: "bottom:-8px;right:-12px",
          3: "bottom:-12px;right:-12px",
          4: "bottom:-8px;left:-12px",
          5: "top:-8px;left:-12px",
        };
        btn.style.cssText = (btnPositions[i] || "") + ";position:absolute";
        div.style.position = "absolute";
        div.appendChild(btn);
      }

      // Bet chip — positioned outside the seat panel toward center
      if (s.bet > 0) {
        const chip = document.createElement("div");
        chip.className = "bet-chip";
        chip.textContent = c$(s.bet);
        const chipPositions = {
          0: "top:-28px;left:50%;transform:translateX(-50%)",
          1: "top:50%;right:-55px;transform:translateY(-50%)",
          2: "bottom:50%;right:-55px;transform:translateY(50%)",
          3: "bottom:-24px;left:50%;transform:translateX(-50%)",
          4: "bottom:50%;left:-55px;transform:translateY(50%)",
          5: "top:50%;left:-55px;transform:translateY(-50%)",
        };
        chip.style.cssText = chipPositions[i] || "";
        div.appendChild(chip);
      }
    }
    felt.appendChild(div);
    // Hero cards — render as separate element outside the seat panel
    if (s.status !== "EMPTY" && i === 0 && s.holeCards && s.holeCards.length > 0) {
      const hc = document.createElement("div");
      hc.className = "hero-cards";
      hc.innerHTML = s.holeCards.map(heroCardHtml).join("");
      felt.appendChild(hc);
    }
  }

  // Board — show during active hand, or hold showdown board after completion
  const board = (handActive && hand) ? hand.board : (showdownBoard || []);
  let boardHtml = "";
  for (let i = 0; i < 5; i++) {
    if (board[i]) {
      boardHtml += cardHtml(board[i]);
    } else if (handActive) {
      boardHtml += '<span class="card-slot"></span>';
    }
  }
  document.getElementById("board").innerHTML = boardHtml;
  document.getElementById("pot").textContent = handActive && hand.pot > 0 ? `Pot: ${c$(hand.pot)}` : "";
  document.getElementById("phase").textContent = handActive ? hand.phase : (showdownReveals ? "SHOWDOWN" : "");

  updateActionButtons();

  // Try auto-actions after render (slight delay so state is current)
  setTimeout(() => tryAutoAction(), 50);
}

function updateActionButtons() {
  const hand = state ? state.hand : null;
  const legal = hand ? hand.legalActions : null;
  const actions = legal ? legal.actions : [];

  // Hide action buttons when it's not hero's turn (seat 0)
  const isHeroTurn = hand && hand.actionSeat === 0 && hand.phase !== "COMPLETE";
  const actionBar = document.getElementById("action-bar");
  const handActive = hand && hand.phase !== "COMPLETE";
  const showBar = isHeroTurn || !handActive;
  actionBar.style.display = showBar ? "flex" : "none";

  document.getElementById("fold-btn").disabled = !actions.includes("FOLD");
  document.getElementById("check-btn").disabled = !actions.includes("CHECK");
  document.getElementById("call-btn").disabled = !actions.includes("CALL");
  document.getElementById("bet-btn").disabled = !actions.includes("BET");
  document.getElementById("raise-btn").disabled = !actions.includes("RAISE");

  // Hide/show action buttons based on hero's turn
  document.getElementById("fold-btn").style.display = isHeroTurn && actions.includes("FOLD") ? "" : "none";
  document.getElementById("check-btn").style.display = isHeroTurn && actions.includes("CHECK") ? "" : "none";
  document.getElementById("call-btn").style.display = isHeroTurn && actions.includes("CALL") ? "" : "none";
  document.getElementById("bet-btn").style.display = isHeroTurn && actions.includes("BET") ? "" : "none";
  document.getElementById("raise-btn").style.display = isHeroTurn && actions.includes("RAISE") ? "" : "none";

  const callBtn = document.getElementById("call-btn");
  callBtn.innerHTML = actions.includes("CALL") && legal
    ? `Call ${c$(legal.callAmount)} <span class="key-hint">[C]</span>`
    : 'Call <span class="key-hint">[C]</span>';

  const betBtn = document.getElementById("bet-btn");
  const raiseBtn = document.getElementById("raise-btn");
  const betInput = document.getElementById("bet-input");
  const showBetInput = isHeroTurn && (actions.includes("BET") || actions.includes("RAISE"));
  betInput.classList.toggle("visible", showBetInput);
  if (actions.includes("BET") && legal) {
    betInput.value = legal.minBet; betInput.min = legal.minBet;
    betBtn.innerHTML = `Bet ${c$(legal.minBet)}`;
  } else {
    betBtn.innerHTML = 'Bet';
  }
  if (actions.includes("RAISE") && legal) {
    betInput.value = legal.minRaise; betInput.min = legal.minRaise; betInput.max = legal.maxRaise;
    raiseBtn.innerHTML = `Raise to ${c$(legal.minRaise)}`;
  } else {
    raiseBtn.innerHTML = 'Raise';
  }

  const occupied = state ? Object.values(state.seats).filter((s) => s.status === "OCCUPIED").length : 0;
  document.getElementById("start-btn").disabled = handActive || occupied < 2;
  // Always show start button when between hands
  document.getElementById("start-btn").style.display = handActive ? "none" : "";

  // Update bet sizing slider
  updateSizingBar();

  // Update turn timer
  if (hand && hand.actionSeat !== undefined && hand.phase !== "COMPLETE") {
    startTurnTimer(hand.actionSeat);
  } else {
    stopTurnTimer();
  }
}

// ── Banners ────────────────────────────────────────────────────────────────

function showResultBanner(resultEvent) {
  // Legacy single-event path (kept for backwards compat if called directly)
  showResultBannerMulti([resultEvent], null);
}

function showResultBannerMulti(resultEvents, reveals) {
  const banner = document.getElementById("result-banner");
  if (!resultEvents || resultEvents.length === 0) { banner.innerHTML = ""; return; }

  const lines = [];
  const isShowdown = reveals && reveals.length > 0;
  const potNames = resultEvents.length === 1 ? [""] : resultEvents.map((_, i) => i === 0 ? "Main: " : `Side ${i}: `);

  for (let i = 0; i < resultEvents.length; i++) {
    const re = resultEvents[i];
    const prefix = potNames[i];
    for (const r of re.results || []) {
      if (!r.won) continue;
      const revealInfo = isShowdown && reveals ? reveals.find((rv) => rv.seat === r.seat) : null;
      const handDesc = revealInfo ? ` (${revealInfo.handName})` : "";
      lines.push(`${prefix}${r.player} wins ${c$(r.amount)}${handDesc}`);
    }
  }

  banner.innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
  clearTimeout(resultBannerTimeout);
  resultBannerTimeout = setTimeout(() => { banner.innerHTML = ""; }, resultEvents.length > 1 ? 8000 : 5000);
}

function clearResultBanner() {
  clearTimeout(resultBannerTimeout);
  document.getElementById("result-banner").textContent = "";
}

function showRecoveryBanner(welcomeMsg) {
  const banner = document.getElementById("recovery-banner");
  let text = `Recovered session ${welcomeMsg.sessionId.slice(-15)} (${welcomeMsg.eventCount} events)`;
  if (welcomeMsg.voidedHands && welcomeMsg.voidedHands.length > 0) {
    text += ` | Voided: Hand ${welcomeMsg.voidedHands.join(", ")}`;
  }
  banner.textContent = text;
  banner.style.display = "block";
  clearTimeout(recoveryBannerTimeout);
  recoveryBannerTimeout = setTimeout(() => { banner.style.display = "none"; }, 8000);
}

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
  if (e.potIndex != null && e.type === "POT_AWARD") detail = `[pot ${e.potIndex}] ` + detail;
  if (e.reveals) detail += e.reveals.map((r) => `${r.player}: ${r.cards.join(" ")} (${r.handName})`).join(" | ") + " ";
  if (e.detail) detail += e.detail + " ";
  if (e.error) detail += e.error + " ";
  if (e.void) detail += "[VOIDED] ";
  const typeColor = e.type === "RECOVERY" ? "color:#8ecae6" : "";
  div.innerHTML = `<span class="type" style="${typeColor}">${e.type || "?"}</span> <span class="detail">${detail.trim()}</span>`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ── Panel Tabs ─────────────────────────────────────────────────────────────

function switchTab(tab) {
  document.querySelectorAll(".panel-tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".panel-content").forEach((p) => p.classList.remove("active"));
  const idx = { events: 0, history: 1, sessions: 2, study: 3 }[tab] || 0;
  document.querySelectorAll(".panel-tab")[idx].classList.add("active");

  if (tab === "events") document.getElementById("events-panel").classList.add("active");
  else if (tab === "history") { document.getElementById("history-panel").classList.add("active"); loadHandList(); }
  else if (tab === "sessions") { document.getElementById("sessions-panel").classList.add("active"); loadSessionList(); }
  else if (tab === "study") { document.getElementById("study-panel").classList.add("active"); loadActorList(); }
}

// ── Hand History ───────────────────────────────────────────────────────────

function loadHandList() {
  send("GET_HAND_LIST", {}, (resp) => {
    if (resp.ok && resp.state && resp.state.hands) renderHandList(resp.state.hands);
  });
}

function renderHandList(hands, readOnly) {
  const el = document.getElementById("hand-list");
  if (!hands || hands.length === 0) {
    el.innerHTML = '<div style="color:#555;padding:8px">No completed hands yet</div>';
    return;
  }
  el.innerHTML = hands.map((h) => {
    if (h.voided) {
      return `<div class="hand-row"><span class="hid">#${h.handId}</span> <span class="voided">[VOIDED - mid-hand recovery]</span></div>`;
    }
    const onclick = readOnly
      ? `onclick="loadSessionHandDetail('${currentBrowseSessionId}','${h.handId}')"`
      : `onclick="loadHandDetail('${h.handId}')"`;
    const sdTag = h.showdown ? ' <span style="color:#8ecae6;font-size:9px">[SD]</span>' : '';
    return `<div class="hand-row" ${onclick}><span class="hid">#${h.handId}</span> <span class="hwinner">${h.winner}</span> <span class="hpot">${c$(h.pot)}</span>${sdTag}</div>`;
  }).join("");
  el.style.display = "block";
  document.getElementById("hand-detail").style.display = "none";
  document.getElementById("hand-detail-back").style.display = "none";
}

function loadHandDetail(handId) {
  send("GET_HAND_EVENTS", { handId }, (resp) => {
    if (resp.ok && resp.events) renderHandDetail(resp.events);
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

// ── Sessions Tab ───────────────────────────────────────────────────────────

let currentBrowseSessionId = null;

function loadSessionList() {
  send("GET_SESSION_LIST", {}, (resp) => {
    if (resp.ok && resp.state && resp.state.sessions) renderSessionsList(resp.state.sessions);
  });
}

function renderSessionsList(sessions) {
  const el = document.getElementById("sessions-list");
  document.getElementById("sessions-detail").style.display = "none";
  document.getElementById("sessions-detail-back").style.display = "none";
  el.style.display = "block";

  if (!sessions || sessions.length === 0) {
    el.innerHTML = '<div style="color:#555;padding:8px">No sessions</div>';
    return;
  }

  let html = sessions.map((s) => {
    const isActive = s.status === "active";
    const dotCls = isActive ? "active" : "complete";
    const rowCls = isActive ? "session-row active-session" : "session-row";
    const created = s.createdAt ? new Date(s.createdAt).toLocaleString() : "?";
    const onclick = isActive ? "" : `onclick="browseSession('${s.sessionId}')"`;
    return `<div class="${rowCls}" ${onclick}>` +
      `<span class="session-dot ${dotCls}"></span>` +
      `<span class="session-id">${s.sessionId}</span><br>` +
      `<span class="session-info">${s.status} | ${s.handsPlayed || 0} hands | ${created}</span>` +
      `</div>`;
  }).join("");

  html += `<button class="session-archive-btn" onclick="archiveSession()">Archive & New Session</button>`;
  el.innerHTML = html;
}

function browseSession(sid) {
  currentBrowseSessionId = sid;
  send("GET_HAND_LIST", { sessionId: sid }, (resp) => {
    if (resp.ok && resp.state && resp.state.hands) {
      renderSessionHandList(sid, resp.state.hands);
    }
  });
}

function renderSessionHandList(sid, hands) {
  const el = document.getElementById("sessions-detail");
  document.getElementById("sessions-list").style.display = "none";
  document.getElementById("sessions-detail-back").style.display = "block";
  el.style.display = "block";

  let html = `<div style="padding:4px 8px; font-size:11px; color:#888; border-bottom:1px solid #333">${sid} (complete)</div>`;
  if (!hands || hands.length === 0) {
    html += '<div style="color:#555;padding:8px">No hands</div>';
  } else {
    html += hands.map((h) => {
      if (h.voided) {
        return `<div class="hand-row"><span class="hid">#${h.handId}</span> <span class="voided">[VOIDED]</span></div>`;
      }
      return `<div class="hand-row" onclick="loadSessionHandDetail('${sid}','${h.handId}')">` +
        `<span class="hid">#${h.handId}</span> <span class="hwinner">${h.winner}</span> <span class="hpot">${c$(h.pot)}</span></div>`;
    }).join("");
  }
  el.innerHTML = html;
}

function loadSessionHandDetail(sid, handId) {
  send("GET_HAND_EVENTS", { sessionId: sid, handId }, (resp) => {
    if (resp.ok && resp.events) {
      const el = document.getElementById("sessions-detail");
      const lines = formatTimeline(resp.events);
      el.innerHTML = `<div style="padding:4px 8px; color:#4ecca3; cursor:pointer; font-size:11px; border-bottom:1px solid #333" onclick="browseSession('${sid}')">Back to hands</div>` +
        `<pre style="padding:4px 8px; font-size:10px; line-height:1.6; white-space:pre-wrap; color:#ccc">${lines.join("\n")}</pre>`;
    }
  });
}

function showSessionsList() {
  document.getElementById("sessions-detail").style.display = "none";
  document.getElementById("sessions-detail-back").style.display = "none";
  document.getElementById("sessions-list").style.display = "block";
  loadSessionList();
}

// ── Timeline Formatter ─────────────────────────────────────────────────────

function formatTimeline(events) {
  const lines = [];
  let board = [];
  let potCount = 0;
  // Count POT_AWARDs to know if multi-pot
  for (const e of events) { if (e.type === "POT_AWARD") potCount++; }
  const isMultiPot = potCount > 1;

  for (const e of events) {
    switch (e.type) {
      case "HAND_START":
        lines.push(`Hand #${e.handId} | Button: Seat ${e.button}`);
        const stacks = Object.entries(e.players || {}).map(([s, p]) => `${p.name} ${c$(p.stack)}`);
        lines.push(`Stacks: ${stacks.join(" | ")}`);
        lines.push("");
        break;
      case "BLIND_POST": lines.push(`${e.player} posts ${e.blindType} ${c$(e.amount)}`); break;
      case "HERO_CARDS": lines.push(`[${e.seat}] cards: ${e.cards.join(" ")}`); break;
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
      case "BET_RETURN": lines.push(`${e.player} returned ${c$(e.amount)}`); break;
      case "DEAL_COMMUNITY": board = e.board || []; lines.push(""); lines.push(`--- ${e.street} [${board.join(" ")}] ---`); break;
      case "SHOWDOWN_REVEAL":
        lines.push("");
        lines.push("--- SHOWDOWN ---");
        for (const r of e.reveals || []) {
          lines.push(`${r.player}: ${r.cards.join(" ")} (${r.handName})`);
        }
        break;
      case "POT_AWARD": {
        lines.push("");
        const potLabel = isMultiPot ? (e.potIndex === 0 ? "Main pot" : `Side pot ${e.potIndex}`) : "Pot";
        for (const a of e.awards || []) lines.push(`** ${a.player} wins ${c$(a.amount)} [${potLabel}] **`);
        break;
      }
      case "HAND_SUMMARY": {
        const rankStr = e.handRank ? ` with ${e.handRank}` : "";
        const sdStr = e.showdown ? "showdown" : "no showdown";
        lines.push(`Result: ${e.winPlayer} wins ${c$(e.totalPot)}${rankStr} (${sdStr})`);
        if (board.length > 0) lines.push(`Board: ${board.join(" ")}`);
        break;
      }
      case "HAND_RESULT": {
        const potLabel = isMultiPot ? (e.potIndex === 0 ? " [Main]" : ` [Side ${e.potIndex}]`) : "";
        lines.push("");
        for (const r of e.results || []) lines.push(`${r.player}: ${r.text}${potLabel}`);
        break;
      }
      case "HAND_END":
        if (e.void) { lines.push(""); lines.push("[HAND VOIDED - server recovered from mid-hand crash]"); lines.push("[Stacks restored to pre-hand values]"); }
        break;
    }
  }
  return lines;
}

// ── Study Tab ─────────────────────────────────────────────────────────────

let studyActorId = null;
let blindReviewMode = false;
let blindRevealed = new Set(); // sessionId/handId keys for hands whose outcomes have been revealed

function toggleBlindReview() {
  blindReviewMode = !blindReviewMode;
  blindRevealed.clear();
  loadStudyHands();
}

function blindRevealHand() {
  if (replaySessionId && replayHandId) {
    blindRevealed.add(replaySessionId + "/" + replayHandId);
    renderReplayFrame();
  }
}

function isBlindHidden(sessionId, handId) {
  return blindReviewMode && !blindRevealed.has(sessionId + "/" + handId);
}

function loadActorList() {
  send("LIST_ACTORS", {}, (resp) => {
    if (resp.ok && resp.state && resp.state.actors) renderActorList(resp.state.actors);
    else renderActorList([]);
  });
}

function renderActorList(actorList) {
  const el = document.getElementById("study-actors");
  document.getElementById("study-stats").style.display = "none";
  document.getElementById("study-hands").style.display = "none";
  el.style.display = "block";

  if (actorList.length === 0) {
    el.innerHTML = '<div style="color:#555;padding:8px">No actors registered. Seat a player to create one.</div>';
    return;
  }

  let html = actorList.map((a) =>
    `<div class="actor-row" onclick="viewActor('${a.actorId}')">` +
    `<span class="actor-name">${a.name}</span>` +
    `<span class="actor-id">${a.actorId}</span>` +
    (a.notes ? `<br><span class="actor-notes">${a.notes}</span>` : "") +
    `</div>`
  ).join("");
  html += `<button class="study-new-btn" onclick="createActorDialog()">+ New Actor</button>`;
  el.innerHTML = html;
}

function createActorDialog() {
  const name = prompt("Actor name:");
  if (!name) return;
  const notes = prompt("Notes (optional):", "");
  send("CREATE_ACTOR", { name, notes }, (resp) => {
    if (resp.ok) loadActorList();
  });
}

function viewActor(actorId) {
  studyActorId = actorId;
  send("GET_ACTOR_STATS", { actorId }, (resp) => {
    if (!resp.ok) return;
    const stats = resp.state.stats;
    send("GET_ACTOR", { actorId }, (aResp) => {
      const actor = aResp.ok ? aResp.state.actor : { name: actorId, notes: "" };
      renderActorStats(actor, stats);
    });
  });
}

function renderActorStats(actor, stats) {
  document.getElementById("study-actors").style.display = "none";
  document.getElementById("study-hands").style.display = "none";
  const el = document.getElementById("study-stats");
  el.style.display = "block";

  const pct = (v) => v != null ? (v * 100).toFixed(1) + "%" : "—";
  const af = stats.aggFactor != null ? stats.aggFactor.toFixed(2) : "—";

  el.innerHTML =
    `<div class="study-back" onclick="loadActorList()">Back to actors</div>` +
    `<div style="padding:4px 0"><span class="actor-name">${actor.name}</span><span class="actor-id">${actor.actorId}</span></div>` +
    (actor.notes ? `<div class="actor-notes" style="margin-bottom:4px">${actor.notes}</div>` : "") +
    `<table class="stat-table">` +
    `<tr><td>Hands dealt</td><td>${stats.handsDealt}</td></tr>` +
    `<tr><td>Hands won</td><td>${stats.handsWon}</td></tr>` +
    `<tr><td>VPIP</td><td>${pct(stats.vpip)}</td></tr>` +
    `<tr><td>PFR</td><td>${pct(stats.pfr)}</td></tr>` +
    `<tr><td>WTSD</td><td>${pct(stats.wtsd)}</td></tr>` +
    `<tr><td>W$SD</td><td>${pct(stats.wsd)}</td></tr>` +
    `<tr><td>Agg Factor</td><td>${af}</td></tr>` +
    `<tr><td>Net result</td><td>${c$(stats.netResult)}</td></tr>` +
    `<tr><td>Avg pot won</td><td>${c$(stats.avgPotWon)}</td></tr>` +
    `</table>` +
    `<div class="study-filter-bar">` +
    `<select id="study-session-filter"><option value="">All sessions</option></select>` +
    `<select id="study-sd-filter"><option value="">All</option><option value="true">Showdown</option><option value="false">Fold-out</option></select>` +
    `<select id="study-result-filter"><option value="">All</option><option value="won">Won</option><option value="lost">Lost</option></select>` +
    `<select id="study-pos-filter"><option value="">Any pos</option><option value="BTN">BTN</option><option value="SB">SB</option><option value="BB">BB</option><option value="UTG">UTG</option><option value="MP">MP</option><option value="CO">CO</option></select>` +
    `<input id="study-after" type="date" title="After date" style="width:100px">` +
    `<input id="study-before" type="date" title="Before date" style="width:100px">` +
    `<label style="font-size:9px;color:#888;white-space:nowrap"><input type="checkbox" id="study-noted-filter"> Noted</label>` +
    `<select id="study-tag-filter" style="font-size:9px"><option value="">Any tag</option><option value="mistake">mistake</option><option value="interesting">interesting</option><option value="question">question</option><option value="good">good</option><option value="review">review</option></select>` +
    `<button class="rp-btn${blindReviewMode ? " rp-active" : ""}" onclick="toggleBlindReview()" title="${blindReviewMode ? "Show outcomes" : "Hide outcomes to reduce bias"}" style="font-size:9px">Blind</button>` +
    `<button onclick="loadStudyHands()">Filter</button>` +
    `<button onclick="exportStudyHands()" title="Copy results to clipboard">Export</button>` +
    `</div>`;

  // Populate session dropdown
  send("GET_SESSION_LIST", {}, (resp) => {
    const sel = document.getElementById("study-session-filter");
    if (!sel || !resp.ok || !resp.state || !resp.state.sessions) return;
    for (const s of resp.state.sessions) {
      const opt = document.createElement("option");
      opt.value = s.sessionId;
      opt.textContent = s.sessionId.slice(-12) + ` (${s.handsPlayed || 0}h)`;
      sel.appendChild(opt);
    }
  });

  loadStudyHands();
}

function loadStudyHands() {
  if (!studyActorId) return;
  const filters = { actorId: studyActorId };
  const sessEl = document.getElementById("study-session-filter");
  const sdEl = document.getElementById("study-sd-filter");
  const resEl = document.getElementById("study-result-filter");
  const posEl = document.getElementById("study-pos-filter");
  const afterEl = document.getElementById("study-after");
  const beforeEl = document.getElementById("study-before");
  if (sessEl && sessEl.value) filters.sessionId = sessEl.value;
  if (sdEl && sdEl.value) filters.showdown = sdEl.value === "true";
  if (resEl && resEl.value) filters.result = resEl.value;
  if (posEl && posEl.value) filters.position = posEl.value;
  if (afterEl && afterEl.value) filters.after = new Date(afterEl.value).getTime();
  if (beforeEl && beforeEl.value) filters.before = new Date(beforeEl.value + "T23:59:59").getTime();

  send("QUERY_HANDS", filters, (resp) => {
    if (!resp.ok) return;
    const hands = resp.state.hands || [];
    // Fetch annotation counts for each involved session
    const sessionIds = [...new Set(hands.map((h) => h.sessionId))];
    if (sessionIds.length === 0) { renderStudyHands(hands, {}); return; }

    let pending = sessionIds.length;
    const allCounts = {}; // { sessionId: { handId: count } }
    for (const sid of sessionIds) {
      send("GET_ANNOTATION_COUNTS", { sessionId: sid }, (cr) => {
        allCounts[sid] = (cr.ok && cr.state && cr.state.counts) ? cr.state.counts : {};
        if (--pending === 0) renderStudyHands(hands, allCounts);
      });
    }
  });
}

function renderStudyHands(hands, annotationCounts) {
  const el = document.getElementById("study-hands");
  el.style.display = "block";

  // Apply annotation filters
  const notedEl = document.getElementById("study-noted-filter");
  const tagEl = document.getElementById("study-tag-filter");
  const notedOnly = notedEl && notedEl.checked;
  const tagFilter = tagEl ? tagEl.value : "";
  const counts = annotationCounts || {};

  // Helper: get hand's annotation info { count, tags } from counts map
  function handAnn(h) {
    const sc = counts[h.sessionId] || {};
    const info = sc[h.handId];
    if (!info) return { count: 0, tags: [] };
    return typeof info === "number" ? { count: info, tags: [] } : info;
  }

  let filtered = hands;
  if (notedOnly) {
    filtered = filtered.filter((h) => handAnn(h).count > 0);
  }
  if (tagFilter) {
    filtered = filtered.filter((h) => handAnn(h).tags.includes(tagFilter));
  }

  if (filtered.length === 0) {
    el.innerHTML = '<div style="color:#555;padding:4px">No matching hands</div>';
    studyHandCache = [];
    return;
  }

  // Cache for click-through (filtered set)
  studyHandCache = filtered;
  studyAnnotationCounts = counts;
  queueQuizAccum = {}; // new queue = fresh accumulator

  el.innerHTML = filtered.map((h, i) => {
    const hidden = isBlindHidden(h.sessionId, h.handId);
    const resCls = hidden ? "" : (h.result === "won" ? "s-won" : (h.result === "lost" ? "s-lost" : ""));
    const resLabel = hidden ? "---" : h.result;
    const netLabel = hidden ? "---" : c$(h.netResult);
    const sdTag = h.showdown ? " [SD]" : "";
    const rank = hidden ? "" : (h.handRank ? ` (${h.handRank})` : "");
    const ann = handAnn(h);
    const noteBadge = ann.count > 0 ? ` <span class="ann-tag" title="${ann.count} note${ann.count > 1 ? "s" : ""}">${ann.count}n</span>` : "";
    const tagBadges = ann.tags.length > 0 ? " " + ann.tags.map((t) => `<span class="ann-tag">${t}</span>`).join(" ") : "";
    const rs = handReviewState(h.sessionId, h.handId);
    const rsMark = rs === "completed" ? '<span style="color:#4ecca3;font-size:9px" title="Completed">&#10003;</span> '
                 : rs === "in-progress" ? '<span style="color:#ffd700;font-size:9px" title="In progress">&#9679;</span> '
                 : rs === "visited" ? '<span style="color:#555;font-size:9px" title="Visited">&#9675;</span> '
                 : "";
    return `<div class="study-hand-row" style="cursor:pointer" onclick="viewStudyHand(${i})">` +
      `${rsMark}<span style="color:#4ecca3">${h.sessionId.slice(-8)}/#${h.handId}</span> ` +
      `<span class="s-result ${resCls}">${resLabel}</span> ` +
      `${netLabel} | ${h.position}${sdTag}${rank}${noteBadge}${tagBadges}` +
      `</div>`;
  }).join("");
}

let studyHandCache = [];
let studyAnnotationCounts = {};
let studyQueueIndex = -1;
let queueQuizAccum = {};     // { "sessionId/handId": { answered, matches, diffs, totalHero } }

/**
 * Derive review state for a hand from the queue accumulator.
 * Returns "untouched" | "visited" | "in-progress" | "completed".
 */
function handReviewState(sessionId, handId) {
  const key = sessionId + "/" + handId;
  const entry = queueQuizAccum[key];
  if (!entry) return "untouched";
  if (entry.answered === 0) return "visited";
  if (entry.totalHero > 0 && entry.answered >= entry.totalHero) return "completed";
  return "in-progress";
}

function exportStudyHands() {
  if (!studyHandCache || studyHandCache.length === 0) return;
  const header = "session\thand\tposition\tresult\tnet\tshowdown\thandRank\tstartStack\tinvested\twon";
  const rows = studyHandCache.map((h) =>
    [h.sessionId, h.handId, h.position, h.result, h.netResult, h.showdown, h.handRank || "", h.startStack, h.totalInvested, h.totalWon].join("\t")
  );
  const text = header + "\n" + rows.join("\n");
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.querySelector('.study-filter-bar button[onclick="exportStudyHands()"]');
    if (btn) { const orig = btn.textContent; btn.textContent = "Copied!"; setTimeout(() => { btn.textContent = orig; }, 1500); }
  }).catch(() => {
    // Fallback: prompt with text
    prompt("Copy this data:", text);
  });
}

// ── Replay State ──────────────────────────────────────────────────────────

let replayFrames = [];
let replayCursor = 0;
let replayDecisionOnly = false;
let replayHandEvents = [];
let replaySessionId = null;
let replayHandId = null;
let replayAnnotations = [];
let replayStudyMode = false;
let replayHeroSeat = null;
let replayQuizMode = false;
let replayQuizRevealed = false;
let quizAnswer = null;       // "fold" | "passive" | "aggressive" | null
let quizResult = null;       // "match" | "different" | null
let quizLedger = {};         // { frameIndex: { chosen, actual, result } }

function resetReplayState() {
  replayFrames = [];
  replayCursor = 0;
  replayDecisionOnly = false;
  replayHandEvents = [];
  replaySessionId = null;
  replayHandId = null;
  replayAnnotations = [];
  replayStudyMode = false;
  replayHeroSeat = null;
  replayQuizMode = false;
  replayQuizRevealed = false;
  quizAnswer = null;
  quizResult = null;
  quizLedger = {};
}

// ── Frame Compiler ────────────────────────────────────────────────────────

/**
 * Compile hand events into deterministic replay frames.
 * Each frame is a snapshot of the visible state after processing that event.
 * Pure function — no side effects.
 */
function compileFrames(events) {
  const frames = [];
  let street = "";
  let board = [];
  let pot = 0;
  let players = {};      // seat → { name, stack, invested, folded, allIn }
  let handId = "";
  let button = -1;

  for (const e of events) {
    switch (e.type) {
      case "HAND_START": {
        handId = e.handId;
        button = e.button;
        street = "PREFLOP";
        board = [];
        pot = 0;
        players = {};
        for (const [s, p] of Object.entries(e.players || {})) {
          players[s] = { name: p.name, stack: p.stack, invested: 0, folded: false, allIn: false, cards: null };
        }
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `Hand #${e.handId} — Button: Seat ${e.button}`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }

      case "BLIND_POST": {
        const p = players[e.seat];
        if (p) {
          p.stack -= e.amount;
          p.invested += e.amount;
        }
        pot += e.amount;
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `${e.player} posts ${e.blindType} ${c$(e.amount)}`,
          actingSeat: e.seat, actionLabel: `${e.blindType} ${c$(e.amount)}`, isDecision: false, isTerminal: false,
        });
        break;
      }

      case "HERO_CARDS": {
        const p = players[e.seat];
        if (p) p.cards = e.cards;
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `${e.player || "Seat " + e.seat} dealt ${e.cards.join(" ")}`,
          actingSeat: e.seat, actionLabel: "dealt cards", isDecision: false, isTerminal: false,
        });
        break;
      }

      case "PLAYER_ACTION": {
        const p = players[e.seat];
        if (p) {
          p.stack -= (e.delta || 0);
          p.invested += (e.delta || 0);
          if (e.action === "FOLD") p.folded = true;
          if (p.stack <= 0 && e.action !== "FOLD" && e.action !== "CHECK") p.allIn = true;
        }
        pot += (e.delta || 0);

        let label;
        if (e.action === "FOLD") label = `${e.player} folds`;
        else if (e.action === "CHECK") label = `${e.player} checks`;
        else if (e.action === "CALL") label = `${e.player} calls ${c$(e.totalBet)}`;
        else if (e.action === "BET") label = `${e.player} bets ${c$(e.totalBet)}`;
        else if (e.action === "RAISE") label = `${e.player} raises to ${c$(e.totalBet)}`;
        else label = `${e.player} ${e.action} ${c$(e.totalBet)}`;

        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label,
          actingSeat: e.seat, actionLabel: e.action, isDecision: true, isTerminal: false,
        });
        break;
      }

      case "BET_RETURN": {
        const p = players[e.seat];
        if (p) {
          p.stack += e.amount;
          p.invested -= e.amount;
        }
        pot -= e.amount;
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `${e.player} returned ${c$(e.amount)}`,
          actingSeat: e.seat, actionLabel: "return", isDecision: false, isTerminal: false,
        });
        break;
      }

      case "DEAL_COMMUNITY": {
        street = e.street;
        board = e.board || [];
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `--- ${e.street} [${board.join(" ")}] ---`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }

      case "SHOWDOWN_REVEAL": {
        street = "SHOWDOWN";
        for (const r of e.reveals || []) {
          const p = players[r.seat];
          if (p) p.cards = r.cards;
        }
        const revealText = (e.reveals || []).map((r) => `${r.player}: ${r.cards.join(" ")} (${r.handName})`).join(" | ");
        frames.push({
          index: frames.length, handId, street, board: [...board], pot,
          players: clonePlayers(players),
          event: e.type, label: `SHOWDOWN: ${revealText}`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }

      case "POT_AWARD": {
        for (const a of e.awards || []) {
          const p = players[a.seat];
          if (p) p.stack += a.amount;
        }
        const awardText = (e.awards || []).map((a) => `${a.player} wins ${c$(a.amount)}`).join(", ");
        frames.push({
          index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0,
          players: clonePlayers(players),
          event: e.type, label: `Award: ${awardText}`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        pot = 0;
        break;
      }

      case "HAND_SUMMARY": {
        const rankStr = e.handRank ? ` with ${e.handRank}` : "";
        frames.push({
          index: frames.length, handId, street: "SETTLE", board: [...board], pot: 0,
          players: clonePlayers(players),
          event: e.type, label: `Result: ${e.winPlayer} wins ${c$(e.totalPot)}${rankStr}`,
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: false,
        });
        break;
      }

      case "HAND_RESULT": {
        // Informational — skip separate frame, already covered by POT_AWARD + SUMMARY
        break;
      }

      case "HAND_END": {
        frames.push({
          index: frames.length, handId, street: "COMPLETE", board: [...board], pot: 0,
          players: clonePlayers(players),
          event: e.type, label: e.void ? "[VOIDED]" : "Hand complete",
          actingSeat: null, actionLabel: null, isDecision: false, isTerminal: true,
        });
        break;
      }
    }
  }

  return frames;
}

function clonePlayers(players) {
  const out = {};
  for (const [s, p] of Object.entries(players)) {
    out[s] = { ...p, cards: p.cards ? [...p.cards] : null };
  }
  return out;
}

/**
 * Get indices for decision-point frames only.
 */
function getDecisionIndices(frames) {
  return frames.map((f, i) => f.isDecision ? i : -1).filter((i) => i >= 0);
}

/**
 * Find the first frame index for a given street.
 */
function getStreetStartIndex(frames, targetStreet) {
  for (let i = 0; i < frames.length; i++) {
    if (frames[i].street === targetStreet) return i;
  }
  return -1;
}

// ── Study Visibility Policy ───────────────────────────────────────────────

/**
 * Find the frame index of the SHOWDOWN_REVEAL event in the frame list.
 * Returns -1 if no showdown in this hand.
 */
function getShowdownRevealIndex(frames) {
  for (let i = 0; i < frames.length; i++) {
    if (frames[i].event === "SHOWDOWN_REVEAL") return i;
  }
  return -1;
}

/**
 * Apply study-mode visibility to a frame.
 * Returns a new frame object with hidden information masked for the hero's perspective.
 *
 * Rules:
 *   - Hero's own cards: visible if set (HERO_CARDS processed for hero seat)
 *   - Opponent cards: hidden ("??") unless current frame is at or past SHOWDOWN_REVEAL
 *   - Board: already correct in compiled frames (only dealt-so-far cards)
 *   - Labels: HERO_CARDS events for opponents are redacted
 *
 * Pure function — does not mutate the input frame.
 */
function applyStudyVisibility(frame, heroSeat, showdownRevealIdx) {
  const out = {
    ...frame,
    players: {},
    board: [...frame.board],
    label: frame.label,
  };

  const pastShowdown = showdownRevealIdx >= 0 && frame.index >= showdownRevealIdx;

  for (const [s, p] of Object.entries(frame.players)) {
    const seatNum = parseInt(s);
    if (seatNum === heroSeat) {
      // Hero sees own cards
      out.players[s] = { ...p, cards: p.cards ? [...p.cards] : null };
    } else if (pastShowdown) {
      // After showdown reveal: all cards visible
      out.players[s] = { ...p, cards: p.cards ? [...p.cards] : null };
    } else {
      // Opponent before showdown: hide cards
      out.players[s] = { ...p, cards: null };
    }
  }

  // Redact HERO_CARDS labels for opponents
  if (frame.event === "HERO_CARDS" && frame.actingSeat !== heroSeat) {
    out.label = `${frame.players[String(frame.actingSeat)]?.name || "Opponent"} dealt cards`;
  }

  return out;
}

/**
 * Infer hero seat from the study hand cache entry (the actor being studied).
 */
function inferHeroSeat(frames) {
  // Use studyActorId if available — find the seat with that actorId in HAND_START
  if (studyActorId && replayHandEvents.length > 0) {
    const hs = replayHandEvents.find((e) => e.type === "HAND_START");
    if (hs && hs.players) {
      for (const [s, p] of Object.entries(hs.players)) {
        if (p.actorId === studyActorId) return parseInt(s);
      }
    }
  }
  // Fallback: seat 0
  return frames.length > 0 ? parseInt(Object.keys(frames[0].players)[0]) : 0;
}

function replayToggleStudyMode() {
  replayStudyMode = !replayStudyMode;
  if (replayStudyMode && replayHeroSeat === null) {
    replayHeroSeat = inferHeroSeat(replayFrames);
  }
  renderReplayFrame();
}

function replaySetHero(seat) {
  replayHeroSeat = seat;
  quizLedger = {}; // hero change invalidates prior answers
  quizFrameTransition(replayCursor);
  renderReplayFrame();
}

// ── Quiz Mode ─────────────────────────────────────────────────────────────

function replayToggleQuizMode() {
  replayQuizMode = !replayQuizMode;
  quizLedger = {}; // mode toggle invalidates prior answers
  quizFrameTransition(replayCursor);
  // Quiz mode implies study mode
  if (replayQuizMode && !replayStudyMode) {
    replayStudyMode = true;
    if (replayHeroSeat === null) replayHeroSeat = inferHeroSeat(replayFrames);
  }
  renderReplayFrame();
}

function quizReveal() {
  replayQuizRevealed = true;
  const rawFrame = replayFrames[Math.min(replayCursor, replayFrames.length - 1)];
  const actualBucket = actionToBucket(rawFrame.actionLabel);
  if (quizAnswer) {
    quizResult = quizAnswer === actualBucket ? "match" : "different";
    quizLedger[rawFrame.index] = { chosen: quizAnswer, actual: actualBucket, result: quizResult };
    // Live-update queue accumulator for current hand
    snapshotQuizToQueue();
    // Auto-reveal outcome when all hero decisions answered
    if (blindReviewMode && replayHeroSeat !== null) {
      const heroDecs = getDecisionIndices(replayFrames).filter((i) => replayFrames[i].actingSeat === replayHeroSeat);
      if (heroDecs.length > 0 && Object.keys(quizLedger).length >= heroDecs.length) {
        blindRevealed.add(replaySessionId + "/" + replayHandId);
      }
    }
  } else {
    quizResult = null;
  }
  renderReplayFrame();
}

function quizSelectAnswer(bucket) {
  quizAnswer = bucket;
  renderReplayFrame();
}

/**
 * Reset per-frame quiz state, restoring from ledger if available for the target frame.
 */
function quizFrameTransition(targetFrameIdx) {
  const entry = quizLedger[targetFrameIdx];
  if (entry) {
    quizAnswer = entry.chosen;
    quizResult = entry.result;
    replayQuizRevealed = true;
  } else {
    quizAnswer = null;
    quizResult = null;
    replayQuizRevealed = false;
  }
}

/**
 * Reset the current hand's quiz progress for retry.
 * Clears the hand ledger, removes queue contribution, re-hides outcome,
 * and moves cursor to the first frame.
 */
function resetHandQuiz() {
  if (!replaySessionId || !replayHandId) return;

  // Clear per-frame quiz state
  quizLedger = {};
  quizAnswer = null;
  quizResult = null;
  replayQuizRevealed = false;

  // Update queue accumulator: keep the entry (visited) but zero the answers
  const key = replaySessionId + "/" + replayHandId;
  const totalHero = replayHeroSeat !== null
    ? getDecisionIndices(replayFrames).filter((i) => replayFrames[i].actingSeat === replayHeroSeat).length
    : 0;
  queueQuizAccum[key] = { answered: 0, matches: 0, diffs: 0, totalHero };

  // Re-hide outcome in blind mode
  blindRevealed.delete(replaySessionId + "/" + replayHandId);

  // Reset cursor to first hero decision (or frame 0 if none)
  replayCursor = firstHeroDecisionIndex(replayFrames, replayHeroSeat);
  renderReplayFrame();
}

/**
 * Clear the current frame's quiz answer for re-answering.
 * Removes just this frame from the ledger, resets per-frame state,
 * and updates the queue accumulator.
 */
function clearSpotQuiz() {
  const rawFrame = replayFrames[Math.min(replayCursor, replayFrames.length - 1)];
  delete quizLedger[rawFrame.index];
  quizAnswer = null;
  quizResult = null;
  replayQuizRevealed = false;

  // Re-hide blind outcome if this makes the hand no longer fully answered
  if (blindReviewMode && replayHeroSeat !== null) {
    const heroDecs = getDecisionIndices(replayFrames).filter((i) => replayFrames[i].actingSeat === replayHeroSeat);
    if (Object.keys(quizLedger).length < heroDecs.length) {
      blindRevealed.delete(replaySessionId + "/" + replayHandId);
    }
  }

  snapshotQuizToQueue();
  renderReplayFrame();
}

/**
 * Map a concrete action to a coarse bucket.
 *   FOLD → "fold"
 *   CHECK, CALL → "passive"
 *   BET, RAISE → "aggressive"
 */
function actionToBucket(action) {
  if (!action) return null;
  const a = action.toUpperCase();
  if (a === "FOLD") return "fold";
  if (a === "CHECK" || a === "CALL") return "passive";
  if (a === "BET" || a === "RAISE") return "aggressive";
  return null;
}

/**
 * Determine if the current frame should be quiz-masked.
 * A frame is masked when:
 *   - quiz mode is on
 *   - the frame is a hero decision (isDecision && actingSeat === heroSeat)
 *   - the reveal button has not been pressed
 */
function isQuizMasked(frame, heroSeat) {
  return replayQuizMode && !replayQuizRevealed &&
         frame.isDecision && frame.actingSeat === heroSeat;
}

/**
 * Build a pre-action view of a hero decision frame.
 * Shows the state before the hero acted: use the previous frame's player
 * state (stacks/invested/folded) with a "Your action?" prompt.
 * Board and pot are from the previous frame (identical for PLAYER_ACTION).
 */
function applyQuizMask(frame, heroSeat, allFrames) {
  const prevIdx = frame.index > 0 ? frame.index - 1 : 0;
  const prev = allFrames[prevIdx];

  return {
    ...frame,
    players: clonePlayers(prev.players),
    pot: prev.pot,
    board: [...prev.board],
    label: `${prev.players[String(heroSeat)]?.name || "Hero"}'s action?`,
    actionLabel: "?",
  };
}

/**
 * Find the first hero decision frame index, or 0 if none.
 */
function firstHeroDecisionIndex(frames, heroSeat) {
  if (heroSeat === null) return 0;
  const decs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
  return decs.length > 0 ? decs[0] : 0;
}

/**
 * Find the next unanswered hero decision frame index.
 * Scans forward from startIdx+1, wraps to 0 if needed.
 * Returns -1 if all hero decisions are answered.
 */
function nextUnansweredHeroIndex(frames, heroSeat, ledger, startIdx) {
  if (heroSeat === null) return -1;
  const heroDecs = getDecisionIndices(frames).filter((i) => frames[i].actingSeat === heroSeat);
  if (heroDecs.length === 0) return -1;

  // Forward from current position
  for (const idx of heroDecs) {
    if (idx > startIdx && !ledger[idx]) return idx;
  }
  // Wrap: check from start up to current position
  for (const idx of heroDecs) {
    if (idx <= startIdx && !ledger[idx]) return idx;
  }
  return -1; // all answered
}

function jumpNextUnanswered() {
  const idx = nextUnansweredHeroIndex(replayFrames, replayHeroSeat, quizLedger, replayCursor);
  if (idx >= 0) {
    replayCursor = idx;
    quizFrameTransition(replayCursor);
    renderReplayFrame();
  }
}

/**
 * Find the index of the last frame before any settlement/outcome event.
 * Returns the frame just before the first SHOWDOWN_REVEAL or POT_AWARD.
 */
function findPreOutcomeIndex(frames) {
  const settlement = new Set(["SHOWDOWN_REVEAL", "POT_AWARD", "HAND_SUMMARY", "HAND_END"]);
  for (let i = 0; i < frames.length; i++) {
    if (settlement.has(frames[i].event)) return i > 0 ? i - 1 : 0;
  }
  return frames.length - 1;
}

// ── Replay Navigation ─────────────────────────────────────────────────────

function replayFirst() {
  replayCursor = 0;
  if (replayDecisionOnly) {
    const decs = getDecisionIndices(replayFrames);
    replayCursor = decs.length > 0 ? decs[0] : 0;
  }
  quizFrameTransition(replayCursor);
  renderReplayFrame();
}

function replayPrev() {
  if (replayDecisionOnly) {
    const decs = getDecisionIndices(replayFrames);
    const prev = decs.filter((i) => i < replayCursor);
    if (prev.length > 0) replayCursor = prev[prev.length - 1];
  } else {
    if (replayCursor > 0) replayCursor--;
  }
  quizFrameTransition(replayCursor);
  renderReplayFrame();
}

function replayNext() {
  if (replayDecisionOnly) {
    const decs = getDecisionIndices(replayFrames);
    const next = decs.filter((i) => i > replayCursor);
    if (next.length > 0) replayCursor = next[0];
  } else {
    if (replayCursor < replayFrames.length - 1) replayCursor++;
  }
  quizFrameTransition(replayCursor);
  renderReplayFrame();
}

function replayLast() {
  if (replayDecisionOnly) {
    const decs = getDecisionIndices(replayFrames);
    replayCursor = decs.length > 0 ? decs[decs.length - 1] : replayFrames.length - 1;
  } else {
    replayCursor = replayFrames.length - 1;
  }
  quizFrameTransition(replayCursor);
  renderReplayFrame();
}

function replayJumpStreet(street) {
  const idx = getStreetStartIndex(replayFrames, street);
  if (idx >= 0) replayCursor = idx;
  quizFrameTransition(replayCursor);
  renderReplayFrame();
}

function replayToggleDecisionOnly() {
  replayDecisionOnly = !replayDecisionOnly;
  renderReplayFrame();
}

// ── Replay Rendering ──────────────────────────────────────────────────────

function renderReplayFrame() {
  const el = document.getElementById("study-hands");
  if (!replayFrames || replayFrames.length === 0) return;

  const rawFrame = replayFrames[Math.min(replayCursor, replayFrames.length - 1)];
  const showdownIdx = getShowdownRevealIndex(replayFrames);
  let f = replayStudyMode && replayHeroSeat !== null
    ? applyStudyVisibility(rawFrame, replayHeroSeat, showdownIdx)
    : rawFrame;

  // Quiz masking: show pre-action state for hero decisions
  const quizMasked = replayHeroSeat !== null && isQuizMasked(rawFrame, replayHeroSeat);
  if (quizMasked) {
    f = applyQuizMask(f, replayHeroSeat, replayFrames);
    // Also apply study visibility to the masked frame
    if (replayStudyMode) f = applyStudyVisibility(f, replayHeroSeat, showdownIdx);
  }

  // Blind review: mask outcome frame labels
  const blindHidden = isBlindHidden(replaySessionId, replayHandId);
  const outcomeEvents = new Set(["POT_AWARD", "HAND_SUMMARY", "HAND_END"]);
  let displayLabel = f.label;
  if (blindHidden && outcomeEvents.has(rawFrame.event)) {
    displayLabel = "[outcome hidden]";
  }
  // Also mask SHOWDOWN_REVEAL label (reveals winning hand info)
  if (blindHidden && rawFrame.event === "SHOWDOWN_REVEAL") {
    displayLabel = "SHOWDOWN: [cards revealed — outcome hidden]";
  }

  const total = replayFrames.length;
  const decCount = getDecisionIndices(replayFrames).length;

  // Available streets for jump buttons
  const streets = [];
  const seen = new Set();
  for (const fr of replayFrames) {
    if (!seen.has(fr.street) && ["PREFLOP", "FLOP", "TURN", "RIVER", "SHOWDOWN"].includes(fr.street)) {
      streets.push(fr.street);
      seen.add(fr.street);
    }
  }

  // Player state table — freeze to pre-outcome state when blind-hidden on settlement frames
  const settlementEvents = new Set(["POT_AWARD", "HAND_SUMMARY", "HAND_END", "SHOWDOWN_REVEAL"]);
  let tablePlayers = f.players;
  if (blindHidden && settlementEvents.has(rawFrame.event)) {
    // Find last pre-settlement frame's player state
    const preOutcomeIdx = findPreOutcomeIndex(replayFrames);
    if (preOutcomeIdx >= 0) {
      const preFrame = replayStudyMode && replayHeroSeat !== null
        ? applyStudyVisibility(replayFrames[preOutcomeIdx], replayHeroSeat, showdownIdx)
        : replayFrames[preOutcomeIdx];
      tablePlayers = preFrame.players;
    }
  }

  let playerRows = "";
  for (const [s, p] of Object.entries(tablePlayers)) {
    const active = parseInt(s) === f.actingSeat ? ' style="color:#4ecca3;font-weight:bold"' : "";
    const cards = p.cards ? p.cards.join(" ") : "??";
    const status = p.folded ? " [FOLD]" : (p.allIn ? " [ALL-IN]" : "");
    playerRows += `<tr${active}><td>${p.name}</td><td>${c$(p.stack)}</td><td>${cards}</td><td>${c$(p.invested)}${status}</td></tr>`;
  }

  // Board display
  const boardStr = f.board.length > 0 ? f.board.join(" ") : "—";

  const decOnLabel = replayDecisionOnly ? "All" : "Decisions";
  const decOnTitle = replayDecisionOnly ? "Show all frames" : "Show decision points only";

  // Queue navigation
  const qTotal = studyHandCache.length;
  const qPos = studyQueueIndex + 1;
  const qPrevDis = studyQueueIndex <= 0 ? " disabled" : "";
  const qNextDis = studyQueueIndex >= qTotal - 1 ? " disabled" : "";

  el.innerHTML =
    `<div style="display:flex;align-items:center;gap:6px;padding:2px 0;border-bottom:1px solid #222">` +
    `<span class="study-back" onclick="loadStudyHands(); resetReplayState();" style="margin:0">Back</span>` +
    `<span style="flex:1"></span>` +
    `<button class="rp-btn" onclick="queuePrev()" title="Previous hand [Shift+←]"${qPrevDis}>&laquo;</button>` +
    `<span style="color:#888;font-size:9px">${qPos} / ${qTotal}</span>` +
    `<button class="rp-btn" onclick="queueNext()" title="Next hand [Shift+→]"${qNextDis}>&raquo;</button>` +
    (replayQuizMode ? `<button class="rp-btn" onclick="queueNextIncomplete()" title="Skip to next incomplete hand" style="font-size:8px">Next &#9744;</button>` : "") +
    `</div>` +
    // Frame navigation
    `<div style="display:flex;gap:3px;padding:4px 0;flex-wrap:wrap;align-items:center">` +
    `<button class="rp-btn" onclick="replayFirst()" title="First frame">|&lt;</button>` +
    `<button class="rp-btn" onclick="replayPrev()" title="Previous frame">&lt;</button>` +
    `<button class="rp-btn" onclick="replayNext()" title="Next frame">&gt;</button>` +
    `<button class="rp-btn" onclick="replayLast()" title="Last frame">&gt;|</button>` +
    `<span style="color:#888;font-size:9px;margin:0 4px">${f.index + 1}/${total}</span>` +
    `<button class="rp-btn" onclick="replayToggleDecisionOnly()" title="${decOnTitle}">${decOnLabel}</button>` +
    (replayQuizMode ? (() => {
      const nxt = nextUnansweredHeroIndex(replayFrames, replayHeroSeat, quizLedger, replayCursor);
      return nxt >= 0
        ? `<button class="rp-btn" onclick="jumpNextUnanswered()" title="Jump to next unanswered hero decision [N]" style="font-size:8px;color:#ffd700">Next &#9671;</button>`
        : "";
    })() : "") +
    `<span style="border-left:1px solid #333;height:14px;margin:0 2px"></span>` +
    streets.map((s) => `<button class="rp-btn${f.street === s ? " rp-active" : ""}" onclick="replayJumpStreet('${s}')">${s.slice(0, 3)}</button>`).join("") +
    `<span style="border-left:1px solid #333;height:14px;margin:0 2px"></span>` +
    `<button class="rp-btn${replayStudyMode ? " rp-active" : ""}" onclick="replayToggleStudyMode()" title="${replayStudyMode ? "Show all info" : "Hide unknown cards"}">${replayStudyMode ? "Full" : "Study"}</button>` +
    (replayStudyMode ? Object.entries(rawFrame.players).map(([s, p]) =>
      `<button class="rp-btn${parseInt(s) === replayHeroSeat ? " rp-active" : ""}" onclick="replaySetHero(${s})" title="Study as ${p.name}" style="font-size:8px">${p.name.slice(0, 5)}</button>`
    ).join("") : "") +
    `<button class="rp-btn${replayQuizMode ? " rp-active" : ""}" onclick="replayToggleQuizMode()" title="${replayQuizMode ? "Disable quiz" : "Quiz: hide hero actions"}">Quiz</button>` +
    `</div>` +
    // Frame summary
    `<div style="background:${quizMasked ? "#1a1a0d" : "#111"};border:1px solid ${quizMasked ? "#554400" : "#333"};border-radius:4px;padding:6px;margin:4px 0;font-size:10px">` +
    `<div style="color:#888;margin-bottom:2px">${f.street} | Board: ${boardStr} | Pot: ${c$(f.pot)}</div>` +
    (quizMasked
      ? `<div style="color:#ffd700;font-size:12px;font-weight:bold;margin:3px 0">${displayLabel}</div>` +
        `<div style="display:flex;gap:4px;margin:4px 0;align-items:center">` +
        `<span style="color:#888;font-size:9px">Your play:</span>` +
        `<button class="rp-btn${quizAnswer === "fold" ? " rp-active" : ""}" onclick="quizSelectAnswer('fold')" style="font-size:9px">Fold</button>` +
        `<button class="rp-btn${quizAnswer === "passive" ? " rp-active" : ""}" onclick="quizSelectAnswer('passive')" style="font-size:9px">Check/Call</button>` +
        `<button class="rp-btn${quizAnswer === "aggressive" ? " rp-active" : ""}" onclick="quizSelectAnswer('aggressive')" style="font-size:9px">Bet/Raise</button>` +
        `<button class="rp-btn" onclick="quizReveal()" style="color:#ffd700;border-color:#ffd700;margin-left:4px;font-size:9px">Reveal</button>` +
        `</div>`
      : (quizResult
        ? `<div style="color:#e0e0e0;font-size:11px;font-weight:bold;margin:3px 0">${displayLabel} ` +
          `<span style="color:${quizResult === "match" ? "#4ecca3" : "#e88"};font-size:10px;margin-left:6px">${quizResult === "match" ? "Match" : "Different"}</span>` +
          (quizAnswer ? ` <span style="color:#888;font-size:9px">(you: ${quizAnswer})</span>` : "") +
          (replayQuizMode && rawFrame.isDecision && rawFrame.actingSeat === replayHeroSeat
            ? ` <button class="rp-btn" onclick="clearSpotQuiz()" style="font-size:8px;margin-left:4px" title="Clear and re-answer this spot">Redo</button>`
            : "") +
          `</div>`
        : `<div style="color:#e0e0e0;font-size:11px;font-weight:bold;margin:3px 0">${displayLabel}</div>`)) +
    (blindHidden && outcomeEvents.has(rawFrame.event)
      ? `<div><button class="rp-btn" onclick="blindRevealHand()" style="font-size:9px;color:#ffd700;border-color:#ffd700;margin:2px 0">Reveal outcome</button></div>`
      : "") +
    `<table class="stat-table" style="margin-top:4px"><tr style="color:#666"><td>Player</td><td>Stack</td><td>Cards</td><td>Invested</td></tr>` +
    playerRows +
    `</table></div>` +
    // Quiz summary (if quiz mode active)
    (replayQuizMode ? renderQuizSummary() : "") +
    // Annotation panel
    renderAnnotationPanel(f) +
    // Full timeline (collapsed reference) — hidden in blind mode until revealed
    (blindHidden
      ? `<div style="color:#555;font-size:9px;margin-top:4px">[timeline hidden — blind review]</div>`
      : `<details style="margin-top:4px"><summary style="color:#555;font-size:9px;cursor:pointer">Full timeline</summary>` +
        `<pre style="padding:4px 0; font-size:9px; line-height:1.4; white-space:pre-wrap; color:#888">${formatTimeline(replayHandEvents).join("\n")}</pre>` +
        `</details>`);
}

function renderQuizSummary() {
  if (!replayFrames || replayFrames.length === 0 || replayHeroSeat === null) return "";

  const heroDecisions = getDecisionIndices(replayFrames).filter((i) => replayFrames[i].actingSeat === replayHeroSeat);
  const totalHero = heroDecisions.length;
  const answered = Object.keys(quizLedger).length;
  const matches = Object.values(quizLedger).filter((e) => e.result === "match").length;
  const diffs = Object.values(quizLedger).filter((e) => e.result === "different").length;

  if (totalHero === 0) return "";

  const matchColor = matches > 0 ? "#4ecca3" : "#888";
  const diffColor = diffs > 0 ? "#e88" : "#888";

  // Per-hand summary
  let html = `<div style="display:flex;gap:8px;padding:3px 6px;font-size:9px;color:#888;border:1px solid #222;border-radius:3px;margin:2px 0;align-items:center">` +
    `<span>Hand: ${answered}/${totalHero}</span>` +
    `<span style="color:${matchColor}">${matches} match</span>` +
    `<span style="color:${diffColor}">${diffs} diff</span>` +
    (answered === totalHero && totalHero > 0
      ? `<span style="color:#ffd700">${matches}/${totalHero} = ${Math.round(100 * matches / totalHero)}%</span>`
      : "") +
    (answered > 0 ? `<button class="rp-btn" onclick="resetHandQuiz()" style="font-size:8px;margin-left:auto" title="Clear answers and retry this hand">Retry</button>` : "") +
    `</div>`;

  // Queue-level summary (aggregate across all hands in current queue)
  const qVals = Object.values(queueQuizAccum);
  if (qVals.length > 0) {
    const qHands = qVals.length;
    const qAnswered = qVals.reduce((s, v) => s + v.answered, 0);
    const qMatches = qVals.reduce((s, v) => s + v.matches, 0);
    const qDiffs = qVals.reduce((s, v) => s + v.diffs, 0);
    const qTotal = studyHandCache.length;
    const qPct = qAnswered > 0 ? Math.round(100 * qMatches / qAnswered) : 0;

    const qCompleted = qVals.filter((v) => v.totalHero > 0 && v.answered >= v.totalHero).length;
    html += `<div style="display:flex;gap:8px;padding:3px 6px;font-size:9px;color:#666;border:1px solid #1a1a1a;border-radius:3px;margin:1px 0;align-items:center">` +
      `<span>Queue: ${qHands}/${qTotal} hands (${qCompleted} done)</span>` +
      `<span>${qAnswered} ans</span>` +
      `<span style="color:${qMatches > 0 ? "#4ecca3" : "#666"}">${qMatches} match</span>` +
      `<span style="color:${qDiffs > 0 ? "#e88" : "#666"}">${qDiffs} diff</span>` +
      (qAnswered > 0 ? `<span style="color:#aaa;margin-left:auto">${qPct}%</span>` : "") +
      `</div>`;
  }

  return html;
}

function renderAnnotationPanel(currentFrame) {
  // Existing notes for this hand
  let notesHtml = "";
  if (replayAnnotations.length > 0) {
    notesHtml = replayAnnotations.map((a) => {
      const frameBadge = a.frameIndex != null
        ? `<span style="color:#4ecca3;cursor:pointer" onclick="replayCursor=${a.frameIndex};renderReplayFrame()" title="Jump to frame">#${a.frameIndex} ${a.street || ""}</span> `
        : '<span style="color:#555">hand</span> ';
      const tagBadge = a.tag ? `<span class="ann-tag">${a.tag}</span> ` : "";
      return `<div class="ann-entry">` +
        `${frameBadge}${tagBadge}` +
        `<span class="ann-text">${a.text}</span>` +
        `<span class="ann-del" onclick="deleteAnnotation('${a.id}')" title="Delete">x</span>` +
        `</div>`;
    }).join("");
  } else {
    notesHtml = '<div style="color:#555;font-size:9px;padding:2px 0">No notes yet</div>';
  }

  // Count notes on current frame
  const frameNotes = replayAnnotations.filter((a) => a.frameIndex === currentFrame.index);
  const frameNoteBadge = frameNotes.length > 0 ? ` <span style="color:#ffd700;font-size:9px">(${frameNotes.length} note${frameNotes.length > 1 ? "s" : ""} on this frame)</span>` : "";

  return `<div style="border:1px solid #333;border-radius:4px;padding:4px 6px;margin:4px 0;font-size:10px;background:#0d0d1a">` +
    `<div style="color:#888;margin-bottom:3px">Notes${frameNoteBadge}</div>` +
    notesHtml +
    `<div style="display:flex;gap:3px;margin-top:4px;align-items:center">` +
    `<select id="ann-tag" style="font-size:9px;background:#1a1a2e;color:#ccc;border:1px solid #444;border-radius:2px;padding:1px 2px">` +
    `<option value="">—</option><option value="mistake">mistake</option><option value="interesting">interesting</option><option value="question">question</option><option value="good">good</option><option value="review">review</option></select>` +
    `<input id="ann-text" type="text" placeholder="Add note..." style="flex:1;font-size:9px;background:#1a1a2e;color:#ccc;border:1px solid #444;border-radius:2px;padding:2px 4px;font-family:inherit">` +
    `<label style="font-size:9px;color:#888;white-space:nowrap"><input type="checkbox" id="ann-frame-link" checked> frame</label>` +
    `<button class="rp-btn" onclick="addAnnotation()" style="font-size:9px">+</button>` +
    `</div></div>`;
}

// ── viewStudyHand with replay ─────────────────────────────────────────────

function snapshotQuizToQueue() {
  if (replaySessionId && replayHandId) {
    const key = replaySessionId + "/" + replayHandId;
    const vals = Object.values(quizLedger);
    const totalHero = replayHeroSeat !== null
      ? getDecisionIndices(replayFrames).filter((i) => replayFrames[i].actingSeat === replayHeroSeat).length
      : 0;
    // Always write: even 0 answered means "visited"
    queueQuizAccum[key] = {
      answered: vals.length,
      matches: vals.filter((e) => e.result === "match").length,
      diffs: vals.filter((e) => e.result === "different").length,
      totalHero,
    };
  }
}

function viewStudyHand(index) {
  const h = studyHandCache[index];
  if (!h) return;

  // Snapshot outgoing hand's quiz results before resetting
  snapshotQuizToQueue();

  studyQueueIndex = index;

  send("GET_HAND_EVENTS", { sessionId: h.sessionId, handId: h.handId }, (resp) => {
    if (!resp.ok || !resp.events || resp.events.length === 0) return;

    resetReplayState();
    replaySessionId = h.sessionId;
    replayHandId = h.handId;
    replayHandEvents = resp.events;
    replayFrames = compileFrames(resp.events);
    replayHeroSeat = inferHeroSeat(replayFrames);
    replayCursor = replayQuizMode ? firstHeroDecisionIndex(replayFrames, replayHeroSeat) : 0;

    // Register this hand in the queue accumulator (marks as visited)
    snapshotQuizToQueue();

    // Load annotations then render
    loadReplayAnnotations(() => renderReplayFrame());
  });
}

function queuePrev() {
  if (studyQueueIndex > 0) viewStudyHand(studyQueueIndex - 1);
}

function queueNext() {
  if (studyQueueIndex < studyHandCache.length - 1) viewStudyHand(studyQueueIndex + 1);
}

function queueNextIncomplete() {
  for (let i = studyQueueIndex + 1; i < studyHandCache.length; i++) {
    const h = studyHandCache[i];
    if (handReviewState(h.sessionId, h.handId) !== "completed") {
      viewStudyHand(i);
      return;
    }
  }
  // Wrap around from start
  for (let i = 0; i < studyQueueIndex; i++) {
    const h = studyHandCache[i];
    if (handReviewState(h.sessionId, h.handId) !== "completed") {
      viewStudyHand(i);
      return;
    }
  }
}

function loadReplayAnnotations(callback) {
  if (!replaySessionId || !replayHandId) { replayAnnotations = []; if (callback) callback(); return; }
  send("GET_ANNOTATIONS", { sessionId: replaySessionId, handId: replayHandId }, (resp) => {
    replayAnnotations = (resp.ok && resp.state && resp.state.annotations) ? resp.state.annotations : [];
    if (callback) callback();
  });
}

function addAnnotation() {
  if (!replaySessionId || !replayHandId) return;
  const textEl = document.getElementById("ann-text");
  const tagEl = document.getElementById("ann-tag");
  const frameEl = document.getElementById("ann-frame-link");
  if (!textEl || !textEl.value.trim()) return;

  const f = replayFrames[replayCursor];
  const useFrame = frameEl && frameEl.checked;
  const payload = {
    sessionId: replaySessionId,
    handId: replayHandId,
    text: textEl.value.trim(),
    tag: tagEl ? tagEl.value : "",
    frameIndex: useFrame ? replayCursor : null,
    street: useFrame && f ? f.street : null,
  };

  send("ADD_ANNOTATION", payload, (resp) => {
    if (!resp.ok) return;
    textEl.value = "";
    loadReplayAnnotations(() => renderReplayFrame());
  });
}

function deleteAnnotation(annotationId) {
  if (!replaySessionId) return;
  send("DELETE_ANNOTATION", { sessionId: replaySessionId, annotationId }, (resp) => {
    if (!resp.ok) return;
    loadReplayAnnotations(() => renderReplayFrame());
  });
}

// ── Status ─────────────────────────────────────────────────────────────────

function setStatus(cls, text) {
  const el = document.getElementById("status");
  el.className = cls;
  el.textContent = text;
}

// ── Init ───────────────────────────────────────────────────────────────────

connect();
