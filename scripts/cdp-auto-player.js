/**
 * CDP Auto-Player for Unibet (Emscripten/kenobiCanvas).
 *
 * Key insight: the canvas must be focused (tabIndex=0, focus()) before
 * CDP Input.dispatchMouseEvent will register. Without focus, Emscripten
 * ignores all input events.
 *
 * Button positions as % of iframe CSS dimensions:
 *   FOLD:       40.3% x, 94.2% y
 *   CHECK/CALL: 60.5% x, 93.2% y
 *   RAISE/BET:  50.6% x, 93.6% y
 *
 * Usage: node scripts/cdp-auto-player.js
 * Commands on stdin: FOLD, CHECK, CALL, RAISE, BET
 */
const CDP = require('chrome-remote-interface');
const readline = require('readline');

const PORT = parseInt(process.argv[2]) || 9222;

// Button layout from screenshot: FOLD (red left) | CALL (yellow center) | RAISE (green right)
const BUTTON_PCT = {
  FOLD:  { x: 0.403, y: 0.935 },
  CHECK: { x: 0.505, y: 0.935 },
  CALL:  { x: 0.505, y: 0.935 },
  RAISE: { x: 0.609, y: 0.932 },
  BET:   { x: 0.609, y: 0.932 },
};

// Bet input field: above the RAISE button
// From screenshot: ~52% x, 87% y (between slider presets and action buttons)
const BET_INPUT_PCT = { x: 0.524, y: 0.871 };

// Slider preset buttons row (above input)
// 25%, 50%, 80%, All-in — from x=0.30 to x=0.55
const SLIDER_PRESETS = {
  '25':    { x: 0.32, y: 0.836 },
  '50':    { x: 0.39, y: 0.836 },
  '80':    { x: 0.46, y: 0.836 },
  'ALLIN': { x: 0.53, y: 0.836 },
};

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function randomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }

async function main() {
  const targets = await CDP.List({ port: PORT });
  const relaxFrame = targets.find(t => t.type === 'iframe' && t.url.includes('relaxg.com'));
  if (!relaxFrame) {
    console.error('NO_IFRAME');
    process.exit(1);
  }

  const client = await CDP({ target: relaxFrame.id, port: PORT });
  const { Runtime, Input } = client;
  await Runtime.enable();

  // Also connect to main page for bringToFront
  const mainPage = targets.find(t => t.type === 'page' && t.url.includes('unibet'));
  let pageClient = null;
  if (mainPage) {
    pageClient = await CDP({ target: mainPage.id, port: PORT });
    await pageClient.Page.enable();
  }

  // Get iframe CSS dimensions
  const dimResult = await Runtime.evaluate({
    returnByValue: true,
    expression: `(function() {
      return JSON.stringify({ w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio || 1 });
    })()`
  });
  const dims = JSON.parse(dimResult.result.value);
  console.error(`DIMS: ${dims.w}x${dims.h} dpr=${dims.dpr}`);

  async function focusCanvas() {
    await Runtime.evaluate({
      expression: 'var c=document.getElementById("kenobiCanvas");c.tabIndex=0;c.focus();'
    });
    await sleep(50);
  }

  async function clickAt(x, y) {
    // Bring Chrome to front so Emscripten processes input
    if (pageClient) {
      await pageClient.Page.bringToFront();
      await sleep(200);  // wait for window manager to activate
    }
    await focusCanvas();
    await sleep(50);

    // Full mouse event sequence
    await Input.dispatchMouseEvent({ type: 'mouseMoved', x, y });
    await sleep(randomInt(30, 60));
    await Input.dispatchMouseEvent({ type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await sleep(randomInt(50, 100));
    await Input.dispatchMouseEvent({ type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  }

  async function setBetAmount(amount) {
    // Click the bet input field
    const ix = Math.round(dims.w * BET_INPUT_PCT.x);
    const iy = Math.round(dims.h * BET_INPUT_PCT.y);

    if (pageClient) {
      await pageClient.Page.bringToFront();
      await sleep(150);
    }
    await focusCanvas();

    // Triple-click to select existing value
    await Input.dispatchMouseEvent({ type: 'mousePressed', x: ix, y: iy, button: 'left', clickCount: 3 });
    await sleep(50);
    await Input.dispatchMouseEvent({ type: 'mouseReleased', x: ix, y: iy, button: 'left', clickCount: 3 });
    await sleep(150);

    // Type the amount
    const amountStr = amount.toFixed(2);
    for (const char of amountStr) {
      await Input.dispatchKeyEvent({
        type: 'char',
        text: char,
      });
      await sleep(randomInt(40, 90));
    }
    await sleep(200);
  }

  async function processCommand(line) {
    const parts = line.trim().split(' ');
    const action = parts[0].toUpperCase();
    const amount = parseFloat(parts[1]) || 0;

    const pct = BUTTON_PCT[action];
    if (!pct) {
      console.log(`UNKNOWN:${action}`);
      return;
    }

    // For RAISE/BET with amount, set the bet input first
    if ((action === 'RAISE' || action === 'BET') && amount > 0) {
      try {
        await setBetAmount(amount);
      } catch (e) {
        console.log(`SET_AMOUNT_FAILED:${e.message}`);
      }
    }

    const x = Math.round(dims.w * pct.x);
    const y = Math.round(dims.h * pct.y);

    await clickAt(x, y);
    console.log(`CLICKED:${action}:${x},${y}${amount > 0 ? ':' + amount : ''}`);
  }

  const rl = readline.createInterface({ input: process.stdin });
  rl.on('line', async (line) => {
    try {
      await processCommand(line);
    } catch (err) {
      console.log(`ERROR:${err.message}`);
    }
  });

  console.log('READY');
  console.error('Auto-player ready. Canvas focus method.');
}

main().catch(e => {
  console.error('FATAL:' + e.message);
  process.exit(1);
});
