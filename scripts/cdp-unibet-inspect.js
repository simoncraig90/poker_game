/**
 * Deep inspection of the Unibet/Relax Gaming poker client iframe.
 * Checks for anti-cheat, bot detection, fingerprinting, WebSocket protocol.
 *
 * Usage: node scripts/cdp-unibet-inspect.js
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
    const { Page, Network, Runtime, DOM } = client;

    await Network.enable();
    await Page.enable();
    await Runtime.enable();

    // 1. Get all frames (including iframes)
    const frameTree = await Page.getFrameTree();
    console.log('=== FRAME TREE ===');
    function printFrames(frame, indent = 0) {
      const prefix = '  '.repeat(indent);
      console.log(`${prefix}[${frame.id}] ${frame.url.slice(0, 100)}`);
      if (frame.childFrames) {
        for (const child of frame.childFrames) printFrames(child.frame, indent + 1);
      }
    }
    printFrames(frameTree.frameTree.frame);
    if (frameTree.frameTree.childFrames) {
      for (const child of frameTree.frameTree.childFrames) printFrames(child.frame, 1);
    }

    // 2. Execute JS in the Relax Gaming iframe context
    // Find the iframe's execution context
    const contexts = [];
    Runtime.on('executionContextCreated', (params) => {
      contexts.push(params.context);
    });

    // Get existing contexts
    await Runtime.enable();
    // Wait a moment for contexts to populate
    await new Promise(r => setTimeout(r, 1000));

    // Try to evaluate in each context to find the poker one
    console.log('\n=== EXECUTION CONTEXTS ===');
    const ctxResult = await Runtime.evaluate({
      expression: `
        // From main page, inspect the iframe
        const iframes = document.querySelectorAll('iframe');
        const info = Array.from(iframes).map(f => ({
          src: f.src ? f.src.slice(0, 120) : '(no src)',
          id: f.id,
          class: f.className,
          sandbox: f.sandbox ? f.sandbox.toString() : 'none',
          allow: f.allow || 'none',
          w: f.offsetWidth,
          h: f.offsetHeight
        }));
        JSON.stringify(info, null, 2);
      `
    });
    console.log(ctxResult.result.value);

    // 3. Check for known anti-cheat indicators from main page
    console.log('\n=== ANTI-CHEAT INDICATORS (main page) ===');
    const antiCheat = await Runtime.evaluate({
      expression: `
        const checks = {};
        // Canvas fingerprinting
        checks.canvasFingerprint = !!HTMLCanvasElement.prototype.toDataURL.__lookupGetter__
          || HTMLCanvasElement.prototype.toDataURL.toString().includes('native');
        // WebGL fingerprinting
        checks.webglRenderer = (() => {
          try {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl');
            const dbg = gl.getExtension('WEBGL_debug_renderer_info');
            return dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'no debug ext';
          } catch(e) { return 'error'; }
        })();
        // Navigator checks
        checks.webdriver = navigator.webdriver;
        checks.plugins = navigator.plugins.length;
        checks.languages = navigator.languages;
        checks.platform = navigator.platform;
        checks.hardwareConcurrency = navigator.hardwareConcurrency;
        checks.deviceMemory = navigator.deviceMemory;
        // Performance observer (timing attacks)
        checks.performanceObserver = typeof PerformanceObserver !== 'undefined';
        // Detect if mouse/keyboard listeners are attached to window
        checks.eventListenerCount = typeof getEventListeners !== 'undefined' ? 'available' : 'devtools only';
        // Check for known bot detection scripts
        const scripts = Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
        checks.suspiciousScripts = scripts.filter(s =>
          s.includes('fingerprint') || s.includes('deviceid') || s.includes('botd') ||
          s.includes('kasada') || s.includes('akamai') || s.includes('datadome') ||
          s.includes('imperva') || s.includes('perimeterx') || s.includes('recaptcha') ||
          s.includes('hcaptcha') || s.includes('shape') || s.includes('distil')
        );
        checks.allScriptDomains = [...new Set(scripts.map(s => new URL(s).hostname))];
        JSON.stringify(checks, null, 2);
      `
    });
    console.log(antiCheat.result.value);

    // 4. Check cookies for tracking/fingerprint
    console.log('\n=== COOKIES ===');
    const cookies = await Network.getCookies();
    const suspectCookies = cookies.cookies.filter(c =>
      c.name.match(/fp|fingerprint|device|session|track|bot|detect|kasada|__cf|datadome/i)
    );
    console.log(`Total cookies: ${cookies.cookies.length}`);
    console.log(`Suspect cookies: ${suspectCookies.length}`);
    for (const c of suspectCookies) {
      console.log(`  ${c.name} = ${c.value.slice(0, 60)}... (domain: ${c.domain})`);
    }

    // Also list all cookie names for review
    console.log('\nAll cookie names:');
    const byDomain = {};
    for (const c of cookies.cookies) {
      if (!byDomain[c.domain]) byDomain[c.domain] = [];
      byDomain[c.domain].push(c.name);
    }
    for (const [domain, names] of Object.entries(byDomain)) {
      console.log(`  ${domain}: ${names.join(', ')}`);
    }

    // 5. Check WebSocket connections
    console.log('\n=== WEBSOCKET INSPECTION ===');
    const wsInfo = await Runtime.evaluate({
      expression: `
        // Check for WebSocket usage
        const origWS = WebSocket;
        const wsLog = [];
        // Can't intercept already-created WSes, but check if any exist
        JSON.stringify({
          wsConstructorPatched: WebSocket.toString() !== 'function WebSocket() { [native code] }',
          wsPrototype: Object.getOwnPropertyNames(WebSocket.prototype).join(', ')
        }, null, 2);
      `
    });
    console.log(wsInfo.result.value);

    // 6. Check for Relax Gaming specific endpoints
    console.log('\n=== NETWORK: Recent XHR/Fetch requests ===');
    const perfEntries = await Runtime.evaluate({
      expression: `
        const entries = performance.getEntriesByType('resource');
        const xhr = entries.filter(e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch');
        JSON.stringify(xhr.map(x => ({ url: x.name.slice(0, 150), type: x.initiatorType })), null, 2);
      `
    });
    console.log(perfEntries.result.value);

  } catch (err) {
    console.error('CDP error:', err.message);
  } finally {
    if (client) await client.close();
  }
}

main();
