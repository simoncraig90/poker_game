#!/usr/bin/env node
/**
 * Inspect the CoinPoker iframe DOM via CDP and dump everything that looks
 * like a card, button, stack, pot, or player. Used to map real selectors
 * for vision/coinpoker_dom.py and vision/coinpoker_clicker.py.
 *
 * Usage: node scripts/inspect-coinpoker-dom.js
 *
 * Prereq: CoinPoker running with --remote-debugging-port=9223
 *         AND user seated at a table with a hand in progress.
 */
"use strict";

const CDP = require("chrome-remote-interface");

const PORT = 9223;
const IFRAME_URL_MATCH = "cloudfront.net";

async function main() {
  let client;
  try {
    // Find the iframe target
    const targets = await CDP.List({ port: PORT });
    const iframe = targets.find(t => (t.url || "").includes(IFRAME_URL_MATCH));
    if (!iframe) {
      console.error("ERR: No iframe target matching", IFRAME_URL_MATCH);
      console.error("Available targets:");
      targets.forEach(t => console.error(`  ${t.type} ${t.url.substring(0, 100)}`));
      process.exit(1);
    }
    console.log(`Connecting to iframe: ${iframe.id}`);
    console.log(`URL: ${iframe.url.substring(0, 120)}...`);

    client = await CDP({ target: iframe.id, port: PORT });
    await client.Runtime.enable();

    // Inject inspection script that dumps everything potentially relevant
    const script = `
      (function() {
        const out = { sections: {} };

        function describe(el, max = 200) {
          if (!el) return null;
          const tag = el.tagName.toLowerCase();
          const id = el.id ? '#' + el.id : '';
          const cls = el.className && typeof el.className === 'string' ? '.' + el.className.split(' ').slice(0, 3).join('.') : '';
          const text = (el.innerText || el.textContent || '').trim().substring(0, max);
          const attrs = {};
          for (const a of el.attributes || []) {
            if (['data-testid', 'aria-label', 'role', 'name', 'type'].includes(a.name)) {
              attrs[a.name] = a.value;
            }
          }
          return { tag, id, cls, text, attrs };
        }

        function findAll(selector, max = 30) {
          const els = Array.from(document.querySelectorAll(selector));
          return els.slice(0, max).map(e => describe(e));
        }

        // 1. All buttons (action buttons should be here)
        out.sections.buttons = findAll('button', 50);

        // 2. Anything that could be a card — common patterns
        out.sections.cards = [
          ...findAll('[class*="card" i]', 30),
          ...findAll('[data-card]', 30),
          ...findAll('[class*="hole" i]', 20),
        ];

        // 3. Possible board / community cards
        out.sections.board = [
          ...findAll('[class*="board" i]', 20),
          ...findAll('[class*="community" i]', 20),
        ];

        // 4. Pot / stack labels — anything with currency-looking text
        const allDivs = Array.from(document.querySelectorAll('div, span'));
        const moneyEls = allDivs
          .filter(el => {
            const t = (el.innerText || '').trim();
            return /[₮$€]/.test(t) || /^[0-9]+\\.[0-9]{2}$/.test(t) || /\\b[0-9]+\\.?[0-9]*\\s*(usdt|chips?)\\b/i.test(t);
          })
          .slice(0, 30)
          .map(el => describe(el, 50));
        out.sections.money = moneyEls;

        // 5. Possible action bar containers
        out.sections.actionBar = [
          ...findAll('[class*="action" i]', 20),
          ...findAll('[class*="control" i]', 20),
          ...findAll('[class*="bottom" i]', 20),
        ];

        // 6. Player seats
        out.sections.seats = [
          ...findAll('[class*="seat" i]', 20),
          ...findAll('[class*="player" i]', 20),
        ];

        // 7. Bet input fields
        out.sections.inputs = findAll('input', 20);

        // 8. Top-level structure
        out.url = location.href;
        out.title = document.title;
        out.bodyClass = document.body.className;
        out.elementCounts = {
          divs: document.querySelectorAll('div').length,
          buttons: document.querySelectorAll('button').length,
          inputs: document.querySelectorAll('input').length,
        };

        return JSON.stringify(out, null, 2);
      })();
    `;

    const result = await client.Runtime.evaluate({
      expression: script,
      returnByValue: true,
    });

    if (result.exceptionDetails) {
      console.error("Eval error:", result.exceptionDetails);
      process.exit(1);
    }

    console.log(result.result.value);
  } catch (e) {
    console.error("ERR:", e.message);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
}

main();
