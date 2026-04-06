/**
 * CDP capture for Unibet — captures the poker table iframe directly
 * (Relax Gaming engine at cf-mt-cdn1.relaxg.com)
 *
 * Usage: node scripts/cdp-capture-unibet-table.js [output.png]
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');

const OUTPUT = process.argv[2] || 'client/unibet-table.png';

async function main() {
  const targets = await CDP.List({ port: 9222 });

  // Find the Relax Gaming iframe (the actual poker client)
  const relaxFrame = targets.find(t =>
    t.type === 'iframe' && t.url.includes('relaxg.com')
  );

  // Also try the main page as fallback
  const mainPage = targets.find(t =>
    t.type === 'page' && t.url.includes('unibet')
  );

  const target = relaxFrame || mainPage;
  if (!target) {
    console.error('No Unibet/Relax Gaming tab found.');
    process.exit(1);
  }

  console.log(`Connecting to: [${target.type}] ${target.title || '(iframe)'}`);
  console.log(`URL: ${target.url.slice(0, 100)}...`);

  let client;
  try {
    client = await CDP({ target: target.id, port: 9222 });
    const { Page, Runtime, DOM } = client;

    await Page.enable();
    await Runtime.enable();

    // Get viewport/canvas info
    const info = await Runtime.evaluate({
      expression: `JSON.stringify({
        w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio,
        canvases: document.querySelectorAll('canvas').length,
        canvasSize: document.querySelector('canvas') ?
          document.querySelector('canvas').width + 'x' + document.querySelector('canvas').height : 'none',
        bodySize: document.body.scrollWidth + 'x' + document.body.scrollHeight
      })`
    });
    console.log('Frame info:', info.result.value);

    // Screenshot
    const { data } = await Page.captureScreenshot({
      format: 'png',
      captureBeyondViewport: false
    });

    fs.writeFileSync(OUTPUT, Buffer.from(data, 'base64'));
    console.log(`\nScreenshot saved to ${OUTPUT} (${(Buffer.from(data, 'base64').length / 1024).toFixed(0)} KB)`);

    // Also dump the DOM structure for skin building
    const domInfo = await Runtime.evaluate({
      expression: `
        // Get key CSS custom properties / colors
        const cs = getComputedStyle(document.body);
        const styles = {};
        for (const prop of ['background-color', 'color', 'font-family']) {
          styles[prop] = cs.getPropertyValue(prop);
        }
        // Check for canvas-based rendering
        const canvases = Array.from(document.querySelectorAll('canvas')).map(c => ({
          id: c.id, class: c.className, w: c.width, h: c.height,
          style: c.style.cssText.slice(0, 200)
        }));
        JSON.stringify({ styles, canvases, title: document.title }, null, 2);
      `
    });
    console.log('\nDOM info:', domInfo.result.value);

  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
