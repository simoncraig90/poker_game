#!/usr/bin/env node
"use strict";

/**
 * Measure PokerStars screenshot to extract layout positions.
 * Reads the PNG, finds key elements by color, outputs exact % positions.
 */

const fs = require("fs");
const path = require("path");

// Simple PNG reader — we only need pixel data
// Use the raw file and parse manually since we don't have sharp/jimp installed
// Actually, let's install a lightweight image reader

const { execSync } = require("child_process");

// Check if we have any image library
let hasSharp = false;
try { require("sharp"); hasSharp = true; } catch {}

if (!hasSharp) {
  console.log("Installing sharp for image analysis...");
  execSync("npm install sharp --no-save", { cwd: path.join(__dirname, ".."), stdio: "inherit" });
}

const sharp = require("sharp");

async function measure(imgPath) {
  const img = sharp(imgPath);
  const meta = await img.metadata();
  const { width, height } = meta;
  const raw = await img.raw().toBuffer();

  console.log(`Image: ${width}x${height}`);

  // Scan for the green felt region
  // PS felt green is approximately R:30-50 G:130-180 B:50-90
  let feltTop = height, feltBot = 0, feltLeft = width, feltRight = 0;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 3;
      const r = raw[i], g = raw[i + 1], b = raw[i + 2];

      // Green felt detection: high green, low-medium red, low blue
      if (g > 100 && g > r * 1.5 && g > b * 1.5 && r < 100 && b < 100) {
        if (y < feltTop) feltTop = y;
        if (y > feltBot) feltBot = y;
        if (x < feltLeft) feltLeft = x;
        if (x > feltRight) feltRight = x;
      }
    }
  }

  console.log(`\nFelt bounds (pixels):`);
  console.log(`  top: ${feltTop}  bottom: ${feltBot}  left: ${feltLeft}  right: ${feltRight}`);
  console.log(`  width: ${feltRight - feltLeft}  height: ${feltBot - feltTop}`);

  console.log(`\nFelt bounds (% of image):`);
  console.log(`  top: ${(feltTop / height * 100).toFixed(1)}%`);
  console.log(`  bottom: ${(feltBot / height * 100).toFixed(1)}%`);
  console.log(`  left: ${(feltLeft / width * 100).toFixed(1)}%`);
  console.log(`  right: ${(feltRight / width * 100).toFixed(1)}%`);
  console.log(`  width: ${((feltRight - feltLeft) / width * 100).toFixed(1)}%`);
  console.log(`  height: ${((feltBot - feltTop) / height * 100).toFixed(1)}%`);

  // Scan for dark seat panels (very dark pixels, R<50 G<50 B<50, in clusters)
  // Look for horizontal bands of dark pixels outside the felt
  console.log(`\nScanning for dark seat panels...`);

  const darkRows = {};
  for (let y = 0; y < height; y++) {
    let darkRun = 0;
    let darkStart = -1;
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 3;
      const r = raw[i], g = raw[i + 1], b = raw[i + 2];
      if (r < 50 && g < 50 && b < 50) {
        if (darkRun === 0) darkStart = x;
        darkRun++;
      } else {
        if (darkRun > 80 && darkRun < 200) {
          // Found a panel-width dark region
          const cx = darkStart + darkRun / 2;
          const key = `${Math.round(y / 20) * 20}`;
          if (!darkRows[key]) darkRows[key] = [];
          darkRows[key].push({ y, x: darkStart, w: darkRun, cx: Math.round(cx) });
        }
        darkRun = 0;
      }
    }
  }

  // Group dark regions into panels
  const panels = [];
  const seen = new Set();
  for (const [bandY, regions] of Object.entries(darkRows)) {
    if (regions.length > 0 && !seen.has(bandY)) {
      const avg = regions[0];
      panels.push({
        y: parseInt(bandY),
        x: avg.x,
        w: avg.w,
        yPct: (parseInt(bandY) / height * 100).toFixed(1),
        xPct: (avg.x / width * 100).toFixed(1),
      });
      seen.add(bandY);
    }
  }

  // Sort by y position
  panels.sort((a, b) => a.y - b.y);
  console.log(`\nDetected ${panels.length} dark panel regions:`);
  for (const p of panels.slice(0, 10)) {
    console.log(`  y=${p.yPct}% x=${p.xPct}% w=${p.w}px`);
  }

  // Scan for white card regions (R>220 G>220 B>220 in rectangles)
  console.log(`\nScanning for white card regions...`);
  const whiteBlobs = [];
  for (let y = 0; y < height; y += 4) {
    for (let x = 0; x < width; x += 4) {
      const i = (y * width + x) * 3;
      const r = raw[i], g = raw[i + 1], b = raw[i + 2];
      if (r > 230 && g > 230 && b > 230) {
        // Check if this is part of a card-sized white region
        let count = 0;
        for (let dy = 0; dy < 40 && y + dy < height; dy += 4) {
          for (let dx = 0; dx < 30 && x + dx < width; dx += 4) {
            const j = ((y + dy) * width + (x + dx)) * 3;
            if (raw[j] > 220 && raw[j + 1] > 220 && raw[j + 2] > 220) count++;
          }
        }
        if (count > 30) {
          whiteBlobs.push({
            y: (y / height * 100).toFixed(1),
            x: (x / width * 100).toFixed(1),
          });
          x += 40; // skip ahead
        }
      }
    }
  }

  // Deduplicate nearby blobs
  const cards = [];
  for (const b of whiteBlobs) {
    const near = cards.find(c => Math.abs(parseFloat(c.y) - parseFloat(b.y)) < 3 && Math.abs(parseFloat(c.x) - parseFloat(b.x)) < 3);
    if (!near) cards.push(b);
  }
  console.log(`\nDetected ${cards.length} card-like white regions:`);
  for (const c of cards) {
    console.log(`  y=${c.y}% x=${c.x}%`);
  }

  // Output CSS template
  console.log(`\n\n/* ═══ GENERATED CSS FROM SCREENSHOT ═══ */`);
  console.log(`/* Image: ${width}x${height} */`);
  console.log(`/* Felt: top=${(feltTop/height*100).toFixed(1)}% left=${(feltLeft/width*100).toFixed(1)}% w=${((feltRight-feltLeft)/width*100).toFixed(1)}% h=${((feltBot-feltTop)/height*100).toFixed(1)}% */`);
  console.log(``);
  console.log(`.felt {`);
  console.log(`  position: absolute;`);
  console.log(`  top: ${(feltTop/height*100).toFixed(1)}%;`);
  console.log(`  left: ${(feltLeft/width*100).toFixed(1)}%;`);
  console.log(`  width: ${((feltRight-feltLeft)/width*100).toFixed(1)}%;`);
  console.log(`  height: ${((feltBot-feltTop)/height*100).toFixed(1)}%;`);
  console.log(`  border-radius: ${Math.round(((feltRight-feltLeft)/2) / ((feltBot-feltTop)/2) * 45)}% / 48%;`);
  console.log(`}`);
}

const imgPath = process.argv[2] || path.join(__dirname, "..", "client", "ps-reference.png");
if (!fs.existsSync(imgPath)) {
  console.error("Usage: node scripts/measure-screenshot.js <path-to-ps-screenshot.png>");
  process.exit(1);
}

measure(imgPath).catch(console.error);
