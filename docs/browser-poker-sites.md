# Browser-Based Poker Sites — Research

## Real Money Sites with Browser Play (No Download)

### Tier 1: Major Networks (Confirmed Browser Play)

| Site | Browser Play | Client Type | Detection Risk | Notes |
|---|---|---|---|---|
| **PokerStars** | ✅ Full | Cocos2d Canvas | LOW (browser sandbox) | All formats via instant play. UK licensed. We have full analysis. |
| **888poker** | ✅ Full | HTML5 | LOW | Instant play at 888poker.com/desktop/no-download/. UK licensed. Soft games. |
| **PartyPoker** | ✅ Partial | HTML5 + WebGL | LOW | Browser play available. Anonymous tables in some formats. |
| **GGPoker** | ❌ Desktop only | Adobe AIR + native DLLs | HIGH (full spyware) | No browser version. Heavy anti-cheat (analyzed). |
| **WPN/ACR** | ❌ Desktop only | Native | HIGH | No browser version. |

### Tier 2: Smaller Sites (Browser Play)

| Site | Browser Play | Real Money | Notes |
|---|---|---|---|
| **CoinPoker** | ✅ | Yes (crypto) | Blockchain-based, browser play |
| **ClubGG** | ✅ Partial | Club-based | Mobile-first with browser wrapper |
| **PPPoker** | ❌ | Club-based | Mobile/desktop only |
| **Natural8** | ❌ | Yes | GGNetwork skin, same desktop client |
| **iPoker Network** | ✅ Partial | Yes | Various skins (Betfair, Paddy Power, etc.) |
| **Unibet** | ✅ | Yes | Own network, browser play available |

### Tier 3: Free/Social (Browser Only)

| Site | URL | Notes |
|---|---|---|
| Replay Poker | replaypoker.com | Free, large community |
| Poker Now | pokernow.com | Free, private games with friends |
| WSOP Free Play | playwsop.com | Free, WSOP branded |
| Unmasked Poker | unmasked.poker | Free, no registration |
| Poker Patio | pokerpatio.com | Free, play vs bots |

---

## Priority Targets for Multi-Skin Client

### 1. PokerStars (DONE — skin in progress)
- Browser client: Cocos2d Canvas
- SSIM: 0.596 portrait, 0.565 landscape
- 52 card captures, YOLO trained
- PS config extracted (base.json)

### 2. 888poker (NEXT)
- Browser client: HTML5
- URL: 888poker.com/desktop/no-download/
- Likely uses DOM rendering (not canvas) — easier to match
- UK licensed, soft games, good for testing

### 3. PartyPoker
- Browser play available
- Anonymous tables — harder for opponents to profile the bot
- Good for bot testing (less player tracking)

### 4. CoinPoker
- Crypto-only, less regulated
- Browser play
- Lower detection investment likely

---

## Detection Comparison: Browser vs Desktop

| Capability | Desktop Client | Browser Client |
|---|---|---|
| Read running processes | ✅ Full access | ❌ Sandboxed |
| Scan window titles | ✅ EnumWindows | ❌ Cannot |
| Read registry | ✅ Full access | ❌ Cannot |
| Hardware fingerprint | ✅ DeviceIoControl | ⚠️ Canvas/WebGL only |
| Track mouse globally | ✅ GetCursorPos | ❌ Only within page |
| Screenshot other windows | ✅ PrintWindow | ❌ Cannot |
| Scan installed software | ✅ Registry + files | ❌ Cannot |
| Detect VM | ✅ Hardware IDs | ⚠️ Limited (user-agent) |
| Monitor network | ✅ WSA APIs | ❌ Cannot |
| **Server-side behavioral** | ✅ Same | ✅ Same |

**Bottom line:** Browser clients can ONLY detect bots through server-side behavioral analysis. Our humanization score of 46 (out of 100, lower = more human) should pass.

---

## Sources
- [888poker No Download](https://www.888poker.com/desktop/no-download/)
- [PokerStars Browser Instant Play](https://rakerace.com/news/poker-rooms/2025/07/24/pokerstars-expands-browser-based-instant-play-across-nearly-all-game-formats)
- [No Download Poker Sites](https://www.pokerlistings.com/no-download-poker-rooms)
- [Best UK Poker Sites 2026](https://www.pokernews.com/sites/uk.htm)
- [PokerScout UK Rankings](https://www.pokerscout.com/uk/)
