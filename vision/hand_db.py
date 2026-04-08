"""
Persistent hand history database. SQLite-backed, survives restarts.
All hands, all streets, all recommendations logged permanently.
"""

import sqlite3
import json
import os
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hands.db")


class HandDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS hands (
                hand_id INTEGER PRIMARY KEY,
                timestamp REAL,
                time_str TEXT,
                hero_card1 TEXT,
                hero_card2 TEXT,
                position TEXT,
                starting_stack INTEGER,
                profit_cents INTEGER DEFAULT 0,
                site TEXT DEFAULT 'unibet'
            );

            CREATE TABLE IF NOT EXISTS streets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hand_id INTEGER,
                phase TEXT,
                board TEXT,
                pot INTEGER,
                facing_bet INTEGER,
                call_amount INTEGER,
                hero_stack INTEGER,
                rec_action TEXT,
                rec_equity REAL,
                rec_source TEXT,
                cfr_probs TEXT,
                opponent_type TEXT,
                FOREIGN KEY (hand_id) REFERENCES hands(hand_id)
            );

            CREATE TABLE IF NOT EXISTS opponents (
                name TEXT PRIMARY KEY,
                hands_seen INTEGER DEFAULT 0,
                vpip INTEGER DEFAULT 0,
                pfr INTEGER DEFAULT 0,
                postflop_bets INTEGER DEFAULT 0,
                postflop_calls INTEGER DEFAULT 0,
                postflop_folds INTEGER DEFAULT 0,
                went_to_showdown INTEGER DEFAULT 0,
                won_at_showdown INTEGER DEFAULT 0,
                classification TEXT DEFAULT 'UNKNOWN',
                last_seen REAL
            );

            CREATE INDEX IF NOT EXISTS idx_hands_time ON hands(timestamp);
            CREATE INDEX IF NOT EXISTS idx_streets_hand ON streets(hand_id);
            CREATE INDEX IF NOT EXISTS idx_opponents_class ON opponents(classification);
        """)
        self.conn.commit()

    def log_hand_start(self, hand_id, hero_cards, position, starting_stack):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO hands (hand_id, timestamp, time_str, hero_card1, hero_card2, position, starting_stack) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (hand_id, time.time(), time.strftime("%H:%M:%S"), hero_cards[0], hero_cards[1], position, starting_stack)
            )
            self.conn.commit()
        except Exception:
            pass

    def log_street(self, hand_id, phase, board, pot, facing_bet, call_amount,
                   hero_stack, rec_action=None, rec_equity=None, rec_source=None,
                   cfr_probs=None, opponent_type=None):
        try:
            self.conn.execute(
                "INSERT INTO streets (hand_id, phase, board, pot, facing_bet, call_amount, "
                "hero_stack, rec_action, rec_equity, rec_source, cfr_probs, opponent_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hand_id, phase, json.dumps(board), pot, int(facing_bet), call_amount,
                 hero_stack, rec_action, rec_equity, rec_source,
                 json.dumps(cfr_probs) if cfr_probs else None, opponent_type)
            )
            self.conn.commit()
        except Exception:
            pass

    def log_hand_result(self, hand_id, profit_cents):
        try:
            self.conn.execute(
                "UPDATE hands SET profit_cents = ? WHERE hand_id = ?",
                (profit_cents, hand_id)
            )
            self.conn.commit()
        except Exception:
            pass

    def log_opponent(self, name, stats):
        try:
            self.conn.execute(
                "INSERT INTO opponents (name, hands_seen, vpip, pfr, postflop_bets, "
                "postflop_calls, postflop_folds, classification, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "hands_seen=?, vpip=?, pfr=?, postflop_bets=?, postflop_calls=?, "
                "postflop_folds=?, classification=?, last_seen=?",
                (name, stats['hands'], stats['vpip'], stats['pfr'],
                 stats['bets'], stats['calls'], stats['folds'],
                 stats.get('type', 'UNKNOWN'), time.time(),
                 stats['hands'], stats['vpip'], stats['pfr'],
                 stats['bets'], stats['calls'], stats['folds'],
                 stats.get('type', 'UNKNOWN'), time.time())
            )
            self.conn.commit()
        except Exception:
            pass

    def get_session_stats(self, hours=24):
        """Get stats for recent session."""
        since = time.time() - hours * 3600
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(profit_cents) FROM hands WHERE timestamp > ?", (since,)
        ).fetchone()
        return {"hands": row[0] or 0, "profit_cents": row[1] or 0}

    def get_opponent(self, name):
        row = self.conn.execute(
            "SELECT * FROM opponents WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        return {
            "name": row[0], "hands_seen": row[1], "vpip": row[2], "pfr": row[3],
            "postflop_bets": row[4], "postflop_calls": row[5], "postflop_folds": row[6],
            "went_to_showdown": row[7], "won_at_showdown": row[8],
            "classification": row[9], "last_seen": row[10]
        }

    def close(self):
        self.conn.close()
