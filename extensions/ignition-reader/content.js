/**
 * Ignition Casino DOM Reader - Content Script
 *
 * Reads poker game state from Ignition/Bovada/Bodog browser poker client.
 * Uses MutationObserver to detect DOM changes in real time.
 *
 * DOM structure (from PokerEye+ and IgnitionHUD research):
 *   Cards:    data-qa="card-As" (Ace of spades), data-qa="card-Td" (Ten of diamonds)
 *   Players:  data-qa="playerContainer-{seatID}"
 *   Tags:     data-qa="playerTag" (FOLD, CHECK, CALL, BET, RAISE, ALL-IN, SITTING OUT)
 *   Balance:  span[data-qa="playerBalance"]
 *   Board:    Community cards appear in a board container with data-qa="card-*" elements
 *
 * Card format: {rank}{suit} where rank=A,K,Q,J,T,9..2 and suit=s,h,d,c
 */

(function () {
  "use strict";

  // Avoid double-injection
  if (window.__ignitionReaderActive) return;
  window.__ignitionReaderActive = true;

  console.log("[IgnitionReader] Content script loaded");

  // ---------------------------------------------------------------------------
  // Game state — mirrors the Unibet WS reader format
  // ---------------------------------------------------------------------------
  const state = {
    hero_cards: [],      // ['As', 'Kd']
    board_cards: [],     // ['Th', '4d', '9s']
    pot: 0,
    facing_bet: false,
    call_amount: 0,
    hero_turn: false,
    phase: "WAITING",    // WAITING, PREFLOP, FLOP, TURN, RIVER, SHOWDOWN
    position: "MP",      // BTN, SB, BB, UTG, MP, CO
    hero_seat: -1,
    num_players: 0,
    players: [],         // per-seat info: { seat, stack, bet, action, isHero }
    hand_id: null,
  };

  // Internal tracking
  let lastStateJSON = "";
  let heroSeatIndex = -1;  // 0-based seat index for the hero

  // ---------------------------------------------------------------------------
  // DOM selector helpers
  // ---------------------------------------------------------------------------

  /**
   * Parse a card code from a data-qa attribute value.
   * data-qa="card-As" -> "As", data-qa="card-Td" -> "Td"
   * Returns null if not a valid card attribute.
   */
  function parseCardFromAttr(attrValue) {
    if (!attrValue || !attrValue.startsWith("card-")) return null;
    const code = attrValue.slice(5); // strip "card-"
    // Validate: should be 2 chars, rank + suit
    if (code.length !== 2) return null;
    const rank = code[0];
    const suit = code[1];
    const validRanks = "AKQJT98765432";
    const validSuits = "shdc";
    if (!validRanks.includes(rank.toUpperCase()) || !validSuits.includes(suit.toLowerCase())) return null;
    // Normalize: uppercase rank, lowercase suit (matches our format)
    return rank.toUpperCase() + suit.toLowerCase();
  }

  /**
   * Find all card elements on the page with data-qa="card-*".
   * Returns array of { element, card, parent } objects.
   */
  function findAllCards() {
    const results = [];
    const elements = document.querySelectorAll('[data-qa^="card-"]');
    elements.forEach((el) => {
      const card = parseCardFromAttr(el.getAttribute("data-qa"));
      if (card) {
        results.push({ element: el, card, parent: el.parentElement });
      }
    });
    return results;
  }

  /**
   * Parse a currency string like "$1.50" or "$0.10" into a number.
   * Strips non-numeric characters except dots.
   * IgnitionHUD uses: innerHTML.replace(/\D/, '') but we handle decimals.
   */
  function parseCurrency(text) {
    if (!text) return 0;
    const cleaned = text.replace(/[^0-9.]/g, "");
    const val = parseFloat(cleaned);
    return isNaN(val) ? 0 : val;
  }

  // ---------------------------------------------------------------------------
  // Player container detection
  // ---------------------------------------------------------------------------

  /**
   * Find all player containers on the table.
   * Ignition uses data-qa="playerContainer-{seatID}" for each seat.
   */
  function findPlayerContainers() {
    return document.querySelectorAll('[data-qa^="playerContainer"]');
  }

  /**
   * Extract seat index from a player container's data-qa attribute.
   * "playerContainer-0" -> 0, "playerContainer-5" -> 5
   */
  function getSeatIndex(container) {
    const qa = container.getAttribute("data-qa") || "";
    const match = qa.match(/playerContainer-(\d+)/);
    return match ? parseInt(match[1], 10) : -1;
  }

  /**
   * Read player info from a single player container.
   */
  function readPlayerInfo(container) {
    const seat = getSeatIndex(container);

    // Stack / balance: span[data-qa="playerBalance"]
    const balanceEl = container.querySelector('[data-qa="playerBalance"]');
    const stack = balanceEl ? parseCurrency(balanceEl.textContent) : 0;

    // Player action tag: data-qa="playerTag" (FOLD, CHECK, CALL, BET, RAISE, etc.)
    const tagEl = container.querySelector('[data-qa="playerTag"]');
    const action = tagEl ? tagEl.textContent.trim().toUpperCase() : "";

    // Bet amount: look for a bet display element near the player
    // Ignition renders bet chips near the player container
    // Try data-qa="playerBet" or fall back to a bet-amount class
    let bet = 0;
    const betEl =
      container.querySelector('[data-qa="playerBet"]') ||
      container.querySelector('[data-qa="bet"]') ||
      container.querySelector(".bet-amount") ||
      container.querySelector(".player-bet");
    if (betEl) {
      bet = parseCurrency(betEl.textContent);
    }

    // Detect if this seat is the hero (highlighted, active, or has visible hole cards)
    // Hero's container often has a distinct class or the hole cards are face-up
    const holeCards = [];
    const cardEls = container.querySelectorAll('[data-qa^="card-"]');
    cardEls.forEach((el) => {
      const card = parseCardFromAttr(el.getAttribute("data-qa"));
      if (card) holeCards.push(card);
    });

    // A player with visible hole cards (not on the board) is likely the hero
    const isHero = holeCards.length === 2;

    return { seat, stack, bet, action, isHero, holeCards };
  }

  // ---------------------------------------------------------------------------
  // Board / community cards detection
  // ---------------------------------------------------------------------------

  /**
   * Read community/board cards.
   * Board cards are card elements NOT inside a playerContainer.
   * We find all card-* elements and exclude those inside player containers.
   */
  function readBoardCards() {
    const allCards = findAllCards();
    const board = [];

    allCards.forEach(({ element, card }) => {
      // Walk up to see if this card is inside a playerContainer
      let node = element.parentElement;
      let insidePlayer = false;
      while (node) {
        const qa = node.getAttribute ? node.getAttribute("data-qa") : null;
        if (qa && qa.startsWith("playerContainer")) {
          insidePlayer = true;
          break;
        }
        node = node.parentElement;
      }
      if (!insidePlayer) {
        board.push(card);
      }
    });

    return board;
  }

  // ---------------------------------------------------------------------------
  // Pot detection
  // ---------------------------------------------------------------------------

  /**
   * Read the pot amount from the DOM.
   * Look for common pot display selectors.
   */
  function readPot() {
    // Try data-qa based selectors first
    const potEl =
      document.querySelector('[data-qa="totalPot"]') ||
      document.querySelector('[data-qa="pot"]') ||
      document.querySelector('[data-qa="potAmount"]') ||
      document.querySelector(".pot-amount") ||
      document.querySelector(".total-pot");

    if (potEl) {
      return parseCurrency(potEl.textContent);
    }

    // Fallback: look for any element containing "Pot:" or "Total Pot"
    const allSpans = document.querySelectorAll("span, div");
    for (const el of allSpans) {
      const text = el.textContent.trim();
      if (/^(Total\s+)?Pot[:\s]*\$[\d.]+$/i.test(text)) {
        return parseCurrency(text);
      }
    }

    return 0;
  }

  // ---------------------------------------------------------------------------
  // Hero turn detection
  // ---------------------------------------------------------------------------

  /**
   * Detect if it's the hero's turn to act.
   * Indicators:
   *   1. Action buttons are visible (Fold/Check/Call/Raise/Bet)
   *   2. Hero's player container has an "active" or "turn" indicator
   *   3. A timer is running on the hero's seat
   */
  function isHeroTurn() {
    // Check for visible action buttons
    const foldBtn = document.querySelector(
      'button[data-qa="actionButtonFold"], button[data-qa="fold"], [data-qa="foldButton"]'
    );
    const checkBtn = document.querySelector(
      'button[data-qa="actionButtonCheck"], button[data-qa="check"], [data-qa="checkButton"]'
    );
    const callBtn = document.querySelector(
      'button[data-qa="actionButtonCall"], button[data-qa="call"], [data-qa="callButton"]'
    );
    const raiseBtn = document.querySelector(
      'button[data-qa="actionButtonRaise"], button[data-qa="raise"], [data-qa="raiseButton"]'
    );
    const betBtn = document.querySelector(
      'button[data-qa="actionButtonBet"], button[data-qa="bet"], [data-qa="betButton"]'
    );

    // If any action button is visible and not disabled, it's our turn
    const buttons = [foldBtn, checkBtn, callBtn, raiseBtn, betBtn];
    for (const btn of buttons) {
      if (btn && btn.offsetParent !== null && !btn.disabled) {
        return true;
      }
    }

    // Fallback: check for action panel visibility
    const actionPanel = document.querySelector(
      '[data-qa="actionPanel"], [data-qa="actionButtons"], .action-buttons'
    );
    if (actionPanel && actionPanel.offsetParent !== null) {
      return true;
    }

    return false;
  }

  /**
   * Read the call amount from action buttons.
   * The Call button often shows "Call $X.XX"
   */
  function readCallAmount() {
    const callBtn = document.querySelector(
      'button[data-qa="actionButtonCall"], button[data-qa="call"], [data-qa="callButton"]'
    );
    if (callBtn && callBtn.offsetParent !== null) {
      const text = callBtn.textContent;
      return parseCurrency(text);
    }
    return 0;
  }

  // ---------------------------------------------------------------------------
  // Position detection
  // ---------------------------------------------------------------------------

  /**
   * Determine hero's position based on dealer button location and seat count.
   * Ignition may show a dealer chip/icon on one player container.
   */
  function detectPosition(players) {
    const activePlayers = players.filter(
      (p) => p.stack > 0 && p.action !== "SITTING OUT"
    );
    const numActive = activePlayers.length;
    if (numActive < 2) return "MP";

    // Find dealer seat: look for dealer button indicator
    let dealerSeat = -1;
    const dealerEl = document.querySelector(
      '[data-qa="dealerButton"], [data-qa="dealer"], .dealer-button, .dealer-chip'
    );
    if (dealerEl) {
      // Walk up to find the player container
      let node = dealerEl.parentElement;
      while (node) {
        const qa = node.getAttribute ? node.getAttribute("data-qa") : null;
        if (qa && qa.startsWith("playerContainer")) {
          dealerSeat = getSeatIndex(node);
          break;
        }
        node = node.parentElement;
      }
      // If dealer button is standalone, try to find closest player container
      if (dealerSeat === -1) {
        // Use the dealer element's position relative to player containers
        // This is a heuristic — Ignition may place the dealer chip outside containers
      }
    }

    // Fallback: find SB/BB from bet sizes in preflop
    if (dealerSeat === -1) {
      const blindPlayers = activePlayers
        .filter((p) => p.bet > 0)
        .sort((a, b) => a.bet - b.bet);
      if (blindPlayers.length >= 2) {
        const sbSeat = blindPlayers[0].seat;
        // Dealer is one seat before SB
        const seatNumbers = activePlayers.map((p) => p.seat).sort((a, b) => a - b);
        const sbIdx = seatNumbers.indexOf(sbSeat);
        const dealerIdx = (sbIdx - 1 + seatNumbers.length) % seatNumbers.length;
        dealerSeat = seatNumbers[dealerIdx];
      }
    }

    if (dealerSeat === -1 || heroSeatIndex === -1) return "MP";

    // Calculate position distance from dealer
    const seatNumbers = activePlayers.map((p) => p.seat).sort((a, b) => a - b);
    const dealerPos = seatNumbers.indexOf(dealerSeat);
    const heroPos = seatNumbers.indexOf(heroSeatIndex);
    if (dealerPos === -1 || heroPos === -1) return "MP";

    const dist = (heroPos - dealerPos + numActive) % numActive;

    // Standard position mapping for 6-max
    if (numActive <= 3) {
      // Short-handed: BTN, SB, BB
      const map3 = { 0: "BTN", 1: "SB", 2: "BB" };
      return map3[dist] || "MP";
    } else if (numActive <= 6) {
      const map6 = { 0: "BTN", 1: "SB", 2: "BB", 3: "UTG", 4: "MP", 5: "CO" };
      return map6[dist] || "MP";
    } else {
      // Full ring
      const map9 = {
        0: "BTN", 1: "SB", 2: "BB", 3: "UTG",
        4: "UTG", 5: "MP", 6: "MP", 7: "CO", 8: "CO",
      };
      return map9[dist] || "MP";
    }
  }

  // ---------------------------------------------------------------------------
  // Phase detection
  // ---------------------------------------------------------------------------

  /**
   * Determine the current street/phase from the number of board cards.
   */
  function detectPhase(boardCards) {
    switch (boardCards.length) {
      case 0:
        return "PREFLOP";
      case 3:
        return "FLOP";
      case 4:
        return "TURN";
      case 5:
        return "RIVER";
      default:
        // 1 or 2 cards = animation in progress, keep current phase
        return state.phase;
    }
  }

  // ---------------------------------------------------------------------------
  // Full state scan
  // ---------------------------------------------------------------------------

  /**
   * Perform a complete scan of the DOM to build current game state.
   * Called on every MutationObserver tick and on initial load.
   */
  function scanGameState() {
    // Read all player containers
    const containers = findPlayerContainers();
    const players = [];
    let heroFound = false;

    containers.forEach((container) => {
      const info = readPlayerInfo(container);
      players.push(info);

      if (info.isHero) {
        heroFound = true;
        heroSeatIndex = info.seat;
        state.hero_cards = info.holeCards;
        state.hero_seat = info.seat;
      }
    });

    // If no player containers found, try alternate DOM structure
    // Some Ignition versions use different container selectors
    if (players.length === 0) {
      // Try class-based selectors (from IgnitionHUD CSS classes)
      const altContainers = document.querySelectorAll(
        ".player-container, .seat-container, .player-seat"
      );
      // If still nothing, we're probably not on a poker table
      if (altContainers.length === 0) return;
    }

    // Board cards
    const boardCards = readBoardCards();
    state.board_cards = boardCards;

    // Phase from board card count
    const newPhase = detectPhase(boardCards);

    // Detect new hand: phase went backward (RIVER/SHOWDOWN -> PREFLOP)
    // or hero got new hole cards
    if (
      newPhase === "PREFLOP" &&
      (state.phase === "RIVER" || state.phase === "SHOWDOWN" || state.phase === "WAITING")
    ) {
      // New hand started
      state.hand_id = Date.now().toString();
    }

    state.phase = newPhase;

    // Pot
    state.pot = readPot();

    // If pot is 0, sum all player bets as fallback
    if (state.pot === 0) {
      state.pot = players.reduce((sum, p) => sum + p.bet, 0);
    }

    // Hero turn
    state.hero_turn = isHeroTurn();

    // Call amount
    if (state.hero_turn) {
      state.call_amount = readCallAmount();
      state.facing_bet = state.call_amount > 0;
    } else {
      // Keep last known values until next turn
    }

    // Position
    state.position = detectPosition(players);

    // Player count
    state.num_players = players.filter(
      (p) => p.stack > 0 && p.action !== "SITTING OUT"
    ).length;

    // Store player details
    state.players = players.map((p) => ({
      seat: p.seat,
      stack: p.stack,
      bet: p.bet,
      action: p.action,
      isHero: p.isHero,
    }));

    // Check if state changed and broadcast
    const json = JSON.stringify(getExportState());
    if (json !== lastStateJSON) {
      lastStateJSON = json;
      broadcastState();
    }
  }

  // ---------------------------------------------------------------------------
  // State export (matches Unibet WS reader format)
  // ---------------------------------------------------------------------------

  /**
   * Get state in the same format as unibet_ws.py get_state().
   */
  function getExportState() {
    return {
      hero_cards: state.hero_cards,
      board_cards: state.board_cards,
      facing_bet: state.facing_bet,
      call_amount: state.call_amount,
      pot: state.pot,
      position: state.position,
      hero_turn: state.hero_turn,
      phase: state.phase,
      // Extra fields
      hero_seat: state.hero_seat,
      num_players: state.num_players,
      hand_id: state.hand_id,
      players: state.players,
      source: "ignition-dom",
      timestamp: Date.now(),
    };
  }

  // ---------------------------------------------------------------------------
  // Communication: send state to background script via chrome.runtime
  // ---------------------------------------------------------------------------

  function broadcastState() {
    const exportState = getExportState();

    // Log to console for debugging
    if (exportState.hero_cards.length > 0) {
      console.log(
        `[IgnitionReader] [${exportState.phase}] ` +
          `Hero: ${exportState.hero_cards.join(" ")} | ` +
          `Board: ${exportState.board_cards.join(" ") || "-"} | ` +
          `Pot: $${exportState.pot} | ` +
          `Turn: ${exportState.hero_turn} | ` +
          `Facing: ${exportState.facing_bet} (call $${exportState.call_amount}) | ` +
          `Pos: ${exportState.position}`
      );
    }

    // Send to background service worker
    try {
      chrome.runtime.sendMessage(
        { type: "GAME_STATE", state: exportState },
        (response) => {
          // Ignore errors when popup is closed
          if (chrome.runtime.lastError) return;
        }
      );
    } catch (e) {
      // Extension context may be invalidated on reload
    }
  }

  // ---------------------------------------------------------------------------
  // MutationObserver — watches the entire poker game DOM for changes
  // ---------------------------------------------------------------------------

  const observer = new MutationObserver((mutations) => {
    // Debounce: many mutations fire at once during animations
    // Use requestAnimationFrame to batch them
    if (!scanGameState._pending) {
      scanGameState._pending = true;
      requestAnimationFrame(() => {
        scanGameState._pending = false;
        scanGameState();
      });
    }
  });
  scanGameState._pending = false;

  /**
   * Start observing. We watch the entire document body for:
   *   - childList changes (new cards dealt, players joining/leaving)
   *   - attribute changes (data-qa updates, class changes for active player)
   *   - characterData changes (balance/pot text updates)
   *   - subtree: true to catch deeply nested changes
   */
  function startObserving() {
    const target = document.body;
    if (!target) {
      // Body not ready, retry
      setTimeout(startObserving, 500);
      return;
    }

    observer.observe(target, {
      childList: true,
      attributes: true,
      characterData: true,
      subtree: true,
      attributeFilter: ["data-qa", "class", "style"],
    });

    console.log("[IgnitionReader] MutationObserver active on document.body");

    // Do an initial scan
    scanGameState();
  }

  // Also poll at 200ms as a safety net (MutationObserver can miss some changes)
  setInterval(scanGameState, 200);

  // Listen for state requests from popup or background
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "GET_STATE") {
      sendResponse(getExportState());
    }
    return true; // keep channel open for async response
  });

  // Start
  startObserving();

  console.log("[IgnitionReader] Ignition Casino DOM reader initialized");
  console.log("[IgnitionReader] Watching for: data-qa='card-*', playerContainer, playerTag, playerBalance");
})();
