# Unibet VM build — auto-clicker test environment

End state: a Win11 VM that boots, connects to a UK VPN exit, runs Chrome with the existing Unibet bridge stack, and lets the host play CoinPoker simultaneously without focus or cursor interference.

This is the **first** Unibet VM. Unlike the CoinPoker VM (which exists for fingerprint isolation and parallel-table scaling), the Unibet VM exists primarily to **solve the canvas focus problem** that has blocked 8 host-side auto-click approaches.

---

## Why a VM solves the Unibet canvas focus problem

The host-side auto-clicker (`vision/auto_player.py`) uses `SetCursorPos` + `mouse_event` to click into the Unibet Emscripten canvas. Every approach tried on the host has failed because:

1. The user is using the cursor for other things → the click steals it
2. The Unibet canvas needs keyboard focus first → the focus-save-restore window is fragile
3. Chrome devtools open / closed changes coordinate math → coordinates drift
4. DPI scaling differs between Windows zoom levels → coordinates drift
5. Multi-monitor setups confuse `SetCursorPos` with absolute vs relative coords
6. Background tabs lose focus when Windows reclaims them
7. CDP `Input.dispatchMouseEvent` doesn't fire the right canvas events
8. JS injection of synthetic events bypasses Emscripten's pointer state machine

**In a VM, none of these matter:**

- The VM has its own desktop session, its own cursor, its own focus
- The host cursor never moves
- The host's foreground app stays foreground
- The VM can run a single fullscreen Chrome with no other windows competing
- Coordinate math is fixed because the VM has a single fixed resolution
- The host can play CoinPoker (or anything else) at the same time
- If Unibet bans the VM, the host's CoinPoker isn't affected

This is the architecturally clean fix. The host-side approaches have been working around a fundamental conflict — the VM removes the conflict.

---

## 1. Host hypervisor

**Recommended for tonight: Hyper-V on this Win11 Pro box.** Reasons:
- Pre-installed (just enable the feature, no download)
- Native Windows-on-Windows performance
- Enhanced session lets you copy-paste between host and guest
- Quick Create lets you spin up a Win11 image in ~10 min
- Free

**Acceptable: VirtualBox.** GPL, well-supported, easy to snapshot. Slightly slower than Hyper-V on Windows hosts.

**For sustained 24/7 operation: Proxmox on a separate machine.** Same as the CoinPoker VM doc — but this is a future move, not something to do tonight.

### Enabling Hyper-V (one-time host setup)

```powershell
# As Administrator
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
# Reboot required after enabling
```

After reboot, "Hyper-V Manager" is in the Start Menu. The "Quick Create" option can pull a fresh Win11 evaluation image in one click.

---

## 2. Guest VM specs

| Resource | Min | Recommended | Why |
|---|---|---|---|
| OS | Win 11 22H2+ | Win 11 23H2 | Edge/Chrome compatibility |
| vCPU | 2 | 4 | Chrome + CDP bridge + Python advisor |
| RAM | 6 GB | 8 GB | Chrome alone wants 4-6 GB; advisor adds 1-2 |
| Disk | 60 GB | 80 GB | Win11 baseline + Chrome + Python + frame logs |
| GPU | none | none | Canvas renders fine on software path |
| Network | virtio bridge | bridged with VPN | UK exit IP for Unibet detection |
| Display | 1366×768 | 1366×768 | Common laptop res; avoid 1920×1080 default |

Don't mirror the host's 1920×1080 — that's a fingerprint signal if Unibet's anti-bot ML compares hardware profiles across sessions.

---

## 3. Fingerprint hardening (BEFORE first Unibet login)

Same principle as the CoinPoker VM doc — the VM must look like an independent person, not a clone of the host. Per VM:

