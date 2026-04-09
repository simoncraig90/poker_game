const asar = require('@electron/asar');
const fs = require('fs');
const targets = [
  'involved\\web-games\\poker\\kpoker.js',
  'involved\\web-games\\poker\\observer\\observer.js',
  'involved\\web-games\\poker\\appCacheBuild.js',
  'involved\\web-games\\poker\\kpoker.html',
];
for (const t of targets) {
  try {
    const buf = asar.extractFile('extracted/app/resources/app.asar', t);
    const out = 'key-files/' + t.replace(/\\/g, '_');
    fs.writeFileSync(out, buf);
    console.log('extracted', t, buf.length, '->', out);
  } catch (e) { console.log('MISS', t, e.message); }
}
