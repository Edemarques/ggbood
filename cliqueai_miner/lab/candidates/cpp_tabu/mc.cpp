// Maximum clique local search engine (CliqueAI miner, candidate "cpp_tabu").
// Built as a shared library and called from solver.py through ctypes.
//
// State is kept entirely in bitsets: C (clique), PA (vertices adjacent to all of C),
// OM (vertices adjacent to all of C but one).  Adding a vertex updates PA/OM in O(W)
// words; removing vertices recomputes them in O(|C| * W) with a ones/twos accumulator.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <deque>
#include <thread>
#include <vector>

namespace mc {
using u64 = uint64_t;
using u32 = uint32_t;
using Clock = std::chrono::steady_clock;

struct Rng {
    u64 s[4];
    static u64 splitmix(u64& x) {
        u64 z = (x += 0x9e3779b97f4a7c15ULL);
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
        return z ^ (z >> 31);
    }
    explicit Rng(u64 seed) {
        for (int i = 0; i < 4; i++) s[i] = splitmix(seed);
    }
    static inline u64 rotl(u64 x, int k) { return (x << k) | (x >> (64 - k)); }
    inline u64 next() {
        u64 r = rotl(s[1] * 5, 7) * 9;
        u64 t = s[1] << 17;
        s[2] ^= s[0];
        s[3] ^= s[1];
        s[1] ^= s[2];
        s[0] ^= s[3];
        s[2] ^= t;
        s[3] = rotl(s[3], 45);
        return r;
    }
    inline u32 below(u32 n) { return (u32)(((next() >> 32) * (u64)n) >> 32); }
    inline double uni() { return (next() >> 11) * 0x1.0p-53; }
};

struct Timer {
    Clock::time_point t0, end;
    explicit Timer(double seconds) {
        t0 = Clock::now();
        if (seconds < 0) seconds = 0;
        if (seconds > 1e6) seconds = 1e6;
        end = t0 + std::chrono::nanoseconds((long long)(seconds * 1e9));
    }
    inline bool expired() const { return Clock::now() >= end; }
    inline double elapsed() const { return std::chrono::duration<double>(Clock::now() - t0).count(); }
};

static inline int popc(u64 x) { return __builtin_popcountll(x); }
static inline int ctz(u64 x) { return __builtin_ctzll(x); }
static inline int select64(u64 x, int r) {
    int base = 0;
    int c = popc(x & 0xFFFFFFFFULL);
    if (r >= c) {
        r -= c;
        x >>= 32;
        base += 32;
    }
    c = popc(x & 0xFFFFULL);
    if (r >= c) {
        r -= c;
        x >>= 16;
        base += 16;
    }
    c = popc(x & 0xFFULL);
    if (r >= c) {
        r -= c;
        x >>= 8;
        base += 8;
    }
    while (r--) x &= x - 1;
    return base + ctz(x);
}

struct Graph {
    int n = 0, W = 1;  // W = padded number of words (power of two)
    std::vector<u64> A, NA, valid;
    std::vector<int> deg;
    inline const u64* arow(int v) const { return A.data() + (size_t)v * W; }
    inline const u64* nrow(int v) const { return NA.data() + (size_t)v * W; }
    inline bool adj(int u, int v) const { return (A[(size_t)u * W + (v >> 6)] >> (v & 63)) & 1ULL; }

