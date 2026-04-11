#!/usr/bin/env python3
"""
build_emergency_prior.py
Generate the emergency range prior artifact.

Output files (in OUTPUT_DIR):
  emergency_range_prior.bin        -- flat little-endian f64 array
  emergency_range_prior.manifest.json

The prior is a 4,320-entry table indexed as:
  [hand_bucket][board_texture][pot_class][aggressor_role][n_players_bucket]

Dimensions:
  hand_bucket:      12  (Monster … Air)
  board_texture:     6  (DryRainbow … Monotone)
  pot_class:         4  (Limped / SRP / 3BP / 4BP+Squeeze)
  aggressor_role:    3  (None / IP / OOP)
  n_players_bucket:  5  (2-way … 6-way)

Total: 12 × 6 × 4 × 3 × 5 = 4,320 entries × 8 bytes = 34,560 bytes ≈ 34 KB

Each value is the approximate equity [0, 1] for that hand + spot combination
against a typical opponent range. Values are calibrated heuristically —
good enough for a conservative emergency fallback, not PIO-grade.
"""
import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path

# ── Dimensions ────────────────────────────────────────────────────────────────

HAND_BUCKETS = [
    "Monster",            # 0
    "VeryStrong",         # 1
    "Strong",             # 2
    "StrongTwoPair",      # 3
    "WeakTwoPair",        # 4
    "Overpair",           # 5
    "TopPairGoodKicker",  # 6
    "TopPairWeak",        # 7
    "WeakPair",           # 8
    "StrongDraw",         # 9
    "WeakDraw",           # 10
    "Air",                # 11
]

BOARD_TEXTURES = [
    "DryRainbow",        # 0
    "DryPaired",         # 1
    "ConnectedRainbow",  # 2
    "FlushdrawBoard",    # 3
    "WetConnected",      # 4
    "Monotone",          # 5
]

POT_CLASSES = [
    "Limped",  # 0
    "SRP",     # 1
    "3BP",     # 2
    "4BP",     # 3  (4-bet pots and squeezes grouped)
]

AGGRESSOR_ROLES = [
    "None",    # 0
    "IP",      # 1
    "OOP",     # 2
]

N_PLAYERS_BUCKETS = [2, 3, 4, 5, 6]  # raw player count → index 0-4

N_HAND_BUCKETS     = len(HAND_BUCKETS)
N_BOARD_TEXTURES   = len(BOARD_TEXTURES)
N_POT_CLASSES      = len(POT_CLASSES)
N_AGGRESSOR_ROLES  = len(AGGRESSOR_ROLES)
N_PLAYER_BUCKETS   = len(N_PLAYERS_BUCKETS)
TABLE_LEN          = N_HAND_BUCKETS * N_BOARD_TEXTURES * N_POT_CLASSES * N_AGGRESSOR_ROLES * N_PLAYER_BUCKETS
assert TABLE_LEN == 4320, f"Expected 4320 entries, got {TABLE_LEN}"

# ── Base equity by hand bucket (heads-up SRP, dry rainbow, no aggressor) ─────

BASE_EQUITY = {
    "Monster":            0.93,
    "VeryStrong":         0.84,
    "Strong":             0.74,
    "StrongTwoPair":      0.66,
    "WeakTwoPair":        0.58,
    "Overpair":           0.69,
    "TopPairGoodKicker":  0.59,
    "TopPairWeak":        0.50,
    "WeakPair":           0.40,
    "StrongDraw":         0.53,
    "WeakDraw":           0.36,
    "Air":                0.22,
}

# ── Modifiers ─────────────────────────────────────────────────────────────────

# Board texture: wetter = draws worth more → absolute equity of made hands falls.
TEXTURE_MOD = {
    "DryRainbow":        +0.02,
    "DryPaired":         -0.04,  # board pair makes made-hand strength uncertain
    "ConnectedRainbow":  -0.02,
    "FlushdrawBoard":    -0.03,
    "WetConnected":      -0.05,
    "Monotone":          -0.07,  # heavy flush equity skew
}

# Pot class: 3-bet/4-bet ranges are stronger → medium hands worth less.
POT_CLASS_MOD = {
    "Limped":  +0.03,
    "SRP":      0.00,
    "3BP":     -0.05,
    "4BP":     -0.10,
}

# Aggressor role: aggressor has a stronger/narrower range → hero equity falls.
# "None" is limped pots.
AGGRESSOR_MOD = {
    "None":  0.00,
    "IP":   -0.02,
    "OOP":  -0.04,
}

# N-players: more players = lower equity for everything except nuts.
# Applied uniformly; adjust by hand bucket below.
NWAY_MOD = {
    2: 0.00,
    3: -0.05,
    4: -0.08,
    5: -0.10,
    6: -0.12,
}

# For monster hands, multiway barely changes equity (still crushes most villains).
NWAY_MOD_STRONG = {
    2: 0.00,
    3: -0.02,
    4: -0.03,
    5: -0.04,
    6: -0.05,
}

STRONG_BUCKETS = {"Monster", "VeryStrong", "Strong"}

# ── Equity computation ────────────────────────────────────────────────────────

