// tie_nan.cpp - by_rate_desc 在 rate 为 NaN 时是否还满足严格弱序？
// 源码 csrc/engine/mtp_varlen.hpp:210:
//   auto by_rate_desc = [](a, b) { return a.first != b.first ? a.first > b.first : a.second < b.second; };
// NaN 与谁比都"不相等"，但 "NaN > x" 也为假 → 两个方向都返回 false → 与任何元素等价，
// 破坏严格弱序（传递性），std::sort 行为未定义。confidence 链路无 clamp，所以这只是一个"防线存在吗"的检查。
// 编译 g++ -O2 -std=c++17 -o tie_nan tie_nan.cpp
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <random>
#include <utility>
#include <vector>

using Item = std::pair<float, int>;

static bool by_rate_desc(const Item &a, const Item &b) {
    return a.first != b.first ? a.first > b.first : a.second < b.second;
}

int main() {
    std::mt19937 rng(20261107u);
    std::uniform_real_distribution<float> u(0.0f, 1.0f);
    const int pos[] = {0, 128, 255};
    for (int c = 0; c < 3; ++c) {
        std::vector<Item> q;
        for (int i = 0; i < 256; ++i) {
            float r = u(rng);
            if (i == pos[c]) r = std::nanf("");
            q.push_back({r, i});
        }
        std::sort(q.begin(), q.end(), by_rate_desc);
        int inversions = 0;
        for (size_t i = 0; i + 1 < q.size(); ++i)
            if (by_rate_desc(q[i + 1], q[i])) ++inversions;
        int nan_rank = -1;
        for (size_t i = 0; i < q.size(); ++i)
            if (std::isnan(q[i].first)) nan_rank = (int)i;
        std::printf("NaN at input %3d -> rank %3d, comparator inversions after sort = %d\n", pos[c], nan_rank, inversions);
    }
    return 0;
}
