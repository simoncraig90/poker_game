// ═══════════════════════════════════════════════════════════════════════════
//  Poker Bot — Browser WebSocket Client
//  Connects to the poker server, seats itself, and plays automatically.
// ═══════════════════════════════════════════════════════════════════════════

// ── Hand Strength (inlined for browser) ───────────────────────────────────

const PREMIUM_PAIRS = new Set([14, 13, 12, 11]);
const MEDIUM_PAIRS = new Set([10, 9, 8]);

function preflopScore(c1, c2) {
  const r1 = Math.max(c1.rank, c2.rank);
  const r2 = Math.min(c1.rank, c2.rank);
  const suited = c1.suit === c2.suit;
  const pair = r1 === r2;
  const gap = r1 - r2;
  const connected = gap === 1;

  if (pair) {
    if (PREMIUM_PAIRS.has(r1)) return 0.95 - (14 - r1) * 0.02;
    if (MEDIUM_PAIRS.has(r1)) return 0.70 + (r1 - 8) * 0.03;
    return 0.50 + (r1 - 2) * 0.02;
  }

  let score = 0;
  score += (r1 - 2) * 0.03;
  score += (r2 - 2) * 0.01;
  if (suited) score += 0.06;
  if (connected) score += 0.04;
  else if (gap === 2) score += 0.02;

  if (r1 === 14 && r2 === 13) score = suited ? 0.87 : 0.82;
  if (r1 === 14 && r2 === 12) score = suited ? 0.80 : 0.74;
  if (r1 === 14 && r2 === 11) score = suited ? 0.76 : 0.70;
  if (r1 === 14 && r2 === 10) score = suited ? 0.72 : 0.65;
  if (r1 === 13 && r2 === 12) score = suited ? 0.78 : 0.72;
  if (r1 === 13 && r2 === 11) score = suited ? 0.73 : 0.66;

  return Math.min(score, 0.99);
}

const HAND_CATEGORY = {
  HIGH_CARD: 0, PAIR: 1, TWO_PAIR: 2, THREE_OF_A_KIND: 3,
  STRAIGHT: 4, FLUSH: 5, FULL_HOUSE: 6, FOUR_OF_A_KIND: 7, STRAIGHT_FLUSH: 8,
};

function checkStraight(sortedUniqueRanks) {
  const ranks = [...sortedUniqueRanks];
  if (ranks.includes(14)) ranks.push(1);
  const sorted = [...new Set(ranks)].sort((a, b) => b - a);
  for (let i = 0; i <= sorted.length - 5; i++) {
    if (sorted[i] - sorted[i + 4] === 4) return true;
  }
  return false;
}

function evaluateHand(holeCards, board) {
  const all = [...holeCards, ...board];
  const ranks = all.map(c => c.rank);
  const suits = all.map(c => c.suit);
  const rankCounts = {};
  for (const r of ranks) rankCounts[r] = (rankCounts[r] || 0) + 1;
  const suitCounts = {};
  for (const s of suits) suitCounts[s] = (suitCounts[s] || 0) + 1;
  const counts = Object.values(rankCounts).sort((a, b) => b - a);
  const uniqueRanks = Object.keys(rankCounts).map(Number).sort((a, b) => b - a);

  let flushSuit = null;
  for (const [s, count] of Object.entries(suitCounts)) {
    if (count >= 5) flushSuit = Number(s);
  }
  const hasStraight = all.length >= 5 && checkStraight(uniqueRanks);

  if (flushSuit !== null && hasStraight) {
    const flushRanks = all.filter(c => c.suit === flushSuit).map(c => c.rank);
    if (checkStraight([...new Set(flushRanks)].sort((a, b) => b - a))) {
      return { category: HAND_CATEGORY.STRAIGHT_FLUSH, strength: 0.99 };
    }
  }
  if (counts[0] === 4) return { category: HAND_CATEGORY.FOUR_OF_A_KIND, strength: 0.96 };
  if (counts[0] === 3 && counts[1] >= 2) {
    const tripRank = Number(Object.keys(rankCounts).find(r => rankCounts[r] === 3));
    return { category: HAND_CATEGORY.FULL_HOUSE, strength: 0.90 + (tripRank - 2) * 0.003 };
  }
  if (flushSuit !== null) {
    const highFlush = Math.max(...all.filter(c => c.suit === flushSuit).map(c => c.rank));
    return { category: HAND_CATEGORY.FLUSH, strength: 0.82 + (highFlush - 2) * 0.005 };
  }
  if (hasStraight) return { category: HAND_CATEGORY.STRAIGHT, strength: 0.75 + (uniqueRanks[0] - 5) * 0.005 };
  if (counts[0] === 3) {
    const tripRank = Number(Object.keys(rankCounts).find(r => rankCounts[r] === 3));
    return { category: HAND_CATEGORY.THREE_OF_A_KIND, strength: 0.65 + (tripRank - 2) * 0.003 };
  }
  if (counts[0] === 2 && counts[1] === 2) {
    const pairs = Object.keys(rankCounts).filter(r => rankCounts[r] === 2).map(Number).sort((a, b) => b - a);
    return { category: HAND_CATEGORY.TWO_PAIR, strength: 0.50 + pairs[0] * 0.008 + pairs[1] * 0.003 };
  }
  if (counts[0] === 2) {
    const pairRank = Number(Object.keys(rankCounts).find(r => rankCounts[r] === 2));
    const usesHole = holeCards.some(c => c.rank === pairRank);
    return { category: HAND_CATEGORY.PAIR, strength: (usesHole ? 0.35 : 0.25) + (pairRank - 2) * 0.01 };
  }
  const highRank = Math.max(...holeCards.map(c => c.rank));
  return { category: HAND_CATEGORY.HIGH_CARD, strength: 0.05 + (highRank - 2) * 0.015 };
}

