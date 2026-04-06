/**
 * Continuous Unibet table capture for training data.
 * Takes screenshots every N seconds, crops to just the poker iframe area.
 *
 * Usage: node scripts/cdp-unibet-capture-loop.js [interval_sec] [count]
 *   Default: every 3 seconds, 100 captures
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');
const path = require('path');

const INTERVAL = parseInt(process.argv[2]) || 3;
const MAX_COUNT = parseInt(process.argv[3]) || 100;
const OUT_DIR = path.join(__dirname, '..', 'vision', 'captures', 'unibet');

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const targets = await CDP.List({ port: 9222 });
  const page = targets.find(t => t.type === 'page' && t.url.includes('unibet'));
  if (!page) { console.error('No Unibet tab found.'); process.exit(1); }

  let client;
  try {
    client = await CDP({ target: page.id, port: 9222 });
    const { Page } = client;
    await Page.enable();

    console.log(`Capturing ${MAX_COUNT} frames every ${INTERVAL}s to ${OUT_DIR}`);
    console.log('Press Ctrl+C to stop.\n');

    let count = 0;
    let lastHash = '';

    while (count < MAX_COUNT) {
      try {
        const { data } = await Page.captureScreenshot({
          format: 'png',
          captureBeyondViewport: false
        });

        const buf = Buffer.from(data, 'base64');

        // Simple change detection: skip if screenshot is identical
        const hash = require('crypto').createHash('md5').update(buf.slice(0, 10000)).digest('hex');
        if (hash === lastHash) {
          process.stdout.write('.');
          await new Promise(r => setTimeout(r, INTERVAL * 1000));
          continue;
        }
        lastHash = hash;

        const filename = `frame_${String(count).padStart(5, '0')}.png`;
        fs.writeFileSync(path.join(OUT_DIR, filename), buf);
        count++;
        const now = new Date().toLocaleTimeString();
        console.log(`[${now}] ${filename} (${(buf.length/1024).toFixed(0)} KB) — ${count}/${MAX_COUNT}`);
      } catch (e) {
        console.error('Capture error:', e.message);
      }

      await new Promise(r => setTimeout(r, INTERVAL * 1000));
    }

    console.log(`\nDone. ${count} frames saved to ${OUT_DIR}`);

  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
