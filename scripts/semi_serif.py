#!/usr/bin/env python3
"""Turn Lora into Leaopra: a semi-serif derived from Lora.

Rule applied to every glyph, in every master:

  * Serifs attached to a stroke end are removed and the stroke is squared off
    along its own direction (vertical stems end flat, diagonals end flat on the
    baseline / cap height, arms of E T L Z ... end with a straight cut).
  * The angled, left-pointing entry flags at the top of lowercase stems
    (n m h i l b d k p r u j ...) are kept: they carry the pen-written,
    humanist character of the design.
  * Everything else (contrast, curves, terminals, bowls, spacing) is untouched.

Detection is purely geometric and is run on each master independently; an
edit is only applied when the same structure is found in all masters of a
glyph, so interpolation compatibility is preserved.

Usage: python scripts/semi_serif.py [--report]
"""
import math
import sys
import unicodedata
from collections import defaultdict

import glyphsLib
from glyphsLib import GSFont, GSNode
from glyphsLib.types import Point

SOURCES = [
    ("sources/Lora.glyphs", "sources/Leaopra.glyphs"),
    ("sources/Lora-Italic.glyphs", "sources/Leaopra-Italic.glyphs"),
]
FAMILY = "Leaopra"

# --- tuning -----------------------------------------------------------------
EDGE_MIN = 4          # min length of a terminal edge
EDGE_TILT = 0.04      # max tilt of a terminal edge (perp/par)
STEM_LINE_MIN = 40    # a line counts as a stem if it runs this far away
STEM_CURVE_MIN = 150  # a straight-ish curve counts as a stem if this long
STEM_SLOPE = 0.9      # max sideways/forward ratio of a stem
MAX_FLANK = 5         # max segments between terminal edge and stem
BAND = {"H": 240, "V": 260}   # flank must stay this close to the terminal
EXCURSION = 12        # serif must stick out this far past the stem
FLANK_LINE_MAX = 100  # serifs are built from short pieces; a long line is a stroke
EXCURSION_MAX = {"H": 130, "V": 200}  # ...but no further (crossbars, arms)
FLAG_DROP = 12        # a top-left flank whose tip sits this far below the
                      # top is a lowercase entry flag -> kept


# --- contour <-> segments -----------------------------------------------------
def to_segments(path):
    nodes = list(path.nodes)
    # rotate so the list ends on an on-curve node
    k = max(i for i, n in enumerate(nodes) if n.type != "offcurve")
    nodes = nodes[k + 1:] + nodes[:k + 1]
    segs, offs = [], []
    for n in nodes:
        p = (float(n.position.x), float(n.position.y))
        if n.type == "offcurve":
            offs.append(p)
        else:
            segs.append({"type": n.type, "offs": offs, "end": p, "smooth": n.smooth})
            offs = []
    return segs


def from_segments(segs):
    out = []
    for s in segs:
        for o in s["offs"]:
            out.append(GSNode(Point(round(o[0]), round(o[1])), "offcurve"))
        e = s["end"]
        out.append(GSNode(Point(round(e[0]), round(e[1])), s["type"], s["smooth"]))
    return out


# --- frames: H detects horizontal terminals, V vertical ones (beaks) -----------
def rot(p, frame):
    return p if frame == "H" else (-p[1], p[0])


def unrot(p, frame):
    return p if frame == "H" else (p[1], -p[0])


def frame_segs(segs, frame):
    return [{**s, "offs": [rot(o, frame) for o in s["offs"]], "end": rot(s["end"], frame)}
            for s in segs]


# --- geometry -------------------------------------------------------------------
def start(segs, i):
    return segs[(i - 1) % len(segs)]["end"]


def intersect(p1, d1, p2, d2):
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def seg_points(segs, i):
    return [start(segs, i)] + segs[i]["offs"] + [segs[i]["end"]]


