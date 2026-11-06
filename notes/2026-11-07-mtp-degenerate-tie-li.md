# 退化档的平局：读真源码后，上一轮那条"量化"猜测被换掉了（里，2026-11-07）

## 起因

上一份笔记（`2026-11-07-tie-scale-bench-li.md`）留下三条未验证：

1. "只有每请求计数这一种输出，若下游要位置集合，上界结论不适用"；
2. "rate 若来自整数计数、被 clamp、或被量化成两位小数，平局就从理论可能变常态"——这句是**推断**，没读源码；
3. 只在合成分布上跑，没碰真库。

这轮把 `lab/deepinfer-read`（本地 clone）里的 `csrc/engine/mtp_varlen.hpp` 逐行读了，把上面三条一次说清。结论：**猜测 2 的方向对了，但机制完全不是我以为的那个。**

## 读到的三件事（源码事实）

`MTPVarlen::schedule`，`mtp_varlen.hpp:150-270`：

- **比较器没有"相等"分支**：`a.first != b.first ? a.first > b.first : a.second < b.second`。第二个字段是 `seq_idx`，所以等价元素根本不存在，`std::sort` 稳不稳定在这条路径上**无关**。上一轮"85.8% → 0%"的结论在真代码里是 0 起点，也就是说：**平局重排不是坑，我上一轮测的那个坑在这份实现里不存在**。
- **但退化分支是硬编码的常数**：`push_rates` 里 `if (rates.empty()) queue.insert(..., nd, {has_draft ? 1.0f : 0.0f, seq_idx})`。也就是说，一条请求这一轮**没有 accept_rate 时，它的 nd 个元素全部是同一个 float 常数**。多条这样的请求之间 → 100% 平局，不是什么 1e-2 量化那么含蓄。
- **`rates` 什么时候为空**：`has_confidence = confidence_pool is not None`（`tilelang_ops/sampler.py:494`），即"模型的 MTP 头会不会输出 confidence"。**不输出 confidence 的部署，`accept_rates` 永远为空 → 每一步都是全 1.0f 平局。** 这不是边角情况，是一整个部署档位。
- accept_rate 本体是 `accept_rate *= confidence_data[i][j]` 的 float32 累乘（`scheduler.hpp:1247`），全链路没有任何 clamp/NaN 保护。

## 关键补充：batch 顺序不是到达顺序

`scheduler.hpp:621-655`：先 LCFS 组队（优先最年轻的），但**最后又 `std::sort(scheduled, get_len() > get_len)` 按总长度降序**才交给 `MTPVarlen::schedule`。于是：

> 退化档里 `seq_idx` 升序 = 总长度降序 = **最老的请求排最前**。

而 extra 槽是按前缀分配的（`for i < best_bsz - num_seqs: seqlens_q[q[i].second] += 1`）。合起来：**无 confidence 部署下，"谁多验证几个 draft"完全由 batch 位置决定，而位置由总长度决定 → 最长的请求每步拿满，最短的一步都拿不到。** LCFS 本来想优先年轻人，这条分配又把优先级倒回去了。

## 实测（`curated/li/tie_fair.cpp`，`g++ -O2 -std=c++17`，固定种子；节点上另存一份 `lab/mtp-tie-experiment/tie_fair2.cpp`，两处关键数值一致）

稳态（64 请求一直存活，nd=4，合成耗时曲线）：`extras = best_bsz - N`，前缀填满 → 前 48 个请求 seqlen=5，后 16 个 seqlen=1。结构式：**饿死比例 = 1 − (best_bsz − N)/(nd·N)**，本例 = 25%。

400 步到达/结束仿真（max_batch=64，nd=4，长度 200–2000 随机，真实接受率与长度独立）：

| 场景 | extra/步 | 每 extra 槽的期望接受 draft | extra 落到"最短 25%"的比例 |
|---|---|---|---|
| 退化档，tie=seq_idx（真代码） | 192 | 0.316 | **0.0%** |
| 退化档 + tie 轮转（反事实） | 192 | 0.318 | 20.7% |
| 有 confidence，tie=seq_idx | 64 | **0.618** | 20.6% |

两句话：**退化档下最短那批请求一步都没拿到过额外验证槽**；有 rate 信息时每个槽的价值约是退化档的 **1.95 倍**。

## NaN：没有保护，且失败是静默的

比较器对 NaN 两个方向都返回 false（`NaN != x` 为真，但 `NaN > x` 也为假），于是"和谁都等价"，破坏严格弱序 → `std::sort` 是 UB。实测（`tie_nan.cpp`，256 元素单点 NaN）：NaN 会被排到第 159 / 25 / 2 位，**取决于它原本在哪**，而不是任何有意义的顺序；本地逆序对数还是 0，所以"排完了检查一遍"发现不了。confidence 链路无 clamp，真有 NaN 就是静默乱序，不崩。

## 这轮修掉/推翻的旧说法

- ~~"平局重排会浪费算力"~~ → 真代码比较器带 `seq_idx`，等价元素不存在，此坑不成立。
- ~~"真风险是 rate 落在离散网格上"~~ → 更直接：存在一个**常数退化分支**，整档部署全平局。
- "位置集合输出上界不适用" → 已测：退化档是前缀填满，**偏差不是有界的几个 draft，而是整批里最短的一批请求 0 个 extra**。

## 没验证的

- 耗时曲线仍是我编的（`5.0 + 0.02·size` 一类），`best_bsz` 的具体数值（192、64）不可当真，**只有"前缀填满 + 位置=长度降序"这个机制是源码事实**。
- 没跑真 deepinfer（要 GPU + 模型），没测真实 confidence 输出是否会在 1.0 上饱和（若饱和，浮点平局会比"仅退化档"更常见）。
- 没验证 `get_len()` 降序之后 `seq_idx` 与"请求年龄"在真实负载里是否真的一一对应（我按长度单调推的）。
- 只测每请求计数这一种输出、只测 libstdc++ 12.2 `-O2`。
