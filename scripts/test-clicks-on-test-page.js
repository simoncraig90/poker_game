/**
 * Test all click methods against the local test-table.html.
 *
 * The test page has FOLD/CALL/RAISE buttons that log every click.
 * Verifies clicks register WITHOUT real money risk.
 *
 * Usage: node scripts/test-clicks-on-test-page.js
 */
const CDP = require('chrome-remote-interface');

const PORT = 9222;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getClickLog(Runtime) {
  const r = await Runtime.evaluate({
    returnByValue: true,
    expression: `(function() {
      const lines = Array.from(document.querySelectorAll('#log div')).map(d => d.textContent);
      const clickCount = parseInt(document.getElementById('click-count').textContent) || 0;
      const lastAction = document.getElementById('last-action').textContent;
      return JSON.stringify({ lines: lines.slice(-5), clickCount, lastAction });
    })()`
  });
  return JSON.parse(r.result.value);
}

async function clickButton(client, label, buttonId) {
  const { Runtime, Input, Page } = client;

  // Get button position
  const r = await Runtime.evaluate({
    returnByValue: true,
    expression: `(function() {
      const btn = document.getElementById('${buttonId}');
      if (!btn) return JSON.stringify({error:'not found'});
      const rect = btn.getBoundingClientRect();
      return JSON.stringify({
        x: Math.round(rect.x + rect.width/2),
        y: Math.round(rect.y + rect.height/2)
      });
    })()`
  });
  const pos = JSON.parse(r.result.value);
  if (pos.error) {
    console.log(`  ${label}: button not found`);
    return false;
  }

  // Bring page to front
  await Page.bringToFront();
  await sleep(150);

  // Click via CDP
  const before = await getClickLog(Runtime);
  await Input.dispatchMouseEvent({ type: 'mouseMoved', x: pos.x, y: pos.y });
  await sleep(50);
  await Input.dispatchMouseEvent({ type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
  await sleep(80);
  await Input.dispatchMouseEvent({ type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
  await sleep(200);

  const after = await getClickLog(Runtime);
  const success = after.clickCount > before.clickCount;
  console.log(`  ${label}: ${success ? 'OK' : 'FAILED'} (last: "${after.lastAction}")`);
  return success;
}

async function setBetAmountAndRaise(client, amount) {
  const { Runtime, Input, Page } = client;

  // Find the bet input position
  const r = await Runtime.evaluate({
    returnByValue: true,
    expression: `(function() {
      const inp = document.getElementById('bet-input');
      const raiseBtn = document.getElementById('raise');
      const ir = inp.getBoundingClientRect();
      const rr = raiseBtn.getBoundingClientRect();
      return JSON.stringify({
        inputX: Math.round(ir.x + ir.width/2),
        inputY: Math.round(ir.y + ir.height/2),
        raiseX: Math.round(rr.x + rr.width/2),
        raiseY: Math.round(rr.y + rr.height/2)
      });
    })()`
  });
  const pos = JSON.parse(r.result.value);

  await Page.bringToFront();
  await sleep(150);

  // Triple-click input to select all
  await Input.dispatchMouseEvent({ type: 'mousePressed', x: pos.inputX, y: pos.inputY, button: 'left', clickCount: 3 });
  await sleep(50);
  await Input.dispatchMouseEvent({ type: 'mouseReleased', x: pos.inputX, y: pos.inputY, button: 'left', clickCount: 3 });
  await sleep(200);

  // Type new amount
  const amountStr = amount.toFixed(2);
  for (const char of amountStr) {
    await Input.dispatchKeyEvent({ type: 'char', text: char });
    await sleep(60);
  }
  await sleep(300);

  // Click RAISE
  const before = await getClickLog(Runtime);
  await Input.dispatchMouseEvent({ type: 'mousePressed', x: pos.raiseX, y: pos.raiseY, button: 'left', clickCount: 1 });
  await sleep(80);
  await Input.dispatchMouseEvent({ type: 'mouseReleased', x: pos.raiseX, y: pos.raiseY, button: 'left', clickCount: 1 });
  await sleep(300);

  const after = await getClickLog(Runtime);
  const success = after.lastAction.includes(amountStr);
  console.log(`  RAISE ${amount}: ${success ? 'OK' : 'FAILED'} (last: "${after.lastAction}")`);
  return success;
}

async function main() {
  const targets = await CDP.List({ port: PORT });
  const testPage = targets.find(t => t.type === 'page' && t.url.includes('test-table'));

  if (!testPage) {
    console.log('ERROR: test-table.html not open in Chrome');
    process.exit(1);
  }

  console.log('Connected to test page:', testPage.id.slice(0,8));
  const client = await CDP({ target: testPage.id, port: PORT });
  const { Runtime, Page } = client;
  await Runtime.enable();
  await Page.enable();

  console.log('\n=== Test 1: Basic button clicks ===');
  await clickButton(client, 'FOLD', 'fold');
  await sleep(500);
  await clickButton(client, 'CALL', 'call');
  await sleep(500);
  await clickButton(client, 'RAISE (default)', 'raise');
  await sleep(500);

  console.log('\n=== Test 2: RAISE with bet amount ===');
  await setBetAmountAndRaise(client, 0.10);
  await sleep(500);
  await setBetAmountAndRaise(client, 0.25);
  await sleep(500);

  console.log('\n=== Test 3: Repeated rapid clicks ===');
  for (let i = 0; i < 5; i++) {
    await clickButton(client, `FOLD #${i+1}`, 'fold');
    await sleep(300);
  }

  console.log('\n=== Final log state ===');
  const final = await getClickLog(Runtime);
  console.log(`Total clicks: ${final.clickCount}`);
  console.log('Last 5 entries:');
  final.lines.forEach(l => console.log(`  ${l}`));

  await client.close();
}

main().catch(e => {
  console.error('FATAL:', e.message);
  process.exit(1);
});
