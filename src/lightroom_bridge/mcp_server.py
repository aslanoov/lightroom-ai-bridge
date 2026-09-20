#!/usr/bin/env python3
"""
lightroom_bridge.mcp_server -- an MCP (stdio) server for Adobe Lightroom Classic.

Exposes the Claude Bridge plug-in's Develop/catalog/preview operations as MCP
tools. Every tool routes through the same background daemon the ``lrc`` CLI uses
(:mod:`lightroom_bridge._core`), so there is exactly one shared connection to
Lightroom (the plug-in is single-client).

Run it with ``uvx lightroom-bridge`` (or the ``lightroom-bridge`` console
script). Requires Adobe Lightroom Classic 15.3+ running on macOS with the
"Claude Bridge" plug-in installed and started, and a photo selected.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
import os

from fastmcp import FastMCP

from . import _core, preset_xmp
from . import kb as _kb

# Image content helper (path moved across FastMCP versions; degrade gracefully).
try:
    from fastmcp.utilities.types import Image  # type: ignore
except Exception:  # pragma: no cover
    try:
        from fastmcp import Image  # type: ignore
    except Exception:  # pragma: no cover
        Image = None  # type: ignore

# Inline preview is capped so a base64 JPEG stays well under tool-result limits
# (~150k chars on Claude Desktop, 25k tokens on Claude Code). Full resolution
# goes to a file via `render_to_file` instead.
MAX_INLINE_PX = 1536

INSTRUCTIONS = """\
Drive Adobe Lightroom Classic's Develop module and Library, guided by a
self-improving photography knowledge base.

Workflow (golden rule): look -> edit -> look. Call `render_preview` to SEE the
active photo, make edits (`set_develop`, `set_white_balance`, `crop`, `mask`, ...),
then `render_preview` again to verify. Never edit blind.

Knowledge base -- RECALL -> APPLY -> LEARN (not optional; it is what keeps
ratings and edits consistent across sessions and models):
- RECALL: before culling or editing, render the photo, classify it (genre +
  light), then call `kb_recall(genres=..., task="cull"|"edit")` and follow the
  cards it returns. Rate ONLY with the returned culling rubric -- never ad-hoc
  criteria -- and cite its dimensions (L/C/M/T). The user-taste card overrides
  genre defaults on conflict.
- APPLY: 4-5 star ratings may only be awarded from a FULL-RESOLUTION render, and
  dark/night/blue-hour frames must never be dismissed from a thumbnail -- they
  are the top cause of missed keepers.
- LEARN: before ending a session, if the user corrected you, a card was wrong, or
  a new recipe worked, call `kb_learn(...)`. If a card's guidance worked, call
  `kb_bump(card_id)`. Check `kb_stats()` for maintenance flags.

Notes:
- Edits act on the active (most-selected) photo and switch Lightroom to Develop.
  Batch ops take targets="selected". Everything is non-destructive (Lightroom
  History) and reversible with `undo`.
- AI masks under-apply on creation: `mask` create, then `mask` adjust to re-push
  the values, then verify with `render_preview`. Mask read-back is unreliable;
  trust the render, not `list_masks`.
- Before a multi-step edit, `create_snapshot` a named restore point; if the edit
  goes wrong, `apply_snapshot` it back. That beats chained `undo`, which can
  over-rewind past the whole edit.
- For rating/flag/label writeback at scale, prefer `set_metadata` with a uuid:
  it writes directly to that photo and returns the read-back verification.
  `set_rating`/`set_flag`/`set_label` act on the CURRENT SELECTION instead.
