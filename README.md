# Adeel's Trading Command Center

A single-file, Ross Cameron / Warrior-style momentum dashboard: pre-market gap scanner,
watchlist, quotes & news, Gold/Forex, AI trade plans, and a trade journal. It's one
self-contained `index.html` (no backend, no build step) that runs the same on **macOS**,
**Windows**, and mobile.

> **API keys & your data stay on each device.** Keys (Alpha Vantage, Finnhub, Twelve Data,
> Anthropic), your watchlist, and your trade log are stored only in that device's browser
> storage (`localStorage`). Nothing is committed to this repo and nothing is sent anywhere
> except each provider's official HTTPS API. You enter your keys **once per device**
> (Mac + Windows + phone are separate).

---

## Keep Mac and Windows in sync (git is the source of truth)

The dashboard lives in this repo. Both machines clone it once, then stay aligned with
`git pull` / `git push`.

**One-time, on each computer:**

```bash
git clone https://github.com/AdeelSiddiqui38/trading-command-center.git
```

**After you make a change on either machine:**

```bash
git add -A
git commit -m "describe change"
git push
```

**On the other machine, before you use it:**

```bash
git pull
```

That's the whole sync loop. Whatever is on `main` is what both machines run.

---

## Run it on Windows (installable app — recommended)

A Progressive Web App (PWA) needs to be *served* (not opened as a raw file) for the
"Install app" option and offline caching to work. The included launcher handles that.

1. Double-click **`Trading Command Center (Windows).bat`**.
   - If Python is installed it starts a tiny local server and opens
     `http://localhost:8765` in your default browser.
   - If Python isn't found it just opens `index.html` directly (still fully usable —
     only the installable/offline PWA features need the server).
2. In **Microsoft Edge** (or Chrome), open the **...** menu -> **Apps** -> **Install this
   site as an app** (Edge often shows an install icon in the address bar). Name it *Trading
   Command Center* and pin it to the taskbar.
3. Launch it any time from the taskbar/Start menu -- it opens in its own window, just like
   the Mac app.

> No Python? Install it from https://python.org (check "Add to PATH"), or run
> `npx serve` in this folder, then open the printed `localhost` URL. The `.bat` will use
> Python automatically once it's installed.

---

## Run it on macOS

Any of these work -- they all open the same `index.html`:

- **Double-click `index.html`** to open it in your default browser, **or**
- Double-click **`launch-mac.command`** (may need `chmod +x launch-mac.command` once), **or**
- Rebuild the native `.app` wrapper: run `bash build-macos-app.sh`, which produces
  `Trading Command Center.app` you can drag to `/Applications`.

For an installable PWA on Mac too, serve the folder (`python3 -m http.server 8765`) and use
Chrome/Edge -> **Install**.

---

## Optional: host it once, install everywhere (GitHub Pages)

Because there are no secrets in the code, you can serve it over HTTPS for free and install
the *same* PWA on every device (Mac, Windows, phone). Pushing to `main` then updates all of
them:

1. Repo -> **Settings -> Pages -> Build and deployment -> Source: Deploy from a branch ->
   `main` / root -> Save.**
2. Wait ~1 minute, then open `https://adeelsiddiqui38.github.io/trading-command-center/`.
3. Install the PWA from that URL on any device.

(Keys/watchlist/log are still per-device, since they're stored in each browser.)

---

## Files

| File | Purpose |
| --- | --- |
| `index.html` | The entire dashboard (source of truth) |
| `manifest.webmanifest`, `sw.js`, `icon-192.png`, `icon-512.png`, `apple-touch-icon.png` | PWA install + offline shell |
| `Trading Command Center (Windows).bat` | Windows launcher (serves locally, opens browser) |
| `launch-mac.command` | macOS double-click launcher |
| `build-macos-app.sh` | Rebuilds the macOS `.app` wrapper from `index.html` |

---

*This tool is for research and personal workflow only -- it is not financial advice, and it
does not place trades or touch your broker. Always verify live prices on Questrade /
TradingView before trading.*
