/**
 * CDP asset extraction for Unibet/Relax Gaming poker client.
 * Intercepts network requests to capture card sprites, table textures, etc.
 *
 * Must be started BEFORE or WHILE the poker client loads assets.
 * Reload the poker page after starting this to capture asset loads.
 *
 * Usage: node scripts/cdp-unibet-assets.js
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');
const path = require('path');

const ASSET_DIR = path.join(__dirname, '..', 'client', 'unibet_assets');

async function main() {
  const targets = await CDP.List({ port: 9222 });
  const page = targets.find(t => t.type === 'page' && t.url.includes('unibet'));
  if (!page) { console.error('No Unibet tab found.'); process.exit(1); }

  // Also try connecting to the Relax iframe directly
  const relaxFrame = targets.find(t => t.type === 'iframe' && t.url.includes('relaxg.com'));

  fs.mkdirSync(ASSET_DIR, { recursive: true });

  let client;
  try {
    // Connect to main page — we can intercept all network from here
    client = await CDP({ target: page.id, port: 9222 });
    const { Network, Page, Fetch } = client;

    await Network.enable();

    // Track all responses for image/asset content
    const assets = [];
    const seen = new Set();

    Network.on('responseReceived', async (params) => {
      const { response, requestId } = params;
      const url = response.url;

      // Filter for poker-related assets
      const isRelax = url.includes('relaxg.com');
      const isAsset = url.match(/\.(png|jpg|jpeg|gif|svg|webp|atlas|json|fnt|mp3|ogg|woff2?|ttf|css)(\?|$)/i);

      if ((isRelax || isAsset) && !seen.has(url)) {
        seen.add(url);
        const filename = url.replace(/\?.*$/, '').split('/').pop();
        const ext = filename.split('.').pop().toLowerCase();

        assets.push({ url, filename, requestId, type: response.mimeType, status: response.status });

        // Try to get the response body
        try {
          const body = await Network.getResponseBody({ requestId });
          const outPath = path.join(ASSET_DIR, filename);

          if (body.base64Encoded) {
            fs.writeFileSync(outPath, Buffer.from(body.body, 'base64'));
          } else {
            fs.writeFileSync(outPath, body.body);
          }
          console.log(`  SAVED: ${filename} (${response.mimeType})`);
        } catch (e) {
          // Body may not be available yet or was evicted
          console.log(`  FOUND: ${filename} (${response.mimeType}) — body not available`);
        }
      }
    });

    console.log('Listening for assets. Reload the Unibet poker page to capture asset loads...');
    console.log('Or wait — assets loaded dynamically during play will also be captured.');
    console.log('Press Ctrl+C to stop.\n');

    // Also dump what we can see right now from cache
    console.log('=== Checking cached resources ===');

    // Get all resources from the page (limited to what main page sees)
    const resources = await Network.getCookies(); // just to confirm connection works

    // Take periodic screenshots to capture different game states
    let screenshotCount = 2;
    const captureInterval = setInterval(async () => {
      try {
        const { data } = await Page.captureScreenshot({ format: 'png', captureBeyondViewport: false });
        const outFile = `client/unibet-table-${screenshotCount}.png`;
        fs.writeFileSync(outFile, Buffer.from(data, 'base64'));
        console.log(`\n  [Screenshot ${screenshotCount}] saved to ${outFile}`);
        screenshotCount++;
      } catch (e) {}
    }, 8000); // every 8 seconds

    // Run for 60 seconds
    await new Promise(r => setTimeout(r, 60000));
    clearInterval(captureInterval);

    console.log(`\n=== Summary ===`);
    console.log(`Assets found: ${assets.length}`);
    for (const a of assets) {
      console.log(`  ${a.filename} — ${a.type}`);
    }

    // List saved files
    const saved = fs.readdirSync(ASSET_DIR);
    console.log(`\nFiles saved to ${ASSET_DIR}: ${saved.length}`);
    for (const f of saved) {
      const stat = fs.statSync(path.join(ASSET_DIR, f));
      console.log(`  ${f} (${(stat.size/1024).toFixed(1)} KB)`);
    }

  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
