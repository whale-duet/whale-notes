#!/usr/bin/env python3
"""GitHub contents 小工具：给 REST PUT /contents 生成请求体，或解码回读内容。

为什么要有这个：
  用 PUT /contents 写中文时，content 必须是「真实 UTF-8 字节」的 base64。
  手搓 base64 会悄悄写坏汉字（本仓库 README 第一次提交就坏了）。
  所以统一走这个脚本：先写本地文件，再让它生成 JSON。

用法：
  # 生成请求体（覆盖已有文件时带上 --sha）
  python3 scripts/gh_content.py encode notes/xxx.md --message "提交说明" --sha <旧sha>

  # 解码一段 base64（从 REST 或本地文件拿到的）
  python3 scripts/gh_content.py decode <base64字符串>

  # 解码本地 .b64 文件
  python3 scripts/gh_content.py decode-file payload.b64

输出 encode 时是一行 JSON（已做字节级往返自检），直接当 github_request 的 body 用。
"""

import argparse
import base64
import binascii
import json
import sys


def cmd_encode(args):
    raw = open(args.path, "rb").read()
    body = {
        "message": args.message or "更新 %s" % args.path,
        "content": base64.b64encode(raw).decode("ascii"),
    }
    if args.sha:
        body["sha"] = args.sha
    if args.branch:
        body["branch"] = args.branch
    # 先自检：把生成的 base64 解回字节，和原文件逐字节比对，坏一个字节就不输出
    check = base64.b64decode(body["content"])
    if check != raw:
        sys.exit("自检失败：base64 往返不一致，别提交")
    print(json.dumps(body, ensure_ascii=False, separators=(",", ":")))


def _decode(data):
    data = "".join(data.split())
    try:
        text = base64.b64decode(data, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        sys.exit("解码失败：%s" % exc)
    return text


def cmd_decode(args):
    print(_decode(args.data))


def cmd_decode_file(args):
    print(_decode(open(args.path, "r", encoding="utf-8").read()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="GitHub contents base64 工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("encode", help="生成 PUT /contents 请求体 JSON")
    e.add_argument("path")
    e.add_argument("--message")
    e.add_argument("--sha")
    e.add_argument("--branch", default="main")
    e.set_defaults(func=cmd_encode)

    d = sub.add_parser("decode", help="解码 base64 字符串")
    d.add_argument("data")
    d.set_defaults(func=cmd_decode)

    f = sub.add_parser("decode-file", help="解码存有 base64 的文件")
    f.add_argument("path")
    f.set_defaults(func=cmd_decode_file)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
