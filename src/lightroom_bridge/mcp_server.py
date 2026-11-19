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
- Mask structure/IDs can be read on Lightroom 15.4+. Creation waits for a new
  mask ID; this does not prove pixel coverage or completed AI computation.
  Verify effects by rendering; if needed reapply absolute adjustments by mask_id.
- `capabilities` reports documented SDK support separately from UI-only features.
- AI status is partial, and preset/update requests are not completion signals.
  Respect the destination's editing policy: some platforms label AI-edited images.
- Before a multi-step edit, `create_snapshot` a named restore point; if the edit
  goes wrong, `apply_snapshot` it back. That beats chained `undo`, which can
  over-rewind past the whole edit.
- For rating/flag/label writeback at scale, prefer `set_metadata` with a uuid:
  it writes directly to that photo and returns the read-back verification.
  `set_rating`/`set_flag`/`set_label` act on the CURRENT SELECTION instead.
- Geometry delivery: geometry_capture -> geometry_plan -> geometry_apply ->
  geometry_transfer / manual references -> geometry_audit. Inspect BOTH axes and
  the annotated after image; absent credible references need explicit reasons.
  Separate transform from crop with a new capture. Prefer display-space plans
  to raw crop fractions; UUID/state guards prevent stale or wrong-photo writes.
  A successful write or texture average never proves straightness.
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


@mcp.tool(annotations={"title": "Bridge capabilities", **_READ})
def capabilities() -> dict:
    """Report Lightroom/plugin/SDK versions, available APIs, and UI-only features.
    Does not change the module, selection, or photo."""
    return _core.call("capabilities")


@mcp.tool(annotations={"title": "Read native culling selection", **_READ})
def review_selection() -> dict:
    """Read explicitly selected photos after native Assisted Culling or Duplicate
    Detection, with ratings/flags and folder-stack metadata. Does not run native
    analysis or expose unavailable focus/eye scores. Stacks do not prove duplicates."""
    return _core.call("review_selection")


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
    """List mask structure/IDs and selected ID. Supported structure read-back on
    Lightroom 15.4+; render to verify pixel coverage and adjustment strength.
    Read errors are reported explicitly, never converted to an empty list."""
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
    """Select loupe, crop, dust, redeye, masking, upright, point_color,
    local_point_color, or depth_refinement."""
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
               invert | delete | select | duplicate_invert | toggle_hidden
    type (for create/add/subtract/intersect): brush | gradient | radialGradient |
               rangeMask | aiSelection
    subtype (rangeMask/aiSelection only): subject | sky | background | objects |
               people | landscape | color | luminance | depth
    adjustments: local_* params, e.g. {"local_Exposure": -0.5, "local_Clarity": 10}
    (applied to the current mask; adjust accepts mask_id for an explicit target)

    "reset" DELETES ALL masks on the photo; "delete" removes one by `mask_id`.
    Creation waits for a new mask ID, but AI computation can continue. Verify
    with render_preview and reapply adjustments by mask_id if needed.
    toggle_hidden toggles visibility once; do not blindly retry after a timeout.
    The documented SDK 15.3 creation API provides no geometric coordinates.
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
        params = {"values": dict(adjustments)}
        if mask_id:
            params["id"] = mask_id
        return _core.call("mask_adjust", params)
    if op == "list":
        return _core.call("mask_list")
    if op == "reset":
        return _core.call("mask_reset")
    if op == "invert":
        return _core.call("mask_invert", {"id": mask_id} if mask_id else {})
    if op in ("duplicate_invert", "toggle_hidden"):
        return _core.call("mask_" + op, {"id": mask_id} if mask_id else {})
    if op in ("delete", "select"):
        if not mask_id:
            return {"ok": False, "error": "operation %r requires `mask_id`" % op}
        return _core.call("mask_" + op, {"id": mask_id})
    return {"ok": False, "error": "unknown mask operation: %r" % op}


@mcp.tool(annotations={"title": "Point Color", **_DESTROY})
def point_color(
    operation: Literal["list", "add", "update", "select", "delete", "clear"] = "list",
    swatch: Optional[dict] = None,
    index: Optional[int] = None,
    is_local: bool = False,
    mask_id: Optional[str] = None,
) -> dict:
    """Global/per-mask Point Color. add requires SrcHue (0..6), SrcSat and
    SrcLum (0..1). Optional HueShift/SatScale/LumScale/Variance are -1..1;
    RangeAmount is 0..1. HueRange/SatRange/LumRange contain ordered 0..1 limits:
    LowerNone, LowerFull, UpperFull, UpperNone. update merges provided fields
    into the selected or indexed swatch. Indices are 1..8. is_local=true uses
    the current mask or mask_id. clear removes all swatches in that scope.
    Returns SDK read-back; render to verify the effect."""
    params: dict = {"operation": operation, "isLocal": is_local}
    if swatch is not None:
        params["swatch"] = dict(swatch)
    if index is not None:
        params["index"] = index
    if mask_id:
        params["maskId"] = mask_id
    return _core.call("point_color", params)


