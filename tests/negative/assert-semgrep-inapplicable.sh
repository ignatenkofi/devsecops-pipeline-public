#!/usr/bin/env bash
# Фикстура: определение применимости стадии sast-semgrep (#33).
#
# Что доказывается: applicability.py умеет сказать «ноль» (неприменимо),
# «сколько-то» (применимо) и «не знаю» (сломанный вход) — тремя разными
# кодами, а не двумя. Гард, который не умеет провалиться, гардом не
# является; здесь провалов три, по одному на каждое состояние.
#
# Отдельно ассертится ПРОВОДКА: сам action.yml обязан звать помощник и
# заводить маркер. Логика, не подключённая к стадии, зелёная и
# бесполезная — этим уже кончилась история со skip-stages.
#
# Вход:  assert-semgrep-inapplicable.sh [<корень репо>]
set -euo pipefail

ROOT="${1:-.}"
HELPER="$ROOT/actions/semgrep/applicability.py"
ACTION="$ROOT/actions/semgrep/action.yml"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fails=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }

# Замер кода возврата ВСЕГДА начинается с out=$(...); rc=$? — без пайпов
# и без подстановок после измеряемой команды: и пайп, и $(…) в списке
# аргументов затирают $?, и этот портфель на обоих уже обжигался.
run_helper() { # $1=файл отчёта -> печатает "rc|stdout"
  local out rc
  out="$(python3 "$HELPER" "$1" 2>/dev/null)" && rc=0 || rc=$?
  printf '%s|%s' "$rc" "$out"
}

[ -f "$HELPER" ] || { echo "нет $HELPER"; exit 2; }
[ -f "$ACTION" ] || { echo "нет $ACTION"; exit 2; }

echo "1. неприменимая стадия: paths.scanned пуст (Swift-репо, #33)"
printf '{"results": [], "paths": {"scanned": []}}\n' > "$TMP/none.json"
res="$(run_helper "$TMP/none.json")"
[ "$res" = "0|0" ] && ok "rc=0, вывод 0 — неприменимо" || bad "ожидал '0|0', получил '$res'"

echo "2. положительный контроль: стадия применима"
printf '{"results": [], "paths": {"scanned": ["a.py", "b.py"]}}\n' > "$TMP/two.json"
res="$(run_helper "$TMP/two.json")"
[ "$res" = "0|2" ] && ok "rc=0, вывод 2 — применимо" || bad "ожидал '0|2', получил '$res'"
# Без этого контроля помощник, всегда печатающий 0, прошёл бы шаг 1 и
# объявил бы неприменимой ЛЮБУЮ стадию — то есть заглушил бы SAST целиком.

echo "3. состояние неизвестно ≠ неприменимо"
printf 'не json вовсе\n' > "$TMP/broken.json"
res="$(run_helper "$TMP/broken.json")"
[ "${res%%|*}" = "2" ] && ok "битый JSON -> rc=2" || bad "битый JSON: ожидал rc=2, получил '$res'"

printf '{"results": []}\n' > "$TMP/nopaths.json"
res="$(run_helper "$TMP/nopaths.json")"
[ "${res%%|*}" = "2" ] && ok "нет paths -> rc=2" || bad "нет paths: ожидал rc=2, получил '$res'"

printf '{"paths": {"scanned": "не список"}}\n' > "$TMP/wrongtype.json"
res="$(run_helper "$TMP/wrongtype.json")"
[ "${res%%|*}" = "2" ] && ok "scanned не список -> rc=2" || bad "scanned не список: ожидал rc=2, получил '$res'"

res="$(run_helper "$TMP/нет-такого-файла.json")"
[ "${res%%|*}" = "2" ] && ok "отчёта нет -> rc=2" || bad "отчёта нет: ожидал rc=2, получил '$res'"

echo "4. проводка: action.yml зовёт помощник и заводит маркер"
grep -q 'applicability\.py' "$ACTION" \
  && ok "action.yml зовёт applicability.py" \
  || bad "action.yml не зовёт applicability.py — логика не подключена"
grep -q '\.inapplicable' "$ACTION" \
  && ok "action.yml заводит маркер .inapplicable" \
  || bad "action.yml не пишет маркер — гейту нечего читать"
grep -q -- '--json-output' "$ACTION" \
  && ok "action.yml просит JSON-отчёт" \
  || bad "action.yml не просит --json-output — paths.scanned взять неоткуда"

echo
if [ "$fails" -eq 0 ]; then
  echo "assert-semgrep-inapplicable: зелёный"
else
  echo "assert-semgrep-inapplicable: провалов $fails"
  exit 1
fi
