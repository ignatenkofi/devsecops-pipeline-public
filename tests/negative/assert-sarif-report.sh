#!/usr/bin/env bash
# Гард приёмника отчётов: sarif-report обязан РАЗЛИЧАТЬ три состояния файла.
#
# До этой фикстуры summarize-логика жила инлайном в action.yml и сводила их
# к двум: нечитаемый SARIF показывался как "?" на зелёном прогоне, а каталог
# без единого *.sarif печатал фантомную строку "| *.sarif | — (skip) |"
# (bash без nullglob отдаёт неразвернувшийся шаблон). То есть сломанный
# converter и упавшая до записи стадия выглядели как «стадия пропущена».
#
# Проверяется:
#   1. валидный SARIF        → число находок в таблице, выход 0
#   2. пустой файл (0 байт)  → "skip" (намеренный путь: стадия неприменима)
#   3. нечитаемый SARIF      → ВЫХОД 1 + файл назван в сводке
#   4. каталог без *.sarif   → выход 0, но НИ "skip", НИ "*.sarif" в сводке
#
# Пункт 3 — тот, ради которого фикстура существует: он превращает приёмник
# из «не может провалиться» в «падает на сломанном отчёте».
set -euo pipefail

SUMMARIZE="${1:-actions/sarif-report/summarize.py}"
[ -f "$SUMMARIZE" ] || { echo "FAIL: не найден $SUMMARIZE"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*"; exit 1; }

# ---------------------------------------------------------------- 1..3
mixed="$WORK/mixed"; mkdir -p "$mixed"
python3 - "$mixed/good.sarif" <<'PY'
import json, sys
json.dump({"version": "2.1.0", "runs": [{"results": [{"ruleId": "A"}, {"ruleId": "B"}]}]},
          open(sys.argv[1], "w"))
PY
: > "$mixed/empty.sarif"
printf '{"runs": [ oborvalos' > "$mixed/broken.sarif"

out="$WORK/mixed.md"
rc=0
python3 "$SUMMARIZE" --sarif-dir "$mixed" --out "$out" >"$WORK/mixed.log" 2>&1 || rc=$?

[ "$rc" -eq 1 ] || fail "нечитаемый SARIF не уронил приёмник (выход $rc, ожидался 1)"
grep -q '| good.sarif | 2 |'     "$out" || fail "валидный SARIF не посчитан: $(cat "$out")"
grep -q '| empty.sarif | — (skip) |' "$out" || fail "пустой SARIF не помечен skip"
grep -q 'broken.sarif'           "$out" || fail "нечитаемый SARIF не назван в сводке"
grep -q 'не читается'            "$out" || fail "нечитаемый SARIF показан как обычная строка"
grep -qi '^::error::'            "$WORK/mixed.log" || fail "нет ::error:: для раннера"

# Формулировка «?» — ровно то, что чинится: она не должна вернуться.
grep -q '| broken.sarif | ? |' "$out" && fail "нечитаемый SARIF снова показан как '?'"

# ------------------------------------------------------------------- 4
bare="$WORK/bare"; mkdir -p "$bare"
out2="$WORK/bare.md"
rc2=0
python3 "$SUMMARIZE" --sarif-dir "$bare" --out "$out2" >"$WORK/bare.log" 2>&1 || rc2=$?

[ "$rc2" -eq 0 ] || fail "пустой каталог не должен ронять приёмник (выход $rc2)"
grep -q 'skip'     "$out2" && fail "каталог без отчётов выдан за пропущенную стадию"
grep -q '\*\.sarif' "$out2" && fail "фантомная строка из неразвернувшегося шаблона вернулась"
grep -q 'ни одна стадия не записала отчёт' "$out2" \
  || fail "состояние «отчётов нет» не названо вслух: $(cat "$out2")"
grep -qi '^::warning::' "$WORK/bare.log" || fail "нет ::warning:: для раннера"

echo "OK: sarif-report различает валидный / пустой / нечитаемый SARIF и пустой каталог"
