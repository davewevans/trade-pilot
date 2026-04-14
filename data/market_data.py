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

logger = logging.getLogger(__name__)

_risk_free_rate_cache: float | None = None
_risk_free_rate_timestamp: float = 0.0
_RISK_FREE_RATE_TTL = 4 * 3600  # 4 hours in seconds

_fear_greed_cache: dict | None = None
_fear_greed_timestamp: float = 0.0
_FEAR_GREED_TTL = 3600  # 1 hour in seconds

_fundamentals_cache: dict[str, tuple[float, dict]] = {}  # symbol -> (timestamp, data)
_FUNDAMENTALS_TTL = 6 * 3600  # 6 hours in seconds

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


def get_option_snapshot(symbols: list[str]) -> dict:
    """Return the latest snapshot for given option symbols.

    Each snapshot includes: latest quote (bid/ask), latest trade,
    greeks (delta, gamma, theta, vega, rho), and implied volatility.

    Args:
        symbols: OCC option symbols (e.g. ["AAPL240119C00190000"]).

    Returns:
        Dict keyed by symbol, each value containing snapshot data.
    """
    request = OptionSnapshotRequest(symbol_or_symbols=symbols)
    snapshots = _option_client.get_option_snapshot(request)
    return {sym: _snapshot_to_dict(snap) for sym, snap in snapshots.items()}


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


def get_vix() -> float | None:
    """Return the current VIX index value via yfinance.

    Uses the actual ^VIX index, not VIXY (which drifts due to
    futures roll decay). Returns None on failure.
    """
    try:
        vix_data = yf.Ticker("^VIX").fast_info
        vix = float(vix_data["last_price"])
        logger.info("Fetched ^VIX: %.2f", vix)
        return vix
    except Exception:
        logger.warning("Failed to fetch ^VIX via yfinance", exc_info=True)
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


# ── VIX term structure ──────────────────────────────────────

_vix_term_cache: dict | None = None
_vix_term_timestamp: float = 0.0
_VIX_TERM_TTL = 15 * 60


def get_vix_term_structure() -> dict:
    """Return the VIX term structure across multiple timeframes."""
    global _vix_term_cache, _vix_term_timestamp

    if _vix_term_cache is not None and time.monotonic() - _vix_term_timestamp < _VIX_TERM_TTL:
        return _vix_term_cache

    symbols = {"vix9d": "^VIX9D", "vix_spot": "^VIX", "vix3m": "^VIX3M", "vix6m": "^VIX6M"}
    values: dict = {}
    for key, ticker_sym in symbols.items():
        try:
            values[key] = float(yf.Ticker(ticker_sym).fast_info["last_price"])
        except Exception:
            logger.debug("Could not fetch %s", ticker_sym)
            values[key] = None

    vix_spot = values.get("vix_spot")
    vix3m = values.get("vix3m")
    vix9d = values.get("vix9d")

    contango = (vix_spot < vix3m) if vix_spot is not None and vix3m is not None else None
    vix9d_vs_spot = round(vix9d - vix_spot, 2) if vix9d is not None and vix_spot is not None else None
    term_slope_m1_m3 = round(vix3m - vix_spot, 2) if vix_spot is not None and vix3m is not None else None

    result = {
        **values,
        "contango": contango,
        "vix9d_vs_spot": vix9d_vs_spot,
        "term_slope_m1_m3": term_slope_m1_m3,
    }
    _vix_term_cache = result
    _vix_term_timestamp = time.monotonic()
    logger.info("VIX term structure: %s", result)
    return result


# ── Ex-dividend ────────────────────────────────────────────

_exdiv_cache: dict[str, tuple[float, dict]] = {}
_EXDIV_TTL = 12 * 3600


def get_ex_dividend_date(symbol: str) -> dict:
    """Return the next ex-dividend date for a symbol via yfinance."""
    cached = _exdiv_cache.get(symbol)
    if cached:
        ts, data = cached
        if time.monotonic() - ts < _EXDIV_TTL:
            return data

    try:
        from datetime import date as date_type

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        ex_date_ts = info.get("exDividendDate")
        next_ex_div = None
        days_to_ex_div = None
        if ex_date_ts:
            try:
                ex_date = pd.Timestamp(ex_date_ts, unit="s").date()
                today = date_type.today()
                if ex_date >= today:
                    next_ex_div = str(ex_date)
                    days_to_ex_div = (ex_date - today).days
            except Exception:
                logger.debug("Could not parse ex_dividend_date for %s", symbol)

        div_yield = None
        raw_yield = info.get("dividendYield")
        if raw_yield is not None:
            try:
                div_yield = round(float(raw_yield), 4)
            except (TypeError, ValueError):
                pass

        result = {
            "next_ex_dividend_date": next_ex_div,
            "days_to_ex_dividend": days_to_ex_div,
            "annual_dividend_yield": div_yield,
        }
        _exdiv_cache[symbol] = (time.monotonic(), result)
        return result

    except Exception:
        logger.warning("Failed to fetch ex-dividend info for %s", symbol, exc_info=True)
        return {"next_ex_dividend_date": None, "days_to_ex_dividend": None, "annual_dividend_yield": None}


