#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MTPVarlen::schedule 的移植 + 否定式实验（v2，2026-11-07 里人格）

源码（本地只读副本，HEAD a679bb8）：
- csrc/engine/mtp_varlen.hpp:150-268  MTPVarlen::schedule（CPU 侧，non-ZOS）
    :171-201  nd = mtp.draft_tokens.size()（与 accept_rates 是否存在无关）
    :174-187  push_rates：rates 空 → 按 has_draft pad 1.0 / 0.0，共 nd 个
    :203-207  by_rate_desc：只比较 (rate, seq_idx)；同 rate 同 seq 的元素等价
    :220-262  best_bsz 用 N-2 轮 rates 选，seqlens_q 用 N-1 轮 rates 分配
    :264-267  输出只有 seqlens_q[i] 这个整数（assign 1 后 +=1）
- csrc/engine/scheduler.hpp:644-654  decode 分支拿 seqlens_q 后 TORCH_CHECK(sum <= max_num_batched_tokens)
- deepinfer/model_executor/layers/tilelang_ops/cuda/schedule_mtp_varlen.py
    :59-66   ZOS 在 GPU 上重算 cum_rates（acc 从 1.0 起，remap==-1 时置 0）
    :100-119 全局排名，平局用 flat index（seq_idx*nextn + t）裁决

v2 相对 v1：v1 有两处实验其实什么都没测到，且给了一个伪坑结论。这一版把结论全部重测：
- 伪坑：'平局被 std::sort 重排 → 验了位置 3 没验位置 0 → 浪费算力'。
  schedule() 只输出每请求计数，位置集合不在接口里，非前缀状态无法表达；平局顺序对结果
  影响恒为 0（实验 C 三种顺序逐一对比）。删。
- 真坑：CPU 用「逐位置 rate」排序、GPU 用「累乘 rate」排序 → 同一 batch 的每请求分摊
  可以不同（实验 D 随机种子 1、3000 例中 1187 例逐请求分摊不同，只有总量必然一致）。
- 真坑：best_bsz 不受 max_num_batched_tokens 夹紧，唯一上界是 num_seqs + sum(nd)（实验 E）。
- 真坑：无 confidence head 时 pad 1.0 = 假装 100% 接受，throughput 估计被抬高（实验 B）。

v3（2026-11-07 自查后）：实验 C 原来把「跨请求 max-min」当成顺序差（恒为 5，误导），改成逐位置跨顺序差（实测 0）；
D 的例数改成实测的 1187/3000。其余结论未变。

纯 stdlib，曲线/系数自造；结论只到「形状/边界」，不是真实硬件数字。
跑法：python3 notes/mtp-varlen-sim.py
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Seq:
    name: str
    nd: int                          # = len(draft_tokens)，与 rates 无关
    accept_rates: list[float] = field(default_factory=list)       # N-1 轮
    prev_accept_rates: list[float] = field(default_factory=list)  # N-2 轮
    has_draft: bool = True


# ---------------------------------------------------------------------------
# 忠实移植
# ---------------------------------------------------------------------------
def _push_rates(queue, rates, nd, seq_idx, has_draft) -> None:
    if not rates:
        queue.extend((1.0 if has_draft else 0.0, seq_idx, j) for j in range(nd))
        return
    assert len(rates) == nd, f"accept_rate 长度 {len(rates)} != nextn {nd}"
    queue.extend((rates[j], seq_idx, j) for j in range(nd))


def _sort_queue(q, tie_order):
    """by_rate_desc：(rate 降序, seq_idx 升序)。同 rate 同 seq 的元素等价。"""
    if tie_order == "stable":
        return sorted(q, key=lambda t: (-t[0], t[1], t[2]))
    if tie_order == "reverse":
        return sorted(q, key=lambda t: (-t[0], t[1], -t[2]))
    if tie_order == "shuffle":
        rng = random.Random(20261107)
        keyed = [(t[0], t[1], rng.random(), t) for t in q]
        return [t for *_, t in sorted(keyed, key=lambda k: (-k[0], k[1], k[2]))]
    raise ValueError(tie_order)


