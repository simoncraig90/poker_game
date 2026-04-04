/**
 * CDP screenshot — opens lab client in Chrome, waits for render, screenshots.
 * Usage: node scripts/cdp-screenshot.js [output.png]
 */
const CDP = require('chrome-remote-interface');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const OUTPUT = process.argv[2] || 'lab_screenshot.png';
const WIDTH = 500;
const HEIGHT = 900;
const URL = 'http://localhost:9100';

async function main() {
  // Launch Chrome with remote debugging
  const chromeCmd = `start "" "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --headless=new --disable-gpu --window-size=${WIDTH},${HEIGHT}`;
  try {
    execSync(chromeCmd, { stdio: 'ignore', timeout: 5000 });
  } catch (e) { /* may already be running */ }

  // Wait for Chrome to start
  await new Promise(r => setTimeout(r, 2000));

  let client;
  try {
    client = await CDP({ port: 9222 });
    const { Page, Emulation, Runtime } = client;

    // Set viewport to PS portrait size
    await Emulation.setDeviceMetricsOverride({
      width: WIDTH, height: HEIGHT,
      deviceScaleFactor: 1, mobile: false
    });

    await Page.enable();
    await Page.navigate({ url: URL });
    await Page.loadEventFired();

    // Wait for WebSocket connection and initial render
    await new Promise(r => setTimeout(r, 4000));

    // Take screenshot
    const { data } = await Page.captureScreenshot({ format: 'png' });
    fs.writeFileSync(OUTPUT, Buffer.from(data, 'base64'));
    console.log(`Screenshot saved to ${OUTPUT}`);
  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
    // Kill headless chrome
    try { execSync('taskkill /f /im chrome.exe 2>nul', { stdio: 'ignore' }); } catch (e) {}
  }
}

main();
