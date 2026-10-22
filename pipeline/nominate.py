"""DW-NOMINATE (dynamic, weighted NOMINATE), implemented from the published model.

The model (Poole & Rosenthal 1997; Carroll, Lewis, Lo, Poole & Rosenthal 2009):
  - Legislator i in period t sits at x_i(t) inside the unit ball and moves linearly over their
    career; a legislator who serves one period has a single point.
  - Roll call j has its yea outcome at m_j - d_j and its nay outcome at m_j + d_j
    (midpoint m_j, spread d_j).
  - The utility of an outcome O is beta * exp(-1/2 * sum_k w_k^2 (x_k - O_k)^2), with w_1 = 1.
  - Errors are normal, so P(yea) = Phi(U(yea) - U(nay)).

Estimation:
  1. Start from the leading singular vectors of the vote matrix (like NOMINATE's eigenvector start).
  2. Alternate NOMINATE-style: every roll call given the legislators, every legislator given the
     roll calls, then beta and the weights. Each block takes damped Fisher-scoring steps, all
     items at once, with a per-item line search so the likelihood never falls.
  3. Polish all positions and outcomes jointly with L-BFGS (beta and weights held fixed), then
     re-estimate beta and the weights; repeat until the likelihood stops improving.

Identification: legislators live in the unit ball. On dimensions 2..K, shrinking everyone's
coordinate by c while dividing the dimension's weight by c changes nothing, so after each round
that dimension is stretched until the outermost legislator touches the unit circle, the way
NOMINATE solutions look. (Freeing beta during the joint polish would let it trade off against the
spreads and drift, so it is only updated in its own step.)

The inner loops are compiled (nominate_kernels.py) and run on all cores.
"""
import os
import numpy as np
from scipy import optimize
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import svds

import nominate_kernels as nk


def to_ball(P, r=1.0):
    n = np.linalg.norm(P, axis=-1, keepdims=True)
    return np.where(n > r, P * (r / np.maximum(n, 1e-12)), P)


def svd_start(rc, mem, yea, n_rc, n_mem, K):
    """Starting positions from the leading singular vectors of the vote matrix (yea +1, nay -1,
    each roll call centered on its mean, missing = 0)."""
    v = np.where(yea, 1.0, -1.0)
    mean = np.bincount(rc, weights=v, minlength=n_rc) / np.maximum(np.bincount(rc, minlength=n_rc), 1)
    Y = csr_matrix((v - mean[rc], (mem, rc)), shape=(n_mem, n_rc))
    U, S, _ = svds(Y, k=K)
    order = np.argsort(-S)
    X = U[:, order] * S[order]
    # members who cast few votes are pulled toward the center rather than flung outward
    X = X / np.sqrt(np.maximum(np.bincount(mem, minlength=n_mem), 1))[:, None]
    return X / np.abs(X).max(0)


