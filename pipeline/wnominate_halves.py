"""Split each session's W-NOMINATE vote matrix into two halves of its roll calls and scale each half on its own.

How well two halves of the same session agree is the most any other scaling could be expected to agree with
W-NOMINATE there: it measures how much of a W-NOMINATE map is the session and how much is the luck of which votes
were taken. The matrices are the ones validate_wnominate.py wrote (data/processed/wnominate/<leg><chamber>.csv);
their roll calls, in journal order, go alternately to half a and half b, so both halves span the whole session.
Writes data/processed/wnominate_halves/<leg><chamber>_{a,b}.csv and runs pipeline/wnominate.R on them, WORKERS at
a time (default 4). pipeline/wnominate_trace.py reads the result.

Usage: python pipeline/wnominate_halves.py
"""
import csv, io, os, subprocess

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROC = os.path.join(ROOT, "data", "processed")
SRC = os.path.join(PROC, "wnominate")
OUT = os.path.join(PROC, "wnominate_halves")
RSCRIPT = os.environ.get("RSCRIPT", os.path.expanduser("~/.local/micromamba/envs/rstats/bin/Rscript"))


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = []
    for line in open(os.path.join(SRC, "jobs.txt")):
        name = line.split()[0]
        rows = list(csv.reader(open(os.path.join(SRC, name + ".csv"))))
        for half, start in (("a", 1), ("b", 2)):
            cols = [0] + list(range(start, len(rows[0]), 2))
            part = [[r[c] for c in cols] for r in rows]
            # polarity only fixes the signs, which the comparison ignores: the member with the most votes is always kept
            most = max(part[1:], key=lambda r: sum(v != "9" for v in r[1:]))[0]
            # wnominate.R skips a half whose coordinates exist: they're kept only while its votes are unchanged
            path, buf = os.path.join(OUT, f"{name}_{half}.csv"), io.StringIO(newline="")
            csv.writer(buf).writerows(part)
            if not os.path.exists(path) or open(path, newline="").read() != buf.getvalue():
                open(path, "w", newline="").write(buf.getvalue())
                if os.path.exists(path[:-4] + ".coords.csv"):
                    os.remove(path[:-4] + ".coords.csv")
            jobs.append(f"{name}_{half} {most} {most}\n")
    open(os.path.join(OUT, "jobs.txt"), "w").writelines(jobs)
    n = int(os.environ.get("WORKERS", 4))
    env = dict(os.environ, PATH=os.path.dirname(RSCRIPT) + os.pathsep + os.environ.get("PATH", ""))
    procs = [subprocess.Popen([RSCRIPT, os.path.join(ROOT, "pipeline", "wnominate.R"), OUT, str(k), str(n)], env=env)
             for k in range(n)]
    if any(p.wait() for p in procs):
        raise SystemExit("an R worker failed")


if __name__ == "__main__":
    main()