- Start a session with `ping` to confirm Lightroom is connected.
"""

mcp = FastMCP(name="lightroom-bridge", instructions=INSTRUCTIONS)

# Annotation shorthands.
_READ = {"readOnlyHint": True, "openWorldHint": False}
_EDIT = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
_DESTROY = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}


# --------------------------------------------------------------------------- #
# Inspect (read-only)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Ping Lightroom", **_READ})
def ping() -> dict:
    """Handshake: confirm Lightroom + the plug-in are reachable and report the
    active photo. Call this first each session."""
    return _core.call("ping")


@mcp.tool(annotations={"title": "Status", **_READ})
def status() -> dict:
    """Current module, catalog, and selection summary."""
    return _core.call("status")


@mcp.tool(annotations={"title": "List selected photos", **_READ})
def list_selected() -> dict:
    """List every currently selected photo (uuid + filename)."""
    return _core.call("list_selected")


@mcp.tool(annotations={"title": "Get Develop settings", **_READ})
def get_develop_settings(uuid: Optional[str] = None) -> dict:
    """Full Develop settings table (raw keys + values) of the active photo, or
    of a specific photo via `uuid` without changing the selection."""
    return _core.call("get_settings", {"uuid": uuid} if uuid else {})


@mcp.tool(annotations={"title": "Get capture metadata", **_READ})
def get_metadata(uuid: Optional[str] = None) -> dict:
    """Capture/EXIF metadata (camera, lens, ISO, shutter, aperture, focal length,
    GPS, ...). Pass `uuid` to inspect a specific photo without changing selection."""
    return _core.call("get_metadata", {"uuid": uuid} if uuid else {})


@mcp.tool(annotations={"title": "Get parameter value", **_READ})
def get_value(param: str) -> dict:
    """Read one Develop parameter by friendly name, e.g. "Exposure"."""
    return _core.call("get_value", {"param": param})


@mcp.tool(annotations={"title": "Get parameter range", **_READ})
def get_range(param: str) -> dict:
    """Min/max allowed values for a Develop parameter, e.g. "Exposure" -> -5..5."""
    return _core.call("get_range", {"param": param})


@mcp.tool(annotations={"title": "List develop presets", **_READ})
def list_presets() -> dict:
    """List available Develop presets by name."""
    return _core.call("preset_list")


@mcp.tool(annotations={"title": "List masks", **_READ})
def list_masks() -> dict:
    """List masks on the active photo. NOTE: Lightroom's read-back is unreliable
    and often returns []; verify masks with `render_preview` instead."""
    return _core.call("mask_list")


# --------------------------------------------------------------------------- #
# Preview (read-only)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Render preview (inline image)", **_READ})
def render_preview(size: int = 1024, uuid: Optional[str] = None) -> Any:
    """Render the active (or `uuid`) photo through the full Develop pipeline and
    return it as an inline JPEG you can see. Reflects every edit (crop, masks).
    Use this for the look -> edit -> look loop. `size` is the long edge in pixels
    (capped at %d for inline; use `render_to_file` for full resolution).""" % MAX_INLINE_PX
    px = max(64, min(int(size), MAX_INLINE_PX))
    params = {"size": px, "quality": 0.82}
    if uuid:
        params["uuid"] = uuid
    r = _core.call("render", params)
    if not r.get("ok"):
        return r
    path = (r.get("result") or {}).get("path")
    if Image is not None and path and os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                return Image(data=f.read(), format="jpeg")
        except OSError:
            pass
    return r  # fall back to the path/metadata if the image can't be embedded


@mcp.tool(annotations={"title": "Render to file (high res)",
                       "readOnlyHint": False, "destructiveHint": True,
                       "openWorldHint": False})
def render_to_file(
    out: str,
    size: int = 2048,
    quality: float = 0.85,
    uuid: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    """Export a JPEG (long edge capped at `size` px; pass size=0 for native
    resolution) to the absolute path `out` - overwriting any existing file
    there - and return that path. For in-chat viewing use `render_preview`.
    `timeout` (seconds, default 90) bounds a stalled export, e.g. while an AI
    mask is still computing; raise it for very large native-res exports."""
    params: dict = {"size": int(size), "quality": float(quality)}
    if uuid:
        params["uuid"] = uuid
    if timeout is not None:
        params["timeout"] = float(timeout)
    r = _core.call("render", params)
    if r.get("ok") and (r.get("result") or {}).get("path"):
        import shutil
        dst = os.path.abspath(os.path.expanduser(out))
        try:
            shutil.copyfile(r["result"]["path"], dst)
            r["result"]["path"] = dst
        except OSError as e:
            r["result"]["copyError"] = str(e)
    return r


# --------------------------------------------------------------------------- #
# Tone / colour / geometry (edits)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Set Develop sliders", **_EDIT})
def set_develop(settings: dict) -> dict:
    """Set Develop sliders by friendly name, e.g.
    {"Exposure": 0.35, "Contrast": 12, "Shadows": 20, "Vibrance": 8}. Process-
    version mapping is automatic. HSL uses keys like "SaturationAdjustmentBlue".
    A value may also be the string "+" or "-" (nudge by one increment) or
    "reset" (restore that parameter's default)."""
    return _core.call("set", {"values": dict(settings)})


@mcp.tool(annotations={"title": "Set one parameter", **_EDIT})
def set_value(param: str, value: Any) -> dict:
    """Set a single Develop parameter, e.g. param="Exposure", value=0.4.
    `value` may also be "+" / "-" (nudge one increment) or "reset" (default)."""
    return _core.call("set_value", {"param": param, "value": value})


@mcp.tool(annotations={"title": "White balance", **_EDIT})
def set_white_balance(
    temp: Optional[float] = None,
    tint: Optional[float] = None,
    preset: Optional[str] = None,
    targets: Literal["active", "selected"] = "active",
) -> dict:
    """Set white balance by value (`temp` in Kelvin, `tint`) or by `preset`
    (Auto, Daylight, Cloudy, Shade, Tungsten, Fluorescent, Flash).
    `targets`: "active" or "selected". Batch temp/tint (Kelvin) applies to
    RAW/DNG photos; balance JPEG/TIFF frames individually instead."""
    params: dict = {"targets": targets}
    if preset:
        params["as"] = preset
    if temp is not None:
        params["temp"] = temp
    if tint is not None:
        params["tint"] = tint
    return _core.call("white_balance", params)


@mcp.tool(annotations={"title": "Auto Tone", **_EDIT})
def auto_tone() -> dict:
    """Apply Lightroom's Auto Tone to the active photo."""
    return _core.call("auto_tone")


@mcp.tool(annotations={"title": "Auto White Balance", **_EDIT})
def auto_white_balance() -> dict:
    """Apply Lightroom's Auto White Balance to the active photo."""
    return _core.call("auto_wb")


@mcp.tool(annotations={"title": "Crop / straighten", **_EDIT})
def crop(
    left: Optional[float] = None,
    top: Optional[float] = None,
    right: Optional[float] = None,
    bottom: Optional[float] = None,
    angle: Optional[float] = None,
    targets: Literal["active", "selected"] = "active",
    history: Optional[str] = None,
) -> dict:
    """Set the crop box as frame fractions 0..1 (`left`/`top`/`right`/`bottom`)
    and/or straighten by `angle` degrees. Give ALL FOUR edges together: if any
    edge is set, omitted edges reset to the full frame (0/0/1/1). Pass only
    `angle` to straighten without touching the crop box. `history` optionally
    names the History step."""
    params: dict = {"targets": targets}
    if history:
        params["history"] = history
    for k, v in (("left", left), ("top", top), ("right", right),
                 ("bottom", bottom), ("angle", angle)):
        if v is not None:
            params[k] = v
    return _core.call("crop", params)


@mcp.tool(annotations={"title": "Clear crop", **_DESTROY})
def crop_reset() -> dict:
    """Remove the crop from the active photo."""
    return _core.call("crop_reset")


@mcp.tool(annotations={"title": "Rotate 90 degrees", **_EDIT})
def rotate(direction: Literal["left", "right"], uuid: Optional[str] = None) -> dict:
    """Rotate the active photo (or the photo with `uuid`) 90 degrees."""
    params: dict = {"dir": direction}
    if uuid:
        params["uuid"] = uuid
    return _core.call("rotate", params)


@mcp.tool(annotations={"title": "Reset adjustments", **_DESTROY})
def reset_develop(param: Optional[str] = None) -> dict:
    """Reset all Develop adjustments, or just one `param` (e.g. "Texture")."""
    if param:
        return _core.call("reset_param", {"param": param})
    return _core.call("reset_all")


@mcp.tool(annotations={"title": "Select Develop tool", **_EDIT})
def select_tool(tool: str) -> dict:
    """Select a Develop tool: loupe, crop, dust, redeye, masking, or upright."""
    return _core.call("select_tool", {"tool": tool})


# --------------------------------------------------------------------------- #
# Masking (one consolidated tool)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Masking", "readOnlyHint": False,
                       "destructiveHint": True, "openWorldHint": False})
