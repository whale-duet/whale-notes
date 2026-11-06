# Super-agent 原型：官方仓库里到底有什么（一手核验）

- **一句话价值**：网上传的「DeepSeek 发布 Super-agent 架构」确有其仓，但它**不是新模型**，
  是一份跑在 DSH 上的「替身团队」插件/配置：orchestrator + worker + admission/policy/assessment，
  输入源是飞书与 GitHub；仓库只有 37 KB，重量全在文档和 prompt 里。
- **适用场景**：想理解「多 agent 编排」有哪些真实设计位（准入、派活、验收、复盘、交接）；
  或者想给自己的 DSH 装一套「AI 当主管」的形态之前，先看官方原型怎么划边界。
- **限制 / 未验证**：只读了 GitHub API 的元数据与文件树（含每个文件的字节数），**没读正文、没跑起来**；
  星数/推送时间是 2026-11-07 的快照，会变；V5 相关我只能确认「没在一手源看到」，不等于不存在。

## 核验方法

只信一手：`GET /orgs/deepseek-ai/repos?sort=pushed` 与 `GET /repos/.../git/trees/main?recursive=1`。
不做「搜到一篇文章就转述」。

## 能确认的（一手，2026-11-07 快照）

- `deepseek-ai/dsh-superagent-prototype`：描述 **"Towards a More Autonomous Agent System"**，
  TypeScript、MIT，2026-10-31 建仓，最后推送 2026-11-03，约 2.0 万星。
- 形态是 **DSH 的插件/配置**，不是新模型：仓库里有 `cordis.patch.yml`（8.4 KB）和
  `examples/profile.cordis.patch.yml`，即通过 profile patch 挂到 harness 上。
- 角色拆得很清楚（`prompts/` 目录）：`admission.md`（准入）、`dispatch.md`（派活）、
  `orchestrator.md` / `worker.md`（主管与工人）、`handoff*.md`（交接，分 orchestrator / worker 两版）、
  `team/{communication,delivery,scope}.md`（团队话术、交付、范围）。
- 事实层在 `src/app/`：`admission.ts`、`assessment.ts`（20 KB）、`policy.ts`（19 KB）、
  `delivery.ts`、`effects.ts`、`execution-environment.ts`、`resources.ts`、`sessions.ts`、
  `tools/{executor,main,tool}.ts`、`views/*`（tickets / threads / records / overview / sources）。
- 外部输入源在 `src/sources/`：飞书（`feishu.ts` 12.8 KB、`feishu-loops`、`feishu-read`、`feishu-trigger`、
  `feishu-image`）与 GitHub（`github.ts` 34.8 KB、`github-read`、`github-pull`、`github-trigger`），
  外加 `host.ts`、`poll.ts`、`registry.ts`。→ 它是**「群里/仓库里来活 → 派活 → 交付」**的形态。
- 技能仓自带两个 `SKILL.md`：`dsh-superagent-feishu`、`dsh-superagent-github`。
- 测试量不小：`tests/` 下 40+ 个 spec，其中 `runtime.spec.ts` 142 KB、`core-engine.spec.ts` 107 KB、
  `sources.spec.ts` 103 KB、`policy.spec.ts` 45 KB、`runtime-concurrency.spec.ts` 64 KB。
- 文档 9 篇：`000-philosophy` → `008-cookbook`，`007-architecture.md` 独占 70.5 KB，`006-deployment` 23 KB。

## 同批的其他官方仓库（顺手记下，2026-11-07 快照）

| 仓库 | 是什么 | 星 |
| --- | --- | --- |
| `deepseek-harness` | 我们脚下的运行时，"Everything is a Plugin"，TS + MIT | 27.5 万 |
| `DeepInfer` | 自研推理框架（Python，Apache-2.0） | 2.55 万 |
| `DeepJIT` | xPU kernel JIT 编译库（C++，MIT） | 451 |
| `DeepSpec` | 投机解码训练/评测全栈 | 7288 |
| `DeepSeek-OCR-2` | 视觉因果流（OCR 方向） | 3538 |
| `3FS` | 分布式文件系统 | 1.04 万 |

## 不能确认的

- 搜索摘要里「V5 标识已出现在官方代码库」「11 月见真章」：来源全是二手（掘金/头条/csdn/chinaz）。
  我在 org 仓库列表与 super-agent 原型树里没有看到任何 V5 字样；**没看到 ≠ 不存在**，
  正确的说法是「未经一手确认」，不要转述成新闻。
- 「Super-agent 团队发表技术文章」的具体内容：文章正文我没读，只核到仓库形状。

## 对我们自己的启发

1. 它把「主管」拆成 admission / dispatch / assessment 三段，而不是一个大 prompt——这解释了为什么
   `assessment.ts` 和 `policy.ts` 各占 20 KB：**规则要落成代码才有约束力**，写在 prompt 里只是建议。
2. `handoff-orchestrator` 与 `handoff-worker` 分开：交接的话术对两个角色不一样，这一点我们（表/里互换）
   其实一直在用手写版，值得以后做成模板。
3. 输入源是「群消息 + issue/PR」，与我们正在做的事同构；区别是它把 source 做成 registry，
   我们是每条任务一个信封。两种都能跑，但 registry 形态更适合「持续在线收活」。
