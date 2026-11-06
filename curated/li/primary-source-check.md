# 一手核验：把「二手笔记」换成可复现事实

> 适用场景：你读到一篇转述（二手博客、群聊截图、搜索结果摘要），准备引用它的日期、数字、能力边界。
> 结论先行：**转述里最容易错的三样是日期、数量、能力边界**；这三样恰好都能用官方仓库 + REST API + 代码 grep 一次核完。

## 一、五步核验流程（顺序别换）

1. **定位一手源**：官方 README、官方 Q&A/文档正文、仓库本体。别从二次报道里找链接，直接用 `owner/repo` 拼 API。
2. **先拿硬元数据**（不用读正文就能证伪一堆说法）：
   `GET /repos/<owner>/<repo>` → `created_at`、`pushed_at`、`stargazers_count`、`forks_count`、`license`、`size`。
3. **读文档时强制区分两类"有"**：
   - 「仓库里有实现」= 能在代码里指到文件/开关；
   - 「内部自研、未开源」= 只在文档里出现，代码里搜不到。二手笔记最常把这两类混成"它已经开源了 X"。
4. **在代码里验证名字**：文档提到的配置开关写 `grep -r <FLAG> | wc -l`，出现 0 次就说明你引用的说法要么名字错了、要么根本不存在。这一条能筛掉大多数"看起来很专业"的转述。
5. **写成对照表**：每一行「二手说法 | 一手实况 | 证据」。核不动的行原样标「未验证」，别为了表好看删掉。

## 二、一个实例（2026-11-06 核验，开源推理框架 DeepInfer / DeepSeek-V4.1-Flash）

只读未编译，下面都带证据来源。

| 二手说法 | 一手实况 | 证据 |
| --- | --- | --- |
| 「只是离线推理切片」 | 引擎侧大部分在仓库里：EPLB（冗余专家 + 专家重排）、异步调度、动态 draft 长度验证、SWA bounded replay、batch-invariant、确定性采样、结构化输出都是开源代码；缺的是 KV cache 存储、Prefill/Decode/VE 分离部署、DP 副本间负载均衡 | README 明列"不含"的三项 + Q&A v0.2 |
| 「2026-10-20 宣布」 | 仓库 `created_at` 是 2026-10-16，`pushed_at` 2026-11-03；README 和论坛正文里没有 10-20 | REST 元数据 |
| 「和 vLLM/SGLang 并列的新框架」 | 官方致谢写「最初主要基于 vLLM 构建，另受 SGLang、TensorRT-LLM 启发」——是 vLLM 血统 | README |
| star 数「约 25.5k」 | API 实测 25,514 / fork 3,123，Apache-2.0 | REST 元数据 |

顺带的做法：把「二手原件」留着，顶部加一行作废指针指向新笔记，比删掉更有用——下次能看出来自己是怎么被带偏的。

## 三、可复现命令

```sh
# 元数据（硬数字）
curl -s https://api.github.com/repos/<owner>/<repo> | jq '{created_at,pushed_at,stargazers_count,forks_count,license,size}'
# 浅克隆 + 定位版本
git clone --depth 1 https://github.com/<owner>/<repo>.git r && git -C r log -1 --format='%h %ad %s' --date=short
# 验证开关名真的存在于代码里
grep -r --include='*.py' --include='*.hpp' -c 'PREFIX_ENABLE_X' r | head
# 规模（区分 Python / C++ 各自多少行）
find r -name '*.py' -not -path '*/vendor/*' -print0 | xargs -0 wc -l | tail -1
```

## 四、限制 / 未验证

- 只读核验：没编译、没跑测试、没复现基准。**"代码里有"不等于"能跑起来"**，两者要分开写。
- 硬件门槛这类说法（例如要求某代加速卡、某版本 CUDA/Python）只做文档转述；没装成就不算验证过。
- API 数字是核验当时的值，写笔记时带上时间戳；star 数每天都在变，别写成"现在是多少"。
- 文档里的性能增益通常只有"官方给的操作点"，低并发反而可能负收益，转述时务必连反例一起抄。
