#!/usr/bin/env node
"use strict";

const CDP = require("chrome-remote-interface");
const fs = require("fs");
const path = require("path");

// ── Configuration ──────────────────────────────────────────────────────────
const CDP_HOST = process.env.CDP_HOST || "localhost";
const CDP_PORT = parseInt(process.env.CDP_PORT || "9222", 10);
const CAPTURE_BODIES = process.env.CAPTURE_BODIES !== "false"; // default true
const MAX_BODY_SIZE = parseInt(process.env.MAX_BODY_SIZE || "1048576", 10); // 1 MB
const CAPTURE_DIR = process.env.CAPTURE_DIR || path.join(__dirname, "..", "captures");

// ── Session folder ─────────────────────────────────────────────────────────
const now = new Date();
const pad = (n, w = 2) => String(n).padStart(w, "0");
const sessionName = [
  now.getFullYear(),
  pad(now.getMonth() + 1),
  pad(now.getDate()),
  "_",
  pad(now.getHours()),
  pad(now.getMinutes()),
  pad(now.getSeconds()),
].join("");
const sessionDir = path.join(CAPTURE_DIR, sessionName);
fs.mkdirSync(sessionDir, { recursive: true });

// ── Output streams ─────────────────────────────────────────────────────────
const streams = {
  log: fs.createWriteStream(path.join(sessionDir, "session.log")),
  requests: fs.createWriteStream(path.join(sessionDir, "requests.jsonl")),
  websocket: fs.createWriteStream(path.join(sessionDir, "websocket.jsonl")),
  wsLifecycle: fs.createWriteStream(path.join(sessionDir, "ws-lifecycle.jsonl")),
  timing: fs.createWriteStream(path.join(sessionDir, "timing.jsonl")),
};

const sessionMeta = {
  startTime: now.toISOString(),
  cdpHost: CDP_HOST,
  cdpPort: CDP_PORT,
  captureBodies: CAPTURE_BODIES,
  sessionDir,
  stopTime: null,
  stats: { requests: 0, responses: 0, wsFrames: 0, wsConnections: 0 },
};

// ── Helpers ────────────────────────────────────────────────────────────────
function ts() {
  return new Date().toISOString();
}

function log(msg) {
  const line = `[${ts()}] ${msg}`;
  streams.log.write(line + "\n");
  console.log(line);
}

function writeJsonl(stream, obj) {
  stream.write(JSON.stringify(obj) + "\n");
}

// Track in-flight requests so we can pair request+response
const pendingRequests = new Map();

