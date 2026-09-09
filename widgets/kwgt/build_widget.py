"""Build the original AI Usage KWGT preset. No credentials are read or embedded.

Run: uv run --frozen python widgets/kwgt/build_widget.py
The ZIP container and JSON fields follow KWGT exports; see README.md for sources.
"""

from __future__ import annotations

import argparse
import json
import math
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 720, 600
COMPACT_HEIGHT = 480
FLOW_ID = "Faiusage"
COLORS = {
    "bg": "#FF101419",
    "panel": "#FF1B2128",
    "track": "#FF303944",
    "ink": "#FFF3F5F7",
    "muted": "#FFA4AFBC",
    "claude": "#FFE5A27B",
    "codex": "#FF78D9C2",
    "warn": "#FFF0C778",
}


def kode(expression: str) -> str:
    return f"${expression}$"


def jp(path: str, source: str = "gv(data)") -> str:
    return f'tc(json, {source}, ".{path}")'


def matches(value: str, pattern: str) -> str:
    # Replacing a FULL match is empty. Checking length preserves legitimate zero
    # but rejects missing fields, null, arrays, negative values, and error text.
    return f'(tc(len, {value}) > 0 & tc(reg, {value}, "{pattern}", "") = "")'


def valid_status(provider: str, source: str = "#response") -> str:
    value = jp(f"{provider}.status", source)
    return f'({value} = "ok" | {value} = "error" | {value} = "unavailable")'


def globals_spec(variant="midnight") -> dict:
    result = {}

    def add(name, value, description, kind="TEXT", formula=None):
        item = {
            "index": len(result) + 1,
            "type": kind,
            "title": name,
            "description": description,
            "value": value,
        }
        if formula:
            # TEXT globals evaluate their value as Kode. Numeric/color globals
            # instead use global_formula + toggles, as in native exports.
            if kind == "TEXT":
                item["value"] = kode(formula)
            else:
                item.update(global_formula=kode(formula), toggles=10)
        result[name] = item

    add(
        "server",
        "",
        "SET THIS: HTTPS origin only, e.g. https://homelab.example.ts.net; no API path",
    )
    add(
        "token",
        "",
        "SET THIS: backend widget token only. Private; remove before sharing/exporting.",
    )
    for key, color in COLORS.items():
        add(key, color, f"Theme: {key}", "COLOR")
    if variant == "oneui_3x2":
        # Neutral surfaces with restrained accents, following device dark mode.
        for key, light, dark in [
            ("bg", "#FFFCFCFC", "#FF1C1C1E"),
            ("ink", "#FF171719", "#FFF5F5F7"),
            ("muted", "#FF66666C", "#FFB0B0B6"),
            ("track", "#FFE7E7EB", "#FF38383D"),
            ("claude", "#FFAD603A", "#FFE3A47F"),
            ("codex", "#FF087D65", "#FF79D2B5"),
            ("warn", "#FF9B5900", "#FFF0C778"),
        ]:
            result[key].update(
                value=light,
                toggles=10,
                global_formula=kode(f'if(si(darkmode), "{dark}", "{light}")'),
            )
    add("data", "{}", "Internal: last valid compact JSON response; do not edit")
    add("checked", "0", "Internal: time of last valid HTTP response")
    add("attempt", "0", "Internal: time of last connection attempt")
    add("bound", "", "Internal: server associated with cached data")
    origin = matches("gv(server)", r"^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$")
    token = matches("gv(token)", r"^[A-Za-z0-9._~-]{16,}$")
    add(
        "ready",
        "0",
        "Internal: configuration is valid",
        formula=f"if({origin} & {token}, 1, 0)",
    )
    add(
        "hasdata",
        "0",
        "Internal: data belongs to configured server",
        formula="if(gv(checked) > 0 & gv(bound) = gv(server), 1, 0)",
    )
    for key, path in {
        "cs": "claude.session",
        "cw": "claude.weekly",
        "g5": "openai.five_hour",
        "gw": "openai.weekly",
    }.items():
        val = jp(path)
        add(
            key,
            "-1",
            f"Internal: {path}; -1 is unavailable",
            formula=f"if(gv(hasdata) = 1 & {matches(val, '^[0-9]+$')}, {val}, -1)",
        )
    # Both native JSON boolean representations seen by formula engines work.
    stale = jp("stale")
    add(
        "state",
        "SETUP",
        "Internal: status displayed in header",
        formula=(
            'if(gv(ready) = 0, "SETUP", '
            'gv(attempt) > gv(checked) & df(S) - gv(attempt) < 45, "SYNCING", '
            'gv(attempt) > gv(checked), "OFFLINE", '
            'gv(hasdata) = 0, "TAP TO SYNC", '
            f'df(S) - gv(checked) > 1800 | {stale} = "true" | {stale} = 1, "STALE", '
            '"UPDATED")'
        ),
    )
    return result


