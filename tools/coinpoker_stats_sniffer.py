"""
CoinPoker built-in HUD stats sniffer.

CoinPoker's React lobby fetches per-player VPIP/PFR/3-Bet/C-Bet from a REST
endpoint (`/v2/stats/...` based on `GET_STATS` in the app bundle, served by
one of `nxtgenapi.coinpoker.ai` / `casino2-fe.coinpoker.biz`). The patched
PBClient.dll only intercepts SFS2X cmd_bean traffic, so the wire frames in
`coinpoker_frames.jsonl` do NOT contain these stats — they come over a
separate HTTPS channel from inside the cloudfront iframe.

This tool attaches to the lobby's CDP port (the same one
`coinpoker_open_practice.py` enables), enables the Network domain on the
cloudfront iframe target, and dumps the response body of every request
whose URL contains a stats-like path. Non-invasive: doesn't write to the
DOM, doesn't click anything, doesn't restart the lobby. Multiple CDP
clients can attach to the same port concurrently, so this can run while
the runner / advisor / overlay are also live.

Output: appends one JSON object per captured response to
`C:\\Users\\Simon\\coinpoker_hud_stats.jsonl`. Each record has
``ts``, ``url``, ``request_method``, ``status``, ``body_json`` (parsed
if it parses, otherwise raw string).

Usage:
    python tools/coinpoker_stats_sniffer.py
    python tools/coinpoker_stats_sniffer.py --filter stats,/v2/,player

Once running, hover over an opponent in the CoinPoker client to trigger
the stats request — the sniffer will capture the response.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Optional

import websocket  # websocket-client

CDP_PORT = 9223
DEFAULT_OUTPUT = r"C:\Users\Simon\coinpoker_hud_stats.jsonl"
DEFAULT_FILTERS = ("stats", "/v2/", "vpip", "pfr", "playerinfo", "playerprofile")


def get_targets() -> list[dict]:
    with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=3) as r:
        return json.loads(r.read())


def pick_targets() -> list[dict]:
    """Return all attachable page+iframe targets — we attach Network to all
    so we don't miss requests if the React app moves between contexts."""
    targets = []
    for t in get_targets():
        if t.get("type") in ("page", "iframe") and t.get("webSocketDebuggerUrl"):
            targets.append(t)
    return targets