def stem_check(segs, i, P, sign):
    """Is segment i (traversed starting from P) a stem leaving the terminal?
    sign=+1: stem must go up (bottom terminal), -1: down (top terminal)."""
    pts = seg_points(segs, i)
    far = pts[-1] if pts[0] == P else pts[0]
    dx, dy = far[0] - P[0], (far[1] - P[1]) * sign
    if dy <= 0 or abs(dx) > STEM_SLOPE * dy:
        return False
    length = math.hypot(dx, dy)
    if segs[i]["type"] == "line":
        return dy >= STEM_LINE_MIN
    # curve: must be long and nearly straight
    if dy < STEM_CURVE_MIN:
        return False
    ux, uy = (far[0] - P[0]) / length, (far[1] - P[1]) / length
    for o in segs[i]["offs"]:
        if abs((o[0] - P[0]) * uy - (o[1] - P[1]) * ux) > 0.06 * length:
            return False
    return True


def stem_dir(segs, i, P):
    pts = seg_points(segs, i)
    if pts[0] != P:
        pts = pts[::-1]
    for q in pts[1:]:
        if math.hypot(q[0] - P[0], q[1] - P[1]) > 1:
            return (q[0] - P[0], q[1] - P[1])
    return None


def flank_indices(n, k, side, si):
    if side == "fwd":
        return [(k + j) % n for j in range(1, (si - k) % n)]
    return [(si + j) % n for j in range(1, (k - si) % n)]


def measure(segs, k, side, stem_i, frame):
    """Geometry of a serif flank given its structure: corner, excursion, kind.
    Returns None if the structure does not describe a stroke end here."""
    n = len(segs)
    if segs[k]["type"] != "line":
        return None
    A, B = start(segs, k), segs[k]["end"]
    dx, dy = B[0] - A[0], B[1] - A[1]
    if abs(dx) < EDGE_MIN or abs(dy) > max(1.5, 2 * EDGE_TILT * abs(dx)):
        return None
    bottom = dx > 0
    sign = 1 if bottom else -1
    ty = (A[1] + B[1]) / 2
    endpoint = A if side == "back" else B
    flank = flank_indices(n, k, side, stem_i)
    if not flank:
        return None
    # A real stroke end: at the edge's other end the outline must also turn
    # back into the glyph. If it carries on past the edge (the stem of t or f
    # continuing above its crossbar), this edge is a crossbar, not a terminal.
    nxt = (k + 1) % n if side == "back" else (k - 1) % n
    q = seg_points(segs, nxt)
    q = q[1] if side == "back" else q[-2]
    if (q[1] - ty) * sign < -(abs(dy) / 2 + 3):
        return None
    # (the piece touching the edge is the serif's own end and may be long)
    for i in (flank[1:] if side == "fwd" else flank[:-1]):
        if segs[i]["type"] == "line":
            a, b = start(segs, i), segs[i]["end"]
            if math.hypot(b[0] - a[0], b[1] - a[1]) > FLANK_LINE_MAX:
                return None
    flank_pts = [p for i in flank for p in seg_points(segs, i)]
    P = start(segs, stem_i) if side == "fwd" else segs[stem_i]["end"]
    d = stem_dir(segs, stem_i, P)
    if d is None or d[1] * sign <= 0 or abs(d[0]) > 1.5 * abs(d[1]):
        return None
    C = intersect(P, d, A, (dx, dy))
    if C is None:
        return None
    # bottom edge: back flank = left side of the stem, fwd = right. top: reversed.
    left = (side == "back") == bottom
    exc, tip = 0, None
    for p in flank_pts:
        xs = P[0] + (p[1] - P[1]) * d[0] / d[1]
        e = (xs - p[0]) if left else (p[0] - xs)
        if e > exc:
            exc, tip = e, p
    if tip is None:
        return None
    # corner must lie on the edge (not beyond its other end)
    other = B if side == "back" else A
    if (other[0] - C[0]) * (other[0] - endpoint[0]) < 0 and abs(other[0] - C[0]) > 0.5:
        return None
    kind = "serif"
    if frame == "H" and not bottom and left:
        # entry flag: the outer end of the serif slopes down from the top
        # (n, i, l ...); a flat-topped serif (v, w, y, caps) is not a flag.
        xmin = min(p[0] for p in flank_pts)
        top_of_end = max(p[1] for p in flank_pts if p[0] <= xmin + 3)
        if top_of_end < ty - FLAG_DROP:
            kind = "flag"
    return {"corner": C, "P": P, "kind": kind, "exc": exc}