- [ ] **Random hostname** — not `DESKTOP-XXXXX` default. Pick something believable (`work-laptop`, `simon-pc`, etc.)
- [ ] **New Windows SID** — if cloned, run `sysprep /generalize` first. Never boot a clone with the parent SID.
- [ ] **Random MAC** on the virtual NIC (Hyper-V: VM Settings → Network Adapter → Advanced Features → "Generate new MAC")
- [ ] **Unique screen resolution** — 1366×768 or 1600×900, not 1920×1080
- [ ] **Timezone matches the VPN exit** — UK exit → set guest TZ to GMT/BST. Don't leave it on the host's TZ if different.
- [ ] **Locale matches the VPN exit** — en-GB for UK exits
- [ ] **No browser extensions** other than the absolute minimum (uBlock is fine, password manager extensions are NOT — they leak fingerprint)
- [ ] **No Microsoft account login** on the guest. Local account only.
- [ ] **Fresh Edge profile** for any Microsoft test, then disabled. Use Chrome only for Unibet.

---

## 4. Network — VPN routing

Unibet operates in the UK; the account should appear from a UK exit IP that matches your declared residence. Two options:

**Option A — VPN client inside the VM (recommended for tonight):**
- Install your VPN client (Mullvad, ProtonVPN, NordVPN — pick one)
- Connect to a UK exit
- Verify with `whatismyipaddress.com` from inside the VM
- Pin the exit (don't let the client roam to other countries)

**Option B — Host-side VPN, VM routed through it:** more complex, leave for Proxmox setup later.

⚠️ **Don't use the same exit as the host** — if you do CoinPoker from the host on a UK exit AND Unibet from the VM on the same UK exit, that's an IP fingerprint match between two "different" players. Pick different cities (London VPN for one, Manchester for the other).

---

## 5. Inside the VM — install the bridge stack

After Win11 is up, fingerprint hardening done, VPN connected:

### 5.1 Python + dependencies

```powershell
# Install Python 3.12 from python.org (NOT from Microsoft Store — different fingerprint)
# Then in PowerShell:
python -m pip install --upgrade pip
python -m pip install playwright pillow opencv-python numpy
playwright install chromium
```

### 5.2 Chrome with stealth profile

The Unibet auto-login flow uses a custom Chrome profile to avoid reCAPTCHA triggers. From the existing host setup:

```powershell
# Create stealth profile dir
mkdir C:\unibet-chrome-profile

# Download Chrome (NOT Edge — Unibet's bot detection has different
# heuristics for each)
# Install to default location

# Launch Chrome with the stealth profile + remote debugging port
"C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --user-data-dir=C:\unibet-chrome-profile `
  --remote-debugging-port=9222 `
  --disable-blink-features=AutomationControlled `
  --disable-features=IsolateOrigins,site-per-process
```

Save this as `C:\start-chrome.bat` for daily use.

### 5.3 Clone the poker-research repo into the VM

Two options:

**A. Fresh clone via git** (clean, recommended):
```powershell
git clone https://github.com/simoncraig90/poker_game.git C:\poker-research
cd C:\poker-research
git checkout coinpoker-strategy-fixes-20260408  # or rebuild branch when ready
```

**B. Shared folder from host** (faster iteration but fingerprint risk):
- Hyper-V: VM Settings → Integration Services → enable Guest Services
- Set up a SMB share on the host
- Mount inside the VM
- ⚠️ Shared folder paths are visible to in-VM code; if Unibet's anti-bot scrapes filesystem, this could leak that the VM isn't standalone

For tonight: use option A.

---

## 6. Wire the auto-player

The existing Unibet auto-player (`vision/auto_player.py`) should work unchanged inside the VM because all the focus/cursor problems that blocked it on the host disappear when there's no host activity to fight with.

```powershell
# Inside the VM, after Chrome is running with --remote-debugging-port=9222
cd C:\poker-research
python vision\auto_player.py
```

The auto-player connects to Chrome via CDP on port 9222, finds the Unibet tab, and starts watching for hero turns. When the existing strategy engine produces an action, the auto-player calls `SetCursorPos` + `mouse_event` to click the canvas — and because the VM has nothing else competing for the cursor or focus, it works.

---

## 7. Communication back to the host (optional, for monitoring)

If you want to watch what the VM is doing from the host, set up a one-way file sync:

- VM writes hand logs to `C:\unibet-hands.jsonl`
- Hyper-V shared folder mounts that file as a read-only path on the host
- Host can `tail -F` it from a terminal

For a tonight test, this is optional. Just RDP into the VM or use the Hyper-V console window when you want to look at the table.

---

## 8. Test plan for the first Unibet VM session

Don't go straight to real-money. Sequence:

1. **Demo / play money first.** Unibet has a play-money mode. Use it. Verify the auto-player clicks the canvas correctly. ~30 minutes minimum.
2. **Single real-money table at the lowest stake** for ~50 hands. Watch the VM via Hyper-V console. Don't multi-table yet.
3. **Two tables for ~200 hands** if the single-table run is clean.
4. **Then 4 tables for sustained operation.**

What to watch for at each step:
- **Misclicks**: action differs from intended action, or no click at all
- **Focus loss**: the canvas stops responding to mouse events
- **Detection signals**: account flagged for verification, hands marked for review, sudden table closures
- **Coordinate drift**: clicks landing on the wrong button after a while
- **Stack tracking errors**: the advisor shows a wrong stack size

If any of these fire, stop, snapshot the VM, and analyze before continuing.

---

## Evidence-based bot detection analysis (2026-04-09 live capture)

This section was added after a thorough live inspection of the Unibet poker client. Earlier versions of this doc (and earlier conversations) made claims about "Kindred behavioral ML" and "anti-cheat hooks" that were **NOT backed by direct evidence**. This section corrects that with what was actually captured from a logged-in session.

### Methodology

Chrome launched with `--remote-debugging-port=9222` (no `--disable-blink-features` — see warning-bar fix below). Auto-login via `scripts/auto-login.js`. Then:

1. `scripts/cdp-unibet-inspect.js` enumerated all loaded scripts, cookies, fingerprint state on the parent page
2. A custom CDP probe targeted the kenobi iframe directly to enumerate scripts, native API patching, and event listeners
3. `Network.webSocketFrameSent` / `webSocketFrameReceived` hooks captured 18+ seconds of unfiltered WebSocket traffic
4. JS-side checks for `WebSocket.prototype` patching, `navigator.webdriver`, fetch/XHR wrapping, suspicious window globals

### What's actually loaded — login page (parent)

| Category | Found |
|---|---|
| **Bot detection** | Google reCAPTCHA v3 (continuous scoring) + v2 fallback (visible challenge when score is low). Site key `6LcOBWMeAAAAAIRZ1kts2V1wNLAiudjVXWu841_m` |
| **CRM / tracking** (NOT anti-bot, but stable IDs) | LivePerson chat (`lpcdn.lpsnmedia.net`, cookies `LPVID`, `LPSID-60451533`); Optimove (`sdk.optimove.net`); OneTrust GDPR (`cdn-ukwest.onetrust.com`) |
| **Third-party SaaS bot detection vendors** | **NONE** — no DataDome, no PerimeterX/HUMAN, no Akamai BMP, no Kasada, no F5 Shape, no Imperva, no Distil, no Cloudflare BM, no Reblaze, no Sift/ThreatMetrix/Forter, no Arkose/FunCaptcha |

### What's actually loaded — kenobi poker client iframe

The actual game runs in a cross-origin iframe at `https://cf-mt-cdn1.relaxg.com/kenobi/clients/unibet/web/latest/`. Inside it:

| Probe | Result |
|---|---|
| Total `<script>` elements | **3** |
| Script source domains | **`cf-mt-cdn1.relaxg.com` only** (zero third-party) |
| The 3 scripts | `kenobi.js` (main client), `strophe.umd.min.js` (XMPP library), `kenobi_ems.js` (Emscripten WASM) |
| Inline `<script>` count | 0 |
| Vendor regex matches (`/datadome|kasada|akamai|.../`) | `[]` empty |
| Suspicious `window.*` keys (`/bot|detect|fingerprint|integrity|anticheat/`) | `[]` empty |
| Iframe-set cookies | `[]` empty |
| Third-party domains the iframe fetches from | `fonts.googleapis.com` (Google Fonts), `cf-mt-cdn1.relaxg.com` (own CDN), `mclient.api.relaxg.com` (Relax API), `d3nb9o6fmxqr6n.cloudfront.net` (CDN) |
| `WebSocket.prototype.send` native | true |
| `WebSocket.prototype.addEventListener` native | true |
| `WebSocket` constructor patched | false |
| `fetch` native | true |
| `XMLHttpRequest.prototype.open` native | true |
| `XMLHttpRequest.prototype.send` native | true |
| `EventTarget.prototype.addEventListener` native | true |
| `MouseEvent` constructor native | true |
| `setInterval` native | true |
| `document.onmousemove` / `document.onclick` / `document.onkeydown` | all `false` (no document-level mouse handlers via the `.on*` API) |
| `window.onfocus` / `window.onblur` / `document.onvisibilitychange` | all `false` |
| `navigator.webdriver` | undefined (stealth patch working) |

**The kenobi iframe has NO third-party bot detection libraries. None.**

### WebSocket protocol (XMPP over WS)

Captured 8+ seconds of unfiltered WebSocket traffic from the iframe. Observed message types:

```
<message xmlns='jabber:client' ... >  -- chat envelope carrying JSON payLoad
<iq type='get'><ping xmlns='urn:xmpp:ping'/></iq>  -- keep-alive (outbound)
<iq type='result'/>  -- ping response (inbound)
```

**Tags inside chat messages:** `cgupdate` (cash game), `sngupdate` (sit-and-go), tournament monitor pushes from `tourmon2@comp1.e.sw.rlx`.

**What's NOT in the protocol:**
- ❌ No client integrity / hash messages
- ❌ No fingerprint upload messages
- ❌ No bot detection challenge messages
- ❌ No periodic environment validation
- ❌ No client version check messages
- ❌ No nonces or HMAC signatures on messages
- ❌ No anti-tampering challenges

The protocol is **bare-bones XMPP**. The server gets game state messages from the client (action: fold/call/raise + amount) and sends game state updates back. **No extra bot detection layer in the WS traffic.**

### Mouse / click detection — the partial unknown

The kenobi WASM (`kenobi_ems.js`) is an Emscripten-compiled C/C++ blob. Its JS surface exposes:

```
_emscripten_set_mousedown_callback_on_thread
_emscripten_set_mouseenter_callback_on_thread
_emscripten_set_mouseleave_callback_on_thread
_emscripten_set_mousemove_callback_on_thread
_emscripten_set_mouseout_callback_on_thread
_emscripten_set_mouseover_callback_on_thread
_emscripten_set_mouseup_callback_on_thread
setCursor, fillMouseEventData, registerMouseEventCallback
```

These are how a WASM game RECEIVES mouse input from the browser — standard Emscripten boilerplate. Every WASM game has them. Their presence does NOT confirm telemetry — they're how mouse input WORKS in WebAssembly games.

**What we directly tested:** Hooked `WebSocket.prototype.send` on the iframe and watched outbound traffic during an 8-second lobby baseline. **Result: 0 outbound WebSocket frames sent.** The iframe RECEIVED game state messages but TRANSMITTED nothing. Earlier captures showed only XMPP pings (`<iq><ping/>`) on a separate cadence — those are protocol heartbeats, not mouse-correlated.

**What we couldn't test:** Bursty telemetry that only fires during active gameplay at a real-money table. The mouse-injection test hung due to a CDP `Input.dispatchMouseEvent` binding issue with iframe targets. To definitively rule out gameplay-time mouse telemetry, the same hook needs to run during a live hand and we need to compare baseline vs activity.

**Honest confidence level:** ~95% certainty there's no client-side mouse/click detection. The remaining 5% is the WASM blob being opaque from the JS surface.

### Combined risk picture

| Detection vector | Status | Severity |
|---|---|---|
| Third-party SaaS bot detection vendor (DataDome, Akamai, etc) | **CONFIRMED ABSENT** | n/a |
| JavaScript fingerprinting libraries | **CONFIRMED ABSENT** in iframe | n/a |
| WebSocket layer integrity / signing | **CONFIRMED ABSENT** | n/a |
| `WebSocket.prototype` JS-layer hooking | **CONFIRMED ABSENT** | n/a |
| reCAPTCHA v3 on login (continuous scoring) | **CONFIRMED PRESENT** | Medium — defeatable with stealth patches we have |
| reCAPTCHA v2 visible challenge fallback | **CONFIRMED PRESENT** | Low — one manual click per session |
| WASM-internal mouse telemetry | **NOT CONFIRMED** (WASM opaque, but no outbound traffic in 8s lobby baseline) | Probably none |
| Server-side action-timing analysis | **PRESUMED PRESENT** (industry standard, no direct evidence) | Medium — mitigated by humanizer |
| Server-side win-rate / pattern analysis | **PRESUMED PRESENT** (industry standard) | Low — fold-aware play distributes our actions |

### Humanizer wiring audit (vision/auto_player.py, 2026-04-09)

The auto-clicker has the humanizer module imported and partially wired:

| Defense | Status | Notes |
|---|---|---|
| Think time variance (`get_think_time`) | ✅ **WIRED** | Line 229: `humanized = get_think_time(phase, action) * 0.3`, added to 2s base + 0-1s random, capped 2-6s (1.5-4 for folds) |
| Mistake injection (`PlayVariation`) | ⚠️ **WIRED but DISABLED** | `mistake_rate=0.0` — comment: "was converting folds to calls". Disabled because it cost EV |
| Session manager (breaks) | ⚠️ **Imported, not actively used in click loop** | Should be wired for sustained sessions |
| **Click coordinate variance** | ❌ **NOT WIRED** | `sx = pt.x + int(cw * pct[0])` is exact pixel center every time |
| **Click duration variance** | ❌ **NOT WIRED** | Fixed `time.sleep(0.07)` between mousedown/mouseup |
| **Mouse path simulation** | ❌ **NOT WIRED** | `SetCursorPos(sx, sy)` is a jump, no intermediate movements |
| **Reaction time variance** | ⚠️ **Capped at 2s minimum** | Real users vary 400-2000ms; we're always slower than the slow tail |

**The bot-like click mechanics (exact pixel, fixed duration, jumped cursor) only matter if something is watching client-side mouse events.** Per the evidence above, the kenobi iframe has no JS-layer mouse observers and no fingerprinting libs, so these signals are likely **invisible to Unibet's client-side detection**. They WOULD matter if a vendor SaaS bot detection script was loaded, which it isn't.

The remaining risk is **server-side action timing**, which the wired think-time humanizer DOES address. Items NOT to skip before real-money play:

1. Verify SessionManager break logic is integrated into the main loop (currently it's instantiated but I don't see calls to it in the click path)
2. Increase reaction time variance below 2.0s minimum (sometimes act faster, like a human paying attention)
3. Consider re-enabling PlayVariation with a much smaller mistake_rate (0.5%) once we have a way to whitelist "never convert these specific shapes" — the original 2% was a blanket and that's why it cost EV

### Bottom line correction to earlier claims

The earlier "Unibet/Kindred has more anti-cheat hooks than CoinPoker" framing was wrong. Direct evidence:

- **CoinPoker** has a Unity client with IL injection detection (which is why we use a patched DLL) AND server-side ML that banned 98 accounts in Jan 2026
- **Unibet (Relax Gaming kenobi client)** has NO client-side bot detection libraries, NO WebSocket integrity layer, NO fingerprinting JS — only reCAPTCHA on login (parent page) and presumed server-side behavioral analysis on action timing

By client-side detection surface, **Unibet is materially LIGHTER than CoinPoker**, not heavier as previously claimed. The auto-clicker's main risk is server-side timing analysis, which is the same risk on every poker site.

## 9. Risks and what makes this risky

### Server-side bot detection

Unibet (Kindred) uses behavioral ML similar to CoinPoker's. Signals it looks at:
- **Action timing distributions** — too tight, too predictable, or too cross-table-correlated
- **Mouse paths** — straight-line cursor movements vs human-style arcs (the VM solves this for the host but the in-VM movements are still SetCursorPos jumps)
- **Reaction time on hero turn** — sub-200ms = bot
- **Session length and break patterns** — playing 8 hours straight at consistent intensity is human-rare

The auto-player has a humanizer (`vision/humanizer.py`) that injects timing variance and occasional mistakes. **Verify it's enabled** before any real-money play.

### Account-ban tail risk

Same as the host-side warnings: if the VM account gets flagged, the bankroll on it is recoverable only via Unibet's refund process and isn't guaranteed. Don't put more on it than you're willing to lose entirely.

### IP fingerprint collisions

If the host and VM both connect to the same VPN exit (or the same residential IP without VPN), Unibet's collusion detection will flag the two accounts as the same player. **Use different exits.**

### VM fingerprint collisions

The hardening checklist in §3 prevents the obvious fingerprint signals. The non-obvious ones (CPU model strings, GPU vendor, audio device list) are partially exposed by the browser. The browser's `navigator.*` API + WebGL fingerprinting are the biggest risk. Use a stealth Chrome profile and disable WebGL if Unibet doesn't require it.

### Conflict with the rebuild branch

The rebuild branch (`rebuild-foundation-20260408`) has the new Phase 2 / Phase 3 v0 advisor. It has NOT been live-tested. **Use the strategy-fixes branch (`coinpoker-strategy-fixes-20260408`)** for the first Unibet VM session — that's the deployed runner with today's nine stop-loss filters. Switch to the rebuild branch only after Phase 7 burn-in clears it.

---

## 10. Quick-start TL;DR for tonight

```powershell
# 1. On the host (as Administrator)
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
# Reboot

# 2. After reboot, open Hyper-V Manager → Quick Create → "Windows 11 Dev Environment"
#    (Microsoft provides a free evaluation image)

# 3. First boot: complete Windows setup with a LOCAL account, not Microsoft.
#    Set hostname to something believable. Set timezone to GMT.

# 4. Inside the guest:
#    a. Install your VPN client, connect to a UK exit (NOT the same as host)
#    b. Verify IP via whatismyipaddress.com
#    c. Install Chrome (not Edge)
#    d. Install Python 3.12 from python.org
#    e. Clone the poker-research repo
#    f. Switch to the strategy-fixes branch

# 5. Launch Chrome with stealth flags + debug port (one-time per session)
"C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --user-data-dir=C:\unibet-chrome-profile `
  --remote-debugging-port=9222 `
  --disable-blink-features=AutomationControlled

# 6. Log in to Unibet manually the first time, switch to play money

# 7. In a second PowerShell window:
cd C:\poker-research
python vision\auto_player.py

# 8. Watch the play-money table from the Hyper-V console.
#    Verify clicks land. Run for 30+ minutes before any real money.
```

Total setup time: ~60-90 minutes including Win11 install + VPN install + Python install + first manual Unibet login. After that, daily startup is a single batch file.

---

## Future: parallel Proxmox VMs

Once VM #1 has run unattended for a week with no detection signals, the same checklist + hardening applies for VM #2 on Proxmox per the existing CoinPoker VM doc. The architectural pattern: each "player" (CoinPoker host, Unibet VM #1, Unibet VM #2, ...) has its own fingerprint, its own VPN exit, its own bankroll, its own session schedule.

Cross-VM coordination (timing decorrelation, action variance) lives in the future Hive Mind controller from the existing kanban.
