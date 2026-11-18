---
status: accepted
---

# climb_and_descent is compiled with numba, to the same bits

`climb_and_descent` (the spike rule, `_smooth_noisy` and the 10 m dead-band, ADR-0048 and ADR-0062) runs on every segment the destination tile search contracts and on every candidate walk it weighs (ADR-0067), and again for the Catalog's gain, the similarity facts and the serving export. In a dense Slovenia tile it was 62 % of the tile search's CPU: 100,972 calls. The numpy code spends most of that in the overhead of some forty small numpy calls per walk and in a Python loop over the vertices.

**The rule.** `climb_kernel` does the same work in one numba-compiled loop (`@njit`, no `fastmath`), with every float operation in the order the numpy code does it, so it gives the same bits, not merely close numbers. `similarity.climb_and_descent` calls it where numba is installed and keeps the numpy code, which runs where numba is not and which the tests compare it with. The haversine steps stay in numpy (`_step_m`), so the sines and cosines are numpy's own.

Two numpy functions round `a * b + c` once (a fused multiply-add) on some builds and twice on others: `np.interp` and the fill of `np.arange`, numpy 2.5 built for arm64 (macOS) fuses, a build for a CPU whose baseline has no FMA (x86-64) cannot. `climb_kernel` reads which off numpy at import, on probes where the two differ, and does the same (LLVM's `llvm.fma`, exact on every target). Should numpy match neither, the numpy code runs.

**What it gives.** On the 76,450 calls an Écrins ingest makes (recorded on the commit before): the same results to the bit, and about 6× less CPU per call (823 → 128 µs, of which ~35 µs is still `_step_m`). One million random walks with NaN gaps, flat stretches, spikes, steep shores and vertices at one spot: the same bits. An Écrins build (ingest, sample-dem, build-similarity) writes the same Catalog, GeometryStore, TrailGraph and similarity arrays as before, with less CPU: ingest 246 → 195 s, build-similarity 94 → 64 s.

**Cost.** numba and llvmlite join the dependencies (also in the web image). The kernel compiles on its first call in each process, a few seconds; it is not cached on disk, because the web image installs the package as root and runs as another user.

**Considered Options**:

- A vectorised numpy rewrite: rejected. The spike rule is a stack that looks back after every drop, which numpy cannot do without changing the order of the float operations.
- numba with `fastmath`: rejected; it reorders and fuses float operations, so the gain would change in the last bits and the Catalog with it.
- Cache the compiled kernel (`cache=True`): rejected for now; a read-only install has nowhere to write it, and the compile is seconds against hours.
