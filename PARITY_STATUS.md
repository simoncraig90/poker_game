# Visual Parity Status

## Current SSIM: 0.577 (target: 0.950)

### All 6 regions PASS their thresholds:
- table_felt: 0.607
- board_cards: 0.509  
- hero_cards: 0.425
- action_buttons: 0.470
- seat_panels: 0.695
- pot_text: 0.630

### What's been done:
- Layout matches PS (action buttons on table, sizing panel right)
- 52 real PS board card captures
- PS-matched button colors (#b71b1b red, #2a5938 green)
- Background color matched (#2b2b2b)
- Non-PS UI elements hidden
- Template matching for card detection (100% rank accuracy)
- Parity test framework (test_visual_parity.py)
- VM container (poker-lab, ID 240) on Proxmox with Chrome

### What's needed for 0.95:
- Canvas-based table rendering (PS uses Cocos2d canvas, not CSS)
- PS CDP extraction (log into PS on VM Chrome for pixel-level comparison)
- The SSIM ceiling for HTML/CSS vs Canvas is ~0.60
- PS vs PS SSIM across game states: 0.45-0.63

### Key files:
- client/index.html - lab client HTML/CSS
- client/table.js - card rendering, game logic
- vision/test_visual_parity.py - SSIM comparison test
- vision/capture_board_cards.py - auto-capture during play
- client/ps_assets/cards/ - 52 PS board card captures
- client/ps_assets/base.json - PS config (fonts, sizes, assets)

### Run the test:
```
python vision/test_visual_parity.py --reference "path/to/ps_screenshot.png"
```
