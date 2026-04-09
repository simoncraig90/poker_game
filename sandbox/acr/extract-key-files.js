const asar = require('@electron/asar');
const fs = require('fs');
const path = require('path');

const targets = [
  'main.js',
  'package.json',
  'electronClient.js',
  'electronClientG.js',
  'global.js',
  'controller/handhistory.js',
  'controller/index.js',
  'lib/wpn_client.js',
];

const archive = 'extracted/app/resources/app.asar';
const outDir = 'key-files';
fs.mkdirSync(outDir, { recursive: true });

for (const t of targets) {
  try {
    const buf = asar.extractFile(archive, t);
    const out = path.join(outDir, t.replace(/\//g, '_'));
    fs.writeFileSync(out, buf);
    console.log(`extracted ${t} (${buf.length} bytes) -> ${out}`);
  } catch (e) {
    console.log(`MISS: ${t} (${e.message})`);
  }
}
