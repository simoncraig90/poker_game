"""Per-decision JSONL logger for shadow sessions.

Writes to ``vision/data/shadow_{session_id}.jsonl``.

Record types:
  session_meta        — written at start, updated at end
  decision            — one per advisor recommendation
  validation_anomaly  — on warn/unsafe validation
  focus_event         — on every focus request
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionMeta:
    session_id: str
    start_ts: str
    binary_hash: str = ""
    artifact_count: int = 0
    preflight_verdict: str = ""
    hero_id: int = 0
    table_mode: str = "single"
    # Accumulated during session
    total_decisions: int = 0
    total_hands: int = 0
    mode_counts: dict = field(default_factory=dict)
    validation_warns: int = 0
    validation_unsafe: int = 0
    focus_requests: int = 0
    focus_succeeded: int = 0
    latency_sum_us: int = 0
    trust_sum: float = 0.0

    @property
    def mean_latency_us(self) -> int:
        return self.latency_sum_us // max(self.total_decisions, 1)

    @property
    def mean_trust(self) -> float:
        return self.trust_sum / max(self.total_decisions, 1)

    def to_dict(self, end_ts: str = "") -> dict:
        return {
            "type": "session_meta",
            "session_id": self.session_id,
            "start_ts": self.start_ts,
            "end_ts": end_ts or _now_iso(),
            "binary_hash": self.binary_hash,
            "artifact_count": self.artifact_count,
            "preflight_verdict": self.preflight_verdict,
            "hero_id": self.hero_id,
            "table_mode": self.table_mode,
            "total_decisions": self.total_decisions,
            "total_hands": self.total_hands,
            "mode_counts": dict(self.mode_counts),
            "validation_warns": self.validation_warns,
            "validation_unsafe": self.validation_unsafe,
            "focus_requests": self.focus_requests,
            "focus_succeeded": self.focus_succeeded,
            "mean_latency_us": self.mean_latency_us,
            "mean_trust": self.mean_trust,
        }


class DecisionLogger:
    """Append-only JSONL writer for shadow session events."""

    def __init__(self, session_id: str, output_dir: str = ""):
        self.session_id = session_id
        if not output_dir:
            output_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data"
            )
        os.makedirs(output_dir, exist_ok=True)
        self._path = os.path.join(output_dir, f"shadow_{session_id}.jsonl")
        self._file = open(self._path, "a", encoding="utf-8")
        self._seen_hands: set = set()
        self.meta = SessionMeta(session_id=session_id, start_ts=_now_iso())

    @property
    def path(self) -> str:
        return self._path

    def _write(self, record: dict) -> None:
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()

    def log_decision(
        self,
        snap: dict,
        advisor_out,
        validation_status: str,
        table_id: str = "",
        villain_class: str = "",
        villain_class_source: str = "",
    ) -> None:
        """Log a single advisor decision."""
        mode = getattr(advisor_out, "source", "") or ""
        action = getattr(advisor_out, "action", "") or ""
        trust = getattr(advisor_out, "trust_score", 0.0) or 0.0
        equity = getattr(advisor_out, "equity", None)
        latency_us = getattr(advisor_out, "latency_us", 0) or 0
        artifact_key = getattr(advisor_out, "artifact_key", "") or ""
        was_snapped = getattr(advisor_out, "was_snapped", False)
        snap_reason = getattr(advisor_out, "snap_reason", None)
        preflop_chart = getattr(advisor_out, "preflop_chart", None)

        # Parse action kind and amount from the action string
        action_kind = ""
        action_amount = 0.0
        if action:
            parts = action.strip().split()
            action_kind = parts[0].lower() if parts else ""
            if len(parts) > 1:
                try:
                    action_amount = float(parts[1])
                except ValueError:
                    pass

        record = {
            "type": "decision",
            "session_id": self.session_id,
            "wall_ts": _now_iso(),
            "room_name": snap.get("room_name", ""),
            "table_id": table_id,
            "hand_id": str(snap.get("hand_id", "")),
            "phase": snap.get("phase", ""),
            "hero_cards": snap.get("hero_cards", []),
            "board_cards": snap.get("board_cards", []),
            "position": snap.get("position", ""),
            "pot": snap.get("pot", 0),
            "hero_stack": snap.get("hero_stack", 0),
            "facing_bet": snap.get("facing_bet", False),
            "call_amount": snap.get("call_amount", 0),
            "num_opponents": snap.get("num_opponents", 0),
            "validation_status": validation_status,
            "mode": mode,
            "action_kind": action_kind,
            "action_amount": action_amount,
            "trust_score": trust,
            "artifact_key": artifact_key,
            "was_snapped": was_snapped,
            "snap_reason": snap_reason,
            "latency_us": latency_us,
            "villain_class": villain_class,
            "villain_class_source": villain_class_source,
            "equity": equity,
            "preflop_chart": preflop_chart,
        }
        self._write(record)

        # Update meta counters
        self.meta.total_decisions += 1
        hand_id = str(snap.get("hand_id", ""))
        if hand_id and hand_id not in self._seen_hands:
            self._seen_hands.add(hand_id)
            self.meta.total_hands += 1
        mc = self.meta.mode_counts
        mc[mode] = mc.get(mode, 0) + 1
        self.meta.latency_sum_us += latency_us
        self.meta.trust_sum += trust

    def log_validation_anomaly(
        self, snap: dict, severity: str, checks_failed: list
    ) -> None:
        record = {
            "type": "validation_anomaly",
            "wall_ts": _now_iso(),
            "session_id": self.session_id,
            "room_name": snap.get("room_name", ""),
            "hand_id": str(snap.get("hand_id", "")),
            "severity": severity,
            "checks_failed": checks_failed,
            "snap": snap,
        }
        self._write(record)
        if severity == "warn":
            self.meta.validation_warns += 1
        elif severity == "unsafe":
            self.meta.validation_unsafe += 1

    def log_focus_event(
        self, table_id: str, hand_id: str, succeeded: bool, reason: str = ""
    ) -> None:
        record = {
            "type": "focus_event",
            "wall_ts": _now_iso(),
            "session_id": self.session_id,
            "table_id": table_id,
            "hand_id": hand_id,
            "requested": True,
            "succeeded": succeeded,
            "reason": reason,
        }
        self._write(record)
        self.meta.focus_requests += 1
        if succeeded:
            self.meta.focus_succeeded += 1

    def write_session_meta(self) -> None:
        """Write or re-write the session meta summary."""
        self._write(self.meta.to_dict())

    def close(self) -> None:
        self.write_session_meta()
        try:
            self._file.close()
        except Exception:
            pass
