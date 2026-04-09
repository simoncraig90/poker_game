#!/usr/bin/env node
/**
 * Take a Chrome DevTools screenshot of a tab matching a URL substring,
 * save it to a file. Used by scripts/ocr-click.py when --source cdp is
 * set.
 *
 * Why this exists: Python's raw websocket connection to Chrome's
 * /devtools/page/{id} URL gets rejected by the origin check
 * ("Rejected an incoming WebSocket connection from origin..."), but
 * the chrome-remote-interface npm lib knows how to set the right
 * Origin header. So Python shells out to this Node helper for the
 * screenshot, then reads the saved PNG file.
 *
 * Usage:
 *   node scripts/cdp-tab-screenshot.js <output_path> [tab_match] [port]
 *
 * Examples:
 *   node scripts/cdp-tab-screenshot.js out.png unibet 9222
 *   node scripts/cdp-tab-screenshot.js out.png coinpoker 9223
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');

async function main() {
  const outputPath = process.argv[2] || 'cdp-tab-screenshot.png';
  const tabMatch = (process.argv[3] || 'unibet').toLowerCase();
  const port = parseInt(process.argv[4] || '9222', 10);

  let client;
  try {
    const targets = await CDP.List({ port });
    const target = targets.find(
      t => t.type === 'page' && t.url.toLowerCase().includes(tabMatch)
    );
    if (!target) {
      console.error(`No tab matching '${tabMatch}' on port ${port}`);
      process.exit(2);
    }
    client = await CDP({ target: target.id, port });
    await client.Page.enable();
    const ss = await client.Page.captureScreenshot({ format: 'png' });
    fs.writeFileSync(outputPath, Buffer.from(ss.data, 'base64'));
    console.log(`saved ${outputPath} (${ss.data.length} b64 bytes) from ${target.url.slice(0, 100)}`);
  } catch (e) {
    console.error('error:', e.message);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
  process.exit(0);
}

main();
