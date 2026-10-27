"""Compiled inner loops for nominate.py: one pass over the votes per call, all cores.

Every kernel walks the observed votes in per-thread chunks and accumulates into per-thread
buffers that are summed at the end, so there are no races and no big temporaries.
"""
import math
import numba as nb
import numpy as np

SQRT2 = math.sqrt(2.0)
LOG_SQRT_2PI = 0.5 * math.log(2 * math.pi)


@nb.njit(cache=True, inline="always")
def log_ndtr(x):
    if x > 0.0:
        return math.log1p(-0.5 * math.erfc(x / SQRT2))
    if x > -30.0:
        return math.log(0.5 * math.erfc(-x / SQRT2))
    x2 = x * x
    return -0.5 * x2 - math.log(-x) - LOG_SQRT_2PI + math.log(1.0 - 1.0 / x2 + 3.0 / (x2 * x2))


@nb.njit(cache=True, inline="always")
def _utils(Pm, Pp, M, D, w2, G, i, j, f, a, b):
    """G[j, k] is 1 where roll call j uses dimension k, 0 where it does not (the hybrid model's
    era axes). A masked dimension drops out of both utilities, so every gradient on it is zero."""
    K = Pm.shape[1]
    dy = 0.0
    dn = 0.0
    for k in range(K):
        xm = Pm[i, k] * (1.0 - f) + Pp[i, k] * f - M[j, k]
        a[k] = G[j, k] * (xm + D[j, k])
        b[k] = G[j, k] * (xm - D[j, k])
        dy += w2[k] * a[k] * a[k]
        dn += w2[k] * b[k] * b[k]
    return math.exp(-0.5 * dy), math.exp(-0.5 * dn)


def n_threads():
    return nb.get_num_threads()


@nb.njit(parallel=True, cache=True)
def loglik_by(Pm, Pp, M, D, beta, w2, rc, mem, s, frac, G, by, n_out, T):
    """Log-likelihood summed per roll call (by=0), per member (by=1) or in total (by=2)."""
    N = rc.shape[0]
    K = Pm.shape[1]
    out = np.zeros((T, max(n_out, 1)))
    chunk = (N + T - 1) // T
    for t in nb.prange(T):
        a = np.empty(K)
        b = np.empty(K)
        for o in range(t * chunk, min(N, (t + 1) * chunk)):
            ey, en = _utils(Pm, Pp, M, D, w2, G, mem[o], rc[o], frac[o], a, b)
            lc = log_ndtr(s[o] * beta * (ey - en))
            if by == 0:
                out[t, rc[o]] += lc
            elif by == 1:
                out[t, mem[o]] += lc
            else:
                out[t, 0] += lc
    return out.sum(0)


@nb.njit(parallel=True, cache=True)
def objective(Pm, Pp, M, D, beta, w2, rc, mem, s, frac, G, T):
    """Total log-likelihood and its gradient for positions (start, end), midpoints and spreads."""
    N = rc.shape[0]
    K = Pm.shape[1]
    nm, nr = Pm.shape[0], M.shape[0]
    gPm = np.zeros((T, nm, K)); gPp = np.zeros((T, nm, K))
    gM = np.zeros((T, nr, K)); gD = np.zeros((T, nr, K))
    ll = np.zeros(T)
    chunk = (N + T - 1) // T
    for t in nb.prange(T):
        a = np.empty(K)
        b = np.empty(K)
        for o in range(t * chunk, min(N, (t + 1) * chunk)):
            i, j, f = mem[o], rc[o], frac[o]
            ey, en = _utils(Pm, Pp, M, D, w2, G, i, j, f, a, b)
            sz = s[o] * beta * (ey - en)
            lc = log_ndtr(sz)
            ll[t] += lc
            score = s[o] * math.exp(-0.5 * sz * sz - LOG_SQRT_2PI - lc)
            for k in range(K):
                gA = -beta * score * ey * w2[k] * a[k]
                gB = beta * score * en * w2[k] * b[k]
                gx = gA + gB
                gPm[t, i, k] += gx * (1.0 - f)
                gPp[t, i, k] += gx * f
                gM[t, j, k] -= gx
                gD[t, j, k] += gA - gB
    return ll.sum(), gPm.sum(0), gPp.sum(0), gM.sum(0), gD.sum(0)


