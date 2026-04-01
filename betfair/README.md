# Betfair Edge Lab

Sports betting value finder using the Betfair Exchange API. Built on the same ML pipeline patterns as the poker vision system.

## Architecture

```
betfair/
├── client.py          # Betfair Exchange API client (auth, markets, bets)
├── data_collector.py  # Record odds movements & results for training
├── model.py           # PyTorch prediction model (same pattern as hand_strength_net)
├── value_finder.py    # Compare model probs vs market odds, Kelly sizing
├── models/            # Trained model weights
├── data/              # Historical odds & results (JSONL)
└── scripts/           # Sport-specific feature engineering & runners
```

## Workflow

1. **Collect data** — `data_collector.py` records live odds movements to JSONL
2. **Feature engineer** — Add sport-specific `prepare_features()` in `model.py`
3. **Train model** — Train `PredictionNet` on historical features + results
4. **Find value** — `value_finder.py` compares model output vs live market odds
5. **Bet** — Place bets via `client.py` when edge exceeds threshold

## Setup

1. Create Betfair account + API app key
2. Generate SSL certs for non-interactive login
3. `cp .env.example .env` and fill in credentials
4. `pip install requests python-dotenv torch`

## Key Concepts

- **Edge** = model probability - implied probability (same as poker equity vs pot odds)
- **Kelly criterion** = optimal bet sizing (same bankroll math as poker)
- **Back** = bet for an outcome / **Lay** = bet against (unique to exchanges)
- **Quarter Kelly** used by default for safety (conservative sizing)
