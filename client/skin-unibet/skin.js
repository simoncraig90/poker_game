/**
 * Unibet Poker skin for lab client.
 *
 * Applies Unibet-style theming over the base PS layout.
 * Load via ?skin=unibet in URL.
 *
 * Visual reference: Relax Gaming canvas poker client
 * - Bright green felt with darker edge gradient
 * - Dark charcoal background
 * - Dark semi-transparent seat panels
 * - Rounded action buttons (dark bg, white text)
 * - "UNIBET" branding on felt center
 * - Orange/gold chip icons for bets
 * - White dealer button with "D"
 */

const UnibetSkin = (function() {

  const COLORS = {
    // Felt — measured from live Unibet table
    feltCenter:   '#28471d',   // rgb(40,71,29) — forest green center
    feltBright:   '#2a491f',   // rgb(42,73,31) — brightest spot
    feltEdge:     '#1b3a10',   // rgb(27,58,16) — darker edge
    feltBorder:   '#092800',   // rgb(9,40,0) — darkest ring

    // Background — near black
    bg:           '#090909',   // rgb(9,9,9) — measured outside felt
    bgOuter:      '#050505',   // near black

    // Seat panels — very dark
    panelBg:      'rgba(8, 8, 8, 0.9)',
    panelActive:  'rgba(20, 35, 15, 0.9)',
    panelBorder:  'rgba(50, 50, 50, 0.3)',

    // Text
    nameColor:    '#e0e0e0',
    stackColor:   '#ffffff',
    potColor:     '#ffffff',

    // Buttons — dark gray fold/raise, green call/check
    foldBg:       '#1a1a1a',
    foldText:     '#ffffff',
    callBg:       '#28471d',
    callText:     '#ffffff',
    raiseBg:      '#1a1a1a',
    raiseText:    '#ffffff',
    checkBg:      '#28471d',

    // Accents
    accent:       '#f5a623',   // orange/gold (chips, highlights)
    brandGreen:   '#147b3d',   // Unibet brand green (header)
    headerBg:     '#147b3d',   // measured ~rgb(20,123,69)
    headerYellow: '#ffe71f',   // measured rgb(255,231,31)
  };

  function apply() {
    const style = document.createElement('style');
    style.id = 'unibet-skin';
    style.textContent = `
      /* ── UNIBET SKIN ── */

      /* Background */
      body { background: ${COLORS.bg} !important; }
      #header {
        background: ${COLORS.headerBg} !important;
        border-bottom: 2px solid ${COLORS.headerYellow} !important;
      }
      #header .table-name { color: #fff !important; }
      #header .responsible { color: ${COLORS.headerYellow} !important; }
      #table-area {
        background: radial-gradient(ellipse at 50% 45%, #111 10%, #090909 50%, #050505 100%) !important;
      }

      /* Felt — override PS table image with Unibet green gradient + dark rail */
      #table-felt::before {
        background:
          radial-gradient(ellipse at 50% 48%,
            #2e4e22 0%,
            #28471d 20%,
            #23421a 40%,
            #1e3c15 55%,
            #143010 68%,
            #0a2206 78%,
            #0d150a 86%,
            #121210 92%,
            #0c0c0a 100%
          ) !important;
        border: none !important;
        box-shadow: inset 0 0 30px rgba(0,0,0,0.25), 0 4px 30px rgba(0,0,0,0.8) !important;
      }

      /* Branding on felt */
      #table-felt::after {
        content: 'UNIBET' !important;
        font-family: 'Arial Black', 'Roboto Condensed', sans-serif !important;
        font-size: 18px !important;
        font-weight: 900 !important;
        letter-spacing: 8px !important;
        color: rgba(255, 255, 255, 0.08) !important;
      }

      /* Seat panels */
      .seat {
        background: ${COLORS.panelBg} !important;
        border: 1px solid ${COLORS.panelBorder} !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.5) !important;
      }
      .seat.active {
        background: ${COLORS.panelActive} !important;
        border-color: ${COLORS.accent} !important;
        box-shadow: 0 0 12px rgba(245, 166, 35, 0.3) !important;
      }
      .seat.folded { opacity: 0.4 !important; }
      .seat.empty {
        background: rgba(30, 30, 30, 0.4) !important;
        border: 1px dashed rgba(100, 100, 100, 0.4) !important;
      }

      /* Avatar area — solid dark circle instead of PS image */
      .seat-avatar {
        background: linear-gradient(135deg, #444, #222) !important;
        border-radius: 50% !important;
        border: 2px solid rgba(80, 80, 80, 0.5) !important;
      }

      .seat-name { color: ${COLORS.nameColor} !important; font-size: 10px !important; }
      .seat-stack { color: ${COLORS.stackColor} !important; font-weight: 700 !important; }

      /* Action buttons — Unibet uses darker, more muted buttons */
      #action-bar button {
        border-radius: 8px !important;
        font-family: 'Roboto Condensed', Arial, sans-serif !important;
        font-size: 15px !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.5px !important;
        min-height: 50px !important;
      }
      #fold-btn {
        background: linear-gradient(180deg, #252525, #1a1a1a) !important;
        color: ${COLORS.foldText} !important;
        border: 1px solid rgba(60,60,60,0.4) !important;
      }
      #fold-btn:hover:not(:disabled) { background: #333 !important; }
      #check-btn {
        background: linear-gradient(180deg, #2d6b35, #224f28) !important;
        color: #fff !important;
        border: 1px solid rgba(60,120,60,0.3) !important;
      }
      #call-btn {
        background: linear-gradient(180deg, #2d6b35, #224f28) !important;
        color: ${COLORS.callText} !important;
        border: 1px solid rgba(60,120,60,0.3) !important;
      }
      #call-btn:hover:not(:disabled) { background: #35803d !important; }
      #bet-btn, #raise-btn {
        background: linear-gradient(180deg, #252525, #1a1a1a) !important;
        color: ${COLORS.raiseText} !important;
        border: 1px solid rgba(60,60,60,0.4) !important;
      }
      #raise-btn:hover:not(:disabled), #bet-btn:hover:not(:disabled) { background: #333 !important; }

      /* Bet sizing bar */
      #sizing-bar {
        background: rgba(30, 30, 30, 0.95) !important;
        border: 1px solid rgba(80, 80, 80, 0.3) !important;
      }
      #sizing-presets button {
        background: linear-gradient(180deg, #444, #333) !important;
        color: #fff !important;
      }

      /* Cards — clean white with subtle shadow */
      .card {
        border-radius: 5px !important;
        box-shadow: 1px 2px 6px rgba(0,0,0,0.4) !important;
        border: 1px solid #ddd !important;
      }
      .card.facedown {
        background: linear-gradient(135deg, #1a1a1a 0%, #2a2a2a 50%, #1a1a1a 100%) !important;
        background-image: none !important;
        border: 1px solid #333 !important;
        position: relative;
      }
      .card.facedown::after {
        content: 'U';
        position: absolute; top: 50%; left: 50%;
        transform: translate(-50%, -50%);
        font-family: Arial Black, sans-serif;
        font-size: 16px; font-weight: 900;
        color: rgba(255,255,255,0.15);
      }

      /* Board cards */
      #board .card {
        border-radius: 4px !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.3) !important;
      }

      /* Pot display */
      #pot {
        background: rgba(0, 0, 0, 0.5) !important;
        border-radius: 15px !important;
        font-size: 13px !important;
        padding: 4px 16px !important;
        color: ${COLORS.potColor} !important;
      }

      /* Bet chips — orange/gold style */
      .bet-chip {
        background: rgba(0, 0, 0, 0.4) !important;
        color: #fff !important;
        font-size: 11px !important;
      }

      /* Dealer button — white circle */
      .dealer-btn {
        background: radial-gradient(circle, #fff 60%, #ccc 100%) !important;
        color: #333 !important;
        font-size: 10px !important;
        font-weight: 900 !important;
        border: 2px solid #999 !important;
      }

      /* Timer bar — Unibet green */
      .seat-timer-fill {
        background: linear-gradient(90deg, ${COLORS.brandGreen}, ${COLORS.accent}, #e74c3c) !important;
      }

      /* Action bubble */
      .action-bubble {
        background: rgba(0, 0, 0, 0.8) !important;
        border-radius: 6px !important;
        font-size: 12px !important;
      }
      .action-bubble.fold { color: #aaa !important; }
      .action-bubble.call { color: ${COLORS.callBg} !important; }
      .action-bubble.raise, .action-bubble.bet { color: ${COLORS.accent} !important; }

      /* Status bar */
      #status.connected { background: ${COLORS.brandGreen} !important; color: #fff !important; }

      /* Result banner */
      #result-banner { color: ${COLORS.accent} !important; }

      /* Card slot on board */
      .card-slot {
        background: rgba(0, 0, 0, 0.12) !important;
        border: 1px solid rgba(0, 0, 0, 0.08) !important;
      }

      /* Hero cards */
      .hero-cards .card.hero-card {
        box-shadow: 2px 3px 12px rgba(0, 0, 0, 0.6) !important;
      }

      /* Auto-actions */
      #auto-actions label {
        background: rgba(30, 30, 30, 0.9) !important;
        border-color: #444 !important;
      }
      #auto-actions input[type="checkbox"] { accent-color: ${COLORS.brandGreen} !important; }

      /* Right panel */
      #right-panel { background: #111 !important; border-left-color: #333 !important; }
      .panel-tab.active { color: ${COLORS.brandGreen} !important; border-bottom-color: ${COLORS.brandGreen} !important; }
    `;
    document.head.appendChild(style);
    document.title = 'Unibet Poker';

    // Update header text
    const tableName = document.getElementById('header-table-name');
    if (tableName) tableName.textContent = 'Texas Hold\'em';

    // Override pot display format to match Unibet ("Total pot: €X.XX")
    const origPotEl = document.getElementById('pot');
    if (origPotEl) {
      const observer = new MutationObserver(() => {
        const text = origPotEl.textContent;
        if (text && !text.startsWith('Total pot:') && text.includes('$')) {
          origPotEl.textContent = text.replace('Pot:', 'Total pot:').replace('$', '€');
        }
      });
      observer.observe(origPotEl, { childList: true, characterData: true, subtree: true });
    }

    console.log('[Skin] Unibet skin applied');
  }

  function remove() {
    const el = document.getElementById('unibet-skin');
    if (el) el.remove();
  }

  return { apply, remove, COLORS };
})();

// Auto-apply if ?skin=unibet in URL
(function() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('skin') === 'unibet') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', UnibetSkin.apply);
    } else {
      UnibetSkin.apply();
    }
  }
})();
