#!/bin/bash
# =====================================================================
#  BRAIN trading setup (macOS) - run once:
#      bash ~/Documents/BRAIN/trading-command-center/brain-hub/setup-mac.command
#  or double-click it after cloning.
#  * Keeps the Command Center source in ~/Documents/BRAIN (git-synced with Windows)
#  * Installs the background hub in ~/Documents/BRAIN/trading-hub
#  * Puts ONE icon on the Desktop: "Adeel's Trading Command Center"
#  The hub runs any bots found in ~/Documents/BRAIN/trading-paperbot.
#  Run the Telegram listener on ONE machine only (shared login session).
# =====================================================================
set -e
BRAIN="$HOME/Documents/BRAIN"
REPO="$BRAIN/trading-command-center"
HUB="$BRAIN/trading-hub"
mkdir -p "$BRAIN" "$HUB"

if [ -d "$REPO/.git" ]; then git -C "$REPO" pull --ff-only -q || true
else git clone -q https://github.com/AdeelSiddiqui38/trading-command-center.git "$REPO"; fi

cp "$REPO/brain-hub/hub.py" "$REPO/brain-hub/prices.py" "$HUB/"

cat > "$HUB/launch.sh" <<'EOF'
#!/bin/bash
BRAIN="$HOME/Documents/BRAIN"
( cd "$BRAIN/trading-command-center" && git pull --ff-only -q && cp brain-hub/hub.py brain-hub/prices.py "$BRAIN/trading-hub/" ) >/dev/null 2>&1 &
sleep 2
cd "$BRAIN/trading-hub"
nohup python3 hub.py >> logs-launch.txt 2>&1 &
EOF
chmod +x "$HUB/launch.sh"

APP="$HOME/Desktop/Adeel's Trading Command Center.app"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script quoted form of \"$HUB/launch.sh\""
# use the dashboard icon if sips/iconutil are available
if command -v sips >/dev/null && [ -f "$REPO/icon-512.png" ]; then
  ICONSET="$(mktemp -d)/icon.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do sips -z $s $s "$REPO/icon-512.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null; done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/applet.icns" 2>/dev/null || true
  touch "$APP"
fi
echo "Done. Double-click 'Adeel's Trading Command Center' on your Desktop."
