// CDP click at page coordinates. Usage: node cdp-click.js <x> <y> [tab_match]
const CDP = require('chrome-remote-interface');

(async () => {
  const x = parseFloat(process.argv[2]);
  const y = parseFloat(process.argv[3]);
  const tabMatch = (process.argv[4] || 'unibet').toLowerCase();
  let client;
  try {
    const targets = await CDP.List({ port: 9222 });
    const tab = targets.find(t => t.type === 'page' && t.url.toLowerCase().includes(tabMatch));
    if (!tab) { console.error('no matching page'); process.exit(2); }
    client = await CDP({ target: tab.id, port: 9222 });
    const base = { type: 'mousePressed', x, y, button: 'left', clickCount: 1, buttons: 1 };
    await client.Input.dispatchMouseEvent({ type: 'mouseMoved', x, y });
    await client.Input.dispatchMouseEvent(base);
    await client.Input.dispatchMouseEvent({ ...base, type: 'mouseReleased' });
    console.log(`clicked (${x},${y}) on ${tab.url.slice(0, 80)}`);
  } catch (e) {
    console.error('error:', e.message);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
  process.exit(0);
})();