def flow_spec() -> dict:
    actions = []

    def action(kind, **params):
        actions.append(
            {"id": f"TAai{len(actions):04d}", "type": kind, "params": params}
        )

    def stop(condition):
        action(
            "A_STOP_IF", mode="NOT_EMPTY", formula=kode(f'if({condition}, "STOP", "")')
        )

    def set_global(name, expression):
        action("A_FORMULA", formula=kode(expression))
        action("A_GLOBAL", store_mode="TEXT", **{"global": name})

    stop("gv(ready) = 0")
    # Limit taps to one request per 15 seconds; no provider refresh or POST.
    stop("df(S) - gv(attempt) < 15")
    set_global("attempt", "df(S)")
    # Snapshot origin locally so a settings edit mid-request cannot mislabel it.
    action("A_FORMULA", formula=kode("gv(server)"))
    action("A_LOCAL", local_var="origin", store_mode="TEXT")
    action(
        "A_WGET",
        uri=kode('tc(reg, #origin, "/$", "") + "/api/v1/usage/compact"'),
        method="GET",
        headers="Authorization: Bearer $gv(token)$\nAccept: application/json",
        ignore_ssl_errors=False,
        ignore_http_errors=False,
    )
    # WGET produces a private local file. TEXT reads it into a flow-local string.
    action("A_LOCAL", local_var="response", store_mode="TEXT", fail_if_empty=True)
    stop("#code != 200")
    stop(f"({valid_status('claude')} & {valid_status('openai')}) = 0")
    stop("#origin != gv(server)")
    set_global("data", "#response")
    set_global("bound", "#origin")
    set_global("checked", "df(S)")
    return {
        "id": FLOW_ID,
        "name": "Fetch AI usage (every 10 minutes + tap)",
        "t": [
            {"id": "TTaiload", "type": "T_ONCE"},
            {
                "id": "TTaicron",
                "type": "T_CRON",
                "params": {"cron_string": "*/10 * * * *"},
            },
            {"id": "TTaitap0", "type": "T_MANUAL"},
        ],
        "a": actions,
    }


def paint(module, color):
    if color.startswith("$"):
        module.setdefault("internal_toggles", {})["paint_color"] = 10
        module.setdefault("internal_formulas", {})["paint_color"] = color
    elif color in COLORS:
        module.setdefault("internal_toggles", {})["paint_color"] = 100
        module.setdefault("internal_globals", {})["paint_color"] = color
    else:
        module["paint_color"] = color
    return module


def rect(title, x, y, width, height, color, radius=0):
    return paint(
        {
            "internal_type": "ShapeModule",
            "internal_title": title,
            "shape_type": "RECT",
            "shape_width": float(width),
            "shape_height": float(height),
            "shape_corners": float(radius),
            "position_anchor": "TOPLEFT",
            "position_offset_x": float(x),
            "position_offset_y": float(y),
        },
        color,
    )


def text(title, value, x, y, size, color="ink", bold=False, right=False):
    return paint(
        {
            "internal_type": "TextModule",
            "internal_title": title,
            "text_expression": value,
            "text_size": float(size),
            "text_family": "kfile://org.kustom.provider/fonts/Roboto-"
            + ("Medium.ttf" if bold else "Regular.ttf"),
            "position_anchor": "TOPRIGHT" if right else "TOPLEFT",
            "position_offset_x": float(WIDTH - x if right else x),
            "position_offset_y": float(y),
        },
        color,
    )


def layout() -> list:
    items = [
        rect("Card background", 0, 0, WIDTH, HEIGHT, "bg", 36),
        text("Title", "AI USAGE", 36, 28, 29, bold=True),
        text("Subtitle", "YOUR SUBSCRIPTIONS, AT A GLANCE", 37, 67, 13, "muted"),
        text(
            "Connection state",
            "$gv(state)$",
            682,
            38,
            15,
            '$if(gv(state) = "UPDATED", gv(codex), gv(warn))$',
            right=True,
        ),
    ]
    for provider, name, accent, top, windows in [
        (
            "claude",
            "Claude",
            "claude",
            108,
            [
                ("cs", "SESSION", "session_reset_in"),
                ("cw", "WEEKLY", "weekly_reset_in"),
            ],
        ),
        (
            "openai",
            "Codex",
            "codex",
            328,
            [
                ("g5", "5 HOUR", "five_hour_reset_in"),
                ("gw", "WEEKLY", "weekly_reset_in"),
            ],
        ),
    ]:
        items += [
            rect(f"{name} panel", 20, top, 680, 204, "panel", 24),
            rect(f"{name} accent", 42, top + 25, 4, 26, accent, 2),
            text(f"{name} title", name, 58, top + 20, 29, bold=True),
        ]
        status, stale, collected = (
            jp(f"{provider}.status"),
            jp(f"{provider}.stale"),
            jp(f"{provider}.collected_at"),
        )
        # Provider-specific age, not aggregate updated_at (which is the newest).
        sample = kode(
            f'if(gv(hasdata) = 0 | {collected} = "" | {collected} = "null", "NO SAMPLE", '
            f'{status} != "ok" | {stale} = "true" | {stale} = 1 | df(S) - gv(checked) > 1800, "STALE SAMPLE", '
            f'"SAMPLED " + mu(max, 0, mu(floor, (df(S) - df(S, {collected}))/60)) + "m AGO")'
        )
        items.append(
            text(f"{name} sample age", sample, 674, top + 29, 14, "muted", right=True)
        )
        for i, (key, label, reset_key) in enumerate(windows):
            row = top + 76 + i * 62
            value = f"gv({key})"
            items += [
                text(f"{name} {label} label", label, 44, row, 15, "muted"),
                rect(f"{name} {label} track", 154, row + 5, 424, 9, "track", 4.5),
            ]
            bar = rect(
                f"{name} {label} used bar",
                154,
                row + 5,
                1,
                9,
                kode(f'if({value} > 0, gv({accent}), "#00000000")'),
                4.5,
            )
            bar.setdefault("internal_toggles", {})["shape_width"] = 10
            bar.setdefault("internal_formulas", {})["shape_width"] = kode(
                f"mu(max, 1, mu(min, 100, {value}) * 4.24)"
            )
            reset = jp(f"{provider}.{reset_key}")
            reset_label = kode(
                f'if({value} < 0, "Not reported", {reset} = "" | {reset} = "null", "Reset unavailable", '
                f'"Reset in " + tc(ell, {reset}, 22))'
            )
            items += [
                bar,
                text(
                    f"{name} {label} percent",
                    kode(f'if({value} < 0, "—", {value} + "%")'),
                    674,
                    row - 6,
                    25,
                    accent,
                    bold=True,
                    right=True,
                ),
                text(f"{name} {label} reset", reset_label, 154, row + 24, 14, "muted"),
            ]
    items += [
        text(
            "Footer action",
            '$if(gv(ready) = 0, "Set server + token in Globals", "Tap card to sync")$',
            36,
            560,
            15,
            "muted",
        ),
        text(
            "Footer fetch age",
            '$if(gv(hasdata) = 0, "PRIVATE / LOCAL", "Fetched " + mu(max, 0, mu(floor, (df(S) - gv(checked))/60)) + "m ago")$',
            684,
            560,
            15,
            "muted",
            right=True,
        ),
    ]
    return items


