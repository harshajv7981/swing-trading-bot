"""Momentum across the whole NSE market (docs/research/momentum-whole-market-prereg.md).

    python scripts/research/momentum_whole_market_study.py data/raw/eod2_data/daily

Runs momentum_broad_study.py's code unchanged, on new groups: W1 every qualifying stock, W2 the top 500,
and today's mid group as the reference.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import momentum_broad_study as broad  # noqa: E402

broad.BUCKETS = {"mid (reference)": (100, 250, 0.0040), "W1 whole market": (0, None, 0.0050),
                 "W2 top 500": (0, 500, 0.0040)}

if __name__ == "__main__":
    broad.main()
