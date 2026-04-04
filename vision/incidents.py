"""
Automated incident and RCA tracking for the advisor pipeline.

Logs issues as they happen during live sessions with:
  - Timestamp and session context
  - What went wrong (crash, misdetection, bad advice)
  - Root cause category
  - Severity (P0-P3)
  - Affected component

Usage:
  from incidents import IncidentTracker
  tracker = IncidentTracker()
  tracker.log("CARD_MISREAD", "Jc detected as 3c", component="card_id",
              severity="P1", context={"hero": ["3c", "Js"], "expected": ["Jc", "Js"]})
  tracker.log("MODEL_CRASH", "tensor shape mismatch", component="equity_model",
              severity="P0", context={"error": str(e), "hero_len": 3})
"""

import json
import os
import time
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parent
INCIDENTS_PATH = VISION_DIR / "data" / "incidents.jsonl"
SUMMARY_PATH = VISION_DIR / "data" / "incident_summary.json"

# Root cause categories
RCA_CATEGORIES = {
    "CARD_MISREAD": "Template matching confusion between visually similar cards",
    "MODEL_CRASH": "Neural net input shape mismatch or runtime error",
    "WRONG_POSITION": "Dealer button position detection gave wrong seat",
    "WRONG_FACING_BET": "Failed to detect or falsely detected facing a bet",
    "BAD_EQUITY": "Equity estimate significantly wrong for the hand/board",
    "BAD_PREFLOP": "Preflop chart gave wrong action for the hand/position",
    "OVERLAY_CRASH": "Tkinter overlay crashed or froze",
    "YOLO_MISS": "YOLO failed to detect cards or board",
    "STALE_STATE": "State not updating, showing old hand info",
    "UNKNOWN": "Unclassified issue",
}

SEVERITY_LEVELS = {
    "P0": "Crash — advisor stops working entirely",
    "P1": "Wrong info — could cost money (wrong cards, wrong equity)",
    "P2": "Degraded — partial info missing but core works",
    "P3": "Minor — cosmetic or non-impactful",
}


class IncidentTracker:
    def __init__(self, path=None):
        self.path = path or INCIDENTS_PATH
        self.session_start = time.time()
        self.session_id = f"session-{int(self.session_start)}"
        self.incidents = []
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def log(self, category, description, component="unknown", severity="P2",
            context=None, auto_rca=True):
        """Log an incident."""
        incident = {
            "timestamp": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session": self.session_id,
            "category": category,
            "severity": severity,
            "component": component,
            "description": description,
            "rca": RCA_CATEGORIES.get(category, "Unclassified"),
            "context": context or {},
        }

        # Auto-generate fix suggestions
        if auto_rca:
            incident["suggested_fix"] = self._suggest_fix(category, context)

        self.incidents.append(incident)

        # Append to file
        with open(self.path, "a") as f:
            f.write(json.dumps(incident) + "\n")

        # Print to console
        sev_icon = {"P0": "!!!", "P1": "!!", "P2": "!", "P3": "."}
        icon = sev_icon.get(severity, "?")
        print(f"[INCIDENT {icon} {severity}] {category}: {description}")

        return incident

    def _suggest_fix(self, category, context):
        """Auto-generate fix suggestion based on category."""
        fixes = {
            "CARD_MISREAD": "Recapture card templates from current PS theme. Check template similarity scores. Consider adding card rank OCR as fallback.",
            "MODEL_CRASH": "Check input tensor shapes. Clamp hero cards to 2, board to 5. Add try/except with heuristic fallback.",
            "WRONG_POSITION": "Improve dealer button X/Y mapping. Consider OCR of position labels or seat numbering.",
            "WRONG_FACING_BET": "Tune red/green button color thresholds for current PS theme. Add bet amount text detection as confirmation.",
            "BAD_EQUITY": "Retrain equity model with more samples. Check if board texture features are computed correctly for this case.",
            "BAD_PREFLOP": "Review preflop chart ranges. Check hand key parsing (suited/offsuit detection).",
            "OVERLAY_CRASH": "Add try/except around Tk updates. Check for thread safety issues.",
            "YOLO_MISS": "Check YOLO confidence threshold. May need to retrain on current PS theme if UI changed.",
            "STALE_STATE": "Check screen capture rate. Verify table region detection is consistent.",
        }
        return fixes.get(category, "Investigate and classify.")

    def log_card_misread(self, detected, expected, confidence=None):
        """Convenience: log a card misread incident."""
        self.log(
            "CARD_MISREAD",
            f"Detected {detected}, expected {expected}",
            component="card_id",
            severity="P1",
            context={"detected": detected, "expected": expected, "confidence": confidence},
        )

    def log_crash(self, error, component="advisor"):
        """Convenience: log a crash."""
        self.log(
            "MODEL_CRASH" if "tensor" in str(error).lower() or "shape" in str(error).lower() else "OVERLAY_CRASH",
            str(error)[:200],
            component=component,
            severity="P0",
            context={"error_type": type(error).__name__, "error": str(error)[:500]},
        )

    def log_bad_equity(self, hero, board, model_eq, expected_eq):
        """Convenience: log a bad equity estimate."""
        self.log(
            "BAD_EQUITY",
            f"Model={model_eq:.0%} Expected={expected_eq:.0%} for {' '.join(hero)} | {' '.join(board)}",
            component="equity_model",
            severity="P1",
            context={"hero": hero, "board": board, "model_eq": model_eq, "expected_eq": expected_eq},
        )

    def summary(self):
        """Generate incident summary."""
        if not os.path.exists(self.path):
            return {"total": 0, "by_severity": {}, "by_category": {}}

        incidents = []
        with open(self.path) as f:
            for line in f:
                try:
                    incidents.append(json.loads(line))
                except Exception:
                    pass

        by_sev = {}
        by_cat = {}
        by_comp = {}
        for inc in incidents:
            s = inc.get("severity", "P3")
            c = inc.get("category", "UNKNOWN")
            comp = inc.get("component", "unknown")
            by_sev[s] = by_sev.get(s, 0) + 1
            by_cat[c] = by_cat.get(c, 0) + 1
            by_comp[comp] = by_comp.get(comp, 0) + 1

        summary = {
            "total": len(incidents),
            "by_severity": by_sev,
            "by_category": by_cat,
            "by_component": by_comp,
            "latest": incidents[-5:] if incidents else [],
        }

        # Save summary
        with open(SUMMARY_PATH, "w") as f:
            json.dump(summary, f, indent=2)

        return summary

    def print_summary(self):
        """Print incident summary to console."""
        s = self.summary()
        print(f"\n{'='*50}")
        print(f"  INCIDENT SUMMARY — {s['total']} total")
        print(f"{'='*50}")
        if s["by_severity"]:
            print("  By severity:")
            for sev in ["P0", "P1", "P2", "P3"]:
                if sev in s["by_severity"]:
                    print(f"    {sev}: {s['by_severity'][sev]}")
        if s["by_category"]:
            print("  By category:")
            for cat, count in sorted(s["by_category"].items(), key=lambda x: -x[1]):
                print(f"    {cat}: {count}")
        print(f"{'='*50}\n")
