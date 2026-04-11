"""Thin app-facing wrapper around the Rust advisor-cli binary.

Matches the subprocess JSON pattern used by solver_bridge.py.

Usage:
    from advisor_service.mode_router import ModeRouter

    router = ModeRouter(
        artifact_root="artifacts/solver",
        action_menu="configs/action_menu_v1.yaml",
        prior_bin="artifacts/emergency/emergency_range_prior.bin",
        prior_manifest="artifacts/emergency/emergency_range_prior.manifest.json",
    )
    resp = router.recommend(request_dict)
"""

import json
import logging
import subprocess
import sys
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

# Locate the advisor-cli binary relative to project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_BIN = _PROJECT_ROOT / "rust" / "target" / "release" / (
    "advisor-cli.exe" if sys.platform == "win32" else "advisor-cli"
)


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

    def recommend(self, request: dict) -> dict:
        """Send a single request and return the response dict.

        Raises RuntimeError if the process crashes or returns invalid JSON.
        """
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
