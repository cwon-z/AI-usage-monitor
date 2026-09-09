"""Render a reference image from the same modules/formulas as the preset.

Not a screenshot of Android. Pillow is an optional build-time dependency only.
Run with --font-dir pointing to a directory containing Roboto-Regular.ttf and
Roboto-Medium.ttf, or --reference-apk pointing to an official KWGT download.
The APK is only read for its built-in fonts; no app code is executed or shipped.
"""

import argparse
import io
import json
import re
import zipfile
from pathlib import Path

from build_widget import (
    HERE,
    build,
    make_compat_preset,
    make_fullspace_preset,
    make_panel_preset,
    make_positioned_preset,
    make_preset,
    make_reference_preset,
    make_themed_preset,
    overlap_position,
)
from check_kode import Context
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant", choices=("midnight", "oneui_3x2"), default="oneui_3x2"
    )
    parser.add_argument("--revision", type=int, choices=(1, 2, 3, 4, 5, 6, 7))
    parser.add_argument(
        "--width", type=int, help="Root viewport width for responsive-layout QA"
    )
    parser.add_argument(
        "--height", type=int, help="Root viewport height for responsive-layout QA"
    )
    fonts = parser.add_mutually_exclusive_group()
    fonts.add_argument("--font-dir", type=Path)
    fonts.add_argument("--reference-apk", type=Path)
    args = parser.parse_args()
    args.revision = args.revision or (7 if args.variant == "oneui_3x2" else 5)
    if args.revision < 6 and not (args.font_dir or args.reference_apk):
        parser.error("Legacy revisions require --font-dir or --reference-apk")
    preset = {
        1: make_preset,
        2: make_compat_preset,
        3: make_positioned_preset,
        4: make_themed_preset,
        5: make_fullspace_preset,
        6: make_reference_preset,
        7: make_panel_preset,
    }[args.revision](args.variant)
    WIDTH = args.width or preset["preset_info"]["width"]
    HEIGHT = args.height or preset["preset_info"]["height"]
    spec = preset["preset_root"]["globals_list"]
    modules = preset["preset_root"]["viewgroup_items"][0]["viewgroup_items"]
    prefix = "oneui_3x2_" if args.variant == "oneui_3x2" else ""
    if args.revision > 1:
        prefix += f"r{args.revision}_"
    font_data = {}
    for style in ("Regular", "Medium"):
        name = f"Roboto-{style}.ttf"
        if args.revision >= 6:
            font_data[style] = (
                HERE / "assets" / f"NotoSansKR-{style}.ttf"
            ).read_bytes()
        elif args.reference_apk:
            with zipfile.ZipFile(args.reference_apk) as apk:
                font_data[style] = apk.read(f"assets/fonts/{name}")
        else:
            font_data[style] = (args.font_dir / name).read_bytes()

    sample = {
        "claude": {
            "status": "ok",
            "stale": False,
            "session": 37,
            "weekly": 61,
            "session_reset_in": "2h 11m",
            "weekly_reset_in": "3d 7h",
            "collected_at": "2026-09-10T00:00:00Z",
        },
        "openai": {
            "status": "ok",
            "stale": False,
            "five_hour": 18,
            "weekly": 26,
            "five_hour_reset_in": "4h 13m",
            "weekly_reset_in": "5d 2h",
            "collected_at": "2026-09-10T00:00:00Z",
        },
        "stale": False,
    }
    timestamp = 1788998460
    if args.revision >= 6:
        sample["claude"].update(session=100, session_reset_in="4h 45m")
        sample["openai"].update(weekly=23)
    server = "https://homelab.example.ts.net"
    cases = {
        "preview": {
            "server": server,
            "token": "example-widget-token-not-real",
            "bound": server,
            "checked": str(timestamp - 60),
            "attempt": str(timestamp - 61),
            "data": json.dumps(sample),
        },
        "setup": {},
    }
    if args.variant == "oneui_3x2" and args.revision in (1, 4, 5):
        cases["dark"] = cases["preview"]
    for filename, values in cases.items():
        ctx = Context(
            spec,
            values,
            now=timestamp,
            system={
                "rwidth": WIDTH,
                "rheight": HEIGHT,
                "darkmode": int(filename == "dark"),
            },
        )
        scale = 2
        canvas = Image.new("RGBA", (WIDTH * scale, HEIGHT * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        def color(module, ctx=ctx):
            raw = module.get("paint_color", "#FFFFFFFF")
            if "paint_color" in module.get("internal_globals", {}):
                raw = ctx.global_value(module["internal_globals"]["paint_color"])
            if "paint_color" in module.get("internal_formulas", {}):
                raw = ctx.render(module["internal_formulas"]["paint_color"])
            return tuple(int(raw[i : i + 2], 16) for i in (3, 5, 7, 1))

        for module in modules:
            module = dict(module)
            for key, formula in module.get("internal_formulas", {}).items():
                if key.startswith("position_padding_") or key in (
                    "shape_width",
                    "shape_height",
                    "shape_corners",
                    "text_size",
                    "bitmap_width",
                    "bitmap_height",
                ):
                    module[key] = float(ctx.render(formula))
            x, y = overlap_position(module, WIDTH)
            fill = color(module)
            if module["internal_type"] == "ShapeModule":
                width = module["shape_width"]
                if "shape_width" in module.get("internal_formulas", {}):
                    width = float(
                        ctx.render(module["internal_formulas"]["shape_width"])
                    )
                height = module["shape_height"]
                if fill[3]:
                    if module["shape_type"] == "PATH":
                        # This preset uses only M/L/Z path commands, not a
                        # rounded-rectangle approximation of the custom shape.
                        path = module["shape_path"]
                        if re.sub(r"[MLZ0-9.\s-]", "", path):
                            from render_vectors import render_path

                            vector = render_path(
                                path, round(width * scale), round(height * scale), fill
                            )
                            canvas.alpha_composite(
                                vector, (round(x * scale), round(y * scale))
                            )
                        else:
                            numbers = [
                                float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", path)
                            ]
                            points = [
                                (
                                    (x + px * width / 100) * scale,
                                    (y + py * height / 100) * scale,
                                )
                                for px, py in zip(
                                    numbers[::2], numbers[1::2], strict=True
                                )
                            ]
                            draw.polygon(points, fill=fill)
                    else:
                        draw.rounded_rectangle(
                            (
                                x * scale,
                                y * scale,
                                (x + width) * scale,
                                (y + height) * scale,
                            ),
                            radius=min(module["shape_corners"], width / 2, height / 2)
                            * scale,
                            fill=fill,
                        )
            elif module["internal_type"] == "BitmapModule":
                asset = module["bitmap_bitmap"].rsplit("/", 1)[1]
                logo = Image.open(HERE / "assets" / asset).convert("RGBA")
                logo = logo.resize(
                    (
                        round(module["bitmap_width"] * scale),
                        round(module["bitmap_height"] * scale),
                    ),
                    Image.Resampling.LANCZOS,
                )
                canvas.alpha_composite(logo, (round(x * scale), round(y * scale)))
            else:
                label = ctx.render(module["text_expression"])
                style = (
                    "Medium"
                    if "Medium.ttf" in module.get("text_family", "")
                    else "Regular"
                )
                font = ImageFont.truetype(
                    io.BytesIO(font_data[style]), round(module["text_size"] * scale)
                )
                if module["position_anchor"] == "TOPRIGHT":
                    x -= draw.textlength(label, font=font) / scale
                if x < 0 or x + draw.textlength(label, font=font) / scale > WIDTH:
                    raise ValueError(f"Text exceeds card: {module['internal_title']}")
                if (
                    y < 0
                    or y + (font.getbbox(label)[3] - font.getbbox(label)[1]) / scale
                    > HEIGHT
                ):
                    raise ValueError(
                        f"Text exceeds card height: {module['internal_title']}"
                    )
                draw.text(
                    (x * scale, y * scale), label, font=font, fill=fill, anchor="lt"
                )
        viewport_suffix = f"_{WIDTH}x{HEIGHT}" if args.width or args.height else ""
        canvas.save(HERE / f"{prefix}{filename}{viewport_suffix}.png")
        if filename == "preview" and not viewport_suffix:
            # Library art clearly states that its numbers are illustrative.
            thumbnail = Image.new(
                "RGB", (WIDTH, HEIGHT + 38), "#" + ctx.global_value("bg")[3:]
            )
            thumbnail.paste(
                canvas.resize((WIDTH, HEIGHT)), (0, 0), canvas.resize((WIDTH, HEIGHT))
            )
            label_font = ImageFont.truetype(io.BytesIO(font_data["Regular"]), 14)
            ImageDraw.Draw(thumbnail).text(
                (36, HEIGHT + 8),
                "DESIGN PREVIEW · EXAMPLE DATA",
                font=label_font,
                fill="#A4AFBC",
            )
            for orientation in ("portrait", "landscape"):
                thumbnail.save(
                    HERE / f"{prefix}preset_thumb_{orientation}.jpg", quality=92
                )
    print(build(variant=args.variant, revision=args.revision))


if __name__ == "__main__":
    main()
