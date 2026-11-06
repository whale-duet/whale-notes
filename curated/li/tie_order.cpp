// 目的：验证「(rate desc, seq_idx asc) 这个比较键 → 截断取前 k 个后，每请求计数与平局顺序无关」
// 以及反例：如果比较键只看 rate，计数就会随输入顺序/实现细节变。
//
// 对应 notes/mtp-varlen-sim.py 的实验 C（那里是 Python 枚举，这里用真实 std::sort + 随机输入顺序）。
// 编译：g++ -O2 -std=c++17 -o tie_order tie_order.cpp
// 跑法：./tie_order
#include <algorithm>
#include <cstdio>
#include <random>
#include <vector>

struct Item {
    double rate;
    int seq;     // 请求下标
    int pos;     // 该请求内的 draft 位置
};

static std::vector<Item> build(const std::vector<double>& rates, int nd, int nseq) {
    std::vector<Item> q;
    for (int s = 0; s < nseq; ++s)
        for (int j = 0; j < nd; ++j)
            q.push_back({rates[s * nd + j], s, j});
    return q;
}

// 截断取前 k 个，统计每个请求拿到多少个（源码里 seqlens_q 就是这类计数）
static std::vector<int> counts(std::vector<Item> q, int k, bool key_with_seq) {
    if (key_with_seq)
        std::sort(q.begin(), q.end(), [](const Item& a, const Item& b) {
            if (a.rate != b.rate) return a.rate > b.rate;
            return a.seq < b.seq;          // 源码 by_rate_desc 的比较键
        });
    else
        std::sort(q.begin(), q.end(), [](const Item& a, const Item& b) {
            return a.rate > b.rate;        // 只看 rate：同 rate 元素在 C++ 里等价
        });
    std::vector<int> c(16, 0);
    for (int i = 0; i < k && i < (int)q.size(); ++i) c[q[i].seq] += 1;
    return c;
}

int main() {
    const int nseq = 4, nd = 4;
    // 刻意造大量平局：四个请求的 rate 全相同
    const std::vector<double> rates = {
        0.90, 0.80, 0.70, 0.60,   // s0
        0.90, 0.80, 0.70, 0.60,   // s1
        0.90, 0.80, 0.70, 0.60,   // s2
        0.90, 0.80, 0.70, 0.60,   // s3
    };

    auto base = build(rates, nd, nseq);
    const int k = 6;   // 只取 6 个 → 一定切在平局块中间

    std::mt19937 rng(12345);
    int bad_with_seq = 0, bad_rate_only = 0;
    std::vector<int> ref_with_seq, ref_rate_only;
    for (int t = 0; t < 2000; ++t) {
        auto q = base;
        std::shuffle(q.begin(), q.end(), rng);
        auto c1 = counts(q, k, true);
        auto c2 = counts(q, k, false);
        if (t == 0) { ref_with_seq = c1; ref_rate_only = c2; }
        if (c1 != ref_with_seq) ++bad_with_seq;
        if (c2 != ref_rate_only) ++bad_rate_only;
    }

    auto show = [](const char* tag, const std::vector<int>& c) {
        std::printf("%s: ", tag);
        for (int i = 0; i < nseq; ++i) std::printf("%d ", c[i]);
        std::printf("\n");
    };
    show("(rate,seq) 键  首次", ref_with_seq);
    show("(rate) 单独键  首次", ref_rate_only);
    std::printf("2000 次随机输入顺序下，结果发生变化的次数：\n");
    std::printf("  (rate,seq) 键 : %d / 2000\n", bad_with_seq);
    std::printf("  仅 rate 键    : %d / 2000\n", bad_rate_only);

    // 扫一遍截断点 k：看「只看 rate」的不稳定程度跟切在哪里有关
    std::printf("\n截断点 k 扫描（500 次随机输入顺序，统计结果变化次数）：\n");
    for (int kk = 2; kk <= 16; kk += 2) {
        std::vector<int> ref = counts(base, kk, false);
        int bad = 0;
        std::mt19937 r2(999);
        for (int t = 0; t < 500; ++t) {
            auto q = base;
            std::shuffle(q.begin(), q.end(), r2);
            if (counts(q, kk, false) != ref) ++bad;
        }
        std::printf("  k=%2d  仅 rate 键变化 %3d/500", kk, bad);
        // 带 seq 键的对照
        std::vector<int> ref2 = counts(base, kk, true);
        int bad2 = 0;
        std::mt19937 r3(999);
        for (int t = 0; t < 500; ++t) {
            auto q = base;
            std::shuffle(q.begin(), q.end(), r3);
            if (counts(q, kk, true) != ref2) ++bad2;
        }
        std::printf("   (rate,seq) 键变化 %d/500\n", bad2);
    }
    return 0;
}