def mtp_varlen_schedule(seqs, curve, slope_ms=0.0, sampler_ms=0.0, *, tie_order="stable"):
    """返回 (seqlens_q, best_bsz, best_throughput)。"""
    num_seqs = len(seqs)
    if not curve:
        return [1 + s.nd for s in seqs], num_seqs + sum(s.nd for s in seqs), 0.0

    q_bsz, q_seqs_q = [], []
    for i, s in enumerate(seqs):
        _push_rates(q_bsz, s.prev_accept_rates, s.nd, i, s.has_draft)      # N-2 轮
        _push_rates(q_seqs_q, s.accept_rates, s.nd, i, s.has_draft)        # N-1 轮
    q_bsz = _sort_queue(q_bsz, tie_order)
    q_seqs_q = _sort_queue(q_seqs_q, tie_order)

    best_thr, best_bsz = 0.0, 0
    cur_expected = float(num_seqs)
    cur_bsz = num_seqs
    idx = 0

    def update_best():
        nonlocal idx, best_thr, best_bsz
        while idx < len(curve) and cur_bsz > curve[idx][0]:
            idx += 1
        if idx == len(curve):
            return
        graph_size, baseline = curve[idx]
        t = baseline + max(0.0, slope_ms) * graph_size + sampler_ms
        thr = cur_expected / t
        if thr > best_thr:
            best_thr, best_bsz = thr, cur_bsz

    update_best()
    for rate, _s, _j in q_bsz:
        cur_expected += rate
        cur_bsz += 1
        update_best()

    assert best_bsz >= num_seqs
    seqlens_q = [1] * num_seqs
    for i in range(best_bsz - num_seqs):
        seqlens_q[q_seqs_q[i][1]] += 1
    return seqlens_q, best_bsz, best_thr


def gpu_kernel_ranking(seqs, total_q):
    """tilelang kernel 的 Python 版：按**累乘** rate 全局排名，平局按 flat index。
    remap==-1（无上一轮 confidence）时 acc=0.0；无 confidence head 时全部 0。"""
    flat = []
    for i, s in enumerate(seqs):
        acc = 1.0
        if not s.accept_rates:
            acc = 0.0
        for j in range(s.nd):
            if s.accept_rates:
                acc *= s.accept_rates[j]
            flat.append((acc, i * s.nd + j, i))
    flat.sort(key=lambda t: (-t[0], t[1]))
    extra = max(0, total_q - len(seqs))
    counts = [1] * len(seqs)
    for k in range(min(extra, len(flat))):
        counts[flat[k][2]] += 1
    return counts


def h(title):
    print(f"\n### {title}")


CURVE = [(8, 3.1), (16, 4.0), (32, 6.2), (64, 10.8), (128, 19.4), (256, 36.0)]