    void build(int n_, const int64_t* off, const int32_t* nb) {
        n = n_;
        int w0 = (n + 63) / 64;
        W = 1;
        while (W < w0) W <<= 1;
        A.assign((size_t)n * W, 0);
        NA.assign((size_t)n * W, 0);
        valid.assign(W, 0);
        for (int v = 0; v < n; v++) valid[v >> 6] |= 1ULL << (v & 63);
        for (int u = 0; u < n; u++) {
            for (int64_t k = off[u]; k < off[u + 1]; k++) {
                int v = nb[k];
                if (v < 0 || v >= n || v == u) continue;
                A[(size_t)u * W + (v >> 6)] |= 1ULL << (v & 63);
                A[(size_t)v * W + (u >> 6)] |= 1ULL << (u & 63);
            }
        }
        deg.assign(n, 0);
        for (int u = 0; u < n; u++) {
            const u64* r = arow(u);
            u64* q = NA.data() + (size_t)u * W;
            int d = 0;
            for (int w = 0; w < W; w++) {
                d += popc(r[w]);
                q[w] = valid[w] & ~r[w];
            }
            q[u >> 6] &= ~(1ULL << (u & 63));
            deg[u] = d;
        }
    }
};

struct Pool {
    int best = 0;
    size_t cap = 128;
    std::vector<std::vector<int>> cls;
    std::vector<u64> hs;
    double tBest = 0;
    std::vector<double> trace;
    inline bool offer(const int* Cl, int k, u64 h, const Timer& T) {
        if (k < best) return false;
        bool nb = false;
        if (k > best) {
            best = k;
            cls.clear();
            hs.clear();
            tBest = T.elapsed();
            trace.push_back(tBest);
            trace.push_back(k);
            nb = true;
        }
        if (cls.size() >= cap) return nb;
        for (u64 x : hs)
            if (x == h) return nb;
        hs.push_back(h);
        cls.emplace_back(Cl, Cl + k);
        return nb;
    }
};

struct Params {
    int algo = 0;
    double p[16] = {0};
    int target = 0;  // stop when reached (0 = never)
};

template <int W, bool SL>
struct Eng {
    const Graph& G;
    const int n;
    const u64* A;
    const u64* NA;
    Rng& rng;
    Pool& pool;
    const Timer& T;
    const Params& P;
    long long steps = 0;
    alignas(64) u64 Cb[W], PA[W], OM[W], valid[W];
    static constexpr int MAXPL = 17;
    alignas(64) u64 pln[SL ? MAXPL : 1][W];
    int npl = 1;
    std::vector<int> Cl, Cpos;
    int csz = 0;
    std::vector<u64> zob;
    u64 h = 0;

