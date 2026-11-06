// tie_fair.cpp - 在真实 batch 顺序（scheduled 按 get_len 降序）下，量化 MTP varlen 退化档的 extra 槽都给谁。
// 源码依据：csrc/engine/scheduler.hpp:621-655（LCFS 组队 + scheduled 按 get_len 降序）、
//          csrc/engine/mtp_varlen.hpp:150-270（push_rates 的 1.0f/0.0f 退化分支、by_rate_desc、前缀分配）
// 编译 g++ -O2 -std=c++17 -o tie_fair tie_fair.cpp
// 说明：耗时曲线是自造的，best_bsz 的具体数值不可当真；可信的是"退化档 = 全常数平局 → 前缀填满"这个机制。
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <random>
#include <utility>
#include <vector>

using Item = std::pair<float, int>;  // (accept_rate, seq_idx)，与源码同构

// 与 mtp_varlen.hpp:210 逐字一致的比较器：没有"相等"分支，等价元素不存在
static bool by_rate_desc(const Item &a, const Item &b) {
    return a.first != b.first ? a.first > b.first : a.second < b.second;
}

struct PerfCurve {
    std::vector<std::pair<int, float>> pts;
    double slope = 0.005;
    double sampler_ms = 0.4;
};

// 复刻 update_best 的吞吐模型
static int best_bsz_for(const std::vector<Item> &q_bsz, int num_seqs, float cur_expected, const PerfCurve &c) {
    double best_thr = 0.0;
    int best_bsz = 0;
    int idx = 0;
    int cur_bsz = num_seqs;
    auto update_best = [&]() {
        while (idx < (int)c.pts.size() && cur_bsz > c.pts[idx].first) ++idx;
        if (idx == (int)c.pts.size()) return;
        const auto &p = c.pts[idx];
        const double t = p.second + c.slope * p.first + c.sampler_ms;
        const double thr = cur_expected / t;
        if (thr > best_thr) {
            best_thr = thr;
            best_bsz = cur_bsz;
        }
    };
    update_best();
    for (const auto &a : q_bsz) {
        cur_expected += a.first;
        cur_bsz += 1;
        update_best();
    }
    return best_bsz;
}

struct Mode {
    bool informative_rates;  // true: 有 confidence head；false: rates.empty() → 1.0f pad 退化分支
    bool rotate_ties;        // 反事实：tie 用轮转基准，而不是 seq_idx 升序
};

static std::vector<int> schedule(const std::vector<int> &nd, const std::vector<std::vector<float>> &rates_bsz,
                                 const std::vector<std::vector<float>> &rates_q, const PerfCurve &c, const Mode &m, int rotate_base) {
    const int n = (int)nd.size();
    std::vector<Item> q_bsz, q_q;
    auto key = [&](int seq) { return m.rotate_ties ? (seq - rotate_base + n) % n : seq; };
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j < nd[i]; ++j) {
            float rb = rates_bsz[i].empty() ? 1.0f : rates_bsz[i][j];
            float rq = rates_q[i].empty() ? 1.0f : rates_q[i][j];
            q_bsz.push_back({rb, key(i)});
            q_q.push_back({rq, key(i)});
        }
    }
    std::sort(q_bsz.begin(), q_bsz.end(), by_rate_desc);
    std::sort(q_q.begin(), q_q.end(), by_rate_desc);
    int bs = best_bsz_for(q_bsz, n, (float)n, c);
    std::vector<int> seqlen(n, 1);
    int extras = bs - n;
    for (int i = 0; i < extras && i < (int)q_q.size(); ++i) {  // 真代码里 extras ≤ 元素总数，不会越界
        int k = q_q[i].second;
        int seq = m.rotate_ties ? (k + rotate_base) % n : k;
        seqlen[seq] += 1;
    }
    return seqlen;
}

// 稳态：N 条请求一直存活，退化档下看 seqlen 分布
static void steady(const PerfCurve &c, int N, int nd) {
    std::vector<int> ndv(N, nd);
    std::vector<std::vector<float>> empty(N);
    auto sl = schedule(ndv, empty, empty, c, Mode{false, false}, 0);
    int hist[16] = {0};
    for (int i = 0; i < N; ++i) hist[sl[i] < 16 ? sl[i] : 15]++;
    std::printf("稳态 N=%d nd=%d:", N, nd);
    for (int k = 0; k < 16; ++k)
        if (hist[k]) std::printf(" seqlen=%d×%d", k, hist[k]);
    std::printf(" | 末位 seqlen=%d\n", sl[N - 1]);
}

struct Req {
    int id;
    int prompt_len;
    double gen = 0;
    double target;
    double rate;  // 真实期望接受率（与长度无关）
    int extra_hits = 0;
    int steps = 0;
    bool alive = true;
};

