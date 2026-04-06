# Browser Poker Client Protocol Research

**Date:** 2026-04-05
**Purpose:** Technical architecture survey of browser-based poker clients for anti-bot detection research

---

## 1. PokerStars Browser Client (play.pokerstars.com)

### Technology Stack
- **Rendering:** Likely Flutter Web (CanvasKit + WebGL). PokerStars is owned by Flutter Entertainment, and their browser client renders to a single `<canvas>` element — consistent with Flutter Web's approach of drawing every pixel via WebGL rather than using DOM/HTML elements. This makes traditional DOM inspection impossible.
- **Framework:** Proprietary. Uses "Rational Embedded Browser" internally. Migrated infrastructure to AWS for scalability.
- **Platform:** Available in most markets (not US). Single-table only, capped at $25 cash / $20 tournaments as of mid-2025. Multitabling not yet supported.

### Communication Protocol
- **Transport:** WebSocket (industry standard for real-time poker)
- **Message Format:** Unknown — likely binary/protobuf or proprietary encoding. PokerStars has decades of protocol engineering from their desktop client. The browser client almost certainly uses a similar binary protocol adapted for WebSocket transport.
- **Encryption:** TLS (WSS). Message payloads likely use additional encoding/serialization beyond raw JSON.

### Anti-Cheat / Integrity
- **Process monitoring:** Desktop client scans running processes. Browser client cannot do this.
- **Behavioral analysis:** Tracks "thousands of data points per second" — timing patterns, mouse movement analysis, betting patterns.
- **RTA detection:** In-house tools detect real-time assistance with >95% proactive detection rate. Catches both "always-on" and "spot" RTA usage via behavioral indicators.
- **No screen capture:** PokerStars explicitly states they cannot see your screen.
- **JS protection:** Likely uses obfuscation (possibly Jscrambler or similar) to protect client-side game logic. Anti-tamper and self-defending code transforms are standard for gaming platforms.
- **Peer review:** Game integrity investigations use triple-agent peer review system.

### CDP Interception Feasibility
- **Difficulty: HIGH.** If Flutter Web/CanvasKit: all rendering goes to a single canvas, no DOM elements to inspect for game state. WebSocket frames are visible in DevTools Network tab, but likely binary/encoded — not human-readable JSON.
- **Approach:** Would need to intercept WebSocket frames and reverse-engineer the serialization format (protobuf definitions or similar). JS source would be heavily obfuscated.
- **Risk:** PokerStars likely detects CDP automation signals (`navigator.webdriver`, ChromeDriver injected variables).

---

## 2. Ignition Poker / Bovada / Bodog Browser Client

### Technology Stack
- **Rendering:** HTML5 Canvas-based. The "Instant Play" client is engineered for modern browsers with HTML5 builds and adaptive layouts.
- **Framework:** Uses licensed **PokerAPI software** shared across Ignition, Bovada, and Bodog — all three sites run the same client codebase.
- **Platform:** Full browser play available. Zone Poker (fast-fold), cash games, and tournaments all accessible without download.
- **Software providers:** Backend integrates Bgaming (Softswiss), Real Time Gaming, Rival Gaming, Genesis Gaming, and others for casino; poker is proprietary PokerAPI.

### Communication Protocol
- **Transport:** WebSocket
- **Message Format:** **JSON-based and readable.** This is confirmed by multiple open-source Chrome extensions (IgnitionHUD, PokerEye+) that successfully intercept and parse WebSocket messages to extract hand history, player actions, cards, positions, and chip stacks in real-time.
- **Key evidence:** PokerEye+ (GitHub: vuolo/PokerEye-Plus-for-Ignition-Casino) works by injecting JS into the page that monkey-patches the WebSocket constructor to intercept all messages. The messages contain structured game state data that can be parsed without any special decoding.

