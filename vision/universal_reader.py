r"""
Universal poker table reader — detects game state from ANY poker client screenshot.

Uses YOLO for element detection + template matching for card identification.
No hardcoded coordinates — everything detected dynamically.

Returns a structured game state dict that any bot strategy can consume.
"""

import cv2
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_detect import load_model, detect_elements
from card_cnn_detect import CardCNNDetector
from table_ocr import TableOCR


class UniversalReader:
    """Reads game state from any poker table screenshot."""

    def __init__(self):
        self.yolo_model = load_model()
        self.card_detector = CardCNNDetector()
        self.table_ocr = TableOCR()
        print("[UniversalReader] Loaded YOLO + card templates + OCR")

    def find_tables(self, screen_img):
        """Find all poker table regions on screen. Color-agnostic — detects any large
        saturated oval region (green, blue, red, purple felt)."""
        hsv = cv2.cvtColor(screen_img, cv2.COLOR_BGR2HSV)
        h, w = screen_img.shape[:2]
        min_area = h * w * 0.02

        tables = []

        # Try multiple color ranges to catch any felt color
        color_ranges = [
            ([25, 30, 20], [85, 255, 255]),    # green
            ([90, 30, 20], [140, 255, 255]),   # blue/teal
            ([0, 30, 20], [25, 255, 255]),     # red/orange
            ([140, 30, 20], [180, 255, 255]),  # red (wrap)
            ([0, 20, 15], [180, 255, 80]),     # dark saturated (any hue)
        ]

        combined_mask = np.zeros((h, w), dtype=np.uint8)
        for lower, upper in color_ranges:
            mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
            combined_mask = cv2.bitwise_or(combined_mask, mask)

        # Clean up mask
        kernel = np.ones((5, 5), np.uint8)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel, iterations=3)

        contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area > min_area:
                x, y, cw, ch = cv2.boundingRect(c)
                # Table should be roughly oval — check solidity
                hull = cv2.convexHull(c)
                hull_area = cv2.contourArea(hull)
                solidity = area / max(1, hull_area)
                if solidity > 0.5:  # reasonably solid shape
                    tables.append((x, y, cw, ch))

        # Sort by x position (left to right)
        tables.sort(key=lambda t: t[0])
        return tables

    def crop_table(self, screen_img, table_region):
        """Crop table with padding for elements outside the felt."""
        x, y, w, h = table_region
        pad_top = 80
        pad_side = 150
        pad_bottom = 350
        x1 = max(0, x - pad_side)
        y1 = max(0, y - pad_top)
        x2 = min(screen_img.shape[1], x + w + pad_side)
        y2 = min(screen_img.shape[0], y + h + pad_bottom)
        return screen_img[y1:y2, x1:x2], (x1, y1)

    def read_table(self, table_img):
        """
        Read full game state from a cropped table image.

        Returns dict:
            hero_cards: list of card strings e.g. ['Ah', 'Kd']
            board_cards: list of card strings e.g. ['2h', 'Qd', 'Jc']
            is_hero_turn: bool
            buttons: list of {action, cx, cy, w, h} (screen coords within table_img)
            pot: estimated pot (if OCR available)
            num_opponents: int
            dealer_seat: int or None
        """
        elements = detect_elements(table_img, conf=0.3)
        th, tw = table_img.shape[:2]

        # Card identification
        hero_boxes = elements.get("hero_card", [])
        board_boxes = elements.get("board_card", [])

        hero_cards = self.card_detector.identify_hero_from_table(table_img, hero_boxes)
        board_cards = self.card_detector.identify_cards(table_img, board_boxes)

        # Action buttons
        action_buttons = self._detect_action_buttons(table_img, elements)

        # Hero turn detection
        is_hero_turn = len(action_buttons) > 0 or len(hero_boxes) > 0

        # Opponent count
        card_backs = elements.get("card_back", [])
        num_opponents = max(1, len(card_backs))

        # Dealer button
        dealer_buttons = elements.get("dealer_button", [])
        dealer_pos = None
        if dealer_buttons:
            db = dealer_buttons[0]
            dealer_pos = {"x": db["cx"], "y": db["cy"]}

        # OCR: pot, stacks, bets, button amounts
        ocr_info = self.table_ocr.read_table(table_img, elements)
        pot = ocr_info.get("pot")
        players = ocr_info.get("players", [])
        bets = ocr_info.get("bets", [])
        call_amount = None
        for btn in ocr_info.get("action_buttons", []):
            if btn.get("action") == "CALL" and btn.get("amount"):
                call_amount = btn["amount"]

        # Facing bet detection
        facing_bet = call_amount is not None and call_amount > 0
        if not facing_bet and bets:
            facing_bet = True

        # Position detection from dealer button
        position = self._detect_position(elements, th, tw)

        # Phase detection
        phase = "PREFLOP"
        if len(board_cards) >= 5:
            phase = "RIVER"
        elif len(board_cards) >= 4:
            phase = "TURN"
        elif len(board_cards) >= 3:
            phase = "FLOP"

        return {
            "hero_cards": hero_cards,
            "board_cards": board_cards,
            "is_hero_turn": is_hero_turn,
            "buttons": action_buttons,
            "num_opponents": num_opponents,
            "dealer_pos": dealer_pos,
            "position": position,
            "phase": phase,
            "pot": pot,
            "players": players,
            "bets": bets,
            "call_amount": call_amount,
            "facing_bet": facing_bet,
            "table_size": (tw, th),
        }

    def _detect_position(self, elements, th, tw):
        """Detect hero's position from dealer button location.
        Returns position string: BTN, SB, BB, UTG, MP, CO."""
        dealer_buttons = elements.get("dealer_button", [])
        if not dealer_buttons:
            return "BTN"  # default if no button found

        btn = dealer_buttons[0]
        bx = btn["cx"] / tw
        by = btn["cy"] / th

        # 6-max seat positions (normalized)
        seat_positions = [
            (0.50, 0.85),  # seat 0 = hero (bottom)
            (0.15, 0.65),  # seat 1 (lower-left)
            (0.15, 0.25),  # seat 2 (upper-left)
            (0.50, 0.10),  # seat 3 (top)
            (0.85, 0.25),  # seat 4 (upper-right)
            (0.85, 0.65),  # seat 5 (lower-right)
        ]

        # Find closest seat to dealer button
        best_seat = 0
        best_dist = 999
        for si, (sx, sy) in enumerate(seat_positions):
            dist = ((bx - sx) ** 2 + (by - sy) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_seat = si

        # Hero is seat 0. Calculate position relative to button.
        offset = (0 - best_seat) % 6
        pos_map = {0: "BTN", 1: "SB", 2: "BB", 3: "UTG", 4: "MP", 5: "CO"}
        return pos_map.get(offset, "BTN")

    def _detect_action_buttons(self, table_img, elements):
        """Detect and classify action buttons (Fold/Call/Check/Raise/Bet)."""
        th, tw = table_img.shape[:2]
        hsv = cv2.cvtColor(table_img, cv2.COLOR_BGR2HSV)
        buttons = []

        # Method 1: Use YOLO action_button detections
        yolo_buttons = elements.get("action_button", [])
        for btn in yolo_buttons:
            x, y, w, h = btn["x"], btn["y"], btn["w"], btn["h"]
            crop = table_img[y:y+h, x:x+w]
            action = self._classify_button(crop)
            buttons.append({
                "action": action,
                "cx": btn["cx"],
                "cy": btn["cy"],
                "x": x, "y": y, "w": w, "h": h,
            })

        # Method 2: Color-based fallback if YOLO missed buttons
        if not buttons:
            buttons = self._find_buttons_by_color(table_img)

        return buttons

    def _classify_button(self, button_crop):
        """Classify a button crop as FOLD, CALL, CHECK, RAISE, or BET."""
        hsv = cv2.cvtColor(button_crop, cv2.COLOR_BGR2HSV)

        # Red = Fold or Raise
        red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
        red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
        red_pct = (cv2.countNonZero(red1) + cv2.countNonZero(red2)) / max(1, hsv[:,:,0].size)

        # Green = Check or Call
        green = cv2.inRange(hsv, np.array([30, 50, 50]), np.array([90, 255, 200]))
        green_pct = cv2.countNonZero(green) / max(1, hsv[:,:,0].size)

        if green_pct > 0.15:
            # Green button — Check or Call
            # Check has less white text (shorter word), Call has "$" amount
            gray = cv2.cvtColor(button_crop, cv2.COLOR_BGR2GRAY)
            white_pct = np.sum(gray > 200) / max(1, gray.size)
            return "CALL" if white_pct > 0.12 else "CHECK"
        elif red_pct > 0.15:
            # Red button — Fold or Raise
            # Fold is usually leftmost/smallest, Raise has "$" amount
            h, w = button_crop.shape[:2]
            gray = cv2.cvtColor(button_crop, cv2.COLOR_BGR2GRAY)
            white_pct = np.sum(gray > 200) / max(1, gray.size)
            return "RAISE" if white_pct > 0.15 else "FOLD"
        else:
            return "UNKNOWN"

    def _find_buttons_by_color(self, table_img):
        """Fallback: find buttons by scanning for colored rectangles."""
        h, w = table_img.shape[:2]
        # Scan bottom 40% of table for buttons
        scan_region = table_img[int(h * 0.60):, :]
        scan_h, scan_w = scan_region.shape[:2]
        y_offset = int(h * 0.60)

        hsv = cv2.cvtColor(scan_region, cv2.COLOR_BGR2HSV)
        buttons = []

        # Red buttons
        red1 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([15, 255, 255]))
        red2 = cv2.inRange(hsv, np.array([155, 60, 60]), np.array([180, 255, 255]))
        red = cv2.bitwise_or(red1, red2)
        contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            if cv2.contourArea(c) > 500 and bw > 30 and bh > 15:
                crop = scan_region[y:y+bh, x:x+bw]
                action = self._classify_button(crop)
                buttons.append({
                    "action": action,
                    "cx": x + bw // 2,
                    "cy": y_offset + y + bh // 2,
                    "x": x, "y": y_offset + y, "w": bw, "h": bh,
                })

        # Green buttons
        green = cv2.inRange(hsv, np.array([30, 50, 50]), np.array([90, 255, 200]))
        contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            if cv2.contourArea(c) > 500 and bw > 30 and bh > 15:
                crop = scan_region[y:y+bh, x:x+bw]
                action = self._classify_button(crop)
                buttons.append({
                    "action": action,
                    "cx": x + bw // 2,
                    "cy": y_offset + y + bh // 2,
                    "x": x, "y": y_offset + y, "w": bw, "h": bh,
                })

        buttons.sort(key=lambda b: b["cx"])
        return buttons


if __name__ == "__main__":
    """Test: read game state from a PS screenshot."""
    import sys

    reader = UniversalReader()

    test_files = [
        "C:/Users/Simon/OneDrive/Pictures/Screenshots/Screenshot 2026-04-05 021715.png",
        "C:/Users/Simon/OneDrive/Pictures/Screenshots/Screenshot 2026-04-04 032851.png",
    ]

    for f in test_files:
        img = cv2.imread(f)
        if img is None:
            continue

        tables = reader.find_tables(img)
        print(f"\n{Path(f).name}: {len(tables)} table(s) found")

        for i, region in enumerate(tables):
            table_img, offset = reader.crop_table(img, region)
            state = reader.read_table(table_img)

            print(f"  Table {i}:")
            print(f"    Hero: {state['hero_cards']}")
            print(f"    Board: {state['board_cards']}")
            print(f"    Turn: {state['is_hero_turn']}")
            print(f"    Buttons: {[b['action'] for b in state['buttons']]}")
            print(f"    Opponents: {state['num_opponents']}")
