"""
sm_model_centres.py  v1.0

Measure where the STAIR actually is in each model, and write the answer out
for the customer configurator to aim at.

WHY
    The viewer cannot work this out for itself. Its node list gives a name, a
    material and a type - no positions at all - so the only way to centre on
    the stair was to ask Sketchfab to recentre, and that centres the whole
    scene. The room's walls run well above the stair, so the point it lands on
    sits about 700 mm too high and the stair hangs low in the frame.

    Measured on Barcelona 260, Barcelona 280, Antigua 270 and Grande 430:

        scene centre (what Sketchfab used)   2.67 m
        stair centre (measured here)         1.97 m

WHAT IT DOES
    Reads each DAE, applies the node matrices so every part is in its real
    position, and takes the extents of the STAIR parts only - treads, risers,
    nosings, stringers, rails, newels, balusters, glass. Rooms, floors, void
    trims and wall cappings are left out; they are the things that drag the
    centre off.

    Writes model_centres.json keyed exactly as config.json keys its models:

        "Barcelona 260|LH|16|Nouveau|Type 30": [1.84, -1.26, 1.97]

USAGE
    python sm_model_centres.py <output root> --out model_centres.json
    python sm_model_centres.py <output root>            report only

AFTER
    Commit model_centres.json to GitHub beside config.json, in
    data/clarendon-v2/. Nothing else changes - no re-export, no re-upload, no
    publish. The viewer reads it on load and aims there; a model with no entry
    behaves as it does now.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

VERSION = "1.0"
C = '{http://www.collada.org/2005/11/COLLADASchema}'

# The stair itself. A part has to match one of these to be measured.
STAIR = ('TREAD', 'RISER', 'NOSING', 'STRINGER', 'HANDRAIL', 'WALL_RAIL',
         'NEWEL', 'BALUSTER', 'BOTTOM_RAIL', 'BULLNOSE', 'GLASS_PANEL',
         'GLASS_CLAMP', 'STAIR')

# The room it sits in. Never measured, whatever else the name says.
NOT_STAIR = ('ROOM', 'GROUND_FLOOR', 'UPPER_FLOOR', 'UPPER_RIM', 'VOID_TRIM',
             'BEGIN_WALL', 'BEGIN_OPENING', 'IGNORE', 'CAPPING', 'LANDING',
             'OBJECT3D')

NAME_RE = re.compile(r'^(.+?)_(LH|RH)_(\d+)R_(.+?)_\(Type[_ ](\d+)\)$', re.I)


def model_key(stem):
    """Filename -> the key config.json uses, or None."""
    m = NAME_RE.match(stem)
    if not m:
        return None
    house = m.group(1).replace('_', ' ').strip()
    rng = m.group(4).replace('_', ' ').strip()
    return '%s|%s|%s|%s|Type %s' % (house, m.group(2).upper(), m.group(3),
                                    rng, m.group(5))


def is_stair(names):
    for n in names:
        up = (n or '').upper()
        if any(up.startswith(x) for x in NOT_STAIR):
            continue
        if any(up.startswith(x) for x in STAIR):
            return True
    return False


def stair_centre(path):
    """([x, y, z], parts measured) or (None, 0)."""
    root = ET.parse(path).getroot()
    matname = {m.get('id'): (m.get('name') or '') for m in root.iter(C + 'material')}

    geo = {}
    for g in root.iter(C + 'geometry'):
        srcs = {}
        for s in g.iter(C + 'source'):
            fa = s.find(C + 'float_array')
            if fa is not None and fa.text:
                srcs[s.get('id')] = [float(x) for x in fa.text.split()]
        vmap = {vt.get('id'): i.get('source', '').lstrip('#')
                for vt in g.iter(C + 'vertices')
                for i in vt.findall(C + 'input') if i.get('semantic') == 'POSITION'}
        mats, pos = set(), None
        for tri in list(g.iter(C + 'triangles')) + list(g.iter(C + 'polylist')):
            mats.add(matname.get(tri.get('material'), ''))
            for i in tri.findall(C + 'input'):
                if i.get('semantic') == 'VERTEX':
                    pos = srcs.get(vmap.get(i.get('source', '').lstrip('#')))
        geo[g.get('id')] = (pos, mats)

    lo = [1e18] * 3
    hi = [-1e18] * 3
    used = [0]

    def walk(node, M):
        m = node.find(C + 'matrix')
        L = M
        if m is not None and m.text:
            v = [float(x) for x in m.text.split()]
            if len(v) == 16:
                L = [[sum(M[i][k] * v[k * 4 + j] for k in range(4))
                      for j in range(4)] for i in range(4)]
        for ig in node.findall(C + 'instance_geometry'):
            gid = (ig.get('url') or '').lstrip('#')
            if gid not in geo:
                continue
            pos, mats = geo[gid]
            if not pos or not is_stair(mats):
                continue
            used[0] += 1
            for t in range(0, len(pos), 3):
                p = pos[t:t + 3]
                for a in range(3):
                    w = sum(L[a][b] * p[b] for b in range(3)) + L[a][3]
                    if w < lo[a]:
                        lo[a] = w
                    if w > hi[a]:
                        hi[a] = w
        for ch in node.findall(C + 'node'):
            walk(ch, L)

    I = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for sc in root.iter(C + 'visual_scene'):
        for n in sc.findall(C + 'node'):
            walk(n, I)

    if not used[0]:
        return None, 0
    return [round((lo[a] + hi[a]) / 2.0, 3) for a in range(3)], used[0]


def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('root', nargs='?')
    ap.add_argument('--out', default='')
    try:
        args = ap.parse_args(argv[1:])
    except SystemExit:
        print(__doc__)
        return 1
    if not args.root or not os.path.isdir(args.root):
        print(__doc__)
        return 1

    files = []
    for dirpath, _d, names in os.walk(args.root):
        for f in names:
            if f.lower().endswith('.dae') and '_glass_backup' not in dirpath:
                files.append(os.path.join(dirpath, f))
    files.sort()

    print("sm_model_centres v%s   %d file(s)\n" % (VERSION, len(files)))
    out, skipped, unnamed = {}, [], []
    for n, path in enumerate(files, 1):
        sys.stdout.write("\r  reading %d/%d" % (n, len(files)))
        sys.stdout.flush()
        stem = os.path.splitext(os.path.basename(path))[0]
        key = model_key(stem)
        if not key:
            unnamed.append(stem)
            continue
        try:
            c, parts = stair_centre(path)
        except Exception as e:
            skipped.append('%s (%s)' % (stem, e))
            continue
        if not c:
            skipped.append('%s (no stair parts)' % stem)
            continue
        out[key] = c

    print("\r" + " " * 40 + "\r", end='')
    print("%d model(s) measured" % len(out))
    if out:
        hs = sorted(v[2] for v in out.values())
        print("   stair centre height: lowest %.2f   median %.2f   highest %.2f"
              % (hs[0], hs[len(hs) // 2], hs[-1]))
    if unnamed:
        print("\n%d file(s) whose name does not parse - not included:" % len(unnamed))
        for u in unnamed[:10]:
            print("   ", u)
        if len(unnamed) > 10:
            print("    ... and %d more" % (len(unnamed) - 10))
    if skipped:
        print("\n%d file(s) skipped:" % len(skipped))
        for sk in skipped[:10]:
            print("   ", sk)
        if len(skipped) > 10:
            print("    ... and %d more" % (len(skipped) - 10))

    if not args.out:
        print("\nNothing written. Add --out model_centres.json to write it.")
        return 0
    try:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=1, sort_keys=True)
    except Exception as e:
        print("\ncould not write %s: %s" % (args.out, e))
        return 1
    print("\nwritten: %s" % args.out)
    print("Commit it to GitHub in data/clarendon-v2/, beside config.json.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