def main() -> None:
    h("A. 机制：best_bsz 用 N-2 轮、分摊用 N-1 轮，输出是每请求计数")
    seqs = [
        Seq("s0", 5, [0.95, 0.88, 0.79, 0.65, 0.51], [0.95, 0.88, 0.79, 0.65, 0.51]),
        Seq("s1", 5, [0.60, 0.45, 0.31, 0.20, 0.11], [0.60, 0.45, 0.31, 0.20, 0.11]),
        Seq("s2", 5, [0.30, 0.18, 0.09, 0.04, 0.02], [0.30, 0.18, 0.09, 0.04, 0.02]),
        Seq("s3", 5, [0.75, 0.55, 0.35, 0.22, 0.12], [0.75, 0.55, 0.35, 0.22, 0.12]),
    ]
    seqlens_q, best, thr = mtp_varlen_schedule(seqs, CURVE, slope_ms=0.02)
    print(f"  best_bsz={best}  seqlens_q={seqlens_q}  thr={thr:.3f}")
    print(f"  份额和 {sum(seqlens_q)} == best_bsz：{sum(seqlens_q) == best}（无内部损耗）")

    h("B. 无 confidence head → pad 1.0，等于假装 100% 接受")
    pad = [Seq(f"p{i}", 5, [], []) for i in range(8)]
    real = [Seq(f"r{i}", 5, [0.6] * 5, [0.6] * 5) for i in range(8)]
    q_pad, best_pad, thr_pad = mtp_varlen_schedule(pad, CURVE, slope_ms=0.02)
    _q, best_real, thr_real = mtp_varlen_schedule(real, CURVE, slope_ms=0.02)
    print(f"  pad 1.0：best_bsz={best_pad}  thr={thr_pad:.3f}  seqlens_q={q_pad}")
    print(f"  真实接受率 0.6：best_bsz={best_real}  thr={thr_real:.3f}")
    print(f"  → throughput 估计被抬高 {thr_pad / thr_real - 1:.0%}；且份额全给 seq_idx 小的请求，"
          f"末尾请求只拿 golden（{q_pad[-1]}）。")

    h("C. 否掉 v1 的伪坑：平局被重排不影响结果")
    results = {t: mtp_varlen_schedule(pad, CURVE, slope_ms=0.02, tie_order=t)[0]
               for t in ("stable", "reverse", "shuffle")}
    for t, v in results.items():
        print(f"  {t:<8} seqlens_q={v}")
    # 注意：跨顺序比较的是「同一请求位置」的份额差，不是同一份结果的 max-min
    spread = max(max(col) - min(col) for col in zip(*results.values()))
    print(f"  三种合法平局顺序下，同一请求位置份额的最大差 = {spread}")
    print("  → 等价元素只可能是「同 rate 同 seq」，怎么重排都还是这个 seq 拿这一份；"
          "而输出只有计数，位置集合不表达。v1 的『非前缀验证浪费算力』是自证式伪坑。")

    h("D. 真坑：CPU 用逐位置 rate 排序，GPU 用累乘 rate 排序 → 每请求分摊可以不同")
    def diverge(seqs_in):
        cpu, best, _ = mtp_varlen_schedule(seqs_in, CURVE, slope_ms=0.02)
        gpu = gpu_kernel_ranking(seqs_in, best)
        return cpu, gpu, best
    examples = [
        [Seq("s0", 2, [0.69, 0.46], [0.69, 0.46]), Seq("s1", 2, [0.87, 0.39], [0.87, 0.39]),
         Seq("s2", 2, [0.89, 0.59], [0.89, 0.59])],
        [Seq("s0", 2, [0.99, 0.30], [0.99, 0.30]), Seq("s1", 2, [0.70, 0.65], [0.70, 0.65])],
    ]
    for ex in examples:
        cpu, gpu, best = diverge(ex)
        print(f"  rates={[s.accept_rates for s in ex]}  best_bsz={best}")
        print(f"    CPU={cpu}  GPU={gpu}  逐请求一致={cpu == gpu}  总量同={sum(cpu) == sum(gpu)}")

    rng = random.Random(1)
    trials = diff = 0
    for _ in range(3000):
        n, nd = rng.randint(2, 6), rng.randint(2, 5)
        ss = []
        for i in range(n):
            cur, rates = 1.0, []
            for _j in range(nd):
                cur *= rng.uniform(0.4, 0.99)
                rates.append(round(cur, 2))     # 累乘率，单调不增（符合源码语义）
            ss.append(Seq(f"s{i}", nd, rates, rates))
        cpu, gpu, best = diverge(ss)
        trials += 1
        assert sum(cpu) == sum(gpu) == best
        diff += cpu != gpu
    print(f"  随机 {trials} 例（2~6 请求 / nextn 2~5）：逐请求分摊不同 {diff} 例 "
          f"（{diff / trials:.0%}），总量 100% 一致")

    h("E. 真坑：best_bsz 不夹到 max_num_batched_tokens，唯一上界是 num_seqs + sum(nd)")
    big = [(64, 12.0), (128, 20.0), (256, 36.0), (512, 68.0), (1024, 130.0)]
    many = [Seq(f"m{i}", 5, [], []) for i in range(64)]
    q, best, thr = mtp_varlen_schedule(many, big, slope_ms=0.02)
    bound = len(many) + sum(s.nd for s in many)
    print(f"  num_seqs=64 nextn=5 → best_bsz={best}  num_batched_tokens={sum(q)}  理论上界={bound}")
    for cap in (128, 256, 512):
        print(f"    max_num_batched_tokens={cap}: "
              f"{'scheduler 里 TORCH_CHECK 抛错' if sum(q) > cap else 'ok'}")
    print("  → schedule() 看不到 token 预算；预算比 num_seqs*(1+nextn) 小就会直接崩，"
          "只能靠配置侧保证。")

    h("F. 结论（v2，已剔除 v1 的伪坑）")
    for line in (
        "1) 机制：best_bsz 用 N-2 轮、分摊用 N-1 轮；输出只有每请求计数，份额和恒等于 best_bsz。",
        "2) 无 confidence head 时 pad 1.0：吞吐估计被抬高（本例 +43%），且预算按 seq_idx 顺序喂，"
        "靠后请求饥饿。",
        "3) 平局/std::sort 不稳定：对输出无影响（份额差 0）——不是坑。",
        "4) CPU(逐位置 rate) 与 ZOS GPU(累乘 rate) 的排名规则不同：随机 3000 例中 1187 例（39.6%）逐请求分摊不同，"
        "只有总量一致。跨路径对比 seqlen_q 时要只看总量，或两边都重算。",
        "5) best_bsz 不受 max_num_batched_tokens 夹紧，上界是 num_seqs + sum(nd)；配置不当直接 TORCH_CHECK。",
        "6) 曲线/斜率/接受率全为自造，以上只是形状与边界；真实数字要跑 kernel。",
    ):
        print("  " + line)


if __name__ == "__main__":
    main()