def compute_equity(h: int, t: int, p: int, a: int, n: int) -> float:
    hname  = HAND_BUCKETS[h]
    tname  = BOARD_TEXTURES[t]
    pname  = POT_CLASSES[p]
    aname  = AGGRESSOR_ROLES[a]
    nval   = N_PLAYERS_BUCKETS[n]

    nmod = NWAY_MOD_STRONG[nval] if hname in STRONG_BUCKETS else NWAY_MOD[nval]

    eq = (BASE_EQUITY[hname]
          + TEXTURE_MOD[tname]
          + POT_CLASS_MOD[pname]
          + AGGRESSOR_MOD[aname]
          + nmod)

    return max(0.05, min(0.95, eq))


def table_index(h: int, t: int, p: int, a: int, n: int) -> int:
    return (h * N_BOARD_TEXTURES * N_POT_CLASSES * N_AGGRESSOR_ROLES * N_PLAYER_BUCKETS
            + t * N_POT_CLASSES * N_AGGRESSOR_ROLES * N_PLAYER_BUCKETS
            + p * N_AGGRESSOR_ROLES * N_PLAYER_BUCKETS
            + a * N_PLAYER_BUCKETS
            + n)

# ── Build table ───────────────────────────────────────────────────────────────

def build_table() -> list[float]:
    table = [0.0] * TABLE_LEN
    for h in range(N_HAND_BUCKETS):
        for t in range(N_BOARD_TEXTURES):
            for p in range(N_POT_CLASSES):
                for a in range(N_AGGRESSOR_ROLES):
                    for n in range(N_PLAYER_BUCKETS):
                        idx = table_index(h, t, p, a, n)
                        table[idx] = compute_equity(h, t, p, a, n)
    return table

# ── Sanity checks ─────────────────────────────────────────────────────────────

def sanity_check(table: list[float]) -> None:
    def eq(h, t, p, a, n):
        return table[table_index(h, t, p, a, n)]

    # Monster > Air for every combination
    for t in range(N_BOARD_TEXTURES):
        for p in range(N_POT_CLASSES):
            for a in range(N_AGGRESSOR_ROLES):
                for n in range(N_PLAYER_BUCKETS):
                    assert eq(0, t, p, a, n) > eq(11, t, p, a, n), \
                        f"Monster should beat Air at t={t} p={p} a={a} n={n}"

    # All values in [0.05, 0.95]
    assert all(0.05 <= v <= 0.95 for v in table), "Values out of [0.05, 0.95]"

    # Monotone board lowers equity vs DryRainbow for medium hands
    tp_gk_idx_dry  = table_index(6, 0, 1, 0, 0)  # TPGK / Dry / SRP / None / 2way
    tp_gk_idx_mono = table_index(6, 5, 1, 0, 0)  # TPGK / Mono / SRP / None / 2way
    assert table[tp_gk_idx_mono] < table[tp_gk_idx_dry], \
        "Monotone board should lower TPGK equity vs dry rainbow"

    # 3BP reduces equity for WeakPair vs SRP
    wp_srp  = table_index(8, 0, 1, 0, 0)
    wp_3bp  = table_index(8, 0, 2, 0, 0)
    assert table[wp_3bp] < table[wp_srp], \
        "3BP should lower WeakPair equity vs SRP"

    # More players = lower equity for middle-strength hands
    mid_2way = table_index(7, 0, 1, 0, 0)  # TopPairWeak / 2-way
    mid_6way = table_index(7, 0, 1, 0, 4)  # TopPairWeak / 6-way
    assert table[mid_6way] < table[mid_2way], \
        "6-way pot should lower TopPairWeak equity vs 2-way"

    print("Sanity checks passed.")

# ── Output ────────────────────────────────────────────────────────────────────

def write_output(table: list[float], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    bin_path      = output_dir / "emergency_range_prior.bin"
    manifest_path = output_dir / "emergency_range_prior.manifest.json"

    # Binary: little-endian f64
    data = struct.pack(f"<{TABLE_LEN}d", *table)

    checksum = hashlib.sha256(data).hexdigest()

    bin_path.write_bytes(data)
    print(f"Wrote {len(data):,} bytes to {bin_path}")

    manifest = {
        "artifact_type":    "emergency_range_prior",
        "version":           1,
        "checksum_sha256":   checksum,
        "file_size_bytes":   len(data),
        "n_hand_buckets":    N_HAND_BUCKETS,
        "n_board_textures":  N_BOARD_TEXTURES,
        "n_pot_classes":     N_POT_CLASSES,
        "n_aggressor_roles": N_AGGRESSOR_ROLES,
        "n_player_buckets":  N_PLAYER_BUCKETS,
        "index_order":       "hand_bucket,board_texture,pot_class,aggressor_role,n_players_bucket",
        "hand_buckets":      HAND_BUCKETS,
        "board_textures":    BOARD_TEXTURES,
        "pot_classes":       POT_CLASSES,
        "aggressor_roles":   AGGRESSOR_ROLES,
        "n_players_values":  N_PLAYERS_BUCKETS,
        "created_at":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote manifest to {manifest_path}")
    print(f"SHA-256: {checksum}")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # Default output: artifacts/emergency/ relative to repo root.
    repo_root  = Path(__file__).parent.parent.parent
    output_dir = repo_root / "artifacts" / "emergency"

    import sys
    if len(sys.argv) > 1:
        output_dir = Path(sys.argv[1])

    print(f"Building emergency range prior ({TABLE_LEN} entries) …")
    table = build_table()
    sanity_check(table)
    write_output(table, output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
