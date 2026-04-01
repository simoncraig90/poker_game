"""
Betfair Exchange API client.

Handles authentication, market discovery, odds retrieval, and bet placement.
Uses the Betfair Exchange API (JSON-RPC via APING).

Setup:
  1. Create a Betfair account at betfair.com
  2. Get an API app key: https://docs.developer.betfair.com/display/1smk3cen4v3lu3yomq5qye0ni/Application+Keys
  3. Generate SSL certs for non-interactive login (recommended)
  4. Copy betfair/.env.example to betfair/.env and fill in credentials
"""

import os
import json
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

IDENTITY_URL = "https://identitysso-cert.betfair.com/api/certlogin"
API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"
ACCOUNT_URL = "https://api.betfair.com/exchange/account/json-rpc/v1"


class BetfairClient:
    def __init__(self):
        self.app_key = os.getenv("BETFAIR_APP_KEY")
        self.username = os.getenv("BETFAIR_USERNAME")
        self.password = os.getenv("BETFAIR_PASSWORD")
        self.cert_path = os.getenv("BETFAIR_CERT_PATH", "betfair/certs/client-2048.crt")
        self.key_path = os.getenv("BETFAIR_KEY_PATH", "betfair/certs/client-2048.key")
        self.session_token = None

    def login(self):
        """Authenticate via certificate-based login."""
        resp = requests.post(
            IDENTITY_URL,
            data={"username": self.username, "password": self.password},
            cert=(self.cert_path, self.key_path),
            headers={"X-Application": self.app_key},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("loginStatus") != "SUCCESS":
            raise RuntimeError(f"Betfair login failed: {data}")
        self.session_token = data["sessionToken"]
        return self.session_token

    def _headers(self):
        return {
            "X-Application": self.app_key,
            "X-Authentication": self.session_token,
            "Content-Type": "application/json",
        }

    def _call(self, method, params=None):
        """Make an API call to the Betfair exchange."""
        payload = {
            "jsonrpc": "2.0",
            "method": f"SportsAPING/v1.0/{method}",
            "params": params or {},
            "id": 1,
        }
        resp = requests.post(API_URL, json=payload, headers=self._headers())
        resp.raise_for_status()
        result = resp.json()
        if "error" in result:
            raise RuntimeError(f"API error: {result['error']}")
        return result.get("result")

    # ── Market discovery ──

    def list_event_types(self):
        """List all sports/event types available."""
        return self._call("listEventTypes", {"filter": {}})

    def list_events(self, event_type_id, market_start_time=None):
        """List events for a given sport."""
        filt = {"eventTypeIds": [event_type_id]}
        if market_start_time:
            filt["marketStartTime"] = market_start_time
        return self._call("listEvents", {"filter": filt})

    def list_markets(self, event_id, market_types=None):
        """List markets for a specific event."""
        filt = {"eventIds": [event_id]}
        if market_types:
            filt["marketTypeCodes"] = market_types
        return self._call(
            "listMarketCatalogue",
            {
                "filter": filt,
                "maxResults": "100",
                "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_START_TIME"],
            },
        )

    def get_market_book(self, market_ids, price_projection=None):
        """Get live odds/prices for markets."""
        if price_projection is None:
            price_projection = {
                "priceData": ["EX_BEST_OFFERS", "EX_TRADED"],
                "virtualise": True,
            }
        return self._call(
            "listMarketBook",
            {"marketIds": market_ids, "priceProjection": price_projection},
        )

    # ── Bet placement ──

    def place_bet(self, market_id, selection_id, side, price, size):
        """
        Place a single bet.

        Args:
            market_id: Betfair market ID
            selection_id: Runner/selection ID
            side: 'BACK' or 'LAY'
            price: Decimal odds (e.g. 2.5)
            size: Stake in account currency
        """
        instruction = {
            "orderType": "LIMIT",
            "selectionId": selection_id,
            "side": side,
            "limitOrder": {"size": str(size), "price": str(price), "persistenceType": "LAPSE"},
        }
        return self._call(
            "placeOrders",
            {"marketId": market_id, "instructions": [instruction]},
        )

    def get_balance(self):
        """Get account balance."""
        payload = {
            "jsonrpc": "2.0",
            "method": "AccountAPING/v1.0/getAccountFunds",
            "params": {},
            "id": 1,
        }
        resp = requests.post(ACCOUNT_URL, json=payload, headers=self._headers())
        resp.raise_for_status()
        return resp.json().get("result")


if __name__ == "__main__":
    client = BetfairClient()
    client.login()
    print("Logged in successfully")

    # List available sports
    sports = client.list_event_types()
    for sport in sorted(sports, key=lambda x: x["marketCount"], reverse=True):
        et = sport["eventType"]
        print(f"  {et['id']:>4}  {et['name']:<30} {sport['marketCount']} markets")
