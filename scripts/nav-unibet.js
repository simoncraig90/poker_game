// Navigate the existing about:blank tab to Unibet poker, wait for load.
const CDP = require('chrome-remote-interface');

(async () => {
  let client;
  try {
    const targets = await CDP.List({ port: 9222 });
    const tab = targets.find(t => t.type === 'page');
    if (!tab) { console.error('no page tab'); process.exit(2); }
    client = await CDP({ target: tab.id, port: 9222 });
    await client.Page.enable();
    await client.Page.navigate({ url: 'https://www.unibet.co.uk/play/pokerwebclient#playforreal' });
    await new Promise(r => setTimeout(r, 6000));
    const { result } = await client.Runtime.evaluate({ expression: 'location.href' });
    console.log('url:', result.value);
    const { result: title } = await client.Runtime.evaluate({ expression: 'document.title' });
    console.log('title:', title.value);
  } catch (e) {
    console.error('error:', e.message);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
  process.exit(0);
})();
