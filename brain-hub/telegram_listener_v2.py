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
from datetime import datetime, timezone

from telethon import TelegramClient, events

import telegram_core as core          # original file: API_ID, API_HASH, CHANNELS, helpers
import image_signal_parser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "telegram_state.json")
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


# ---------------------------------------------------------------- helpers
def _openai():
    return image_signal_parser._client_lazy()


def transcribe(audio_bytes):
    f = io.BytesIO(audio_bytes)
    f.name = "voice.ogg"
    r = _openai().audio.transcriptions.create(model="whisper-1", file=f)
    return (r.text or "").strip()


def extract(text):
    r = _openai().chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": EXTRACT_PROMPT},
                  {"role": "user", "content": text[:6000]}],
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(r.choices[0].message.content)
    except Exception:
        return {"is_trade_signal": False, "summary": text[:120]}


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


async def catch_up(client, state):
    for ch in core.CHANNELS:
        try:
            entity = await client.get_entity(ch)
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


async def main():
    if not core.API_ID or not core.API_HASH:
        raise SystemExit("Set API_ID and API_HASH in telegram_core.py")
    client = TelegramClient(core.SESSION_PATH, core.API_ID, core.API_HASH)
    state = load_state()

    @client.on(events.NewMessage(chats=core.CHANNELS))
    async def on_new(event):
        chat = await event.get_chat()
        name = getattr(chat, "username", None) or getattr(chat, "title", "unknown")
        await handle(event.message, name, live=True)
        state[str(name)] = max(int(state.get(str(name), 0)), event.message.id)
        save_state(state)

    await client.start()
    log(f"Listening on channels: {core.CHANNELS} (text + voice + photos)")
    await catch_up(client, state)
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
