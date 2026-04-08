"""
Multi-table poker advisor overlay.

Receives JSON messages on stdin and renders a dashboard:
- Top bar: session stats (hands, P&L, bb/hr, mode)
- Per-table panels: hero/board/recommendation/timer
- Click status indicators

Auto-resizes based on number of active tables.

Message protocol (JSON per line on stdin):

  {"type": "session", "hands": 372, "profit_eur": 16.45, "bb_per_hr": 62, "mode": "AUTO"}

  {"type": "table_update", "table_id": "unibet_t1", "site": "Unibet",
   "stake": "€0.04 NL", "position": "BTN",
   "cards": "Ah Kh", "board": "Td 9s 4c",
   "phase": "FLOP", "equity": 0.78, "category": "TOP_PAIR",
   "pot": 0.20, "stack": 4.50, "facing_bet": false, "call": 0.0,
   "pot_odds": null, "rec": "BET 0.30", "rec_color": "green",
   "timer": 4.5, "last_click": "OK", "opponent": "TAG 22/18"}

  {"type": "table_remove", "table_id": "unibet_t1"}

  Legacy: any message without 'type' is treated as a single-table update
  for table_id="default" (backwards-compat with existing code).
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import os
import tkinter as tk
import sys
import json
import threading
import queue
import time

msg_queue = queue.Queue()

# Shared pause flag — auto_player.py checks for this file before clicking.
# Existence = paused, missing = running.
PAUSE_FLAG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".autoplay_pause"
)


def stdin_reader():
    """Read stdin in a thread (blocking reads don't stall Tkinter)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg_queue.put(json.loads(line))
        except Exception:
            pass


# ── Color palette ──
COLORS = {
    "bg":          "#0a0a14",
    "panel_bg":    "#1a1a2e",
    "panel_alt":   "#1f1f3a",
    "text":        "#e0e0e0",
    "text_dim":    "#888",
    "accent":      "#4ecca3",
    "gold":        "#ffd700",
    "border":      "#2a2a4a",
    "rec_action":  "#1a3a1a",  # green bg for raise/call/bet
    "rec_fold":    "#3a1a1a",  # red bg for fold
    "rec_check":   "#1a1a3a",  # blue bg for check
    "rec_text":    "#ffffff",
    "click_ok":    "#4caf50",
    "click_fail":  "#f44336",
    "timer":       "#ffaa00",
    "card_red":    "#e74c3c",
    "card_black":  "#e0e0e0",
}


def card_color(card_str):
    """Return color for a card based on suit (h/d red, c/s black)."""
    if not card_str or len(card_str) < 2:
        return COLORS["text"]
    s = card_str[1].lower()
    return COLORS["card_red"] if s in ('h', 'd') else COLORS["card_black"]


class TablePanel(tk.Frame):
    """A single table's display panel."""

    def __init__(self, parent, table_id):
        super().__init__(parent, bg=COLORS["panel_bg"], padx=8, pady=6,
                         highlightbackground=COLORS["border"], highlightthickness=1)
        self.table_id = table_id
        self.data = {}
        self.timer_remaining = 0
        self.timer_total = 0

        # Header row: site + stake + position
        header = tk.Frame(self, bg=COLORS["panel_bg"])
        header.pack(fill="x")
        self.site_var = tk.StringVar(value=table_id)
        tk.Label(header, textvariable=self.site_var,
                 font=("Consolas", 9, "bold"), fg=COLORS["text_dim"],
                 bg=COLORS["panel_bg"]).pack(side="left")
        self.position_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self.position_var,
                 font=("Consolas", 9, "bold"), fg=COLORS["accent"],
                 bg=COLORS["panel_bg"]).pack(side="right")

        # Cards row (rendered as colored letters)
        self.cards_frame = tk.Frame(self, bg=COLORS["panel_bg"])
        self.cards_frame.pack(fill="x", pady=(3, 0))

        # Equity + category line
        self.equity_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.equity_var,
                 font=("Consolas", 11), fg=COLORS["accent"],
                 bg=COLORS["panel_bg"], anchor="w").pack(fill="x", pady=(2, 0))

        # Pot/stack/odds line
        self.context_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.context_var,
                 font=("Consolas", 9), fg=COLORS["text_dim"],
                 bg=COLORS["panel_bg"], anchor="w").pack(fill="x")

        # Opponent profile line
        self.opponent_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.opponent_var,
                 font=("Consolas", 9, "italic"), fg=COLORS["text_dim"],
                 bg=COLORS["panel_bg"], anchor="w").pack(fill="x")

        # Recommendation (large, color-coded)
        self.rec_var = tk.StringVar(value="")
        self.rec_label = tk.Label(self, textvariable=self.rec_var,
                                   font=("Consolas", 14, "bold"),
                                   fg=COLORS["rec_text"], bg=COLORS["panel_bg"],
                                   padx=8, pady=4, anchor="w")
        self.rec_label.pack(fill="x", pady=(4, 2))

        # Timer + click status row
        status = tk.Frame(self, bg=COLORS["panel_bg"])
        status.pack(fill="x")
        self.timer_var = tk.StringVar(value="")
        tk.Label(status, textvariable=self.timer_var,
                 font=("Consolas", 9, "bold"), fg=COLORS["timer"],
                 bg=COLORS["panel_bg"]).pack(side="left")
        self.click_var = tk.StringVar(value="")
        self.click_label = tk.Label(status, textvariable=self.click_var,
                                     font=("Consolas", 9, "bold"),
                                     fg=COLORS["text_dim"], bg=COLORS["panel_bg"])
        self.click_label.pack(side="right")

    def update(self, msg):
        """Apply a table_update message."""
        self.data.update(msg)

        # Header
        site = msg.get("site", self.table_id)
        stake = msg.get("stake", "")
        self.site_var.set(f"{site}  {stake}".strip())
        self.position_var.set(msg.get("position", ""))

        # Cards: render each card with color
        for w in self.cards_frame.winfo_children():
            w.destroy()
        cards_str = msg.get("cards", "")
        board_str = msg.get("board", "")
        if cards_str:
            for c in cards_str.split():
                tk.Label(self.cards_frame, text=c, font=("Consolas", 14, "bold"),
                         fg=card_color(c), bg=COLORS["panel_bg"]).pack(side="left", padx=1)
        if board_str:
            tk.Label(self.cards_frame, text="|", font=("Consolas", 14, "bold"),
                     fg=COLORS["text_dim"], bg=COLORS["panel_bg"]).pack(side="left", padx=4)
            for c in board_str.split():
                tk.Label(self.cards_frame, text=c, font=("Consolas", 14, "bold"),
                         fg=card_color(c), bg=COLORS["panel_bg"]).pack(side="left", padx=1)

        # Equity + category
        eq = msg.get("equity")
        cat = msg.get("category", "")
        eq_str = f"Eq: {eq:.0%}" if eq is not None else ""
        if cat and cat != "TEST":
            eq_str += f"  {cat}"
        self.equity_var.set(eq_str)

        # Context: pot, stack, pot odds
        pot = msg.get("pot", 0)
        stack = msg.get("stack", 0)
        facing = msg.get("facing_bet", False)
        call = msg.get("call", 0)
        pot_odds = msg.get("pot_odds")
        ctx_parts = [f"Pot: €{pot:.2f}", f"Stack: €{stack:.2f}"]
        if facing and call > 0:
            ctx_parts.append(f"To call: €{call:.2f}")
            if pot_odds is not None:
                ctx_parts.append(f"Odds: {pot_odds:.0%}")
        self.context_var.set("  ".join(ctx_parts))

        # Opponent profile
        opp = msg.get("opponent", "")
        self.opponent_var.set(f"vs {opp}" if opp else "")

        # Recommendation
        rec = msg.get("rec", "")
        rec_color = msg.get("rec_color", "neutral")
        self.rec_var.set(rec)
        if rec_color == "green" or "RAISE" in rec.upper() or "BET" in rec.upper() or "CALL" in rec.upper():
            self.rec_label.config(bg=COLORS["rec_action"])
        elif rec_color == "red" or "FOLD" in rec.upper():
            self.rec_label.config(bg=COLORS["rec_fold"])
        elif rec_color == "blue" or "CHECK" in rec.upper():
            self.rec_label.config(bg=COLORS["rec_check"])
        else:
            self.rec_label.config(bg=COLORS["panel_bg"])

        # Timer
        timer = msg.get("timer")
        if timer is not None and timer > 0:
            self.timer_total = timer
            self.timer_remaining = timer
        elif "timer" in msg:
            self.timer_remaining = 0
            self.timer_total = 0
        self._update_timer_display()

        # Click status
        last_click = msg.get("last_click", "")
        if last_click == "OK":
            self.click_var.set("✓ clicked")
            self.click_label.config(fg=COLORS["click_ok"])
        elif last_click == "FAILED":
            self.click_var.set("✗ FAILED")
            self.click_label.config(fg=COLORS["click_fail"])
        elif last_click:
            self.click_var.set(last_click)
            self.click_label.config(fg=COLORS["text_dim"])

    def tick_timer(self, dt):
        """Decrement the action timer."""
        if self.timer_remaining > 0:
            self.timer_remaining -= dt
            if self.timer_remaining < 0:
                self.timer_remaining = 0
            self._update_timer_display()

    def _update_timer_display(self):
        if self.timer_total > 0 and self.timer_remaining > 0:
            self.timer_var.set(f"⏱ {self.timer_remaining:.1f}s")
        elif self.timer_total > 0:
            self.timer_var.set("⏱ acting…")
        else:
            self.timer_var.set("")


class OverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Poker Advisor")
        self.root.configure(bg=COLORS["bg"])
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.resizable(False, False)
        self.root.overrideredirect(False)

        # Position top-right
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"500x320+{sw - 520}+30")

        # Dragging
        drag = {"x": 0, "y": 0}
        self.root.bind("<Button-1>", lambda e: drag.update(x=e.x, y=e.y))
        self.root.bind("<B1-Motion>", lambda e: self.root.geometry(
            f"+{self.root.winfo_x() + e.x - drag['x']}+{self.root.winfo_y() + e.y - drag['y']}"))
        self.root.bind("<Button-3>", lambda e: self.root.destroy())

        # Top bar: session stats
        self.topbar = tk.Frame(self.root, bg=COLORS["bg"], padx=10, pady=6)
        self.topbar.pack(fill="x")

        title = tk.Label(self.topbar, text="POKER", font=("Consolas", 11, "bold"),
                          fg=COLORS["gold"], bg=COLORS["bg"])
        title.pack(side="left")
        self.mode_var = tk.StringVar(value="MANUAL")
        self.mode_label = tk.Label(self.topbar, textvariable=self.mode_var,
                                    font=("Consolas", 9, "bold"), fg="#888",
                                    bg=COLORS["bg"], padx=6)
        self.mode_label.pack(side="left", padx=(8, 0))

        # Pause/resume button — toggles PAUSE_FLAG file on disk.
        # Auto-player checks the flag before each click.
        self.paused = os.path.exists(PAUSE_FLAG)
        self.pause_btn = tk.Button(
            self.topbar, text="", font=("Consolas", 9, "bold"),
            command=self.toggle_pause,
            relief="flat", bd=0, padx=10, pady=2,
            activebackground=COLORS["panel_bg"], cursor="hand2",
        )
        self.pause_btn.pack(side="left", padx=(8, 0))
        self._refresh_pause_btn()

        self.stats_var = tk.StringVar(value="")
        tk.Label(self.topbar, textvariable=self.stats_var,
                 font=("Consolas", 10), fg=COLORS["accent"],
                 bg=COLORS["bg"]).pack(side="right")

        # Tables container (each TablePanel goes here)
        self.tables_container = tk.Frame(self.root, bg=COLORS["bg"])
        self.tables_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.panels = {}  # table_id → TablePanel
        self.session_data = {}

    def toggle_pause(self):
        self.paused = not self.paused
        try:
            if self.paused:
                with open(PAUSE_FLAG, "w") as f:
                    f.write(str(time.time()))
            else:
                if os.path.exists(PAUSE_FLAG):
                    os.remove(PAUSE_FLAG)
        except Exception as e:
            print(f"[overlay] pause toggle failed: {e}", file=sys.stderr)
        self._refresh_pause_btn()

    def _refresh_pause_btn(self):
        if self.paused:
            self.pause_btn.config(
                text="⏸ PAUSED",
                fg="#000000",
                bg=COLORS["timer"],  # amber
            )
        else:
            self.pause_btn.config(
                text="▶ RUNNING",
                fg="#000000",
                bg=COLORS["click_ok"],  # green
            )

    def update_session(self, msg):
        self.session_data.update(msg)
        mode = msg.get("mode", "MANUAL").upper()
        self.mode_var.set(f"[{mode}]")
        if mode == "AUTO":
            self.mode_label.config(fg=COLORS["click_ok"])
        else:
            self.mode_label.config(fg=COLORS["text_dim"])
        hands = msg.get("hands", 0)
        profit = msg.get("profit_eur", 0.0)
        bbhr = msg.get("bb_per_hr", 0.0)
        sign = "+" if profit >= 0 else ""
        self.stats_var.set(f"{hands}h  {sign}€{profit:.2f}  {sign}{bbhr:.0f}bb/hr")

    def update_table(self, msg):
        tid = msg.get("table_id", "default")
        if tid not in self.panels:
            panel = TablePanel(self.tables_container, tid)
            panel.pack(fill="x", pady=(0, 4))
            self.panels[tid] = panel
            self._resize()
        self.panels[tid].update(msg)

    def remove_table(self, tid):
        if tid in self.panels:
            self.panels[tid].destroy()
            del self.panels[tid]
            self._resize()

    def _resize(self):
        """Resize the window based on number of tables."""
        n = max(1, len(self.panels))
        # Top bar ~40px, container padding ~16px, each panel ~210px
        # (header 22 + cards 28 + equity 22 + context 18 + opponent 18 +
        #  rec 32 + status 18 + frame padding 14 + border 2 + spacing 16).
        # Old value was 150 which clipped the bottom rows including rec.
        h = 60 + n * 210
        self.root.geometry(f"500x{h}")

    def handle_message(self, msg):
        """Dispatch a message based on its type."""
        msg_type = msg.get("type")
        if msg_type == "session":
            self.update_session(msg)
        elif msg_type == "table_update":
            self.update_table(msg)
        elif msg_type == "table_remove":
            tid = msg.get("table_id")
            if tid:
                self.remove_table(tid)
        else:
            # Legacy: backwards compat with old single-table flat messages
            # {cards, info, rec, rec_bg, rec_fg}
            legacy = self._legacy_to_table_update(msg)
            self.update_table(legacy)

    def _legacy_to_table_update(self, msg):
        """Convert old single-table message format to new table_update."""
        cards = msg.get("cards", "")
        info = msg.get("info", "")
        rec = msg.get("rec", "")
        rec_bg = msg.get("rec_bg", "")
        rec_color = "neutral"
        if rec_bg == "#1a3a1a":
            rec_color = "green"
        elif rec_bg == "#3a1a1a":
            rec_color = "red"
        elif rec_bg == "#1a1a3a":
            rec_color = "blue"

        # Parse cards into hero/board
        if "  |  " in cards:
            hero, board = cards.split("  |  ", 1)
        else:
            hero, board = cards, ""

        # Try to extract equity from info string ("Eq: 64%")
        equity = None
        import re
        m = re.search(r'Eq(?:uity)?:\s*(\d+)%', info)
        if m:
            equity = int(m.group(1)) / 100.0

        return {
            "type": "table_update",
            "table_id": "default",
            "site": "default",
            "cards": hero,
            "board": board,
            "rec": rec,
            "rec_color": rec_color,
            "opponent": msg.get("opponent", ""),
            "equity": equity,
            "info": info,
        }

    def loop(self):
        """Main poll loop."""
        last_tick = time.time()
        def check():
            nonlocal last_tick
            now = time.time()
            dt = now - last_tick
            last_tick = now

            # Tick timers
            for p in self.panels.values():
                p.tick_timer(dt)

            # Process queued messages
            while not msg_queue.empty():
                try:
                    self.handle_message(msg_queue.get_nowait())
                except Exception as e:
                    print(f"[overlay] handle error: {e}", file=sys.stderr)

            self.root.after(100, check)

        self.root.after(100, check)
        self.root.mainloop()


def main():
    app = OverlayApp()
    t = threading.Thread(target=stdin_reader, daemon=True)
    t.start()
    app.loop()


if __name__ == "__main__":
    main()