# ── ORATS + Finnhub integration ────────────────────────────


def get_orats_summary(symbol: str) -> dict | None:
    """Return the full ORATS analytics summary for a symbol."""
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
    """Return upcoming earnings from Finnhub (yfinance fallback)."""
    if settings.FINNHUB_API_KEY:
        try:
            from data.finnhub_client import FinnhubClient

            result = FinnhubClient().get_next_earnings(symbol)
            if result and result.get("next_earnings_date"):
                return {**result, "source": "finnhub"}
        except Exception:
            logger.warning("Finnhub earnings failed for %s, trying yfinance", symbol, exc_info=True)

    try:
        from datetime import date

        earnings_date = get_earnings_date(symbol)
        if earnings_date:
            ed = date.fromisoformat(earnings_date)
            days = (ed - date.today()).days
            return {
                "next_earnings_date": earnings_date,
                "days_to_earnings": days,
                "eps_estimate": None,
                "revenue_estimate": None,
                "source": "yfinance",
            }
    except Exception:
        logger.warning("yfinance earnings fallback failed for %s", symbol, exc_info=True)

    return {
        "next_earnings_date": None, "days_to_earnings": None,
        "eps_estimate": None, "revenue_estimate": None, "source": "unavailable",
    }


# ── Earnings & technicals ────────────────────────────────────

_FUNDAMENTALS_KEYS = [
    "next_earnings_date", "days_to_earnings", "pe_ratio", "market_cap",
    "sector", "industry", "avg_volume", "fifty_two_week_high",
    "fifty_two_week_low", "analyst_rating",
    "next_ex_dividend_date", "days_to_ex_dividend",
]


def get_fundamentals(symbol: str) -> dict:
    """Fetch fundamental data for a symbol via yfinance.

    Returns earnings info, valuation metrics, sector/industry, and the most
    recent analyst rating.  Results are cached per symbol for 6 hours.
    On any failure every key is returned as None.
    """
    cached = _fundamentals_cache.get(symbol)
    if cached is not None:
        ts, data = cached
        if (time.monotonic() - ts) < _FUNDAMENTALS_TTL:
            return data

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        # ── Earnings date ───────────────────────────────────
        next_earnings_date: str | None = None
        days_to_earnings: int | None = None
        try:
            cal = ticker.calendar
            if isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                date_val = pd.Timestamp(cal.loc["Earnings Date"].iloc[0]).date()
                next_earnings_date = str(date_val)
                days_to_earnings = (date_val - datetime.now().date()).days
            elif isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                if dates:
                    date_val = pd.Timestamp(dates[0]).date()
                    next_earnings_date = str(date_val)
                    days_to_earnings = (date_val - datetime.now().date()).days
        except Exception:
            logger.debug("Could not parse earnings date for %s", symbol, exc_info=True)

        # ── Ex-dividend date ────────────────────────────────
        next_ex_dividend_date: str | None = None
        days_to_ex_dividend: int | None = None
        try:
            ex_date_raw = info.get("exDividendDate")
            if ex_date_raw:
                ex_date = pd.Timestamp(ex_date_raw, unit="s").date()
                if ex_date >= datetime.now().date():
                    next_ex_dividend_date = str(ex_date)
                    days_to_ex_dividend = (ex_date - datetime.now().date()).days
        except Exception:
            logger.debug("Could not parse ex-dividend date for %s", symbol, exc_info=True)

        # ── Analyst rating ──────────────────────────────────
        analyst_rating: str | None = None
        try:
            recs = ticker.recommendations
            if recs is not None and not recs.empty:
                last_row = recs.iloc[-1]
                analyst_rating = last_row.get("To Grade") or last_row.get("toGrade")
        except Exception:
            logger.debug("Could not parse analyst rating for %s", symbol, exc_info=True)

        result = {
            "next_earnings_date": next_earnings_date,
            "days_to_earnings": days_to_earnings,
            "pe_ratio": info.get("trailingPE"),
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "avg_volume": info.get("averageVolume"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "analyst_rating": analyst_rating,
            "next_ex_dividend_date": next_ex_dividend_date,
            "days_to_ex_dividend": days_to_ex_dividend,
        }

        _fundamentals_cache[symbol] = (time.monotonic(), result)
        logger.info("Fetched fundamentals for %s", symbol)
        return result

    except Exception:
        logger.warning("Failed to fetch fundamentals for %s", symbol, exc_info=True)
        return {k: None for k in _FUNDAMENTALS_KEYS}


def get_earnings_date(symbol: str) -> str | None:
    """Return the next earnings date for a symbol as an ISO date string.

    Uses yfinance. Returns None if no upcoming earnings date is found.
    """
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or cal.empty if isinstance(cal, pd.DataFrame) else not cal:
            return None
        if isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.index:
                date_val = cal.loc["Earnings Date"].iloc[0]
                return str(pd.Timestamp(date_val).date())
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
            if dates:
                return str(pd.Timestamp(dates[0]).date())
        return None
    except Exception:
        logger.exception("Failed to fetch earnings date for %s", symbol)
        return None


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
        price_change_pct_20d.
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