// ── Main ───────────────────────────────────────────────────────────────────
async function main() {
  log("Connecting to Chrome CDP...");

  let client;
  try {
    client = await CDP({ host: CDP_HOST, port: CDP_PORT });
  } catch (err) {
    console.error(`\nFailed to connect to Chrome on ${CDP_HOST}:${CDP_PORT}`);
    console.error("Make sure Chrome is running with: --remote-debugging-port=9222\n");
    console.error("Launch command:");
    console.error(
      '  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\chrome-poker-profile"\n'
    );
    process.exit(1);
  }

  const { Network, Page, Runtime } = client;

  // Enable domains
  await Network.enable({
    maxPostDataSize: 65536,
    maxTotalBufferSize: 10485760,
    maxResourceBufferSize: 5242880,
  });
  await Page.enable();

  // Get initial info
  try {
    const { result } = await Runtime.evaluate({ expression: "window.location.href" });
    sessionMeta.initialUrl = result.value;
    log(`Initial URL: ${result.value}`);
  } catch (_) {
    sessionMeta.initialUrl = "unknown";
  }

  try {
    const version = await CDP.Version({ host: CDP_HOST, port: CDP_PORT });
    sessionMeta.chromeVersion = version["Browser"];
    log(`Chrome: ${version["Browser"]}`);
  } catch (_) {}

  log(`Session folder: ${sessionDir}`);
  log(`Capture bodies: ${CAPTURE_BODIES}`);
  log("Listening... (Ctrl+C to stop)\n");

  // ── HTTP Request ───────────────────────────────────────────────────────
  Network.requestWillBeSent((params) => {
    sessionMeta.stats.requests++;
    const { requestId, request, timestamp, type, initiator } = params;

    pendingRequests.set(requestId, {
      requestId,
      timestamp,
      url: request.url,
      method: request.method,
      headers: request.headers,
      postData: request.postData || null,
      type,
      initiatorType: initiator?.type || null,
    });

    const shortUrl = request.url.length > 120 ? request.url.slice(0, 120) + "..." : request.url;
    log(`→ ${request.method} ${shortUrl}`);
  });

  // ── HTTP Response ──────────────────────────────────────────────────────
  Network.responseReceived(async (params) => {
    sessionMeta.stats.responses++;
    const { requestId, response, timestamp, type } = params;
    const req = pendingRequests.get(requestId);

    const record = {
      requestId,
      timestamp,
      url: response.url,
      status: response.status,
      statusText: response.statusText,
      method: req?.method || "?",
      requestHeaders: req?.headers || {},
      responseHeaders: response.headers,
      mimeType: response.mimeType,
      type,
      postData: req?.postData || null,
      timing: response.timing || null,
      body: null,
    };

    // Log timing separately
    if (response.timing) {
      writeJsonl(streams.timing, {
        requestId,
        url: response.url,
        timestamp,
        timing: response.timing,
      });
    }

    // Capture response body for JSON responses
    if (CAPTURE_BODIES && response.mimeType && response.mimeType.includes("json")) {
      try {
        const { body, base64Encoded } = await Network.getResponseBody({ requestId });
        if (body && body.length <= MAX_BODY_SIZE) {
          record.body = base64Encoded ? `[base64:${body.length}chars]` : body;
        }
      } catch (_) {
        // Body not available (e.g., streaming, or already evicted)
      }
    }

    writeJsonl(streams.requests, record);
    pendingRequests.delete(requestId);

    log(`← ${response.status} ${response.url.length > 100 ? response.url.slice(0, 100) + "..." : response.url}`);
  });

  // ── WebSocket Created ──────────────────────────────────────────────────
  Network.webSocketCreated((params) => {
    sessionMeta.stats.wsConnections++;
    const record = { event: "created", timestamp: ts(), ...params };
    writeJsonl(streams.wsLifecycle, record);
    log(`🔌 WS OPEN  ${params.url}`);
  });

  // ── WebSocket Handshake ────────────────────────────────────────────────
  Network.webSocketHandshakeResponseReceived((params) => {
    const record = {
      event: "handshake",
      timestamp: ts(),
      requestId: params.requestId,
      status: params.response?.status,
      headers: params.response?.headers || {},
    };
    writeJsonl(streams.wsLifecycle, record);
    log(`🤝 WS HANDSHAKE ${params.requestId} status=${params.response?.status}`);
  });

  // ── WebSocket Frame Received ───────────────────────────────────────────
  Network.webSocketFrameReceived((params) => {
    sessionMeta.stats.wsFrames++;
    const { requestId, timestamp, response } = params;
    const record = {
      direction: "recv",
      requestId,
      timestamp,
      opcode: response.opcode,
      payloadLength: response.payloadData?.length || 0,
      payload: response.payloadData,
    };
    writeJsonl(streams.websocket, record);

    const preview =
      response.payloadData && response.payloadData.length > 200
        ? response.payloadData.slice(0, 200) + "..."
        : response.payloadData;
    log(`⬇ WS RECV [${requestId}] ${preview}`);
  });

  // ── WebSocket Frame Sent ───────────────────────────────────────────────
  Network.webSocketFrameSent((params) => {
    sessionMeta.stats.wsFrames++;
    const { requestId, timestamp, response } = params;
    const record = {
      direction: "sent",
      requestId,
      timestamp,
      opcode: response.opcode,
      payloadLength: response.payloadData?.length || 0,
      payload: response.payloadData,
    };
    writeJsonl(streams.websocket, record);

    const preview =
      response.payloadData && response.payloadData.length > 200
        ? response.payloadData.slice(0, 200) + "..."
        : response.payloadData;
    log(`⬆ WS SEND [${requestId}] ${preview}`);
  });

  // ── WebSocket Closed ───────────────────────────────────────────────────
  Network.webSocketClosed((params) => {
    const record = { event: "closed", timestamp: ts(), ...params };
    writeJsonl(streams.wsLifecycle, record);
    log(`❌ WS CLOSE ${params.requestId}`);
  });

  // ── WebSocket Error ────────────────────────────────────────────────────
  Network.webSocketFrameError((params) => {
    const record = { event: "error", timestamp: ts(), ...params };
    writeJsonl(streams.wsLifecycle, record);
    log(`⚠ WS ERROR ${params.requestId}: ${params.errorMessage}`);
  });

  // ── Page Navigation ────────────────────────────────────────────────────
  Page.frameNavigated((params) => {
    if (params.frame.parentId) return; // only top-level
    log(`📄 NAVIGATE ${params.frame.url}`);
  });

  Page.domContentEventFired((params) => {
    log(`📄 DOMContentLoaded @ ${params.timestamp}`);
  });

  Page.loadEventFired((params) => {
    log(`📄 Load @ ${params.timestamp}`);
  });

  // ── Graceful shutdown ──────────────────────────────────────────────────
  async function shutdown() {
    log("\nShutting down capture...");
    sessionMeta.stopTime = new Date().toISOString();

    // Write session metadata
    fs.writeFileSync(
      path.join(sessionDir, "session-meta.json"),
      JSON.stringify(sessionMeta, null, 2)
    );

    // Close streams
    for (const s of Object.values(streams)) {
      s.end();
    }

    log(`Stats: ${sessionMeta.stats.requests} requests, ${sessionMeta.stats.responses} responses, ${sessionMeta.stats.wsFrames} WS frames, ${sessionMeta.stats.wsConnections} WS connections`);
    console.log(`\nSession saved to: ${sessionDir}\n`);

    try {
      await client.close();
    } catch (_) {}

    process.exit(0);
  }

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  // Keep process alive
  await new Promise(() => {});
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
