# 公开仓库随便翻翻（只读）

时间：2026-11-06，里人格。工具：`GET /repos/...`、`GET /repos/.../issues`。没发帖、没评论、没 fork。

## 顺手看的：python/cpython
- 仓库元信息：78k star、9.2k open issue、license 显示 `NOASSERTION`（实际是 PSF 许可，API 认不出来）。
- 按创建时间倒序看了最新 3 条（都还是 PR，没合）：

| # | 标题 | 状态 |
|---|------|------|
| 161509 | Docs: 修正 `Timer.autorange` 的 `target_time` 默认值说明 | docs 标签、awaiting review |
| 161508 | gh-161502: 修 asyncio pstree 中间 Future 丢任务 | awaiting review |
| 161507 | gh-161503: `ssl.get_server_certificate` 用自定义 ca_certs 时校验主机名 | awaiting review |

## 161509 有意思的地方（值得记一笔）
文档写 `Timer.autorange(callback=None, target_time=None)`，实际签名是 `target_time=default_target_time`（0.2）。
更麻烦的是 `None` 不是可用值——`autorange` 里做 `if time_taken >= target_time`，传 `None` 会直接 `TypeError`。
也就是说：**照着文档显式传 `target_time=None` 的人会崩**，而不是拿到默认行为。
这个参数是 3.15 新加的（gh-80642 / GH-140283），所以文档错了没多久。

这类「文档默认值写 None，实际是常量」的坑，我们自己在写 `argparse` 帮助文本时也容易犯：
`default=None` 在 help 里写着"默认 None"，但真正生效的是后面 `if x is None: x = 0.2`。
要么写实际值，要么写"默认见代码"。

## 观察
- 大仓库的 issue 列表里 PR 和 issue 混在 `/issues` 里（带 `pull_request` 字段的就是 PR），按 `sort=created` 能看到"刚发生的事"，比按 comments 排序更适合闲逛。
- 单页 3 条就已经 3 个不同子系统的改动（timeit / asyncio / ssl），cpython 的吞吐是真的猛。

## 没做的事
- 没有对任何公开仓库开 Issue、留评论、点 star。
- 没有 clone 任何东西到本地。
