# 源码小读：异步调度里的「零开销」到底零了哪部分

> 场景：框架宣称「CPU 调度和 GPU 前向重叠，调度开销为零」。听起来像魔法，其实只有一句话的机制 + 一堆补丁。
> 本文按一个真实开源推理框架（DeepInfer，2026-11）的代码读出来，机制本身通用，可直接对照你手上的推理框架。

## 一、机制本体：把 decode 步的后处理挪到下一跳

开关 `DEEPINFER_ZERO_OVERHEAD_SCHEDULING`，步进函数大致这样：

1. `forward(curr)` 先甩给 GPU（**不等**）；
2. `_post_process_step(prev_step_state)` —— 处理**上一步**的采样结果（纯 CPU 活）；
3. `execute_sample(curr)` 把本步采样压进队列；
4. **prompt 步 / 关掉这个开关**：当场后处理本步；**decode 步**：把 `curr_step_state` 留给下一步。

所以"零开销"不是调度变快，而是**把 decode 的 post-process 从关键路径上搬走**。prompt 步搬不动，因为下一步的 batch 形状直接依赖它。

兜底：`flush_inflight_reqs()` 在退出/长阻塞时强制处理在途那一步，否则结果丢。

## 二、代价与补丁（这段才是重点）

延后一步 ⇒ CPU 侧的 `seqlens_q` / `seqlens_k` **落后一步**，而下一步调度要按它们切 batch。

补法：`_correct_seqlens_qk()`

- 先比对上一步和本步 batch 的**交集**得到 `remap`，没有交集直接跳过；
- 长度修正在 **GPU** 上算（两个 kernel：按 confidence 率算 `seqlens_q`、按上一步接受数算 `seqlens_k`），再按需要拷回；
- 为什么不能在 CPU 算：投机解码**接受几个 draft 是数据相关的**，长度不是静态可推的量；
- 只有开 `MTP_VARLEN` 时才真把修正后的 `seqlens_q` 拷回 pinned 内存并记事件；普通路径上 H2D → 修正 → D2H 在同一 stream 上有序，省掉一次回读。

这条是可以迁移的经验：**任何"流水线化"的方案，都要为"下游依赖的是上游的运行时结果"付一次修正成本**；能把它留在同一 stream 上做完，比搬回 CPU 便宜。

## 三、顺带看清的三件事

- Python 层（engine）只有一千行左右，**调度器本体是 C++**（chunked prefill、抢占、DP all-reduce MIN 都在里面）。读性能问题先读 C++，别在 Python 里找调度。
- 计时点分得很细：`schedule` / `schedule:cpp` / `schedule:clone` / `schedule:post` / `run_workers`，还单独给 `bubble`（气泡）计时——**把气泡当一等公民计量**，比翻日志猜有效。
- 对象生命周期显式化：请求输出走对象池，`step()` 的 docstring 明写"下一次 step 会释放，要留就 clone"，另有 `DeferredRelease` 批量回收。"推迟释放 + 下一跳回收"和上面的"推迟后处理"是同一套思路。

## 四、限制 / 未验证

- 纯静态阅读：没编译、没跑、没见过真实日志；`graph_size`、`nextn` 之类运行时取值没核。
- 只读了这一条线，其他子模块（EPLB、SWA、投机解码）没读。
- 文档给的生产增益依赖 confidence 预测质量，没有数据判断它在你机器上是否成立；低并发场景可能出现负收益。
- 行号、文件路径随版本变动，本文只保留函数/开关名，克隆后自己 `grep` 定位。
