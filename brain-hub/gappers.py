"""
Gappers scanner (Ross Cameron "5 pillars") for the BRAIN trading hub.

Runs as its own process (supervised by hub.py), scans US stocks with Yahoo
Finance via yfinance and writes logs/gappers.json for the Command Center
"Bots" tab. It only READS market data - it never places or queues trades.

Pillars (all adjustable in hub_config.json under "gappers"):
  price   $2 - $20
  change  up 10%+ (pre-market change while pre-market, otherwise day change)
  rvol    relative volume 5x+  (today's volume / 3-month average daily volume)
  float   under 20M shares
  news    a headline in the last 24h

Quotes from Yahoo are delayed ~15 min on some venues; treat as a watchlist
builder and confirm the setup on your broker / TradingView before acting.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HUB_DIR, "logs", "gappers.json")
CONFIG_PATH = os.path.join(HUB_DIR, "hub_config.json")

DEFAULTS = {"price_min": 2.0, "price_max": 20.0, "min_change": 10.0, "min_rvol": 5.0,
            "max_float_m": 20.0, "feed_paperbot": 1, "feed_min_score": 4, "feed_max_per_day": 3, "max_rows": 25, "scan_seconds": 60, "idle_scan_seconds": 600}
_float_cache = {}   # ticker -> (float_shares or None, fetched_at)
_news_cache = {}    # ticker -> (has_news, fetched_at)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def load_cfg():
    cfg = dict(DEFAULTS)
    user = (read_json(CONFIG_PATH, {}) or {}).get("gappers") or {}
    for k, v in user.items():
        if k in cfg:
            try:
                cfg[k] = float(v)
            except Exception:
                pass
    return cfg


def write_out(state):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, OUT)


def session_now():
    """('PRE'|'REG'|'POST'|'CLOSED', is_weekday) in US Eastern time."""
    try:
        import zoneinfo
        n = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        return "REG", True
    if n.weekday() >= 5:
        return "CLOSED", False
    hm = (n.hour, n.minute)
    if (4, 0) <= hm < (9, 30):
        return "PRE", True
    if (9, 30) <= hm < (16, 0):
        return "REG", True
    if (16, 0) <= hm < (20, 0):
        return "POST", True
    return "CLOSED", True


def num(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def quote_change(q, session):
    """% change vs previous close, using extended-hours price when relevant."""
    if session == "PRE" and q.get("preMarketChangePercent") is not None:
        return num(q.get("preMarketChangePercent")), num(q.get("preMarketPrice"))
    if session == "POST" and q.get("postMarketChangePercent") is not None:
        return num(q.get("postMarketChangePercent")), num(q.get("postMarketPrice"))
    return num(q.get("regularMarketChangePercent")), num(q.get("regularMarketPrice"))


def score_row(q, session, cfg):
    chg, price = quote_change(q, session)
    if price is None or chg is None:
        return None
    vol = num(q.get("regularMarketVolume"), 0) or 0
    if session == "PRE":
        vol = num(q.get("preMarketVolume"), vol) or vol
    avg = num(q.get("averageDailyVolume3Month")) or num(q.get("averageDailyVolume10Day"))
    rvol = (vol / avg) if avg else None
    return {"ticker": q.get("symbol"), "name": (q.get("shortName") or q.get("longName") or "")[:40],
            "price": price, "chg": chg, "volume": vol, "rvol": rvol, "float_m": None, "news": None,
            "exchange": q.get("fullExchangeName") or q.get("exchange") or ""}


def pillars(r, cfg):
    return {
        "price": cfg["price_min"] <= r["price"] <= cfg["price_max"],
        "change": r["chg"] >= cfg["min_change"],
        "rvol": r["rvol"] is not None and r["rvol"] >= cfg["min_rvol"],
        "float": r["float_m"] is not None and r["float_m"] <= cfg["max_float_m"],
        "news": bool(r["news"]),
    }


def get_float_m(yf, t):
    hit = _float_cache.get(t)
    if hit and time.time() - hit[1] < 6 * 3600:
        return hit[0]
    val = None
    try:
        info = yf.Ticker(t).info or {}
        f = info.get("floatShares") or info.get("sharesOutstanding")
        val = round(float(f) / 1e6, 2) if f else None
    except Exception:
        val = None
    _float_cache[t] = (val, time.time())
    return val


def get_news(yf, t):
    hit = _news_cache.get(t)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    has = False
    try:
        now = time.time()
        for n in (yf.Ticker(t).news or []):
            c = n.get("content") if isinstance(n, dict) else None
            ts = None
            if c and c.get("pubDate"):
                ts = datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00")).timestamp()
            elif n.get("providerPublishTime"):
                ts = float(n["providerPublishTime"])
            if ts and now - ts < 86400:
                has = True
                break
    except Exception:
        has = False
    _news_cache[t] = (has, time.time())
    return has


def fetch_candidates(yf, cfg, session):
    """Raw Yahoo screener quotes: US exchanges, price in range, positive movers."""
    from yfinance import EquityQuery
    q = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", "NMS", "NYQ", "ASE", "NCM", "NGM"]),
        EquityQuery("btwn", ["intradayprice", cfg["price_min"] * 0.8, cfg["price_max"] * 1.5]),
        EquityQuery("gt", ["percentchange", max(1.0, cfg["min_change"] * 0.5)]),
    ])
    res = yf.screen(q, sortField="percentchange", sortAsc=False, size=100)
    return (res or {}).get("quotes") or []


def scan(yf, cfg):
    session, _ = session_now()
    quotes = fetch_candidates(yf, cfg, session)
    rows = [r for r in (score_row(q, session, cfg) for q in quotes) if r]
    # cheap pillars first; only look up float / news for plausible names
    rows = [r for r in rows if r["price"] and cfg["price_min"] <= r["price"] <= cfg["price_max"] and r["chg"] >= cfg["min_change"] * 0.5]
    rows.sort(key=lambda r: r["chg"], reverse=True)
    rows = rows[:40]
    for r in rows[:30]:
        r["float_m"] = get_float_m(yf, r["ticker"])
        if r["chg"] >= cfg["min_change"]:
            r["news"] = get_news(yf, r["ticker"])
    for r in rows:
        p = pillars(r, cfg)
        r["pillars"] = p
        r["score"] = sum(p.values())
    rows.sort(key=lambda r: (r["score"], r["chg"]), reverse=True)
    return session, rows[: int(cfg["max_rows"])]


def feed_paperbot(rows, session, cfg):
    """Hand the strongest gappers to the equity paper bot's AI watchlist (candidates.json).
    Paper only; the bot's own AI still decides buy/hold. Max N new tickers per day."""
    if not cfg.get("feed_paperbot") or session not in ("PRE", "REG"):
        return []
    hub_cfg = read_json(CONFIG_PATH, {}) or {}
    bot_dir = hub_cfg.get("bot_dir") or os.path.join(os.path.dirname(HUB_DIR), "trading-paperbot")
    path = os.path.join(bot_dir, "candidates.json")
    if not os.path.isdir(bot_dir):
        return []
    cands = read_json(path, [])
    if not isinstance(cands, list):
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    done = read_json(os.path.join(HUB_DIR, "logs", "gappers_fed.json"), {}) or {}
    if done.get("day") != today:
        done = {"day": today, "tickers": []}
    added = []
    for r in rows:
        if len(done["tickers"]) >= int(cfg["feed_max_per_day"]):
            break
        if r["score"] < cfg["feed_min_score"] or not (r.get("pillars") or {}).get("change") or r["ticker"] in done["tickers"]:
            continue
        cands.append({"ticker": r["ticker"], "channel": "gappers", "consumed": False,
                      "received_at": datetime.now(timezone.utc).isoformat(),
                      "text": f"gapper {r['score']}/5: +{r['chg']:.1f}% RVOL {r['rvol'] or 0:.1f}x float {r['float_m']}M"})
        done["tickers"].append(r["ticker"])
        added.append(r["ticker"])
    if added:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cands, f, indent=2)
        os.replace(tmp, path)
        write_out_json(os.path.join(HUB_DIR, "logs", "gappers_fed.json"), done)
        print("fed paper bot:", added, flush=True)
    return added


