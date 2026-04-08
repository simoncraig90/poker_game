#!/usr/bin/env node
"use strict";

/**
 * Auto-login to Unibet. Handles cookies, login, navigation.
 * Uses defaultViewport: null to preserve page layout.
 */

const puppeteer = require("puppeteer-core");

const EMAIL = "simon.craig90@gmail.com";
const PASS = "Largekebab17!";

async function main() {
  const browser = await puppeteer.connect({ browserURL: "http://127.0.0.1:9222", defaultViewport: null });
  const pages = await browser.pages();

  // Prefer an existing Unibet tab; otherwise reuse about:blank or open new tab
  let page = pages.find(p => p.url().includes("unibet"));
  const alreadyOnUnibet = !!page;
  if (!page) page = pages.find(p => p.url() === "about:blank" || p.url() === "");
  if (!page) page = await browser.newPage();

  // Force exact window size — must match what the bot's click coordinates were
  // calibrated against. Prevents canvas-bottom-cut-off and click misalignment.
  await enforceWindowSize(page, 1280, 721);

  // Apply stealth patches BEFORE navigation so they're active when Unibet/reCAPTCHA loads
  await applyStealth(page);

  if (!alreadyOnUnibet) {
    console.log("[login] Navigating to Unibet poker");
    await page.goto("https://www.unibet.co.uk/play/pokerwebclient#playforreal", { waitUntil: "domcontentloaded", timeout: 60000 });
  }

  await sleep(2500);

  // Reject cookies if present
  try {
    const btns = await page.$$("button");
    for (const b of btns) {
      const t = await page.evaluate(el => el.textContent.trim().toLowerCase(), b);
      if (t.includes("reject all")) { await b.click(); console.log("[login] Cookies rejected"); await sleep(1000); break; }
    }
  } catch {}

  // Check if login form exists
  let hasLogin = await page.evaluate(() => !!document.querySelector('input[name=username]'));

  // If no login form, check if we need to click "Log In" link
  if (!hasLogin) {
    try {
      const links = await page.$$("a, button");
      for (const l of links) {
        const t = await page.evaluate(el => el.textContent.trim().toLowerCase(), l);
        if (t === "log in") { await l.click(); console.log("[login] Clicked Log In link"); await sleep(2000); break; }
      }
      hasLogin = await page.evaluate(() => !!document.querySelector('input[name=username]'));
    } catch {}
  }

  // If still no login form, we're already logged in
  if (!hasLogin) {
    console.log("[login] Already logged in");
    // Make sure we're on the poker page
    if (!page.url().includes("pokerwebclient")) {
      await page.goto("https://www.unibet.co.uk/play/pokerwebclient#playforreal", { waitUntil: "domcontentloaded", timeout: 30000 });
      console.log("[login] Navigated to poker");
    }
    browser.disconnect();
    return;
  }

  // Fill login form
  const email = await page.$('input[name=username]');
  if (email) { await email.click({ clickCount: 3 }); await page.keyboard.type(EMAIL, { delay: 90 + Math.floor(Math.random() * 80) }); }

  const pass = await page.$('input[name=password]');
  if (pass) { await pass.click({ clickCount: 3 }); await page.keyboard.type(PASS, { delay: 90 + Math.floor(Math.random() * 80) }); }

  // Tick "remember me" / "stay logged in" if present, so we skip login next time
  try {
    const ticked = await page.evaluate(() => {
      const boxes = Array.from(document.querySelectorAll('input[type=checkbox]'));
      for (const cb of boxes) {
        const label = (cb.closest('label')?.textContent || '') +
                      (document.querySelector(`label[for="${cb.id}"]`)?.textContent || '');
        const t = label.toLowerCase();
        if (t.includes('remember') || t.includes('stay') || t.includes('keep me')) {
          if (!cb.checked) cb.click();
          return true;
        }
      }
      return false;
    });
    if (ticked) console.log("[login] Ticked remember-me");
  } catch {}

  // DOB
  const selects = await page.$$("select");
  const formSelects = [];
  for (const s of selects) { if (await page.evaluate(el => el.options.length, s) > 10) formSelects.push(s); }
  if (formSelects.length >= 3) {
    await page.evaluate(el => { el.value = "14"; el.dispatchEvent(new Event("change", { bubbles: true })); }, formSelects[0]);
    await page.evaluate(el => { el.value = "02"; el.dispatchEvent(new Event("change", { bubbles: true })); }, formSelects[1]);
    await page.evaluate(el => { el.value = "1990"; el.dispatchEvent(new Event("change", { bubbles: true })); }, formSelects[2]);
  }

  // Wait for human to solve reCAPTCHA if present (before submit)
  await waitForCaptcha(page);

  // Submit with Enter
  await sleep(300);
  if (pass) await pass.click();
  await page.keyboard.press("Enter");
  console.log("[login] Submitted");

  // After-submit captcha (some flows show it post-submit)
  await sleep(2000);
  await waitForCaptcha(page);

  // Wait for login, dismiss prompts
  await sleep(5000);
  await page.keyboard.press("Escape");
  await sleep(500);

  // Navigate to poker if redirected elsewhere
  if (!page.url().includes("pokerwebclient")) {
    await page.goto("https://www.unibet.co.uk/play/pokerwebclient#playforreal", { waitUntil: "domcontentloaded", timeout: 30000 });
    console.log("[login] Navigated to poker");
  }

  // Make poker client fill the window (hide site header)
  await sleep(2000);
  try {
    const currentPage = (await browser.pages()).find(p => p.url().includes("unibet"));
    if (currentPage) {
      await currentPage.evaluate(() => {
        const apply = () => {
          document.querySelectorAll("header, nav, [class*=header], [class*=Header], [class*=nav-bar]")
            .forEach(h => h.style.display = "none");
          document.querySelectorAll("iframe").forEach(f => {
            if (f.src && f.src.includes("relaxg")) {
              f.style.position = "fixed";
              f.style.top = "0";
              f.style.left = "0";
              f.style.width = "100vw";
              f.style.height = "100vh";
              f.style.zIndex = "9999";
            }
          });
          // Force the Emscripten canvas inside the iframe to re-flow.
          // The canvas listens for window.resize; without this event,
          // it stays at whatever size it computed at initial load.
          window.dispatchEvent(new Event("resize"));
        };

        // Apply immediately
        apply();

        // Re-apply on any future viewport change (handles user-resized window)
        try {
          const ro = new ResizeObserver(() => apply());
          ro.observe(document.documentElement);
        } catch {}

        // Belt-and-braces: re-apply for the next 8 seconds in case the
        // Emscripten canvas finishes loading after our first pass.
        let n = 0;
        const iv = setInterval(() => {
          apply();
          if (++n >= 8) clearInterval(iv);
        }, 1000);
      });
      console.log("[login] Poker client fullscreen (with ResizeObserver + retry loop)");
    }
  } catch {}

  browser.disconnect();
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Force the Chrome window to exact outer dimensions via CDP. Overrides
// Chrome's saved window state which may ignore the --window-size launch flag.
async function enforceWindowSize(page, width, height) {
  try {
    const cdp = await page.target().createCDPSession();
    const { windowId } = await cdp.send("Browser.getWindowForTarget");
    // Reset normal state first (in case window was minimized/maximized)
    await cdp.send("Browser.setWindowBounds", {
      windowId,
      bounds: { windowState: "normal" },
    });
    await cdp.send("Browser.setWindowBounds", {
      windowId,
      bounds: { width, height, left: 0, top: 0, windowState: "normal" },
    });
    await cdp.detach();
    console.log(`[login] Window forced to ${width}x${height}`);
  } catch (e) {
    console.log(`[login] enforceWindowSize failed: ${e.message}`);
  }
}

// Inject patches to mask CDP/automation tells before any page script runs.
// Targets the fingerprints reCAPTCHA and Cloudflare bot-detection look at.
async function applyStealth(page) {
  await page.evaluateOnNewDocument(() => {
    // 1. navigator.webdriver — must be undefined, not just false
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. chrome.runtime — real Chrome has it, headless/automation often doesn't
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) window.chrome.runtime = { id: undefined, connect: () => {}, sendMessage: () => {} };

    // 3. navigator.plugins — empty array is a bot tell
    Object.defineProperty(navigator, 'plugins', {
      get: () => [
        { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      ],
    });

    // 4. navigator.languages — automation often shows just ['en-US']
    Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });

    // 5. permissions.query — bot detectors check that notifications return 'denied' for headless
    const origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (origQuery) {
      window.navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : origQuery(params);
    }

    // 6. WebGL vendor/renderer — headless reports SwiftShader, real GPUs don't
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (param) {
      if (param === 37445) return 'Intel Inc.';                    // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Intel(R) Iris(R) Xe Graphics';  // UNMASKED_RENDERER_WEBGL
      return getParameter.apply(this, [param]);
    };

    // 7. iframe.contentWindow — Puppeteer's default has a quirk detectors test for
    try {
      const desc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function () { return desc.get.call(this); },
      });
    } catch {}

    // 8. navigator.hardwareConcurrency — should match a real machine
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

    // 9. Hide CDP runtime markers (Puppeteer leaves these on window)
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
  });
}

// Detect a reCAPTCHA iframe on the page. Returns true if one is present and unsolved.
async function hasUnsolvedCaptcha(page) {
  try {
    return await page.evaluate(() => {
      const frames = Array.from(document.querySelectorAll("iframe"));
      const recap = frames.find(f => (f.src || "").includes("recaptcha/api2/anchor"));
      if (!recap) return false;
      // If a token has been filled, the hidden textarea will have a value
      const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
      if (ta && ta.value && ta.value.length > 20) return false;
      return true;
    });
  } catch { return false; }
}

// If a captcha is present, wait (up to 2 min) for the user to click it.
async function waitForCaptcha(page) {
  if (!(await hasUnsolvedCaptcha(page))) return;
  console.log("[login] *** reCAPTCHA detected — please click the checkbox in Chrome ***");
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await sleep(1000);
    if (!(await hasUnsolvedCaptcha(page))) {
      console.log("[login] reCAPTCHA solved, continuing");
      await sleep(500);
      return;
    }
  }
  console.log("[login] reCAPTCHA wait timed out, continuing anyway");
}
main().catch(e => { console.error("[login] Error:", e.message); process.exit(0); });