@nb.njit(parallel=True, cache=True)
def block_rollcalls(Pm, Pp, M, D, beta, w2, rc, mem, s, frac, G, T):
    """Per roll call: score g (n_rc, 2K) and Fisher information H (n_rc, 2K, 2K) for (midpoint, spread)."""
    N = rc.shape[0]
    K = Pm.shape[1]
    P = 2 * K
    nr = M.shape[0]
    g = np.zeros((T, nr, P))
    H = np.zeros((T, nr, P, P))
    chunk = (N + T - 1) // T
    for t in nb.prange(T):
        a = np.empty(K); b = np.empty(K); J = np.empty(P)
        for o in range(t * chunk, min(N, (t + 1) * chunk)):
            j = rc[o]
            ey, en = _utils(Pm, Pp, M, D, w2, G, mem[o], j, frac[o], a, b)
            z = beta * (ey - en)
            sz = s[o] * z
            lpdf = -0.5 * z * z - LOG_SQRT_2PI
            score = s[o] * math.exp(lpdf - log_ndtr(sz))
            info = math.exp(2.0 * lpdf - log_ndtr(z) - log_ndtr(-z))
            for k in range(K):
                J[k] = beta * (ey * w2[k] * a[k] - en * w2[k] * b[k])          # dz / d midpoint
                J[K + k] = -beta * (ey * w2[k] * a[k] + en * w2[k] * b[k])     # dz / d spread
            for p in range(P):
                g[t, j, p] += score * J[p]
                for q in range(P):
                    H[t, j, p, q] += info * J[p] * J[q]
    return g.sum(0), H.sum(0)


@nb.njit(parallel=True, cache=True)
def block_members(Pm, Pp, M, D, beta, w2, rc, mem, s, frac, G, T):
    """Per member: score and Fisher information for (start position, end position)."""
    N = rc.shape[0]
    K = Pm.shape[1]
    P = 2 * K
    nm = Pm.shape[0]
    g = np.zeros((T, nm, P))
    H = np.zeros((T, nm, P, P))
    chunk = (N + T - 1) // T
    for t in nb.prange(T):
        a = np.empty(K); b = np.empty(K); J = np.empty(P)
        for o in range(t * chunk, min(N, (t + 1) * chunk)):
            i, f = mem[o], frac[o]
            ey, en = _utils(Pm, Pp, M, D, w2, G, i, rc[o], f, a, b)
            z = beta * (ey - en)
            sz = s[o] * z
            lpdf = -0.5 * z * z - LOG_SQRT_2PI
            score = s[o] * math.exp(lpdf - log_ndtr(sz))
            info = math.exp(2.0 * lpdf - log_ndtr(z) - log_ndtr(-z))
            for k in range(K):
                dx = -beta * (ey * w2[k] * a[k] - en * w2[k] * b[k])           # dz / d position
                J[k] = dx * (1.0 - f)
                J[K + k] = dx * f
            for p in range(P):
                g[t, i, p] += score * J[p]
                for q in range(P):
                    H[t, i, p, q] += info * J[p] * J[q]
    return g.sum(0), H.sum(0)


@nb.njit(parallel=True, cache=True)
def block_global(Pm, Pp, M, D, beta, w2, rc, mem, s, frac, G, T):
    """Score and information for (beta, w2[1], ..., w2[K-1])."""
    N = rc.shape[0]
    K = Pm.shape[1]
    g = np.zeros((T, K))
    H = np.zeros((T, K, K))
    chunk = (N + T - 1) // T
    for t in nb.prange(T):
        a = np.empty(K); b = np.empty(K); J = np.empty(K)
        for o in range(t * chunk, min(N, (t + 1) * chunk)):
            ey, en = _utils(Pm, Pp, M, D, w2, G, mem[o], rc[o], frac[o], a, b)
            z = beta * (ey - en)
            sz = s[o] * z
            lpdf = -0.5 * z * z - LOG_SQRT_2PI
            score = s[o] * math.exp(lpdf - log_ndtr(sz))
            info = math.exp(2.0 * lpdf - log_ndtr(z) - log_ndtr(-z))
            J[0] = ey - en
            for k in range(1, K):
                J[k] = beta * (-0.5 * a[k] * a[k] * ey + 0.5 * b[k] * b[k] * en)
            for p in range(K):
                g[t, p] += score * J[p]
                for q in range(K):
                    H[t, p, q] += info * J[p] * J[q]
    return g.sum(0), H.sum(0)


@nb.njit(parallel=True, cache=True)
def predict(Pm, Pp, M, D, beta, w2, rc, mem, s, frac, G, T):
    """Per vote: z (the utility difference) and log-likelihood."""
    N = rc.shape[0]
    K = Pm.shape[1]
    z = np.empty(N)
    lc = np.empty(N)
    chunk = (N + T - 1) // T
    for t in nb.prange(T):
        a = np.empty(K); b = np.empty(K)
        for o in range(t * chunk, min(N, (t + 1) * chunk)):
            ey, en = _utils(Pm, Pp, M, D, w2, G, mem[o], rc[o], frac[o], a, b)
            z[o] = beta * (ey - en)
            lc[o] = log_ndtr(s[o] * z[o])
    return z, lc
