#!/bin/bash
# Builds ~/Applications/PhoneStereo.app: a native SwiftUI app (mac/PhoneStereoApp.swift) that finds the
# phone over Bonjour, pairs with its code, asks for camera access and runs the Python vision modes from
# this folder as child processes (so camera + local-network permission belong to the app).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/PhoneStereo.app"
BUILD="$HERE/mac/build"
mkdir -p "$BUILD"
xcrun swiftc -parse-as-library -O -target arm64-apple-macos14.0 \
  "$HERE/mac/PhoneStereoApp.swift" -o "$BUILD/PhoneStereo"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/PhoneStereo" "$APP/Contents/MacOS/PhoneStereo"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>PhoneStereo</string>
  <key>CFBundleDisplayName</key><string>PhoneStereo</string>
  <key>CFBundleIdentifier</key><string>dev.saikiran.phonestereo.mac</string>
  <key>CFBundleExecutable</key><string>PhoneStereo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSCameraUsageDescription</key><string>Sign Check uses the Mac camera as a third view of your hand.</string>
  <key>NSLocalNetworkUsageDescription</key><string>PhoneStereo finds and connects to the PhoneStereo app on your phone.</string>
  <key>NSBonjourServices</key><array><string>_phonestereo._tcp</string></array>
  <key>PSProjectDir</key><string>$HERE</string>
</dict></plist>
EOF
codesign --force --sign - "$APP" >/dev/null
echo "built $APP"
