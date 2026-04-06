/**
 * CDP WebSocket bridge for CoinPoker.
 *
 * CoinPoker uses SmartFoxServer 2X (SFS2X) binary protocol over WebSocket.
 * This bridge intercepts raw binary frames from the CoinPoker browser tab,
 * decodes the SFS2X binary format, and outputs JSON events to stdout.
 *
 * It also supports a DOM-scraping fallback mode (--dom flag) that reads
 * game state directly from the React Native Web DOM elements.
 *
 * Usage:
 *   node scripts/cdp-coinpoker-bridge.js [cdp-port] [--dom]
 *
 * Output format (one JSON per line, prefixed with "SFS:" or "DOM:"):
 *   SFS:{"event":"HOLE_CARDS","data":{...}}
 *   DOM:{"hero_cards":["Ah","Kd"],"board_cards":[],...}
 */
const CDP = require('chrome-remote-interface');

const port = parseInt(process.argv[2]) || 9222;
const domMode = process.argv.includes('--dom');

// ---------------------------------------------------------------------------
// SFS2X Binary Protocol Decoder
// ---------------------------------------------------------------------------
// SFS2X uses a custom binary serialization for SFSObject/SFSArray.
// Wire format: [header byte 0x80] [compressed flag] [payload length (4 bytes)] [payload]
// Payload is a serialized SFSObject.
//
// SFSObject binary format:
//   [type byte] per value, with type-specific encoding:
//     0x00 = NULL
//     0x01 = BOOL (1 byte)
//     0x02 = BYTE (1 byte signed)
//     0x03 = SHORT (2 bytes signed BE)
//     0x04 = INT (4 bytes signed BE)
//     0x05 = LONG (8 bytes signed BE)
//     0x06 = FLOAT (4 bytes IEEE 754 BE)
//     0x07 = DOUBLE (8 bytes IEEE 754 BE)
//     0x08 = UTF_STRING (2-byte length prefix + UTF-8 bytes)
//     0x09 = BOOL_ARRAY
//     0x0A = BYTE_ARRAY
//     0x0B = SHORT_ARRAY
//     0x0C = INT_ARRAY
//     0x0D = LONG_ARRAY
//     0x0E = FLOAT_ARRAY
//     0x0F = DOUBLE_ARRAY
//     0x10 = UTF_STRING_ARRAY
//     0x11 = SFS_ARRAY
//     0x12 = SFS_OBJECT
//     0x13 = CLASS (not used in practice)
//     0x14 = TEXT (4-byte length prefix + UTF-8)

const SFS_TYPES = {
  NULL: 0x00,
  BOOL: 0x01,
  BYTE: 0x02,
  SHORT: 0x03,
  INT: 0x04,
  LONG: 0x05,
  FLOAT: 0x06,
  DOUBLE: 0x07,
  UTF_STRING: 0x08,
  BOOL_ARRAY: 0x09,
  BYTE_ARRAY: 0x0A,
  SHORT_ARRAY: 0x0B,
  INT_ARRAY: 0x0C,
  LONG_ARRAY: 0x0D,
  FLOAT_ARRAY: 0x0E,
  DOUBLE_ARRAY: 0x0F,
  UTF_STRING_ARRAY: 0x10,
  SFS_ARRAY: 0x11,
  SFS_OBJECT: 0x12,
  CLASS: 0x13,
  TEXT: 0x14
};

class SFSDecoder {
  constructor(buffer) {
    this.buf = Buffer.from(buffer);
    this.pos = 0;
  }

  remaining() {
    return this.buf.length - this.pos;
  }

  readByte() {
    if (this.pos >= this.buf.length) throw new Error('EOF reading byte');
    return this.buf.readInt8(this.pos++);
  }

  readUByte() {
    if (this.pos >= this.buf.length) throw new Error('EOF reading ubyte');
    return this.buf.readUInt8(this.pos++);
  }

  readShort() {
    if (this.pos + 2 > this.buf.length) throw new Error('EOF reading short');
    const v = this.buf.readInt16BE(this.pos);
    this.pos += 2;
    return v;
  }

  readUShort() {
    if (this.pos + 2 > this.buf.length) throw new Error('EOF reading ushort');
    const v = this.buf.readUInt16BE(this.pos);
    this.pos += 2;
    return v;
  }

  readInt() {
    if (this.pos + 4 > this.buf.length) throw new Error('EOF reading int');
    const v = this.buf.readInt32BE(this.pos);
    this.pos += 4;
    return v;
  }

  readLong() {
    if (this.pos + 8 > this.buf.length) throw new Error('EOF reading long');
    const hi = this.buf.readInt32BE(this.pos);
    const lo = this.buf.readUInt32BE(this.pos + 4);
    this.pos += 8;
    return hi * 0x100000000 + lo;
  }