def compact_layout() -> list:
    """Purpose-built 3:2 card; no nested panels or tiny four-row dashboard."""
    items = [
        rect("Card background", 0, 0, WIDTH, COMPACT_HEIGHT, "bg", 64),
        text("Title", "AI usage", 40, 30, 36, bold=True),
        text("Measurement", "% used", 680, 41, 24, "muted", right=True),
    ]
    for provider, name, accent, left, main, week, label, reset_key in [
        ("claude", "Claude", "claude", 40, "cs", "cw", "Session", "session_reset_in"),
        ("openai", "Codex", "codex", 392, "g5", "gw", "5 hour", "five_hour_reset_in"),
    ]:
        status, stale = jp(f"{provider}.status"), jp(f"{provider}.stale")
        bad = (
            f'gv(hasdata) = 1 & ({status} != "ok" | {stale} = "true" | '
            f"{stale} = 1 | df(S) - gv(checked) > 1800)"
        )
        items += [
            text(
                f"{name} title",
                kode(f'if({bad}, "{name} · stale", "{name}")'),
                left,
                100,
                32,
                bold=True,
            ),
            text(
                f"{name} primary percent",
                kode(f'if(gv({main}) < 0, "—", gv({main}) + "%")'),
                left,
                146,
                80,
                "ink",
                bold=True,
            ),
        ]
        reset = jp(f"{provider}.{reset_key}")
        reset_line = kode(
            f'if(gv({main}) < 0, "{label} · unavailable", '
            f'{reset} = "" | {reset} = "null", "{label} · no reset", '
            f'"{label} · " + tc(ell, {reset}, 12))'
        )
        items.append(text(f"{name} primary reset", reset_line, left, 238, 24, "muted"))
        for key, y, height in ((main, 280, 12), (week, 390, 8)):
            items.append(
                rect(f"{name} {key} track", left, y, 288, height, "track", height / 2)
            )
            bar = rect(
                f"{name} {key} used bar",
                left,
                y,
                1,
                height,
                kode(f'if(gv({key}) > 0, gv({accent}), "#00000000")'),
                height / 2,
            )
            bar.setdefault("internal_toggles", {})["shape_width"] = 10
            bar.setdefault("internal_formulas", {})["shape_width"] = kode(
                f"mu(max, 1, mu(min, 100, gv({key})) * 2.88)"
            )
            items.append(bar)
        reset = jp(f"{provider}.weekly_reset_in")
        items += [
            text(f"{name} weekly label", "Weekly", left, 322, 28, "muted"),
            text(
                f"{name} weekly percent",
                kode(f'if(gv({week}) < 0, "—", gv({week}) + "%")'),
                left + 288,
                312,
                38,
                "ink",
                bold=True,
                right=True,
            ),
            text(
                f"{name} weekly reset",
                kode(
                    f'if(gv({week}) < 0, "Unavailable", '
                    f'{reset} = "" | {reset} = "null", "No reset reported", "Resets in " + tc(ell, {reset}, 12))'
                ),
                left,
                359,
                23,
                "muted",
            ),
        ]
    items += [
        text(
            "Connection and fetch age",
            kode(
                'if(gv(ready) = 0, "Set server + token", '
                'gv(state) != "UPDATED", gv(state), "Synced " + '
                'mu(max, 0, mu(floor, (df(S) - gv(checked))/60)) + "m ago")'
            ),
            40,
            433,
            23,
            '$if(gv(state) = "UPDATED", gv(muted), gv(warn))$',
        ),
        text("Tap hint", "Tap to sync", 680, 433, 23, "muted", right=True),
    ]
    return items


