/**
 * Ignition Reader — Background Service Worker (Manifest V3)
 *
 * Receives game state from the content script and:
 *   1. Stores the latest state for the popup to read
 *   2. Can forward state to an external advisor (Python WebSocket server)
 *   3. Tracks hand history within the session
 */

// Current game state from content script
let currentState = null;

// Hand history for the current session
const handHistory = [];
let lastHandId = null;

// WebSocket connection to external advisor (optional)
let advisorWs = null;
const ADVISOR_URL = "ws://localhost:9200"; // Python advisor WebSocket endpoint

// ---------------------------------------------------------------------------
// Message handling from content script and popup
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "GAME_STATE") {
    // Received updated game state from content script
    currentState = message.state;

    // Track hand transitions
    if (currentState.hand_id && currentState.hand_id !== lastHandId) {
      lastHandId = currentState.hand_id;
      handHistory.push({
        hand_id: currentState.hand_id,
        started: Date.now(),
        hero_cards: currentState.hero_cards,
        events: [],
      });
      // Keep history bounded (last 100 hands)
      if (handHistory.length > 100) handHistory.shift();
    }

    // Append events to current hand
    if (handHistory.length > 0) {
      const currentHand = handHistory[handHistory.length - 1];
      currentHand.events.push({
        time: Date.now(),
        phase: currentState.phase,
        board: currentState.board_cards,
        pot: currentState.pot,
        hero_turn: currentState.hero_turn,
        facing_bet: currentState.facing_bet,
        call_amount: currentState.call_amount,
      });
    }

    // Forward to external advisor if connected
    forwardToAdvisor(currentState);

    sendResponse({ ok: true });
  }

  if (message.type === "GET_STATE") {
    sendResponse(currentState);
  }

  if (message.type === "GET_HISTORY") {
    sendResponse({ hands: handHistory.length, history: handHistory.slice(-10) });
  }

  if (message.type === "CONNECT_ADVISOR") {
    connectAdvisor(message.url || ADVISOR_URL);
    sendResponse({ ok: true });
  }

  if (message.type === "DISCONNECT_ADVISOR") {
    disconnectAdvisor();
    sendResponse({ ok: true });
  }

  if (message.type === "GET_ADVISOR_STATUS") {
    sendResponse({
      connected: advisorWs !== null && advisorWs.readyState === WebSocket.OPEN,
      url: ADVISOR_URL,
    });
  }

  return true; // keep channel open
});

// ---------------------------------------------------------------------------
// External advisor WebSocket connection
// ---------------------------------------------------------------------------

function connectAdvisor(url) {
  disconnectAdvisor();

  try {
    advisorWs = new WebSocket(url);

    advisorWs.onopen = () => {
      console.log("[IgnitionReader:BG] Connected to advisor at", url);
    };

    advisorWs.onmessage = (event) => {
      // Advisor might send recommendations back
      try {
        const advice = JSON.parse(event.data);
        console.log("[IgnitionReader:BG] Advisor says:", advice);
        // Could forward to content script for overlay display
      } catch (e) {
        // Ignore non-JSON messages
      }
    };

    advisorWs.onerror = (err) => {
      console.log("[IgnitionReader:BG] Advisor WS error:", err);
    };

    advisorWs.onclose = () => {
      console.log("[IgnitionReader:BG] Advisor disconnected");
      advisorWs = null;
    };
  } catch (e) {
    console.log("[IgnitionReader:BG] Failed to connect advisor:", e);
    advisorWs = null;
  }
}

function disconnectAdvisor() {
  if (advisorWs) {
    try {
      advisorWs.close();
    } catch (e) {
      // Ignore
    }
    advisorWs = null;
  }
}

function forwardToAdvisor(state) {
  if (!advisorWs || advisorWs.readyState !== WebSocket.OPEN) return;
  try {
    advisorWs.send(JSON.stringify(state));
  } catch (e) {
    console.log("[IgnitionReader:BG] Failed to send to advisor:", e);
  }
}

console.log("[IgnitionReader:BG] Background service worker started");
