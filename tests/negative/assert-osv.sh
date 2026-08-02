#!/usr/bin/env bash
# Прогнать osv-scanner по негатив-фикстуре и проверить, что найдена ИМЕННО
# эталонная уязвимость, а не «хоть что-нибудь».
#
# Зачем содержательный ассерт, а не только код возврата. Прошлый гард
# (inline в nightly-bump) проверял ровно `rc == 1`. Единица у osv-scanner
# означает «найдены уязвимости», но получить её можно и по другому поводу —
# на другом пакете, на транзитивной зависимости, на находке, приехавшей из
# нового резолвера. Гард отвечает на вопрос «этот бинарь всё ещё находит
# известную уязвимость в лок-файле», и ответ на него даёт идентификатор
# находки, а не число на выходе.
#
# Почему эталон — GHSA-462w-v97r-4m45 (CVE-2019-10906, sandbox escape в
# Jinja2 < 2.10.1). Advisory опубликовано в 2019, входит в базу OSV с
# момента её наполнения, относится к точной версии из фикстуры и не имеет
# шансов «переехать»: пакет мёртвый, версия зафиксирована. Набор CVE у
# jinja2 2.10 со временем растёт — требовать его целиком значит завести
# гард, который протухнет на следующем добавлении. Требуем один стабильный
# идентификатор и печатаем все найденные.
#
# usage: assert-osv.sh <путь-к-бинарю> <версия-для-сообщений>
set -euo pipefail

bin="${1:?usage: assert-osv.sh <binary> <version-label>}"
label="${2:-?}"

fixture="$(mktemp -d)"
report="$(mktemp -u)".json

# jinja2 2.10 несёт давно опубликованные CVE — эталонная находка в живой
# базе OSV (стадия sca в проде тоже бьётся в живую базу, офлайн-режим не
# используется).
printf 'jinja2==2.10\n' > "$fixture/requirements.txt"

# Флаги — те же, что у стадии sca (actions/osv-scanner/action.yml), иначе гард
# отвечает не на тот вопрос: «бинарь умеет находить» при вызове, которого у нас
# нет, ничего не гарантирует. Форма команды разъехалась на мажоре 2.0 —
# `scan source`, --output-file вместо --output, --no-resolve против включённого
# по умолчанию похода в deps.dev, --all-vulns против нового умолчания скрывать
# unimportant/uncalled. Гард обязан работать по обе стороны бампа, поэтому
# пробуем новую форму и откатываемся на старую (та же схема, что в
# assert-gitleaks.sh).
set +e
"$bin" scan source --recursive --no-resolve --all-vulns --format json \
  --output-file "$report" "$fixture" >/dev/null 2>&1
rc=$?
if [ ! -s "$report" ]; then
  "$bin" scan --recursive --format json --output "$report" "$fixture" >/dev/null 2>&1
  rc=$?
fi
set -e

python3 - "$report" "$label" "$rc" <<'PY'
import json
import sys

report_path, label, rc = sys.argv[1], sys.argv[2], sys.argv[3]
EXPECTED = "GHSA-462w-v97r-4m45"  # CVE-2019-10906, Jinja2 sandbox escape

if rc != "1":
    sys.exit(
        f"::error::osv-scanner {label}: ожидал rc=1 на jinja2==2.10, получил rc={rc}. "
        "Либо сменилась семантика кодов возврата, либо сканер перестал видеть "
        "фикстуру — разобрать руками, не глушить."
    )

try:
    report = json.load(open(report_path, encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"::error::osv-scanner {label}: отчёт не прочитался ({exc}) — гард не состоялся")

# Форма JSON у 1.x и 2.x одна: results[].packages[].vulnerabilities[].
# Собираем идентификаторы вместе с алиасами: эталон может приехать как id
# находки или как alias у CVE-записи.
found = set()
for result in report.get("results", []):
    for package in result.get("packages", []):
        for vuln in package.get("vulnerabilities", []):
            if vuln.get("id"):
                found.add(vuln["id"])
            found.update(vuln.get("aliases", []))

if not found:
    sys.exit(
        f"::error::osv-scanner {label}: rc=1, но в отчёте нет ни одной "
        "уязвимости — формат отчёта разъехался с гардом."
    )

if EXPECTED not in found:
    sys.exit(
        f"::error::osv-scanner {label} не нашёл эталонную находку {EXPECTED} "
        f"в jinja2==2.10. Найдено: {', '.join(sorted(found))}. Либо advisory "
        "переехало в апстриме, либо сканер перестал разбирать requirements.txt "
        "— разобрать руками, не глушить."
    )

print(f"  jinja2 2.10: {', '.join(sorted(found))}")
print(f"osv-scanner {label}: негатив ок — эталонная находка {EXPECTED} на месте")
PY