def make_preset(variant="midnight") -> dict:
    if variant not in ("midnight", "oneui_3x2"):
        raise ValueError(f"Unknown widget variant: {variant}")
    compact = variant == "oneui_3x2"
    height = COMPACT_HEIGHT if compact else HEIGHT
    return {
        "preset_info": {
            "version": 14,
            "title": "AI Usage — One UI 3x2" if compact else "AI Usage — Midnight",
            "author": "AI Usage Monitor",
            "description": "Claude + Codex usage. Set server and token in Globals. Requires KWGT 3.80+. Tap to sync.",
            "width": WIDTH,
            "height": height,
            "features": "",
            "release": 380522706,
            "locked": False,
            "pflags": 0,
        },
        "preset_root": {
            "internal_type": "RootLayerModule",
            "background_color": "#00000000",
            "globals_list": globals_spec(variant),
            "internal_flows": [flow_spec()],
            "internal_events": [
                {"type": "SINGLE_TAP", "action": "TRIGGER_FLOW", "flow_id": FLOW_ID}
            ],
            "viewgroup_items": [
                {
                    "internal_type": "OverlapLayerModule",
                    "internal_title": "AI Usage card",
                    "config_scale_value": 100.0,
                    "internal_toggles": {"config_scale_value": 10},
                    "internal_formulas": {
                        "config_scale_value": kode(
                            f"mu(min, si(rwidth)/720, si(rheight)/{height})*100"
                        )
                    },
                    "viewgroup_items": compact_layout() if compact else layout(),
                }
            ],
        },
    }


def make_compat_preset(variant="oneui_3x2") -> dict:
    """R2 avoids whole-card render dependencies on launcher size and theme.

    This removes a suspected failure path, not proof of the phone's root cause.
    Retain R1 generation unchanged for regression comparison.
    """
    preset = make_preset(variant)
    preset["preset_info"]["title"] += " R2"
    preset["preset_info"]["id"] = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"ai-usage-monitor/{variant}/r2")
    )
    preset["preset_info"]["description"] = (
        "Rendering compatibility revision. Fixed scale and palette. Configure server/token AFTER confirming the card is visible."
    )
    root = preset["preset_root"]
    root["config_scale_value"] = 100.0
    card = root["viewgroup_items"][0]
    card["config_scale_value"] = 50.0
    card.pop("internal_toggles", None)
    card.pop("internal_formulas", None)
    # Color values remain editable in Globals, but have no system-theme formulas.
    for item in root["globals_list"].values():
        if item["type"] == "COLOR":
            item.pop("toggles", None)
            item.pop("global_formula", None)
    # Essential geometry, background and labels render even if formula evaluation
    # or global initialization fails. No clipping/visibility/opacity formulas.
    for module in card["viewgroup_items"]:
        if module["internal_title"] in (
            "Card background",
            "Title",
            "Measurement",
            "Subtitle",
            "Tap hint",
        ):
            color_key = module.get("internal_globals", {}).get("paint_color", "ink")
            module["paint_color"] = root["globals_list"][color_key]["value"]
            module.pop("internal_globals", None)
            module.pop("internal_toggles", None)
            module.pop("internal_formulas", None)
            # Let KWGT use its built-in default font for the always-visible title.
            module.pop("text_family", None)
    return preset


def make_positioned_preset(variant="oneui_3x2") -> dict:
    """R3: children of an overlap group use padding, not root-layer offsets.

    https://docs.kustom.rocks/tags/stack/ documents the positioning semantics.
    Keep the old revisions available solely for reproducing reported defects.
    """
    preset = make_compat_preset(variant)
    preset["preset_info"]["title"] = (
        preset["preset_info"]["title"].removesuffix(" R2") + " R3"
    )
    preset["preset_info"]["id"] = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"ai-usage-monitor/{variant}/r3")
    )
    preset["preset_info"]["description"] = (
        "Corrected overlap-group positioning. Fixed scale and palette. Set server/token in Globals after import."
    )
    for module in preset["preset_root"]["viewgroup_items"][0]["viewgroup_items"]:
        anchor = module["position_anchor"]
        assert anchor in ("TOPLEFT", "TOPRIGHT")
        side = "right" if anchor == "TOPRIGHT" else "left"
        module[f"position_padding_{side}"] = module.pop("position_offset_x")
        module["position_padding_top"] = module.pop("position_offset_y")
    return preset


