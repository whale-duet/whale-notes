// 目的：把 tie_order.cpp 的结论推到「真实 batch 量级」，并处理 tie_order.cpp 没覆盖的两点：
//   1) 规模：nseq=64、变长 nd（4~12）→ 元素数 500+，等价块比 16 元素时更大
//   2) 变长：MTPVarlen 的每个请求 draft 数不同，跨请求等价块里混着不同请求的不同份额
// 输出：每个 k 下「仅 rate 键」和「(rate,seq) 键」的结果变化次数 / 试验数，以及最坏份额差。
//
// 编译：g++ -O2 -std=c++17 -o tie_scale tie_scale.cpp
// 跑法：./tie_scale            # 默认 T=200 次/档
//       ./tie_scale 2000       # 加大试验次数
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

struct Item {
    double rate;
    int seq;
    int pos;
};

static std::vector<int> counts(std::vector<Item> q, int k, bool key_with_seq, int nseq) {
    if (key_with_seq)
        std::sort(q.begin(), q.end(), [](const Item& a, const Item& b) {
            if (a.rate != b.rate) return a.rate > b.rate;
            return a.seq < b.seq;
        });
    else
        std::sort(q.begin(), q.end(), [](const Item& a, const Item& b) { return a.rate > b.rate; });
    std::vector<int> c(nseq, 0);
    for (int i = 0; i < k && i < (int)q.size(); ++i) c[q[i].seq] += 1;
    return c;
}

struct Spread {
    int max_abs_dev = 0;   // 相对基准，单请求份额最大绝对偏差
    int bad = 0;           // 与基准不同的试验次数
};

