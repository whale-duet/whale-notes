// 价值：给「不稳定排序 + 截断/top-k」补两条可复现判据——① 失稳只发生在切点落进等价块内部，与全局重复率无关；
//       ② 单请求最坏偏差不是常数，上界≈该请求落在切点等价块内的元素数（全落在块内就顶到 nd）。
// 适用场景：写任何 `sort` 后取前 k 的代码之前；review 别人的「最坏偏差恒为 N」这类经验结论时；
//           设计这类实验时，避免「k 取在等价块边界 → 假阴性」这个坑。
// 限制 / 未验证：合成 rate 分布、只数「每请求几个」这一种输出（下游若需要具体位置集合，结论不适用）；
//           只在 libstdc++ 12.2 `-O2` 验过，没进真实推理框架实跑；「等价」用 double 精确相等判定。
//
// 编译：g++ -O2 -std=c++17 -o tie-cut-bound tie-cut-bound.cpp
// 跑法：./tie-cut-bound            # 默认 T=200 次/档
//       ./tie-cut-bound 2000       # 加大试验次数
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <random>
#include <vector>

struct Item {
    double rate;
    int seq;
    int pos;
};

// 只按 rate 排序（不稳定的简化比较器）后取前 k，数每个请求各取到几个
static std::vector<int> counts(std::vector<Item> q, int k, int nseq) {
    std::sort(q.begin(), q.end(), [](const Item& a, const Item& b) { return a.rate > b.rate; });
    std::vector<int> c(nseq, 0);
    for (int i = 0; i < k && i < (int)q.size(); ++i) c[q[i].seq] += 1;
    return c;
}

// 用固定 base 跑 T 次打乱，返回 {与基准不同的次数, 单请求最大绝对偏差}
static std::pair<int, int> spread(const std::vector<Item>& base, int k, int nseq, int T, unsigned seed) {
    auto ref = counts(base, k, nseq);
    int bad = 0, dev = 0;
    std::mt19937 rng(seed);
    for (int t = 0; t < T; ++t) {
        auto q = base;
        std::shuffle(q.begin(), q.end(), rng);
        auto c = counts(q, k, nseq);
        if (c != ref) ++bad;
        for (int i = 0; i < nseq; ++i) dev = std::max(dev, std::abs(c[i] - ref[i]));
    }
    return {bad, dev};
}

int main(int argc, char** argv) {
    const int T = (argc > 1) ? std::atoi(argv[1]) : 200;
    const int nseq = 64, nd = 8;

    // ---- 实验 1：切点处的等价块 —— 「切点值重复几次」与「其中几个在切点之前」才是判据 ----
    // 同样一批数据，量化网格越粗全局重复率越高，但失稳只取决于 mult > before（切点落在块内部）。
    std::printf("=== 实验 1：rate 量化网格 vs 切点等价块（nseq=%d, nd=%d, k=N/2-8） ===\n", nseq, nd);
    std::printf("%8s %8s %10s %10s %12s %10s\n", "网格", "不同值", "切点值重复", "块内序号", "变化次数", "最大偏差");
    for (double scale : {1e6, 1e4, 1e3, 5e2, 1e2, 1e1}) {
        std::mt19937 rng(20261107);
        std::uniform_real_distribution<double> dist(0.0, 1.0);
        std::vector<Item> base;
        for (int s = 0; s < nseq; ++s)
            for (int j = 0; j < nd; ++j)
                base.push_back({std::round(dist(rng) * scale) / scale, s, j});
        const int N = (int)base.size(), k = N / 2 - 8;
        std::vector<double> v;
        for (auto& it : base) v.push_back(it.rate);
        std::sort(v.begin(), v.end(), std::greater<double>());
        const double cutv = v[k - 1];
        int mult = 0, before = 0;
        for (int i = 0; i < N; ++i)
            if (v[i] == cutv) { ++mult; if (i < k) ++before; }
        const int uniq = (int)(std::unique(v.begin(), v.end()) - v.begin());
        auto sp = spread(base, k, nseq, T, 4242);
        std::printf("%8.0f %8d %10d %7d/%-3d %7d/%-4d %10d\n", scale, uniq, mult, before, mult, sp.first, T,
                    sp.second);
    }
    // 参考读数（T=200, 本机 libstdc++ 12.2）：网格 1e3 → 3 重、2/3 在切点前 → 131/200 失稳、偏差 1；
    // 网格 5e2 → 2 重、2/2 在切点前（切点正好落块边界）→ 0/200，尽管全局有 15.8% 的重复值。
    std::printf("\n");

    // ---- 实验 2：单请求最坏偏差的上界 —— 让某请求的 8 个元素全落在同一等价块里 ----
    // 前 32 个请求 rate 全 0.9（共 256 个元素），后 32 个全 0.1；k 取在 0.9 块内部时偏差能顶到 nd。
    std::printf("=== 实验 2：每请求元素全落在切点块内时，偏差上界是 nd 而不是常数 2 ===\n");
    std::printf("%6s %10s %10s %10s\n", "k", "块内歧义", "变化次数", "最大偏差");
    {
        std::vector<Item> base;
        for (int s = 0; s < nseq; ++s)
            for (int j = 0; j < nd; ++j) base.push_back({s < nseq / 2 ? 0.9 : 0.1, s, j});
        for (int k : {120, 200, 250, 256}) {
            auto sp = spread(base, k, nseq, T, 777);
            std::printf("%6d %10d %7d/%-4d %10d\n", k, std::min(k, nseq / 2 * nd), sp.first, T, sp.second);
        }
        // 参考读数（T=200）：k=120/200 → 200/200 失稳、偏差 8；k=250 → 偏差 6；k=256（块边界）→ 0/200。
    }
    return 0;
}
