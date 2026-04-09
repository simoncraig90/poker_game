"""Static inspection of ACR installer — strings, sections, embedded markers."""
import re
import sys
from pathlib import Path

PATH = Path(r"C:\poker-research\sandbox\acr\SetupACR.exe")
data = PATH.read_bytes()
print(f"size: {len(data):,} bytes")
print(f"PE? {data[:2] == b'MZ'}")

# Quick PE header parse for arch + sections count
import struct
e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
sig = data[e_lfanew:e_lfanew+4]
print(f"PE sig: {sig}")
machine = struct.unpack_from("<H", data, e_lfanew + 4)[0]
n_sections = struct.unpack_from("<H", data, e_lfanew + 6)[0]
print(f"machine: {hex(machine)} ({'x64' if machine == 0x8664 else 'x86' if machine == 0x14c else 'other'})")
print(f"sections: {n_sections}")

# Extract printable ASCII strings >= 6 chars
def strings(buf, min_len=6):
    out = []
    cur = bytearray()
    for b in buf:
        if 0x20 <= b < 0x7f:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append(cur.decode("ascii", "ignore"))
            cur = bytearray()
    if len(cur) >= min_len:
        out.append(cur.decode("ascii", "ignore"))
    return out

# Also UTF-16 LE strings (common in Windows binaries)
def utf16_strings(buf, min_len=6):
    out = []
    cur = []
    i = 0
    while i < len(buf) - 1:
        b1, b2 = buf[i], buf[i+1]
        if b2 == 0 and 0x20 <= b1 < 0x7f:
            cur.append(chr(b1))
            i += 2
        else:
            if len(cur) >= min_len:
                out.append("".join(cur))
            cur = []
            i += 1
    if len(cur) >= min_len:
        out.append("".join(cur))
    return out

print("\nextracting strings...")
ascii_str = strings(data)
utf16_str = utf16_strings(data)
print(f"  ascii: {len(ascii_str):,}  utf16: {len(utf16_str):,}")
all_str = ascii_str + utf16_str

# Patterns of interest
patterns = {
    "URLs":           r"https?://[A-Za-z0-9._/\-?=&%#]+",
    "domains":        r"\b[a-z0-9-]+\.(com|net|eu|io|gg|cc|tv|app|cloud|xyz)\b",
    "anti_cheat":     r"(?i)(easyanticheat|battleeye|battl?eye|vanguard|gameguard|xigncode|secorock|denuvo|themida|vmprotect|enigma|ironvest|sentry|raven|hawkeye)",
    "browsers":       r"(?i)(chromium|electron|cef|webview2|chrome\.exe|chromedriver)",
    "frameworks":     r"(?i)(adobe air\b|qt[0-9]|wxwidgets|electron|node\.js|node_modules|nw\.js|tauri|wasm|flash player)",
    "telemetry":      r"(?i)(telemetry|analytics|amplitude|mixpanel|segment|google.analytics|sentry|datadog|appsflyer|adjust)",
    "anti_debug":     r"(?i)(IsDebuggerPresent|CheckRemoteDebuggerPresent|NtQueryInformationProcess|OutputDebugString|GetTickCount|QueryPerformanceCounter|rdtsc|VirtualProtect)",
    "wpn_specific":   r"(?i)(winning poker|wpn|americas cardroom|acrpoker|lunar software|black chip|truepoker|yapoker|pokerking)",
    "interest_dlls":  r"(?i)([A-Za-z0-9_]+\.dll)",
    "version_strs":   r"\b\d+\.\d+\.\d+(?:\.\d+)?\b",
}

import collections
findings = collections.defaultdict(list)
for s in all_str:
    for name, pat in patterns.items():
        m = re.findall(pat, s)
        if m:
            for hit in m:
                hit_str = hit if isinstance(hit, str) else " ".join(hit)
                findings[name].append((hit_str, s[:200]))

for name, hits in findings.items():
    if not hits:
        continue
    seen = set()
    uniq = []
    for k, ctx in hits:
        if k.lower() in seen:
            continue
        seen.add(k.lower())
        uniq.append((k, ctx))
    print(f"\n=== {name} ({len(uniq)} unique) ===")
    for k, ctx in uniq[:40]:
        print(f"  {k}")