struct Agg {
    double expect_accept = 0;  // 期望被验证并接受的 draft 数（吞吐代理）
    double extra_total = 0;
    double extra_lowq = 0;     // 分给"长度最短 25%"请求的 extra
    long long steps = 0, steps_hit_lowq = 0;
};

static Agg run(const PerfCurve &c, const Mode &m, int max_batch, int nd, unsigned seed, int T = 400) {
    std::mt19937 rng(seed);
    std::uniform_real_distribution<double> u(0.0, 1.0);
    std::vector<Req> reqs;
    int next_id = 0;
    auto add = [&]() {
        Req r;
        r.id = next_id++;
        r.prompt_len = 200 + (int)(1800 * u(rng));
        r.target = 60 + 240 * u(rng);
        r.rate = 0.2 + 0.75 * u(rng);
        reqs.push_back(r);
    };
    for (int i = 0; i < max_batch; ++i) add();

    Agg agg;
    for (int step = 0; step < T; ++step) {
        std::vector<Req *> act;
        for (auto &r : reqs)
            if (r.alive) act.push_back(&r);
        if (act.empty()) break;
        // 真实 batch 顺序：get_len 降序（prompt + 已生成）
        std::sort(act.begin(), act.end(), [](const Req *a, const Req *b) {
            return a->prompt_len + a->gen > b->prompt_len + b->gen;
        });
        if ((int)act.size() > max_batch) act.resize(max_batch);
        const int n = (int)act.size();

        std::vector<double> lens;
        for (int i = 0; i < n; ++i) lens.push_back(act[i]->prompt_len + act[i]->gen);
        std::vector<double> ls = lens;
        std::sort(ls.begin(), ls.end());
        const double lowq_thr = ls[(size_t)(0.25 * (n - 1))];

        std::vector<int> ndv(n, nd);
        std::vector<std::vector<float>> rates(n), rates_bsz(n);
        if (m.informative_rates) {
            for (int i = 0; i < n; ++i) {
                float acc = 1.0f;
                for (int j = 0; j < nd; ++j) {  // 复刻 scheduler.hpp:1247 的 float32 累乘
                    float conf = (float)(act[i]->rate + 0.15 * (u(rng) - 0.5));
                    conf = std::min(1.0f, std::max(0.02f, conf));
                    acc *= conf;
                    rates[i].push_back(acc);
                }
                rates_bsz[i] = rates[i];
            }
        }
        auto seqlen = schedule(ndv, rates_bsz, rates, c, m, step);
        for (int i = 0; i < n; ++i) {
            const int e = seqlen[i] - 1;
            double acc = 1.0;
            for (int j = 0; j < e; ++j) {
                acc *= act[i]->rate;
                agg.expect_accept += acc;
            }
            agg.extra_total += e;
            if (lens[i] <= lowq_thr) {
                agg.extra_lowq += e;
                agg.steps_hit_lowq += (e > 0 ? 1 : 0);
            }
            act[i]->gen += 1 + e * act[i]->rate;
            act[i]->steps++;
            act[i]->extra_hits += (e > 0);
            agg.steps++;
            if (act[i]->gen >= act[i]->target) act[i]->alive = false;
        }
        while ((int)std::count_if(reqs.begin(), reqs.end(), [](const Req &r) { return r.alive; }) < max_batch) add();
    }
    return agg;
}

int main() {
    PerfCurve slow;
    for (int s : {1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024}) slow.pts.push_back({s, (float)(5.0 + 0.02 * s)});

    std::printf("== 稳态分布（退化档，前缀填满的直接证据）==\n");
    steady(slow, 64, 4);
    steady(slow, 32, 4);

    struct Case {
        const char *name;
        Mode m;
    } cases[] = {
        {"退化档 无confidence tie=seq_idx", {false, false}},
        {"退化档 + tie 轮转（反事实）", {false, true}},
        {"有 confidence tie=seq_idx", {true, false}},
    };
    std::printf("\n== 400 步 到达/结束仿真 max_batch=64 nd=4 ==\n");
    std::printf("%-34s %12s %10s %16s %10s\n", "场景", "期望接受/步", "extra/步", "extra 落短请求25%", "每槽价值");
    for (auto &cs : cases) {
        Agg a = run(slow, cs.m, 64, 4, 20261107u);
        double per_slot = a.extra_total > 0 ? a.expect_accept / a.extra_total : 0.0;
        std::printf("%-34s %12.2f %10.1f %15.1f%% %10.3f\n", cs.name, a.expect_accept / 400.0, a.extra_total / 400.0,
            100.0 * a.extra_lowq / (a.extra_total > 0 ? a.extra_total : 1), per_slot);
    }
    return 0;
}
