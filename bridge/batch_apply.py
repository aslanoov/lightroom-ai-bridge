#!/usr/bin/env python3
"""batch_apply.py -- apply a develop recipe to a LIST of photos, verified per photo.

Why this exists: `lrc apply` targets only `active` or `selected`, and a
selected-batch apply is known to silently drop keys on SOME photos (kb:
learned/2026-08-06-selected-batch-apply-can-drop-a-key-on-s.md, and the
SplitToning/Incremental first-call drop). This walks an explicit uuid list,
applies to each photo as the ACTIVE one, reads the settings back, and re-issues
only the keys that did not land -- so "applied" is never taken on faith.

    python3 bridge/batch_apply.py --uuids u1,u2,u3 \
        --keys 'Exposure2012=0.3,Contrast2012=12,WhiteBalance=Custom'
    python3 bridge/batch_apply.py --uuid-file list.txt --keys-file recipe.json

Values parse as JSON when possible (numbers, lists for ToneCurve*), else string.
NOTE: selecting a photo collapses Lightroom's selection to that one photo --
tell the user their multi-selection is gone when the batch finishes.
"""
import argparse, json, os, subprocess, sys, time

LRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lrc")
LRC = os.path.normpath(LRC)
TOL = 0.0005


def lrc(*args, timeout=120):
    r = subprocess.run([LRC] + list(args) + ["--raw"], capture_output=True,
                       text=True, timeout=timeout)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"ok": False, "error": (r.stdout or r.stderr or "")[-300:]}


def parse_val(s):
    try:
        return json.loads(s)
    except Exception:
        return s


def close(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= TOL
    return a == b


def apply_one(uuid, keys, retries=3, settle=0.8):
    """Select, apply, read back, re-issue the keys that did not land."""
    sel = lrc("select", "--uuid", uuid)
    if not sel.get("ok"):
        return {"uuid": uuid, "ok": False, "error": "select failed: %s" % sel}
    active = sel["result"]["active"]["uuid"]
    if active != uuid:
        return {"uuid": uuid, "ok": False, "error": "active is %s" % active}

    pending = dict(keys)
    missed = {}
    for attempt in range(retries):
        pairs = ["%s=%s" % (k, json.dumps(v) if not isinstance(v, str) else v)
                 for k, v in pending.items()]
        res = lrc("apply", *pairs, "--targets", "active")
        if not res.get("ok"):
            return {"uuid": uuid, "ok": False, "error": res.get("error")}
        time.sleep(settle)
        cur = lrc("settings", "--uuid", uuid).get("result", {})
        missed = {k: v for k, v in pending.items() if not close(cur.get(k), v)}
        if not missed:
            return {"uuid": uuid, "ok": True, "attempts": attempt + 1}
        pending = missed
        settle += 0.7
    return {"uuid": uuid, "ok": False, "missed": missed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuids", help="comma-separated photo uuids")
    ap.add_argument("--uuid-file", help="file with one uuid per line")
    ap.add_argument("--keys", help="comma-separated RawKey=value pairs")
    ap.add_argument("--keys-file", help="JSON file {RawKey: value}")
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    uuids = []
    if args.uuids:
        uuids += [u.strip() for u in args.uuids.split(",") if u.strip()]
    if args.uuid_file:
        uuids += [l.strip() for l in open(args.uuid_file) if l.strip()]
    keys = {}
    if args.keys_file:
        keys.update(json.load(open(args.keys_file)))
    if args.keys:
        for pair in args.keys.split(","):
            k, _, v = pair.partition("=")
            keys[k.strip()] = parse_val(v.strip())
    if not uuids or not keys:
        ap.error("need uuids and keys")

    bad = []
    for n, u in enumerate(uuids, 1):
        r = apply_one(u, keys, retries=args.retries)
        print("%3d/%d %s %s" % (n, len(uuids), u[:8],
                                "ok(%d)" % r.get("attempts", 0) if r["ok"]
                                else "FAIL %s" % (r.get("missed") or r.get("error"))),
              flush=True)
        if not r["ok"]:
            bad.append(r)
    print("\n%d/%d verified" % (len(uuids) - len(bad), len(uuids)))
    if bad:
        print(json.dumps(bad, indent=1))
        sys.exit(1)


if __name__ == "__main__":
    main()
