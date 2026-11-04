#!/bin/bash
# Build the phone app's StereoStream into a dex jar and push it over USB (no APK install needed).
# SurfaceTouch --zoom runs it as the shell user with a sensor crop on each lens.
set -e
cd "$(dirname "$0")"
SDK=~/Library/Android/sdk
export JAVA_HOME=$HOME/Library/Java/jdk-17.0.21+9/Contents/Home PATH=$HOME/Library/Java/jdk-17.0.21+9/Contents/Home/bin:$PATH
rm -rf build/stream && mkdir -p build/stream/classes
javac -source 11 -target 11 -nowarn -cp $SDK/platforms/android-36/android.jar -d build/stream/classes \
  android/app/src/main/java/dev/saikiran/phonestereo/StereoStream.java
$SDK/build-tools/36.0.0/d8 --min-api 33 --lib $SDK/platforms/android-36/android.jar --output build/stream \
  $(find build/stream/classes -name '*.class')
(cd build/stream && zip -q stream_zoom.jar classes.dex)
$SDK/platform-tools/adb shell "mkdir -p /data/local/tmp/stereo"
$SDK/platform-tools/adb push build/stream/stream_zoom.jar /data/local/tmp/stereo/stream_zoom.jar >/dev/null
echo "pushed /data/local/tmp/stereo/stream_zoom.jar"
