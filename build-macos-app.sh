#!/bin/bash
# Rebuilds "Trading Command Center.app" from index.html (macOS).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/Trading Command Center.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/launcher" <<'SH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
open "$DIR/index.html"
SH
chmod +x "$APP/Contents/MacOS/launcher"

# App bundles need the shell inside Resources; copy the whole app so relative PWA files resolve.
cp "$DIR/index.html" "$APP/Contents/Resources/index.html"
for f in manifest.webmanifest sw.js icon-192.png icon-512.png apple-touch-icon.png; do
  [ -f "$DIR/$f" ] && cp "$DIR/$f" "$APP/Contents/Resources/$f"
done
[ -f "$DIR/icon.icns" ] && cp "$DIR/icon.icns" "$APP/Contents/Resources/icon.icns"

cat > "$APP/Contents/Info.plist" <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Trading Command Center</string>
  <key>CFBundleDisplayName</key><string>Trading Command Center</string>
  <key>CFBundleIdentifier</key><string>com.adeel.tradingcommandcenter</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
</dict>
</plist>
PL
echo "Built: $APP"
