#!/usr/bin/env bash
# Прогнать gitleaks по негатив-фикстурам и проверить, что найден КАЖДЫЙ файл.
#
# Требовать все три, а не «хотя бы одну» — сознательно. Гард отвечает на
# вопрос «этот бинарь всё ещё ловит эталонные находки»; «хотя бы одна» тихо
# деградирует до одной работающей проверки, а именно так и протухла прошлая
# фикстура. Отвалившееся правило обязано назвать себя и уронить прогон, чтобы
# человек решил — правило переехало или фикстура устарела.
#
# usage: assert-gitleaks.sh <путь-к-бинарю> <версия-для-сообщений>
set -euo pipefail

bin="${1:?usage: assert-gitleaks.sh <binary> <version-label>}"
label="${2:-?}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fixtures="$(mktemp -d)"
report="$(mktemp -u)".json
"$here/secret-fixtures.sh" "$fixtures" >/dev/null

# `dir` появился в 8.19; в 8.18.x его нет, а `detect --no-git` объявлен
# устаревшим в пользу `dir`. Гард обязан работать по обе стороны бампа,
# поэтому пробуем новую форму и откатываемся на старую.
if ! "$bin" dir "$fixtures" --report-format json --report-path "$report" \
      --no-banner --exit-code 0 >/dev/null 2>&1; then
  "$bin" detect --no-git --source "$fixtures" --report-format json \
    --report-path "$report" --no-banner --exit-code 0 >/dev/null 2>&1
fi

python3 - "$report" "$label" <<'PY'
import json
import sys

report_path, label = sys.argv[1], sys.argv[2]
expected = {"aws.txt", "github-pat.txt", "slack.txt"}
try:
    findings = json.load(open(report_path, encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"::error::gitleaks {label}: отчёт не прочитался ({exc}) — гард не состоялся")

found = {}
for item in findings:
    found.setdefault(item.get("File", "").rsplit("/", 1)[-1], set()).add(item.get("RuleID", "?"))

missing = sorted(expected - set(found))
if missing:
    sys.exit(
        f"::error::gitleaks {label} не нашёл эталонные находки в {missing}. "
        "Либо правило переехало в апстриме, либо фикстура устарела — "
        "разобрать руками, не глушить."
    )
for name in sorted(expected):
    print(f"  {name}: {', '.join(sorted(found[name]))}")
print(f"gitleaks {label}: негатив ок — все {len(expected)} эталонные находки на месте")
PY
