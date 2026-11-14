"""
Telegram listener v2 - reads Sona's channel (and any others in CHANNELS).

What's new vs v1:
  * VOICE NOTES: downloaded, transcribed (OpenAI), and turned into a signal.
  * Text: $TICKER mentions as before, plus crypto calls ("BTC long 20x ...")
    parsed by the same AI extractor.
  * Photos: position screenshots as before (image_signal_parser).
  * CATCH-UP: on start it reads messages it missed while the PC was off.
    Old messages are logged/shown but only signals newer than
    MAX_SIGNAL_AGE_MIN are paper-traded (no trading on stale calls).
  * Symbols are normalised to e.g. SOLUSDT so the leverage bot can price them.

Credentials / channel list come from telegram_core.py (your original
listener, saved under a new name) so they never need to be retyped.
Paper trading only.
"""
import asyncio
import io
import json
import os
import re
import sqlite3
import time
import traceback
from datetime import datetime, timezone

from telethon import TelegramClient, events

import telegram_core as core          # original file: API_ID, API_HASH, CHANNELS, helpers
import image_signal_parser

try:                                  # optional: not present on every machine this file is synced to
    import singleton
except ImportError:
    singleton = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "telegram_state.json")
FX_JOURNAL_PATH = os.path.join(BASE_DIR, "fx_signals.json")   # forex/gold calls: journal only, never traded
KIND = {}                         # channel name -> crypto | fx | auto (filled when channels are resolved)
MAX_SIGNAL_AGE_MIN = 30          # only trade calls fresher than this
CATCHUP_LIMIT = 40               # messages per channel to look back on start
DEFAULT_LEVERAGE = 10            # used when a call gives no leverage (flagged in the log)

log = core.log
TICKER_RE = re.compile(r"\$([A-Z]{1,6})\b")
TRADEY_RE = re.compile(r"\b(long|short|buy|sell|entry|tp|sl|target|stop|leverage|\d+x)\b", re.I)

EXTRACT_PROMPT = """You read posts from a crypto futures trader's Telegram channel.
The text may be English, Urdu or Hindi (or a mix) and may be a voice-note transcript.
Decide whether it contains a concrete NEW trade call (a position to open).

Respond with ONLY one JSON object, no markdown:
{"is_trade_signal": true|false,
 "symbol": "<base asset like BTC, SOL, PEPE or empty>",
 "side": "long"|"short"|"",
 "leverage": <integer or null>,
 "entry": "<price/zone or empty>",
 "take_profit": "<targets or empty>",
 "stop_loss": "<price or empty>",
 "summary": "<one short English sentence of what was said>"}

Commentary, market views without a clear position, closing/updating an old trade,
or general talk => is_trade_signal false (still give the summary)."""


FX_PROMPT = """You read posts from a Telegram trading channel (forex, gold, indices, crypto or stocks).
The text may be English, Urdu or Hindi (or a mix) and may be a voice-note transcript.
Decide whether it contains a concrete NEW trade call (a position to open) and which market it is.

Respond with ONLY one JSON object, no markdown:
{"is_trade_signal": true|false,
 "asset_class": "fx"|"gold"|"index"|"crypto"|"stock"|"",
 "symbol": "<e.g. XAUUSD, EURUSD, US30, BTC, AAPL or empty>",
 "side": "long"|"short"|"",
 "leverage": <integer or null>,
 "entry": "<price/zone or empty>",
 "take_profit": "<targets or empty>",
 "stop_loss": "<price or empty>",
 "summary": "<one short English sentence of what was said>"}

Adverts, testimonials, results screenshots, commentary or closing an old trade => is_trade_signal false."""


# ---------------------------------------------------------------- helpers
def _openai():
    return image_signal_parser._client_lazy()


def transcribe(audio_bytes):
    f = io.BytesIO(audio_bytes)
    f.name = "voice.ogg"
    r = _openai().audio.transcriptions.create(model="whisper-1", file=f)
    return (r.text or "").strip()


def extract(text, prompt=None):
    r = _openai().chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": prompt or EXTRACT_PROMPT},
                  {"role": "user", "content": text[:6000]}],
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(r.choices[0].message.content)
    except Exception:
        return {"is_trade_signal": False, "summary": text[:120]}


