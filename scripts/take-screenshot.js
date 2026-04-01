const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 400, height: 740, deviceScaleFactor: 2 });
  
  await page.goto('http://localhost:9100', { waitUntil: 'networkidle0', timeout: 10000 });
  await new Promise(r => setTimeout(r, 1000));
  
  const outputPath = process.argv[2] || path.join(__dirname, '..', 'vision', 'captures', 'ps_bg_test.png');
  await page.screenshot({ path: outputPath, type: 'png' });
  console.log('Screenshot saved to', outputPath);
  
  await browser.close();
})();
