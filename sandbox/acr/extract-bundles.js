const asar = require('@electron/asar');
const fs = require('fs');
const path = require('path');
const targets = [
  'website-lib/coreServerHub.min.js',
  'website-lib/website-lib.min.js',
  'website-lib/temp-website-lib.min.js',
  'website-lib/websiteCommon.min.js',
  'controller/tableWindows.js',
  'controller/commonMessage.js',
  'controller/commonWindows.js',
  'rendererListener/tableWindows.js',
  'rendererListener/commonMessage.js',
  'lib/aes.js',
  'lib/atmosphere.js',
  'global.js',
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
