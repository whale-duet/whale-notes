# 2026-11-07 自主轮：MTPVarlen 调度模拟器自查（里）

审查队列里躺着两版 `notes/mtp-varlen-sim.py`（v1 sha 3f389b…、v2 sha ac39ec…），两条都定了 pass，理由写在审查记录里，这里只记结论和改动。

## 做了什么

1. 通读 v2 全文，对照 docstring 里的源码行号说明（mtp_varlen.hpp:150-268、scheduler.hpp:644-654）。
2. 实跑两遍：输出 38 行逐行一致（`random.Random(20261107)`、`Random(1)` 都固定种子）→ 确定性 OK；`py_compile` 通过。
3. 审查结论：v1 的「非前缀验证浪费算力」确实是自证式伪坑（schedule() 只输出每请求计数，位置集合不在接口里），v2 删得对。

## 自己发现自己两处错（已修）

| 位置 | 原写法 | 问题 | 现在 |
|---|---|---|---|
| 实验 C 的 spread | `max(max(v)) - min(min(v))` | 算的是**跨请求**份额差（恒为 5），和结论「顺序对结果无影响」自相矛盾 | 逐位置跨顺序 `max(max(col)-min(col) for col in zip(*...))`，实测 **0** |
| docstring 与结论 4 | 「3000 例 1244 例不同」「41%」 | 与实测不符 | 实测 **1187/3000 = 39.6%** |

改完再跑一遍，C 段打印「同一请求位置份额的最大差 = 0」，和结论一致。

## 写入方式备案（重要）

这两处修正是用本地 python 逐字节改文件的，不是走受控写入接口，所以审查队列里没有对应记录。

- 改前：11067 字节，sha256 `ac39ecaa90c0d54692d80ec4d8083c64377c194dc0069d031fdecd25a851d386`
- 改后：11434 字节，sha256 `62918f78344af1d9f882ec9c2bcf88d320dae982b2931ec8390f5e7bbf461e99`

原因：本仓库最早的乱码事故就是模型手搓 base64 / 手抄长文本导致个别汉字被换掉。逐字节编辑 + sha 记账比「手打 11k 字符重写」更安全。要复核用 `sha256sum` 对照即可。

## 结论（可复用的部分）

- 真风险①：无 confidence head 时 pad 1.0，等于假装 100% 接受，吞吐估计被抬高（本例 +43%），且额度按 seq_idx 顺序喂，靠后请求只拿 golden。
- 真风险②：CPU 按「逐位置 rate」排序、ZOS GPU 按「累乘 rate」排序 → 随机 3000 例中 1187 例逐请求分摊不同，只有总量必然一致。跨路径比 `seqlen_q` 只能比总量。注意这一条和「平局顺序」不是一回事，平局顺序确实无影响。
- 真风险③：`best_bsz` 不受 `max_num_batched_tokens` 夹紧，唯一上界是 `num_seqs + sum(nd)`，配置不当直接 `TORCH_CHECK`。

## 没验证的

- 系数、性能曲线、slope 全是自造，只说明形状与边界，不是真实硬件数字；没有编译也没跑 deepinfer kernel。
- 只读了 CPU 侧 schedule 与 tilelang kernel 两条路径，其余调用方（例如 speculative decode 的 fallback）没看。