  readFloat() {
    if (this.pos + 4 > this.buf.length) throw new Error('EOF reading float');
    const v = this.buf.readFloatBE(this.pos);
    this.pos += 4;
    return v;
  }

  readDouble() {
    if (this.pos + 8 > this.buf.length) throw new Error('EOF reading double');
    const v = this.buf.readDoubleBE(this.pos);
    this.pos += 8;
    return v;
  }

  readUTF() {
    const len = this.readUShort();
    if (this.pos + len > this.buf.length) throw new Error('EOF reading utf');
    const s = this.buf.toString('utf8', this.pos, this.pos + len);
    this.pos += len;
    return s;
  }

  readText() {
    const len = this.readInt();
    if (len < 0 || this.pos + len > this.buf.length) throw new Error('EOF reading text');
    const s = this.buf.toString('utf8', this.pos, this.pos + len);
    this.pos += len;
    return s;
  }

  readValue() {
    const type = this.readUByte();
    switch (type) {
      case SFS_TYPES.NULL: return null;
      case SFS_TYPES.BOOL: return this.readByte() !== 0;
      case SFS_TYPES.BYTE: return this.readByte();
      case SFS_TYPES.SHORT: return this.readShort();
      case SFS_TYPES.INT: return this.readInt();
      case SFS_TYPES.LONG: return this.readLong();
      case SFS_TYPES.FLOAT: return Math.round(this.readFloat() * 10000) / 10000;
      case SFS_TYPES.DOUBLE: return Math.round(this.readDouble() * 10000) / 10000;
      case SFS_TYPES.UTF_STRING: return this.readUTF();
      case SFS_TYPES.TEXT: return this.readText();

      case SFS_TYPES.BOOL_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readByte() !== 0);
        return arr;
      }
      case SFS_TYPES.BYTE_ARRAY: {
        const len = this.readInt();
        if (len < 0 || this.pos + len > this.buf.length) throw new Error('EOF reading byte array');
        const arr = Array.from(this.buf.slice(this.pos, this.pos + len));
        this.pos += len;
        return arr;
      }
      case SFS_TYPES.SHORT_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readShort());
        return arr;
      }
      case SFS_TYPES.INT_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readInt());
        return arr;
      }
      case SFS_TYPES.LONG_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readLong());
        return arr;
      }
      case SFS_TYPES.FLOAT_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readFloat());
        return arr;
      }
      case SFS_TYPES.DOUBLE_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readDouble());
        return arr;
      }
      case SFS_TYPES.UTF_STRING_ARRAY: {
        const len = this.readUShort();
        const arr = [];
        for (let i = 0; i < len; i++) arr.push(this.readUTF());
        return arr;
      }
      case SFS_TYPES.SFS_ARRAY: return this.readSFSArray();
      case SFS_TYPES.SFS_OBJECT: return this.readSFSObject();

      default:
        throw new Error(`Unknown SFS type: 0x${type.toString(16)} at pos ${this.pos - 1}`);
    }
  }

  readSFSObject() {
    const count = this.readUShort();
    const obj = {};
    for (let i = 0; i < count; i++) {
      const key = this.readUTF();
      obj[key] = this.readValue();
    }
    return obj;
  }

  readSFSArray() {
    const count = this.readUShort();
    const arr = [];
    for (let i = 0; i < count; i++) {
      arr.push(this.readValue());
    }
    return arr;
  }
}

/**
 * Decode an SFS2X binary WebSocket frame.
 * Returns null if the frame is not a valid SFS2X message, or an object with
 * { controller, action, params } on success.
 */
function decodeSFSFrame(base64Data) {
  try {
    const buf = Buffer.from(base64Data, 'base64');
    if (buf.length < 6) return null;

    // SFS2X frame: header byte (0x80), compressed flag, 4-byte payload length
    const header = buf.readUInt8(0);
    if (header !== 0x80) {
      // Might be a raw SFSObject without the framing header — try direct decode
      const dec = new SFSDecoder(buf);
      try {
        const typeTag = dec.readUByte();
        if (typeTag === SFS_TYPES.SFS_OBJECT) {
          const obj = dec.readSFSObject();
          return extractEvent(obj);
        }
      } catch (_) {}
      return null;
    }

    const compressed = buf.readUInt8(1) !== 0;
    const payloadLen = buf.readInt32BE(2);
    if (payloadLen <= 0 || payloadLen > buf.length - 6) return null;

    let payload = buf.slice(6, 6 + payloadLen);

    // Decompress if needed (zlib)
    if (compressed) {
      try {
        const zlib = require('zlib');
        payload = zlib.inflateSync(payload);
      } catch (e) {
        return null;
      }
    }

    // Payload should start with SFSObject type tag
    const dec = new SFSDecoder(payload);
    const typeTag = dec.readUByte();
    if (typeTag !== SFS_TYPES.SFS_OBJECT) return null;
    const obj = dec.readSFSObject();
    return extractEvent(obj);
  } catch (e) {
    return null;
  }
}

