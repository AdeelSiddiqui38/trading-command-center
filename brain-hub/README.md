# BRAIN trading hub

Runs the paper-trading bots in the background (no console windows, one copy each)
and feeds the Command Center's **🤖 Bots — Live Trades** tab at `http://127.0.0.1:7777`.

| File | Purpose |
| --- | --- |
| `hub.py` | Supervisor + local API + repo auto-sync (Python stdlib only) |
| `prices.py` | Live prices for open positions (yfinance / MEXC), runs as its own process |
| `telegram_listener_v2.py` | Installs as `trading-paperbot/telegram_listener.py`: text, **voice notes** (transcribed), screenshots, catch-up on missed messages. Needs `telegram_core.py` (your original listener, holds your Telegram keys; never committed) |
| `Adeel's Trading Command Center.pyw` | Optional double-click launcher |
| `setup-windows.ps1` / `setup-mac.command` | One-time setup: folders, hub, Desktop icon |

## Desktop icon (Windows)
Right-click Desktop → New → Shortcut:
`"C:\Python314\pythonw.exe" "C:\Users\<you>\Documents\BRAIN\trading-hub\hub.py"`

## Folder layout
```
Documents\BRAIN\
  trading-command-center\   git clone of this repo
  trading-hub\              hub.py, prices.py, hub_config.json, logs\
  trading-paperbot\         bots, telegram_core.py (keys), portfolios + logs
  Vibe-Trading\  trading-signal-agent\  trading-archive\
```

## Staying aligned with GitHub
20 s after it starts (and on **↻ Sync now** in the Bots tab, or `POST /api/sync`) the hub:
1. `git pull`s `trading-command-center` (downloads the zip if git isn't installed),
2. copies `brain-hub/hub.py` and `prices.py` into `trading-hub` (a new `hub.py` runs after the next hub restart),
3. copies `telegram_listener_v2.py` to `trading-paperbot/telegram_listener.py` and restarts that service.

Only changed files are written (a `.bak` is kept). Turn off with `"auto_sync": false` in `hub_config.json`.
Edit code in this repo, push, and the PC picks it up; don't hand-edit the copies.

Stop everything: **■ Stop all bots** in the tab, or `python hub.py --stop`. Paper money only.
