"""
OCR-based screen click. Finds text on screen and clicks its center.
Usage: python scripts/ocr-click.py "CASH GAME"
"""
import sys
import time
import ctypes
import numpy as np
from PIL import Image, ImageEnhance
import mss
import easyocr

target = sys.argv[1] if len(sys.argv) > 1 else "CASH GAME"

# Screenshot
with mss.mss() as sct:
    monitor = sct.monitors[1]
    shot = sct.grab(monitor)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

# Scan the top 400px where tabs would be
crop = img.crop((0, 150, img.width // 2, 400))
crop2 = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
crop2 = ImageEnhance.Contrast(crop2).enhance(2.0)

reader = easyocr.Reader(['en'], gpu=False, verbose=False)
results = reader.readtext(np.array(crop2), paragraph=False)

clicked = False
for (bbox, text, conf) in results:
    if target.upper() in text.upper() and conf > 0.5:
        cx = int((bbox[0][0] + bbox[2][0]) / 2) / 2  # undo 2x
        cy = int((bbox[0][1] + bbox[2][1]) / 2) / 2 + 150  # undo 2x + offset
        print(f"Found '{text}' at ({cx:.0f},{cy:.0f}) conf={conf:.2f}")

        # Click
        ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
        time.sleep(0.2)
        ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)  # down
        ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)  # up
        print(f"Clicked '{target}' at ({cx:.0f},{cy:.0f})")
        clicked = True
        break

if not clicked:
    print(f"'{target}' not found on screen")
    sys.exit(1)