def detect(segs, frame):
    """Return {key: info}. key = (edge_index, side, stem_index)."""
    n = len(segs)
    found = {}
    for k in range(n):
        s = segs[k]
        if s["type"] != "line":
            continue
        A, B = start(segs, k), s["end"]
        dx, dy = B[0] - A[0], B[1] - A[1]
        if abs(dx) < EDGE_MIN or abs(dy) > max(1.5, EDGE_TILT * abs(dx)):
            continue
        # interior is to the left of travel: +x -> bottom terminal, -x -> top
        bottom = dx > 0
        sign = 1 if bottom else -1
        ty = (A[1] + B[1]) / 2
        for side in ("back", "fwd"):
            step = -1 if side == "back" else 1
            endpoint = A if side == "back" else B
            flank_pts = []
            stem_i = None
            for j in range(1, MAX_FLANK + 2):
                i = (k + step * j) % n if side == "fwd" else (k - j) % n
                if i == k:
                    break
                # point where segment i is entered when walking away from edge
                P = start(segs, i) if side == "fwd" else segs[i]["end"]
                if j > 1 and stem_check(segs, i, P, sign):
                    stem_i = i
                    break
                pts = seg_points(segs, i)
                if any((p[1] - ty) * sign > BAND[frame] or (p[1] - ty) * sign < -(abs(dy) / 2 + 3) for p in pts):
                    break
                flank_pts += pts
            if stem_i is None:
                continue
            m = measure(segs, k, side, stem_i, frame)
            if m is None or m["exc"] < EXCURSION or m["exc"] > EXCURSION_MAX[frame]:
                continue
            found[(k, side, stem_i)] = m
    return found


def apply(segs, edits):
    """edits: list of (k, side, stem_i, corner)."""
    n = len(segs)
    segs = [dict(s) for s in segs]
    delete = set()
    touched = set()
    for k, side, si, C in edits:
        flank = flank_indices(n, k, side, si)
        span = set(flank) | {k, si}
        if span & touched:
            raise ValueError("overlapping edits")
        touched |= set(flank)
        if side == "fwd":
            segs[k] = {**segs[k], "end": C, "smooth": False}
            if segs[si]["type"] == "line":
                delete |= set(flank)
            else:
                last = flank[-1]
                segs[last] = {**segs[last], "type": "line", "offs": [], "smooth": False}
                delete |= set(flank[:-1])
        else:
            if segs[si]["type"] == "line":
                segs[si] = {**segs[si], "end": C, "smooth": False}
                delete |= set(flank)
            else:
                first = flank[0]
                segs[si] = {**segs[si], "smooth": False}
                segs[first] = {"type": "line", "offs": [], "end": C, "smooth": False}
                delete |= set(flank[1:])
    return [s for i, s in enumerate(segs) if i not in delete]


# --- driver ---------------------------------------------------------------------
def is_uppercase(glyph, font):
    """Entry flags are a lowercase (and figure) feature; on capitals every
    serif goes. Alternates (.ss01, .loclBGR, .case ...) follow their base."""
    for name in (glyph.name, glyph.name.split(".")[0]):
        g = font.glyphs[name]
        if g is not None and g.unicode:
            return unicodedata.category(chr(int(g.unicode, 16))) in ("Lu", "Lt")
    return glyph.name[:1].isupper() and not glyph.name.startswith("uni")


