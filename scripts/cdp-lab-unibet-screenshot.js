/**
 * Takes a screenshot of the lab client with Unibet skin applied.
 * Opens a new tab in the existing Chrome debug session.
 *
 * Usage: node scripts/cdp-lab-unibet-screenshot.js [output.png]
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');

const OUTPUT = process.argv[2] || 'client/unibet-lab-preview.png';

async function main() {
  let client;
  try {
    // Create a new tab for the lab client
    const target = await CDP.New({
      port: 9222,
      url: 'http://localhost:9100/?skin=unibet'
    });

    // Wait for page to load
    await new Promise(r => setTimeout(r, 3000));

    client = await CDP({ target: target.id, port: 9222 });
    const { Page, Emulation } = client;

    await Page.enable();

    // Match Unibet iframe dimensions
    await Emulation.setDeviceMetricsOverride({
      width: 1065, height: 594,
      deviceScaleFactor: 1.5, mobile: false
    });

    // Wait for render
    await new Promise(r => setTimeout(r, 3000));

    const { data } = await Page.captureScreenshot({ format: 'png' });
    fs.writeFileSync(OUTPUT, Buffer.from(data, 'base64'));
    console.log(`Screenshot saved to ${OUTPUT}`);

    // Close the tab
    await CDP.Close({ port: 9222, id: target.id });

  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
