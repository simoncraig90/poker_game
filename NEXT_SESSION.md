# Next Session: Unibet Advisor

## What Works
- YOLO: hero_card + board_card detection at 99.5% mAP
- OCR rank on YOLO crop (NOT tight crop): 100% — ranks always correct
- Board card suits: 100% via template matching
- Hero red/black: 100% via color detection
- Facing bet: orange CALL button color detection (red>3000 + green>3000 = buttons visible, orange>2000 = CALL)
- CHECK override: when facing_bet=False and chart says FOLD, show CHECK instead
- Overlay: subprocess, stays on top
- Hero card locking: prevents flickering
- 301 tight crop training samples extracted

## What Doesn't Work
- Hero suit detection: ~80-90%, confuses h/d and c/s
- Position detection: always defaults to "MP"
- Facing bet timing: sometimes detects hero_turn=True when buttons aren't fully visible

## Hero Suit Detection — Approaches Tried and Failed
1. CNN on full YOLO crop — noisy background confuses it
2. CNN on tight crop — tight box sometimes cuts off rank/suit
3. Template matching — board templates don't match dark hero backgrounds
4. Contour analysis — too much noise from surrounding content
5. Edge matching — edges are noisy
6. Pixel sampling at fixed offsets — YOLO boxes aren't consistent
7. Grid sampling with confidence — high confidence wrong answers
8. CNN on 16x16 suit center samples — inconsistent sample positions

## What SHOULD Work (Not Yet Tried)
- **Color-based binarization**: extract ONLY the suit-colored pixels (red for h/d, dark-on-lighter for c/s), then template match the isolated shape
- **OCR the suit symbol**: train EasyOCR or Tesseract to read suit Unicode characters
- **Larger CNN with more data**: 301 crops may still not be enough — need 500+ with manual verification
- **Per-card template matching**: capture ALL 52 cards as templates from board cards (23/52 done), for hero use rank+color to narrow to 2 candidates, then match

## Key Insight
The tight_box extraction BREAKS rank detection (6→5, 8→3) because it crops too aggressively. Use YOLO crop for rank, tight crop only for suit.

## Architecture (current)
```
Screen → find_table → crop_table → YOLO → hero_card boxes
  → YOLO crop → OCR rank (5x scale)
  → tight crop → CNN suit (4-class)
  → color sanity check (red/black override)
  → facing bet (button color detection)
  → preflop chart + equity
  → subprocess overlay
```

## Data Available
- 301 tight crops in vision/card_crops_unibet/tight_labeled/
- 120 live frames in vision/captures/unibet/live_*.png
- 30 crosscheck frames in vision/captures/xcheck_*.png
- 7 verified test images with known ground truth

## Debt: $3 to Simon

## IMPORTANT
- DO NOT launch advisor live until suit detection is verified on NEW unseen images
- Kill ALL python processes: taskkill /F /IM python.exe
- Tight box BREAKS rank detection — only use for suit, not rank
