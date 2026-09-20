#!/usr/bin/env python3
"""
lightroom_bridge.cli -- the `lrc` command-line client.

Thin argparse front-end over :mod:`lightroom_bridge._core`. Behavior is
identical to the original standalone ``bridge/lrc.py``; the transport now lives
in the shared core so the MCP server can reuse it.

Usage examples
--------------
  lrc ping
  lrc status
  lrc settings                 # full Develop settings of active photo
  lrc set Exposure=0.4 Contrast=12 Vibrance=8
  lrc wb --temp 5200 --tint 8
  lrc crop --left 0.05 --top 0.05 --right 0.95 --bottom 0.95 --angle -1.5
  lrc mask create aiSelection sky local_Exposure=-0.4 local_Temperature=-6
  lrc render --size 2048 --out /tmp/after.jpg
  lrc call <cmd> key=value ... [--json '{...}']   # generic escape hatch
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import _core, preset_xmp
from ._core import DAEMON_LOG, call


# --------------------------------------------------------------------------- #
# Argument helpers
# --------------------------------------------------------------------------- #

def autotype(s):
    """Convert a CLI string to bool/int/float when it looks like one."""
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("nil", "null", "none"):
        return None
    try:
        if s.lstrip("+-").isdigit():
            return int(s)
        return float(s)
    except ValueError:
        return s


def parse_kv(pairs):
    """['Exposure=0.5','Contrast=12'] -> {'Exposure':0.5,'Contrast':12}"""
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit("expected KEY=VALUE, got: %r" % p)
        k, v = p.split("=", 1)
        out[k.strip()] = autotype(v.strip())
    return out


def emit(resp, raw=False):
    """Print a response and return a process exit code."""
    if raw:
        print(json.dumps(resp))
    else:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
    return 0 if resp.get("ok") else 1


_direct_noted = [False]


def _note_direct():
    # A true one-shot "direct" connection cannot work: the plug-in is a
    # single-client server whose *send* socket does not re-arm after a client
    # disconnects, so connecting then disconnecting for one call wedges the
    # bridge for every later client. Everything is routed through the daemon.
    if not _direct_noted[0]:
        _direct_noted[0] = True
        print("note: --direct is not supported by the single-client plug-in "
              "(a one-shot connection would wedge the bridge); using the "
              "background daemon instead.", file=sys.stderr)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        prog="lrc",
        description="Drive Adobe Lightroom Classic through the Claude Bridge plug-in.",
    )
    p.add_argument("--direct", action="store_true",
                   help="(compatibility) routed through the daemon; a true one-shot "
                        "bypass isn't supported by the single-client plug-in")
    p.add_argument("--raw", action="store_true",
                   help="print compact single-line JSON instead of pretty output")
    sub = p.add_subparsers(dest="command", required=True)

    def with_global_flags(sp):
        sp.add_argument("--direct", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
        sp.add_argument("--raw", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
        return sp

    def add(name, **kw):
        return with_global_flags(sub.add_parser(name, **kw))

    add("ping", help="handshake + active photo summary")
    add("status", help="module, catalog and selection summary")
    add("list-selected", help="list all selected photos")
    s = add("settings", help="full Develop settings of the active photo")
    s.add_argument("--uuid", help="target a specific photo without changing the selection")
    s = add("metadata", help="capture metadata: camera, lens, ISO, shutter, aperture, GPS …")
    s.add_argument("--uuid", help="target a specific photo without changing the selection")
    add("help", help="list every command the plug-in understands")

    s = add("set", help="set Develop sliders, e.g. set Exposure=0.4 Contrast=12 "
                        "(a value may be '+', '-' to nudge, or 'reset' for the default)")
    s.add_argument("pairs", nargs="+")

    s = add("value", help="get or set a single parameter "
                          "(set also accepts '+', '-' or 'reset')")
    s.add_argument("param")
    s.add_argument("newvalue", nargs="?")

    s = add("range", help="min/max of a parameter")
    s.add_argument("param")

    s = add("apply", help="apply RAW develop-settings keys (batch capable)")
    s.add_argument("pairs", nargs="+")
    s.add_argument("--targets", choices=["active", "selected"], default="active")
    s.add_argument("--history", help="custom name for the History step")

    s = add("crop", help="set crop box (fractions 0..1) and/or angle (deg); "
                         "if any edge is given, omitted edges reset to the full frame")
    s.add_argument("--left", type=float)
    s.add_argument("--top", type=float)
    s.add_argument("--right", type=float)
    s.add_argument("--bottom", type=float)
    s.add_argument("--angle", type=float)
    s.add_argument("--targets", choices=["active", "selected"], default="active")
    s.add_argument("--history", help="custom name for the History step")
    add("crop-reset", help="clear the crop")

    s = add("rotate", help="rotate the photo")
    s.add_argument("dir", choices=["left", "right"])
    s.add_argument("--uuid", help="rotate a specific photo without changing the selection")

    s = add("wb", help="white balance: --temp/--tint or --as <preset>")
    s.add_argument("--temp", type=float)
    s.add_argument("--tint", type=float)
    s.add_argument("--as", dest="as_preset")
    s.add_argument("--targets", choices=["active", "selected"], default="active")
    add("auto-tone", help="Auto Tone")
    add("auto-wb", help="Auto White Balance")

    s = add("reset", help="reset all adjustments, or one PARAM")
    s.add_argument("param", nargs="?")

    s = add("tool", help="select a Develop tool")
    s.add_argument("tool", help="loupe|crop|dust|redeye|masking|upright")

    # Masking
    s = add("mask", help="masking operations")
    msub = s.add_subparsers(dest="mask_cmd", required=True)
    for verb in ("create", "add", "subtract", "intersect"):
        m = with_global_flags(msub.add_parser(verb))
        m.add_argument("type", help="brush|gradient|radialGradient|rangeMask|aiSelection")
        m.add_argument("rest", nargs="*",
                       help="[subtype] local_* adjustments, e.g. sky local_Exposure=-0.4")
    m = with_global_flags(msub.add_parser("adjust")); m.add_argument("pairs", nargs="+")
    with_global_flags(msub.add_parser("list"))
    with_global_flags(msub.add_parser("reset"))
    m = with_global_flags(msub.add_parser("invert")); m.add_argument("--id")
    m = with_global_flags(msub.add_parser("delete")); m.add_argument("id")
    m = with_global_flags(msub.add_parser("select")); m.add_argument("id")

    # Presets
    s = add("preset", help="develop presets")
    psub = s.add_subparsers(dest="preset_cmd", required=True)
    with_global_flags(psub.add_parser("list"))
    pa = with_global_flags(psub.add_parser("apply"))
    pa.add_argument("name", nargs="?", help="preset name (or use --uuid for an exact match)")
    pa.add_argument("--uuid", help="preset uuid from `preset list` (unambiguous)")
    pa.add_argument("--targets", choices=["active", "selected"], default="active")
    ps = with_global_flags(psub.add_parser(
        "save", help="save the current edit as a UI-visible develop preset "
                     "(XMP in CameraRaw/Settings; loads at next Lightroom restart)"))
    ps.add_argument("name", help="preset name shown in the Develop Presets panel")
    ps.add_argument("--uuid", help="capture settings from this photo instead of the active one")
    ps.add_argument("--groups", help="comma list replacing the default groups "
                                     "(default: %s)" % ",".join(preset_xmp.DEFAULT_GROUPS))
    ps.add_argument("--with", dest="with_groups",
                    help="comma list of extra groups (e.g. wb,exposure)")
    ps.add_argument("--without", dest="without_groups",
                    help="comma list of groups to drop (e.g. detail,effects)")
    ps.add_argument("--group", default=preset_xmp.DEFAULT_GROUP,
                    help="Develop-panel group name (default: %(default)s)")
    ps.add_argument("--dry-run", action="store_true",
                    help="show the exact keys/values that would be stored; write nothing")
    ps.add_argument("--force", action="store_true",
                    help="overwrite an existing preset file of the same name")

    # Selection / navigation
    s = add("select", help="change the active photo")
    s.add_argument("nav", nargs="?", choices=["next", "prev", "previous", "first", "last"])
    s.add_argument("--uuid")
    s.add_argument("--path")

    uuid_note = ("with --uuid, writes directly to that photo (selection-safe, "
                 "returns read-back verification); without it, acts on the UI "
                 "selection (ALL selected photos in grid view)")
    s = add("rating", help="set star rating 0-5; " + uuid_note)
    s.add_argument("value", type=int)
    s.add_argument("--uuid")
    s = add("flag", help="set flag; " + uuid_note)
    s.add_argument("value", choices=["pick", "reject", "none"])
    s.add_argument("--uuid")
    s = add("label", help="set color label; " + uuid_note)
    s.add_argument("value")
    s.add_argument("--uuid")
    s = add("module", help="switch module"); s.add_argument("name")
    add("undo"); add("redo")
    s = add("snapshot", help="create a develop snapshot")
    s.add_argument("name", nargs="?")
    s.add_argument("--uuid", help="snapshot a specific photo without changing the selection")
    s = add("snapshots", help="list develop snapshots")
    s.add_argument("--uuid")
    s = add("snapshot-apply", help="restore a develop snapshot by name or id "
                                   "(the safe rollback; beats chained undo)")
    s.add_argument("name_or_id")
    s.add_argument("--uuid")
    s = add("snapshot-delete", help="delete a develop snapshot by id (see snapshots)")
    s.add_argument("id")
    s.add_argument("--uuid")

    # Previews
    s = add("thumb", help="fast JPEG preview of the active photo")
    s.add_argument("--size", type=int, default=1600)
    s.add_argument("--out")
    s.add_argument("--uuid")
    s.add_argument("--tag")

    s = add("render", help="full-fidelity JPEG export of the active photo")
    s.add_argument("--size", type=int, default=2048,
                   help="long-edge pixels; 0 = native resolution")
    s.add_argument("--quality", type=float, default=0.85)
    s.add_argument("--uuid")
    s.add_argument("--out", help="also copy the rendered JPEG to this path")
    s.add_argument("--timeout", type=float,
                   help="seconds before a stalled export fails (default 90)")

    # Generic escape hatch + daemon control
    s = add("call", help="send any command: call <cmd> key=value ... [--json '{...}']")
    s.add_argument("cmd")
    s.add_argument("pairs", nargs="*")
    s.add_argument("--json", dest="json_blob")

    s = add("daemon", help="manage the background daemon")
    s.add_argument("action", choices=["status", "stop", "start"])

    s = add("install-plugin", help="copy ClaudeBridge.lrplugin into Lightroom's Modules folder")
    s.add_argument("--dest", help="target directory (default: ~/Library/Application "
                                  "Support/Adobe/Lightroom/Modules)")

    return p


def dispatch_cli(args):
    if getattr(args, "direct", False):
        _note_direct()
    cmd = args.command

    if cmd == "ping":
        return call("ping")
    if cmd == "status":
        return call("status")
    if cmd == "list-selected":
        return call("list_selected")
    if cmd == "settings":
        return call("get_settings", {"uuid": args.uuid} if args.uuid else {})
    if cmd == "metadata":
        return call("get_metadata", {"uuid": args.uuid} if args.uuid else {})
    if cmd == "help":
        return call("help")

    if cmd == "set":
        return call("set", {"values": parse_kv(args.pairs)})

    if cmd == "value":
        if args.newvalue is None:
            return call("get_value", {"param": args.param})
        return call("set_value", {"param": args.param, "value": autotype(args.newvalue)})

    if cmd == "range":
        return call("get_range", {"param": args.param})

    if cmd == "apply":
        params = {"settings": parse_kv(args.pairs), "targets": args.targets}
        if args.history:
            params["history"] = args.history
        return call("apply_settings", params)

    if cmd == "crop":
        params = {"targets": args.targets}
        if args.history:
            params["history"] = args.history
        for k in ("left", "top", "right", "bottom", "angle"):
            v = getattr(args, k)
            if v is not None:
                params[k] = v
        return call("crop", params)
    if cmd == "crop-reset":
        return call("crop_reset")

    if cmd == "rotate":
        params = {"dir": args.dir}
        if args.uuid:
            params["uuid"] = args.uuid
        return call("rotate", params)

    if cmd == "wb":
        params = {"targets": args.targets}
        if args.as_preset:
            params["as"] = args.as_preset
        if args.temp is not None:
            params["temp"] = args.temp
        if args.tint is not None:
            params["tint"] = args.tint
        return call("white_balance", params)
    if cmd == "auto-tone":
        return call("auto_tone")
    if cmd == "auto-wb":
        return call("auto_wb")

    if cmd == "reset":
        if args.param:
            return call("reset_param", {"param": args.param})
        return call("reset_all")

    if cmd == "tool":
        return call("select_tool", {"tool": args.tool})

    if cmd == "mask":
        mc = args.mask_cmd
        if mc in ("create", "add", "subtract", "intersect"):
            params = {"type": args.type}
            rest = list(args.rest)
            if rest and "=" not in rest[0]:
                params["subtype"] = rest.pop(0)
            if rest:
                params["values"] = parse_kv(rest)
            return call("mask_" + mc, params)
        if mc == "adjust":
            return call("mask_adjust", {"values": parse_kv(args.pairs)})
        if mc == "list":
            return call("mask_list")
        if mc == "reset":
            return call("mask_reset")
        if mc == "invert":
            return call("mask_invert", {"id": args.id} if args.id else {})
        if mc == "delete":
            return call("mask_delete", {"id": args.id})
        if mc == "select":
            return call("mask_select", {"id": args.id})

    if cmd == "preset":
        if args.preset_cmd == "list":
            return call("preset_list")
        if args.preset_cmd == "save":
            params = {}
            if args.uuid:
                params["uuid"] = args.uuid
            resp = call("get_settings", params)
            if not resp.get("ok"):
                return resp
            split = lambda s: [g.strip() for g in s.split(",") if g.strip()]  # noqa: E731
            return preset_xmp.save_preset(
                args.name, resp["result"],
                groups=split(args.groups) if args.groups else None,
                with_groups=split(args.with_groups) if args.with_groups else None,
                without_groups=split(args.without_groups) if args.without_groups else None,
                group=args.group, dry_run=args.dry_run, force=args.force)
        if not args.name and not args.uuid:
            raise SystemExit("preset apply: give a name or --uuid")
        params = {"targets": args.targets}
        if args.name:
            params["name"] = args.name
        if args.uuid:
            params["uuid"] = args.uuid
        return call("preset_apply", params)

    if cmd == "select":
        params = {}
        if args.nav:
            params["nav"] = args.nav
        if args.uuid:
            params["uuid"] = args.uuid
        if args.path:
            params["path"] = args.path
        if not params:
            raise SystemExit("select: give next/prev/first/last or --uuid/--path")
        return call("select_photo", params)

    if cmd == "rating":
        if args.uuid:
            return call("set_metadata", {"uuid": args.uuid, "rating": args.value})
        return call("set_rating", {"value": args.value})
    if cmd == "flag":
        if args.uuid:
            return call("set_metadata", {"uuid": args.uuid, "flag": args.value})
        return call("set_flag", {"value": args.value})
    if cmd == "label":
        if args.uuid:
            return call("set_metadata", {"uuid": args.uuid, "label": args.value})
        return call("set_label", {"value": args.value})
    if cmd == "module":
        return call("switch_module", {"module": args.name})
    if cmd == "undo":
        return call("undo")
    if cmd == "redo":
        return call("redo")
    if cmd == "snapshot":
        params = {}
        if args.name:
            params["name"] = args.name
        if args.uuid:
            params["uuid"] = args.uuid
        return call("snapshot_create", params)
    if cmd == "snapshots":
        return call("snapshot_list", {"uuid": args.uuid} if args.uuid else {})
    if cmd == "snapshot-apply":
        params = {"id": args.name_or_id}  # the plug-in resolves id-or-name
        if args.uuid:
            params["uuid"] = args.uuid
        return call("snapshot_apply", params)
    if cmd == "snapshot-delete":
        params = {"id": args.id}
        if args.uuid:
            params["uuid"] = args.uuid
        return call("snapshot_delete", params)

    if cmd == "thumb":
        params = {"size": args.size}
        if args.out:
            params["path"] = os.path.abspath(args.out)
        if args.uuid:
            params["uuid"] = args.uuid
        if args.tag:
            params["tag"] = args.tag
        resp = call("thumb", params)
        if resp.get("ok"):
            return resp
        # Lightroom's preview pipeline often refuses requestJpegThumbnail for the
        # active photo in the Develop module ("error loading thumb"). Fall back to
        # a full render, which always reflects the current edits.
        rparams = {"size": args.size, "quality": 0.85}
        if args.uuid:
            rparams["uuid"] = args.uuid
        r = call("render", rparams)
        if r.get("ok"):
            if args.out and r.get("result", {}).get("path"):
                dst = os.path.abspath(args.out)
                try:
                    shutil.copyfile(r["result"]["path"], dst)
                    r["result"]["path"] = dst
                except OSError as e:
                    r["result"]["copyError"] = str(e)
            r["result"]["note"] = "thumb unavailable; used render fallback"
        return r

    if cmd == "render":
        params = {"size": args.size, "quality": args.quality}
        if args.uuid:
            params["uuid"] = args.uuid
        if args.timeout is not None:
            params["timeout"] = args.timeout
        r = call("render", params)
        if r.get("ok") and args.out and r.get("result", {}).get("path"):
            dst = os.path.abspath(args.out)
            try:
                shutil.copyfile(r["result"]["path"], dst)
                r["result"]["path"] = dst
            except OSError as e:
                r["result"]["copyError"] = str(e)
        return r

    if cmd == "call":
        params = parse_kv(args.pairs) if args.pairs else {}
        if args.json_blob:
            params.update(json.loads(args.json_blob))
        return call(args.cmd, params)

    if cmd == "daemon":
        return _core.daemon_control(args.action)

    if cmd == "install-plugin":
        return install_plugin(args.dest)

    return {"ok": False, "error": "unhandled command: %s" % cmd}


def _plugin_source():
    """Locate ClaudeBridge.lrplugin: bundled package data (wheel installs)
    first, then the source-checkout layout."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, "data", "ClaudeBridge.lrplugin"),
        os.path.abspath(os.path.join(here, os.pardir, os.pardir, "ClaudeBridge.lrplugin")),
    ):
        if os.path.isfile(os.path.join(cand, "Info.lua")):
            return cand
    return None


