# CDP Background Capture Plan

## Goal

Capture all network traffic, WebSocket frames, and timing data from a live Chrome session using the Chrome DevTools Protocol (CDP) — without touching the browser window or interrupting gameplay.

## Architecture

```
Chrome (--remote-debugging-port=9222)
    ↑
    │  CDP WebSocket connection
    ↓
cdp-capture.js (Node.js, background terminal)
    │
    ├── Enables Network domain (HTTP req/res, headers, timing)
    ├── Enables Network.webSocketFrame* events (WS lifecycle + frames)
    ├── Enables Page domain (navigation events, timestamps)
    │
    └── Writes to:
        captures/YYYY-MM-DD_HHMMSS/
        ├── session.log          ← human-readable chronological log
        ├── requests.jsonl       ← one JSON object per HTTP request/response pair
        ├── websocket.jsonl      ← one JSON object per WS frame (sent + received)
        ├── ws-lifecycle.jsonl   ← WS created/closed events
        ├── timing.jsonl         ← request timing breakdowns (DNS, TLS, TTFB, etc.)
        └── session-meta.json    ← start time, Chrome version, URL, stop time
```

## Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| CDP client | `chrome-remote-interface` npm package | Mature, zero-dependency CDP client. No Playwright needed. |
| Transport | Connect to existing Chrome via WS | User plays normally; script is passive observer. |
| Output format | JSONL (one object per line) | Streamable, grep-friendly, easy to load in Python/JS later. |
| Human log | `session.log` | Plaintext, one line per event with timestamp — readable in any editor. |
| Session folder | Timestamped at script start | One folder per capture run, no collisions. |
| Graceful stop | Ctrl+C in the capture terminal | SIGINT handler flushes buffers, writes session-meta.json, closes cleanly. |

## What Gets Captured

### HTTP Requests & Responses
- URL, method, status code, headers (request + response)
- Request body (POST/PUT when available)
- Response body (optional, for JSON responses under 1MB)
- Timing breakdown: DNS, connect, TLS, send, wait (TTFB), receive

### WebSocket
- **Lifecycle**: creation URL, handshake headers, close code/reason
- **Frames**: direction (sent/received), opcode (text/binary), payload, timestamp
- Binary payloads logged as base64

### Navigation & Timing
- Page navigations (URL changes)
- DOMContentLoaded, load events

## Dependencies

```
npm init -y
npm install chrome-remote-interface
```

That's it. One dependency.

## Chrome Launch

Chrome must be started with remote debugging enabled. One-time setup:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-poker-profile"
```

Or add `--remote-debugging-port=9222` to an existing shortcut.

## Usage

```bash
# Terminal 1: Chrome (if not already running with debug port)
# (use the shortcut or command above)

# Terminal 2: Start capture
cd C:\poker-research
node scripts/cdp-capture.js

# Play poker normally in Chrome...

# When done: Ctrl+C in Terminal 2
# Output appears in captures/YYYY-MM-DD_HHMMSS/
```

## Capture Settings (configurable at top of script)

| Setting | Default | Notes |
|---------|---------|-------|
| `CDP_HOST` | `localhost` | |
| `CDP_PORT` | `9222` | Must match Chrome's `--remote-debugging-port` |
| `CAPTURE_BODIES` | `true` | Capture HTTP response bodies (JSON only, <1MB) |
| `CAPTURE_DIR` | `./captures` | Base directory for session folders |
| `MAX_BODY_SIZE` | `1048576` | Skip response bodies larger than this (bytes) |

## File Sizes (Estimates)

- 1 hour session, moderate traffic: ~5-20 MB total
- WebSocket-heavy game: ws frames dominate, ~50-100 KB/min
- Response bodies (if enabled): largest contributor, disable if disk is a concern

## Limitations

- Cannot capture traffic from other browser tabs unless they share the same target
- Binary WebSocket payloads are base64-encoded (not decoded/parsed)
- Response bodies require an extra CDP call per response; adds minor overhead
- Chrome must be launched with `--remote-debugging-port` flag