@mcp.tool(annotations={"title": "Edit a mask component", **_DESTROY})
def mask_tool(
    operation: Literal["select", "delete", "toggle_hidden", "invert"],
    tool_id: str,
    mask_id: Optional[str] = None,
) -> dict:
    """Operate on a component ID from list_masks. Requires membership in the
    selected/explicit parent mask. Toggles must not be retried blindly."""
    params = {"operation": operation, "toolId": tool_id}
    if mask_id:
        params["maskId"] = mask_id
    return _core.call("mask_tool", params)


@mcp.tool(annotations={"title": "Delete empty masks", **_DESTROY})
def delete_empty_masks(targets: Literal["active", "selected"] = "active") -> dict:
    """Ask Lightroom to delete empty masks from the active or explicitly selected
    photos. Create snapshots first. Does not report a guessed deleted-mask count."""
    return _core.call("mask_delete_empty", {"targets": targets})


@mcp.tool(annotations={"title": "Read partial AI edit status", **_READ})
def ai_state() -> dict:
    """Read Enhance, Reflection Removal and known-filter status for the active
    photo. Partial SDK coverage, not a universal AI-free/completion test."""
    return _core.call("ai_state")


@mcp.tool(annotations={"title": "Update AI settings", **_EDIT})
def update_ai_settings(targets: Literal["active", "selected"] = "active") -> dict:
    """Request recomputation of AI settings. May continue in the background;
    inspect Lightroom AI Edit Status and render before using the result.
    Respect channel restrictions on AI processing."""
    return _core.call("update_ai_settings", {"targets": targets})


@mcp.tool(annotations={"title": "Enhance photo", **_EDIT})
def enhance(
    feature: Literal["denoise", "rawDetails", "superRes"] = "denoise",
    enable: bool = True,
    amount: float = 50,
) -> dict:
    """Set AI Denoise, Raw Details or Super Resolution explicitly. Checks photo
    availability and returns panel state, not GPU-completion confirmation.
    amount (1..100) is for Denoise. Skip it when the destination labels AI edits."""
    return _core.call("enhance", {"feature": feature, "enable": enable, "amount": amount})


@mcp.tool(annotations={"title": "Reflection Removal status", **_READ})
def reflection_state() -> dict:
    """Read Reflection Removal availability, enabled state, amount and quality."""
    return _core.call("reflection_state")


@mcp.tool(annotations={"title": "Reflection Removal", **_EDIT})
def reflection_removal(
    enable: bool = True,
    amount: Optional[float] = None,
    quality: Optional[Literal["preview", "standard", "best"]] = None,
    timeout: float = 90,
) -> dict:
    """Set Reflection Removal; amount -100..100, timeout 1..600 seconds.
    Waits for its SDK callback where applicable; timedOut=true means pending,
    not success. Render to verify. Skip it when the destination labels AI edits."""
    params: dict = {"enable": enable, "timeout": timeout}
    if amount is not None:
        params["amount"] = amount
    if quality:
        params["quality"] = quality
    return _core.call("reflection_removal", params)


# --------------------------------------------------------------------------- #
# Presets / batch (edits)
# --------------------------------------------------------------------------- #

@mcp.tool(annotations={"title": "Apply preset", **_EDIT})
def apply_preset(
    name: Optional[str] = None,
    uuid: Optional[str] = None,
    targets: Literal["active", "selected"] = "active",
    amount: int = 100,
    update_ai: bool = False,
) -> dict:
    """Apply a Develop preset by `name` (case-insensitive, first match across
    folders) or exactly by preset `uuid` (from `list_presets` - unambiguous
    when names collide). amount: 0..200, where supported by the preset.
    update_ai requests AI recomputation after applying; opt-in, subject to
    channel policy. The response is not background-AI completion confirmation."""
    if not name and not uuid:
        return {"ok": False, "error": "give a preset name or uuid (see list_presets)"}
    params: dict = {"targets": targets, "amount": amount, "updateAISettings": update_ai}
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




