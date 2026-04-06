/**
 * CDP WebSocket bridge for Unibet.
 * Connects to the Relax Gaming iframe and forwards game state messages to stdout.
 */
const CDP = require('chrome-remote-interface');
const port = parseInt(process.argv[2]) || 9222;

async function main() {
  const targets = await CDP.List({ port });
  const relaxFrame = targets.find(t => t.type === 'iframe' && t.url.includes('relaxg.com'));
  if (!relaxFrame) {
    console.error('NO_IFRAME');
    process.exit(1);
  }

  const client = await CDP({ target: relaxFrame.id, port });
  const { Network } = client;
  await Network.enable();

  Network.on('webSocketFrameReceived', (params) => {
    const data = params.response.payloadData || '';
    if (data.includes('payLoad') && data.includes('hid')) {
      console.log('WS:' + data);
    }
  });

  // Keep alive
  setInterval(() => {}, 1000);
}

main().catch(e => {
  console.error('ERR:' + e.message);
  process.exit(1);
});
