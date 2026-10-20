#!/bin/bash
# Build StereoProbe.java to dex and run it on the USB-connected phone as the shell user.
set -e
cd "$(dirname "$0")"
SDK=~/Library/Android/sdk
export JAVA_HOME=/opt/homebrew/opt/openjdk@17 PATH=/opt/homebrew/opt/openjdk@17/bin:$PATH
rm -rf build && mkdir -p build/classes
javac -source 11 -target 11 -nowarn -cp $SDK/platforms/android-36/android.jar -d build/classes StereoProbe.java
$SDK/build-tools/37.0.0/d8 --min-api 33 --lib $SDK/platforms/android-36/android.jar --output build $(find build/classes -name '*.class')
(cd build && zip -q probe.jar classes.dex)
adb shell "rm -rf /data/local/tmp/stereo; mkdir -p /data/local/tmp/stereo"
adb push build/probe.jar /data/local/tmp/stereo/probe.jar >/dev/null
adb shell "CLASSPATH=/data/local/tmp/stereo/probe.jar app_process / StereoProbe ${1:-4}" 2>&1 | grep -vE '^\s+at |ErrnoException|FileNotFound|^\s+\.\.\.'
mkdir -p frames && adb pull /data/local/tmp/stereo/. frames/ >/dev/null && echo "frames in $(pwd)/frames"
