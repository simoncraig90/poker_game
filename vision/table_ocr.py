"""
OCR module for reading text from PokerStars table regions.

Reads: pot size, player names, stack sizes, bet amounts.
Uses EasyOCR on YOLO-detected regions for speed (small crops only).

Usage:
  from table_ocr import TableOCR
  ocr = TableOCR()
  info = ocr.read_table(table_img, yolo_elements)
"""

import re
import time

import cv2
import numpy as np

_reader = None


def _get_reader():
    """Lazy-load EasyOCR reader (GPU)."""
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=True, verbose=False)
        print("[OCR] EasyOCR loaded (GPU)")
    return _reader


def _ocr_crop(image, x, y, w, h, pad=3):
    """OCR a small region of the image."""
    reader = _get_reader()
    img_h, img_w = image.shape[:2]
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img_w, x + w + pad)
    y2 = min(img_h, y + h + pad)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return []

    results = reader.readtext(crop)
    parsed = []
    for bbox, text, conf in results:
        if conf < 0.25:
            continue
        parsed.append({"text": text.strip(), "confidence": conf})
    return parsed


def _parse_dollar(text):
    """Parse a dollar amount from text. Handles OCR quirks."""
    s = text
    # Common OCR fixes
    s = re.sub(r'S([0-9O])', r'$\1', s)  # S → $
    s = re.sub(r'(?<=[\$\d.])O', '0', s)  # O → 0
    s = re.sub(r'O(?=[\d.])', '0', s)
    match = re.search(r'\$?([\d,]+\.?\d*)', s)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


class TableOCR:
    """Reads text from YOLO-detected table regions."""

    def __init__(self):
        self._last_read = {}  # cache recent reads to avoid redundant OCR
        self._cache_ttl = 1.0  # seconds

    def read_table(self, table_img, elements):
        """
        Read pot, player names/stacks, and bet amounts from the table.

        Args:
            table_img: cropped table image (BGR)
            elements: dict from yolo_detect with detected regions

        Returns:
            dict with:
              pot: float or None
              players: list of {name, stack, x, y}
              bets: list of {amount, x, y} (chips near players)
              action_buttons: list of {text, amount} (Fold/Call $X/Raise $Y)
        """
        result = {
            "pot": None,
            "players": [],
            "bets": [],
            "action_buttons": [],
        }

        if elements is None:
            return result

        # ── Pot ──────────────────────────────────────────────────────
        if elements.get("pot_text"):
            pt = elements["pot_text"][0]
            texts = _ocr_crop(table_img, pt["x"], pt["y"], pt["w"], pt["h"])
            for t in texts:
                amt = _parse_dollar(t["text"])
                if amt is not None and amt > 0:
                    result["pot"] = amt
                    break

        # ── Player panels (name + stack) ─────────────────────────────
        for panel in elements.get("player_panel", []):
            texts = _ocr_crop(table_img, panel["x"], panel["y"], panel["w"], panel["h"], pad=5)
            name = None
            stack = None
            for t in texts:
                amt = _parse_dollar(t["text"])
                if amt is not None:
                    stack = amt
                elif len(t["text"]) >= 2 and not re.match(r'^[\d$.,]+$', t["text"]):
                    # Not a number — probably a player name
                    if name is None:
                        name = t["text"]

            if name or stack:
                result["players"].append({
                    "name": name or "?",
                    "stack": stack,
                    "x": panel["cx"],
                    "y": panel["cy"],
                })

        # ── Bet chips (amounts near players) ─────────────────────────
        for chip in elements.get("chip", []):
            texts = _ocr_crop(table_img, chip["x"], chip["y"], chip["w"], chip["h"], pad=8)
            for t in texts:
                amt = _parse_dollar(t["text"])
                if amt is not None and amt > 0:
                    result["bets"].append({
                        "amount": amt,
                        "x": chip["cx"],
                        "y": chip["cy"],
                    })
                    break

        # ── Action buttons (Fold / Call $X / Raise to $Y) ────────────
        for btn in elements.get("action_button", []):
            texts = _ocr_crop(table_img, btn["x"], btn["y"], btn["w"], btn["h"], pad=3)
            for t in texts:
                text_lower = t["text"].lower()
                amt = _parse_dollar(t["text"])
                action_info = {"text": t["text"]}
                if amt is not None:
                    action_info["amount"] = amt
                if "fold" in text_lower:
                    action_info["action"] = "FOLD"
                elif "call" in text_lower:
                    action_info["action"] = "CALL"
                elif "raise" in text_lower:
                    action_info["action"] = "RAISE"
                elif "check" in text_lower:
                    action_info["action"] = "CHECK"
                elif "bet" in text_lower:
                    action_info["action"] = "BET"
                result["action_buttons"].append(action_info)
                break

        return result
