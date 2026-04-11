"""Thin app-facing wrapper around the Rust advisor-cli binary.

Matches the subprocess JSON pattern used by solver_bridge.py.

Usage:
    from advisor_service.mode_router import ModeRouter

    router = ModeRouter(
        artifact_root="artifacts/solver",
        action_menu="configs/action_menu_v1.yaml",
        prior_bin="artifacts/emergency/emergency_range_prior.bin",
        prior_manifest="artifacts/emergency/emergency_range_prior.manifest.json",
        preflop_charts="configs/preflop_charts.json",
    )
    resp = router.recommend(request_dict)
"""

import json
import logging
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

# Locate the advisor-cli binary relative to project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_BIN = _PROJECT_ROOT / "rust" / "target" / "release" / (
    "advisor-cli.exe" if sys.platform == "win32" else "advisor-cli"
)


_RANK_ORDER = "AKQJT98765432"
_RANK_NAMES = {"A": 14, "K": 13, "Q": 12, "J": 11, "T": 10,
               "9": 9, "8": 8, "7": 7, "6": 6, "5": 5, "4": 4, "3": 3, "2": 2}


def _cards_to_hand_key(hole_cards: list) -> str:
    """Convert ["As", "Kd"] to canonical hand name like "AKo".

    Convention: high card first, "s" suffix if suited, "o" if offsuit or pair.
    """
    if not hole_cards or len(hole_cards) < 2:
        return ""
    r1 = hole_cards[0][0].upper()
    s1 = hole_cards[0][1].lower()
    r2 = hole_cards[1][0].upper()
    s2 = hole_cards[1][1].lower()

    # Sort so higher rank is first
    v1 = _RANK_NAMES.get(r1, 0)
    v2 = _RANK_NAMES.get(r2, 0)
    if v1 < v2:
        r1, r2 = r2, r1
        s1, s2 = s2, s1

    suited = s1 == s2
    if r1 == r2:
        return f"{r1}{r2}o"  # pair
    return f"{r1}{r2}s" if suited else f"{r1}{r2}o"


def _classify_preflop_facing(request: dict) -> str:
    """Determine the preflop facing scenario from the request.

    Returns: 'unopened', 'facing_open', 'facing_3bet', or 'facing_limp'.
    """
    facing_bet = request.get("facing_bet", False)
    call_amount = request.get("call_amount", 0)
    big_blind = request.get("big_blind", 10)
    call_bb = call_amount / big_blind if big_blind > 0 else 0

    if not facing_bet:
        return "unopened"

    # Facing a bet: distinguish limp vs open vs 3bet by call size
    if call_bb < 1.5:
        return "facing_limp"
    elif call_bb < 5.0:
        return "facing_open"
    else:
        return "facing_3bet"


# Position seat number to label (6-max, BTN=4).
_SEAT_TO_POS = {1: "UTG", 2: "HJ", 3: "CO", 4: "BTN", 5: "SB", 6: "BB"}


