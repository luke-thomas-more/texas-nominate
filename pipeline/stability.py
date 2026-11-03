"""How firmly do the votes fix the map? Tools for the stability checks.

  twist SRC DST DEGREES   Copy fit SRC to DST with everything before 1961 (members and roll calls of legislatures
                          1-56) turned by DEGREES against the rest; a member who served on both sides of 1961 turns
                          by the share of their legislatures before it. A refit started from DST (FIT_START=DST,
                          FIX_BETA=1 FIX_W=1) shows whether the votes pull the old eras back into place: if they come
                          back, the eras' relative orientation is fixed by the data; if they stay turned, it isn't.
  compare A B             For each era: the rotation that best maps fit A's members onto fit B's (orthogonal
                          Procrustes; det -1 means a reflection too), and how closely the two fits agree on each axis.
Fits are directories under data/processed (or paths) holding member_scores.csv, rollcall_params.csv, summary.json.
"""
import csv
import math
import os
import shutil
import sys

import numpy as np

PROC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "processed")
ERAS = [(1, 13, "1846-73"), (14, 26, "1874-1900"), (27, 41, "1901-30"), (42, 56, "1931-60"), (57, 62, "1961-72"),
        (63, 67, "1973-82"), (68, 73, "1983-94"), (74, 89, "1995-2025")]
SPLIT = 56                                         # the last legislature turned by `twist` (1959-60)


def path(fit):
    return fit if os.path.isabs(fit) or os.path.exists(fit) else os.path.join(PROC, fit)


def turn(x, y, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return c * x - s * y, s * x + c * y


def twist(src, dst, deg):
    src, dst = path(src), path(dst)
    os.makedirs(dst, exist_ok=True)
    shutil.copy(os.path.join(src, "summary.json"), dst)
    rows = list(csv.DictReader(open(os.path.join(src, "member_scores.csv"))))
    legs = {}
    for r in rows:
        legs.setdefault(r["member_id"], []).append(int(r["leg"]))
    share = {m: sum(l <= SPLIT for l in ls) / len(ls) for m, ls in legs.items()}
    for r in rows:
        x, y = turn(float(r["dim1"]), float(r["dim2"]), deg * share[r["member_id"]])
        r["dim1"], r["dim2"] = f"{x:.4f}", f"{y:.4f}"
    with open(os.path.join(dst, "member_scores.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    rows = list(csv.DictReader(open(os.path.join(src, "rollcall_params.csv"))))
    for r in rows:
        if int(r["rc_id"].split("-")[0][:-1]) <= SPLIT:
            for a, b in (("mid1", "mid2"), ("spread1", "spread2")):
                if r.get(a) not in (None, "") and r.get(b) not in (None, ""):
                    x, y = turn(float(r[a]), float(r[b]), deg)
                    r[a], r[b] = f"{x:.4f}", f"{y:.4f}"
    with open(os.path.join(dst, "rollcall_params.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{dst}: legislatures 1-{SPLIT} turned {deg:+g} degrees")


def load(fit):
    out = {}
    for r in csv.DictReader(open(os.path.join(path(fit), "member_scores.csv"))):
        m = out.setdefault(int(r["member_id"]), {"legs": set(), "x": (float(r["dim1"]), float(r["dim2"]))})
        m["legs"].add(int(r["leg"]))
    return out


def compare(a, b):
    A, B = load(a), load(b)
    print(f"{a} -> {b}: per era, the best rotation (positive = counterclockwise) and agreement on each axis")
    for lo, hi, era in ERAS:
        ids = [m for m in A if m in B and any(lo <= l <= hi for l in A[m]["legs"])]
        X, Y = np.array([A[m]["x"] for m in ids]), np.array([B[m]["x"] for m in ids])
        U, _, Vt = np.linalg.svd(X.T @ Y)
        R = U @ Vt
        print(f"  {era:10s} n={len(ids):5d}  turn {math.degrees(math.atan2(R[0, 1], R[0, 0])):+6.1f} deg"
              f"{' (with a reflection)' if np.linalg.det(R) < 0 else ''}  "
              f"r dim1 {np.corrcoef(X[:, 0], Y[:, 0])[0, 1]:.3f}  r dim2 {np.corrcoef(X[:, 1], Y[:, 1])[0, 1]:.3f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "twist" and len(sys.argv) == 5:
        twist(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    elif cmd == "compare" and len(sys.argv) == 4:
        compare(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