def write_out_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def main():
    try:
        import yfinance as yf
    except Exception as e:
        write_out({"at": datetime.now(timezone.utc).isoformat(), "rows": [], "error": f"yfinance missing: {e}"})
        print("yfinance unavailable:", e, flush=True)
        return
    print("gappers scanner running", flush=True)
    while True:
        cfg = load_cfg()
        state = {"at": datetime.now(timezone.utc).isoformat(), "criteria": cfg, "rows": [], "session": None, "error": None}
        try:
            state["session"], state["rows"] = scan(yf, cfg)
            try:
                state["fed"] = feed_paperbot(state["rows"], state["session"], cfg)
            except Exception as e:
                print("feed failed:", e, flush=True)
        except Exception as e:
            state["error"] = f"{type(e).__name__}: {e}"[:200]
            print("scan failed:", state["error"], flush=True)
            prev = read_json(OUT, {}) or {}
            state["rows"] = prev.get("rows") or []
        try:
            write_out(state)
        except Exception as e:
            print("write failed:", e, flush=True)
        sess = state["session"] or session_now()[0]
        time.sleep(cfg["scan_seconds"] if sess in ("PRE", "REG", "POST") else cfg["idle_scan_seconds"])


if __name__ == "__main__":
    if "--once" in sys.argv:
        import yfinance as yf
        print(json.dumps(scan(yf, load_cfg())[1], indent=1))
    else:
        main()