int main(int argc, char** argv) {
    const int T = (argc > 1) ? std::atoi(argv[1]) : 200;

    // ---- 场景 A：等长 nd=8，nseq=64（512 元素），rate 只取 4 档 → 大量精确平局 ----
    // ---- 场景 B：变长 nd∈[4,12]，nseq=64，rate 连续随机（少数平局） ----
    for (int scen = 0; scen < 2; ++scen) {
        const int nseq = 64;
        const bool variable = (scen == 1);
        std::mt19937 rng(20261107 + scen);
        std::vector<Item> base;
        std::vector<int> nd_of(nseq);
        std::uniform_int_distribution<int> nd_dist(4, 12);
        std::uniform_real_distribution<double> rate_dist(0.0, 1.0);
        const double ladder[4] = {0.90, 0.75, 0.60, 0.45};

        for (int s = 0; s < nseq; ++s) {
            int nd = variable ? nd_dist(rng) : 8;
            nd_of[s] = nd;
            for (int j = 0; j < nd; ++j) {
                double r = variable ? rate_dist(rng) : ladder[(s * 7 + j * 3) % 4];
                base.push_back({r, s, j});
            }
        }
        const int N = (int)base.size();
        std::printf("=== 场景 %c：%s，%d 请求，%d 元素 ===\n", 'A' + scen,
                    variable ? "变长 nd∈[4,12]、连续 rate" : "等长 nd=8、rate 仅 4 档（大量平局）", nseq, N);

        std::printf("%4s %12s %12s %14s %14s\n", "k", "仅rate:变化", "带seq:变化", "仅rate:最坏偏差",
                    "带seq:最坏偏差");
        // 采样 k：包含块内切点与大 k
        std::vector<int> ks;
        for (int k = 8; k <= N; k += std::max(1, N / 16)) ks.push_back(k);
        ks.push_back(N - 1);
        std::sort(ks.begin(), ks.end());
        ks.erase(std::unique(ks.begin(), ks.end()), ks.end());

        for (int k : ks) {
            std::vector<int> ref_rate = counts(base, k, false, nseq);
            std::vector<int> ref_seq = counts(base, k, true, nseq);
            Spread sp_rate, sp_seq;
            std::mt19937 r2(999);
            for (int t = 0; t < T; ++t) {
                std::vector<Item> q = base;
                std::shuffle(q.begin(), q.end(), r2);
                auto c_r = counts(q, k, false, nseq);
                auto c_s = counts(q, k, true, nseq);
                if (c_r != ref_rate) ++sp_rate.bad;
                if (c_s != ref_seq) ++sp_seq.bad;
                for (int i = 0; i < nseq; ++i) {
                    sp_rate.max_abs_dev = std::max(sp_rate.max_abs_dev, std::abs(c_r[i] - ref_rate[i]));
                    sp_seq.max_abs_dev = std::max(sp_seq.max_abs_dev, std::abs(c_s[i] - ref_seq[i]));
                }
            }
            std::printf("%4d %8d/%-3d %8d/%-3d %14d %14d\n", k, sp_rate.bad, T, sp_seq.bad, T,
                        sp_rate.max_abs_dev, sp_seq.max_abs_dev);
        }
        std::printf("\n");
    }

    // ---- 场景 D：只按 rate 排序的「简化陷阱」随规模如何退化？ ----
    // 固定 rate 阶梯与 nd，放大请求数，看单请求份额的最坏偏差是收敛还是有上界。
    {
        const double ladder[4] = {0.90, 0.75, 0.60, 0.45};
        std::printf("=== 场景 D：等长 nd=8、rate 4 档，放大请求数（k=N/2，仅 rate 键） ===\n");
        std::printf("%6s %8s %10s %12s %14s\n", "nseq", "元素数", "变化次数", "最坏偏差(块内)", "最坏偏差(边界k)");
        for (int nseq : {16, 64, 256, 1024}) {
            std::vector<Item> base;
            for (int s = 0; s < nseq; ++s)
                for (int j = 0; j < 8; ++j) base.push_back({ladder[(s * 7 + j * 3) % 4], s, j});
            const int N = (int)base.size();
            // 注意：k=N/2 恰好落在「rate 分组」的边界上（每档 2*nseq 个），此时任何排序都稳定。
            // 所以这里取块内切点 k=N/2-8，才测得到真实退化。
            const int k = N / 2 - 8;
            auto ref = counts(base, k, false, nseq);
            int bad = 0, dev = 0;
            std::mt19937 rng(1234);
            for (int t = 0; t < T; ++t) {
                auto q = base;
                std::shuffle(q.begin(), q.end(), rng);
                auto c = counts(q, k, false, nseq);
                if (c != ref) ++bad;
                for (int i = 0; i < nseq; ++i) dev = std::max(dev, std::abs(c[i] - ref[i]));
            }
            // 对照：边界切点 k=N/2（每档份额整数倍）
            auto refb = counts(base, N / 2, false, nseq);
            int devb = 0;
            std::mt19937 rngb(1234);
            for (int t = 0; t < T; ++t) {
                auto q = base;
                std::shuffle(q.begin(), q.end(), rngb);
                auto c = counts(q, N / 2, false, nseq);
                for (int i = 0; i < nseq; ++i) devb = std::max(devb, std::abs(c[i] - refb[i]));
            }
            std::printf("%6d %8d %7d/%-3d %12d %12d\n", nseq, N, bad, T, dev, devb);
        }
        std::printf("\n");
    }

    // ---- 场景 E：真实连续 rate 需要「精确 double 相等」才平局；把 rate 量化到 2 位小数会怎样？ ----
    {
        std::printf("=== 场景 E：nseq=64、nd=8、连续随机 rate，分别不量化 / 量化到 1e-2 / 1e-1 ===\n");
        std::printf("%10s %10s %8s %14s\n", "rate 精度", "不同值个数", "变化次数", "最坏单请求偏差");
        for (int digits : {9, 2, 1}) {
            double scale = std::pow(10.0, digits);
            std::mt19937 rng(20261107);
            std::uniform_real_distribution<double> dist(0.0, 1.0);
            std::vector<Item> base;
            for (int s = 0; s < 64; ++s)
                for (int j = 0; j < 8; ++j)
                    base.push_back({std::round(dist(rng) * scale) / scale, s, j});
            const int N = (int)base.size();
            const int k = N / 2 - 8;
            std::vector<double> vals;
            for (auto& it : base) vals.push_back(it.rate);
            std::sort(vals.begin(), vals.end());
            const int uniq = (int)(std::unique(vals.begin(), vals.end()) - vals.begin());
            auto ref = counts(base, k, false, 64);
            int bad = 0, dev = 0;
            std::mt19937 r2(4242);
            for (int t = 0; t < T; ++t) {
                auto q = base;
                std::shuffle(q.begin(), q.end(), r2);
                auto c = counts(q, k, false, 64);
                if (c != ref) ++bad;
                for (int i = 0; i < 64; ++i) dev = std::max(dev, std::abs(c[i] - ref[i]));
            }
            std::printf("%10s %10d %7d/%-3d %14d\n", digits == 9 ? "精确" : (digits == 2 ? "1e-2" : "1e-1"),
                        uniq, bad, T, dev);
        }
        std::printf("\n");
    }

    // ---- 场景 C：等价元素只可能来自同一请求时，带 seq 键是否恒稳 ----
    // 这是源码安全性的关键：比较键含 seq_idx 时，等价块内的元素同名同 seq，
    // 因此只数「每请求几个」的输出必然与顺序无关。用 nd=1 的极端平局造一次。
    {
        const int nseq = 32;
        std::vector<Item> base;
        for (int s = 0; s < nseq; ++s) base.push_back({0.5, s, 0}); // 全部 rate 相同
        const int N = (int)base.size();
        int bad = 0;
        std::mt19937 rng(7);
        auto ref = counts(base, N / 2, true, nseq);
        for (int t = 0; t < T; ++t) {
            auto q = base;
            std::shuffle(q.begin(), q.end(), rng);
            if (counts(q, N / 2, true, nseq) != ref) ++bad;
        }
        std::printf("=== 场景 C：%d 请求全部 rate=0.5（全局等价），k=%d，(rate,seq) 键变化 %d/%d ===\n", nseq,
                    N / 2, bad, T);
        std::printf("（此时每请求份额由 seq_idx 顺序决定，与输入顺序无关：稳定）\n");
    }
    return 0;
}
