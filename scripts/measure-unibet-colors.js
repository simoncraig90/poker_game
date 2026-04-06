/**
 * Extract dominant colors from Unibet table screenshot.
 * Measures felt, background, buttons, panels from specific regions.
 *
 * Usage: node scripts/measure-unibet-colors.js client/unibet-table-1.png
 */
const sharp = require('sharp');
const fs = require('fs');

const INPUT = process.argv[2] || 'client/unibet-table-1.png';

async function main() {
  const img = sharp(INPUT);
  const meta = await img.metadata();
  console.log(`Image: ${meta.width}x${meta.height}`);

  const raw = await img.raw().toBuffer();
  const W = meta.width;
  const H = meta.height;
  const C = meta.channels;

  function getPixel(x, y) {
    const i = (y * W + x) * C;
    return { r: raw[i], g: raw[i+1], b: raw[i+2] };
  }

  function avgRegion(x1, y1, x2, y2) {
    let r = 0, g = 0, b = 0, n = 0;
    for (let y = y1; y < y2; y += 2) {
      for (let x = x1; x < x2; x += 2) {
        const p = getPixel(x, y);
        r += p.r; g += p.g; b += p.b; n++;
      }
    }
    return {
      r: Math.round(r/n), g: Math.round(g/n), b: Math.round(b/n),
      hex: `#${Math.round(r/n).toString(16).padStart(2,'0')}${Math.round(g/n).toString(16).padStart(2,'0')}${Math.round(b/n).toString(16).padStart(2,'0')}`
    };
  }

  // The poker client iframe area is roughly:
  // From the screenshot, the iframe starts around y=160 and ends around y=460 (within 594px height at 1.5x DPR)
  // Full page is 1252x1278 at 1.5x DPR = 1878x1917 actual pixels? No, screenshot is at native res.
  // Let me just measure at percentage-based regions

  // Felt center (middle of the green oval)
  const feltCenter = avgRegion(
    Math.round(W * 0.4), Math.round(H * 0.22),
    Math.round(W * 0.6), Math.round(H * 0.28)
  );
  console.log(`Felt center: ${feltCenter.hex} (r=${feltCenter.r} g=${feltCenter.g} b=${feltCenter.b})`);

  // Felt edge (left/right edge of oval)
  const feltEdge = avgRegion(
    Math.round(W * 0.12), Math.round(H * 0.25),
    Math.round(W * 0.18), Math.round(H * 0.30)
  );
  console.log(`Felt edge: ${feltEdge.hex} (r=${feltEdge.r} g=${feltEdge.g} b=${feltEdge.b})`);

  // Background (below table)
  const bg = avgRegion(
    Math.round(W * 0.1), Math.round(H * 0.40),
    Math.round(W * 0.2), Math.round(H * 0.45)
  );
  console.log(`Background: ${bg.hex} (r=${bg.r} g=${bg.g} b=${bg.b})`);

  // Header bar (green bar at top of client area)
  const header = avgRegion(
    Math.round(W * 0.3), Math.round(H * 0.08),
    Math.round(W * 0.7), Math.round(H * 0.09)
  );
  console.log(`Header: ${header.hex} (r=${header.r} g=${header.g} b=${header.b})`);

  // Seat panel area (a player's panel)
  const seatPanel = avgRegion(
    Math.round(W * 0.08), Math.round(H * 0.15),
    Math.round(W * 0.15), Math.round(H * 0.17)
  );
  console.log(`Seat panel: ${seatPanel.hex} (r=${seatPanel.r} g=${seatPanel.g} b=${seatPanel.b})`);

  // Action button area
  const actionBtn = avgRegion(
    Math.round(W * 0.3), Math.round(H * 0.33),
    Math.round(W * 0.5), Math.round(H * 0.36)
  );
  console.log(`Action area: ${actionBtn.hex} (r=${actionBtn.r} g=${actionBtn.g} b=${actionBtn.b})`);

  // Sample grid of pixels across felt
  console.log('\n=== Felt color gradient (left to right at 25% height) ===');
  for (let pct = 10; pct <= 90; pct += 10) {
    const p = getPixel(Math.round(W * pct/100), Math.round(H * 0.25));
    console.log(`  ${pct}%: rgb(${p.r},${p.g},${p.b}) = #${p.r.toString(16).padStart(2,'0')}${p.g.toString(16).padStart(2,'0')}${p.b.toString(16).padStart(2,'0')}`);
  }

  // Card back color (sample from facedown card area)
  console.log('\n=== Pixel samples at specific points ===');
  const samples = [
    { name: 'Top-left corner', x: 0.05, y: 0.13 },
    { name: 'Yellow header text area', x: 0.5, y: 0.08 },
    { name: 'Felt very center', x: 0.5, y: 0.22 },
    { name: 'Pot text bg', x: 0.42, y: 0.20 },
    { name: 'Below table', x: 0.5, y: 0.38 },
    { name: 'Right side bg', x: 0.95, y: 0.25 },
  ];
  for (const s of samples) {
    const p = getPixel(Math.round(W * s.x), Math.round(H * s.y));
    console.log(`  ${s.name}: rgb(${p.r},${p.g},${p.b}) = #${p.r.toString(16).padStart(2,'0')}${p.g.toString(16).padStart(2,'0')}${p.b.toString(16).padStart(2,'0')}`);
  }
}

main().catch(console.error);
