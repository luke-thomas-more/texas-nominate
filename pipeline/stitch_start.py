"""Starting positions for an all-history fit, stitched from the era-only fits.

One SVD over 1846-2025 is dominated by the modern votes, and a fit started from it gets stuck in a
poor solution for the eras it describes badly (1901-60: started there, even a free fit of 1901-30
alone stays at held-out APRE 0.03 where one started from its own votes reaches 0.19). Each era's
own fit does not have that problem, so start from those instead, put in one frame: the newest era
is the reference, and walking back, each era is rotated (or reflected) onto the next one by
orthogonal Procrustes on the members who served in both. No scale: each fit is already scaled to
the unit ball, and a fitted scale shrinks every step back (bridging members never line up exactly,
so least squares pulls toward zero; the scales compound to 0.004 by 1846). Dimension 2 is compared
in weighted units (w * x2), since each fit has its own weight.
"""
import csv, json, os
import numpy as np

from era_check import ERAS


def _read(folder):
    pos = {}
    w = json.load(open(os.path.join(folder, "summary.json")))["w"]
    for r in csv.DictReader(open(os.path.join(folder, "member_scores.csv"))):
        pos.setdefault(int(r["member_id"]), np.array([float(r["dim1"]), w[1] * float(r["dim2"])]))
    return pos, w[1]


def _procrustes(A, B):
    """Orthogonal R minimizing ||A R - B|| (A, B centered on the same members)."""
    U, _, Vt = np.linalg.svd(A.T @ B)
    return U @ Vt


def stitched_positions(mem_keys, prefix):
    """(n_mem, 2) start positions and a mask of members found in some era fit. prefix + "<a>_<b>"
    names each era's fit folder (e.g. data/processed/fit_era_ or data/processed/holdout/era_)."""
    eras = [_read(f"{prefix}{a}_{b}") for a, b, _ in ERAS]
    ref, w_ref = eras[-1]
    aligned = [dict(ref)]
    for pos, _ in reversed(eras[:-1]):
        nxt = aligned[-1]
        common = sorted(pos.keys() & nxt.keys())
        A = np.array([pos[m] for m in common]); B = np.array([nxt[m] for m in common])
        ca, cb = A.mean(0), B.mean(0)
        R = _procrustes(A - ca, B - cb)
        aligned.append({m: (x - ca) @ R + cb for m, x in pos.items()})
        r1 = np.corrcoef(((A - ca) @ R)[:, 0], B[:, 0])[0, 1]
        r2 = np.corrcoef(((A - ca) @ R)[:, 1], B[:, 1])[0, 1]
        print(f"    stitch: {len(common)} bridging members, rotation {np.degrees(np.arctan2(R[0, 1], R[0, 0])):+.0f} deg"
              f"{' (reflected)' if np.linalg.det(R) < 0 else ''}, agreement r = {r1:.2f} / {r2:.2f}", flush=True)
    sums, counts = {}, {}
    for era in aligned:
        for m, x in era.items():
            sums[m] = sums.get(m, 0) + x
            counts[m] = counts.get(m, 0) + 1
    X = np.zeros((len(mem_keys), 2)); found = np.zeros(len(mem_keys), bool)
    for i, m in enumerate(mem_keys):
        if int(m) in sums:
            X[i] = sums[int(m)] / counts[int(m)]
            found[i] = True
    X[:, 1] /= w_ref                       # back to unweighted units in the reference frame
    return X, found