def process_glyph(glyph, masters, report, font):
    layers = [l for l in glyph.layers if l.layerId == l.associatedMasterId
              and l.layerId in masters]
    if len(layers) != len(masters) or not any(l.paths for l in layers):
        return 0
    changed = 0
    uppercase = is_uppercase(glyph, font)
    for frame in ("H", "V"):
        npaths = len(layers[0].paths)
        if any(len(l.paths) != npaths for l in layers):
            report["incompatible"].append(glyph.name)
            return changed
        for pi in range(npaths):
            paths = [l.paths[pi] for l in layers]
            if not all(p.closed for p in paths):
                continue
            segsets = [to_segments(p) for p in paths]
            if len({len(s) for s in segsets}) != 1:
                report["incompatible"].append(f"{glyph.name}#{pi}")
                continue
            fsegs = [frame_segs(s, frame) for s in segsets]
            found = [detect(s, frame) for s in fsegs]
            # A serif found in any master is removed in all of them (same
            # structure, geometry re-measured per master) so that the masters
            # stay compatible and the rule is applied identically.
            todo = []
            for key in sorted(set().union(*found)):
                ms = [f.get(key) or measure(fs, *key, frame) for f, fs in zip(found, fsegs)]
                if any(m is None for m in ms):
                    report["mismatch"].append(f"{glyph.name}#{pi} {frame} {key}")
                    continue
                if any(m["kind"] == "flag" for m in ms) and not uppercase:
                    report["flags"][glyph.name] += 1
                    continue
                todo.append(key)
                for f, m in zip(found, ms):
                    f[key] = m
            # drop edits whose flanks overlap an earlier one
            used, keep = set(), []
            n = len(fsegs[0])
            for key in todo:
                span = set(flank_indices(n, *key))
                if span & used:
                    report["mismatch"].append(f"{glyph.name}#{pi} {frame} overlap {key}")
                    continue
                used |= span
                keep.append(key)
            todo = keep
            if not todo:
                continue
            for li, layer in enumerate(layers):
                edits = [(k[0], k[1], k[2], found[li][k]["corner"]) for k in todo]
                try:
                    new = apply(fsegs[li], edits)
                except ValueError as e:
                    report["mismatch"].append(f"{glyph.name}#{pi} {frame} {e}")
                    break
                new = [{**s, "offs": [unrot(o, frame) for o in s["offs"]],
                        "end": unrot(s["end"], frame)} for s in new]
                layer.paths[pi].nodes = from_segments(new)
            else:
                changed += len(todo)
                report["serifs"][glyph.name] += len(todo)
    return changed


def rename(font):
    # OFL: keep the original copyright notice; the Reserved Font Name "Lora"
    # must not be used, and Cyreal's trademark / vendor ID do not apply.
    font.familyName = FAMILY
    if "Leaopra" not in font.copyright:
        font.copyright += " Leaopra is a modified version of Lora."
    font.designer = f"{font.designer}; semi-serif modification (Leaopra)"
    font.manufacturer = None
    font.manufacturerURL = None
    font.customParameters = [p for p in font.customParameters
                             if p.name not in ("trademark", "vendorID")]


def main():
    verbose = "--report" in sys.argv
    for src, dst in SOURCES:
        font = GSFont(src)
        masters = [m.id for m in font.masters]
        report = {"serifs": defaultdict(int), "flags": defaultdict(int),
                  "mismatch": [], "incompatible": []}
        total = sum(process_glyph(g, masters, report, font) for g in font.glyphs)
        rename(font)
        font.save(dst)
        print(f"{dst}: removed {total} serifs in {len(report['serifs'])} glyphs, "
              f"kept {sum(report['flags'].values())} entry flags, "
              f"{len(report['mismatch'])} master mismatches")
        if verbose:
            print("  serifs:", dict(report["serifs"]))
            print("  flags:", dict(report["flags"]))
            for m in report["mismatch"]:
                print("  MISMATCH", m)
            for m in report["incompatible"]:
                print("  INCOMPATIBLE", m)


if __name__ == "__main__":
    main()
