#!/usr/bin/env bash
# mutation-check.sh —— 变异检查模板：确认「测试真的钉住了那条保护点」
#
# 一句话价值：改一处源码、跑一遍定向测试、看它是否变红、再原样还回去——把「测试是绿的」
#             升级成「保护点没了测试就会红」。全自动，一条命令给出 RED/GREEN 表格。
#
# 用法：
#   1. 复制本脚本到被测仓库根目录（或任意位置，用 TREE 指向仓库根）；
#   2. 顶部改 SPEC 和 MUTANTS；
#   3. ./mutation-check.sh
#
# MUTANTS 每行：文件|原文|替换成|标签
#   原文必须在该文件里**只出现一次**，否则脚本直接放弃（避免改错地方）。
#
# 环境变量：
#   TREE    仓库根（默认当前目录）
#   RUNNER  跑测试的命令（默认 "npx vitest run"，会自动追加 spec 路径）
#   TIMEOUT 单次运行的秒数上限（默认 600）
set -u

TREE=${TREE:-$PWD}
RUNNER=${RUNNER:-npx vitest run}
TIMEOUT=${TIMEOUT:-600}
SPEC=${SPEC:?请先设置 SPEC（要跑的定向测试文件）}

cd "$TREE" || { echo "TREE 不存在：$TREE"; exit 1; }

MUTANTS=(
  # 例：src/app/effects.ts|return Number(result.changes) === 1|return true|claim 恒真
)

strip_ansi() { sed 's/\x1b\[[0-9;]*m//g'; }

run() { # $1 = 标签；输出一行结论
  local out rc tally
  out=$(timeout "$TIMEOUT" $RUNNER "$SPEC" 2>&1); rc=$?
  tally=$(printf '%s\n' "$out" | strip_ansi | grep -oE 'Tests +[0-9]+ (failed|passed)[^)]*\)' | tail -1)
  if printf '%s\n' "$out" | grep -qE 'Tests +[0-9]+ failed'; then
    echo "  [$1] RED   (${tally:-no tally})"
    printf '%s\n' "$out" | strip_ansi | grep -E '×|✕|AssertionError' | head -3 | sed 's/^ */        /'
    return 0
  elif printf '%s\n' "$out" | grep -qE 'Tests +[0-9]+ passed'; then
    echo "  [$1] GREEN (${tally:-no tally})"
    return 1
  else
    echo "  [$1] ??? 读不出用例计数（rc=$rc），原文尾巴："
    printf '%s\n' "$out" | strip_ansi | tail -12 | sed 's/^ */        /'
    return 2
  fi
}

mutate_and_run() { # $1 文件 $2 原文 $3 替换 $4 标签
  local f=$1 old=$2 new=$3 label=$4
  [ -f "$f" ] || { echo "  [$label] 跳过：没有 $f"; return 2; }
  cp "$f" "$f.mutbak"
  if ! OLD="$old" NEW="$new" python3 - "$f" <<'PY'
import os, sys
p = sys.argv[1]
old, new = os.environ["OLD"], os.environ["NEW"]
s = open(p, encoding="utf-8").read()
n = s.count(old)
if n != 1:
    sys.stderr.write("  !! %s: 原文出现 %d 次，放弃这处变异\n" % (p, n))
    sys.exit(2)
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
  then rm -f "$f.mutbak"; return 2; fi
  run "$label"
  local verdict=$?
  mv -f "$f.mutbak" "$f"
  return $verdict
}

echo "== 基线（未变异，必须是 GREEN）"
run baseline
case $? in
  1) echo "  基线绿，可以开始变异";;
  0) echo "  !! 基线本来就是红的：先修好测试再谈变异"; exit 1;;
  *) echo "  !! 基线读不出结论，先看上面原文"; exit 1;;
esac

echo "== 逐处变异"
red=0; green=0; other=0
for m in "${MUTANTS[@]}"; do
  IFS='|' read -r f old new label <<<"$m"
  [ -z "${label:-}" ] && continue
  mutate_and_run "$f" "$old" "$new" "$label"
  case $? in
    0) red=$((red+1));;
    1) green=$((green+1)); echo "      ^ 这处保护没被测试钉住";;
    *) other=$((other+1));;
  esac
done

echo "== 还原核对（工作树应只剩本轮真改动）"
if [ -d .git ]; then git status --porcelain=v1 | sed 's/^/  /'; fi

echo "== 小结：RED(钉住) $red 处 / GREEN(没钉住) $green 处 / 其他 $other 处"
[ "$green" -eq 0 ] && [ "$other" -eq 0 ] && exit 0 || exit 1