class DWNominate:
    """
    rc, mem : int arrays, one entry per observed yea/nay vote
    yea     : bool array (True = yea)
    frac    : where the vote falls in the member's career, 0 = first period, 1 = last
    """

    def __init__(self, rc, mem, yea, frac, n_rc, n_mem, K=2, trend=True):
        self.rc = np.ascontiguousarray(rc, np.int64)
        self.mem = np.ascontiguousarray(mem, np.int64)
        self.s = np.where(yea, 1.0, -1.0)
        self.frac = np.ascontiguousarray(frac, float) if trend else np.zeros(len(self.rc))
        self.n_rc, self.n_mem, self.K, self.trend = n_rc, n_mem, K, trend
        self.T = nk.n_threads()
        self.log = []

    def _data(self):
        return self.rc, self.mem, self.s, self.frac

    def _state(self, **kw):
        return (kw.get("Pm", self.Pm), kw.get("Pp", self.Pp), kw.get("M", self.M), kw.get("D", self.D),
                float(kw.get("beta", self.beta)), kw.get("w2", self.w2))

    # ---- starting values
    def start(self, X0, beta=5.0, w2=0.5):
        """X0: (n_mem, K) rough positions. Outcomes start at the yea and nay voters' average positions."""
        X0 = np.asarray(X0, float)
        X0 = 0.8 * X0 / max(np.linalg.norm(X0, axis=1).max(), 1e-9)
        Xo = X0[self.mem]
        yea = self.s > 0
        cy = np.bincount(self.rc[yea], minlength=self.n_rc)[:, None]
        cn = np.bincount(self.rc[~yea], minlength=self.n_rc)[:, None]
        mu_y = np.stack([np.bincount(self.rc[yea], weights=Xo[yea, k], minlength=self.n_rc) for k in range(self.K)], 1) / np.maximum(cy, 1)
        mu_n = np.stack([np.bincount(self.rc[~yea], weights=Xo[~yea, k], minlength=self.n_rc) for k in range(self.K)], 1) / np.maximum(cn, 1)
        self.Pm, self.Pp = X0.copy(), X0.copy()
        self.M = to_ball((mu_y + mu_n) / 2, 0.99)
        self.D = (mu_n - mu_y) / 2
        self.beta = float(beta)
        self.w2 = np.array([1.0] + [w2] * (self.K - 1))
        return self

    def loglik(self, by=2, **kw):
        n_out = self.n_rc if by == 0 else self.n_mem if by == 1 else 1
        out = nk.loglik_by(*self._state(**kw), *self._data(), by, n_out, self.T)
        return out if by != 2 else float(out[0])

    # ---- NOMINATE-style alternating blocks
    @staticmethod
    def _solve(g, H, damp=1e-3):
        p = g.shape[1]
        Hd = H + damp * (np.einsum("nii->ni", H)[:, :, None] * np.eye(p)) + 1e-6 * np.eye(p)
        return np.linalg.solve(Hd, g[:, :, None])[:, :, 0]

    def _line_search(self, by, n, step, apply, halvings=6):
        before = self.loglik(by=by)
        alpha = np.ones(n)
        accepted = np.zeros(n, bool)
        for _ in range(halvings):
            trial = apply(step * (alpha * ~accepted)[:, None])
            after = self.loglik(by=by, **trial)
            accepted |= after >= before - 1e-12
            if accepted.all():
                break
            alpha = np.where(accepted, alpha, alpha / 2)
        for k, v in apply(step * (alpha * accepted)[:, None]).items():
            setattr(self, k, v)

    def step_rollcalls(self):
        g, H = nk.block_rollcalls(*self._state(), *self._data(), self.T)
        K = self.K
        self._line_search(0, self.n_rc, self._solve(g, H),
                          lambda st: {"M": to_ball(self.M + st[:, :K]), "D": self.D + st[:, K:]})

    def step_members(self):
        g, H = nk.block_members(*self._state(), *self._data(), self.T)
        K = self.K
        if self.trend:
            apply = lambda st: {"Pm": to_ball(self.Pm + st[:, :K]), "Pp": to_ball(self.Pp + st[:, K:])}
            step = self._solve(g, H)
        else:
            g1, H1 = g[:, :K], H[:, :K, :K]
            step = self._solve(g1, H1)
            apply = lambda st: {"Pm": to_ball(self.Pm + st), "Pp": to_ball(self.Pm + st)}
        self._line_search(1, self.n_mem, step, apply)

    def step_global(self):
        """beta and the squared weights of dimensions 2..K (w_1 = 1)."""
        g, H = nk.block_global(*self._state(), *self._data(), self.T)
        step = np.linalg.solve(H + 1e-3 * np.diag(np.diag(H)) + 1e-9 * np.eye(len(g)), g)
        if getattr(self, "fix_beta", False):
            step[0] = 0.0                      # beta held at its current value
        old = self.loglik()
        for a in (1.0, 0.5, 0.25, 0.125, 0.0625):
            beta = float(np.clip(self.beta + a * step[0], 0.5, 60.0))
            w2 = np.concatenate([[1.0], np.clip(self.w2[1:] + a * step[1:], 0.005, 4.0)])
            new = self.loglik(beta=beta, w2=w2)
            if new >= old:
                self.beta, self.w2 = beta, w2
                return new
        return old

    def normalize(self):
        """Pin the flat direction of dimensions 2..K: stretch each until the outermost legislator
        touches the unit circle, dividing its weight to match (the likelihood is unchanged)."""
        for k in range(1, self.K):
            P = np.vstack([self.Pm, self.Pp])
            other = (P ** 2).sum(1) - P[:, k] ** 2
            room = np.where(P[:, k] ** 2 > 1e-12, (1 - np.minimum(other, 1)) / np.maximum(P[:, k] ** 2, 1e-12), np.inf)
            c = float(np.sqrt(room.min()))
            if not np.isfinite(c) or abs(c - 1) < 1e-6:
                continue
            for arr in (self.Pm, self.Pp, self.M, self.D):
                arr[:, k] *= c
            self.w2 = self.w2.copy()
            self.w2[k] /= c * c
            self.M = to_ball(self.M, 0.999)
        return self

    def alternate(self, iters=30, verbose=True, warmup=5):
        if iters <= 0:
            return self
        for _ in range(warmup):
            self.step_rollcalls()
        for it in range(1, iters + 1):
            self.step_rollcalls()
            self.step_members()
            self.step_global()
            ll = self.normalize().loglik()
            self.log.append(ll)
            if verbose and (it % 10 == 0 or it == 1):
                print(f"    alternate {it:4d}  loglik {ll:,.2f}  beta {self.beta:.2f}  w {np.sqrt(self.w2[1:]).round(3)}", flush=True)
        return self

    # ---- joint polish: positions and midpoints reparameterized as v * tanh(|v|) / |v| so they stay
    # inside the unit ball exactly
    @staticmethod
    def _squash(V):
        r = np.linalg.norm(V, axis=1, keepdims=True)
        return V * np.where(r > 1e-8, np.tanh(r) / np.maximum(r, 1e-8), 1.0)

    @staticmethod
    def _unsquash(X, cap=0.999):
        r = np.linalg.norm(X, axis=1, keepdims=True)
        s = np.where(r > cap, cap / np.maximum(r, 1e-12), 1.0)
        X, r = X * s, np.minimum(r * s, cap)
        return X * np.where(r > 1e-8, np.arctanh(r) / np.maximum(r, 1e-8), 1.0)

    @staticmethod
    def _squash_grad(V, G):
        r = np.linalg.norm(V, axis=1, keepdims=True)
        rs = np.maximum(r, 1e-8)
        t = np.tanh(rs)
        f = np.where(r > 1e-8, t / rs, 1.0)
        fp_over_r = np.where(r > 1e-4, (rs * (1 - t * t) - t) / rs ** 3, -2.0 / 3.0)
        return f * G + fp_over_r * V * (V * G).sum(1, keepdims=True)

    def _sizes(self):
        return self.n_mem * self.K, self.n_rc * self.K

    def _pack(self):
        return np.concatenate([self._unsquash(self.Pm).ravel(), self._unsquash(self.Pp).ravel(),
                               self._unsquash(self.M).ravel(), self.D.ravel()])

    def _unpack(self, theta):
        nm, nr = self._sizes()
        K = self.K
        return (theta[:nm].reshape(-1, K), theta[nm:2 * nm].reshape(-1, K),
                theta[2 * nm:2 * nm + nr].reshape(-1, K), theta[2 * nm + nr:].reshape(-1, K))

    def _objective(self, theta):
        Vm, Vp, Mr, D = self._unpack(theta)
        Pm, M = self._squash(Vm), self._squash(Mr)
        Pp = self._squash(Vp) if self.trend else Pm
        ll, gPm, gPp, gM, gD = nk.objective(Pm, Pp, M, np.ascontiguousarray(D), self.beta, self.w2, *self._data(), self.T)
        if not self.trend:
            gPm, gPp = gPm + gPp, np.zeros_like(gPp)
        grad = np.concatenate([self._squash_grad(Vm, gPm).ravel(), self._squash_grad(Vp, gPp).ravel(),
                               self._squash_grad(Mr, gM).ravel(), gD.ravel()])
        return -ll, -grad

    @staticmethod
    def _lbfgs(fg, x, maxiter, m=20, ftol=1e-11, verbose=False):
        """Plain L-BFGS (two-loop recursion, backtracking Armijo line search). scipy's L-BFGS-B
        spends far more time on its bound-handling bookkeeping than on our likelihood at this size."""
        f, g = fg(x)
        S, Y, R = [], [], []
        nfev, small = 1, 0
        for it in range(1, maxiter + 1):
            q = g.copy()
            alphas = []
            for s, y, r in zip(reversed(S), reversed(Y), reversed(R)):
                a = r * s.dot(q)
                alphas.append(a)
                q -= a * y
            q *= (S[-1].dot(Y[-1]) / Y[-1].dot(Y[-1])) if S else 1.0 / max(np.linalg.norm(g), 1.0)
            for (s, y, r), a in zip(zip(S, Y, R), reversed(alphas)):
                q += s * (a - r * y.dot(q))
            d = -q
            gd = g.dot(d)
            if gd >= 0:                                  # not a descent direction: restart
                S, Y, R = [], [], []
                d, gd = -g / max(np.linalg.norm(g), 1.0), -g.dot(g) / max(np.linalg.norm(g), 1.0)
            t = 1.0
            while True:
                xn = x + t * d
                fn, gn = fg(xn)
                nfev += 1
                if fn <= f + 1e-4 * t * gd or t < 1e-12:
                    break
                t *= 0.5
            s, y = xn - x, gn - g
            sy = s.dot(y)
            if sy > 1e-10 * y.dot(y):
                S.append(s); Y.append(y); R.append(1.0 / sy)
                if len(S) > m:
                    S.pop(0); Y.pop(0); R.pop(0)
            small = small + 1 if f - fn < ftol * max(1.0, abs(f)) else 0
            x, f, g = xn, fn, gn
            if verbose and it % 250 == 0:
                print(f"    polish {it:5d}  loglik {-f:,.2f}", flush=True)
            if small >= 5 or t < 1e-12:
                break
        return x, f, it, nfev

    def polish(self, maxiter=1500, verbose=True):
        x, f, nit, nfev = self._lbfgs(self._objective, self._pack(), maxiter, verbose=verbose)
        Vm, Vp, Mr, D = self._unpack(x)
        self.Pm, self.M, self.D = self._squash(Vm), self._squash(Mr), np.ascontiguousarray(D)
        self.Pp = self._squash(Vp) if self.trend else self.Pm
        self.polish_result = optimize.OptimizeResult(x=x, fun=f, nit=nit, nfev=nfev)
        return self

    def fit(self, verbose=True, rounds=4, alternate_iters=30, polish_iter=1500, tol=1e-6, move_tol=0.005):
        """Stops when the likelihood stops improving or no legislator moves more than move_tol
        (on the -1..1 scale) between rounds."""
        self.alternate(alternate_iters, verbose)
        self.normalize()
        prev = self.loglik()
        prev_pos = np.hstack([self.Pm, self.Pp])
        for r in range(rounds):
            self.polish(polish_iter, verbose)
            for _ in range(3):
                self.step_global()
            self.normalize()
            ll = self.loglik()
            pos = np.hstack([self.Pm, self.Pp])
            moved = np.abs(pos - prev_pos)
            self.moved = (float(moved.max()), float(moved.mean()))
            if verbose:
                print(f"    round {r + 1}: loglik {ll:,.2f}  beta {self.beta:.2f}  w {np.sqrt(self.w2[1:]).round(3)}"
                      f"  moved max {self.moved[0]:.4f} mean {self.moved[1]:.4f}", flush=True)
            if ll - prev < tol * abs(ll) or self.moved[0] < move_tol:
                break
            prev, prev_pos = ll, pos
        return self

    # ---- results
    def position(self, mem_idx, frac):
        f = np.asarray(frac, float)[:, None]
        return self.Pm[mem_idx] * (1 - f) + self.Pp[mem_idx] * f

    def fit_stats(self):
        """Correct classification, APRE and GMP over the scaled votes."""
        z, lc = nk.predict(*self._state(), *self._data(), self.T)
        correct = (z > 0) == (self.s > 0)
        yeas = np.bincount(self.rc, weights=(self.s > 0).astype(float), minlength=self.n_rc)
        tot = np.bincount(self.rc, minlength=self.n_rc)
        minority = np.minimum(yeas, tot - yeas)
        errors = np.bincount(self.rc, weights=(~correct).astype(float), minlength=self.n_rc)
        return {"correct": float(correct.mean()), "apre": float((minority - errors).sum() / minority.sum()),
                "gmp": float(np.exp(lc.mean())), "loglik": float(lc.sum()), "n_votes": int(len(z)),
                "beta": self.beta, "w": [float(x) for x in np.sqrt(self.w2)]}
