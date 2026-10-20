#!/bin/bash
# Live stereo depth from the phone's main + ultra-wide cameras.
# Rebuilds and pushes the phone streamer when StereoStream.java changed, then opens the viewer.
# Extra arguments go to viewer.py (e.g. ./live.sh --focus 0 for autofocus, --headless --duration 30).
set -e
cd "$(dirname "$0")"
SDK=~/Library/Android/sdk
JAR=build_stream/stream.jar
if [ ! -f "$JAR" ] || [ StereoStream.java -nt "$JAR" ]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@17 PATH=/opt/homebrew/opt/openjdk@17/bin:$PATH
  rm -rf build_stream && mkdir -p build_stream/classes
  javac -source 11 -target 11 -nowarn -cp $SDK/platforms/android-36/android.jar -d build_stream/classes StereoStream.java
  $SDK/build-tools/37.0.0/d8 --min-api 33 --lib $SDK/platforms/android-36/android.jar --output build_stream $(find build_stream/classes -name '*.class')
  (cd build_stream && zip -q stream.jar classes.dex)
fi
adb get-state >/dev/null || { echo "Phone not connected (USB debugging on?)"; exit 1; }
adb shell mkdir -p /data/local/tmp/stereo
adb push "$JAR" /data/local/tmp/stereo/stream.jar >/dev/null
exec .venv/bin/python viewer.py "$@"
