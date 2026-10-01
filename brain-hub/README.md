# BRAIN trading hub

Runs the paper-trading bots in the background (no console windows, one copy each)
and feeds the Command Center's **🤖 Bots — Live Trades** tab at `http://127.0.0.1:7777`.

| File | Purpose |
| --- | --- |
| `hub.py` | Supervisor + local API (Python stdlib; uses yfinance/ccxt for live prices if installed) |
| `Launch-Trading.vbs` | Windows Desktop-icon target: pulls the latest hub, starts it hidden, opens the app |
| `setup-windows.ps1` | One-time: moves bots into `Documents\BRAIN`, installs hub, creates the one icon |
| `setup-mac.command` | Same for macOS |

Installed layout:

```
Documents\BRAIN\
  trading-command-center\   (this repo)
  trading-hub\              hub.py, hub_config.json, logs\
  trading-paperbot\         paperbot / leverage bot / telegram listener + their JSON + logs
  Vibe-Trading\             research agent
  trading-signal-agent\     optional Node signal agent
  trading-archive\          old launchers, logs, shortcuts
```

`hub_config.json` controls the watchlist, interval and which services auto-start.
Stop everything: **■ Stop all bots** in the tab, or `python hub.py --stop`.
Paper money only — nothing here connects to a broker.
