#!/usr/bin/env python3
"""apply_settings with FULL JSON values (arrays/tables), which `lrc apply` cannot send.

`lrc apply KEY=VALUE` runs every value through cli.autotype(), which only knows
bool/int/float/str -- so an array like the tone curve arrives as the STRING
"[0,0,26,17,...]" and Lightroom silently drops it (the response still echoes the
key, so it looks like it landed). The transport itself is fine: _core.to_lua
serializes lists and dicts correctly. This script skips autotype and hands
_core.call a real Python object.

Usage:
    python3 bridge/apply_json.py '{"ToneCurvePV2012":[0,0,128,140,255,255]}'
    python3 bridge/apply_json.py --targets selected @settings.json

Always read the values back (`lrc settings`) before trusting them.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from lightroom_bridge import _core  # noqa: E402


def main(argv):
    targets = "active"
    history = None
    payload = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--targets":
            i += 1
            targets = argv[i]
        elif a == "--history":
            i += 1
            history = argv[i]
        elif a.startswith("@"):
            with open(a[1:]) as fh:
                payload = json.load(fh)
        else:
            payload = json.loads(a)
        i += 1

    if not isinstance(payload, dict):
        raise SystemExit("expected a JSON object of develop keys")

    params = {"settings": payload, "targets": targets}
    if history:
        params["history"] = history
    resp = _core.call("apply_settings", params)
    print(json.dumps(resp, indent=2))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
