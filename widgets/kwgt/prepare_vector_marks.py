"""Convert licensed source SVG marks to Kustom's 100×100 Shape Path viewport.

Run in a build-only environment with fonttools installed. Original logo curves
are transformed, not traced or approximated; no fonts or image files are needed
by the logo modules at runtime. Sources/license are in assets/ATTRIBUTION.md.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path

ASSETS = Path(__file__).resolve().parent / "assets"


def main():
    marks = {}
    for name in ("claude", "openai"):
        svg = ET.fromstring((ASSETS / f"{name}.svg").read_text())
        x, y, width, height = map(float, svg.attrib["viewBox"].split())
        pen = SVGPathPen(None)
        transform = TransformPen(
            pen, (100 / width, 0, 0, 100 / height, -x * 100 / width, -y * 100 / height)
        )
        paths = list(svg.iter("{http://www.w3.org/2000/svg}path"))
        if not paths:
            raise ValueError(f"No source paths in {name}")
        for path in paths:
            if "transform" in path.attrib:
                raise ValueError("Source transformed paths require explicit handling")
            parse_path(path.attrib["d"], transform)
        marks[name] = pen.getCommands()
    (ASSETS / "provider_paths.json").write_text(json.dumps(marks, indent=2) + "\n")


if __name__ == "__main__":
    main()
