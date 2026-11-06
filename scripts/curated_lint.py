#!/usr/bin/env python3
"""curated/ 自检：把「去敏 + 边界三件套」从人工通读变成一条命令。

用法：
    python3 scripts/curated_lint.py curated                 # 人读格式
    python3 scripts/curated_lint.py curated --json          # 机器格式
    python3 scripts/curated_lint.py curated --strict        # 警告也当失败
    python3 scripts/curated_lint.py --self-test             # 自测（临时目录造夹具）

退出码：0 = 没有 error（默认警告不拦），1 = 有 error 或 --strict 下有警告，
        2 = 用法/IO 错误。

设计要点（都有踩坑背景）：
1. 只报「位置 + 规则名 + 片段」，不打印整行原文——自检输出本身也不能泄密。
2. 命中行只要含 `lint:allow` 就放行（该行是真例子、或已判定无害），
   例外必须写在文件里留痕，不允许改脚本放宽规则。
3. 私网 IP、手机号这类高噪声规则只给 warning，不当 error：别人的示例数字很容易误伤。
4. 头部三行（价值 / 适用场景 / 限制）缺失只 warning：不同人格的文件排版不一样，
   有人把「价值/适用场景」写在索引表里、正文只写限制——所以默认不拦，--strict 才拦。
"""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

TEXT_EXT = {".md", ".py", ".sh", ".cpp", ".c", ".h", ".hpp", ".js", ".ts", ".txt", ".json", ".yml", ".yaml"}
SKIP_DIRS = {".git", ".versions", "__pycache__", "node_modules"}

