#!/usr/bin/env python3
"""Extract the Android Auto phone identity from your own copy of the Android Auto app, on a computer.

Most users do this on the comma instead: The Galaxy → Vehicle Controls → Android Auto Identity.
This is the same extraction (starpilot/system/android_auto/apk_identity.py) for
testing with the Desktop Head Unit or installing by hand. Needs only Python with
``cryptography``; accepts an APK, XAPK or APKM.

  python tools/android_auto/import_identity.py --apk ~/Downloads/android-auto.xapk

The output is Google's key: keep it private and never commit it.
"""

import argparse
import json
from pathlib import Path

from openpilot.starpilot.system.android_auto import apk_identity


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--apk", type=Path, required=True, help="Android Auto APK, XAPK or APKM")
  parser.add_argument("--output", type=Path, default=Path(".cache/android_auto/identity"),
                      help="identity directory to write (a previous one is kept as <output>.previous)")
  args = parser.parse_args()
  try:
    files, metadata = apk_identity.extract_identity(args.apk.expanduser(), progress=lambda stage: print(f"{stage}…", flush=True))
    apk_identity.install_identity(files, metadata, args.output)
  except apk_identity.IdentityImportError as error:
    parser.exit(1, f"error: {error}\n")
  print(json.dumps({**metadata, "directory": str(args.output)}, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