    Eng(const Graph& g, Rng& r, Pool& pl, const Timer& t, const Params& p)
        : G(g), n(g.n), A(g.A.data()), NA(g.NA.data()), rng(r), pool(pl), T(t), P(p) {
        Cl.assign(n + 1, 0);
        Cpos.assign(n + 1, -1);
        zob.resize(n);
        for (int v = 0; v < n; v++) zob[v] = rng.next();
        for (int w = 0; w < W; w++) {
            valid[w] = g.valid[w];
            Cb[w] = 0;
            PA[w] = valid[w];
            OM[w] = 0;
        }
        if (SL)
            for (int j = 0; j < MAXPL; j++)
                for (int w = 0; w < W; w++) pln[j][w] = 0;
    }
    static inline bool tst(const u64* S, int v) { return (S[v >> 6] >> (v & 63)) & 1ULL; }
    static inline void setb(u64* S, int v) { S[v >> 6] |= 1ULL << (v & 63); }
    static inline void clrb(u64* S, int v) { S[v >> 6] &= ~(1ULL << (v & 63)); }
    inline const u64* arow(int v) const { return A + (size_t)v * W; }
    inline const u64* nrow(int v) const { return NA + (size_t)v * W; }
    static inline bool any(const u64* S) {
        u64 x = 0;
        for (int w = 0; w < W; w++) x |= S[w];
        return x != 0;
    }
    static inline int count(const u64* S) {
        int c = 0;
        for (int w = 0; w < W; w++) c += popc(S[w]);
        return c;
    }
    static inline int nth(const u64* S, int r) {
        for (int w = 0; w < W; w++) {
            int c = popc(S[w]);
            if (r < c) return (w << 6) + select64(S[w], r);
            r -= c;
        }
        return -1;
    }
    inline int pick(const u64* S) {
        int c = count(S);
        if (!c) return -1;
        return nth(S, (int)rng.below((u32)c));
    }
    inline int pickGreedy(const u64* S) {
        int best = -1, cnt = 0, pk = -1;
        for (int w0 = 0; w0 < W; w0++) {
            u64 y = S[w0];
            while (y) {
                int v = (w0 << 6) + ctz(y);
                y &= y - 1;
                const u64* a = arow(v);
                int c = 0;
                for (int w = 0; w < W; w++) c += popc(a[w] & S[w]);
                if (c > best) {
                    best = c;
                    cnt = 1;
                    pk = v;
                } else if (c == best && rng.below(++cnt) == 0)
                    pk = v;
            }
        }
        return pk;
    }
    inline void derive() {
        if constexpr (SL) {
            u64 nz[W];
            for (int w = 0; w < W; w++) nz[w] = 0;
            for (int j = 1; j < npl; j++)
                for (int w = 0; w < W; w++) nz[w] |= pln[j][w];
            for (int w = 0; w < W; w++) {
                PA[w] = valid[w] & ~Cb[w] & ~pln[0][w] & ~nz[w];
                OM[w] = pln[0][w] & ~nz[w];
            }
        }
    }
    inline void inc(const u64* x) {
        u64 c[W];
        for (int w = 0; w < W; w++) c[w] = x[w];
        for (int j = 0; j < npl; j++)
            for (int w = 0; w < W; w++) {
                u64 t = pln[j][w] & c[w];
                pln[j][w] ^= c[w];
                c[w] = t;
            }
    }
    inline void dec(const u64* x) {
        u64 b[W];
        for (int w = 0; w < W; w++) b[w] = x[w];
        for (int j = 0; j < npl; j++)
            for (int w = 0; w < W; w++) {
                u64 t = ~pln[j][w] & b[w];
                pln[j][w] ^= b[w];
                b[w] = t;
            }
    }
    // out = non-clique vertices with exactly k non-neighbours in C
    inline void missEq(int k, u64* out) {
        if constexpr (SL) {
            if (k >= (1 << npl)) {
                for (int w = 0; w < W; w++) out[w] = 0;
                return;
            }
            for (int w = 0; w < W; w++) out[w] = valid[w] & ~Cb[w];
            for (int j = 0; j < npl; j++)
                for (int w = 0; w < W; w++) out[w] &= ((k >> j) & 1) ? pln[j][w] : ~pln[j][w];
        } else {
            u64 b0[W], b1[W], b2[W], ov[W];
            for (int w = 0; w < W; w++) b0[w] = b1[w] = b2[w] = ov[w] = 0;
            for (int i = 0; i < csz; i++) {
                const u64* x = nrow(Cl[i]);
                for (int w = 0; w < W; w++) {
                    u64 c0 = b0[w] & x[w];
                    b0[w] ^= x[w];
                    u64 c1 = b1[w] & c0;
                    b1[w] ^= c0;
                    ov[w] |= b2[w] & c1;
                    b2[w] ^= c1;
                }
            }
            for (int w = 0; w < W; w++) {
                u64 m0 = (k & 1) ? b0[w] : ~b0[w];
                u64 m1 = (k & 2) ? b1[w] : ~b1[w];
                u64 m2 = (k & 4) ? b2[w] : ~b2[w];
                out[w] = (k < 8) ? (m0 & m1 & m2 & ~ov[w] & valid[w] & ~Cb[w]) : 0;
            }
        }
    }
    inline void add(int v) {  // v must be in PA
        if constexpr (SL) {
            while (npl < MAXPL && csz + 1 >= (1 << npl)) npl++;
            inc(nrow(v));
            setb(Cb, v);
            Cpos[v] = csz;
            Cl[csz++] = v;
            h += zob[v];
            derive();
            return;
        }
        const u64* a = arow(v);
        for (int w = 0; w < W; w++) {
            u64 p = PA[w], o = OM[w], x = a[w];
            OM[w] = (o & x) | (p & ~x);
            PA[w] = p & x;
        }
        clrb(OM, v);
        setb(Cb, v);
        Cpos[v] = csz;
        Cl[csz++] = v;
        h += zob[v];
    }
    inline void remRaw(int u) {
        if constexpr (SL) dec(nrow(u));
        clrb(Cb, u);
        int p = Cpos[u];
        int last = Cl[--csz];
        Cl[p] = last;
        Cpos[last] = p;
        Cpos[u] = -1;
        h -= zob[u];
    }
    inline void recompute() {
        if constexpr (SL) {
            derive();
            return;
        }
        u64 ones[W], twos[W];
        for (int w = 0; w < W; w++) ones[w] = twos[w] = 0;
        for (int i = 0; i < csz; i++) {
            const u64* x = nrow(Cl[i]);
            for (int w = 0; w < W; w++) {
                twos[w] |= ones[w] & x[w];
                ones[w] |= x[w];
            }
        }
        for (int w = 0; w < W; w++) {
            PA[w] = valid[w] & ~ones[w] & ~Cb[w];
            OM[w] = ones[w] & ~twos[w];
        }
    }
    inline int partner(int v) const {  // v in OM: the unique clique vertex not adjacent to v
        const u64* x = nrow(v);
        for (int w = 0; w < W; w++) {
            u64 y = x[w] & Cb[w];
            if (y) return (w << 6) + ctz(y);
        }
        return -1;
    }
    inline void swapIn(int v) {
        int u = partner(v);
        remRaw(u);
        recompute();
        add(v);
    }
    inline void clearC() {
        while (csz) remRaw(Cl[csz - 1]);
        recompute();
    }
    inline void offer() { pool.offer(Cl.data(), csz, h, T); }
    inline bool done() const { return P.target && pool.best >= P.target; }

