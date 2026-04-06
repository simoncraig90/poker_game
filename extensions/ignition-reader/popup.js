/**
 * Ignition Reader — Popup UI Script
 *
 * Polls game state from the background service worker and renders it.
 * Updates every 250ms while the popup is open.
 */

const SUIT_SYMBOLS = { s: "\u2660", h: "\u2665", d: "\u2666", c: "\u2663" };
const SUIT_COLORS = { s: "spade", h: "heart", d: "diamond", c: "club" };

let advisorConnected = false;
let pollTimer = null;

/**
 * Create a card element for display.
 * card = "As" -> Ace of spades
 */
function createCardElement(card) {
  const el = document.createElement("div");
  el.className = "card";

  if (!card || card === "??") {
    el.className = "card empty";
    el.textContent = "?";
    return el;
  }

  const rank = card[0];
  const suit = card[1];
  const suitSymbol = SUIT_SYMBOLS[suit] || suit;
  const suitColor = SUIT_COLORS[suit] || "";

  el.className = `card ${suitColor}`;
  el.textContent = `${rank}${suitSymbol}`;
  return el;
}

/**
 * Render cards into a container element.
 */
function renderCards(containerId, cards, emptyCount) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  if (cards.length === 0) {
    for (let i = 0; i < (emptyCount || 2); i++) {
      container.appendChild(createCardElement(null));
    }
    return;
  }

  cards.forEach((card) => {
    container.appendChild(createCardElement(card));
  });
}

/**
 * Update the popup UI with the latest game state.
 */
function updateUI(state) {
  const noTable = document.getElementById("noTable");
  const gameInfo = document.getElementById("gameInfo");
  const statusDot = document.getElementById("statusDot");
  const statusText = document.getElementById("statusText");

  if (!state || state.phase === "WAITING") {
    noTable.style.display = "block";
    gameInfo.style.display = "none";
    statusDot.className = "status-dot dot-red";
    statusText.textContent = "No table";
    return;
  }

  noTable.style.display = "none";
  gameInfo.style.display = "block";

  // Status indicator
  if (state.hero_turn) {
    statusDot.className = "status-dot dot-green";
    statusText.textContent = "Your turn";
  } else if (state.hero_cards.length > 0) {
    statusDot.className = "status-dot dot-yellow";
    statusText.textContent = "In hand";
  } else {
    statusDot.className = "status-dot dot-yellow";
    statusText.textContent = "Active";
  }

  // Phase and position
  document.getElementById("phase").textContent = state.phase;
  document.getElementById("position").textContent = state.position;
  document.getElementById("numPlayers").textContent = state.num_players || "-";

  // Cards
  renderCards("heroCards", state.hero_cards, 2);
  renderCards("boardCards", state.board_cards, 5);

  // Betting info
  document.getElementById("pot").textContent = `$${state.pot.toFixed(2)}`;
  document.getElementById("facingBet").textContent = state.facing_bet ? "Yes" : "No";
  document.getElementById("callAmount").textContent = `$${state.call_amount.toFixed(2)}`;

  // Turn indicator
  const turnEl = document.getElementById("turnIndicator");
  turnEl.style.display = state.hero_turn ? "block" : "none";
}

/**
 * Poll state from the background script or directly from the content script.
 */
function pollState() {
  // Try background first
  chrome.runtime.sendMessage({ type: "GET_STATE" }, (bgState) => {
    if (chrome.runtime.lastError || !bgState) {
      // Fallback: query the active tab's content script directly
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
          chrome.tabs.sendMessage(tabs[0].id, { type: "GET_STATE" }, (tabState) => {
            if (chrome.runtime.lastError) {
              updateUI(null);
              return;
            }
            updateUI(tabState);
          });
        }
      });
      return;
    }
    updateUI(bgState);
  });

  // Update hand count
  chrome.runtime.sendMessage({ type: "GET_HISTORY" }, (resp) => {
    if (chrome.runtime.lastError || !resp) return;
    document.getElementById("handCount").textContent = `Hands: ${resp.hands}`;
  });
}

/**
 * Toggle advisor connection.
 */
function toggleAdvisor() {
  const btn = document.getElementById("advisorBtn");

  if (advisorConnected) {
    chrome.runtime.sendMessage({ type: "DISCONNECT_ADVISOR" });
    advisorConnected = false;
    btn.textContent = "Connect to Advisor";
    btn.className = "advisor-btn";
  } else {
    chrome.runtime.sendMessage({ type: "CONNECT_ADVISOR" });
    advisorConnected = true;
    btn.textContent = "Disconnect Advisor";
    btn.className = "advisor-btn connected";
  }
}

/**
 * Check initial advisor status.
 */
function checkAdvisorStatus() {
  chrome.runtime.sendMessage({ type: "GET_ADVISOR_STATUS" }, (resp) => {
    if (chrome.runtime.lastError || !resp) return;
    const btn = document.getElementById("advisorBtn");
    if (resp.connected) {
      advisorConnected = true;
      btn.textContent = "Disconnect Advisor";
      btn.className = "advisor-btn connected";
    }
  });
}

// Initialize
document.getElementById("advisorBtn").addEventListener("click", toggleAdvisor);
checkAdvisorStatus();

// Start polling
pollState();
pollTimer = setInterval(pollState, 250);

// Clean up on popup close
window.addEventListener("unload", () => {
  if (pollTimer) clearInterval(pollTimer);
});
