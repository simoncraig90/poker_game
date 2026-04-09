const asar = require('@electron/asar');
const list = asar.listPackage('extracted/app/resources/app.asar');
const bigJs = list.filter(p => p.endsWith('.js') && !p.includes('node_modules'));
const sized = bigJs.map(p => {
  try {
    const norm = p.replace(/^\\/, '').replace(/\\/g, '/');
    const size = asar.extractFile('extracted/app/resources/app.asar', norm).length;
    return [size, p];
  } catch(e) { return [-1, p + ' [' + e.message + ']']; }
}).sort((a,b) => b[0]-a[0]);
console.log('Top 30 largest non-node_modules JS files:');
sized.slice(0, 30).forEach(([s, p]) => console.log(`  ${s.toString().padStart(10)} ${p}`));