def make_themed_preset(variant="oneui_3x2") -> dict:
    """R4 restores palette bindings without restoring broken group offsets."""
    preset = make_positioned_preset(variant)
    preset["preset_info"]["title"] = (
        preset["preset_info"]["title"].removesuffix(" R3") + " R4"
    )
    preset["preset_info"]["id"] = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"ai-usage-monitor/{variant}/r4")
    )
    preset["preset_info"]["description"] = (
        "Corrected layout with theme global: auto, dark, or light. Set server/token to fetch your backend; credentials are not included."
    )
    root = preset["preset_root"]
    root["internal_flows"] = [native_flow_spec()]
    original = make_preset(variant)
    globals_ = root["globals_list"]
    globals_["theme"] = {
        "index": len(globals_) + 1,
        "type": "TEXT",
        "title": "theme",
        "value": "auto",
        "description": "One UI palette: auto follows phone; dark or light forces a mode",
    }
    for key, item in original["preset_root"]["globals_list"].items():
        if item["type"] == "COLOR":
            globals_[key] = dict(item)
            if "global_formula" in item:
                globals_[key]["global_formula"] = item["global_formula"].replace(
                    "si(darkmode)",
                    '(gv(theme) = "dark" | (gv(theme) != "light" & si(darkmode)))',
                )
    originals = {
        m["internal_title"]: m
        for m in original["preset_root"]["viewgroup_items"][0]["viewgroup_items"]
    }
    for module in root["viewgroup_items"][0]["viewgroup_items"]:
        binding = (
            originals[module["internal_title"]]
            .get("internal_globals", {})
            .get("paint_color")
        )
        if binding:
            # R2 hardcoded the background/title colors; both must follow theme.
            module["paint_color"] = globals_[binding]["value"]
            module.setdefault("internal_globals", {})["paint_color"] = binding
            module.setdefault("internal_toggles", {})["paint_color"] = 100
    return preset


def squircle_outline():
    """100×100 path with n=4 superellipse corners and straight middle edges.

    KWGT's native SQUIRCLE is symmetric (square-only). PATH permits independent
    width/height. Sampled corner segments also drive the reference preview.
    """
    points = []
    for cx, cy, start in ((92, 12, -90), (92, 88, 0), (8, 88, 90), (8, 12, 180)):
        for i in range(97):
            angle = math.radians(start + i * 90 / 96)
            cos, sin = math.cos(angle), math.sin(angle)
            x = cx + 8 * math.copysign(math.sqrt(abs(cos)), cos)
            y = cy + 12 * math.copysign(math.sqrt(abs(sin)), sin)
            points.append((round(x, 5), round(y, 5)))
    return points


def make_fullspace_preset(variant="oneui_3x2") -> dict:
    """R5 sizes geometry to root dimensions; no aspect-fit group scaling."""
    preset = make_themed_preset(variant)
    preset["preset_info"]["title"] = (
        preset["preset_info"]["title"].removesuffix(" R4") + " R5"
    )
    preset["preset_info"]["id"] = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"ai-usage-monitor/{variant}/r5")
    )
    preset["preset_info"]["description"] = (
        "Full-space responsive squircle card. Resize home-screen widget to 3x2 before loading. Set theme, server and token in Globals."
    )
    return fit_to_viewport(preset)


def fit_to_viewport(preset):
    """Apply responsive geometry to an already positioned card."""
    root = preset["preset_root"]
    height = preset["preset_info"]["height"]
    globals_ = root["globals_list"]
    for name, expression in (
        ("vw", "if(si(rwidth) > 0, si(rwidth), 720)"),
        ("vh", f"if(si(rheight) > 0, si(rheight), {height})"),
        ("unit", f"mu(min, gv(vw)/720, gv(vh)/{height})"),
    ):
        globals_[name] = {
            "index": len(globals_) + 1,
            "type": "TEXT",
            "title": name,
            "value": kode(expression),
            "description": "Internal responsive geometry; do not edit",
        }
    card = root["viewgroup_items"][0]
    card["config_scale_value"] = 100.0
    for module in card["viewgroup_items"]:
        for key in list(module):
            factor = None
            if key in (
                "position_padding_left",
                "position_padding_right",
                "shape_width",
            ):
                factor = "gv(vw)/720"
            elif key in (
                "position_padding_top",
                "position_padding_bottom",
                "shape_height",
            ):
                factor = f"gv(vh)/{height}"
            elif key in ("text_size", "shape_corners", "bitmap_width", "bitmap_height"):
                factor = "gv(unit)"
            if factor is not None:
                previous = module.get("internal_formulas", {}).get(key)
                expression = previous[1:-1] if previous else str(module[key])
                module.setdefault("internal_toggles", {})[key] = 10
                module.setdefault("internal_formulas", {})[key] = kode(
                    f"({expression}) * ({factor})"
                )
    background = card["viewgroup_items"][0]
    background["shape_type"] = "PATH"
    background["shape_path_scale"] = "AUTO"
    points = squircle_outline()
    background["shape_path"] = (
        "M " + " L ".join(f"{x:g} {y:g}" for x, y in points) + " Z"
    )
    # Custom path already defines corner geometry; rounded-corner processing
    # must not alter it or turn the squircle outline into a rounded rectangle.
    background["shape_corners"] = 0.0
    background["internal_toggles"].pop("shape_corners", None)
    background["internal_formulas"].pop("shape_corners", None)
    return preset