/**
 * Extract game event info from a decoded SFS2X object.
 * SFS2X system messages have 'c' (controller), 'a' (action), 'p' (params).
 * Extension responses have controller=1, and params contain 'cmd' + 'p' (inner params).
 */
function extractEvent(obj) {
  // Standard SFS2X envelope
  const controller = obj.c !== undefined ? obj.c : null;
  const action = obj.a !== undefined ? obj.a : null;
  const params = obj.p || obj;

  // Extension response (controller 1, action 13 = ExtensionResponse)
  if (controller === 1 && params && params.cmd) {
    return {
      event: params.cmd,
      data: params.p || params,
      raw: obj
    };
  }

  // Direct TABLE_EVENT style — sometimes 'cmd' or 'evt' at top level
  if (params.cmd || params.evt || params.event) {
    return {
      event: params.cmd || params.evt || params.event,
      data: params.p || params.data || params,
      raw: obj
    };
  }

  // Return the raw decoded object for inspection
  return {
    event: '_RAW',
    data: obj,
    raw: obj
  };
}

// Table events we care about for game state
const GAME_EVENTS = new Set([
  'TABLE_INIT', 'PRE_HAND_START', 'GAME_START', 'GAME_READY',
  'HOLE_CARDS', 'DEALER_CARDS', 'USER_ACTION', 'USER_TURN',
  'POT_INFO', 'WINNER_INFO', 'CUMULATIVE_WINNER_INFO',
  'HAND_STRENGTH', 'PLAYER_INFO', 'SEAT_INFO', 'SEAT', 'TAKE_SEAT',
  'LEAVE_SEAT', 'SIT_OUT', 'USER_BALANCE',
  'SHOW_CARDS_REQUEST', 'REVEAL_CARDS_REQUEST',
  'ADVANCE_PLAYER_ACTION', 'GAME_DYNAMIC_PROPERTIES',
  'TRANSACTION_WINNINGS', 'STRADDLE',
]);

// ---------------------------------------------------------------------------
// DOM Scraping Mode
// ---------------------------------------------------------------------------
async function runDOMMode(client) {
  const { Runtime } = client;
  await Runtime.enable();

  console.error('[COIN-DOM] DOM scraping mode active. Polling every 500ms.');

  setInterval(async () => {
    try {
      const { result } = await Runtime.evaluate({
        expression: `
          (function() {
            // CoinPoker uses React Native Web — DOM elements with data-testid or
            // specific class patterns. We scrape text content from visible elements.
            var state = {
              hero_cards: [],
              board_cards: [],
              pot: '',
              players: [],
              hero_turn: false,
              actions: [],
              bets: []
            };

            // Helper: get all text from elements matching a selector
            function textsOf(sel) {
              return Array.from(document.querySelectorAll(sel)).map(function(e) {
                return e.textContent.trim();
              }).filter(Boolean);
            }

            // Helper: find elements by data-testid pattern
            function byTestId(pattern) {
              return Array.from(document.querySelectorAll('[data-testid]')).filter(function(e) {
                return e.getAttribute('data-testid').includes(pattern);
              });
            }

            // Look for card elements. React Native Web renders cards as View components.
            // Common patterns: data-testid containing "card", "hole", "community", "board"
            var cardEls = byTestId('card').concat(byTestId('hole')).concat(byTestId('board'));

            // Try to find cards from img elements (card images)
            var imgs = document.querySelectorAll('img');
            var cardImgs = [];
            imgs.forEach(function(img) {
              var src = img.src || img.getAttribute('src') || '';
              // Card images typically have rank and suit in filename or alt text
              if (/card|suit|rank|[2-9tjqka][cdhs]/i.test(src) || /[2-9tjqka][cdhs]/i.test(img.alt || '')) {
                cardImgs.push({
                  src: src,
                  alt: img.alt || '',
                  rect: img.getBoundingClientRect()
                });
              }
            });
            state._cardImgs = cardImgs.length;

            // Look for pot amount — often in a div near center of table
            var allText = document.body.innerText;
            var potMatch = allText.match(/(?:pot|total)[:\\s]*\\$?([\\d,.]+)/i);
            if (potMatch) state.pot = potMatch[1];

            // Look for action buttons (Check, Call, Fold, Raise, All-In)
            var buttons = Array.from(document.querySelectorAll('[role="button"]'));
            buttons.forEach(function(btn) {
              var text = btn.textContent.trim();
              if (/^(fold|check|call|raise|bet|all.in)/i.test(text)) {
                state.actions.push(text);
              }
            });
            // Also check Pressable/TouchableOpacity elements
            var pressables = document.querySelectorAll('[data-testid*="action"], [data-testid*="btn"], [data-testid*="button"]');
            pressables.forEach(function(p) {
              var text = p.textContent.trim();
              if (/^(fold|check|call|raise|bet|all.in)/i.test(text)) {
                if (state.actions.indexOf(text) === -1) state.actions.push(text);
              }
            });

            // hero_turn = action buttons are visible
            state.hero_turn = state.actions.length > 0;

            // Enumerate ALL data-testid values for discovery
            var testIds = [];
            document.querySelectorAll('[data-testid]').forEach(function(e) {
              var tid = e.getAttribute('data-testid');
              if (testIds.indexOf(tid) === -1) testIds.push(tid);
            });
            state._testIds = testIds.slice(0, 100);

            // Enumerate role attributes for discovery
            var roles = [];
            document.querySelectorAll('[role]').forEach(function(e) {
              var r = e.getAttribute('role');
              if (roles.indexOf(r) === -1) roles.push(r);
            });
            state._roles = roles;

            return JSON.stringify(state);
          })();
        `,
        returnByValue: true
      });
      if (result && result.value) {
        console.log('DOM:' + result.value);
      }
    } catch (e) {
      // Ignore transient errors
    }
  }, 500);
}