# Geometry uses the same implementation as the CLI; imports are lazy so ordinary
# bridge tools work without the optional computer-vision dependencies.
@mcp.tool(annotations={"title": "Capture geometry evidence", **_READ})
def geometry_capture(uuid: str, out_dir: str, size: int = 2560) -> dict:
    """Save photo UUID, full Develop state and unique render together. Use a NEW
    output directory. Returns capture path and reference template. Look at render.
    Needs plug-in 1.4+ and the geometry package extra."""
    from . import geometry
    return geometry.run('capture', uuid=uuid, out_dir=out_dir, size=size)


@mcp.tool(annotations={"title": "Plan display-space geometry", **_READ})
def geometry_plan(capture_path: str, out: str, reason: str,
                  controls: Optional[dict[str, float]] = None,
                  box: Optional[list[float]] = None, ratio: Optional[float] = None,
                  anchor: Optional[list[float]] = None,
                  protected: Optional[list[dict[str, Any]]] = None) -> dict:
    """Create a reviewable plan, no Lightroom writes. controls are absolute display
    angle (clockwise), vertical, horizontal. OR trim CURRENT RENDER with box
    [left,top,right,bottom], optional width/height ratio and anchor [x,y]. Never
    combine transform and trim: capture a new render between them. Protected
    feature entries: label, point=[x,y], margin=.02. Fractions are DISPLAY space.
    Checks sensor crop math against actual native dimensions; fails closed on
    unsupported states. Keep reason specific to the visible problem."""
    from . import geometry
    return geometry.run('plan', capture_path=capture_path, out=out, reason=reason,
                        controls=controls, box=box, ratio=ratio, anchor=anchor or [.5,.5], protected=protected)


@mcp.tool(annotations={"title": "Apply guarded geometry plan", **_EDIT})
def geometry_apply(plan_path: str, out_dir: str) -> dict:
    """Apply to captured UUID only if catalog and FULL Develop state still match.
    Creates rollback snapshot and persistent receipt before editing, checks
    readback and unchanged tone/masks, exports native after image and checks crop
    dimensions. Never automatically retries a write. On error inspect receipt
    and snapshot first. Successful response still requires geometry_audit and
    visual comparison. out_dir must be new."""
    from . import geometry
    return geometry.run('apply', plan_path=plan_path, out_dir=out_dir)


@mcp.tool(annotations={"title": "Audit named horizontal and vertical references", **_READ})
def geometry_audit(capture_path: str, references: str, out: str,
                   review: Optional[dict[str, Any]] = None) -> dict:
    """Measure named lines bound to this exact render hash, save JSON and overlay.
    References JSON: image_size, image_sha256, references [{label,axis,reason,
    points:[[x,y],...]}], unavailable {axis:reason}. Angles use physical pixels.
    Reports residual, span and placement uncertainty, never automatic level
    certification. Read overlay and full image. Optional review must explicitly
    check reference placement, proportions, borders, composition, and provide
    decision accept/revise/unverifiable plus reason. Missing axes cannot pass."""
    from . import geometry
    return geometry.run('audit', capture_path=capture_path, references=references, out=out, review=review)


@mcp.tool(annotations={"title": "Relocate geometry references after transform", **_READ})
def geometry_transfer(source_capture: str, target_capture: str, references: str, out: str) -> dict:
    """Match SAME PHOTO's features and relocate references after geometry only.
    Rejects stale images, changed tone/lens/masks, sparse/clustered matching,
    unsupported reference regions and lines outside crop. Output is provisional:
    inspect target overlay on actual edges. Manually re-mark if rejected."""
    from . import geometry
    return geometry.run('transfer', source_capture=source_capture, target_capture=target_capture,
                        references=references, out=out)


@mcp.tool(annotations={"title": "Suggest a bounded geometry correction", **_READ})
def geometry_suggest(base_audit: str, probe_audits: list[str], out: str) -> dict:
    """Estimate local response from separate ONE-CONTROL probes of the SAME
    baseline and SAME named lines. Rejects rank deficiency, poor conditioning
    and sub-uncertainty residuals. Proposes at most 1 degree / 15 perspective
    units per iteration. No writes, no extrapolated one-shot solution. Restore
    baseline, plan proposal, apply, transfer/re-mark and re-audit actual output."""
    from . import geometry
    return geometry.run('suggest', base_audit=base_audit, probe_audits=probe_audits, out=out)


if __name__ == "__main__":
    main()