def make_reference_preset(variant="oneui_3x2", *, fit=True) -> dict:
    """R6: the user's Korean stacked-row reference, with truthful live quotas."""
    if variant != "oneui_3x2":
        raise ValueError("The reference style is a 3x2 preset")
    preset = make_themed_preset(variant)
    info = preset["preset_info"]
    info.update(
        title="AI 사용량 — Reference 3x2 R6",
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-usage-monitor/reference/r6")),
        description="Dark Korean reference style. Claude session / Codex weekly. Full-space card; set server and token in root Globals.",
    )
    root = preset["preset_root"]
    globals_ = root["globals_list"]
    globals_.pop("theme", None)
    palette = {
        "bg": "#FF0F1318",
        "ink": "#FFF5F5F5",
        "muted": "#FF8D929A",
        "track": "#FF242A32",
        "claude": "#FFFA956F",
        "codex": "#FF30CE6C",
        "panel": "#FF1B2128",
    }
    for key, value in palette.items():
        globals_[key].update(value=value)
        globals_[key].pop("toggles", None)
        globals_[key].pop("global_formula", None)
    # No misleading "updated now" before a successful fetch.
    state = kode(
        'if(gv(state) = "SETUP", "설정 필요", gv(state) = "SYNCING", "동기화 중", '
        'gv(state) = "OFFLINE", "연결 오류", gv(state) = "TAP TO SYNC", "탭하여 새로고침", '
        'gv(state) = "STALE", "지연된 데이터", "업데이트 " + '
        'mu(max, 0, mu(floor, (df(S) - gv(checked))/60)) + "분 전")'
    )
    items = [
        rect("Card background", 0, 0, 720, 480, "bg"),
        text("Title", "AI 사용량", 52, 44, 38, bold=True),
        text("Connection and fetch age", state, 668, 49, 25, "muted", right=True),
    ]
    for provider, name, key, accent, top, label, reset_key, icon in (
        (
            "claude",
            "Claude",
            "cs",
            "claude",
            144,
            "이번 세션",
            "session_reset_in",
            "claude.png",
        ),
        (
            "openai",
            "Codex",
            "gw",
            "codex",
            330,
            "이번 주",
            "weekly_reset_in",
            "openai.png",
        ),
    ):
        reset = jp(f"{provider}.{reset_key}")
        bad = f'gv(hasdata) = 1 & ({jp(provider + ".status")} != "ok" | {jp(provider + ".stale")} = "true" | {jp(provider + ".stale")} = 1 | df(S) - gv(checked) > 1800)'
        items += [
            {
                "internal_type": "BitmapModule",
                "internal_title": f"{name} logo",
                "bitmap_bitmap": f"kfile://org.kustom.provider/bitmaps/{icon}",
                "bitmap_width": 78.0,
                "bitmap_height": 78.0,
                "position_anchor": "TOPLEFT",
                "position_offset_x": 58.0,
                "position_offset_y": float(top + 10),
            },
            text(
                f"{name} title",
                kode(f'if({bad}, "{name} · 지연", "{name}")'),
                174,
                top,
                39,
                bold=True,
            ),
            text(
                f"{name} percent",
                kode(f'if(gv({key}) < 0, "—", gv({key}) + "%")'),
                668,
                top - 7,
                50,
                bold=True,
                right=True,
            ),
            text(
                f"{name} reset",
                kode(
                    f'if(gv({key}) < 0, "{label} · 데이터 없음", '
                    f'{reset} = "" | {reset} = "null", "{label} · 초기화 정보 없음", '
                    f'"{label} · " + tc(ell, {reset}, 12) + " 후 초기화")'
                ),
                174,
                top + 63,
                27,
                "muted",
            ),
            rect(f"{name} track", 174, top + 111, 494, 18, "track", 9),
        ]
        bar = rect(
            f"{name} used bar",
            174,
            top + 111,
            1,
            18,
            kode(f'if(gv({key}) > 0, gv({accent}), "#00000000")'),
            9,
        )
        bar.setdefault("internal_toggles", {})["shape_width"] = 10
        bar.setdefault("internal_formulas", {})["shape_width"] = kode(
            f"mu(max, 1, mu(min, 100, gv({key})) * 4.94)"
        )
        items.append(bar)
    items.insert(9, rect("Row divider", 52, 301, 616, 1, "panel"))
    for module in items:
        side = "right" if module["position_anchor"] == "TOPRIGHT" else "left"
        module[f"position_padding_{side}"] = module.pop("position_offset_x")
        module["position_padding_top"] = module.pop("position_offset_y")
        if module["internal_type"] == "TextModule":
            style = "Medium" if "Medium" in module.get("text_family", "") else "Regular"
            module["text_family"] = (
                f"kfile://org.kustom.provider/fonts/NotoSansKR-{style}.ttf"
            )
    root["viewgroup_items"][0]["viewgroup_items"] = items
    return fit_to_viewport(preset) if fit else preset