    // -----------------------------------------------------------------------
    // DLS-MC (Pullan & Hoos 2006) with pd = 1 (no penalties): random expansion,
    // plateau search with per-phase tabu, perturbation = add random vertex.
    void run_dls() {
        alignas(64) u64 startM[W], remM[W], tmp[W];
        const int perturbMode = (int)P.p[1];
        const int expandMode = (int)P.p[2];
        const double lowMissProb = P.p[3];
        const double pk3 = P.p[4];
        const double pk4 = P.p[5];
        const long long ptenure = (long long)P.p[6];
        long long cyc = 0;
        std::vector<long long> lastDrop(n, -(1LL << 40));
        std::deque<std::pair<int, long long>> dq;
        alignas(64) u64 ptabu[W];
        for (int w = 0; w < W; w++) ptabu[w] = 0;
        {
            int v = (int)rng.below(n);
            add(v);
        }
        int chk = 0;
        while (true) {
            if (++chk >= 16) {
                chk = 0;
                if (T.expired()) break;
            }
            if (done()) break;
            while (any(PA)) {
                add(expandMode ? pickGreedy(PA) : pick(PA));
                steps++;
            }
            offer();
#ifdef MC_HOOK
            MC_HOOK(this);
#endif
            // plateau
            for (int w = 0; w < W; w++) {
                startM[w] = Cb[w];
                remM[w] = 0;
            }
            while (true) {
                for (int w = 0; w < W; w++) tmp[w] = OM[w] & ~remM[w];
                int v = pick(tmp);
                if (v < 0) break;
                int u = partner(v);
                remRaw(u);
                recompute();
                add(v);
                setb(remM, u);
                steps++;
                if (any(PA)) break;
                offer();
                u64 x = 0;
                for (int w = 0; w < W; w++) x |= Cb[w] & startM[w];
                if (!x) break;
            }
            if (any(PA)) continue;
            // perturbation
            int v;
            if (csz >= n) break;
            double r = rng.uni();
            int kk = r < lowMissProb ? 2 : r < lowMissProb + pk3 ? 3 : r < lowMissProb + pk3 + pk4 ? 4 : 0;
            if (kk) {
                missEq(kk, tmp);
                if (ptenure > 0) {
                    while (!dq.empty() && cyc - dq.front().second >= ptenure) {
                        int z = dq.front().first;
                        dq.pop_front();
                        if (cyc - lastDrop[z] >= ptenure) clrb(ptabu, z);
                    }
                    for (int w = 0; w < W; w++) tmp[w] &= ~ptabu[w];
                }
                v = pick(tmp);
                if (v < 0) {
                    for (int w = 0; w < W; w++) tmp[w] = valid[w] & ~Cb[w];
                    v = pick(tmp);
                }
            } else if (perturbMode == 0) {
                for (int w = 0; w < W; w++) tmp[w] = valid[w] & ~Cb[w];
                v = pick(tmp);
            } else {
                v = (int)rng.below(n);
                while (tst(Cb, v)) v = (int)rng.below(n);
            }
            const u64* x = nrow(v);
            cyc++;
            for (int i = csz - 1; i >= 0; i--)
                if (tst(x, Cl[i])) {
                    int z = Cl[i];
                    remRaw(z);
                    if (ptenure > 0) {
                        lastDrop[z] = cyc;
                        setb(ptabu, z);
                        dq.emplace_back(z, cyc);
                    }
                }
            recompute();
            add(v);
        }
    }