def journal_fx(res, channel_name, source, tradeable):
    """Forex/gold/index calls are journaled only (no bot paper-trades them yet)."""
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "channel": channel_name, "source": source,
           "fresh": bool(tradeable)}
    row.update({k: res.get(k) for k in ("asset_class", "symbol", "side", "entry", "take_profit", "stop_loss", "summary")})
    try:
        with open(FX_JOURNAL_PATH, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        rows = []
    rows.append(row)
    tmp = FX_JOURNAL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows[-2000:], f, indent=2)
    os.replace(tmp, FX_JOURNAL_PATH)
    log(f"[{channel_name}] FX/GOLD SIGNAL (journal only, {source}): {str(res.get('side') or '').upper()} "
        f"{res.get('symbol')} entry={res.get('entry')} tp={res.get('take_profit')} sl={res.get('stop_loss')}")


def norm_symbol(sym):
    s = re.sub(r"[^A-Z0-9]", "", str(sym or "").upper())
    if not s:
        return ""
    if s.endswith("USDT"):
        return s
    if s.endswith("USD"):
        s = s[:-3]
    return s + "USDT"


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def minutes_old(msg):
    d = msg.date
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).total_seconds() / 60


# ---------------------------------------------------------------- core
async def handle(msg, channel_name, live):
    age = minutes_old(msg)
    tradeable = live or age <= MAX_SIGNAL_AGE_MIN
    tag = "" if tradeable else f" (missed, {age/60:.1f}h old - not traded)"
    text = (msg.raw_text or "").strip()

    try:
        # ---- voice notes
        if msg.voice or (msg.audio and not text):
            data = await msg.download_media(bytes)
            transcript = transcribe(data)
            log(f"[{channel_name}] VOICE ({getattr(msg.file, 'duration', '?')}s): {transcript[:160]}{tag}")
            await act_on_text(transcript, channel_name, tradeable, source="voice")
            return

        # ---- photos (position screenshots)
        if msg.photo and KIND.get(channel_name, "crypto") == "fx":
            if text:
                await act_on_text(text, channel_name, tradeable, source="caption")
            else:
                log(f"[{channel_name}] photo (fx channel, not parsed){tag}")
            return
        if msg.photo:
            data = await msg.download_media(bytes)
            res = image_signal_parser.extract_signal_from_image(data)
            if res.get("is_trade_signal"):
                record_leverage(res.get("symbol"), res.get("side"), res.get("leverage"),
                                channel_name, tradeable, "screenshot", tag)
            else:
                log(f"[{channel_name}] photo - not a position screenshot{tag}")
            if text:
                await act_on_text(text, channel_name, tradeable, source="caption")
            return

        # ---- text
        if text and KIND.get(channel_name, "crypto") != "crypto":
            if TRADEY_RE.search(text) or TICKER_RE.search(text):
                await act_on_text(text, channel_name, tradeable, source="text")
            else:
                log(f"[{channel_name}] message: {text[:100]}{tag}")
            return
        if text:
            tickers = TICKER_RE.findall(text)
            for t in tickers:
                if tradeable:
                    core.add_candidate(t, channel_name, text)
                log(f"[{channel_name}] candidate: {t} -- {text[:80]}{tag}")
            if not tickers and TRADEY_RE.search(text):
                await act_on_text(text, channel_name, tradeable, source="text")
            elif not tickers:
                log(f"[{channel_name}] message: {text[:100]}{tag}")
            return

        log(f"[{channel_name}] {type(msg.media).__name__ if msg.media else 'empty'} message (ignored){tag}")
    except Exception as e:
        log(f"[{channel_name}] error handling message {msg.id}: {e}")


async def act_on_text(text, channel_name, tradeable, source):
    if not text:
        return
    kind = KIND.get(channel_name, "crypto")
    if kind != "crypto":
        res = extract(text, FX_PROMPT)
        if not res.get("is_trade_signal"):
            log(f"[{channel_name}] {source}: no new trade call - {(res.get('summary') or '')[:140]}")
            return
        ac = str(res.get("asset_class") or "").lower()
        if kind == "fx" or ac in ("fx", "gold", "index", ""):
            journal_fx(res, channel_name, source, tradeable)
        elif ac == "stock":
            sym = re.sub(r"[^A-Z]", "", str(res.get("symbol") or "").upper())
            if sym and tradeable:
                core.add_candidate(sym, channel_name, text)
            log(f"[{channel_name}] candidate ({source}): {sym} -- {(res.get('summary') or '')[:80]}")
        else:
            record_leverage(res.get("symbol"), res.get("side"), res.get("leverage"), channel_name, tradeable, source)
        return
    res = extract(text)
    summary = res.get("summary") or ""
    if not res.get("is_trade_signal"):
        log(f"[{channel_name}] {source}: no new trade call - {summary[:140]}")
        return
    extra = " ".join(f"{k}={res[k]}" for k in ("entry", "take_profit", "stop_loss") if res.get(k))
    record_leverage(res.get("symbol"), res.get("side"), res.get("leverage"),
                    channel_name, tradeable, source, (" " + extra) if extra else "")