def install_plugin(dest=None):
    """Copy the Lightroom plug-in into Lightroom's Modules folder so a
    pip/uvx install has a working plug-in without the source checkout."""
    src = _plugin_source()
    if not src:
        return {"ok": False, "error": "ClaudeBridge.lrplugin not found (neither "
                "bundled package data nor a source checkout)"}
    base = dest or os.path.expanduser(
        "~/Library/Application Support/Adobe/Lightroom/Modules")
    target = os.path.join(base, "ClaudeBridge.lrplugin")
    try:
        os.makedirs(base, exist_ok=True)
        if os.path.isdir(target):
            # Only replace something that actually is our plug-in.
            marker = os.path.join(target, "Info.lua")
            if not os.path.isfile(marker):
                return {"ok": False, "error": "%s exists but does not look like "
                        "ClaudeBridge.lrplugin; remove it manually" % target}
            shutil.rmtree(target)
        shutil.copytree(src, target)
    except OSError as e:
        return {"ok": False, "error": "install failed: %s" % e}
    return {"ok": True, "result": {
        "installed": target,
        "from": src,
        "next": "In Lightroom Classic: File > Plug-in Manager -- the plug-in "
                "appears automatically from the Modules folder (restart "
                "Lightroom if it doesn't). Then select a photo and run "
                "`lrc ping`.",
    }}


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        resp = dispatch_cli(args)
    except (ConnectionError, OSError) as e:
        resp = {"ok": False, "error": "transport error: %s" % e}
    return emit(resp, raw=getattr(args, "raw", False))


if __name__ == "__main__":
    sys.exit(main())
