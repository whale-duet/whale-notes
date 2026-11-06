#!/usr/bin/env python3
"""写入证据链自检（只读）：工作区文件是否都和「最近一次受控写入」对得上。

每个文件算 sha256，与写入记录表里同一 (persona, scope, rel) 最近一条的 sha256 比对：
  OK        当前 sha == 最近记录（没被绕过）
  DRIFT     有记录但 sha 不符 → 记录之后被工具以外的方式改过（证据链断点）
  UNTRACKED 完全没有写入记录的文件

--explain 给每个 DRIFT 补一段「当前正文 vs 记录里的 previous_text」的 diff 摘要。
previous_text 是上一次受控写入之前的正文，这段 diff 覆盖「那次写入 + 之后的绕过改动」
之和：它说不清"是谁改的"，但能回答"断点之后变了什么、变了多少"。

假设一张表（列名不同就改参数）：
  writes(rel, persona, scope, sha256, chars, time, previous_text)

用法：
  python3 write-audit.py --db rec.sqlite --root ~/work --persona me --scope workspace \
      [--explain] [--explain-lines 40] [-v]
退出码：0 = 无 DRIFT；1 = 有 DRIFT；2 = 运行错误。
"""
import argparse
import difflib
import hashlib
import os
import sqlite3
import sys

EXTS = (".md", ".py", ".sh", ".json", ".jsonl", ".txt")
SKIP_DIRS = {".versions", "__pycache__", ".git", "node_modules"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def latest_writes(a):
    """每个 rel 取最近一条写入：(sha256, chars, time, previous_text)。"""
    sql = ("select %s, %s, %s, %s, %s from %s where persona = ? and scope = ? order by %s"
           % (a.rel_col, a.sha_col, a.char_col, a.time_col, a.prev_col, a.table, a.time_col))
    con = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    try:
        rows = con.execute(sql, (a.persona, a.scope)).fetchall()
    finally:
        con.close()
    latest = {}
    for rel, sha, chars, time, prev in rows:  # 时间升序，后写的覆盖前面的
        latest[rel] = (sha, chars, time, prev)
    return latest


def walk_files(root, exts):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(exts):
                full = os.path.join(dirpath, fn)
                yield os.path.relpath(full, root).replace(os.sep, "/"), full


def diff_summary(old, new, budget):
    a, b = old.splitlines(), new.splitlines()
    if a == b:
        return ["      （与 previous_text 完全一致：断点发生在那次写入之前的历史版本上）"], False
    out, added, removed, hunks, cur = [], 0, 0, [], []
    for line in difflib.unified_diff(a, b, fromfile="记录前", tofile="现在", lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = [line]
            continue
        added += line.startswith("+")
        removed += line.startswith("-")
        cur.append(line)
    if cur:
        hunks.append(cur)
    out.append("      +%d / -%d 行，%d 处 hunk" % (added, removed, len(hunks)))
    for hunk in hunks:
        for line in hunk:
            if budget <= 0:
                return out, True
            out.append("      " + line)
            budget -= 1
    return out, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="写入记录库（sqlite）")
    ap.add_argument("--root", required=True, help="要检查的工作区目录")
    ap.add_argument("--persona", default="")
    ap.add_argument("--scope", default="", help="记录里的 scope，例如 workspace")
    ap.add_argument("--table", default="writes")
    ap.add_argument("--rel-col", default="rel")
    ap.add_argument("--sha-col", default="sha256")
    ap.add_argument("--char-col", default="chars")
    ap.add_argument("--time-col", default="time")
    ap.add_argument("--prev-col", default="previous_text")
    ap.add_argument("--exts", default=",".join(EXTS), help="逗号分隔的扩展名")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--explain-lines", type=int, default=40)
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print("[ERROR] 找不到库 %s" % a.db)
        return 2
    if not os.path.isdir(a.root):
        print("[ERROR] 找不到工作区 %s" % a.root)
        return 2

    exts = tuple(e.strip() for e in a.exts.split(",") if e.strip())
    latest = latest_writes(a)
    ok, drift, untracked = [], [], []
    for rel, full in sorted(walk_files(a.root, exts)):
        want = latest.get(rel)
        if want is None:
            untracked.append(rel)
        elif sha256_file(full) == want[0]:
            ok.append(rel)
        else:
            drift.append((rel, want[0][:12], sha256_file(full)[:12], want[2], want[1], want[3]))

    print("== 写入证据链自检 persona=%s scope=%s ==" % (a.persona or "-", a.scope or "-"))
    print("OK        %d" % len(ok))
    print("DRIFT     %d" % len(drift))
    for rel, rec_sha, now_sha, t, rec_chars, prev in drift:
        now_chars = len(read_text(os.path.join(a.root, rel)) or "")
        print("  - %s  记录 %s / 现在 %s（记录时间 %s；%d → %d 字）"
              % (rel, rec_sha, now_sha, t, rec_chars, now_chars))
        if not a.explain:
            continue
        if prev is None:
            print("      （库里没有 previous_text：这是该文件的第一次受控写入）")
            continue
        now_text = read_text(os.path.join(a.root, rel))
        if now_text is None:
            print("      （读不到当前正文）")
            continue
        lines, cut = diff_summary(prev, now_text, a.explain_lines)
        print("\n".join(lines))
        if cut:
            print("      …（diff 已截断，用 --explain-lines 调大）")
    print("UNTRACKED %d" % len(untracked))
    for rel in untracked:
        print("  - %s" % rel)
    if a.verbose:
        print("已对上：")
        for rel in ok:
            print("  + %s" % rel)

    # 只有 DRIFT 影响证据链；UNTRACKED 仅提示（初始文件、脚本生成物都可能没记录）
    print("结论", "[OK] 没有断点" if not drift else "[WARN] 有 %d 处绕过记录的写入" % len(drift))
    return 0 if not drift else 1


if __name__ == "__main__":
    sys.exit(main())
