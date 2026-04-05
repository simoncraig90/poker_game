# Next Session Priorities

## Immediate: Unibet Skin
- Simon has Unibet account (Skurj_uni41), table open in Chrome
- Need screenshot of active table with cards dealt
- Extract layout: card positions, button styles, felt color, seat panels
- Build Unibet skin for lab client (client/skin-unibet/)
- Unibet is pure browser, zero native anti-cheat — safest target

## Advisor
- Card detection: 100% rank accuracy via template matching (6/6 screenshots)
- facing_bet: fixed (checks call amount, chips, button text)
- Position: fixed (seat-based mapping from dealer button)
- Overlay: needs testing live — start Chrome, run advisor, play PS

## Universal Bot
- Built: vision/universal_bot.py
- YOLO retrained on 5 felt colors (mAP50 0.991)
- Detection score: 59 raw, 46 humanized (target was <60)
- Tested dry-run on random skin — detects table and cards
- Needs: fix overlapping hero card detection (gets 1 of 2)

## Visual Parity (PS)
- CSS: 0.596 SSIM portrait, 0.565 landscape
- CSS optimizer converged at ~0.56-0.60 ceiling
- Canvas renderer built but needs calibration
- Need PS CDP access for card extraction at display resolution

## Key Files
- vision/universal_bot.py — the bot
- vision/universal_reader.py — table detection for any client
- vision/test_visual_parity.py — SSIM comparison
- vision/optimize_parity.py — automated CSS search
- vision/capture_board_cards.py — card capture during play
- client/skin-generator.js — random skin generator
- docs/bot-detection-research.md — detection methods
- docs/client-analysis-framework.md — RE methodology + GGPoker analysis
- docs/browser-poker-sites.md — which sites have browser play

## VM Setup
- Proxmox container 240 (poker-lab): Chrome + Node + Python installed
- Lab server runs at 192.168.0.130:9100
- CDP accessible via socat port 9223
