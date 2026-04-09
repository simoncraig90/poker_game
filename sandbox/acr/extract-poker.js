const asar = require('@electron/asar');
const fs = require('fs');
const path = require('path');
const targets = [
  'involved/poker/app.js',
  'involved/poker/lib/internal/internal.common.js',
];
fs.mkdirSync('key-files', { recursive: true });
for (const t of targets) {
  try {
    const buf = asar.extractFile('extracted/app/resources/app.asar', t);
    const out = path.join('key-files', t.replace(/\//g, '_'));
    fs.writeFileSync(out, buf);
    console.log(`extracted ${t} (${buf.length} bytes)`);
  } catch (e) {
    console.log(`MISS: ${t} -- ${e.message}`);
  }
}
