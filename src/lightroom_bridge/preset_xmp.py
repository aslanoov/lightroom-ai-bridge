"""Write a Lightroom develop preset as an XMP file the Develop Presets
panel can load.

Lightroom Classic (7.3+) loads user develop presets from XMP files in
~/Library/Application Support/Adobe/CameraRaw/Settings/ at STARTUP; a
newly written preset appears in the panel (under the group named by
crs:Group) after the next Lightroom restart, and from then on is visible
to `preset list` / `preset apply` like any other preset. The SDK's own
addDevelopPresetForPlugin was rejected for this feature: presets it
creates are hidden from the Develop panel by design.

The XMP shell mirrors knowledge/Clean_Tones.xmp (a preset verified to
load in this user's LrC 15.4): flat crs:* attributes for sliders,
crs:Name / crs:Group as rdf:Alt children, tone curves as rdf:Seq pairs.

Pure stdlib, Python 3.9 compatible (same contract as cli.py / _core.py).
"""

import os
import re
import uuid as _uuid
from xml.dom import minidom
from xml.sax.saxutils import escape, quoteattr, unescape

SETTINGS_DIR = os.path.expanduser(
    "~/Library/Application Support/Adobe/CameraRaw/Settings")

DEFAULT_GROUP = "Claude"

# What transfers between photos (a "look") vs. what is photo-specific.
# Keys are raw develop-settings names exactly as photo:getDevelopSettings()
# returns them. Groups off by default: wb and exposure are per-scene/per-frame
# (kb/styles/clean-tones.md: "The preset is the grade, not the exposure").
# Never included at all: crop/geometry, masks (write-broken via settings,
# and scene-specific anyway), retouch, lens corrections, profile/Look.
GROUPS = {
    "wb": ["Temperature", "Tint", "WhiteBalance"],
    "exposure": ["Exposure2012"],
    "tone": ["Contrast2012", "Highlights2012", "Shadows2012",
             "Whites2012", "Blacks2012"],
    "curve": ["ToneCurveName2012", "ToneCurvePV2012", "ToneCurvePV2012Red",
              "ToneCurvePV2012Green", "ToneCurvePV2012Blue",
              "ParametricShadows", "ParametricDarks", "ParametricLights",
              "ParametricHighlights", "ParametricShadowSplit",
              "ParametricMidtoneSplit", "ParametricHighlightSplit",
              "CurveRefineSaturation", "EnableToneCurve"],
    "presence": ["Texture", "Clarity2012", "Dehaze"],
    "color": ["Vibrance", "Saturation", "ConvertToGrayscale",
              "EnableColorAdjustments"] + [
        adj + c
        for adj in ("HueAdjustment", "SaturationAdjustment",
                    "LuminanceAdjustment")
        for c in ("Red", "Orange", "Yellow", "Green",
                  "Aqua", "Blue", "Purple", "Magenta")],
    "grade": ["SplitToningShadowHue", "SplitToningShadowSaturation",
              "SplitToningHighlightHue", "SplitToningHighlightSaturation",
              "SplitToningBalance",
              "ColorGradeMidtoneHue", "ColorGradeMidtoneSat",
              "ColorGradeMidtoneLum", "ColorGradeShadowLum",
              "ColorGradeHighlightLum", "ColorGradeGlobalHue",
              "ColorGradeGlobalSat", "ColorGradeGlobalLum",
              "ColorGradeBlending", "EnableSplitToning"],
    "effects": ["PostCropVignetteAmount", "PostCropVignetteMidpoint",
                "PostCropVignetteFeather", "PostCropVignetteRoundness",
                "PostCropVignetteStyle", "PostCropVignetteHighlightContrast",
                "GrainAmount", "GrainSize", "GrainFrequency",
                "EnableEffects"],
    "detail": ["Sharpness", "SharpenRadius", "SharpenDetail",
               "SharpenEdgeMasking", "LuminanceSmoothing",
               "LuminanceNoiseReductionDetail",
               "LuminanceNoiseReductionContrast", "ColorNoiseReduction",
               "ColorNoiseReductionDetail", "ColorNoiseReductionSmoothness",
               "EnableDetail"],
    "calibration": ["ShadowTint", "RedHue", "RedSaturation", "GreenHue",
                    "GreenSaturation", "BlueHue", "BlueSaturation",
                    "EnableCalibration"],
    # B&W channel mixer; auto-added when the source photo is a grayscale
    # conversion so a saved B&W look carries its actual recipe.
    "bw": ["GrayMixerRed", "GrayMixerOrange", "GrayMixerYellow",
           "GrayMixerGreen", "GrayMixerAqua", "GrayMixerBlue",
           "GrayMixerPurple", "GrayMixerMagenta", "EnableGrayscaleMix"],
}