### Anti-Cheat / Integrity
- **Minimal browser-side protection.** The fact that Chrome extensions can freely intercept WebSocket traffic and inject scripts suggests limited client-side anti-tamper.
- **Anonymous tables:** Ignition's primary anti-bot/anti-HUD measure is anonymous player identities — players are shown as "Player 1", "Player 2" etc., making cross-session tracking impossible through the normal client.
- **No process scanning** in browser mode (impossible from web context).
- **Server-side detection:** Likely behavioral pattern analysis on the backend.

### CDP Interception Feasibility
- **Difficulty: LOW.** Multiple working open-source tools already exist:
  - **PokerEye+** — full hand history recording via WebSocket interception
  - **IgnitionHUD** — real-time HUD stats overlay via content script injection
  - Messages are JSON, human-readable, and well-documented by the community.
- **Approach:** Monkey-patch WebSocket constructor in content script, parse incoming JSON messages for game state updates.
- **Risk:** Low detection risk in browser. Anonymous tables limit the value of tracking data.

### Key GitHub Resources
- [PokerEye+ for Ignition Casino](https://github.com/vuolo/PokerEye-Plus-for-Ignition-Casino) — Chrome extension, records stats, works on Bovada/Bodog too
- [IgnitionHUD](https://github.com/CaseRegan/IgnitionHUD) — Chrome extension, draggable stats popups

---

## 3. BetOnline Poker Browser Client (Chico Network / Connective Games)

### Technology Stack
- **Rendering:** HTML5. The browser version is described as "a copy of a PC client with good optimization and full functionality."
- **Framework:** Built on **Connective Games** platform. Software tested/certified by iTech Labs and GLI.
- **Network:** Chico Poker Network — shared player pool with TigerGaming and Sportsbetting.ag (~3,000 simultaneous connections).
- **Platform:** Instant Play browser version available. Also has dedicated HTML5 mobile version.

### Communication Protocol
- **Transport:** WebSocket (inferred from Connective Games platform architecture)
- **Message Format:** Likely proprietary binary or encoded format. Unlike Ignition, third-party HUDs do NOT work in the browser version — only in the desktop client. This suggests either:
  - (a) The browser version uses a different/encoded protocol, or
  - (b) The client actively prevents extension injection
- **Hand history:** Requires "BetOnline Card Catcher" (by Ace Poker Solutions) to grab hand histories from the desktop client. No equivalent exists for the browser version.
- **Built-in HUD:** BetOnline provides its own HUD with stats displayed next to player nicknames, suggesting they control the data pipeline.

### Anti-Cheat / Integrity
- **HUD blocking in browser:** The browser version explicitly does not support third-party HUDs or hand tracking, which may indicate active countermeasures.
- **Certified software:** iTech Labs and GLI certification means the platform meets gambling industry security standards.
- **Server-side:** Standard behavioral monitoring likely in place.

### CDP Interception Feasibility
- **Difficulty: MEDIUM.** No public tools exist for the browser client. The desktop client has third-party integrations, but the browser version appears more locked down.
- **Approach:** Would need to inspect WebSocket traffic in DevTools Network tab, determine encoding format, and reverse-engineer the message structure from the (likely obfuscated) JavaScript source.
- **Risk:** Connective Games may implement some form of client integrity checking. Certified platform suggests more security investment than average.

---

## 4. CoinPoker Browser Client (play.coinpoker.com)

### Technology Stack
- **Rendering:** Unknown specific renderer. Platform rebuilt from scratch in March 2026 using technology from **PokerBaazi** (one of India's largest poker platforms).
- **Framework:** ASP.NET backend, Bootstrap + HTML/CSS/JS frontend. The new client includes built-in HUD, PokerIntel analytics, Showdown Meter, and Skill Score.
- **Platform:** PWA (Progressive Web App) on iOS — can be installed to home screen via Safari. Full desktop clients for Windows, macOS, Android.
- **Blockchain:** RNG is blockchain-based for transparency. Funds secured with multi-auth vaults and MPC (Multi-Party Computation) technology.

### Communication Protocol
- **Transport:** WebSocket (required for real-time poker; confirmed by PWA architecture)
- **Message Format:** Unknown. No public reverse engineering or Chrome extensions exist for the new (March 2026) client. The PokerBaazi-derived platform is relatively new and hasn't been publicly analyzed.
- **Blockchain integration:** Some game data may be written to blockchain for RNG verification, but real-time gameplay almost certainly uses standard WebSocket for latency reasons.

### Anti-Cheat / Integrity
- **Built-in analytics:** The PokerIntel feature and Skill Score suggest the platform actively monitors player behavior and has infrastructure for pattern detection.
- **Enhanced security:** The March 2026 migration explicitly mentioned "enhanced security protocols" as a key upgrade.
- **Blockchain RNG:** Provides provably fair card dealing, but doesn't directly address bot detection.
- **New platform:** Being built on PokerBaazi tech (a major Indian operator) suggests enterprise-grade security, but the specific anti-tamper measures are unknown.

### CDP Interception Feasibility
- **Difficulty: MEDIUM.** No existing tools or public analysis of the new client.
- **Approach:** As a PWA, it runs in a standard browser context. WebSocket traffic is visible in DevTools. The ASP.NET/Bootstrap stack suggests server-rendered or traditional web app architecture (not canvas-only like Flutter), meaning DOM inspection may also yield useful information.
- **Advantage:** PWA architecture means it's designed to work in a standard browser — less likely to have exotic anti-debugging measures compared to native apps.
- **Risk:** Unknown. Platform is only weeks old (March 2026 launch).

---

## Summary Matrix

| Feature | PokerStars | Ignition/Bovada | BetOnline | CoinPoker |
|---|---|---|---|---|
| **Rendering** | Canvas (Flutter/WebGL) | HTML5 Canvas | HTML5 | HTML/CSS + Canvas |
| **Transport** | WebSocket (WSS) | WebSocket | WebSocket | WebSocket |
| **Message Format** | Binary/encoded | **JSON (readable)** | Proprietary/encoded | Unknown |
| **Existing Tools** | None | PokerEye+, IgnitionHUD | None (browser) | None |
| **Anti-Tamper** | Heavy (JS obfuscation) | Minimal | Moderate | Unknown (new) |
| **CDP Difficulty** | HIGH | **LOW** | MEDIUM | MEDIUM |
| **Anonymous Tables** | No | **Yes** | No | No |
| **Multi-table Browser** | No (single only) | Yes | Yes | Yes (PWA) |

## Recommendations for Detection Research

1. **Start with Ignition/Bovada** — JSON WebSocket messages, multiple open-source tools already parsing their protocol, lowest barrier to entry for traffic analysis.

2. **CoinPoker second** — PWA architecture is standard web tech, new platform means less community analysis but also potentially less hardened. ASP.NET/Bootstrap stack suggests conventional web app patterns.

3. **BetOnline third** — Connective Games platform is mature but the browser version is intentionally locked down. Desktop client protocol analysis may inform browser approach.

4. **PokerStars last** — Highest security investment, likely canvas-only rendering (no DOM), binary protocol, heavy obfuscation. Their 95% proactive bot detection rate indicates sophisticated server-side analysis that would apply regardless of client interception success.

## Key Technical Patterns

- **WebSocket monkey-patching** is the proven approach for browser poker interception (override `WebSocket` constructor to capture all `send`/`onmessage` events)
- **Protobuf** is commonly used for binary poker protocols — look for Base64-encoded messages and `.proto` definitions in obfuscated JS
- **Jscrambler** and similar tools are used by gaming platforms for JS anti-tamper — detects debugging, code modification, and monkey patching
- **CDP detection** is possible via `navigator.webdriver`, injected ChromeDriver variables, and debugger stack traces — use anti-detect browsers (e.g., Camoufox) to mitigate