    // -----------------------------------------------------------------------
    // Multi-neighbourhood tabu search (Wu, Hao, Glover 2012), unweighted.
    void run_tabu() {
        const int tlS = (int)P.p[0];
        const double tlSom = P.p[1];
        const int tlSr = (int)P.p[2];
        const int tlD = (int)P.p[3];
        const int tlDr = (int)P.p[4];
        const long long L = (long long)P.p[5];
        alignas(64) u64 tabuM[W], tmp[W];
        std::vector<long long> expiry(n, 0);
        std::vector<int> tl;  // currently tabu vertices
        long long it = 0, noImp = 0;
        int localBest = 0;
        auto restart = [&]() {
            clearC();
            for (int w = 0; w < W; w++) tabuM[w] = 0;
            tl.clear();
            noImp = 0;
            localBest = 0;
            add((int)rng.below(n));
        };
        restart();
        while (true) {
            if ((++steps & 255) == 0 && T.expired()) break;
            if (done()) break;
            ++it;
            // expire tabu
            for (size_t i = 0; i < tl.size();) {
                int v = tl[i];
                if (expiry[v] <= it) {
                    clrb(tabuM, v);
                    tl[i] = tl.back();
                    tl.pop_back();
                } else
                    i++;
            }
            int v = -1;
            if (any(PA)) {
                if (csz + 1 > pool.best)
                    v = pick(PA);
                else {
                    for (int w = 0; w < W; w++) tmp[w] = PA[w] & ~tabuM[w];
                    v = pick(tmp);
                }
            }
            if (v >= 0) {
                add(v);
            } else {
                for (int w = 0; w < W; w++) tmp[w] = OM[w] & ~tabuM[w];
                int om = count(tmp);
                if (om) {
                    v = nth(tmp, (int)rng.below(om));
                    int u = partner(v);
                    remRaw(u);
                    recompute();
                    add(v);
                    expiry[u] = it + tlS + rng.below((u32)(tlSr + tlSom * om) + 1);
                    setb(tabuM, u);
                    tl.push_back(u);
                } else if (csz) {
                    int u = Cl[rng.below(csz)];
                    remRaw(u);
                    recompute();
                    expiry[u] = it + tlD + rng.below(tlDr + 1);
                    if (!tst(tabuM, u)) tl.push_back(u);
                    setb(tabuM, u);
                }
            }
            if (!any(PA)) offer();
            if (csz > localBest) {
                localBest = csz;
                noImp = 0;
            } else if (++noImp > L) {
                restart();
            }
            if (!csz) restart();
        }
    }

    void run() {
        if (P.algo == 0)
            run_dls();
        else
            run_tabu();
    }
};

// ---------------------------------------------------------------------------
// k-fixed weighted local search (NuMVC, Cai et al. 2013, run on the complement):
// S has fixed size t, non-adjacent pairs inside S are weighted conflicts.
template <int W>
struct Kfix {
    const Graph& G;
    const int n;
    Rng& rng;
    Pool& pool;
    const Timer& T;
    const Params& P;
    long long steps = 0;
    std::vector<int> nnOff, nn;
    std::vector<int> wt;  // n*n weights of non-edges
    std::vector<long long> score;
    std::vector<int> cnt, cc;
    std::vector<long long> age;
    std::vector<int> Sl, Spos;
    alignas(64) u64 Sb[W];
    int t = 0;
    long long nConf = 0;  // number of conflicting pairs in S
    long long totalW = 0, nEdges = 0;
    u64 h = 0;
    std::vector<u64> zob;