class ModeRouter:
    """Subprocess bridge to the Rust advisor-cli."""

    def __init__(
        self,
        artifact_root,
        action_menu,
        prior_bin,
        prior_manifest,
        quarantine_dir=None,
        binary_path=None,
        preflop_charts=None,
    ):
        self.binary = str(binary_path or _DEFAULT_BIN)
        self.artifact_root = str(artifact_root)
        self.action_menu = str(action_menu)
        self.prior_bin = str(prior_bin)
        self.prior_manifest = str(prior_manifest)
        self.quarantine_dir = str(quarantine_dir or "quarantine")

        # Counters for structured logging.
        self._mode_counts = Counter()
        self._snap_counts = Counter()
        self._error_count = 0

        self._proc = None

        # ── Preflop charts ───────────────────────────────────────────────
        self._preflop_charts = None
        if preflop_charts:
            pf_path = Path(preflop_charts)
            if pf_path.exists():
                self._preflop_charts = json.loads(pf_path.read_text())
                log.info("loaded preflop charts from %s", pf_path)

    def _ensure_process(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        cmd = [
            self.binary,
            "--artifact-root", self.artifact_root,
            "--action-menu", self.action_menu,
            "--prior-bin", self.prior_bin,
            "--prior-manifest", self.prior_manifest,
            "--quarantine-dir", self.quarantine_dir,
        ]
        log.info("starting advisor-cli: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def _preflop_recommend(self, request: dict, profile: str = None) -> dict:
        """Handle a preflop request using the chart lookup.

        Returns a response dict in the same shape as the Rust advisor,
        or None if preflop charts are not loaded.
        """
        if self._preflop_charts is None:
            return None

        street = request.get("street", "")
        if street != "preflop":
            return None

        hole_cards = request.get("hole_cards", [])
        hand_key = _cards_to_hand_key(hole_cards)
        if not hand_key:
            return None

        hero_seat = request.get("hero_seat", 0)
        position = _SEAT_TO_POS.get(hero_seat, "BB")

        facing = _classify_preflop_facing(request)
        profile = profile or self._preflop_charts.get("default_profile", "tag")

        profiles = self._preflop_charts.get("profiles", {})
        chart = profiles.get(profile, {}).get(facing, {}).get(position, {})
        action = chart.get(hand_key)

        if action is None:
            return None  # hand not in chart, fall through to Rust

        # Map chart action to response format
        big_blind = request.get("big_blind", 10)
        hero_stack = request.get("hero_stack", 0)
        pot = request.get("pot", 0)
        legal_actions = request.get("legal_actions", [])

        # Determine action kind and amount
        if action == "FOLD":
            kind = "fold"
            amount = 0
        elif action in ("OPEN", "OPEN_LARGE"):
            multiplier = 3.0 if action == "OPEN_LARGE" else 2.5
            amount = big_blind * multiplier
            # Check if bet_to is legal
            has_bet = any(la.get("kind") == "bet_to" for la in legal_actions)
            has_raise = any(la.get("kind") == "raise_to" for la in legal_actions)
            if has_raise:
                kind = "raise_to"
            elif has_bet:
                kind = "bet_to"
            else:
                kind = "call"  # fallback if can't raise
                amount = request.get("call_amount", 0)
        elif action == "CALL":
            kind = "call"
            amount = request.get("call_amount", 0)
            if amount == 0:
                kind = "check"
        elif action == "RAISE_3X":
            # 3bet: ~3x the open, or iso-raise vs limpers
            call_amt = request.get("call_amount", 0)
            if call_amt > 0:
                amount = call_amt * 3 + pot
            else:
                amount = big_blind * 3
            has_raise = any(la.get("kind") == "raise_to" for la in legal_actions)
            has_bet = any(la.get("kind") == "bet_to" for la in legal_actions)
            if has_raise:
                kind = "raise_to"
            elif has_bet:
                kind = "bet_to"
            else:
                kind = "call"
                amount = call_amt
        elif action == "JAM":
            kind = "raise_to"
            amount = hero_stack
        else:
            return None

        # Clamp amount to legal bounds
        for la in legal_actions:
            if la.get("kind") == kind:
                lo = la.get("min", 0)
                hi = la.get("max", hero_stack)
                amount = max(lo, min(amount, hi))
                break

        return {
            "mode": "preflop_chart",
            "action_kind": kind,
            "action_amount": amount,
            "trust_score": 0.85,
            "was_snapped": False,
            "snap_reason": None,
            "preflop_chart": {
                "hand": hand_key,
                "position": position,
                "facing": facing,
                "profile": profile,
                "chart_action": action,
            },
        }

    def recommend(self, request: dict, profile: str = None) -> dict:
        """Send a single request and return the response dict.

        For preflop requests, uses the chart lookup if available.
        For postflop, delegates to the Rust advisor-cli.

        Raises RuntimeError if the process crashes or returns invalid JSON.
        """
        # Try preflop chart first
        pf_resp = self._preflop_recommend(request, profile)
        if pf_resp is not None:
            self._mode_counts["preflop_chart"] += 1
            return pf_resp

        self._ensure_process()

        line = json.dumps(request, separators=(",", ":")) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            out_line = self._proc.stdout.readline()
        except (BrokenPipeError, OSError) as e:
            self._error_count += 1
            raise RuntimeError(f"advisor-cli pipe error: {e}") from e

        if not out_line:
            self._error_count += 1
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"advisor-cli produced no output. stderr: {stderr}")

        resp = json.loads(out_line)

        if "error" in resp:
            self._error_count += 1
            log.warning("advisor-cli error: %s", resp["error"])
        else:
            self._mode_counts[resp.get("mode", "unknown")] += 1
            if resp.get("was_snapped"):
                self._snap_counts[resp.get("snap_reason", "unknown")] += 1
            log.debug(
                "mode=%s action=%s@%.0f trust=%.2f latency=%dus key=%s",
                resp.get("mode"), resp.get("action_kind"), resp.get("action_amount", 0),
                resp.get("trust_score", 0), resp.get("latency_us", 0),
                resp.get("artifact_key", "?"),
            )

        return resp

    def recommend_batch(self, requests):
        """Send multiple requests, return list of responses."""
        return [self.recommend(r) for r in requests]

    def stats(self) -> dict:
        """Return accumulated counters."""
        total = sum(self._mode_counts.values())
        return {
            "total": total,
            "mode_counts": dict(self._mode_counts),
            "exact_rate": self._mode_counts.get("exact", 0) / max(total, 1),
            "emergency_rate": self._mode_counts.get("emergency", 0) / max(total, 1),
            "snap_counts": dict(self._snap_counts),
            "error_count": self._error_count,
        }

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
            self._proc = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