class CDPClient:
    """Minimal CDP client that supports both request/response and event
    streaming. send() blocks for a matching id; events arrive via poll()."""

    def __init__(self, ws_url: str, label: str):
        self.label = label
        self.ws = websocket.create_connection(ws_url, timeout=15)
        self.ws.settimeout(0.2)  # short for non-blocking poll
        self._id = 0
        self._pending: dict[int, dict] = {}

    def send(self, method: str, params: Optional[dict] = None, timeout: float = 8.0) -> dict:
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                raise RuntimeError(f"CDP recv failed on {self.label}: {e}")
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("id") == msg_id:
                return m
            # Buffer events for the poller
            self._pending[len(self._pending)] = m
        raise TimeoutError(f"CDP {method} on {self.label} timed out")

    def poll(self) -> list[dict]:
        """Drain everything currently readable. Returns a list of frames
        (events + late responses). Caller filters by ``method``."""
        out = list(self._pending.values())
        self._pending.clear()
        while True:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                break
            try:
                out.append(json.loads(raw))
            except Exception:
                continue
        return out

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def url_matches(url: str, filters: tuple[str, ...]) -> bool:
    u = url.lower()
    return any(f.lower() in u for f in filters)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--filter", default=",".join(DEFAULT_FILTERS),
                    help="Comma-separated substrings — capture URLs containing any of these")
    ap.add_argument("--print-all-urls", action="store_true",
                    help="Echo every URL the network domain sees, not just matches (debugging)")
    args = ap.parse_args()
    filters = tuple(f.strip() for f in args.filter.split(",") if f.strip())

    print(f"[sniffer] connecting to CDP on localhost:{CDP_PORT}")
    try:
        targets = pick_targets()
    except Exception as e:
        print(f"FATAL: cannot reach CDP: {e}")
        print("       Make sure CoinPoker was launched via tools/coinpoker_open_practice.py")
        print("       (which sets --remote-debugging-port=9223)")
        return 2
    if not targets:
        print("FATAL: no page/iframe targets found")
        return 2

    print(f"[sniffer] found {len(targets)} target(s):")
    clients: list[CDPClient] = []
    for t in targets:
        url = t.get("url", "")
        ttype = t.get("type", "?")
        print(f"  - [{ttype}] {url[:90]}")
        try:
            c = CDPClient(t["webSocketDebuggerUrl"], label=ttype)
            c.send("Network.enable", {"maxTotalBufferSize": 50_000_000,
                                       "maxResourceBufferSize": 10_000_000})
            clients.append(c)
        except Exception as e:
            print(f"    ! attach failed: {e}")
    if not clients:
        print("FATAL: could not attach Network to any target")
        return 2

    print(f"[sniffer] writing matches to {args.output}")
    print(f"[sniffer] filters: {filters}")
    print(f"[sniffer] hover an opponent in the table client to trigger stats fetches")
    print(f"[sniffer] Ctrl+C to stop")

    # Map requestId -> request info, so when responseReceived fires we can
    # pull the body via getResponseBody.
    pending_requests: dict[str, dict] = {}
    captured_count = 0

    out_file = open(args.output, "a", encoding="utf-8")

    try:
        while True:
            for c in clients:
                events = c.poll()
                for ev in events:
                    method = ev.get("method")
                    params = ev.get("params") or {}
                    if method == "Network.requestWillBeSent":
                        req = params.get("request") or {}
                        url = req.get("url", "")
                        if args.print_all_urls:
                            print(f"  REQ {req.get('method','?')} {url[:120]}")
                        if url_matches(url, filters):
                            pending_requests[params.get("requestId")] = {
                                "url": url,
                                "method": req.get("method", "GET"),
                                "ts": time.time(),
                                "client": c,
                            }
                    elif method == "Network.responseReceived":
                        rid = params.get("requestId")
                        if rid in pending_requests:
                            resp = params.get("response") or {}
                            pending_requests[rid]["status"] = resp.get("status")
                            pending_requests[rid]["mime"] = resp.get("mimeType")
                            pending_requests[rid]["headers"] = resp.get("headers", {})
                    elif method == "Network.loadingFinished":
                        rid = params.get("requestId")
                        if rid not in pending_requests:
                            continue
                        info = pending_requests.pop(rid)
                        client = info.pop("client")
                        try:
                            r = client.send("Network.getResponseBody",
                                            {"requestId": rid}, timeout=5)
                            body = (r.get("result") or {}).get("body", "")
                            if (r.get("result") or {}).get("base64Encoded"):
                                import base64
                                body = base64.b64decode(body).decode("utf-8", "ignore")
                        except Exception as e:
                            body = f"<<getResponseBody failed: {e}>>"
                        try:
                            parsed = json.loads(body)
                            info["body_json"] = parsed
                        except Exception:
                            info["body_raw"] = body[:5000]
                        out_file.write(json.dumps(info) + "\n")
                        out_file.flush()
                        captured_count += 1
                        print(f"  [+] captured {info['method']} {info['url'][:90]} "
                              f"({info.get('status','?')}) — total {captured_count}")
                    elif method == "Network.loadingFailed":
                        rid = params.get("requestId")
                        if rid in pending_requests:
                            info = pending_requests.pop(rid)
                            print(f"  [-] failed {info['url'][:90]}: "
                                  f"{params.get('errorText','?')}")
            time.sleep(0.05)
    except KeyboardInterrupt:
        print(f"\n[sniffer] stopping. captured {captured_count} responses to {args.output}")
    finally:
        out_file.close()
        for c in clients:
            c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
