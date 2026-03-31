"""
PokerStars screen capture — proof of concept.
Captures the screen, finds the PokerStars window region, and saves frames.
"""

import mss
import cv2
import numpy as np
import time
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "captures")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def capture_screen():
    """Capture the full screen and return as numpy array."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # Primary monitor
        img = sct.grab(monitor)
        frame = np.array(img)
        # MSS returns BGRA, convert to BGR for OpenCV
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        return frame


def find_pokerstars_region(frame):
    """
    Find the PokerStars table region by looking for the green felt.
    Returns (x, y, w, h) or None.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # PokerStars green felt: H=100-150, S=80-255, V=60-200
    lower_green = np.array([35, 80, 60])
    upper_green = np.array([75, 255, 200])
    mask = cv2.inRange(hsv, lower_green, upper_green)

    # Find the largest green contour
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)

    # Must be significant size (at least 5% of screen)
    screen_area = frame.shape[0] * frame.shape[1]
    if area < screen_area * 0.03:
        return None

    x, y, w, h = cv2.boundingRect(largest)
    return (x, y, w, h)


def main():
    print("PokerStars Screen Capture")
    print("=" * 40)
    print("Make sure PokerStars is visible on screen.")
    print("Press Ctrl+C to stop.")
    print()

    # Single capture for testing
    frame = capture_screen()
    print(f"Screen captured: {frame.shape[1]}x{frame.shape[0]}")

    region = find_pokerstars_region(frame)
    if region:
        x, y, w, h = region
        print(f"Found green felt at: x={x} y={y} w={w} h={h}")

        # Crop the table region with some padding
        pad = 50
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(frame.shape[1], x + w + pad)
        y2 = min(frame.shape[0], y + h + pad * 3)  # Extra padding below for action bar
        table = frame[y1:y2, x1:x2]

        # Save
        ts = int(time.time())
        path = os.path.join(OUTPUT_DIR, f"table_{ts}.png")
        cv2.imwrite(path, table)
        print(f"Saved table region to {path}")

        # Also save the full screenshot with rectangle overlay
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
        full_path = os.path.join(OUTPUT_DIR, f"full_{ts}.png")
        cv2.imwrite(full_path, frame)
        print(f"Saved full screenshot with region marked to {full_path}")
    else:
        print("Could not find PokerStars table on screen.")
        path = os.path.join(OUTPUT_DIR, f"screen_{int(time.time())}.png")
        cv2.imwrite(path, frame)
        print(f"Saved full screenshot to {path}")


if __name__ == "__main__":
    main()