def mask(
    operation: str,
    type: Optional[str] = None,
    subtype: Optional[str] = None,
    adjustments: Optional[dict] = None,
    mask_id: Optional[str] = None,
) -> dict:
    """Masking operations.

    operation: create | add | subtract | intersect | adjust | list | reset |
               invert | delete | select
    type (for create/add/subtract/intersect): brush | gradient | radialGradient |
               rangeMask | aiSelection
    subtype (rangeMask/aiSelection only): subject | sky | background | objects |
               people | landscape | color | luminance | depth
    adjustments: local_* params, e.g. {"local_Exposure": -0.5, "local_Clarity": 10}
    (applied to the current mask; also accepted by add/subtract/intersect)

    "reset" DELETES ALL masks on the photo; "delete" removes one by `mask_id`.
    IMPORTANT: AI masks under-apply on creation. Reliable pattern is
    operation="create" then operation="adjust" to re-push `adjustments`, then
    verify with `render_preview`. You CANNOT position geometric masks at chosen
    coordinates (Lightroom limitation); they land at the default spot.
    """
    op = operation
    if op in ("create", "add", "subtract", "intersect"):
        if not type:
            return {"ok": False, "error": "operation %r requires `type`" % op}
        params: dict = {"type": type}
        if subtype:
            params["subtype"] = subtype
        if adjustments:
            params["values"] = dict(adjustments)
        return _core.call("mask_" + op, params)
    if op == "adjust":
        if not adjustments:
            return {"ok": False, "error": "operation 'adjust' requires `adjustments`"}
        return _core.call("mask_adjust", {"values": dict(adjustments)})
    if op == "list":
        return _core.call("mask_list")
    if op == "reset":
        return _core.call("mask_reset")
    if op == "invert":
        return _core.call("mask_invert", {"id": mask_id} if mask_id else {})
    if op in ("delete", "select"):
        if not mask_id:
            return {"ok": False, "error": "operation %r requires `mask_id`" % op}
        return _core.call("mask_" + op, {"id": mask_id})
    return {"ok": False, "error": "unknown mask operation: %r" % op}