    Kfix(const Graph& g, Rng& r, Pool& pl, const Timer& tm, const Params& p)
        : G(g), n(g.n), rng(r), pool(pl), T(tm), P(p) {
        nnOff.assign(n + 1, 0);
        for (int v = 0; v < n; v++) {
            const u64* x = G.nrow(v);
            for (int w = 0; w < W; w++) {
                u64 y = x[w];
                while (y) {
                    nn.push_back((w << 6) + ctz(y));
                    y &= y - 1;
                }
            }
            nnOff[v + 1] = (int)nn.size();
        }
        nEdges = (long long)nn.size() / 2;
        wt.assign((size_t)n * n, 1);
        totalW = nEdges;
        score.assign(n, 0);
        cnt.assign(n, 0);
        cc.assign(n, 1);
        age.assign(n, 0);
        Sl.assign(n + 1, 0);
        Spos.assign(n, -1);
        for (int w = 0; w < W; w++) Sb[w] = 0;
        zob.resize(n);
        for (int v = 0; v < n; v++) zob[v] = rng.next();
    }
    inline bool inS(int v) const { return Spos[v] >= 0; }
    inline void enter(int v) {
        nConf += cnt[v];
        Spos[v] = t;
        Sl[t++] = v;
        Sb[v >> 6] |= 1ULL << (v & 63);
        h += zob[v];
        const int* wr = &wt[(size_t)v * n];
        for (int k = nnOff[v]; k < nnOff[v + 1]; k++) {
            int x = nn[k];
            score[x] += wr[x];
            cnt[x]++;
        }
    }
    inline void leave(int v) {
        int p = Spos[v];
        int last = Sl[--t];
        Sl[p] = last;
        Spos[last] = p;
        Spos[v] = -1;
        Sb[v >> 6] &= ~(1ULL << (v & 63));
        h -= zob[v];
        nConf -= cnt[v];
        const int* wr = &wt[(size_t)v * n];
        for (int k = nnOff[v]; k < nnOff[v + 1]; k++) {
            int x = nn[k];
            score[x] -= wr[x];
            cnt[x]--;
        }
    }
    int pickEnter(int forbid) {
        long long bs = (1LL << 62);
        long long ba = (1LL << 62);
        int bv = -1;
        for (int v = 0; v < n; v++) {
            if (Spos[v] >= 0 || v == forbid) continue;
            long long s = score[v];
            if (s < bs || (s == bs && age[v] < ba)) {
                bs = s;
                ba = age[v];
                bv = v;
            }
        }
        return bv;
    }
    // record S if it is a clique (nConf == 0); extend to maximal for the pool
    void record() {
        // greedy extension (on a copy)
        u64 cand[W];
        for (int w = 0; w < W; w++) cand[w] = G.valid[w] & ~Sb[w];
        for (int i = 0; i < t; i++) {
            const u64* a = G.arow(Sl[i]);
            for (int w = 0; w < W; w++) cand[w] &= a[w];
        }
        bool anyc = false;
        for (int w = 0; w < W; w++) anyc |= cand[w] != 0;
        if (!anyc) {
            pool.offer(Sl.data(), t, h, T);
            return;
        }
        std::vector<int> cl(Sl.begin(), Sl.begin() + t);
        u64 hh = h;
        while (true) {
            int c = 0;
            for (int w = 0; w < W; w++) c += popc(cand[w]);
            if (!c) break;
            int r = (int)rng.below(c), v = -1;
            for (int w = 0; w < W; w++) {
                int k = popc(cand[w]);
                if (r < k) {
                    v = (w << 6) + select64(cand[w], r);
                    break;
                }
                r -= k;
            }
            cl.push_back(v);
            hh += zob[v];
            const u64* a = G.arow(v);
            for (int w = 0; w < W; w++) cand[w] &= a[w];
        }
        pool.offer(cl.data(), (int)cl.size(), hh, T);
    }
    void forget(double rho) {
        // w := floor(rho * w) (min 1), recompute scores
        totalW = 0;
        for (int v = 0; v < n; v++)
            for (int k = nnOff[v]; k < nnOff[v + 1]; k++) {
                int x = nn[k];
                if (x < v) continue;
                int nw = (int)(rho * wt[(size_t)v * n + x]);
                if (nw < 1) nw = 1;
                wt[(size_t)v * n + x] = wt[(size_t)x * n + v] = nw;
                totalW += nw;
            }
        std::fill(score.begin(), score.end(), 0);
        for (int i = 0; i < t; i++) {
            int v = Sl[i];
            const int* wr = &wt[(size_t)v * n];
            for (int k = nnOff[v]; k < nnOff[v + 1]; k++) score[nn[k]] += wr[nn[k]];
        }
    }
    void run() {
        const double gammaF = P.p[0] > 0 ? P.p[0] : 0.5;  // gamma = gammaF * n
        const double rho = P.p[1] > 0 ? P.p[1] : 0.3;
        const double gamma = gammaF * n;
        // initial greedy random clique
        {
            u64 cand[W];
            for (int w = 0; w < W; w++) cand[w] = G.valid[w];
            while (true) {
                int c = 0;
                for (int w = 0; w < W; w++) c += popc(cand[w]);
                if (!c) break;
                int r = (int)rng.below(c), v = -1;
                for (int w = 0; w < W; w++) {
                    int k = popc(cand[w]);
                    if (r < k) {
                        v = (w << 6) + select64(cand[w], r);
                        break;
                    }
                    r -= k;
                }
                enter(v);
                const u64* a = G.arow(v);
                for (int w = 0; w < W; w++) cand[w] &= a[w];
            }
        }
        record();
        if (t >= n) return;
        int lastLeft = -1;
        {
            int v = pickEnter(-1);
            enter(v);
            age[v] = 0;
        }
        std::vector<int> ca, cb;
        while (true) {
            if ((++steps & 63) == 0 && T.expired()) break;
            if (P.target && pool.best >= P.target) break;
            if (nConf == 0) {
                record();
                if (t >= n) break;
                int v = pickEnter(-1);
                enter(v);
                age[v] = steps;
                cc[v] = 0;
                for (int k = nnOff[v]; k < nnOff[v + 1]; k++) cc[nn[k]] = 1;
                continue;
            }
            // enter: best vertex outside S
            int v = pickEnter(lastLeft);
            if (v < 0) break;
            enter(v);
            age[v] = steps;
            cc[v] = 0;
            for (int k = nnOff[v]; k < nnOff[v + 1]; k++) cc[nn[k]] = 1;
            // leave: endpoint of a random conflict pair
            ca.clear();
            cb.clear();
            for (int i = 0; i < t; i++) {
                int a = Sl[i];
                const u64* x = G.nrow(a);
                for (int w = 0; w < W; w++) {
                    u64 y = x[w] & Sb[w];
                    while (y) {
                        int b = (w << 6) + ctz(y);
                        y &= y - 1;
                        if (b > a) {
                            ca.push_back(a);
                            cb.push_back(b);
                        }
                    }
                }
            }
            int e = (int)rng.below((u32)ca.size());
            int a = ca[e], b = cb[e];
            int y;
            bool oka = cc[a] != 0, okb = cc[b] != 0;
            if (oka && !okb)
                y = a;
            else if (okb && !oka)
                y = b;
            else if (score[a] != score[b])
                y = score[a] > score[b] ? a : b;
            else
                y = age[a] < age[b] ? a : b;
            leave(y);
            age[y] = steps;
            lastLeft = y;
            for (int k = nnOff[y]; k < nnOff[y + 1]; k++) cc[nn[k]] = 1;
            // weights of remaining conflicts
            for (int i = 0; i < t; i++) {
                int a2 = Sl[i];
                const u64* x = G.nrow(a2);
                for (int w = 0; w < W; w++) {
                    u64 yy = x[w] & Sb[w];
                    while (yy) {
                        int b2 = (w << 6) + ctz(yy);
                        yy &= yy - 1;
                        if (b2 > a2) {
                            wt[(size_t)a2 * n + b2]++;
                            wt[(size_t)b2 * n + a2]++;
                            score[a2]++;
                            score[b2]++;
                            totalW++;
                        }
                    }
                }
            }
            if ((double)totalW > gamma * (double)nEdges) forget(rho);
        }
    }
};

static void greedy_extend(const Graph& G, std::vector<int>& cl, Rng& rng) {
    const int W = G.W;
    std::vector<u64> cand(G.valid);
    for (int v : cl) {
        const u64* r = G.arow(v);
        for (int w = 0; w < W; w++) cand[w] &= r[w];
    }
    for (int v : cl) cand[v >> 6] &= ~(1ULL << (v & 63));
    std::vector<int> pa;
    while (true) {
        pa.clear();
        for (int w = 0; w < W; w++) {
            u64 x = cand[w];
            while (x) {
                pa.push_back((w << 6) + ctz(x));
                x &= x - 1;
            }
        }
        if (pa.empty()) break;
        int best = -1, cnt = 0, pk = pa[0];
        for (int v : pa) {
            const u64* r = G.arow(v);
            int c = 0;
            for (int w = 0; w < W; w++) c += popc(r[w] & cand[w]);
            if (c > best) {
                best = c;
                cnt = 1;
                pk = v;
            } else if (c == best && rng.below(++cnt) == 0)
                pk = v;
        }
        cl.push_back(pk);
        const u64* r = G.arow(pk);
        for (int w = 0; w < W; w++) cand[w] &= r[w];
    }
}

static bool is_clique(const Graph& G, const std::vector<int>& cl) {
    for (size_t i = 0; i < cl.size(); i++) {
        if (cl[i] < 0 || cl[i] >= G.n) return false;
        for (size_t j = i + 1; j < cl.size(); j++)
            if (cl[i] == cl[j] || !G.adj(cl[i], cl[j])) return false;
    }
    return true;
}

template <int W>
static long long run_eng(const Graph& G, Rng& rng, Pool& pool, const Timer& T, const Params& P) {
    long long s;
    if (P.algo == 3) {
        Kfix<W>* e = new Kfix<W>(G, rng, pool, T, P);
        e->run();
        s = e->steps;
        delete e;
    } else if (P.algo == 2) {
        Params Q = P;
        Q.algo = 0;
        Eng<W, true>* e = new Eng<W, true>(G, rng, pool, T, Q);
        e->run();
        s = e->steps;
        delete e;
    } else {
        Eng<W, false>* e = new Eng<W, false>(G, rng, pool, T, P);
        e->run();
        s = e->steps;
        delete e;
    }
    return s;
}

}  // namespace mc

