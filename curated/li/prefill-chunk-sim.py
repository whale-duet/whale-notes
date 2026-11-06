#!/usr/bin/env python3
"""prefill chunk 大小：固定值 vs 从时间预算反解 —— 纯本地小模拟（只读、无依赖）

背景：某开源推理框架的官方 Q&A 说单卡内 prefill 的 chunk 不是固定值：每步给一个
时间预算 `compute_quota_limit`(ns)，用拟合的二次成本模型 cost(q, c) 对 q 解方程，
取「刚好用完预算」的最大 q，且预算在 DP 组内做 all-reduce MIN。这段反解逻辑不在
开源切片里（调度器里只有静态 max_num_batched_tokens 和按多模态边界收放的
_adjust_chunk_size）。所以这里不调它的代码，只用自造系数做定性复现，看
「为什么值得反解」——换成你自己的机器也成立，只是系数要重拟。

系数全是自造值：输出只能用来比较两种切法的形状，不能当任何真实硬件的性能数字。

用法：python3 prefill-chunk-sim.py   （断言不过退出码 1）
"""
import math
import random

MAX_CHUNK = 32768  # Q&A 里的 32,768 token 上限

# cost(q, c)：MLP 线性于 q；注意力线性于 q*c（历史前缀）和 q^2/2（本 chunk 内部）
MLP_NS_PER_TOKEN = 9.0e3
ATTN_NS_PER_PAIR = 4.0e-2
BUDGET_NS = 2.0e7  # 每步 20 ms（自造，不对应任何真实硬件）


def cost_ns(q, c):
    return MLP_NS_PER_TOKEN * q + ATTN_NS_PER_PAIR * (q * c + q * q / 2.0)


def solve_q(c, budget=BUDGET_NS):
    """A q^2 + B q - budget = 0，A = ATTN/2，B = MLP + ATTN*c"""
    a = ATTN_NS_PER_PAIR / 2.0
    b = MLP_NS_PER_TOKEN + ATTN_NS_PER_PAIR * c
    q = (-b + math.sqrt(b * b + 4 * a * budget)) / (2 * a)
    return max(1, min(MAX_CHUNK, int(q)))


def run_prefill(prefix, total, chunk_fn, jitter=0.0, rng=None, speed=1.0, budget=BUDGET_NS):
    """把 total 个 token 的 prefill 按 chunk_fn(c) 切完 → (步数, 每步耗时, 破预算步数, 最大超出量)"""
    steps, times, over, worst = 0, [], 0, 0.0
    done = 0
    while done < total:
        q = min(chunk_fn(prefix + done), total - done)
        t = cost_ns(q, prefix + done) / speed
        if jitter and rng:
            t *= 1.0 + rng.uniform(-jitter, jitter)
        times.append(t)
        if t > budget:
            over += 1
            worst = max(worst, t / budget - 1.0)
        done += q
        steps += 1
    return steps, times, over, worst


def cv(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) / m


def main():
    print(f"预算 {BUDGET_NS/1e6:.0f} ms/步\n")

    print("A. 前缀越长，反解出来的 chunk 越小（固定 chunk 不会）")
    print(f"{'前缀 c':>9} {'反解 q':>8} {'反解该步/ms':>12} {'固定2048/ms':>12}")
    for c in (0, 4_000, 16_000, 64_000, 130_000):
        q = solve_q(c)
        print(f"{c:>9} {q:>8} {cost_ns(q, c)/1e6:>12.2f} {cost_ns(2048, c)/1e6:>12.2f}")

    print("\nB. 两种切法跑同一条 32768 token 请求（c 从 0 起）")
    rows = {}
    for name, fn in (("固定2048", lambda c: 2048), ("反解", solve_q)):
        steps, times, over, worst = run_prefill(0, MAX_CHUNK, fn)
        rows[name] = (steps, cv(times), over, worst)
        print(f"  {name:<8} 步数 {steps:>4}  步耗时 CV {cv(times):>4.2f}  "
              f"最长 {max(times)/1e6:>5.1f} ms  破预算 {over:>2} 步  最坏超出 {worst*100:>5.1f}%")

    print("\nC. 同一条请求挂在长前缀上（c=64k 起，再 prefill 16k）")
    for name, fn in (("固定2048", lambda c: 2048), ("反解", solve_q)):
        steps, times, over, worst = run_prefill(64_000, 16_384, fn)
        print(f"  {name:<8} 步数 {steps:>4}  步耗时 CV {cv(times):>4.2f}  "
              f"最长 {max(times)/1e6:>5.1f} ms  破预算 {over:>2}/{steps} 步  最坏超出 {worst*100:>5.1f}%")

    print("\nD. DP 组内 all-reduce MIN：预算按最慢 rank 缩，chunk 跟着缩")
    ranks = {"rank0 1.00x": 1.00, "rank1 0.72x": 0.72, "rank2 0.55x": 0.55, "rank3 0.40x": 0.40}
    budget_eff = BUDGET_NS * min(ranks.values())
    q_solved = solve_q(0, budget_eff)
    for name, speed in ranks.items():
        print(f"  {name}: 固定2048 需 {cost_ns(2048, 0)/speed/1e6:>6.1f} ms/步；"
              f"共用预算 {budget_eff/1e6:>4.1f} ms → 反解 q={q_solved}，自己那步 "
              f"{cost_ns(q_solved, 0)/speed/1e6:>4.1f} ms"
              f"{'（等最慢）' if speed < 1.0 else '（本来可以更大，被 MIN 压住）'}")

    print("\nE. ±20% 抖动下谁更容易破预算（c=64k 起 prefill 8k，模拟 200 次）")
    for name, fn in (("固定2048", lambda c: 2048), ("反解", solve_q)):
        total_over = worst = 0
        for i in range(200):
            _, _, over, w = run_prefill(64_000, 8_192, fn, jitter=0.2, rng=random.Random(i))
            total_over += over
            worst = max(worst, w)
        print(f"  {name:<8} 共 {total_over:>4} 步破预算  最坏超出 {worst*100:>5.1f}%")

    # 断言只验证模拟自身性质，不是对那个框架的断言
    assert solve_q(0) > solve_q(130_000), "前缀变长时反解出的 chunk 应变小"
    assert 1 <= solve_q(0) <= MAX_CHUNK
    assert rows["固定2048"][2] > 0, "固定 2048 在长前缀下应当破预算"
    assert rows["反解"][2] == 0, "反解本身不应破预算（无抖动时）"
    _, _, over_fixed_c, _ = run_prefill(130_000, 4_096, lambda c: 2048)
    assert over_fixed_c > 0, "前缀 130k 时固定 2048 必然破预算"
    print("\n[OK] 断言全过（只验证模拟自身性质，不代表那个框架的实测）")


if __name__ == "__main__":
    main()