DEFAULT_GROUPS = ["tone", "curve", "presence", "color", "grade",
                  "effects", "detail", "calibration"]

# Present in getDevelopSettings but deliberately never saved; surfaced as a
# warning when the source edit actually uses them so the loss is visible.
_UNSUPPORTED_LOOK_KEYS = {
    "PointColors": "Point Color edits are not supported in saved presets",
    "Look": "profile/Look is not included (the preset leaves the "
            "photo's profile untouched)",
}

# Fixed shell attributes, copied from a preset verified to load in LrC 15.4.
_SHELL_ATTRS = [
    ("crs:PresetType", "Normal"),
    ("crs:Cluster", ""),
    # crs:UUID inserted here at build time
    ("crs:SupportsAmount2", "True"),
    ("crs:SupportsAmount", "True"),
    ("crs:SupportsColor", "True"),
    ("crs:SupportsMonochrome", "True"),
    ("crs:SupportsHighDynamicRange", "True"),
    ("crs:SupportsNormalDynamicRange", "True"),
    ("crs:SupportsSceneReferred", "True"),
    ("crs:SupportsOutputReferred", "True"),
    ("crs:RequiresRGBTables", "False"),
    ("crs:ShowInPresets", "True"),
    ("crs:ShowInQuickActions", "False"),
    ("crs:CameraModelRestriction", ""),
    ("crs:Copyright", ""),
    ("crs:ContactInfo", ""),
    ("crs:Version", "18.1"),
    ("crs:CompatibleVersion", "285212672"),
]


def resolve_groups(groups=None, with_groups=None, without_groups=None):
    """Return the ordered group list: DEFAULT_GROUPS unless `groups`
    overrides, then +with_groups, -without_groups. Raises on unknown names
    in ANY of the three lists (a typo'd --without must not silently keep
    the group it meant to drop)."""
    unknown = [g for g in list(groups or []) + list(with_groups or [])
               + list(without_groups or []) if g not in GROUPS]
    if unknown:
        raise ValueError("unknown preset group(s): %s (valid: %s)"
                         % (", ".join(unknown), ", ".join(sorted(GROUPS))))
    chosen = list(groups) if groups else list(DEFAULT_GROUPS)
    for g in (with_groups or []):
        if g not in chosen:
            chosen.append(g)
    return [g for g in chosen if g not in set(without_groups or [])]


def filter_settings(settings, groups):
    """Pick the keys for `groups` out of a raw develop-settings dict.
    Keys absent from the photo's settings are skipped silently (older
    process versions lack some keys)."""
    payload = {}
    for g in groups:
        for key in GROUPS[g]:
            if key in settings:
                payload[key] = settings[key]
    return payload


def _fmt_number(v):
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return repr(round(v, 6))
    return _clean(str(v))


def _curve_element(name, points):
    """Flat [x0, y0, x1, y1, ...] -> crs curve child element."""
    lines = ["   <%s>" % name, "    <rdf:Seq>"]
    for i in range(0, len(points) - 1, 2):
        lines.append("     <rdf:li>%s, %s</rdf:li>"
                     % (_fmt_number(points[i]), _fmt_number(points[i + 1])))
    lines.extend(["    </rdf:Seq>", "   </%s>" % name])
    return "\n".join(lines)


def _alt_element(name, text):
    return ("   <%s>\n    <rdf:Alt>\n     <rdf:li xml:lang=\"x-default\">"
            "%s</rdf:li>\n    </rdf:Alt>\n   </%s>" % (name, escape(text), name))