def make_panel_preset(variant="oneui_3x2") -> dict:
    """R7: compact row internals, secondary quotas, resource-free vector marks."""
    preset = make_reference_preset(variant, fit=False)
    preset["preset_info"].update(
        title="AI 사용량 — Quota Panel 3x2 R7",
        height=620,
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-usage-monitor/quota-panel/r7")),
        description="Compact provider rows, native vector logos, and secondary quota panel. Set server/token; use root/card scale 100.",
    )
    root = preset["preset_root"]
    items = root["viewgroup_items"][0]["viewgroup_items"]
    marks = json.loads((HERE / "assets" / "provider_paths.json").read_text())
    items[0]["shape_height"] = 620.0
    row_positions = {}
    for module in items:
        title = module["internal_title"]
        for name, top, extra_share in (("Claude", 132, 0.2), ("Codex", 292, 0.6)):
            if title.startswith(name + " "):
                suffix = title[len(name) + 1 :]
                offset = {
                    "logo": 10,
                    "title": 0,
                    "percent": -7,
                    "reset": 50,
                    "track": 94,
                    "used bar": 94,
                }[suffix]
                module["position_padding_top"] = float(top + offset)
                row_positions[title] = (top + offset, extra_share)
                if suffix == "logo":
                    # Use original library vector contours, not PNG loading or
                    # a drawn approximation. Kustom Shape PATH is self-contained.
                    for key in list(module):
                        if key.startswith("bitmap_"):
                            module.pop(key)
                    module.update(
                        internal_type="ShapeModule",
                        shape_type="PATH",
                        shape_path_scale="AUTO",
                        shape_path=marks["claude" if name == "Claude" else "openai"],
                        shape_width=78.0,
                        shape_height=78.0,
                        shape_corners=0.0,
                        paint_color="#FFD97757" if name == "Claude" else "#FFF5F5F5",
                    )
        if title == "Row divider":
            module["position_padding_top"] = 264.0
            row_positions[title] = (264, 0.4)
    panel_offsets = {}
    panel_items = [
        rect("Secondary quota panel", 40, 450, 640, 142, "panel", 32),
        rect("Panel divider", 360, 474, 1, 94, "track"),
    ]
    panel_offsets.update({"Secondary quota panel": 0, "Panel divider": 24})
    for name, key, provider, window, reset_field, left, accent in (
        ("Claude", "cw", "claude", "주간", "weekly_reset_in", 68, "claude"),
        ("Codex", "g5", "openai", "5시간", "five_hour_reset_in", 392, "codex"),
    ):
        reset = jp(f"{provider}.{reset_field}")
        prefix = f"Panel {name}"
        panel_items += [
            text(prefix + " label", f"{name} · {window}", left, 472, 22, "muted"),
            text(
                prefix + " percent",
                kode(f'if(gv({key}) < 0, "—", gv({key}) + "%")'),
                left + 260,
                466,
                32,
                bold=True,
                right=True,
            ),
            text(
                prefix + " reset",
                kode(
                    f'if(gv({key}) < 0, "데이터 없음", {reset} = "" | {reset} = "null", '
                    f'"초기화 정보 없음", tc(ell, {reset}, 12) + " 후 초기화")'
                ),
                left,
                514,
                18,
                "muted",
            ),
            rect(prefix + " track", left, 554, 260, 6, "track", 3),
        ]
        bar = rect(
            prefix + " used bar",
            left,
            554,
            1,
            6,
            kode(f'if(gv({key}) > 0, gv({accent}), "#00000000")'),
            3,
        )
        bar.setdefault("internal_toggles", {})["shape_width"] = 10
        bar.setdefault("internal_formulas", {})["shape_width"] = kode(
            f"mu(max, 1, mu(min, 100, gv({key})) * 2.6)"
        )
        panel_items.append(bar)
        panel_offsets.update(
            {
                prefix + " label": 22,
                prefix + " percent": 16,
                prefix + " reset": 64,
                prefix + " track": 104,
                prefix + " used bar": 104,
            }
        )
    for module in panel_items:
        side = "right" if module["position_anchor"] == "TOPRIGHT" else "left"
        module[f"position_padding_{side}"] = module.pop("position_offset_x")
        module["position_padding_top"] = module.pop("position_offset_y")
        if module["internal_type"] == "TextModule":
            style = "Medium" if "Medium" in module.get("text_family", "") else "Regular"
            module["text_family"] = (
                f"kfile://org.kustom.provider/fonts/NotoSansKR-{style}.ttf"
            )
    items.extend(panel_items)
    fit_to_viewport(preset)
    for module in items:
        title = module["internal_title"]
        # Internal row gaps scale with text, never independently with height.
        # Spare height is distributed BETWEEN rows and the bottom panel.
        top = module["position_padding_top"]
        expression = f"{top} * gv(unit)"
        if title in row_positions:
            y, share = row_positions[title]
            expression = (
                f"{y} * gv(unit) + mu(max, 0, gv(vh) - 620 * gv(unit)) * {share}"
            )
        if title in panel_offsets:
            expression = f"gv(vh) - (170 - {panel_offsets[title]}) * gv(unit)"
        module["internal_formulas"]["position_padding_top"] = kode(expression)
        if "shape_height" in module and (
            title in panel_offsets
            or title == "Row divider"
            or title.endswith((" track", "used bar"))
        ):
            module["internal_formulas"]["shape_height"] = kode(
                f"{module['shape_height']} * gv(unit)"
            )
        if title.endswith(" logo"):
            for key in ("shape_width", "shape_height"):
                module["internal_formulas"][key] = kode("78 * gv(unit)")
    return preset


