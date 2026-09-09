"""Reference-render the exact exported SVG contours, including curved holes.

Optional preview dependencies only: fonttools, aggdraw, Pillow. Android uses
Kustom's native Shape PATH renderer instead, so this is not device verification.
"""

import aggdraw
from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from PIL import Image


class RasterPen(BasePen):
    def __init__(self):
        super().__init__(None)
        self.path = aggdraw.Path()

    def _moveTo(self, p):
        self.path.moveto(*p)

    def _lineTo(self, p):
        self.path.lineto(*p)

    def _curveToOne(self, p1, p2, p3):
        self.path.curveto(*p1, *p2, *p3)

    def _closePath(self):
        self.path.close()

    def _endPath(self):
        pass


def render_path(path, width, height, color):
    image = Image.new("RGBA", (width, height))
    pen = RasterPen()
    parse_path(path, TransformPen(pen, (width / 100, 0, 0, height / 100, 0, 0)))
    draw = aggdraw.Draw(image)
    draw.path(pen.path, aggdraw.Brush(color))
    draw.flush()
    return image