function countDraws(holeCards, board) {
  const all = [...holeCards, ...board];
  const suitCounts = {};
  for (const c of all) suitCounts[c.suit] = (suitCounts[c.suit] || 0) + 1;
  const flushDraw = Object.values(suitCounts).some(c => c === 4);
  const ranks = [...new Set(all.map(c => c.rank))].sort((a, b) => a - b);
  let straightDraw = false;
  for (let i = 0; i <= ranks.length - 4; i++) {
    if (ranks[i + 3] - ranks[i] === 3) { straightDraw = true; break; }
  }
  return { flushDraw, straightDraw };
}

// ── Strategy ──────────────────────────────────────────────────────────────

const STYLE_MODIFIERS = {
  TAG:  { openAdj: 0, callAdj: 0, aggrAdj: 0 },
  LAG:  { openAdj: -0.15, callAdj: -0.10, aggrAdj: 0.10 },
  ROCK: { openAdj: 0.15, callAdj: 0.10, aggrAdj: -0.05 },
  FISH: { openAdj: -0.20, callAdj: -0.20, aggrAdj: -0.15 },
};

const BASE_OPEN = { EP: 0.72, MP: 0.60, LP: 0.45, BTN: 0.35, SB: 0.50, BB: 0.30 };

function getPosition(seatIdx, buttonSeat, numPlayers, maxSeats) {
  const dist = (seatIdx - buttonSeat + maxSeats) % maxSeats;
  if (dist === 0) return "BTN";
  if (numPlayers <= 3) return dist === 1 ? "SB" : "BB";
  if (dist <= 1) return "SB";
  if (dist <= 2) return "BB";
  if (dist <= Math.floor(numPlayers / 3)) return "EP";
  if (dist <= Math.floor((numPlayers * 2) / 3)) return "MP";
  return "LP";
}

function decide(params, style) {
  const { hand, seat, legalActions, bb, button, numPlayers, maxSeats } = params;
  const { actions: legal, callAmount, minBet, minRaise, maxRaise } = legalActions;
  if (legal.length === 0) return { action: "FOLD" };
  if (legal.length === 1) return { action: legal[0] };

  const mod = STYLE_MODIFIERS[style] || STYLE_MODIFIERS.TAG;
  const position = getPosition(seat.seat !== undefined ? seat.seat : seat.seatIdx, button, numPlayers, maxSeats);
  const potSize = hand.pot;

  if (hand.phase === "PREFLOP") {
    return decidePreflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand, mod });
  }
  return decidePostflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand, mod });
}

function decidePreflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand, mod }) {
  const cards = seat.holeCards;
  if (!cards || cards.length < 2) {
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }
  const score = preflopScore(cards[0], cards[1]);
  const threshold = Math.max(0.10, (BASE_OPEN[position] || 0.50) + mod.openAdj);

  const preflopActions = (hand.actions || []).filter(
    a => a.street === "PREFLOP" && a.type !== "BLIND_SB" && a.type !== "BLIND_BB"
  );
  const raises = preflopActions.filter(a => a.type === "RAISE" || a.type === "BET").length;
  const facingRaise = callAmount > bb;
  const facingThreebet = raises >= 2;

  // Premium
  if (score >= 0.85) {
    if (legal.includes("RAISE")) {
      const sz = facingRaise
        ? Math.min(Math.round(callAmount * 3), maxRaise)
        : Math.min(bb * 3, maxRaise);
      return { action: "RAISE", amount: Math.max(sz, minRaise) };
    }
    if (legal.includes("BET")) return { action: "BET", amount: Math.min(bb * 3, seat.stack) };
    if (legal.includes("CALL")) return { action: "CALL" };
    return { action: "CHECK" };
  }

  // Strong
  if (score >= 0.65 + mod.callAdj) {
    if (facingThreebet) {
      if (score >= 0.75 && legal.includes("CALL")) return { action: "CALL" };
      return legal.includes("FOLD") ? { action: "FOLD" } : { action: "CALL" };
    }
    if (!facingRaise) {
      if (legal.includes("RAISE")) return { action: "RAISE", amount: Math.max(Math.min(bb * 3, maxRaise), minRaise) };
      if (legal.includes("BET")) return { action: "BET", amount: Math.min(bb * 3, seat.stack) };
    }
    if (legal.includes("CALL")) return { action: "CALL" };
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  // Playable
  if (score >= threshold) {
    if (!facingRaise) {
      if (legal.includes("RAISE")) return { action: "RAISE", amount: Math.max(Math.min(bb * 3, maxRaise), minRaise) };
      if (legal.includes("BET")) return { action: "BET", amount: Math.min(bb * 3, seat.stack) };
      if (legal.includes("CHECK")) return { action: "CHECK" };
    }
    if (facingRaise && !facingThreebet && callAmount <= bb * (4 - mod.callAdj * 10)) {
      if (legal.includes("CALL")) return { action: "CALL" };
    }
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  if (legal.includes("CHECK")) return { action: "CHECK" };
  return { action: "FOLD" };
}

function decidePostflop({ seat, legal, callAmount, minBet, minRaise, maxRaise, bb, position, potSize, hand, mod }) {
  const cards = seat.holeCards;
  const board = hand.board || [];
  if (!cards || cards.length < 2) {
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }

  const ev = evaluateHand(cards, board);
  const draws = countDraws(cards, board);
  const strength = ev.strength;
  const category = ev.category;
  const potOdds = callAmount > 0 ? callAmount / (potSize + callAmount) : 0;

  function valAction(sizeFrac) {
    const bet = Math.max(Math.round(potSize * (sizeFrac + mod.aggrAdj)), bb);
    if (legal.includes("BET")) return { action: "BET", amount: Math.max(Math.min(bet, maxRaise || bet), minBet) };
    if (legal.includes("RAISE") && minRaise > 0) return { action: "RAISE", amount: Math.min(Math.max(bet, minRaise), maxRaise) };
    if (legal.includes("CHECK")) return { action: "CHECK" };
    if (legal.includes("CALL")) return { action: "CALL" };
    return { action: "FOLD" };
  }

  if (category >= HAND_CATEGORY.FULL_HOUSE) return valAction(0.75);
  if (category >= HAND_CATEGORY.STRAIGHT || (category === HAND_CATEGORY.THREE_OF_A_KIND && strength > 0.68)) return valAction(0.65);

  if (category >= HAND_CATEGORY.TWO_PAIR || strength >= 0.45) {
    if (callAmount > 0) {
      if (potOdds < 0.40 && legal.includes("CALL")) return { action: "CALL" };
      if (category >= HAND_CATEGORY.TWO_PAIR && strength >= 0.55 && legal.includes("RAISE")) {
        return { action: "RAISE", amount: Math.min(Math.max(Math.round(potSize * 0.7) + (seat.bet || 0), minRaise), maxRaise) };
      }
      if (legal.includes("CALL")) return { action: "CALL" };
    }
    return valAction(0.55);
  }

  if (category >= HAND_CATEGORY.PAIR && strength >= 0.30) {
    if (callAmount > 0) {
      if (potOdds < 0.30 && legal.includes("CALL")) return { action: "CALL" };
      return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
    }
    if (hand.phase === "FLOP" || hand.phase === "TURN") return valAction(0.40);
    if (legal.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  if (draws.flushDraw || draws.straightDraw) {
    const drawStr = draws.flushDraw ? 0.35 : 0.30;
    if (callAmount > 0) {
      if (potOdds < drawStr && legal.includes("CALL")) return { action: "CALL" };
      return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
    }
    if (hand.phase !== "RIVER") return valAction(0.50);
    if (legal.includes("CHECK")) return { action: "CHECK" };
    return { action: "FOLD" };
  }

  if (callAmount > 0) {
    if (hand.phase === "RIVER" && potOdds < 0.15 && legal.includes("CALL")) return { action: "CALL" };
    return legal.includes("CHECK") ? { action: "CHECK" } : { action: "FOLD" };
  }
  if (legal.includes("CHECK")) return { action: "CHECK" };
  return { action: "FOLD" };
}

// ── Card parsing (server sends display strings like "As", "Td") ───────────

const RANK_MAP = { "2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"T":10,"J":11,"Q":12,"K":13,"A":14 };
const SUIT_MAP = { "c":1,"d":2,"h":3,"s":4 };

function parseCard(display) {
  if (typeof display === "object" && display.rank) return display; // already parsed
  const r = RANK_MAP[display[0]];
  const s = SUIT_MAP[display[1]];
  return { rank: r || 0, suit: s || 0, display };
}

function parseCards(arr) {
  if (!arr) return [];
  return arr.map(c => typeof c === "string" ? parseCard(c) : c);
}

// ── Bot Instance ──────────────────────────────────────────────────────────

class PokerBot {
  constructor({ name, seatIdx, buyIn, style, delay, onLog }) {
    this.name = name;
    this.seatIdx = seatIdx;
    this.buyIn = buyIn;
    this.style = style || "TAG";
    this.delay = delay != null ? delay : 800;
    this.onLog = onLog || (() => {});
    this.ws = null;
    this.state = null;
    this.sessionId = null;
    this.running = false;
    this.seated = false;
    this.msgId = 0;
    this.handsPlayed = 0;
    this.actionsTaken = 0;
    this.holeCards = null; // our private cards (from HERO_CARDS events)
    this.pendingAction = false;
  }

  connect() {
    this.ws = new WebSocket(`ws://${location.host}`);
    this.ws.onopen = () => this.log("info", "Connected to server");
    this.ws.onclose = () => {
      this.log("error", "Disconnected");
      if (this.running) setTimeout(() => this.connect(), 2000);
    };
    this.ws.onerror = () => {};
    this.ws.onmessage = (evt) => this._handleMessage(JSON.parse(evt.data));
  }

  disconnect() {
    this.running = false;
    if (this.ws) { this.ws.close(); this.ws = null; }
  }

  send(cmd, payload) {
    if (!this.ws || this.ws.readyState !== 1) return;
    const id = `bot-${this.name}-${++this.msgId}`;
    this.ws.send(JSON.stringify({ id, cmd, payload: payload || {} }));
    return id;
  }

  seat() {
    this.log("action", `Seating at seat ${this.seatIdx} with ${this.buyIn} buy-in`);
    this.send("SEAT_PLAYER", { seat: this.seatIdx, name: this.name, buyIn: this.buyIn, country: "BOT" });
    this.seated = true;
  }

  start() {
    this.running = true;
    this.log("info", `Bot started (${this.style} style, ${this.delay}ms delay)`);
    // Check if we need to act right now
    this._checkAction();
  }

  stop() {
    this.running = false;
    this.log("info", "Bot stopped");
  }

  // ── Message handler ─────────────────────────────────────────────────────

  _handleMessage(msg) {
    if (msg.welcome) {
      this.sessionId = msg.sessionId;
      this.state = msg.state;
      this.log("info", `Session: ${msg.sessionId} (${msg.eventCount} events)`);
      this._checkAction();
      return;
    }

    if (msg.broadcast) {
      this._processEvents(msg.events);
      // Refresh state after broadcast
      this.send("GET_STATE");
      return;
    }

    if (msg.ok === false) {
      this.log("error", msg.error);
      this.pendingAction = false;
      return;
    }

    if (msg.events && msg.events.length > 0) {
      this._processEvents(msg.events);
    }
    if (msg.state && msg.state.seats) {
      this.state = msg.state;
      this._checkAction();
    }
  }

  _processEvents(events) {
    for (const e of events) {
      // Capture our hole cards from HERO_CARDS events
      if (e.type === "HERO_CARDS" && e.seat === this.seatIdx) {
        this.holeCards = parseCards(e.cards);
        this.log("event", `Dealt: ${e.cards.join(" ")}`);
      }
      if (e.type === "HAND_START") {
        this.holeCards = null;
        this.log("event", `Hand #${e.handId} started (button seat ${e.button})`);
      }
      if (e.type === "DEAL_COMMUNITY") {
        this.log("event", `${e.street}: ${(e.newCards || e.cards || []).join(" ")}`);
      }
      if (e.type === "PLAYER_ACTION" && e.seat === this.seatIdx) {
        this.pendingAction = false;
      }
      if (e.type === "POT_AWARD") {
        for (const a of (e.awards || [])) {
          if (a.seat === this.seatIdx) this.log("action", `Won ${a.amount}!`);
        }
      }
      if (e.type === "HAND_SUMMARY") {
        this.handsPlayed++;
      }
      if (e.type === "HAND_END") {
        this.holeCards = null;
        // Auto-deal next hand if enabled
        this._autoDeal();
      }
    }
  }

  // ── Decision logic ──────────────────────────────────────────────────────

  _checkAction() {
    if (!this.running || !this.state || this.pendingAction) return;
    const hand = this.state.hand;
    if (!hand || hand.phase === "COMPLETE") return;
    if (hand.actionSeat !== this.seatIdx) return;

    const legalActions = hand.legalActions;
    if (!legalActions || legalActions.actions.length === 0) return;

    // Build seat info with our private hole cards
    const seatState = this.state.seats[this.seatIdx];
    if (!seatState || seatState.status === "EMPTY") return;

    const seatWithCards = {
      ...seatState,
      seat: this.seatIdx,
      holeCards: this.holeCards || parseCards(seatState.holeCards),
    };

    // Parse board cards
    const handWithParsedBoard = {
      ...hand,
      board: parseCards(hand.board),
    };

    const numPlayers = Object.values(this.state.seats).filter(s => s.status === "OCCUPIED").length;

    const decision = decide({
      hand: handWithParsedBoard,
      seat: seatWithCards,
      legalActions,
      bb: this.state.bb,
      button: hand.button != null ? hand.button : this.state.button,
      numPlayers,
      maxSeats: this.state.maxSeats,
    }, this.style);

    this.pendingAction = true;
    const delayMs = this.delay + Math.floor(Math.random() * 300); // add jitter

    setTimeout(() => {
      if (!this.running) { this.pendingAction = false; return; }

      const payload = { seat: this.seatIdx, action: decision.action };
      if (decision.amount != null) payload.amount = decision.amount;

      const amtStr = decision.amount != null ? ` ${decision.amount}` : "";
      this.log("action", `${decision.action}${amtStr} (${hand.phase})`);
      this.actionsTaken++;
      this.send("PLAYER_ACTION", payload);
    }, delayMs);
  }

  _autoDeal() {
    if (!this.running) return;
    const autoDeal = document.getElementById("auto-deal");
    if (!autoDeal || autoDeal.value !== "on") return;

    // Only the lowest-numbered active bot triggers the deal to avoid duplicates
    const activeBots = allBots.filter(b => b.running);
    if (activeBots.length === 0) return;
    activeBots.sort((a, b) => a.seatIdx - b.seatIdx);
    if (activeBots[0] !== this) return;

    setTimeout(() => {
      if (!this.running) return;
      this.log("info", "Auto-dealing next hand...");
      this.send("START_HAND");
    }, 1000);
  }

  log(tag, message) {
    this.onLog(this.name, tag, message);
  }
}

// ── Global bot management ─────────────────────────────────────────────────

const allBots = [];

function logToUI(botName, tag, message) {
  const logArea = document.getElementById("log-area");
  const div = document.createElement("div");
  div.className = "log-line";
  const ts = new Date().toLocaleTimeString();
  const tagClass = { info: "tag-info", action: "tag-action", event: "tag-event", error: "tag-error", state: "tag-state" }[tag] || "tag-info";
  div.innerHTML = `<span class="ts">${ts}</span><span class="tag ${tagClass}">[${botName}]</span> ${message}`;
  logArea.appendChild(div);
  logArea.scrollTop = logArea.scrollHeight;

  // Update stats
  updateStats();
  updateRoster();
}

function updateStats() {
  let totalHands = 0, totalActions = 0;
  for (const b of allBots) {
    totalHands += b.handsPlayed;
    totalActions += b.actionsTaken;
  }
  document.getElementById("hands-count").textContent = totalHands;
  document.getElementById("actions-count").textContent = totalActions;

  const anyConnected = allBots.some(b => b.ws && b.ws.readyState === 1);
  const dot = document.getElementById("ws-dot");
  const label = document.getElementById("ws-label");
  dot.className = anyConnected ? "status-dot on" : "status-dot off";
  label.textContent = anyConnected ? `Connected (${allBots.length} bots)` : "Disconnected";

  if (allBots.length > 0 && allBots[0].sessionId) {
    document.getElementById("session-label").textContent = allBots[0].sessionId.slice(-12);
  }
}

function updateRoster() {
  const roster = document.getElementById("bot-roster");
  if (allBots.length === 0) { roster.innerHTML = ""; return; }
  roster.innerHTML = allBots.map(b => {
    const statusColor = b.running ? "#4ecca3" : "#888";
    const statusText = b.running ? "Playing" : (b.seated ? "Seated" : "Idle");
    return `<div class="bot-row">
      <span class="bot-name">${b.name}</span>
      <span class="bot-seat">Seat ${b.seatIdx}</span>
      <span class="bot-status" style="color:${statusColor}">${statusText}</span>
      <span class="bot-hands">${b.handsPlayed} hands / ${b.actionsTaken} actions</span>
    </div>`;
  }).join("");
}

// ── UI Button handlers ────────────────────────────────────────────────────

function getConfig() {
  return {
    name: document.getElementById("bot-name").value || "Bot",
    seatIdx: parseInt(document.getElementById("bot-seat").value),
    buyIn: parseInt(document.getElementById("bot-buyin").value) || 1000,
    style: document.getElementById("bot-style").value,
    delay: parseInt(document.getElementById("bot-delay").value) || 800,
  };
}

function seatBot() {
  const cfg = getConfig();
  let bot = allBots.find(b => b.seatIdx === cfg.seatIdx);
  if (!bot) {
    bot = new PokerBot({ ...cfg, onLog: logToUI });
    allBots.push(bot);
    bot.connect();
    // Wait for connection before seating
    setTimeout(() => bot.seat(), 500);
  } else {
    bot.seat();
  }
}

function startBot() {
  const cfg = getConfig();
  let bot = allBots.find(b => b.seatIdx === cfg.seatIdx);
  if (!bot) {
    bot = new PokerBot({ ...cfg, onLog: logToUI });
    allBots.push(bot);
    bot.connect();
    setTimeout(() => { bot.seat(); setTimeout(() => bot.start(), 500); }, 500);
  } else {
    bot.start();
  }
  document.getElementById("stop-btn").disabled = false;
}

function stopBot() {
  const cfg = getConfig();
  const bot = allBots.find(b => b.seatIdx === cfg.seatIdx);
  if (bot) bot.stop();
}

function clearLog() {
  document.getElementById("log-area").innerHTML = "";
}

// ── Multi-bot ─────────────────────────────────────────────────────────────

const BOT_NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"];

function spawnMultiBots() {
  const count = parseInt(document.getElementById("multi-count").value) || 3;
  const style = document.getElementById("bot-style").value;
  const delay = parseInt(document.getElementById("bot-delay").value) || 800;
  const buyIn = parseInt(document.getElementById("bot-buyin").value) || 1000;

  // Stop and disconnect existing bots
  for (const b of allBots) { b.stop(); b.disconnect(); }
  allBots.length = 0;

  for (let i = 0; i < Math.min(count, 6); i++) {
    const bot = new PokerBot({
      name: BOT_NAMES[i],
      seatIdx: i,
      buyIn,
      style,
      delay,
      onLog: logToUI,
    });
    allBots.push(bot);
    bot.connect();
    // Stagger seating to avoid race conditions
    setTimeout(() => bot.seat(), 600 + i * 400);
  }

  logToUI("System", "info", `Spawned ${count} bots`);
  updateRoster();
}

function startAllBots() {
  for (const b of allBots) b.start();
  document.getElementById("stop-btn").disabled = false;
  // Auto-deal the first hand after a short delay
  setTimeout(() => {
    if (allBots.length >= 2 && allBots[0].ws && allBots[0].ws.readyState === 1) {
      allBots[0].send("START_HAND");
    }
  }, 2000);
}

function stopAllBots() {
  for (const b of allBots) b.stop();
  document.getElementById("stop-btn").disabled = true;
}

// Initial roster render
updateRoster();
