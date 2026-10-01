"""
Live price feed for the BRAIN trading hub.

Runs as its own process (started/supervised by hub.py) so heavy libraries
like yfinance / ccxt can never stall the hub. Reads the bots' portfolios,
looks up prices for open positions, and writes logs/prices.json:

    {"equity": {"NVDA": 182.4}, "crypto": {"SOL/USDT:USDT": 151.2},
     "eq_at": "...", "crypto_at": "..."}
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HUB_DIR, "logs", "prices.json")
bot_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HUB_DIR), "trading-paperbot")


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def write_out(state):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, OUT)


def main():
    state = read_json(OUT, {}) or {}
    state.setdefault("equity", {})
    state.setdefault("crypto", {})
    yf = ex = None
    try:
        import yfinance as yf  # noqa
    except Exception as e:
        print("yfinance unavailable:", e, flush=True)
    try:
        import ccxt
        ex = ccxt.mexc()
    except Exception as e:
        print("ccxt unavailable:", e, flush=True)
    print("price feed running", flush=True)

    last_eq = 0
    while True:
        lev = read_json(os.path.join(bot_dir, "leverage_portfolio.json"), {})
        if ex:
            for sym in {p.get("symbol") for p in lev.get("open_positions") or [] if p.get("symbol")}:
                try:
                    state["crypto"][sym] = float(ex.fetch_ticker(sym)["last"])
                except Exception as e:
                    print(f"crypto {sym}: {e}", flush=True)
            state["crypto_at"] = datetime.now(timezone.utc).isoformat()

        if yf and time.time() - last_eq >= 30:
            pf = read_json(os.path.join(bot_dir, "portfolio.json"), {})
            for t in list((pf.get("positions") or {}).keys()):
                try:
                    fi = yf.Ticker(t).fast_info
                    p = getattr(fi, "last_price", None) or fi.get("lastPrice")
                    if p:
                        state["equity"][t] = float(p)
                except Exception as e:
                    print(f"equity {t}: {e}", flush=True)
            state["eq_at"] = datetime.now(timezone.utc).isoformat()
            last_eq = time.time()

        try:
            write_out(state)
        except Exception as e:
            print("write failed:", e, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
