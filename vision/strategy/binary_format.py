"""
Memory-mapped binary strategy reader.

Loads a CFR1 binary file via mmap for near-zero memory overhead.
Binary search lookup: O(log n), <1 microsecond per query.

Usage:
    from strategy.binary_format import MmapStrategy
    strat = MmapStrategy("models/flop_cfr_strategy.bin")
    probs = strat.lookup("FLOP:34:s1:IP:SRP:kbt")
    # -> {'FOLD': 0.12, 'CALL': 0.88} or None
"""

import mmap
import struct
import os

# Fixed action order — must match export-binary.js
ACTION_NAMES = [
    "FOLD", "CHECK", "CALL",
    "BET_33", "BET_66", "BET_POT", "BET_ALLIN",
    "RAISE_HALF", "RAISE_POT", "RAISE_ALLIN",
    "BET_HALF",
]


def _fnv1a64(s: str) -> int:
    """FNV-1a 64-bit hash, matching the JS implementation."""
    h = 0xcbf29ce484222325
    prime = 0x100000001b3
    mask = 0xFFFFFFFFFFFFFFFF
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * prime) & mask
    return h


class MmapStrategy:
    """Memory-mapped binary strategy for fast, low-memory lookups."""

    def __init__(self, bin_path: str):
        if not os.path.exists(bin_path):
            raise FileNotFoundError(f"Strategy file not found: {bin_path}")

        self._file = open(bin_path, "rb")
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)

        # Read header
        magic = self._mm[0:4]
        if magic != b"CFR1":
            raise ValueError(f"Invalid magic: {magic!r}, expected b'CFR1'")

        self.version = struct.unpack_from("<I", self._mm, 4)[0]
        self.num_entries = struct.unpack_from("<I", self._mm, 8)[0]
        self.num_actions = struct.unpack_from("<I", self._mm, 12)[0]
        self.bucket_count = struct.unpack_from("<I", self._mm, 16)[0]

        self._header_size = 32
        self._index_entry_size = 12
        self._data_entry_size = self.num_actions * 4
        self._index_start = self._header_size
        self._data_start = self._index_start + self.num_entries * self._index_entry_size

    def lookup(self, info_set_key: str) -> dict | None:
        """
        Binary search for info set key.
        Returns dict of action -> probability (only actions with prob > 0.001).
        Returns None if key not found.
        """
        target = _fnv1a64(info_set_key)
        lo, hi = 0, self.num_entries - 1

        while lo <= hi:
            mid = (lo + hi) // 2
            offset = self._index_start + mid * self._index_entry_size
            entry_hash = struct.unpack_from("<Q", self._mm, offset)[0]

            if entry_hash == target:
                data_rel_offset = struct.unpack_from("<I", self._mm, offset + 8)[0]
                abs_offset = self._data_start + data_rel_offset
                probs = struct.unpack_from(
                    f"<{self.num_actions}f", self._mm, abs_offset
                )
                return {
                    ACTION_NAMES[i]: probs[i]
                    for i in range(min(self.num_actions, len(ACTION_NAMES)))
                    if probs[i] > 0.001
                }
            elif entry_hash < target:
                lo = mid + 1
            else:
                hi = mid - 1

        return None

    def lookup_fuzzy(self, street: str, bucket: int, stack_bucket: int,
                     pos: str, pot_class: str, history: str,
                     max_bucket_delta: int = 5) -> tuple[dict | None, str | None]:
        """
        Try exact lookup, then nearby buckets.
        Returns (probs_dict, matched_key) or (None, None).
        """
        key = f"{street}:{bucket}:s{stack_bucket}:{pos}:{pot_class}:{history}"
        result = self.lookup(key)
        if result is not None:
            return result, key

        for delta in range(1, max_bucket_delta + 1):
            for d in (delta, -delta):
                b = bucket + d
                if b < 0 or b >= self.bucket_count:
                    continue
                k = f"{street}:{b}:s{stack_bucket}:{pos}:{pot_class}:{history}"
                result = self.lookup(k)
                if result is not None:
                    return result, k

        return None, None

    @property
    def size_bytes(self):
        """Total file size."""
        return len(self._mm)

    def close(self):
        self._mm.close()
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self):
        return (f"MmapStrategy(entries={self.num_entries}, actions={self.num_actions}, "
                f"buckets={self.bucket_count}, size={self.size_bytes / 1024:.1f}KB)")
