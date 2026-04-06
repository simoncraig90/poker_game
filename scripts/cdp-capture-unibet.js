/**
 * CDP capture for Unibet — connects to already-running Chrome,
 * finds the Unibet tab, and takes a screenshot.
 *
 * Usage: node scripts/cdp-capture-unibet.js [output.png]
 *
 * Chrome must be running with --remote-debugging-port=9222
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');

const OUTPUT = process.argv[2] || 'client/unibet-reference.png';

async function main() {
  // List all tabs
  const targets = await CDP.List({ port: 9222 });
  console.log(`Found ${targets.length} tabs:`);
  for (const t of targets) {
    console.log(`  [${t.type}] ${t.title} — ${t.url}`);
  }

  // Find the Unibet tab
  const unibet = targets.find(t =>
    t.type === 'page' && (
      t.url.toLowerCase().includes('unibet') ||
      t.title.toLowerCase().includes('unibet') ||
      t.title.toLowerCase().includes('poker')
    )
  );

  if (!unibet) {
    console.error('No Unibet tab found. Open Unibet poker in this Chrome instance.');
    console.error('Available pages:', targets.filter(t => t.type === 'page').map(t => t.url));
    process.exit(1);
  }

  console.log(`\nConnecting to: ${unibet.title} (${unibet.url})`);

  let client;
  try {
    client = await CDP({ target: unibet.id, port: 9222 });
    const { Page, Runtime } = client;

    await Page.enable();

    // Get viewport dimensions
    const dims = await Runtime.evaluate({
      expression: `JSON.stringify({ w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio })`
    });
    console.log('Viewport:', dims.result.value);

    // Screenshot the full page
    const { data } = await Page.captureScreenshot({
      format: 'png',
      captureBeyondViewport: false
    });

    fs.writeFileSync(OUTPUT, Buffer.from(data, 'base64'));
    console.log(`\nScreenshot saved to ${OUTPUT} (${(Buffer.from(data, 'base64').length / 1024).toFixed(0)} KB)`);
  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
