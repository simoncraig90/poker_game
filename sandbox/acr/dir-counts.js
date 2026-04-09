const asar = require('@electron/asar');
const list = asar.listPackage('extracted/app/resources/app.asar');
const counts = {};
list.filter(p => p.endsWith('.js') && !p.includes('node_modules')).forEach(p => {
  const parts = p.replace(/^\\/, '').split('\\');
  const dir = parts.slice(0, -1).join('/');
  counts[dir] = (counts[dir] || 0) + 1;
});
const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
sorted.slice(0, 30).forEach(([d, c]) => console.log(c.toString().padStart(4), d));
