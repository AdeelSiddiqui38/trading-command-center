"""
Pre-market / intraday mover scan from Yahoo Finance - no API key.

Served by hub.py at /api/scan for the Command Center's Pre-Mkt Gaps panel, because
Finnhub's free /quote has no pre-market prices (it returns the last regular close).

Universe = Yahoo's predefined screeners (largely YESTERDAY's movers) plus any extra
symbols passed in (the watchlist).  Gap = pre-market price vs the last regular close
while the market state is PRE, otherwise last price vs previous close.

Known limits: names that were not on yesterday's lists (fresh-news gappers) are
missed; Yahoo flags this feed as 15-20 min delayed even though pre-market trade
times look fresh - `age_min` (minutes since the last trade) is reported per row so
the UI can show it; there is no pre-market volume.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SCREENS = ("small_cap_gainers", "day_gainers", "most_actives", "aggressive_small_caps")
CACHE_TTL = 45
MAX_EXTRA = 15
_SYM_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_cache = {}
_lock = threading.Lock()


def _gap_fields(q):
    """(state, reference close, current price, timestamp of that price)"""
    state = q.get("marketState")
    if state == "PRE":
        base = q.get("regularMarketPrice")                      # last regular-session close
        return state, base, (q.get("preMarketPrice") or base), q.get("preMarketTime")
    return state, q.get("regularMarketPreviousClose"), q.get("regularMarketPrice"), q.get("regularMarketTime")


def _row(q, now):
    state, base, px, t = _gap_fields(q)
    if not base or not px or base <= 0 or px <= 0:
        return None
    return {
        "sym": q["symbol"], "px": float(px), "pc": float(base), "gap": (px / base - 1) * 100,
        "age_min": round((now - t) / 60, 1) if t else None,
        "avg_vol": q.get("averageDailyVolume3Month"), "mcap": q.get("marketCap"),
        "state": state, "delay_flag_min": q.get("exchangeDataDelayedBy"),
    }


def _extra_quote(sym):
    try:
        import yfinance as yf
        info = yf.Ticker(sym).get_info()
        info.setdefault("symbol", sym)
        return info
    except Exception:
        return None


def yahoo_scan(extra=(), max_age=CACHE_TTL):
    syms = sorted({s.strip().upper() for s in extra if _SYM_RE.match(s.strip().upper())})[:MAX_EXTRA]
    key = tuple(syms)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        try:
            import yfinance as yf
        except Exception:
            return {"error": "yfinance is not installed for the hub's Python"}

        seen, errors = {}, []
        for name in SCREENS:
            try:
                for q in yf.screen(name, count=25).get("quotes", []):
                    seen.setdefault(q["symbol"], q)
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")

        missing = [s for s in syms if s not in seen]
        if missing:
            with ThreadPoolExecutor(max_workers=6) as ex:
                for s, info in zip(missing, ex.map(_extra_quote, missing)):
                    if info:
                        seen[s] = info

        now = time.time()
        rows = sorted((r for r in (_row(q, now) for q in seen.values()) if r), key=lambda r: r["gap"], reverse=True)
        data = {"source": "yahoo", "asof": time.strftime("%Y-%m-%dT%H:%M:%S"), "universe": len(seen),
                "rows": rows, "errors": errors}
        if not rows:
            data["error"] = "; ".join(errors) or "no quotes returned"
        else:
            _cache[key] = (time.time(), data)
        return data