// ---------------------------------------------------------------------------
// WebSocket Interception Mode (primary)
// ---------------------------------------------------------------------------
async function runWSMode(client) {
  const { Network } = client;
  await Network.enable();

  console.error('[COIN-WS] WebSocket interception mode active.');

  // Track WebSocket connections
  Network.on('webSocketCreated', (params) => {
    console.error(`[COIN-WS] WebSocket created: ${params.url}`);
  });

  // Binary frames arrive as base64 in CDP
  Network.on('webSocketFrameReceived', (params) => {
    const data = params.response.payloadData || '';
    const opcode = params.response.opcode;

    // Binary frame (opcode 2) — SFS2X binary protocol
    // CDP delivers binary frame payloads as base64
    if (opcode === 2 || looksLikeBase64(data)) {
      const decoded = decodeSFSFrame(data);
      if (decoded) {
        // Filter to game-relevant events (or pass all with _RAW for discovery)
        if (decoded.event === '_RAW' || GAME_EVENTS.has(decoded.event)) {
          console.log('SFS:' + JSON.stringify(decoded));
        }
      }
    } else {
      // Text frame — might be Socket.io (lobby/chat) or JSON
      if (data.length > 2 && (data[0] === '{' || data[0] === '[')) {
        try {
          const parsed = JSON.parse(data);
          // Socket.io messages sometimes wrap game data
          if (parsed.event || parsed.cmd || parsed.type) {
            console.log('SIO:' + data);
          }
        } catch (_) {}
      }
    }
  });

  // Also intercept sent frames (for action detection)
  Network.on('webSocketFrameSent', (params) => {
    const data = params.response.payloadData || '';
    const opcode = params.response.opcode;

    if (opcode === 2 || looksLikeBase64(data)) {
      const decoded = decodeSFSFrame(data);
      if (decoded && decoded.event !== '_RAW') {
        console.log('SFS_SENT:' + JSON.stringify(decoded));
      }
    }
  });

  // Keep alive
  setInterval(() => {}, 1000);
}

function looksLikeBase64(s) {
  if (!s || s.length < 4) return false;
  return /^[A-Za-z0-9+/=]+$/.test(s.substring(0, 64));
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  const targets = await CDP.List({ port });

  // Find CoinPoker tab — look for coinpoker.com or the play.coinpoker.com page
  let target = targets.find(t =>
    t.type === 'page' && t.url && t.url.includes('coinpoker.com')
  );

  // Also check iframes (in case it loads in an iframe)
  if (!target) {
    target = targets.find(t =>
      t.type === 'iframe' && t.url && t.url.includes('coinpoker')
    );
  }

  // Fallback: any page target (user might have only one tab open)
  if (!target) {
    target = targets.find(t => t.type === 'page');
  }

  if (!target) {
    console.error('NO_TARGET: Could not find CoinPoker browser tab. Is Chrome open with --remote-debugging-port=' + port + '?');
    process.exit(1);
  }

  console.error(`[COIN] Attached to: ${target.url}`);
  const client = await CDP({ target: target.id, port });

  if (domMode) {
    await runDOMMode(client);
  } else {
    // Run both WS interception and periodic DOM scraping for maximum coverage
    await runWSMode(client);
  }
}

main().catch(e => {
  console.error('ERR:' + e.message);
  process.exit(1);
});
