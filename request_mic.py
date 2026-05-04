#!/usr/bin/env python3
"""Run this in iTerm to grant Python microphone access."""
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
import time

status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
if status == 3:
    print("✅ Microphone access ALREADY GRANTED")
elif status == 0:
    print("⏳ Requesting microphone access — look for the macOS popup...")
    AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVMediaTypeAudio, lambda granted: print("✅ GRANTED!" if granted else "❌ DENIED"))
    time.sleep(15)
elif status == 2:
    print("❌ DENIED — go to System Settings > Privacy > Microphone")
    print("   Click + and add: /usr/local/bin/python3.13")
