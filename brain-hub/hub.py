"""
BRAIN Trading Hub
=================
Runs every trading process in the background (no console windows), keeps
exactly one copy of each alive, and serves their state to the Command Center
"Bots — Live Trades" tab at http://127.0.0.1:7777.

Services (all paper / simulated, nothing touches a broker):
  vibe          Vibe-Trading research agent (backend :8899 + UI :5899)
  telegram      Telegram channel listener -> candidates.json / leverage_candidates.json
  paperbot      Equity paper bot (asks Vibe-Trading buy/sell/hold, trades portfolio.json)
  leverage      Crypto perpetuals paper bot (trades leverage_portfolio.json)
  signal_agent  Optional Node signal agent (:3000), if its folder exists

Python standard library only. yfinance / ccxt are used for live prices when
installed (the bots already need them).

Usage:
  pythonw hub.py            start hub (or just open the app if it's already running)
  python  hub.py --no-open  start without opening the Command Center
  python  hub.py --stop     stop the running hub and every bot it manages
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
BRAIN_DIR = os.path.dirname(HUB_DIR)
LOG_DIR = os.path.join(HUB_DIR, "logs")
CONFIG_PATH = os.path.join(HUB_DIR, "hub_config.json")
IS_WIN = os.name == "nt"
os.makedirs(LOG_DIR, exist_ok=True)

# Never write to a console: a hidden console that nobody reads can block a
# write forever and freeze the hub. stdout/stderr (and hard-crash tracebacks)
# all go to logs/hub.console.log instead.
_console = None
try:
    _console = open(os.path.join(LOG_DIR, "hub.console.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = _console
    sys.stderr = _console
    import faulthandler
    faulthandler.enable(_console)
except Exception:
    pass

DEFAULT_CONFIG = {
    "port": 7777,
    "app_url": "https://adeelsiddiqui38.github.io/trading-command-center/index.html",
    "chrome_app_id": "pgmmaaemnofafbhjagpfmmaegijlibbb",
    "bot_dir": os.path.join(BRAIN_DIR, "trading-paperbot"),
    "vibe_dir": os.path.join(BRAIN_DIR, "Vibe-Trading"),
    "signal_agent_dir": os.path.join(BRAIN_DIR, "trading-signal-agent"),
    "equity_watchlist": "AAPL,MSFT,NVDA,GOOGL",
    "equity_interval_minutes": 30,
    "autostart": {"vibe": True, "telegram": True, "paperbot": True, "leverage": True, "signal_agent": True},
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                user = json.load(f)
            auto = user.pop("autostart", {})
            cfg.update(user)
            cfg["autostart"].update(auto)
        except Exception as e:
            print("config error:", e)
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


CFG = load_config()
PORT = int(CFG["port"])


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


_hub_log_lock = threading.Lock()


def hub_log(msg):
    line = f"[{now_iso()}] {msg}"
    with _hub_log_lock:
        try:
            with open(os.path.join(LOG_DIR, "hub.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def python_exe():
    exe = sys.executable or "python"
    base = os.path.basename(exe).lower()
    if base == "pythonw.exe":
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    return exe


# ---------------------------------------------------------------------------
# Windows job object: when the hub dies, every bot it started dies with it,
# so nothing is ever left running "invisibly" without the hub.
# ---------------------------------------------------------------------------
_JOB = None
if IS_WIN:
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        _JOB = k32.CreateJobObjectW(None, None)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(_JOB, 9, ctypes.byref(info), ctypes.sizeof(info))
    except Exception as e:  # never fatal
        _JOB = None


def attach_to_job(proc):
    if IS_WIN and _JOB:
        try:
            k32.AssignProcessToJobObject(_JOB, int(proc._handle))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Finding / killing stray copies (e.g. the old PowerShell windows)
# ---------------------------------------------------------------------------
def list_processes():
    """[(pid, ppid, cmdline)] for every process we can see."""
    out = []
    try:
        if IS_WIN:
            ps = ("Get-CimInstance Win32_Process | ForEach-Object { "
                  "\"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, stdin=subprocess.DEVNULL,
                               text=True, timeout=60, creationflags=0x08000000, encoding="utf-8", errors="replace")
            lines = r.stdout.splitlines()
        else:
            r = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=30)
            lines = [re.sub(r"^\s*(\d+)\s+(\d+)\s+", r"\1\t\2\t", l) for l in r.stdout.splitlines()]
        for l in lines:
            parts = l.split("\t", 2)
            if len(parts) == 3 and parts[0].strip().isdigit():
                out.append((int(parts[0]), int(parts[1] or 0), parts[2]))
    except Exception as e:
        hub_log(f"process list failed: {e}")
    return out


def kill_tree(pid):
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
                           creationflags=0x08000000)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def kill_port_owners(ports):
    """Free ports our services need (leftovers from old windows / a previous hub)."""
    pids = set()
    try:
        if IS_WIN:
            r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, stdin=subprocess.DEVNULL,
                               text=True, timeout=20, creationflags=0x08000000)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                    port = parts[1].rsplit(":", 1)[-1]
                    if port.isdigit() and int(port) in ports and parts[4].isdigit():
                        pids.add(int(parts[4]))
        else:
            for port in ports:
                r = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True,
                                   stdin=subprocess.DEVNULL, text=True, timeout=15)
                pids.update(int(t) for t in r.stdout.split() if t.isdigit())
    except Exception:
        pass
    pids -= {0, 4, os.getpid()}
    for pid in pids:
        hub_log(f"freeing port held by leftover process {pid}")
        kill_tree(pid)
    return len(pids)


def kill_strays(patterns, protect=()):
    me = os.getpid()
    procs = list_processes()
    victims = []
    for pid, ppid, cmd in procs:
        if pid in (me,) or pid in protect or not cmd:
            continue
        if "hub.py" in cmd:
            continue
        if any(re.search(p, cmd, re.I) for p in patterns):
            victims.append((pid, cmd))
    for pid, cmd in victims:
        hub_log(f"stopping stray process {pid}: {cmd[:160]}")
        kill_tree(pid)
    return len(victims)


# ---------------------------------------------------------------------------
# Feed: parse the bots' own log files into trade / signal events
# ---------------------------------------------------------------------------
LINE_RE = re.compile(r"^\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\]\s?(.*)$")
FEED = deque(maxlen=1500)
FEED_LOCK = threading.Lock()


def classify(src, msg):
    m = msg.strip()
    if src == "equity":
        if m.startswith("BUY "):
            return "buy"
        if m.startswith("SELL "):
            return "sell"
        if "ERROR" in m:
            return "error"
        if "-> no trade" in m or "signal but" in m:
            return "hold"
        return "info"
    if src == "crypto":
        if m.startswith("open:") or m.startswith("Cash="):
            return None  # 30-second heartbeat noise; live values come from the portfolio instead
        if m.startswith("OPEN "):
            return "open"
        if "LIQUIDATED" in m:
            return "liq"
        if m.startswith("TIME EXIT"):
            return "exit"
        if m.startswith("Could not"):
            return "error"
        return "info"
    if src == "telegram":
        if "candidate:" in m or "LEVERAGE SIGNAL" in m:
            return "signal"
        if "error" in m.lower():
            return "error"
        return "info"
    return "info"


class LogFollower:
    def __init__(self, path, src):
        self.path, self.src, self.pos, self.partial = path, src, 0, ""

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.pos:  # truncated / rotated
            self.pos = 0
        if size == self.pos:
            return
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self.pos)
            data = f.read()
            self.pos = f.tell()
        data = self.partial + data
        lines = data.split("\n")
        self.partial = lines.pop()
        new = []
        for line in lines:
            mm = LINE_RE.match(line.rstrip("\r"))
            if not mm:
                continue
            kind = classify(self.src, mm.group(2))
            if kind is None:
                continue
            new.append({"ts": mm.group(1), "src": self.src, "kind": kind, "text": mm.group(2).strip()})
        if new:
            with FEED_LOCK:
                FEED.extend(new)


def hub_event(kind, text):
    with FEED_LOCK:
        FEED.append({"ts": now_iso(), "src": "hub", "kind": kind, "text": text})
    hub_log(text)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
class Service:
    def __init__(self, name, label, cmd, cwd, stray_patterns, env=None, note="", ports=()):
        self.name, self.label, self.cmd, self.cwd = name, label, cmd, cwd
        self.ports = tuple(ports)
        self.stray_patterns, self.env, self.note = stray_patterns, env or {}, note
        self.proc = None
        self.status = "stopped"
        self.started_at = None
        self.restarts = 0
        self.crash_times = deque(maxlen=10)
        self.want_running = False
        self.last_line = ""
        self.lock = threading.Lock()
        self.out_path = os.path.join(LOG_DIR, f"{name}.out.log")

    def available(self):
        if not self.cwd or not os.path.isdir(self.cwd):
            return False
        exe = self.cmd[0]
        return bool(os.path.exists(exe) or shutil.which(exe))

    def start(self, manual=False):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            if not self.available():
                self.status = "missing"
                return
            if manual:
                self.crash_times.clear()
            self.want_running = True
            self.status = "starting"
            env = dict(os.environ)
            env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "FORCE_COLOR": "0", "NO_COLOR": "1"})
            env.update(self.env)
            exe = self.cmd[0] if os.path.exists(self.cmd[0]) else shutil.which(self.cmd[0])
            kw = dict(cwd=self.cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT)
            if IS_WIN:
                kw["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
            else:
                kw["start_new_session"] = True
            try:
                self.proc = subprocess.Popen([exe] + self.cmd[1:], **kw)
            except Exception as e:
                self.status = "crashed"
                hub_event("error", f"{self.label}: failed to start — {e}")
                return
            attach_to_job(self.proc)
            self.started_at = time.time()
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
            hub_event("info", f"{self.label} started (pid {self.proc.pid})")

    def _pump(self, proc):
        try:
            if os.path.exists(self.out_path) and os.path.getsize(self.out_path) > 5_000_000:
                with open(self.out_path, "rb") as f:
                    f.seek(-1_000_000, 2)
                    tail = f.read()
                with open(self.out_path, "wb") as f:
                    f.write(tail)
            with open(self.out_path, "a", encoding="utf-8", errors="replace") as out:
                out.write(f"\n===== {now_iso()} started: {' '.join(self.cmd)} =====\n")
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
                    if line.strip():
                        self.last_line = line.strip()[:300]
                    out.write(line + "\n")
                    out.flush()
        except Exception:
            pass

    def stop(self):
        with self.lock:
            self.want_running = False
            p = self.proc
            if p and p.poll() is None:
                kill_tree(p.pid)
                try:
                    p.wait(timeout=10)
                except Exception:
                    pass
                hub_event("info", f"{self.label} stopped")
            self.status = "stopped" if self.status != "missing" else "missing"
            self.proc = None

    def check(self):
        """Called by the supervisor loop: notices crashes and restarts with back-off."""
        with self.lock:
            p = self.proc
            if p is None:
                if self.want_running and self.status == "starting":
                    return  # waiting out the restart back-off
                if self.status not in ("missing", "crashed"):
                    self.status = "stopped" if self.available() else "missing"
                return
            code = p.poll()
            if code is None:
                if self.status == "starting" and time.time() - (self.started_at or 0) > 8:
                    self.status = "running"
                return
            self.proc = None
            if not self.want_running:
                self.status = "stopped"
                return
            self.crash_times.append(time.time())
            recent = [t for t in self.crash_times if time.time() - t < 600]
            hub_event("error", f"{self.label} exited (code {code}). Last output: {self.last_line[:160]}")
            if len(recent) >= 5:
                self.status = "crashed"
                self.want_running = False
                hub_event("error", f"{self.label} crashed 5 times in 10 minutes — left stopped. "
                                   f"See {self.out_path}")
                return
            self.status = "starting"
            self.restarts += 1
        delay = min(300, 5 * (2 ** (len(recent) - 1)))
        threading.Timer(delay, self._restart_if_wanted).start()

    def _restart_if_wanted(self):
        if self.status == "starting" and (self.proc is None):
            self.want_running = True
            self.start()

    def info(self):
        up = time.time() - self.started_at if (self.proc and self.started_at) else 0
        return {"name": self.name, "label": self.label, "status": self.status,
                "pid": self.proc.pid if self.proc else None, "uptime_s": round(up),
                "restarts": self.restarts, "last_line": self.last_line, "note": self.note,
                "log": self.out_path}


def build_services():
    py = python_exe()
    bot, vibe, sig = CFG["bot_dir"], CFG["vibe_dir"], CFG["signal_agent_dir"]
    vibe_exe = shutil.which("vibe-trading") or "vibe-trading"
    svcs = [
        Service("vibe", "Vibe-Trading agent (:8899 / UI :5899)",
                [vibe_exe, "dev", "--frontend-dir", os.path.join(vibe, "frontend")], vibe,
                [r"vibe-trading(\.exe)?\W+dev", r"cli\._legacy\s+serve", r"vite(\.js)?\W.*--port\s+5899",
                 r"npm-cli\.js.*run dev.*5899", r"npm(\.CMD)?\W+run dev -- --port 5899"],
                note="research agent the equity bot asks for buy/sell/hold", ports=(8899, 5899)),
        Service("telegram", "Telegram listener", [py, "telegram_listener.py"], bot,
                [r"telegram_listener\.py"], note="reads your channel(s) for $TICKERs + position screenshots"),
        Service("paperbot", "Equity paper bot (AI)",
                [py, "paperbot.py", "--watchlist", CFG["equity_watchlist"],
                 "--interval-minutes", str(CFG["equity_interval_minutes"])], bot,
                [r"paperbot\.py"], note="AAPL/MSFT/NVDA/GOOGL + Telegram tickers, every 30 min in market hours"),
        Service("leverage", "Crypto leverage paper bot", [py, "leverage_bot.py"], bot,
                [r"leverage_bot\.py"], note="opens perps from Telegram screenshots, checks every 30s"),
    ]
    svcs.append(Service("prices", "Live price feed (yfinance / MEXC)",
                        [py, os.path.join(HUB_DIR, "prices.py"), bot], HUB_DIR, [r"trading-hub.prices\.py"],
                        note="live prices for open positions"))
    if os.path.isdir(sig):
        node = shutil.which("node") or "node"
        svcs.append(Service("signal_agent", "Signal agent (:3000)", [node, "server.js"], sig,
                            [r"trading-signal-agent.*server\.js"], note="multi-channel signal voting (mock mode)",
                            ports=(3000,)))
    return svcs


SERVICES = build_services()
SVC = {s.name: s for s in SERVICES}

# ---------------------------------------------------------------------------
# Live prices
# ---------------------------------------------------------------------------
EQ_PRICES, CRYPTO_PRICES = {}, {}
PRICE_META = {"eq_at": None, "crypto_at": None}


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


PRICES_PATH = os.path.join(LOG_DIR, "prices.json")


def refresh_prices():
    """Prices come from prices.py (its own process) so nothing can stall the hub."""
    st = read_json(PRICES_PATH, {}) or {}
    EQ_PRICES.update(st.get("equity") or {})
    CRYPTO_PRICES.update(st.get("crypto") or {})
    PRICE_META["eq_at"] = st.get("eq_at")
    PRICE_META["crypto_at"] = st.get("crypto_at")


# ---------------------------------------------------------------------------
# State for the dashboard
# ---------------------------------------------------------------------------
def us_market_open():
    try:
        import zoneinfo
        n = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        if n.weekday() >= 5:
            return False
        return (n.hour, n.minute) >= (9, 30) and n.hour < 16
    except Exception:
        return None


def equity_state():
    pf = read_json(os.path.join(CFG["bot_dir"], "portfolio.json"),
                   {"cash": 10000.0, "positions": {}, "trades": [], "starting_cash": 10000.0})
    positions, unreal, mkt = [], 0.0, 0.0
    for t, p in (pf.get("positions") or {}).items():
        price = EQ_PRICES.get(t, p.get("avg_price"))
        qty, avg = p.get("qty", 0), p.get("avg_price", 0.0)
        u = (price - avg) * qty
        unreal += u
        mkt += price * qty
        positions.append({"ticker": t, "qty": qty, "avg_price": avg, "price": price, "value": price * qty,
                          "upnl": u, "upnl_pct": ((price / avg - 1) * 100) if avg else 0})
    trades = list(reversed(pf.get("trades") or []))
    sells = [t for t in trades if t.get("action") == "sell" and t.get("pnl") is not None]
    realized = sum(t["pnl"] for t in sells)
    wins = sum(1 for t in sells if t["pnl"] > 0)
    return {"cash": pf.get("cash", 0), "starting_cash": pf.get("starting_cash", 10000.0),
            "positions": positions, "trades": trades[:200], "unrealized": unreal, "realized": realized,
            "win_rate": round(wins / len(sells) * 100) if sells else None,
            "total_value": pf.get("cash", 0) + mkt, "prices_at": PRICE_META["eq_at"]}


def leverage_state():
    pf = read_json(os.path.join(CFG["bot_dir"], "leverage_portfolio.json"),
                   {"cash": 10000.0, "starting_cash": 10000.0, "open_positions": [], "closed_trades": []})
    opn, unreal, margin = [], 0.0, 0.0
    for p in pf.get("open_positions") or []:
        price = CRYPTO_PRICES.get(p.get("symbol"), p.get("entry_price"))
        entry, lev, m = p.get("entry_price") or 0, p.get("leverage") or 1, p.get("margin") or 0
        ch = (price - entry) / entry if entry else 0
        if p.get("side") == "short":
            ch = -ch
        u = m * lev * ch
        unreal += u
        margin += m
        liq = p.get("liquidation_price") or 0
        opn.append(dict(p, base=str(p.get("symbol", "")).split("/")[0], price=price, upnl=u,
                        upnl_pct=(u / m * 100) if m else 0,
                        dist_to_liq_pct=(abs(price - liq) / price * 100) if price else 0))
    closed = list(reversed(pf.get("closed_trades") or []))
    for c in closed:
        c["base"] = str(c.get("symbol", "")).split("/")[0]
    realized = sum(c.get("pnl") or 0 for c in closed)
    liqs = sum(1 for c in closed if c.get("close_reason") == "liquidated")
    wins = sum(1 for c in closed if (c.get("pnl") or 0) > 0)
    return {"cash": pf.get("cash", 0), "starting_cash": pf.get("starting_cash", 10000.0), "open": opn,
            "closed": closed[:200], "unrealized": unreal, "realized": realized, "margin_in_use": margin,
            "equity": pf.get("cash", 0) + margin + unreal, "wins": wins,
            "losses": len(closed) - wins - liqs, "liquidations": liqs, "prices_at": PRICE_META["crypto_at"]}


def signals_state():
    bot = CFG["bot_dir"]
    return {"tickers": (read_json(os.path.join(bot, "candidates.json"), []) or [])[-60:],
            "leverage": (read_json(os.path.join(bot, "leverage_candidates.json"), []) or [])[-60:]}


STARTED = now_iso()


def full_state():
    refresh_prices()
    with FEED_LOCK:
        feed = sorted(FEED, key=lambda e: e["ts"], reverse=True)[:400]
    return {"hub": {"version": 1, "started": STARTED, "root": BRAIN_DIR, "logs": LOG_DIR},
            "services": [s.info() for s in SERVICES],
            "equity": equity_state(), "leverage": leverage_state(), "signals": signals_state(),
            "feed": feed, "market": {"us_open": us_market_open()}}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(BRAIN_DIR, "trading-command-center")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".png": "image/png",
        ".webmanifest": "application/manifest+json", ".json": "application/json", ".css": "text/css"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            return self._json(full_state())
        if path == "/api/ping":
            return self._json({"ok": True, "hub": "brain-trading"})
        # Local copy of the Command Center (handy offline); the installed app uses GitHub Pages.
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if fp.startswith(STATIC_DIR) and os.path.isfile(fp):
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(os.path.splitext(fp)[1], "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        if parts[:2] == ["api", "service"] and len(parts) == 4 and parts[2] in SVC:
            s, act = SVC[parts[2]], parts[3]
            if act == "start":
                s.start(manual=True)
            elif act == "stop":
                s.stop()
            elif act == "restart":
                threading.Thread(target=lambda: (s.stop(), time.sleep(1), s.start(manual=True)), daemon=True).start()
            else:
                return self._json({"error": "bad action"}, 400)
            return self._json({"ok": True, "service": s.info()})
        if parts == ["api", "shutdown"]:
            self._json({"ok": True})
            threading.Thread(target=shutdown, daemon=True).start()
            return
        self._json({"error": "not found"}, 404)


# ---------------------------------------------------------------------------
def open_app():
    if os.environ.get("BRAIN_NO_OPEN"):
        return
    try:
        if IS_WIN:
            for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                         os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                         os.path.join(os.environ.get("LOCALAPPDATA", ""), "")):
                proxy = os.path.join(base, "Google", "Chrome", "Application", "chrome_proxy.exe")
                if CFG.get("chrome_app_id") and os.path.exists(proxy):
                    subprocess.Popen([proxy, "--profile-directory=Default", f"--app-id={CFG['chrome_app_id']}"],
                                     creationflags=0x08000000)
                    return
        elif sys.platform == "darwin":
            r = subprocess.run(["open", "-na", "Google Chrome", "--args", f"--app={CFG['app_url']}"])
            if r.returncode == 0:
                return
        webbrowser.open(CFG["app_url"])
    except Exception:
        webbrowser.open(CFG["app_url"])


def hub_alive():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=2) as r:
            return json.loads(r.read()).get("hub") == "brain-trading"
    except Exception:
        return False


_server = None


def shutdown(*_):
    hub_log("hub shutting down — stopping all services")
    for s in SERVICES:
        try:
            s.stop()
        except Exception:
            pass
    if _server:
        threading.Thread(target=_server.shutdown, daemon=True).start()
    time.sleep(1)
    os._exit(0)


def main():
    global _server
    args = sys.argv[1:]
    if "--stop" in args:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/shutdown", method="POST")
            urllib.request.urlopen(req, timeout=5)
            print("hub stopped")
        except Exception:
            print("hub not running")
        return

    if hub_alive():
        hub_log("hub already running — opening the Command Center")
        if "--no-open" not in args:
            open_app()
        return

    try:
        _server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        hub_log(f"port {PORT} busy ({e}); opening app anyway")
        if "--no-open" not in args:
            open_app()
        return

    hub_log(f"BRAIN trading hub starting in {BRAIN_DIR}")
    # 1) take over: stop leftover copies from the old PowerShell windows (prevents
    #    'database is locked' and port 8899 clashes), plus their parent windows.
    patterns = [p for s in SERVICES for p in s.stray_patterns]
    patterns += [r"-NoExit.*(telegram_listener|paperbot|leverage_bot|vibe-trading|server\.js)"]
    n = kill_strays(patterns) if CFG.get("kill_strays") else 0
    n += kill_port_owners([p for s in SERVICES for p in s.ports])
    if n:
        time.sleep(2)

    # 2) follow the bots' own logs for the feed (full history first, then live)
    followers = [LogFollower(os.path.join(CFG["bot_dir"], "paperbot.log"), "equity"),
                 LogFollower(os.path.join(CFG["bot_dir"], "leverage_bot.log"), "crypto"),
                 LogFollower(os.path.join(CFG["bot_dir"], "telegram_listener.log"), "telegram")]
    for f in followers:
        f.poll()

    # 3) start services
    for s in SERVICES:
        if CFG["autostart"].get(s.name, True):
            s.start()
            if s.name == "vibe":
                time.sleep(3)
        else:
            s.status = "stopped" if s.available() else "missing"


    def supervisor():
        import faulthandler
        while True:
            try:
                faulthandler.dump_traceback_later(90, repeat=False, file=_console or sys.__stderr__, exit=False)
            except Exception:
                pass
            for s in SERVICES:
                try:
                    s.check()
                except Exception as e:
                    hub_log(f"supervisor error {s.name}: {e}")
            for f in followers:
                try:
                    f.poll()
                except Exception:
                    pass
            time.sleep(2)

    threading.Thread(target=supervisor, daemon=True).start()
    if "--no-open" not in args:
        threading.Timer(2.0, open_app).start()
    try:
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
    except Exception:
        pass
    hub_log(f"hub listening on http://127.0.0.1:{PORT}")
    _server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        hub_log("HUB CRASHED:\n" + traceback.format_exc())
        raise