# (规则名, 正则, 级别, 说明)
RULES = [
    ("posix-home-path", re.compile(r"/(?:home|root|Users|mnt|srv)/[A-Za-z0-9_.\-]+"), "error",
     "私有绝对路径"),
    ("posix-opt-path", re.compile(r"/opt/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+"), "error",
     "私有绝对路径"),
    ("win-path", re.compile(r"\b[A-Za-z]:\\\\?[A-Za-z0-9_.\-]+"), "error", "Windows 私有绝对路径"),
    ("github-token", re.compile(r"(?:ghp_|gho_|ghs_|ghu_|github_pat_)[A-Za-z0-9_]{10,}"), "error",
     "GitHub 凭据"),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "error", "疑似 API key"),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}"), "error", "疑似 token"),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "error", "疑似 AWS key"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "error",
     "邮箱/人员标识"),
    ("url-credential", re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/:@]+:[^\s/@]+@"), "error",
     "URL 内嵌账号密码"),
    ("private-ipv4", re.compile(r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b|"
                                r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"), "warn",
     "内网地址（示例数字可能误伤）"),
    ("cn-mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "warn", "疑似手机号（示例数字可能误伤）"),
]

HEADER_HINTS = [
    ("一句话价值", ("一句话价值", "一句话", "价值：", "value:")),
    ("适用场景", ("适用场景", "use case", "适用：", "场景：")),
    ("限制", ("限制", "未验证", "limitation")),
]
HEADER_SCAN_LINES = 40


def iter_files(root):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXT:
                yield os.path.join(dirpath, name)


def redact(match_text, limit=4):
    """只留能定位的片段，中间打码，免得自检输出变成泄密源。"""
    if len(match_text) <= limit:
        return match_text[0] + "*" * (len(match_text) - 1) if match_text else ""
    return match_text[:limit] + "…"


def check_file(path, root):
    findings = []
    rel = os.path.relpath(path, root if os.path.isdir(root) else os.path.dirname(root))
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        findings.append({"file": rel, "line": 0, "rule": "io", "level": "error",
                         "detail": "读不了：%s" % exc.__class__.__name__, "snippet": ""})
        return findings
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        findings.append({"file": rel, "line": 0, "rule": "not-utf8", "level": "error",
                         "detail": "不是 UTF-8，上传后中文必坏", "snippet": ""})
        return findings
    if "\ufffd" in text:
        bad_lines = [i + 1 for i, ln in enumerate(text.splitlines()) if "\ufffd" in ln]
        findings.append({"file": rel, "line": bad_lines[0], "rule": "replacement-char",
                         "level": "error", "detail": "含 U+FFFD，多为编码写坏的形近字/问号",
                         "snippet": "共 %d 行" % len(bad_lines)})

    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        if "lint:allow" in line:
            continue
        for name, pattern, level, detail in RULES:
            m = pattern.search(line)
            if m:
                findings.append({"file": rel, "line": lineno, "rule": name, "level": level,
                                 "detail": detail, "snippet": redact(m.group(0))})

    if os.path.splitext(path)[1].lower() in {".md", ".cpp", ".py", ".sh"}:
        head = "\n".join(lines[:HEADER_SCAN_LINES]).lower()
        for label, hints in HEADER_HINTS:
            if not any(h.lower() in head for h in hints):
                findings.append({"file": rel, "line": 1, "rule": "missing-" + label,
                                 "level": "warn", "detail": "前 %d 行没找到「%s」小节"
                                 % (HEADER_SCAN_LINES, label), "snippet": ""})
    return findings


def run(root, strict=False, as_json=False):
    findings = []
    for path in iter_files(root):
        findings.extend(check_file(path, root))
    errors = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    if as_json:
        print(json.dumps({"root": root, "errors": errors, "warnings": warns,
                          "error_count": len(errors), "warning_count": len(warns)},
                         ensure_ascii=False, indent=2))
    else:
        for f in errors + warns:
            print("[%s] %s:%s %s — %s %s"
                  % (f["level"].upper(), f["file"], f["line"], f["rule"], f["detail"],
                     ("(" + f["snippet"] + ")") if f["snippet"] else ""))
        print("—— %d error / %d warning（扫描 %s）" % (len(errors), len(warns), root))
    if errors or (strict and warns):
        return 1
    return 0


# ---------- 自测 ----------

CLEAN_MD = """# 示例：干净文件

- **一句话价值**：演示格式。
- **适用场景**：自测。
- **限制**：只是夹具。

命令：`python3 -m json.tool file.json`，示例域名 example.com。
"""

# 夹具里的「脏东西」用拼接写：本脚本自己也要过自己的规则，否则一提交就被自己报错
_HOME = "/" + "home/alice/proj/app.py"
_OPT = "/" + "opt/secret/x"
_GH = "ghp_" + "ABCDEFGHIJKLMNOPQRST"
_MAIL = "some" + "one" + "@" + "example.org"
_IP = "192.168" + ".1.10"

DIRTY_MD = "\n".join([
    "# 脏文件",
    "",
    "- **一句话价值**：故意放脏东西。",
    "- **适用场景**：自测。",
    "- **限制**：自测。",
    "",
    "路径 %s 不该出现。" % _HOME,
    "token %s 也不该。" % _GH,
    "邮箱 %s 同理。" % _MAIL,
    "内网 %s 只算警告。" % _IP,
    "这一行 %s 放行吧  # lint:allow" % _OPT,
    "",
])


def self_test():
    tmp = tempfile.mkdtemp(prefix="curated-lint-selftest-")
    failures = []
    try:
        os.makedirs(os.path.join(tmp, "sub"))
        open(os.path.join(tmp, "clean.md"), "w", encoding="utf-8").write(CLEAN_MD)
        open(os.path.join(tmp, "sub", "dirty.md"), "w", encoding="utf-8").write(DIRTY_MD)
        open(os.path.join(tmp, "bad.md"), "wb").write(b"ok \xff\xfe broken\n")
        open(os.path.join(tmp, "ignore.bin"), "wb").write(b"\x00\x01 " + _HOME.encode())

        found = check_file(os.path.join(tmp, "clean.md"), tmp)
        if found:
            failures.append("干净夹具被报问题：%s" % [f["rule"] for f in found])

        dirty = check_file(os.path.join(tmp, "sub", "dirty.md"), tmp)
        got = {(f["rule"], f["level"]) for f in dirty}
        for want in [("posix-home-path", "error"), ("github-token", "error"),
                     ("email", "error"), ("private-ipv4", "warn")]:
            if want not in got:
                failures.append("脏夹具漏报 %s" % (want[0],))
        if any(f["rule"] == "posix-opt-path" for f in dirty):
            failures.append("lint:allow 没生效")
        if any(_HOME[:12] in f["snippet"] for f in dirty):
            failures.append("输出没有打码，自检自己泄密")

        broken = check_file(os.path.join(tmp, "bad.md"), tmp)
        if not any(f["rule"] == "not-utf8" for f in broken):
            failures.append("非 UTF-8 没报")

        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):  # 自测中间输出别刷屏
            bad_rc = run(tmp, strict=True, as_json=True)
        if bad_rc == 0:
            failures.append("有 error 时退出码不该是 0")

        clean_dir = os.path.join(tmp, "only-clean")
        os.makedirs(clean_dir)
        shutil.copy(os.path.join(tmp, "clean.md"), clean_dir)
        with contextlib.redirect_stdout(io.StringIO()):
            if run(clean_dir) != 0:
                failures.append("干净目录退出码应为 0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("自测失败：")
        for f in failures:
            print(" -", f)
        return 1
    print("自测通过：干净通过 / 脏的 5 类命中 / lint:allow 生效 / 打码生效 / 非 UTF-8 报错 / 退出码正确")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="curated 目录去敏与边界自检")
    ap.add_argument("path", nargs="?", help="要扫描的文件或目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="警告也算失败")
    ap.add_argument("--self-test", action="store_true", help="跑内置自测")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.path:
        ap.error("要么给路径，要么 --self-test")
    if not os.path.exists(args.path):
        print("路径不存在：%s" % args.path, file=sys.stderr)
        return 2
    return run(args.path, strict=args.strict, as_json=args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # 管道给 head/grep 时别吐一屏 traceback
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
