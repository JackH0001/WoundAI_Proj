#!/usr/bin/env python3
"""Export user-approved artwork (2026-10-01) to iOS catalogs, without redrawing it.

SSOT: iOS/IconSources/{medical,lite}.png. Uses macOS sips only for PNG
format/size conversion; keep composition and the approved pale mint background.
The generated imagegen previews were approved in the WoundAI release conversation.
"""
import json
from pathlib import Path
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    for name, target in (("medical", "WoundMeasurementApp"), ("lite", "WoundLite")):
        source = root / "iOS" / "IconSources" / (name + ".png")
        properties = subprocess.check_output(
            ["sips", "-g", "hasAlpha", "-g", "pixelWidth", "-g", "pixelHeight", str(source)], text=True)
        if "hasAlpha: no" not in properties:
            raise SystemExit("Icon source must be opaque: " + str(source))
        dest = root / "iOS" / target / "Assets.xcassets" / "AppIcon.appiconset"
        subprocess.run(["sips", "-s", "format", "png", "-z", "1024", "1024", str(source),
                        "--out", str(dest / "icon-1024.png")], check=True)
        (dest / "Contents.json").write_text(json.dumps({
            "images": [{"filename": "icon-1024.png", "idiom": "universal",
                        "platform": "ios", "size": "1024x1024"}],
            "info": {"author": "xcode", "version": 1}}, indent=2) + "\n")


if __name__ == "__main__":
    main()