def build_xmp(name, payload, group=DEFAULT_GROUP, process_version="15.4",
              preset_uuid=None):
    """Render the preset XMP document text."""
    preset_uuid = preset_uuid or _uuid.uuid4().hex.upper()
    attrs, children = [], []
    for k, v in _SHELL_ATTRS:
        attrs.append((k, v))
        if k == "crs:Cluster":
            attrs.append(("crs:UUID", preset_uuid))
    attrs.append(("crs:ProcessVersion", str(process_version)))
    for key in sorted(payload):
        v = payload[key]
        if isinstance(v, (list, tuple)):
            children.append(_curve_element("crs:" + key, list(v)))
        else:
            attrs.append(("crs:" + key, _fmt_number(v)))
    attrs.append(("crs:HasSettings", "True"))

    attr_text = "\n".join("   %s=%s" % (k, quoteattr(str(v)))
                          for k, v in attrs)
    child_text = "\n".join(
        [_alt_element("crs:Name", name), _alt_element("crs:Group", group)]
        + children)
    return (
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" x:xmptk=\"lightroom-bridge\">\n"
        " <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\n"
        "  <rdf:Description rdf:about=\"\"\n"
        "    xmlns:crs=\"http://ns.adobe.com/camera-raw-settings/1.0/\"\n"
        + attr_text + ">\n"
        + child_text + "\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n")


# XML 1.0 forbids C0 controls (except tab/newline/CR) even when escaped;
# saxutils passes them through, which would yield a file LrC silently skips.
_XML_ILLEGAL = re.compile(u"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(s):
    return _XML_ILLEGAL.sub("", s)


def _slug(name):
    # \w keeps unicode letters/digits so non-Latin names get distinct files.
    s = re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("_")
    return s or "preset"


def _existing_preset_name(path):
    """crs:Name recorded in an existing preset file, or None if unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"<crs:Name>.*?<rdf:li[^>]*>(.*?)</rdf:li>", text, re.S)
    return unescape(m.group(1)) if m else None


def save_preset(name, settings, groups=None, with_groups=None,
                without_groups=None, group=DEFAULT_GROUP, dry_run=False,
                force=False, settings_dir=None):
    """Filter `settings` and write the preset XMP. Returns a result dict
    (never raises for the expected failure modes; mirrors the CLI's
    {ok, result|error} envelope convention)."""
    name = _clean(name).strip()
    group = _clean(group).strip() or DEFAULT_GROUP
    if not name:
        return {"ok": False, "error": "preset name is empty"}
    try:
        chosen = resolve_groups(groups, with_groups, without_groups)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if (settings.get("ConvertToGrayscale") and "bw" not in chosen
            and "bw" not in set(without_groups or [])):
        chosen.append("bw")
    payload = filter_settings(settings, chosen)
    if not payload:
        return {"ok": False, "error": "no develop settings matched groups: %s"
                % ", ".join(chosen)}

    target_dir = settings_dir or SETTINGS_DIR
    path = os.path.join(target_dir, _slug(name) + ".xmp")
    warnings = [msg for key, msg in sorted(_UNSUPPORTED_LOOK_KEYS.items())
                if settings.get(key)]
    result = {
        "preset": name,
        "group": group,
        "path": path,
        "groups": chosen,
        "keys": len(payload),
        "excluded": sorted(set(GROUPS) - set(chosen)),
        "note": ("appears in the Develop Presets panel (group '%s') after "
                 "the next Lightroom Classic restart; then usable via "
                 "`preset list` / `preset apply`" % group),
    }
    if warnings:
        result["warnings"] = warnings
    if dry_run:
        result["dryRun"] = True
        result["payload"] = {k: payload[k] for k in sorted(payload)}
        return {"ok": True, "result": result}

    if os.path.exists(path):
        existing = _existing_preset_name(path)
        if existing != name:
            return {"ok": False, "error":
                    "filename collision: %s already holds the preset %r "
                    "(different from %r). Pick another name; overwrite is "
                    "refused to protect the existing preset."
                    % (path, existing, name)}
        if not force:
            return {"ok": False, "error": "preset %r already exists at %s "
                    "(pass force/--force to overwrite it)" % (name, path)}
    xmp = build_xmp(name, payload,
                    group=group,
                    process_version=settings.get("ProcessVersion", "15.4"))
    try:
        minidom.parseString(xmp)
    except Exception as e:
        return {"ok": False, "error": "generated XMP failed validation "
                "(nothing written): %s" % e}
    try:
        os.makedirs(target_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(xmp)
    except OSError as e:
        return {"ok": False, "error": "could not write %s: %s" % (path, e)}
    return {"ok": True, "result": result}