# --------------------------------------------------------------------------- #
# Presets / batch (edits)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Apply preset", **_EDIT})
def apply_preset(
    name: Optional[str] = None,
    uuid: Optional[str] = None,
    targets: Literal["active", "selected"] = "active",
) -> dict:
    """Apply a Develop preset by `name` (case-insensitive, first match across
    folders) or exactly by preset `uuid` (from `list_presets` - unambiguous
    when names collide). `targets`: "active" or "selected"."""
    if not name and not uuid:
        return {"ok": False, "error": "give a preset name or uuid (see list_presets)"}
    params: dict = {"targets": targets}
    if name:
        params["name"] = name
    if uuid:
        params["uuid"] = uuid
    return _core.call("preset_apply", params)


@mcp.tool(annotations={"title": "Save preset", "readOnlyHint": False,
                       "destructiveHint": True, "openWorldHint": False})
def save_preset(
    name: str,
    photo_uuid: Optional[str] = None,
    groups: Optional[str] = None,
    with_groups: Optional[str] = None,
    without_groups: Optional[str] = None,
    group: str = preset_xmp.DEFAULT_GROUP,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Save the current edit as a UI-visible Develop preset (XMP written to
    CameraRaw/Settings; appears in the Develop Presets panel, under `group`,
    after the next Lightroom Classic restart). Captures the active photo's
    settings (or `photo_uuid`'s), filtered to transferable "look" groups -
    by default tone, curve, presence, color, grade, effects, detail,
    calibration (plus bw automatically for grayscale edits); wb and exposure
    stay out (photo-specific) unless added via `with_groups`. Crop, masks,
    retouch, Point Color, and the profile/Look are never included (a warning
    is returned when the source edit uses them). Comma lists for
    `groups`/`with_groups`/`without_groups`. Use `dry_run` to preview the
    stored keys, `force` to overwrite the same-named preset (a filename
    collision with a *different* preset is always refused)."""
    params: dict = {}
    if photo_uuid:
        params["uuid"] = photo_uuid
    resp = _core.call("get_settings", params)
    if not resp.get("ok"):
        return resp
    split = lambda s: [g.strip() for g in s.split(",") if g.strip()]  # noqa: E731
    return preset_xmp.save_preset(
        name, resp["result"],
        groups=split(groups) if groups else None,
        with_groups=split(with_groups) if with_groups else None,
        without_groups=split(without_groups) if without_groups else None,
        group=group, dry_run=dry_run, force=force)


@mcp.tool(annotations={"title": "Apply raw settings", "readOnlyHint": False,
                       "destructiveHint": True, "openWorldHint": False})
def apply_raw_settings(
    settings: dict,
    targets: Literal["active", "selected"] = "active",
    history: Optional[str] = None,
) -> dict:
    """Advanced: apply RAW Develop-settings keys (exact keys from
    `get_develop_settings`, e.g. "Exposure2012", "CropLeft"). Batch-capable via
    targets="selected". `history` optionally names the History step."""
    params: dict = {"settings": dict(settings), "targets": targets}
    if history:
        params["history"] = history
    return _core.call("apply_settings", params)


@mcp.tool(annotations={"title": "Create snapshot", **_EDIT})
def create_snapshot(name: Optional[str] = None, uuid: Optional[str] = None) -> dict:
    """Create a Develop snapshot (optionally named) of the active photo, or of
    the photo with `uuid`. Create one before a multi-step edit: restoring it
    via `apply_snapshot` is the safe rollback (chained undo can over-rewind)."""
    params: dict = {}
    if name:
        params["name"] = name
    if uuid:
        params["uuid"] = uuid
    return _core.call("snapshot_create", params)


@mcp.tool(annotations={"title": "List snapshots", **_READ})
def list_snapshots(uuid: Optional[str] = None) -> dict:
    """List Develop snapshots (name + snapshotID) of the active or `uuid` photo."""
    return _core.call("snapshot_list", {"uuid": uuid} if uuid else {})


@mcp.tool(annotations={"title": "Apply (restore) snapshot", **_EDIT})
def apply_snapshot(
    name: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    uuid: Optional[str] = None,
) -> dict:
    """Restore a Develop snapshot by `name` or `snapshot_id` (see
    `list_snapshots`). The reliable rollback: unlike chained `undo`, it cannot
    over-rewind. The restore itself is a History step, so it is undoable."""
    if not name and not snapshot_id:
        return {"ok": False, "error": "give a snapshot name or snapshot_id"}
    params: dict = {}
    if name:
        params["name"] = name
    if snapshot_id:
        params["id"] = snapshot_id
    if uuid:
        params["uuid"] = uuid
    return _core.call("snapshot_apply", params)


@mcp.tool(annotations={"title": "Delete snapshot", **_DESTROY})
def delete_snapshot(snapshot_id: str, uuid: Optional[str] = None) -> dict:
    """Permanently delete a Develop snapshot by its snapshotID (from
    `list_snapshots`)."""
    params: dict = {"id": snapshot_id}
    if uuid:
        params["uuid"] = uuid
    return _core.call("snapshot_delete", params)


# --------------------------------------------------------------------------- #
# Selection / catalog (edits)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Select photo", **_EDIT})
def select_photo(
    nav: Optional[str] = None,
    uuid: Optional[str] = None,
    path: Optional[str] = None,
) -> dict:
    """Change the active photo by navigation (next, prev, first, last) or by
    `uuid` / file `path`. Give exactly one."""
    params: dict = {}
    if nav:
        params["nav"] = nav
    if uuid:
        params["uuid"] = uuid
    if path:
        params["path"] = path
    if not params:
        return {"ok": False, "error": "give nav (next/prev/first/last) or uuid/path"}
    return _core.call("select_photo", params)


@mcp.tool(annotations={"title": "Set star rating", **_EDIT})
def set_rating(value: int) -> dict:
    """Set the star rating (0-5) via the UI selection. CAUTION: in Library grid
    view this applies to ALL currently selected photos, not just the active
    one. For targeted or batch-verified writeback prefer `set_metadata`."""
    return _core.call("set_rating", {"value": value})


@mcp.tool(annotations={"title": "Set flag", **_EDIT})
def set_flag(value: Literal["pick", "reject", "none"]) -> dict:
    """Set the pick flag via the UI selection. CAUTION: in Library grid view
    this applies to ALL selected photos. Prefer `set_metadata` for targeting."""
    return _core.call("set_flag", {"value": value})


@mcp.tool(annotations={"title": "Set color label", **_EDIT})
def set_label(value: Literal["none", "red", "yellow", "green", "blue", "purple"]) -> dict:
    """Set the color label via the UI selection. CAUTION: in Library grid view
    this applies to ALL selected photos. Prefer `set_metadata` for targeting."""
    return _core.call("set_label", {"value": value})


@mcp.tool(annotations={"title": "Set photo metadata (by uuid, verified)", **_EDIT})
def set_metadata(
    uuid: Optional[str] = None,
    rating: Optional[int] = None,
    flag: Optional[Literal["pick", "reject", "none"]] = None,
    label: Optional[Literal["none", "red", "yellow", "green", "blue", "purple"]] = None,
    title: Optional[str] = None,
    caption: Optional[str] = None,
) -> dict:
    """Set rating (0-5), flag, color label, title and/or caption DIRECTLY on
    one photo (the active photo, or `uuid`), bypassing the UI selection. This
    is the reliable writeback path for rating many photos in a burst - unlike
    `set_rating`, it cannot hit the wrong photo when the selection goes stale -
    and the response echoes the photo's read-back state for verification."""
    params: dict = {}
    if uuid:
        params["uuid"] = uuid
    for k, v in (("rating", rating), ("flag", flag), ("label", label),
                 ("title", title), ("caption", caption)):
        if v is not None:
            params[k] = v
    if not [k for k in params if k != "uuid"]:
        return {"ok": False, "error": "give rating, flag, label, title and/or caption"}
    return _core.call("set_metadata", params)


@mcp.tool(annotations={"title": "Switch module", **_EDIT})
def switch_module(module: str) -> dict:
    """Switch Lightroom module, e.g. "library" or "develop"."""
    return _core.call("switch_module", {"module": module})


@mcp.tool(annotations={"title": "Undo", **_EDIT})
def undo() -> dict:
    """Undo the last Lightroom history step. The response includes canUndo /
    canRedo so you can tell when to stop. CAUTION: chained undo can rewind past
    the edit you meant to revert - prefer `apply_snapshot` for a clean rollback."""
    return _core.call("undo")


@mcp.tool(annotations={"title": "Redo", **_EDIT})
def redo() -> dict:
    """Redo the last undone Lightroom history step (response includes canUndo /
    canRedo)."""
    return _core.call("redo")


# --------------------------------------------------------------------------- #
# Knowledge base (kb/) -- recall -> apply -> learn
# --------------------------------------------------------------------------- #

def _kb_err(e: Exception) -> dict:
    return {"ok": False, "error": str(e)}


@mcp.tool(annotations={"title": "KB: recall cards for this task", **_READ})
def kb_recall(genres: str = "all", task: str = "edit",
              full: bool = True) -> dict:
    """RECALL step -- call this BEFORE culling or editing. Returns the knowledge
    cards that govern the task: the culling rubric / edit workflow, the matching
    genre card(s), and (for edits) technique, style and user-taste cards.

    Classify the photo first (from a render, not the filename), then pass its
    genre(s). `genres` is comma-separated: portrait, wildlife, landscape, street,
    city, nature, macro, travel, documentary, night, astro, architecture,
    interiors -- or "all". `task` is "cull" or "edit".

    Rate ONLY with the returned rubric (never ad-hoc criteria) and cite its
    dimensions. kb/styles/user-taste.md overrides genre defaults on conflict.
    Set full=False for just the card list (cheap) instead of their contents.
    """
    try:
        cards = _kb.recall(genres=genres, task=task)
    except _kb.KBNotFound as e:
        return _kb_err(e)
    if not full:
        for c in cards:
            c.pop("body", None)
    return {"ok": True, "count": len(cards), "cards": cards}


@mcp.tool(annotations={"title": "KB: read one card", **_READ})
def kb_read_card(card_id: str) -> dict:
    """Read a single knowledge card by its id (e.g. "core-culling-rubric",
    "genre-travel-documentary"). Use kb_recall to discover ids."""
    try:
        card = _kb.read_card(card_id)
    except _kb.KBNotFound as e:
        return _kb_err(e)
    if not card:
        return {"ok": False, "error": "no card with id %r" % card_id}
    return {"ok": True, "card": card}


@mcp.tool(annotations={"title": "KB: write a lesson (learn)", **_EDIT})
def kb_learn(title: str, situation: str, what_happened: str, rule: str,
             merge_target: str = "", genres: str = "all",
             tags: str = "uncategorized") -> dict:
    """LEARN step -- call before ending a session when one of these happened:
    the user CORRECTED you, a card's guidance was clearly wrong, or a new recipe
    worked. Writes a lesson card into kb/learned/ for later consolidation.

    Do NOT log trivia, restatements of existing cards (use kb_bump instead), or
    session mechanics. Put the ACTUAL NUMBERS in `what_happened` -- they are what
    a future session can reuse. `rule` must be one or two imperative, checkable
    sentences. `merge_target` names the card this should fold into.
    """
    try:
        path = _kb.new_lesson(title=title, situation=situation,
                              what_happened=what_happened, rule=rule,
                              merge_target=merge_target, genres=genres, tags=tags)
        _kb.build_index()
    except (_kb.KBNotFound, FileExistsError) as e:
        return _kb_err(e)
    return {"ok": True, "path": path,
            "note": "Lesson recorded. It merges into its target card at the "
                    "next consolidation pass (kb/README.md §4)."}


@mcp.tool(annotations={"title": "KB: bump a card's evidence", **_EDIT})
def kb_bump(card_id: str, to: Optional[str] = None) -> dict:
    """Record that a card's guidance was applied successfully (render + user
    confirm). Increments its evidence counter. Pass to="tested" (after ~3
    successes) or to="proven" to promote its confidence."""
    try:
        out = _kb.bump(card_id, to=to)
        _kb.build_index()  # keep kb/INDEX.md's evidence column current
        return {"ok": True, **out}
    except (_kb.KBNotFound, KeyError, ValueError) as e:
        return _kb_err(e)


@mcp.tool(annotations={"title": "KB: absorb a lesson (consolidation)", **_EDIT})
def kb_absorb(card_id: str, reset: bool = False) -> dict:
    """Consolidation step (kb/README.md §4-5): after MERGING a lesson's content
    into its target card by rewriting the card's sections, mark the lesson
    absorbed with its own id. Pass reset=True after a deep rewrite of a card to
    clear its absorbed-lessons counter."""
    try:
        out = _kb.absorb(card_id, reset=reset)
        _kb.build_index()
        return {"ok": True, **out}
    except (_kb.KBNotFound, KeyError, ValueError) as e:
        return _kb_err(e)


@mcp.tool(annotations={"title": "KB: validate all cards", **_READ})
def kb_validate() -> dict:
    """Lint every knowledge card's frontmatter (missing fields, duplicate ids).
    Run after kb_learn / manual card edits; fix whatever it reports."""
    try:
        problems, count = _kb.validate()
    except _kb.KBNotFound as e:
        return _kb_err(e)
    return {"ok": not problems, "cards": count, "problems": problems}


@mcp.tool(annotations={"title": "KB: stats and maintenance flags", **_READ})
def kb_stats() -> dict:
    """Counts plus the self-improvement flags: consolidation_due (>=10 unmerged
    lessons), rewrite_due (a card absorbed >=4 lessons and needs a deep
    re-synthesis), and stale_seeds (untested research >180 days old).
    Act on these per kb/README.md §4-5."""
    try:
        return {"ok": True, **_kb.stats()}
    except _kb.KBNotFound as e:
        return _kb_err(e)


# --------------------------------------------------------------------------- #
# Escape hatch
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Call plug-in command (advanced)",
                       "readOnlyHint": False, "destructiveHint": True,
                       "openWorldHint": False})
def call_plugin(command: str, params: Optional[dict] = None) -> dict:
    """Advanced escape hatch: send any raw plug-in command with a params object.
    Use only when no dedicated tool fits. Call it with command="help" (no
    params) to list every plug-in command. Note: `thumb` (preview-cache JPEG)
    exists here but often fails for the active Develop photo - prefer
    `render_preview`."""
    return _core.call(command, dict(params) if params else {})


@mcp.tool(annotations={"title": "Bridge daemon status", **_READ})
def daemon_status() -> dict:
    """Health of the local bridge daemon (distinct from Lightroom itself):
    whether the daemon process is running and whether it currently holds a
    connection to Lightroom. Use to tell a dead daemon from a closed Lightroom
    when `ping` fails."""
    return _core.daemon_control("status")


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
