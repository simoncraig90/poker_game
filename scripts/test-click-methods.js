/**
 * Test all click methods on the Unibet canvas.
 *
 * Tests three approaches:
 * 1. dispatchMouseEvent on IFRAME target
 * 2. dispatchMouseEvent on PAGE target
 * 3. Runtime.evaluate to dispatch canvas MouseEvent via JS
 *
 * For each, clicks the "FOLD TO ANY BET" checkbox (always visible)
 * and checks if it toggled.
 *
 * Usage: node scripts/test-click-methods.js
 */
const CDP = require('chrome-remote-interface');
const PORT = 9222;

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  const targets = await CDP.List({ port: PORT });
  const relaxFrame = targets.find(t => t.type === 'iframe' && t.url.includes('relaxg.com'));
  const mainPage = targets.find(t => t.type === 'page' && t.url.includes('unibet'));

  if (!relaxFrame || !mainPage) {
    console.log('FAIL: Missing targets. iframe:', !!relaxFrame, 'page:', !!mainPage);
    process.exit(1);
  }

  // Connect to both
  const iframeClient = await CDP({ target: relaxFrame.id, port: PORT });
  const pageClient = await CDP({ target: mainPage.id, port: PORT });

  const { Input: iframeInput, Runtime: iframeRuntime } = iframeClient;
  const { Input: pageInput, Runtime: pageRuntime, Page } = pageClient;

  await iframeRuntime.enable();
  await pageRuntime.enable();
  await Page.enable();

  // Get dimensions
  const dimResult = await iframeRuntime.evaluate({
    returnByValue: true,
    expression: `(function() {
      const c = document.querySelector('canvas');
      return JSON.stringify({
        canvasW: c ? c.width : 0, canvasH: c ? c.height : 0,
        clientW: c ? c.clientWidth : 0, clientH: c ? c.clientHeight : 0,
        dpr: window.devicePixelRatio || 1,
        innerW: window.innerWidth, innerH: window.innerHeight
      });
    })()`
  });
  const dims = JSON.parse(dimResult.result.value);
  console.log('Canvas dims:', JSON.stringify(dims));

  // "FOLD TO ANY BET" checkbox position
  // From screenshots: roughly at 17% x, 93% y of the iframe
  // It's a toggle — clicking it should produce a visual change
  const foldAnyX = Math.round(dims.innerW * 0.17);
  const foldAnyY = Math.round(dims.innerH * 0.93);

  // FOLD button position (when visible): 40.3% x, 94.2% y
  const foldX = Math.round(dims.innerW * 0.403);
  const foldY = Math.round(dims.innerH * 0.942);

  // CHECK/CALL button: 60.5% x, 93.2% y
  const callX = Math.round(dims.innerW * 0.605);
  const callY = Math.round(dims.innerH * 0.932);

  console.log(`\nTest positions (CSS coords):`);
  console.log(`  FOLD_ANY_BET: (${foldAnyX}, ${foldAnyY})`);
  console.log(`  FOLD button:  (${foldX}, ${foldY})`);
  console.log(`  CALL button:  (${callX}, ${callY})`);

  // ═══════════════════════════════════════════════════
  // METHOD 1: dispatchMouseEvent on IFRAME
  // ═══════════════════════════════════════════════════
  console.log('\n--- Method 1: dispatchMouseEvent on IFRAME ---');
  try {
    await iframeInput.dispatchMouseEvent({ type: 'mouseMoved', x: foldAnyX, y: foldAnyY, button: 'none' });
    await sleep(50);
    await iframeInput.dispatchMouseEvent({ type: 'mousePressed', x: foldAnyX, y: foldAnyY, button: 'left', clickCount: 1 });
    await sleep(80);
    await iframeInput.dispatchMouseEvent({ type: 'mouseReleased', x: foldAnyX, y: foldAnyY, button: 'left', clickCount: 1 });
    console.log('  Click sent OK');
  } catch (e) {
    console.log('  ERROR:', e.message);
  }
  await sleep(500);

  // ═══════════════════════════════════════════════════
  // METHOD 2: dispatchMouseEvent on PAGE
  // ═══════════════════════════════════════════════════
  console.log('\n--- Method 2: dispatchMouseEvent on PAGE ---');
  try {
    // Same coords — iframe fills the page
    await pageInput.dispatchMouseEvent({ type: 'mouseMoved', x: foldAnyX, y: foldAnyY, button: 'none' });
    await sleep(50);
    await pageInput.dispatchMouseEvent({ type: 'mousePressed', x: foldAnyX, y: foldAnyY, button: 'left', clickCount: 1 });
    await sleep(80);
    await pageInput.dispatchMouseEvent({ type: 'mouseReleased', x: foldAnyX, y: foldAnyY, button: 'left', clickCount: 1 });
    console.log('  Click sent OK');
  } catch (e) {
    console.log('  ERROR:', e.message);
  }
  await sleep(500);

  // ═══════════════════════════════════════════════════
  // METHOD 3: JavaScript canvas event dispatch
  // ═══════════════════════════════════════════════════
  console.log('\n--- Method 3: JS canvas event dispatch on IFRAME ---');
  try {
    // Dispatch MouseEvent directly on the canvas element
    const jsResult = await iframeRuntime.evaluate({
      returnByValue: true,
      expression: `(function() {
        const canvas = document.querySelector('canvas');
        if (!canvas) return 'NO_CANVAS';

        const rect = canvas.getBoundingClientRect();
        const x = ${foldAnyX};
        const y = ${foldAnyY};

        // Create and dispatch mouse events on the canvas
        const opts = {
          bubbles: true, cancelable: true, view: window,
          clientX: x, clientY: y,
          screenX: x, screenY: y,
          button: 0, buttons: 1
        };

        canvas.dispatchEvent(new MouseEvent('mousemove', opts));
        canvas.dispatchEvent(new MouseEvent('mousedown', opts));
        canvas.dispatchEvent(new MouseEvent('mouseup', opts));
        canvas.dispatchEvent(new MouseEvent('click', opts));

        return 'DISPATCHED at (' + x + ',' + y + ') on canvas ' + canvas.width + 'x' + canvas.height;
      })()`
    });
    console.log('  Result:', jsResult.result.value);
  } catch (e) {
    console.log('  ERROR:', e.message);
  }
  await sleep(500);

  // ═══════════════════════════════════════════════════
  // METHOD 4: JS canvas event with DPR-scaled coordinates
  // ═══════════════════════════════════════════════════
  console.log('\n--- Method 4: JS canvas event with DPR-scaled coords ---');
  try {
    const jsResult = await iframeRuntime.evaluate({
      returnByValue: true,
      expression: `(function() {
        const canvas = document.querySelector('canvas');
        if (!canvas) return 'NO_CANVAS';

        // Some canvas frameworks use canvas pixel coords, not CSS coords
        const dpr = window.devicePixelRatio || 1;
        const x = ${foldAnyX};
        const y = ${foldAnyY};

        // Try with offsetX/offsetY which some frameworks read
        const opts = {
          bubbles: true, cancelable: true, view: window,
          clientX: x, clientY: y,
          offsetX: x, offsetY: y,
          screenX: x + window.screenX, screenY: y + window.screenY,
          button: 0, buttons: 1
        };

        canvas.dispatchEvent(new PointerEvent('pointermove', opts));
        canvas.dispatchEvent(new PointerEvent('pointerdown', {...opts, pointerId: 1}));
        canvas.dispatchEvent(new PointerEvent('pointerup', {...opts, pointerId: 1}));

        return 'DISPATCHED pointer events at (' + x + ',' + y + ')';
      })()`
    });
    console.log('  Result:', jsResult.result.value);
  } catch (e) {
    console.log('  ERROR:', e.message);
  }
  await sleep(500);

  // ═══════════════════════════════════════════════════
  // METHOD 5: JS touch event (some canvas frameworks use touch)
  // ═══════════════════════════════════════════════════
  console.log('\n--- Method 5: JS touch event dispatch ---');
  try {
    const jsResult = await iframeRuntime.evaluate({
      returnByValue: true,
      expression: `(function() {
        const canvas = document.querySelector('canvas');
        if (!canvas) return 'NO_CANVAS';

        const x = ${foldAnyX};
        const y = ${foldAnyY};

        const touch = new Touch({
          identifier: Date.now(),
          target: canvas,
          clientX: x, clientY: y,
          screenX: x, screenY: y,
          pageX: x, pageY: y
        });

        canvas.dispatchEvent(new TouchEvent('touchstart', {
          bubbles: true, cancelable: true, touches: [touch], targetTouches: [touch], changedTouches: [touch]
        }));
        canvas.dispatchEvent(new TouchEvent('touchend', {
          bubbles: true, cancelable: true, touches: [], targetTouches: [], changedTouches: [touch]
        }));

        return 'DISPATCHED touch at (' + x + ',' + y + ')';
      })()`
    });
    console.log('  Result:', jsResult.result.value);
  } catch (e) {
    console.log('  ERROR:', e.message);
  }

  console.log('\n--- Check what event listeners the canvas has ---');
  try {
    const listenersResult = await iframeRuntime.evaluate({
      returnByValue: true,
      expression: `(function() {
        const canvas = document.querySelector('canvas');
        if (!canvas) return 'NO_CANVAS';

        // Check for event listeners via getEventListeners (only works in devtools)
        // Instead, check what the framework registers
        const events = [];
        const origAdd = EventTarget.prototype.addEventListener;
        // Can't retroactively find listeners, but we can check known properties
        const props = ['onclick', 'onmousedown', 'onmouseup', 'onmousemove',
                       'onpointerdown', 'onpointerup', 'ontouchstart', 'ontouchend',
                       'oncontextmenu'];
        for (const p of props) {
          if (canvas[p]) events.push(p);
        }

        // Check parent elements too
        let el = canvas.parentElement;
        const parentEvents = [];
        while (el && el !== document.body) {
          for (const p of props) {
            if (el[p]) parentEvents.push(el.tagName + '.' + p);
          }
          el = el.parentElement;
        }

        return JSON.stringify({ canvasEvents: events, parentEvents: parentEvents,
                                canvasId: canvas.id, canvasClass: canvas.className });
      })()`
    });
    console.log('  Listeners:', listenersResult.result.value);
  } catch (e) {
    console.log('  ERROR:', e.message);
  }

  // Also check what framework is running
  console.log('\n--- Detect framework ---');
  try {
    const fwResult = await iframeRuntime.evaluate({
      returnByValue: true,
      expression: `(function() {
        const checks = {
          pixi: typeof PIXI !== 'undefined',
          phaser: typeof Phaser !== 'undefined',
          createjs: typeof createjs !== 'undefined',
          three: typeof THREE !== 'undefined',
          fabric: typeof fabric !== 'undefined',
          konva: typeof Konva !== 'undefined',
          cocos: typeof cc !== 'undefined',
          unity: typeof unityInstance !== 'undefined' || typeof UnityLoader !== 'undefined',
          emscripten: typeof Module !== 'undefined' && typeof Module.canvas !== 'undefined',
        };
        const found = Object.entries(checks).filter(([k,v]) => v).map(([k]) => k);

        // Check for common game engine globals
        const globals = Object.keys(window).filter(k =>
          k.length > 2 && !k.startsWith('_') && typeof window[k] === 'object' && window[k] !== null
        ).slice(0, 30);

        return JSON.stringify({ frameworks: found, sampleGlobals: globals });
      })()`
    });
    console.log('  Framework:', fwResult.result.value);
  } catch (e) {
    console.log('  ERROR:', e.message);
  }

  await iframeClient.close();
  await pageClient.close();
  console.log('\nDone. Check if the FOLD_TO_ANY_BET checkbox toggled after any method.');
}

main().catch(e => {
  console.error('FATAL:', e.message);
  process.exit(1);
});
