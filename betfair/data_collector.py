"""
Historical data collector for Betfair markets.

Records odds movements and results for model training.
Same pattern as the poker hand history pipeline — collect data,
store as JSONL, use for training.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from client import BetfairClient

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def collect_market_snapshot(client, market_ids):
    """Take a snapshot of current odds for given markets."""
    books = client.get_market_book(market_ids)
    timestamp = datetime.utcnow().isoformat()

    snapshots = []
    for book in books:
        snapshot = {
            "timestamp": timestamp,
            "market_id": book["marketId"],
            "status": book.get("status"),
            "inplay": book.get("inplay", False),
            "total_matched": book.get("totalMatched", 0),
            "runners": [],
        }

        for runner in book.get("runners", []):
            back = runner.get("ex", {}).get("availableToBack", [])
            lay = runner.get("ex", {}).get("availableToLay", [])

            snapshot["runners"].append({
                "selection_id": runner["selectionId"],
                "status": runner.get("status"),
                "last_price_traded": runner.get("lastPriceTraded"),
                "total_matched": runner.get("totalMatched", 0),
                "best_back": back[0]["price"] if back else None,
                "best_back_size": back[0]["size"] if back else None,
                "best_lay": lay[0]["price"] if lay else None,
                "best_lay_size": lay[0]["size"] if lay else None,
            })

        snapshots.append(snapshot)

    return snapshots


def record_market(client, market_ids, output_file, interval_secs=60, duration_mins=180):
    """
    Record odds movements for a set of markets over time.

    Args:
        client: Authenticated BetfairClient
        market_ids: List of market IDs to track
        output_file: Path to JSONL output file
        interval_secs: Seconds between snapshots
        duration_mins: Total recording duration in minutes
    """
    output_path = DATA_DIR / output_file
    end_time = time.time() + (duration_mins * 60)
    snapshot_count = 0

    print(f"Recording {len(market_ids)} markets every {interval_secs}s for {duration_mins}min")
    print(f"Output: {output_path}")

    with open(output_path, "a") as f:
        while time.time() < end_time:
            try:
                snapshots = collect_market_snapshot(client, market_ids)
                for snap in snapshots:
                    f.write(json.dumps(snap) + "\n")
                snapshot_count += len(snapshots)

                # Stop if all markets are closed
                all_closed = all(s["status"] == "CLOSED" for s in snapshots)
                if all_closed:
                    print("All markets closed.")
                    break

                print(f"  {snapshot_count} snapshots recorded", end="\r")
                time.sleep(interval_secs)

            except Exception as e:
                print(f"Error: {e}")
                time.sleep(interval_secs)

    print(f"\nDone. {snapshot_count} total snapshots saved to {output_path}")


def collect_results(client, market_ids):
    """
    Collect final results for settled markets.

    Returns list of {market_id, winner_selection_id, settled_time}
    """
    books = client.get_market_book(market_ids)
    results = []

    for book in books:
        if book.get("status") != "CLOSED":
            continue

        for runner in book.get("runners", []):
            if runner.get("status") == "WINNER":
                results.append({
                    "market_id": book["marketId"],
                    "winner_selection_id": runner["selectionId"],
                })

    return results


if __name__ == "__main__":
    client = BetfairClient()
    client.login()

    # Example: record today's football match odds markets
    # 1 = Football event type on Betfair
    events = client.list_events("1")
    print(f"Found {len(events)} football events")

    if events:
        # Get markets for first event
        event_id = events[0]["event"]["id"]
        event_name = events[0]["event"]["name"]
        print(f"Recording: {event_name}")

        markets = client.list_markets(event_id, ["MATCH_ODDS"])
        market_ids = [m["marketId"] for m in markets]

        if market_ids:
            record_market(client, market_ids, f"football_{event_id}.jsonl")