def native_flow_spec() -> dict:
    """Native RenderFlowParamValue is an inline String, including booleans.

    Verified against the official 3.82 APK's RenderFlowParamValue serializer,
    whose decoder reads String and whose encoder writes String. JSON booleans
    in earlier files do not conform to that serialized parameter contract.
    """
    flow = flow_spec()
    for task in flow["a"] + flow["t"]:
        for key, value in task.get("params", {}).items():
            if isinstance(value, bool):
                task["params"][key] = "true" if value else "false"
            elif not isinstance(value, str):
                raise TypeError(f"Flow parameter {key} must serialize as text")
    return flow


def overlap_position(module, width):
    """Anchor point for our top-anchored children, in unscaled group units.

    Intentionally ignore position_offset_*: those fields do not position
    overlap children. This is a limited reference renderer, not KWGT itself.
    """
    anchor = module["position_anchor"]
    if anchor == "TOPLEFT":
        x = module.get("position_padding_left", 0)
    elif anchor == "TOPRIGHT":
        x = width - module.get("position_padding_right", 0)
    else:
        raise ValueError(f"Unsupported reference-renderer anchor: {anchor}")
    return x, module.get("position_padding_top", 0)


def make_render_test() -> dict:
    """Minimal import probe: no globals, formulas, flows, files, or network."""
    return {
        "preset_info": {
            "version": 14,
            "title": "AI Widget Render Test R2",
            "author": "AI Usage Monitor",
            "description": "Static diagnostic only. No live usage or setup required.",
            "id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, "ai-usage-monitor/render-test/r2")
            ),
            "width": 360,
            "height": 240,
            "release": 380522706,
            "features": "",
            "pflags": 0,
            "locked": False,
        },
        "preset_root": {
            "internal_type": "RootLayerModule",
            "config_scale_value": 100.0,
            "internal_events": [{"action": "KUSTOM_ACTION"}],
            "viewgroup_items": [
                {
                    "internal_type": "ShapeModule",
                    "shape_type": "RECT",
                    "shape_width": 360.0,
                    "shape_height": 240.0,
                    "shape_corners": 28.0,
                    "paint_color": "#FFE5EFFF",
                },
                {
                    "internal_type": "TextModule",
                    "text_expression": "Widget render OK",
                    "text_size": 28.0,
                    "paint_color": "#FF15345B",
                    "position_offset_y": -24.0,
                },
                {
                    "internal_type": "TextModule",
                    "text_expression": "No server needed",
                    "text_size": 18.0,
                    "paint_color": "#FF15345B",
                    "position_offset_y": 26.0,
                },
            ],
        },
    }


def build(output: Path | None = None, variant="midnight", revision=1) -> Path:
    factories = {
        1: make_preset,
        2: make_compat_preset,
        3: make_positioned_preset,
        4: make_themed_preset,
        5: make_fullspace_preset,
        6: make_reference_preset,
        7: make_panel_preset,
    }
    preset = (
        make_render_test() if variant == "render_test" else factories[revision](variant)
    )
    if output is None:
        stem = {
            "oneui_3x2": "AI_Usage_OneUI_3x2",
            "midnight": "AI_Usage_Midnight",
            "render_test": "AI_Widget_Render_Test",
        }[variant]
        output = HERE / (stem + (f"_R{revision}" if revision > 1 else "") + ".kwgt")
    prefix = "oneui_3x2_" if variant == "oneui_3x2" else ""
    if revision >= 3:
        prefix += f"r{revision}_"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # Stable metadata makes repeat builds byte-for-byte reproducible.
        entries = {
            "preset.json": json.dumps(preset, ensure_ascii=False, indent=2).encode()
        }
        if revision in (6, 7):
            if revision == 6:
                for name in ("claude.png", "openai.png"):
                    entries[f"bitmaps/{name}"] = (HERE / "assets" / name).read_bytes()
            for name in ("NotoSansKR-Regular.ttf", "NotoSansKR-Medium.ttf"):
                entries[f"fonts/{name}"] = (HERE / "assets" / name).read_bytes()
            for name in ("ATTRIBUTION.md", "OFL.txt"):
                entries[f"licenses/{name}"] = (HERE / "assets" / name).read_bytes()
        for name in ("preset_thumb_portrait.jpg", "preset_thumb_landscape.jpg"):
            if variant != "render_test" and (HERE / (prefix + name)).is_file():
                entries[name] = (HERE / (prefix + name)).read_bytes()
        for name, data in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 10, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant",
        choices=("midnight", "oneui_3x2", "render_test"),
        default="oneui_3x2",
    )
    parser.add_argument("--revision", type=int, choices=(1, 2, 3, 4, 5, 6, 7))
    args = parser.parse_args()
    print(
        build(
            variant=args.variant,
            revision=args.revision
            or (
                2
                if args.variant == "render_test"
                else 7
                if args.variant == "oneui_3x2"
                else 5
            ),
        )
    )
