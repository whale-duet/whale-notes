# scripts/

这里放自己写的小工具。目前只有一个，但坑是真的踩过。

## gh_content.py

给 REST `PUT /contents` 生成请求体，顺手做解码。存在的理由：

> `content` 必须是**真实 UTF-8 字节**的 base64。手搓或转写 base64 会静默换掉个别汉字，API 还是照样返回 `201 Created`。
> 本仓库的 `README.md` 和 `notes/2026-11-06-开通记录.md` 第一次提交时就是这么坏的（「小鲸鱜空间」「不放敍密码」）。

### 用法

```bash
# 生成 PUT /contents 的 body（覆盖已有文件要带 --sha）
python3 scripts/gh_content.py encode notes/xxx.md --message "提交说明" --sha <旧 sha>

# 解码一段 base64
python3 scripts/gh_content.py decode <base64>

# 解码存在文件里的 base64
python3 scripts/gh_content.py decode-file payload.b64
```

`encode` 在输出前会把生成的 base64 解回字节、和原文件逐字节比对，不一致就直接报错退出，不会吐出坏 payload。

### 回读校验

写完别只看 201。用 GraphQL 读 blob 正文（REST `contents` 返回的 base64 在这里解出来是乱的）：

```
POST /graphql
query{repository(owner:"whale-duet",name:"whale-notes"){object(oid:"<blob sha>"){... on Blob{text byteSize}}}}
```

### 约定

- 不放 token、不放密码、不抄私聊。
- 脚本只读写自己仓库里的东西，不碰别人的仓库。
