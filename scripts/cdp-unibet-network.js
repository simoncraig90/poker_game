/**
 * CDP network capture for Unibet — lists all loaded resources
 * to find card sprites, table assets, fonts etc.
 *
 * Usage: node scripts/cdp-unibet-network.js
 */
const CDP = require('chrome-remote-interface');
const fs = require('fs');

async function main() {
  const targets = await CDP.List({ port: 9222 });
  const page = targets.find(t => t.type === 'page' && t.url.includes('unibet'));
  if (!page) { console.error('No Unibet tab found.'); process.exit(1); }

  let client;
  try {
    client = await CDP({ target: page.id, port: 9222 });
    const { Page, Network, Runtime } = client;

    await Network.enable();
    await Page.enable();

    // Get all resources loaded by the page (including iframe sub-resources)
    // Use Performance.getResourceTimingEntries for a snapshot
    const resources = await Runtime.evaluate({
      expression: `JSON.stringify(performance.getEntriesByType('resource').map(r => ({
        name: r.name,
        type: r.initiatorType,
        size: r.transferSize
      })))`
    });

    const entries = JSON.parse(resources.result.value);
    console.log(`Found ${entries.length} resources on main page\n`);

    // Filter for interesting assets (images, fonts, sprites)
    const images = entries.filter(e =>
      e.name.match(/\.(png|jpg|jpeg|gif|svg|webp|atlas|json|fnt|mp3|ogg|woff|ttf)/i)
    );

    console.log(`=== Asset files (${images.length}) ===`);
    for (const img of images) {
      const short = img.name.replace(/\?.*$/, '');
      const filename = short.split('/').pop();
      console.log(`  ${filename} (${(img.size/1024).toFixed(1)}KB) — ${short.slice(0, 120)}`);
    }

    // Also check for relaxg.com resources specifically
    console.log('\n=== All relaxg.com resources ===');
    const relaxResources = entries.filter(e => e.name.includes('relaxg.com'));
    for (const r of relaxResources) {
      const short = r.name.replace(/\?.*$/, '').split('/').slice(-3).join('/');
      console.log(`  ${short} (${(r.size/1024).toFixed(1)}KB)`);
    }

    // Check for WebSocket connections
    console.log('\n=== JS/CSS bundles ===');
    const bundles = entries.filter(e => e.name.match(/\.(js|css)(\?|$)/i) && e.name.includes('relaxg'));
    for (const b of bundles) {
      const short = b.name.replace(/\?.*$/, '').split('/').slice(-2).join('/');
      console.log(`  ${short} (${(b.size/1024).toFixed(1)}KB)`);
    }

  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
