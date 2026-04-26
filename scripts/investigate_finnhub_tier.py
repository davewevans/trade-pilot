"""Investigation script — Finnhub free-tier endpoint coverage check.

NOT production code. Run once to produce the Stage 4 migration report.
Delete after the report is filed.

Usage:
    python scripts/investigate_finnhub_tier.py
"""

import json
import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from config import settings

BASE = "https://finnhub.io/api/v1"
TOKEN = settings.FINNHUB_API_KEY

SYMBOLS = ["AAPL", "TSLA", "SPY", "QQQ", "XLE", "GLD"]
SECTOR_ETFS = ["XLE", "XLF", "XLK"]

PROFILE_FIELDS = ["name", "finnhubIndustry", "marketCapitalization", "country",
                  "exchange", "currency", "ipo", "shareOutstanding"]
METRIC_FIELDS = ["peBasicExclExtraTTM", "peNormalizedAnnual",
                 "dividendYieldIndicatedAnnual", "52WeekHigh", "52WeekLow",
                 "marketCapitalization"]


def fetch_profile2(symbol):
    r = requests.get(
        f"{BASE}/stock/profile2",
        params={"symbol": symbol, "token": TOKEN},
        timeout=10,
    )
    return r.status_code, (r.json() if r.status_code == 200 else r.text)


def fetch_metric(symbol):
    r = requests.get(
        f"{BASE}/stock/metric",
        params={"symbol": symbol, "metric": "all", "token": TOKEN},
        timeout=10,
    )
    return r.status_code, (r.json() if r.status_code == 200 else r.text)


def main():
    print("=" * 70)
    print("  Finnhub free-tier investigation")
    print(f"  Symbols: {SYMBOLS}")
    print("=" * 70)

    profile_results = {}
    metric_results = {}

    for sym in SYMBOLS:
        print(f"\n--- {sym} ---")

        status, data = fetch_profile2(sym)
        profile_results[sym] = {"status": status, "data": data}
        if status == 200:
            for f in PROFILE_FIELDS:
                print(f"  profile2.{f}: {data.get(f)!r}")
        else:
            print(f"  profile2 HTTP {status}: {data!r}")
        time.sleep(1.5)

        status, data = fetch_metric(sym)
        metric_results[sym] = {"status": status, "data": data}
        if status == 200:
            metric = data.get("metric", {}) if isinstance(data, dict) else {}
            for f in METRIC_FIELDS:
                print(f"  metric.{f}: {metric.get(f)!r}")
            print(f"  metric total field count: {len(metric)}")
            # Sample a few non-target fields to distinguish free vs full
            sample = {k: v for k, v in list(metric.items())[:8]}
            print(f"  metric sample keys: {list(sample.keys())}")
        else:
            print(f"  metric HTTP {status}: {data!r}")
        time.sleep(1.5)

    # Extra sector ETF check
    print("\n" + "=" * 70)
    print("  Sector ETF finnhubIndustry check")
    print("=" * 70)
    for sym in SECTOR_ETFS:
        if sym in profile_results:
            d = profile_results[sym].get("data", {})
            print(f"  {sym}: finnhubIndustry={d.get('finnhubIndustry')!r}  name={d.get('name')!r}")
        else:
            status, data = fetch_profile2(sym)
            if status == 200:
                print(f"  {sym}: finnhubIndustry={data.get('finnhubIndustry')!r}  name={data.get('name')!r}")
            else:
                print(f"  {sym}: HTTP {status}")
            time.sleep(1.5)

    print("\n" + "=" * 70)
    print("  Raw JSON dump (for report)")
    print("=" * 70)
    print("\n--- profile2 raw ---")
    for sym, r in profile_results.items():
        if r["status"] == 200:
            print(f"{sym}: {json.dumps(r['data'], indent=2)}")
    print("\n--- metric raw (metric sub-dict only) ---")
    for sym, r in metric_results.items():
        if r["status"] == 200 and isinstance(r["data"], dict):
            print(f"{sym}: {json.dumps(r['data'].get('metric', {}), indent=2)}")


if __name__ == "__main__":
    main()
