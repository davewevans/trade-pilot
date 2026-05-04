"""Market data retrieval from broker APIs.

Uses the alpaca-py SDK to fetch real-time and historical options data.
The data endpoints (data.alpaca.markets) are the same for paper and live
accounts, so no paper flag is needed on the data clients.
"""

import logging
import time
from datetime import datetime, timedelta

import requests as _requests
import numpy as np
import ta
import pandas as pd
import yfinance as yf
from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.live import OptionDataStream
from alpaca.data.enums import DataFeed
from alpaca.data.requests import (
    OptionChainRequest,
    OptionLatestQuoteRequest,
    OptionSnapshotRequest,
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame

from config import settings
from utils.retry import retry_on_transient

logger = logging.getLogger(__name__)

_risk_free_rate_cache: float | None = None
_risk_free_rate_timestamp: float = 0.0
_RISK_FREE_RATE_TTL = 4 * 3600  # 4 hours in seconds

_fear_greed_cache: dict | None = None
_fear_greed_timestamp: float = 0.0
_FEAR_GREED_TTL = 3600  # 1 hour in seconds


_option_client = OptionHistoricalDataClient(
    api_key=settings.ALPACA_PAPER1_API_KEY,
    secret_key=settings.ALPACA_PAPER1_SECRET_KEY,
)

_stock_client = StockHistoricalDataClient(
    api_key=settings.ALPACA_PAPER1_API_KEY,
    secret_key=settings.ALPACA_PAPER1_SECRET_KEY,
)


def _snapshot_to_dict(snapshot) -> dict:
    """Convert an OptionsSnapshot model to a plain dict."""
    data: dict = {
        "symbol": snapshot.symbol,
        "implied_volatility": snapshot.implied_volatility,
    }

    if snapshot.latest_quote:
        q = snapshot.latest_quote
        data["latest_quote"] = {
            "bid_price": q.bid_price,
            "bid_size": q.bid_size,
            "ask_price": q.ask_price,
            "ask_size": q.ask_size,
            "timestamp": str(q.timestamp),
        }

    if snapshot.latest_trade:
        t = snapshot.latest_trade
        data["latest_trade"] = {
            "price": t.price,
            "size": t.size,
            "timestamp": str(t.timestamp),
        }

    if snapshot.greeks:
        g = snapshot.greeks
        data["greeks"] = {
            "delta": g.delta,
            "gamma": g.gamma,
            "theta": g.theta,
            "vega": g.vega,
            "rho": g.rho,
        }

    return data


_SNAPSHOT_BATCH_SIZE = 50


def get_option_snapshot(symbols: list[str]) -> dict:
    """Return the latest snapshot for given option symbols.

    Each snapshot includes: latest quote (bid/ask), latest trade,
    greeks (delta, gamma, theta, vega, rho), and implied volatility.

    Symbols are chunked into batches of 50 to avoid HTTP 400 errors from
    URL length limits.  A batch failure is logged and skipped.

    Args:
        symbols: OCC option symbols (e.g. ["AAPL240119C00190000"]).

    Returns:
        Dict keyed by symbol, each value containing snapshot data.
    """
    result: dict = {}
    if not symbols:
        return result

    batches = [
        symbols[i:i + _SNAPSHOT_BATCH_SIZE]
        for i in range(0, len(symbols), _SNAPSHOT_BATCH_SIZE)
    ]
    logger.info(
        "Fetching option snapshots: total_symbols=%d batch_count=%d",
        len(symbols), len(batches),
    )

    for batch in batches:
        try:
            request = OptionSnapshotRequest(symbol_or_symbols=batch)
            snapshots = _option_client.get_option_snapshot(request)
            result.update({sym: _snapshot_to_dict(snap) for sym, snap in snapshots.items()})
        except Exception:
            logger.exception(
                "Failed to fetch option snapshots for batch of %d symbols", len(batch)
            )
            continue

    return result


def get_option_chain(
    underlying_symbol: str, expiration_date: str | None = None
) -> dict:
    """Return the full option chain snapshot for an underlying symbol.

    Args:
        underlying_symbol: Ticker symbol (e.g. "AAPL").
        expiration_date: Optional filter by expiration (YYYY-MM-DD).

    Returns:
        Dict keyed by OCC symbol, each value containing snapshot data.
    """
    params: dict = {"underlying_symbol": underlying_symbol}
    if expiration_date is not None:
        params["expiration_date"] = expiration_date

    request = OptionChainRequest(**params)
    snapshots = _option_client.get_option_chain(request)
    return {sym: _snapshot_to_dict(snap) for sym, snap in snapshots.items()}


def get_option_latest_quote(symbols: list[str]) -> dict:
    """Return the latest bid/ask quote for given option symbols.

    Args:
        symbols: OCC option symbols (e.g. ["AAPL240119C00190000"]).

    Returns:
        Dict keyed by symbol, each value containing quote data.
    """
    request = OptionLatestQuoteRequest(symbol_or_symbols=symbols)
    quotes = _option_client.get_option_latest_quote(request)
    return {
        sym: {
            "bid_price": q.bid_price,
            "bid_size": q.bid_size,
            "ask_price": q.ask_price,
            "ask_size": q.ask_size,
            "timestamp": str(q.timestamp),
        }
        for sym, q in quotes.items()
    }


def start_option_stream(symbols: list[str], on_quote, on_trade) -> None:
    """Start a real-time WebSocket stream for the given option symbols.

    **This call blocks.** Run it in a separate thread or async context.

    Args:
        symbols: OCC option symbols to subscribe to.
        on_quote: Async callback ``async def(quote) -> None`` for quote updates.
        on_trade: Async callback ``async def(trade) -> None`` for trade updates.
    """
    stream = OptionDataStream(
        api_key=settings.ALPACA_PAPER1_API_KEY,
        secret_key=settings.ALPACA_PAPER1_SECRET_KEY,
    )
    stream.subscribe_quotes(on_quote, *symbols)
    stream.subscribe_trades(on_trade, *symbols)
    logger.info("Starting option stream for %s", symbols)
    stream.run()


# ── VIX proxy ───────────────────────────────────────────────

_VIX_CACHE: dict = {"value": None, "fetched_at": None}


def _is_market_hours() -> bool:
    """Return True if current ET time falls within regular trading hours."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def get_vix_with_age(fresh_vix: "float | None") -> dict:
    """Apply stale-tolerance logic to an already-fetched VIX value.

    Call this after get_vix() resolves (e.g. after the parallel-fetch pool).
    Updates the in-memory cache on a fresh fetch. When VIX_STALE_TOLERANCE_ENABLED
    is true and the fresh fetch failed, returns a cached value if it is still
    within the configured age window.

    Returns {"value": float|None, "age_seconds": int|None, "stale": bool}.
    """
    from config import settings
    now = time.time()

    if fresh_vix is not None:
        _VIX_CACHE["value"] = fresh_vix
        _VIX_CACHE["fetched_at"] = now
        return {"value": fresh_vix, "age_seconds": 0, "stale": False}

    # Fresh fetch failed.
    if not settings.VIX_STALE_TOLERANCE_ENABLED:
        return {"value": None, "age_seconds": None, "stale": True}

    cached_value = _VIX_CACHE["value"]
    cached_at = _VIX_CACHE["fetched_at"]
    if cached_value is None or cached_at is None:
        return {"value": None, "age_seconds": None, "stale": True}

    age = int(now - cached_at)
    max_age = (
        settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS if _is_market_hours()
        else settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS
    )
    if age > max_age:
        return {"value": None, "age_seconds": age, "stale": True}
    return {"value": cached_value, "age_seconds": age, "stale": True}


@retry_on_transient(max_retries=2, base_delay=1.0)
def _fetch_vix_from_fred() -> float:
    """Fetch VIX from FRED VIXCLS; retried by caller on transient errors."""
    from fredapi import Fred
    fred = Fred(api_key=settings.FRED_API_KEY)
    series = fred.get_series("VIXCLS")
    return float(series.dropna().iloc[-1])


def get_vix() -> float | None:
    """Return the current VIX index value.

    Primary source: FRED VIXCLS series (CBOE Volatility Index daily close).
    Fallback: yfinance ^VIX last_price.

    Note: FRED VIXCLS is a daily series. During market hours this returns
    yesterday's official close. yfinance ^VIX gave intraday spot; FRED does
    not. Acceptable for trade-pilot's main entry cycles (pre_market and
    market_open run before/at market open) but a freshness regression for
    intraday position_check cycles. Mitigation: regime stability filter
    requires 3 consecutive readings before flipping, so single-day VIX
    spikes do not silently change strategy routing.

    Returns None on total failure.
    """
    if settings.USE_FRED_FOR_VIX:
        try:
            vix = _fetch_vix_from_fred()
            logger.info("Fetched VIX from FRED VIXCLS: %.2f", vix)
            return vix
        except Exception:
            logger.warning(
                "Failed to fetch VIX from FRED VIXCLS; falling back to yfinance",
                exc_info=True,
            )

    try:
        vix_data = yf.Ticker("^VIX").fast_info
        vix = float(vix_data["last_price"])
        logger.info("Fetched ^VIX via yfinance fallback: %.2f", vix)
        return vix
    except Exception:
        logger.warning("Failed to fetch ^VIX via yfinance fallback", exc_info=True)
        return None


def interpret_vix(vix: float) -> str:
    """Classify a VIX-proxy value into a volatility regime."""
    if vix < 15:
        return "low"
    if vix < 25:
        return "normal"
    if vix < 35:
        return "elevated"
    return "extreme"


# ── Ex-dividend ────────────────────────────────────────────

_exdiv_cache: dict[str, tuple[float, dict]] = {}
_EXDIV_TTL = 12 * 3600


@retry_on_transient(max_retries=2, base_delay=1.0)
def _get_ex_dividend_alpaca(symbol: str) -> dict | None:
    """Fetch next ex-dividend via Alpaca Corporate Actions API.

    Returns the contract dict on success (including the all-None case where
    the symbol has no upcoming dividend — valid data, e.g. GLD). Raises on
    failure so the caller's retry wrapper can handle retries before falling
    back to yfinance.
    """
    from datetime import date as _date

    today = _date.today()
    end = today + timedelta(days=180)

    resp = _requests.get(
        f"{settings.ALPACA_DATA_URL}/v1/corporate-actions",
        headers={
            "APCA-API-KEY-ID": settings.ALPACA_PAPER1_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_PAPER1_SECRET_KEY,
        },
        params={
            "symbols": symbol,
            "types": "cash_dividend",
            "start": today.isoformat(),
            "end": end.isoformat(),
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    cash_divs = (
        payload.get("corporate_actions", {}).get("cash_dividends", []) or []
    )

    today_iso = today.isoformat()
    upcoming = sorted(
        [d for d in cash_divs if (d.get("ex_date") or "") >= today_iso],
        key=lambda d: d["ex_date"],
    )

    if not upcoming:
        logger.info("Alpaca: no upcoming dividend for %s within 180 days", symbol)
        return {
            "next_ex_dividend_date": None,
            "days_to_ex_dividend": None,
            "annual_dividend_yield": None,
            "ex_dividend_data_available": True,
        }

    next_ev = upcoming[0]
    ex_date_str = next_ev["ex_date"]
    days = (_date.fromisoformat(ex_date_str) - today).days
    logger.info("Alpaca: next ex-dividend for %s on %s (%d days)", symbol, ex_date_str, days)
    return {
        "next_ex_dividend_date": ex_date_str,
        "days_to_ex_dividend": days,
        # Annual yield requires a price fetch — deferred to Stage 4 (Finnhub fundamentals).
        "annual_dividend_yield": None,
        "ex_dividend_data_available": True,
    }


def _get_ex_dividend_yfinance(symbol: str) -> dict:
    """Fetch next ex-dividend via yfinance .info — legacy fallback path.

    Always returns the contract dict. Returns all-None fields when yfinance
    has no data, when the symbol is an ETF with broken quoteSummary coverage,
    or on any error.
    """
    try:
        info = yf.Ticker(symbol).info or {}

        ex_date_ts = info.get("exDividendDate")
        if ex_date_ts is None:
            return {
                "next_ex_dividend_date": None,
                "days_to_ex_dividend": None,
                "annual_dividend_yield": None,
                "ex_dividend_data_available": True,
            }

        ex_date = pd.Timestamp(ex_date_ts, unit="s").date()
        today = datetime.now().date()
        days = (ex_date - today).days
        raw_yield = info.get("dividendYield")
        return {
            "next_ex_dividend_date": str(ex_date) if ex_date >= today else None,
            "days_to_ex_dividend": days if days >= 0 else None,
            "annual_dividend_yield": round(float(raw_yield), 4) if raw_yield is not None else None,
            "ex_dividend_data_available": True,
        }
    except Exception:
        logger.warning("yfinance ex-dividend fetch failed for %s", symbol, exc_info=True)
        return {
            "next_ex_dividend_date": None,
            "days_to_ex_dividend": None,
            "annual_dividend_yield": None,
            "ex_dividend_data_available": False,
        }


def get_ex_dividend_date(symbol: str) -> dict:
    """Return the next ex-dividend date and yield for a symbol.

    Primary source: Alpaca Corporate Actions API (types=cash_dividend).
    Fallback: yfinance .info exDividendDate.

    Returns a dict with keys:
        next_ex_dividend_date: ISO date string "YYYY-MM-DD" or None.
        days_to_ex_dividend:   int or None (relative to today).
        annual_dividend_yield: float or None. None when sourced from Alpaca
                               (yield calc deferred to Stage 4 / Finnhub).
        ex_dividend_data_available: True on success (including no-dividend
                               case); False only when yfinance raises.

    Cache: 12 hours per symbol.
    """
    global _exdiv_cache

    cached = _exdiv_cache.get(symbol)
    if cached is not None and (time.monotonic() - cached[0]) < _EXDIV_TTL:
        return cached[1]

    if settings.USE_ALPACA_FOR_EX_DIVIDEND:
        try:
            result = _get_ex_dividend_alpaca(symbol)
        except Exception:
            logger.warning(
                "Alpaca corporate actions fetch failed for %s; will fall back to yfinance",
                symbol, exc_info=True,
            )
            result = None
        if result is not None:
            _exdiv_cache[symbol] = (time.monotonic(), result)
            return result
        # Alpaca hard error — fall through to yfinance.

    result = _get_ex_dividend_yfinance(symbol)
    _exdiv_cache[symbol] = (time.monotonic(), result)
    return result


# ── ORATS + Finnhub integration ────────────────────────────


def get_orats_summary(symbol: str) -> dict | None:
    """Return ORATS /summaries data for a symbol.

    NOTE: /summaries does NOT return iv_rank_1y, iv_rank_1m, iv_pct_1y,
    or iv_pct_1m. Those fields live on /ivrank — use get_orats_iv_rank()
    or get_orats_iv_rank_batch() for IV rank data.

    See https://docs.orats.io/datav2-api-guide/data.html#summaries for the
    actual /summaries response shape (stockPrice, tradeDate, impliedMove,
    iv20d/iv30d/iv60d/iv90d term-structure IVs, dividend & borrow fields).
    """
    if not settings.ORATS_API_KEY:
        logger.warning("ORATS_API_KEY not set — IV analytics unavailable")
        return None
    try:
        from data.orats_client import ORATSClient

        return ORATSClient().get_summary(symbol)
    except Exception:
        logger.warning("ORATS summary failed for %s", symbol, exc_info=True)
        return None


def get_orats_cores(symbol: str) -> dict | None:
    """Return extracted ORATS /cores analytics for a symbol."""
    if not settings.ORATS_API_KEY:
        return None
    try:
        from data.orats_client import ORATSClient

        return ORATSClient().get_cores(symbol)
    except Exception:
        logger.warning("ORATS cores failed for %s", symbol, exc_info=True)
        return None


def get_orats_monies(symbol: str) -> list[dict]:
    """Return ORATS implied monies (vol smile) for a symbol.

    Each row covers one expiration and contains vol5…vol100 — the
    smoothed IV at standardized delta levels.  vol100 ≈ ATM;
    vol30 = 30-delta put; vol5 = 5-delta put.

    Returns an empty list when ORATS is unavailable.
    """
    if not settings.ORATS_API_KEY:
        return []
    try:
        from data.orats_client import ORATSClient

        return ORATSClient().get_monies(symbol)
    except Exception:
        logger.warning("ORATS monies failed for %s", symbol, exc_info=True)
        return []


def get_orats_iv_rank_batch(symbols: list[str]) -> dict[str, dict]:
    """Return ORATS IV rank/percentile for a batch of tickers."""
    if not settings.ORATS_API_KEY or not symbols:
        return {}
    try:
        from data.orats_client import ORATSClient

        return ORATSClient().get_iv_rank_batch(symbols)
    except Exception:
        logger.warning("ORATS iv_rank batch failed", exc_info=True)
        return {}


def get_orats_iv_rank(symbol: str) -> dict | None:
    """Return ORATS IV rank/percentile for a single ticker.

    Single-symbol convenience wrapper around get_orats_iv_rank_batch.
    Returns the entry dict (with keys iv, ivRank1y, ivPct1y, ivRank1m, ivPct1m)
    or None if the symbol isn't returned or the call fails.

    Used by ContextBuilder for wheel/turnover-wheel decisions, which need
    ivRank1y as a hard entry gate. ORATS returns IV rank ONLY on /ivrank,
    not on /summaries — see https://docs.orats.io/datav2-api-guide/data.html
    """
    if not settings.ORATS_API_KEY or not symbol:
        return None
    try:
        result = get_orats_iv_rank_batch([symbol])
        return result.get(symbol.upper())
    except Exception:
        logger.warning("ORATS iv_rank single-symbol failed for %s", symbol, exc_info=True)
        return None


def get_finnhub_earnings_history(symbol: str) -> list[dict]:
    """Return recent earnings surprise history from Finnhub."""
    if not settings.FINNHUB_API_KEY:
        return []
    try:
        from data.finnhub_client import FinnhubClient

        return FinnhubClient().get_earnings_history(symbol)
    except Exception:
        logger.warning(
            "Finnhub earnings history failed for %s", symbol, exc_info=True,
        )
        return []


def get_finnhub_analyst_data(symbol: str) -> dict:
    """Return analyst recommendations and price target from Finnhub."""
    if not settings.FINNHUB_API_KEY:
        return {}
    try:
        from data.finnhub_client import FinnhubClient

        return FinnhubClient().get_analyst_data(symbol)
    except Exception:
        logger.warning(
            "Finnhub analyst data failed for %s", symbol, exc_info=True,
        )
        return {}


def get_finnhub_news_sentiment(symbol: str) -> dict | None:
    """Return Finnhub NLP news sentiment."""
    if not settings.FINNHUB_API_KEY:
        return None
    try:
        from data.finnhub_client import FinnhubClient

        return FinnhubClient().get_news_sentiment(symbol)
    except Exception:
        logger.warning(
            "Finnhub news sentiment failed for %s", symbol, exc_info=True,
        )
        return None


def get_earnings_calendar(symbol: str) -> dict:
    """Return upcoming earnings from Finnhub."""
    if settings.FINNHUB_API_KEY:
        try:
            from data.finnhub_client import FinnhubClient

            result = FinnhubClient().get_next_earnings(symbol)
            if result and result.get("next_earnings_date"):
                return {**result, "source": "finnhub"}
        except Exception:
            logger.warning("Finnhub earnings failed for %s", symbol, exc_info=True)

    return {
        "next_earnings_date": None, "days_to_earnings": None,
        "eps_estimate": None, "revenue_estimate": None, "source": "unavailable",
    }


# ── Company profile (Finnhub) ────────────────────────────────

def get_company_profile(symbol: str) -> dict:
    """Return Finnhub-sourced company profile and key metrics, ETF-aware.

    For ETFs (per config.ETF_SYMBOLS), Finnhub returns empty data from
    /stock/profile2. This wrapper applies config.SECTOR_ETF_MAP to populate
    sector for sector-specific ETFs and leaves it None for broad-market ETFs.
    52-week high/low IS populated by Finnhub for ETFs (sparse metric response).

    If a symbol is not in ETF_SYMBOLS but Finnhub returns empty profile data,
    a warning is logged — likely the symbol is an ETF that wasn't added to the
    set.

    Returns the same dict as FinnhubClient.get_company_profile() plus:
        is_etf: bool    True if symbol is in config.ETF_SYMBOLS

    On Finnhub failure: all fields None, both data_available flags False.
    No yfinance fallback — fundamentals fields come from canonical sources
    (earnings → get_earnings_calendar, ex-div → get_ex_dividend_date).
    """
    sym = symbol.upper()
    is_etf = sym in settings.ETF_SYMBOLS

    if not settings.FINNHUB_API_KEY:
        return {
            "sector": settings.SECTOR_ETF_MAP.get(sym) if is_etf else None,
            "market_cap": None, "pe_ratio": None, "annual_dividend_yield": None,
            "fifty_two_week_high": None, "fifty_two_week_low": None,
            "profile_data_available": False, "metric_data_available": False,
            "is_etf": is_etf,
        }

    try:
        from data.finnhub_client import FinnhubClient
        result = FinnhubClient().get_company_profile(symbol)
    except Exception:
        logger.warning("Finnhub company profile failed for %s", symbol, exc_info=True)
        result = {
            "sector": None, "market_cap": None, "pe_ratio": None,
            "annual_dividend_yield": None,
            "fifty_two_week_high": None, "fifty_two_week_low": None,
            "profile_data_available": False, "metric_data_available": False,
        }

    if is_etf and result.get("sector") is None:
        result["sector"] = settings.SECTOR_ETF_MAP.get(sym)
    elif not is_etf and not result.get("profile_data_available"):
        logger.warning(
            "get_company_profile: %s returned empty Finnhub profile but is not "
            "in config.ETF_SYMBOLS. If %s is an ETF, add it to ETF_SYMBOLS.",
            symbol, symbol,
        )

    result["is_etf"] = is_etf
    return result


# ── Earnings & technicals ────────────────────────────────────


def _safe_float(value) -> float | None:
    """Convert a value to float, returning None if NaN or missing."""
    if value is None:
        return None
    f = float(value)
    return None if np.isnan(f) else f


def get_stock_technicals(symbol: str) -> dict:
    """Fetch daily bars and compute technical indicators.

    Returns:
        Dict with keys: current_price, atr_14, rsi_14, sma_20,
        bollinger_upper, bollinger_lower, price_change_pct_30d,
        sma_50, sma_200, above_sma_50, above_sma_200, golden_cross,
        macd_value, macd_signal, macd_bullish, avg_volume_10d,
        avg_volume_30d, volume_trend, price_change_pct_5d,
        price_change_pct_20d, high_20d, low_20d.
    """
    end = datetime.now()
    start = end - timedelta(days=300)  # enough for SMA-200 warm-up

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = _stock_client.get_stock_bars(request)
    df = bars.df

    # Flatten multi-index (symbol, timestamp) to just timestamp
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")

    if len(df) < 20:
        raise ValueError(f"Not enough data for {symbol}: got {len(df)} bars")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    current_price = float(close.iloc[-1])

    # ATR-14
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1])

    # RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_14 = float(rsi_series.iloc[-1])

    # SMA-20 & Bollinger Bands
    sma_20 = float(close.rolling(20).mean().iloc[-1])
    std_20 = float(close.rolling(20).std().iloc[-1])
    bollinger_upper = sma_20 + 2 * std_20
    bollinger_lower = sma_20 - 2 * std_20

    # 30-day price change %
    if len(close) >= 30:
        price_30d_ago = float(close.iloc[-30])
        price_change_pct_30d = ((current_price - price_30d_ago) / price_30d_ago) * 100
    else:
        price_change_pct_30d = None

    # ── SMA 50 / 200 (via ta) ──────────────────────────────
    sma_50 = _safe_float(ta.trend.SMAIndicator(close, window=50).sma_indicator().iloc[-1])
    sma_200 = _safe_float(ta.trend.SMAIndicator(close, window=200).sma_indicator().iloc[-1])

    above_sma_50 = (current_price > sma_50) if sma_50 is not None else None
    above_sma_200 = (current_price > sma_200) if sma_200 is not None else None
    golden_cross = (sma_50 > sma_200) if (sma_50 is not None and sma_200 is not None) else None

    # ── MACD (via ta) ──────────────────────────────────────
    macd_ind = ta.trend.MACD(close)
    macd_value = _safe_float(macd_ind.macd().iloc[-1])
    macd_signal = _safe_float(macd_ind.macd_signal().iloc[-1])
    macd_bullish = (macd_value > macd_signal) if (macd_value is not None and macd_signal is not None) else None

    # ── Volume trend ───────────────────────────────────────
    avg_volume_10d = float(volume.tail(10).mean()) if len(volume) >= 10 else None
    avg_volume_30d = float(volume.tail(30).mean()) if len(volume) >= 30 else None
    if avg_volume_10d is not None and avg_volume_30d is not None:
        volume_trend = "increasing" if avg_volume_10d > avg_volume_30d else "decreasing"
    else:
        volume_trend = None

    # ── Price momentum ─────────────────────────────────────
    if len(close) >= 6:
        price_change_pct_5d = round(((current_price - float(close.iloc[-6])) / float(close.iloc[-6])) * 100, 2)
    else:
        price_change_pct_5d = None

    if len(close) >= 21:
        price_change_pct_20d = round(((current_price - float(close.iloc[-21])) / float(close.iloc[-21])) * 100, 2)
    else:
        price_change_pct_20d = None

    # 20-day high/low (support/resistance proxies for strike selection)
    high_20d = float(high.tail(20).max())
    low_20d = float(low.tail(20).min())

    return {
        "current_price": current_price,
        "atr_14": round(atr_14, 4),
        "rsi_14": round(rsi_14, 2),
        "sma_20": round(sma_20, 4),
        "bollinger_upper": round(bollinger_upper, 4),
        "bollinger_lower": round(bollinger_lower, 4),
        "price_change_pct_30d": round(price_change_pct_30d, 2) if price_change_pct_30d is not None else None,
        "sma_50": round(sma_50, 4) if sma_50 is not None else None,
        "sma_200": round(sma_200, 4) if sma_200 is not None else None,
        "above_sma_50": above_sma_50,
        "above_sma_200": above_sma_200,
        "golden_cross": golden_cross,
        "macd_value": round(macd_value, 4) if macd_value is not None else None,
        "macd_signal": round(macd_signal, 4) if macd_signal is not None else None,
        "macd_bullish": macd_bullish,
        "avg_volume_10d": round(avg_volume_10d) if avg_volume_10d is not None else None,
        "avg_volume_30d": round(avg_volume_30d) if avg_volume_30d is not None else None,
        "volume_trend": volume_trend,
        "price_change_pct_5d": price_change_pct_5d,
        "price_change_pct_20d": price_change_pct_20d,
        "high_20d": round(high_20d, 2),
        "low_20d": round(low_20d, 2),
    }


def get_fear_greed_index() -> dict:
    """Fetch the CNN Fear & Greed Index.

    Returns a dict with 'score' (0-100 float) and 'rating' (str).
    The result is cached for 1 hour. On failure returns score=None, rating="Unknown".
    """
    global _fear_greed_cache, _fear_greed_timestamp

    if _fear_greed_cache is not None and (time.monotonic() - _fear_greed_timestamp) < _FEAR_GREED_TTL:
        return _fear_greed_cache

    _CNN_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
        "Origin": "https://www.cnn.com",
    }

    for url in [
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        "https://production.dataviz.cnn.io/index/fearandgreed/current",
    ]:
        try:
            resp = _requests.get(url, headers=_CNN_HEADERS, timeout=10)
            if resp.status_code in (403, 418):
                logger.warning(
                    "Fear & Greed fetch blocked (%d) from %s — CNN may be blocking datacenter IPs",
                    resp.status_code, url,
                )
                continue
            resp.raise_for_status()
            data = resp.json()
            fg = data.get("fear_and_greed") or data
            result = {
                "score": round(float(fg["score"]), 2),
                "rating": str(fg["rating"]),
            }
            _fear_greed_cache = result
            _fear_greed_timestamp = time.monotonic()
            logger.info("Fetched Fear & Greed Index: %.1f (%s)", result["score"], result["rating"])
            return result
        except Exception:
            logger.warning("Failed to fetch Fear & Greed Index from %s", url, exc_info=True)

    return {"score": None, "rating": "Unknown"}


def get_risk_free_rate() -> float:
    """Fetch the current risk-free rate from the FRED DGS3MO series.

    Uses the fredapi library to pull the 3-Month Treasury Bill secondary
    market rate.  Returns the most recent non-null value as a decimal
    (e.g. 5.23% -> 0.0523).  The result is cached for 4 hours.
    Falls back to 0.05 on any failure.
    """
    global _risk_free_rate_cache, _risk_free_rate_timestamp

    if _risk_free_rate_cache is not None and (time.monotonic() - _risk_free_rate_timestamp) < _RISK_FREE_RATE_TTL:
        return _risk_free_rate_cache

    try:
        from fredapi import Fred

        fred = Fred(api_key=settings.FRED_API_KEY)
        series = fred.get_series("DGS3MO")
        rate = float(series.dropna().iloc[-1]) / 100.0
        _risk_free_rate_cache = rate
        _risk_free_rate_timestamp = time.monotonic()
        logger.info("Fetched risk-free rate from FRED DGS3MO: %.4f", rate)
        return rate
    except Exception:
        logger.warning("Failed to fetch risk-free rate from FRED; using 0.05 fallback", exc_info=True)
        return 0.05


def get_iv_rank(symbol: str) -> float | None:
    """Return the 1-year IV rank for a symbol.

    Delegates to ORATS (professional-grade, true 52-week ATM IV rank).
    Falls back to None if ORATS is unavailable.
    """
    summary = get_orats_summary(symbol)
    if summary is not None:
        return summary.get("iv_rank_1y")
    logger.warning("IV rank unavailable for %s (ORATS returned None)", symbol)
    return None
