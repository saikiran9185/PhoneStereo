#!/usr/bin/env python3
"""ARCHIVED 4 Nov 2026 (git tag surfacetouch-v1-archive): SurfaceTouch V1 launcher - projector wall touch from the phone's dual cameras. See surfacetouch/app.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from surfacetouch.app import main  # noqa: E402

if __name__ == "__main__":
    main()