def record_leverage(symbol, side, leverage, channel_name, tradeable, source, note=""):
    sym = norm_symbol(symbol)
    side = str(side or "").lower()
    lev_note = ""
    try:
        lev = int(leverage) if leverage else None
    except Exception:
        lev = None
    if not lev:
        lev, lev_note = DEFAULT_LEVERAGE, f" (no leverage given, using {DEFAULT_LEVERAGE}x)"
    if not sym or side not in ("long", "short"):
        log(f"[{channel_name}] incomplete {source} signal: symbol={symbol} side={side}{note}")
        return
    if tradeable:
        core.add_leverage_candidate(sym, side, lev, channel_name)
        log(f"[{channel_name}] LEVERAGE SIGNAL ({source}): {side.upper()} {sym} {lev}x{lev_note}{note}")
    else:
        log(f"[{channel_name}] missed {source} call: {side.upper()} {sym} {lev}x{note}")


async def resolve_channels(client):
    """Each configured channel by @username, or by (part of) its title among your chats. Returns entities."""
    found = []
    dialogs = None
    for c in core.CHANNEL_CONFIG:
        m, ent = c["match"], None
        if re.fullmatch(r"@?[A-Za-z0-9_]{5,}", m):
            try:
                ent = await client.get_entity(m)
            except Exception:
                ent = None
        if ent is None:
            if dialogs is None:
                dialogs = [d async for d in client.iter_dialogs()]
            want = m.lower()
            ent = next((d.entity for d in dialogs if want in (d.name or "").lower()), None)
        if ent is None:
            log(f"[{m}] channel not found in your chats - skipped")
            continue
        name = getattr(ent, "username", None) or getattr(ent, "title", m)
        KIND[str(name)] = c.get("kind", "crypto")
        found.append(ent)
    return found


async def catch_up(client, state, entities):
    for entity in entities:
        ch = getattr(entity, "title", "?")
        try:
            name = getattr(entity, "username", None) or getattr(entity, "title", ch)
            last_id = int(state.get(str(name), 0))
            msgs = []
            async for m in client.iter_messages(entity, limit=CATCHUP_LIMIT,
                                                min_id=last_id if last_id else 0):
                msgs.append(m)
            msgs.reverse()
            if not last_id:
                msgs = msgs[-5:]   # first run: just show the latest few
            log(f"[{name}] connected - {len(msgs)} message(s) since last check")
            for m in msgs:
                await handle(m, name, live=False)
                state[str(name)] = max(int(state.get(str(name), 0)), m.id)
            save_state(state)
        except Exception as e:
            log(f"[{ch}] catch-up failed: {e}")


async def run_once(state):
    client = TelegramClient(core.SESSION_PATH, core.API_ID, core.API_HASH)

    async def on_new(event):
        chat = await event.get_chat()
        name = getattr(chat, "username", None) or getattr(chat, "title", "unknown")
        await handle(event.message, name, live=True)
        state[str(name)] = max(int(state.get(str(name), 0)), event.message.id)
        save_state(state)

    try:
        await client.start()
        entities = await resolve_channels(client)
        client.add_event_handler(on_new, events.NewMessage(chats=entities))
        log("Listening on channels: " + ", ".join(f"{n} ({k})" for n, k in KIND.items()) + " (text + voice + photos)")
        await catch_up(client, state, entities)
        await client.run_until_disconnected()
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main():
    if not core.API_ID or not core.API_HASH:
        raise SystemExit("Set API_ID and API_HASH in telegram_core.py")
    if singleton:
        singleton.guard("telegram_listener", log)
    state = load_state()

    # Supervisor: a locked session database or any other crash is logged with a
    # traceback and retried with back-off instead of killing the process (the
    # hub gives up on a service after 5 quick crashes).
    delay = 5
    while True:
        started = time.time()
        try:
            await run_once(state)
            log("connection closed - reconnecting")
        except sqlite3.OperationalError as e:
            log(f"session database error ({e}) - another process may be using "
                f"telegram_session.session; retrying in {delay}s")
        except Exception:
            log("listener crashed, will retry:\n" + traceback.format_exc())
        if time.time() - started > 120:
            delay = 5
        await asyncio.sleep(delay)
        delay = min(delay * 2, 120)


if __name__ == "__main__":
    asyncio.run(main())
