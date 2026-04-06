"""
Standalone overlay window for poker advisor.
Receives JSON updates via stdin. Stays always-on-top.
Run as separate process to avoid Tkinter blocking.
"""
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import tkinter as tk
import sys
import json
import threading
import queue

msg_queue = queue.Queue()


def stdin_reader():
    """Read stdin in a thread (blocking reads don't stall Tkinter)."""
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                msg_queue.put(json.loads(line))
            except Exception:
                pass


def main():
    root = tk.Tk()
    root.title("Poker Advisor")
    root.configure(bg="#1a1a2e")
    screen_w = root.winfo_screenwidth()
    root.geometry(f"450x240+{screen_w - 480}+30")
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.9)
    root.resizable(False, False)

    # Dragging
    drag = {"x": 0, "y": 0}
    root.bind("<Button-1>", lambda e: drag.update(x=e.x, y=e.y))
    root.bind("<B1-Motion>", lambda e: root.geometry(
        f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}"))
    root.bind("<Button-3>", lambda e: root.destroy())

    frame = tk.Frame(root, bg="#1a1a2e", padx=10, pady=8)
    frame.pack(fill=tk.BOTH, expand=True)

    title = tk.Label(frame, text="POKER ADVISOR",
                     font=("Consolas", 11, "bold"), fg="#666", bg="#1a1a2e")
    title.pack(anchor="w")

    cards_var = tk.StringVar(value="Waiting for cards...")
    tk.Label(frame, textvariable=cards_var,
             font=("Consolas", 16, "bold"), fg="#e0e0e0", bg="#1a1a2e").pack(anchor="w", pady=(5, 0))

    info_var = tk.StringVar(value="")
    tk.Label(frame, textvariable=info_var,
             font=("Consolas", 13), fg="#4ecca3", bg="#1a1a2e", justify="left").pack(anchor="w")

    rec_var = tk.StringVar(value="")
    rec_label = tk.Label(frame, textvariable=rec_var,
                         font=("Consolas", 14, "bold"), fg="#ffd700",
                         bg="#1a1a2e", padx=8, pady=4)
    rec_label.pack(anchor="w", pady=(5, 0), fill="x")

    # Start stdin reader thread
    t = threading.Thread(target=stdin_reader, daemon=True)
    t.start()

    def check_queue():
        while not msg_queue.empty():
            try:
                data = msg_queue.get_nowait()
                cards_var.set(data.get("cards", ""))
                info_var.set(data.get("info", ""))
                rec_var.set(data.get("rec", ""))
                rec_label.config(
                    bg=data.get("rec_bg", "#1a1a2e"),
                    fg=data.get("rec_fg", "#ffd700"))
            except Exception:
                pass
        root.after(100, check_queue)

    root.after(100, check_queue)
    root.mainloop()


if __name__ == "__main__":
    main()
