// ═══════════════════════════════════════════════════════════════════════════
//  Canvas-based table renderer — matches PS Cocos2d rendering approach
//  Draws table, cards, buttons, seats, text on a single <canvas> element
// ═══════════════════════════════════════════════════════════════════════════

const CanvasRenderer = (function() {
  let canvas, ctx;
  let tableImg = null;
  let cardImages = {};
  let cardBackImg = null;
  let chipImg = null;
  let dealerImg = null;
  let assetsLoaded = false;

  // PS-matched dimensions (portrait)
  const TABLE_W = 440;
  const TABLE_H = 800;

  // PS-matched positions (as fractions of table dimensions)
  const LAYOUT = {
    // Board cards: y=35.3% of felt, each 17.5%w x 13.1%h (from PS YOLO)
    board: { y: 0.34, cardW: 0.130, cardH: 0.105, gap: 0.006 },
    // Hero cards: y=90% of felt, 20%w x 15%h (from PS YOLO)
    hero: { x: 0.20, y: 0.78, cardW: 0.155, cardH: 0.130, overlap: 0.065 },
    // Pot text: y=30.4% (from PS YOLO)
    pot: { y: 0.28, fontSize: 13 },
    // Action buttons: y=53% (from PS YOLO)
    buttons: { y: 0.50, h: 0.068, gap: 0.012 },
    // Seat positions (6-max): [x, y] as fractions
    seats: [
      [0.50, 0.88],  // 0: hero (bottom center)
      [0.08, 0.62],  // 1: lower-left
      [0.08, 0.22],  // 2: upper-left
      [0.50, 0.04],  // 3: top center
      [0.92, 0.22],  // 4: upper-right
      [0.92, 0.62],  // 5: lower-right
    ],
    // Felt text
    feltText: { y: 0.56, fontSize: 10, opacity: 0.10 },
  };

  function init(canvasEl) {
    canvas = canvasEl;
    ctx = canvas.getContext('2d');
    canvas.width = TABLE_W;
    canvas.height = TABLE_H;
    loadAssets();
  }

  function loadAssets() {
    let loaded = 0;
    const total = 55; // 52 cards + table + cardback + chip

    function onLoad() {
      loaded++;
      if (loaded >= total) {
        assetsLoaded = true;
        console.log('[Canvas] All assets loaded');
      }
    }

    // Table background
    tableImg = new Image();
    tableImg.onload = onLoad;
    tableImg.src = 'ps_assets/table_portrait.png';

    // Card back
    cardBackImg = new Image();
    cardBackImg.onload = onLoad;
    cardBackImg.src = 'ps_assets/card_back.png';

    // Chip
    chipImg = new Image();
    chipImg.onload = onLoad;
    chipImg.onerror = onLoad;
    chipImg.src = 'ps_assets/chip.png';

    // All 52 cards
    const ranks = '23456789TJQKA';
    const suits = 'cdhs';
    for (const r of ranks) {
      for (const s of suits) {
        const key = r + s;
        const img = new Image();
        img.onload = onLoad;
        img.onerror = onLoad;
        img.src = `ps_assets/cards/${key}.png`;
        cardImages[key] = img;
      }
    }
  }

  function drawRoundedRect(x, y, w, h, r, fill, stroke) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
  }

  function drawTable() {
    // Dark background
    ctx.fillStyle = '#2b2b2b';
    ctx.fillRect(0, 0, TABLE_W, TABLE_H);

    // Table felt image (oval clipped)
    if (tableImg && tableImg.complete) {
      ctx.save();
      // Create oval clip path
      ctx.beginPath();
      ctx.ellipse(TABLE_W / 2, TABLE_H * 0.40, TABLE_W * 0.50, TABLE_H * 0.38, 0, 0, Math.PI * 2);
      ctx.clip();
      ctx.drawImage(tableImg, 0, 0, TABLE_W, TABLE_H * 0.85);
      ctx.restore();
    }

    // POKERSTARS text on felt
    ctx.save();
    ctx.globalAlpha = LAYOUT.feltText.opacity;
    ctx.font = `bold ${LAYOUT.feltText.fontSize}px 'Roboto Condensed', sans-serif`;
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.fillText('♠  POKERSTARS', TABLE_W / 2, TABLE_H * LAYOUT.feltText.y);
    ctx.restore();
  }

  function drawCard(card, x, y, w, h) {
    const img = cardImages[card];
    if (img && img.complete && img.naturalWidth > 0) {
      // Card shadow
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,0.4)';
      ctx.shadowBlur = 4;
      ctx.shadowOffsetX = 1;
      ctx.shadowOffsetY = 2;
      // Draw card with rounded corners
      drawRoundedRect(x, y, w, h, 3, null, null);
      ctx.clip();
      ctx.drawImage(img, x, y, w, h);
      ctx.restore();
    }
  }

  function drawCardBack(x, y, w, h) {
    if (cardBackImg && cardBackImg.complete) {
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,0.5)';
      ctx.shadowBlur = 3;
      ctx.shadowOffsetX = 1;
      ctx.shadowOffsetY = 1;
      ctx.drawImage(cardBackImg, x, y, w, h);
      ctx.restore();
    }
  }

  function drawBoardCards(board) {
    if (!board || board.length === 0) return;
    const cw = TABLE_W * LAYOUT.board.cardW;
    const ch = TABLE_H * LAYOUT.board.cardH;
    const gap = TABLE_W * LAYOUT.board.gap;
    const totalW = board.length * cw + (board.length - 1) * gap;
    const startX = (TABLE_W - totalW) / 2;
    const y = TABLE_H * LAYOUT.board.y;

    for (let i = 0; i < board.length; i++) {
      const x = startX + i * (cw + gap);
      drawCard(board[i], x, y, cw, ch);
    }
  }

  function drawHeroCards(cards) {
    if (!cards || cards.length === 0) return;
    const cw = TABLE_W * LAYOUT.hero.cardW;
    const ch = TABLE_H * LAYOUT.hero.cardH;
    const x0 = TABLE_W * LAYOUT.hero.x;
    const y0 = TABLE_H * LAYOUT.hero.y;
    const overlap = TABLE_W * LAYOUT.hero.overlap;

    for (let i = 0; i < cards.length; i++) {
      const x = x0 + i * (cw - overlap);
      const y = y0 + i * 4;
      drawCard(cards[i], x, y, cw, ch);
    }
  }

  function drawPot(pot) {
    if (!pot) return;
    const text = `Pot: $${(pot / 100).toFixed(2)}`;
    ctx.font = "bold 13px 'Roboto Condensed', sans-serif";
    ctx.textAlign = 'center';

    // Background pill
    const metrics = ctx.measureText(text);
    const tw = metrics.width + 20;
    const th = 22;
    const x = TABLE_W / 2 - tw / 2;
    const y = TABLE_H * LAYOUT.pot.y - th / 2;
    drawRoundedRect(x, y, tw, th, 10, 'rgba(0,0,0,0.45)', null);

    // Text
    ctx.fillStyle = '#ffffff';
    ctx.fillText(text, TABLE_W / 2, TABLE_H * LAYOUT.pot.y + 5);
  }

  function drawActionButtons(legalActions, callAmount, minRaise) {
    if (!legalActions || legalActions.length === 0) return;
    const actions = legalActions;
    const y = TABLE_H * LAYOUT.buttons.y;
    const h = TABLE_H * LAYOUT.buttons.h;
    const gap = TABLE_W * LAYOUT.buttons.gap;

    // Filter to visible buttons
    const buttons = [];
    if (actions.includes('FOLD')) buttons.push({ text: 'Fold', color: '#b71b1b' });
    if (actions.includes('CHECK')) buttons.push({ text: 'Check', color: '#2a5938' });
    if (actions.includes('CALL')) buttons.push({ text: `Call\n$${(callAmount / 100).toFixed(2)}`, color: '#2a5938' });
    if (actions.includes('BET')) buttons.push({ text: 'Bet', color: '#b71b1b' });
    if (actions.includes('RAISE')) buttons.push({ text: `Raise to\n$${(minRaise / 100).toFixed(2)}`, color: '#b71b1b' });

    if (buttons.length === 0) return;

    const totalGap = (buttons.length - 1) * gap;
    const btnW = (TABLE_W * 0.88 - totalGap) / buttons.length;
    const startX = TABLE_W * 0.06;

    for (let i = 0; i < buttons.length; i++) {
      const x = startX + i * (btnW + gap);
      drawRoundedRect(x, y, btnW, h, 10, buttons[i].color, null);

      // Button text
      ctx.font = "bold 15px 'Roboto Condensed', sans-serif";
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';

      const lines = buttons[i].text.split('\n');
      const lineH = 17;
      const textY = y + h / 2 - (lines.length - 1) * lineH / 2 + 5;
      for (let j = 0; j < lines.length; j++) {
        ctx.fillText(lines[j], x + btnW / 2, textY + j * lineH);
      }
    }
  }

  function drawSeat(seatData, seatIndex, isButton) {
    if (!seatData || seatData.status === 'EMPTY') return;
    const [sx, sy] = LAYOUT.seats[seatIndex];
    const x = TABLE_W * sx - 60;
    const y = TABLE_H * sy - 20;
    const w = 120;
    const h = 40;

    // Seat panel background
    const isActive = seatData.active;
    ctx.save();
    ctx.globalAlpha = isActive ? 1.0 : 0.9;
    drawRoundedRect(x, y, w, h, 3, 'rgba(30,30,30,0.85)', isActive ? '#4ddb7a' : '#444');

    // Name
    ctx.font = "bold 9px 'Roboto Condensed', sans-serif";
    ctx.fillStyle = '#e0e0e0';
    ctx.textAlign = 'left';
    const name = seatData.player ? seatData.player.name : `Seat ${seatIndex}`;
    ctx.fillText(name, x + 40, y + 15);

    // Stack
    ctx.fillStyle = '#4ece78';
    ctx.font = "bold 10px 'Roboto Condensed', sans-serif";
    const stack = seatData.stack != null ? `$${(seatData.stack / 100).toFixed(2)}` : '--';
    ctx.fillText(stack, x + 40, y + 30);

    ctx.restore();

    // Card backs for opponents in hand
    if (seatIndex !== 0 && seatData.inHand && !seatData.folded) {
      const cbw = 28, cbh = 39;
      let cbx, cby;
      if (seatIndex <= 2) {
        // Left/top seats: cards to the right
        cbx = x + w + 4;
        cby = y - 5;
      } else {
        // Right seats: cards to the left
        cbx = x - cbw * 2 + 8;
        cby = y - 5;
      }
      drawCardBack(cbx, cby, cbw, cbh);
      drawCardBack(cbx + cbw * 0.6, cby + 2, cbw, cbh);
    }
  }

  function drawFeltInfo(tableName, sb, bb) {
    const text = `${tableName} - No Limit Hold'em\n$${(sb/100).toFixed(2)}/$${(bb/100).toFixed(2)}`;
    ctx.save();
    ctx.globalAlpha = 0.20;
    ctx.font = "8px 'Roboto Condensed', sans-serif";
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
      ctx.fillText(lines[i], TABLE_W / 2, TABLE_H * 0.58 + i * 12);
    }
    ctx.restore();
  }

  function render(gameState) {
    if (!assetsLoaded || !ctx) return;

    const hand = gameState.hand;
    const handActive = hand && hand.phase !== 'COMPLETE';

    // Clear and draw table
    drawTable();

    // Felt info text
    drawFeltInfo(
      gameState.tableName || 'Poker Lab',
      gameState.sb || 5,
      gameState.bb || 10
    );

    // Board cards
    if (hand && hand.board) {
      drawBoardCards(hand.board);
    }

    // Pot
    if (hand && hand.pot > 0) {
      drawPot(hand.pot);
    }

    // Seats
    for (let i = 0; i < gameState.maxSeats; i++) {
      const s = gameState.seats[i];
      if (s) {
        s.active = handActive && hand && hand.actionSeat === i;
        drawSeat(s, i, gameState.button === i);
      }
    }

    // Hero cards
    if (gameState.seats[0] && gameState.seats[0].holeCards) {
      drawHeroCards(gameState.seats[0].holeCards);
    }

    // Action buttons (only when hero's turn)
    if (handActive && hand.actionSeat === 0 && hand.legalActions) {
      drawActionButtons(
        hand.legalActions.actions,
        hand.legalActions.callAmount || 0,
        hand.legalActions.minRaise || 0
      );
    }
  }

  return { init, render };
})();