namespace mc {
static long long dispatch(const Graph& G, Rng& rng, Pool& pool, const Timer& T, const Params& P) {
    switch (G.W) {
        case 1: return run_eng<1>(G, rng, pool, T, P);
        case 2: return run_eng<2>(G, rng, pool, T, P);
        case 4: return run_eng<4>(G, rng, pool, T, P);
        case 8: return run_eng<8>(G, rng, pool, T, P);
        case 16: return run_eng<16>(G, rng, pool, T, P);
        case 32: return run_eng<32>(G, rng, pool, T, P);
        case 64: return run_eng<64>(G, rng, pool, T, P);
        case 128: return run_eng<128>(G, rng, pool, T, P);
        case 256: return run_eng<256>(G, rng, pool, T, P);
        default: return 0;  // too large: greedy only
    }
}
}  // namespace mc

extern "C" {

// Returns clique size written into out (capacity >= n). stats (>= 8 doubles):
// [0]=steps [1]=time of best [2]=best size [3]=pool size [4]=elapsed [5]=#trace entries
// threads > 1 runs independent searches in parallel and merges their pools.
int mc_solve2(int n, const int64_t* off, const int32_t* nb, double seconds, unsigned long long seed, int algo,
              const double* params, int nparams, int target, int threads, int32_t* out, double* stats,
              double* trace, int tcap) {
    using namespace mc;
    Timer T(seconds);
    if (n <= 0) return 0;
    if (threads < 1) threads = 1;
    if (threads > 64) threads = 64;
    Rng rng(seed);
    Graph G;
    G.build(n, off, nb);
    Params P;
    P.algo = algo;
    P.target = target;
    for (int i = 0; i < nparams && i < 16; i++) P.p[i] = params[i];
    std::vector<Pool> pools(threads);
    std::vector<long long> steps(threads, 0);
    std::vector<Rng> rngs;
    for (int t = 0; t < threads; t++) rngs.emplace_back(rng.next());
    if (threads == 1) {
        steps[0] = dispatch(G, rngs[0], pools[0], T, P);
    } else {
        std::vector<std::thread> th;
        for (int t = 1; t < threads; t++) {
            try {
                th.emplace_back([&, t]() { steps[t] = dispatch(G, rngs[t], pools[t], T, P); });
            } catch (...) {
                break;
            }
        }
        steps[0] = dispatch(G, rngs[0], pools[0], T, P);
        for (auto& x : th) x.join();
    }
    int best = 0;
    double tBest = 0;
    for (auto& p : pools)
        if (p.best > best || (p.best == best && p.tBest < tBest)) {
            best = p.best;
            tBest = p.tBest;
        }
    std::vector<std::vector<int>> cand;
    for (auto& p : pools)
        if (p.best == best)
            for (auto& c : p.cls) {
                std::vector<int> s(c);
                std::sort(s.begin(), s.end());
                bool dup = false;
                for (auto& d : cand)
                    if (d == s) {
                        dup = true;
                        break;
                    }
                if (!dup) cand.push_back(s);
            }
    std::vector<int> cl;
    if (!cand.empty()) cl = cand[rng.below((u32)cand.size())];
    if (!is_clique(G, cl)) cl.clear();
    greedy_extend(G, cl, rng);
    int k = (int)cl.size();
    for (int i = 0; i < k; i++) out[i] = cl[i];
    if (stats) {
        long long st = 0;
        for (long long x : steps) st += x;
        stats[0] = (double)st;
        stats[1] = tBest;
        stats[2] = best > k ? best : k;
        stats[3] = (double)cand.size();
        stats[4] = T.elapsed();
        stats[5] = (double)(pools[0].trace.size() / 2);
    }
    if (trace) {
        int m = (int)std::min<size_t>(pools[0].trace.size() / 2, (size_t)tcap);
        for (int i = 0; i < 2 * m; i++) trace[i] = pools[0].trace[i];
    }
    return k;
}

int mc_solve(int n, const int64_t* off, const int32_t* nb, double seconds, unsigned long long seed, int algo,
             const double* params, int nparams, int target, int32_t* out, double* stats, double* trace,
             int tcap) {
    return mc_solve2(n, off, nb, seconds, seed, algo, params, nparams, target, 1, out, stats, trace, tcap);
}
}
