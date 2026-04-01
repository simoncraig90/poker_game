"""
Continuous PokerStars table capture.
Captures frames every 2 seconds, saves them for training data.
Press Ctrl+C to stop.
"""

import mss
import cv2
import numpy as np
import time
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "captures", "training")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def capture_screen():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


def find_table(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([35, 80, 60])
    upper = np.array([75, 255, 200])
    mask = cv2.inRange(hsv, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < frame.shape[0] * frame.shape[1] * 0.03:
        return None
    x, y, w, h = cv2.boundingRect(largest)
    return (x, y, w, h)


def main():
    print("Continuous PokerStars Capture")
    print("Saving to:", OUTPUT_DIR)
    print("Press Ctrl+C to stop.")
    print()

    count = 0
    while True:
        frame = capture_screen()
        region = find_table(frame)

        if region:
            x, y, w, h = region
            pad = 50
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(frame.shape[1], x + w + pad)
            y2 = min(frame.shape[0], y + h + pad * 3)
            table = frame[y1:y2, x1:x2]

            ts = int(time.time() * 1000)
            path = os.path.join(OUTPUT_DIR, f"frame_{ts}.png")
            cv2.imwrite(path, table)
            count += 1
            print(f"[{count}] Captured {w}x{h} table -> {path}")
        else:
            print(f"[{count}] No table found, skipping...")

        time.sleep(0.2)


if __name__ == "__main__":
    main()
