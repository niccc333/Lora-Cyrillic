#!/usr/bin/env python3
"""Render before/after overlays: original Lora in red, Leaopra in black.

Usage: python scripts/proof_overlay.py ORIG.glyphs NEW.glyphs OUT.png [glyph ...]
With no glyph names, every glyph whose outlines changed is rendered.
"""
import sys

import pathops
from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from glyphsLib import GSFont
from PIL import Image, ImageDraw


def draw_layer(font, layer, pen):
    for p in layer.paths:
        nodes = list(p.nodes)
        k = max(i for i, n in enumerate(nodes) if n.type != "offcurve")
        nodes = nodes[k + 1:] + nodes[:k + 1]
        start = nodes[-1].position
        pen.moveTo((start.x, start.y))
        offs = []
        for n in nodes:
            pt = (n.position.x, n.position.y)
            if n.type == "offcurve":
                offs.append(pt)
            elif n.type == "curve":
                pen.curveTo(*offs, pt)
                offs = []
            else:
                pen.lineTo(pt)
                offs = []
        pen.closePath()
    for c in layer.components:
        base = font.glyphs[c.name]
        if base is None:
            continue
        bl = base.layers[layer.associatedMasterId]
        draw_layer(font, bl, TransformPen(pen, tuple(c.transform)))


class Flatten(BasePen):
    def __init__(self):
        super().__init__(None)
        self.polys, self.cur = [], []

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, a, b, c):
        p0 = self.cur[-1]
        for i in range(1, 13):
            t = i / 12
            mt = 1 - t
            self.cur.append(tuple(mt**3 * p0[j] + 3 * mt * mt * t * a[j] + 3 * mt * t * t * b[j] + t**3 * c[j]
                                  for j in range(2)))

    def _closePath(self):
        self.polys.append(self.cur)
        self.cur = []


def polys(font, glyph, master_id):
    path = pathops.Path()
    draw_layer(font, glyph.layers[master_id], path.getPen())
    try:
        path = pathops.simplify(path)
    except Exception:
        pass
    fp = Flatten()
    path.draw(fp)
    return fp.polys


def fill(img, ps, scale, ox, oy, color):
    mask = Image.new("1", img.size, 0)
    d = ImageDraw.Draw(mask)
    for poly in ps:
        if len(poly) < 3:
            continue
        pts = [(ox + x * scale, oy - y * scale) for x, y in poly]
        tmp = Image.new("1", img.size, 0)
        ImageDraw.Draw(tmp).polygon(pts, fill=1)
        mask = Image.frombytes("1", img.size, bytes(a ^ b for a, b in zip(mask.tobytes(), tmp.tobytes())))
    img.paste(Image.new("RGB", img.size, color), (0, 0), mask.convert("L"))


def changed(fa, fb, ga, gb):
    for m in fa.masters:
        la, lb = ga.layers[m.id], gb.layers[m.id]
        if [[(n.position.x, n.position.y) for n in p.nodes] for p in la.paths] != \
           [[(n.position.x, n.position.y) for n in p.nodes] for p in lb.paths]:
            return True
    return False


def main():
    orig, new, out = sys.argv[1:4]
    names = sys.argv[4:]
    fa, fb = GSFont(orig), GSFont(new)
    if not names:
        names = [g.name for g in fb.glyphs if fa.glyphs[g.name] and changed(fa, fb, fa.glyphs[g.name], g)]
    import os
    a = int(os.environ.get("START", 0)); names = names[a:a + int(os.environ.get("COUNT", 10**6))]
    masters = [m.id for m in fa.masters]
    import os
    zoom = float(os.environ.get("ZOOM", 1))
    cell, scale = int(150 * zoom), 0.12 * zoom
    cols = int(12 / zoom)
    rows = (len(names) + cols - 1) // cols
    img = Image.new("RGB", (cols * cell, rows * len(masters) * cell + 10), "white")
    d = ImageDraw.Draw(img)
    for i, name in enumerate(names):
        c, r = i % cols, i // cols
        for mi, mid in enumerate(masters):
            ox = c * cell + 15
            oy = (r * len(masters) + mi) * cell + int(115 * zoom)
            fill(img, polys(fa, fa.glyphs[name], mid), scale, ox, oy, (255, 120, 120))
            fill(img, polys(fb, fb.glyphs[name], mid), scale, ox, oy, (0, 0, 0))
            d.line([(ox - 5, oy), (ox + cell - 25, oy)], fill=(80, 160, 255))
        d.text((c * cell + 3, r * len(masters) * cell + 2), name[:22], fill=(0, 0, 200))
    img.save(out)
    print(f"{len(names)} glyphs -> {out}")


if __name__ == "__main__":
    main()
