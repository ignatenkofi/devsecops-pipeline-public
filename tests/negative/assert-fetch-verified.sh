#!/usr/bin/env bash
# Гард на гард: fetch-verified обязан ОТКАЗАТЬ, а не поставить.
#
# Смысл проверки суммы — в отказе. Скрипт, который сверяет сумму и всё
# равно ставит (или который «не нашёл запись» и счёл это отсутствием
# проверки), хуже отсутствия сверки: он выглядит защитой.
#
# Офлайн: источник — file://-URL во временном каталоге, поэтому фикстура
# не зависит от сети и от доступности апстрима (симметрично
# assert-gitleaks.sh / assert-osv.sh).
#
# Проверяется:
#   1. сумма сошлась            → бинарь на месте, исполняемый, rc=0
#   2. сумма НЕ сошлась         → rc≠0 И ФАЙЛА НЕТ (не «поставил и поругался»)
#   3. записи об ассете нет     → rc≠0 (переименование ассета апстримом не
#                                 имеет права молча отключать проверку)
#   4. архив: член распакован только после сверки
#   5. архив с НЕсошедшейся суммой → отказ (боевой режим стадий —
#      именно --member, и без этого случая гард слеп там, где работает)
#   6. --sha256 сошёлся          → поставлен (режим пина, для апстримов
#                                 без файла сумм — osv-scanner,
#                                 devsecops-pipeline-public#13)
#   7. --sha256 НЕ сошёлся       → rc≠0 И ФАЙЛА НЕТ
#   8. кривой --sha256           → rc=2 и ОТДЕЛЬНОЕ сообщение: опечатка в
#                                 пине не имеет права выглядеть как
#                                 сработавшая защита
#   9. --sums-url и --sha256 вместе → rc=2 (два источника истины — это
#                                 неопределённость, а не гибкость)
#  10. ни --sums-url, ни --sha256  → rc=2 (иначе «забыл аргумент» =
#                                 «скачал и поставил без сверки»)
#  11. --extract-all: сошлось     → архив распакован целиком (вложенная
#                                 раскладка — lychee)
#  12. --extract-all: НЕ сошлось  → отказ И КАТАЛОГ ПУСТ
#  13. два режима установки сразу / ни одного → rc=2
set -euo pipefail

FV="${1:-actions/fetch-verified/fetch_verified.sh}"
[ -f "$FV" ] || { echo "FAIL: не найден $FV"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SRC="$WORK/upstream"; mkdir -p "$SRC"

fail() { echo "FAIL: $*"; exit 1; }

printf '#!/bin/sh\necho i-am-the-tool\n' > "$SRC/tool_linux_amd64"
real_sum="$(sha256sum "$SRC/tool_linux_amd64" | awk '{print $1}')"

# ------------------------------------------------------------------ 1
printf '%s  tool_linux_amd64\n' "$real_sum" > "$SRC/SUMS.good"
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sums-url "file://$SRC/SUMS.good" \
  --dest "$WORK/ok" --output tool >"$WORK/ok.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || fail "честная сумма отвергнута (rc=$rc): $(cat "$WORK/ok.log")"
[ -x "$WORK/ok/tool" ] || fail "бинарь не установлен или не исполняемый"
[ "$("$WORK/ok/tool")" = "i-am-the-tool" ] || fail "установлен не тот файл"

# ------------------------------------------------------------------ 2
printf '%s  tool_linux_amd64\n' "$(printf 'подменённый' | sha256sum | awk '{print $1}')" \
  > "$SRC/SUMS.bad"
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sums-url "file://$SRC/SUMS.bad" \
  --dest "$WORK/bad" --output tool >"$WORK/bad.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "расхождение суммы НЕ уронило установку — гард бесполезен"
[ ! -e "$WORK/bad/tool" ] || fail "файл установлен несмотря на расхождение суммы"
grep -q 'не сошлась' "$WORK/bad.log" || fail "нет внятного сообщения о расхождении"

# ------------------------------------------------------------------ 3
printf '%s  soveršenno-drugoj-fajl\n' "$real_sum" > "$SRC/SUMS.absent"
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sums-url "file://$SRC/SUMS.absent" \
  --dest "$WORK/absent" --output tool >"$WORK/absent.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "отсутствие записи в файле сумм прошло как успех"
[ ! -e "$WORK/absent/tool" ] || fail "файл установлен без записи о нём в суммах"
grep -q 'нет записи' "$WORK/absent.log" || fail "нет внятного сообщения об отсутствии записи"

# ------------------------------------------------------------------ 4
( cd "$SRC" && tar -czf tool.tar.gz tool_linux_amd64 )
printf '%s  tool.tar.gz\n' "$(sha256sum "$SRC/tool.tar.gz" | awk '{print $1}')" > "$SRC/SUMS.tar"
rc=0
bash "$FV" --url "file://$SRC/tool.tar.gz" --sums-url "file://$SRC/SUMS.tar" \
  --dest "$WORK/tar" --member tool_linux_amd64 >"$WORK/tar.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || fail "честный архив отвергнут (rc=$rc): $(cat "$WORK/tar.log")"
[ -x "$WORK/tar/tool_linux_amd64" ] || fail "член архива не распакован или не исполняемый"

# ------------------------------------------------------------------ 5
# Отказ ОБЯЗАН проверяться и в режиме --member: именно им пользуются обе
# боевые стадии (gitleaks, actionlint), а --output в конвейере не зовёт
# никто. Пока этого случая не было, отключение сверки для архивов
# («tar всё равно упадёт на битом») оставляло фикстуру зелёной.
printf '%s  tool.tar.gz\n' "$(printf 'подменённый архив' | sha256sum | awk '{print $1}')" \
  > "$SRC/SUMS.tar.bad"
rc=0
bash "$FV" --url "file://$SRC/tool.tar.gz" --sums-url "file://$SRC/SUMS.tar.bad" \
  --dest "$WORK/tarbad" --member tool_linux_amd64 >"$WORK/tarbad.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "расхождение суммы АРХИВА не уронило установку — гард слеп к боевому режиму"
[ ! -e "$WORK/tarbad/tool_linux_amd64" ] || fail "член архива распакован несмотря на расхождение суммы"
grep -q 'не сошлась' "$WORK/tarbad.log" || fail "нет внятного сообщения о расхождении для архива"

# ------------------------------------------------------------------ 6
# Режим пина: сумма приходит не из сети, а аргументом. Проверяется тем же
# набором утверждений, что и --sums-url, — иначе второй режим оказался бы
# менее покрытым ровно потому, что он новее.
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sha256 "$real_sum" \
  --dest "$WORK/pin" --output tool >"$WORK/pin.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || fail "честный пин отвергнут (rc=$rc): $(cat "$WORK/pin.log")"
[ -x "$WORK/pin/tool" ] || fail "пин сошёлся, но бинарь не установлен"
[ "$("$WORK/pin/tool")" = "i-am-the-tool" ] || fail "по пину установлен не тот файл"

# ------------------------------------------------------------------ 7
wrong_sum="$(printf 'подменённый' | sha256sum | awk '{print $1}')"
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sha256 "$wrong_sum" \
  --dest "$WORK/pinbad" --output tool >"$WORK/pinbad.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "расхождение с пином НЕ уронило установку"
[ ! -e "$WORK/pinbad/tool" ] || fail "файл установлен несмотря на расхождение с пином"
grep -q 'не сошлась' "$WORK/pinbad.log" || fail "нет внятного сообщения о расхождении с пином"

# ------------------------------------------------------------------ 8
# Опечатка в пине обязана отличаться от подмены бинаря. Если кривой пин
# даёт то же «сумма не сошлась», разбираться пойдут в апстрим вместо
# собственного action.yml.
for bad_pin in "deadbeef" "$(printf '%064d' 0 | tr '0' 'z')"; do
  rc=0
  bash "$FV" --url "file://$SRC/tool_linux_amd64" --sha256 "$bad_pin" \
    --dest "$WORK/pinjunk" --output tool >"$WORK/pinjunk.log" 2>&1 || rc=$?
  [ "$rc" -eq 2 ] || fail "кривой пин '$bad_pin' дал rc=$rc, ожидался 2"
  [ ! -e "$WORK/pinjunk/tool" ] || fail "файл установлен при кривом пине '$bad_pin'"
  grep -q 'не сошлась' "$WORK/pinjunk.log" \
    && fail "кривой пин '$bad_pin' выдан за расхождение суммы — диагноз уводит не туда"
  grep -qE 'не шестнадцатеричный|длиной' "$WORK/pinjunk.log" \
    || fail "нет внятного сообщения о кривом пине '$bad_pin'"
done

# ------------------------------------------------------------------ 9
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sums-url "file://$SRC/SUMS.good" \
  --sha256 "$real_sum" --dest "$WORK/both" --output tool >"$WORK/both.log" 2>&1 || rc=$?
[ "$rc" -eq 2 ] || fail "два источника суммы сразу дали rc=$rc, ожидался 2"
[ ! -e "$WORK/both/tool" ] || fail "файл установлен при двух источниках суммы"

# ------------------------------------------------------------------ 10
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" \
  --dest "$WORK/none" --output tool >"$WORK/none.log" 2>&1 || rc=$?
[ "$rc" -eq 2 ] || fail "загрузка без источника суммы дала rc=$rc, ожидался 2"
[ ! -e "$WORK/none/tool" ] || fail "файл установлен вообще без сверки"

# ------------------------------------------------------------------ 11
# Вложенная раскладка: бинарь лежит НЕ в корне архива (так делает lychee —
# каталог с target-triple в имени). Режим --member тут неприменим, а
# «распаковать целиком» обязано сверять ровно так же.
mkdir -p "$SRC/nested-dir"
cp "$SRC/tool_linux_amd64" "$SRC/nested-dir/tool"
( cd "$SRC" && tar -czf nested.tar.gz nested-dir )
nested_sum="$(sha256sum "$SRC/nested.tar.gz" | awk '{print $1}')"
printf '%s  nested.tar.gz\n' "$nested_sum" > "$SRC/SUMS.nested"
rc=0
bash "$FV" --url "file://$SRC/nested.tar.gz" --sums-url "file://$SRC/SUMS.nested" \
  --dest "$WORK/nested" --extract-all >"$WORK/nested.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || fail "честный архив отвергнут в --extract-all (rc=$rc): $(cat "$WORK/nested.log")"
[ -f "$WORK/nested/nested-dir/tool" ] || fail "--extract-all не распаковал вложенный файл"

# ------------------------------------------------------------------ 12
printf '%s  nested.tar.gz\n' "$(printf 'подменённый вложенный' | sha256sum | awk '{print $1}')" \
  > "$SRC/SUMS.nested.bad"
rc=0
bash "$FV" --url "file://$SRC/nested.tar.gz" --sums-url "file://$SRC/SUMS.nested.bad" \
  --dest "$WORK/nestedbad" --extract-all >"$WORK/nestedbad.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "расхождение суммы не уронило --extract-all"
[ -z "$(ls -A "$WORK/nestedbad" 2>/dev/null)" ] || fail "--extract-all распаковал несошедшийся архив"
grep -q 'не сошлась' "$WORK/nestedbad.log" || fail "нет внятного сообщения о расхождении для --extract-all"

# ------------------------------------------------------------------ 13
# Режим установки ровно один. Проверяются оба края: и «задано два», и
# «не задано ничего» — второе опаснее, потому что выглядит как опечатка,
# а означало бы «скачали и ничего не сделали» на зелёном шаге.
for bad_modes in "--member tool_linux_amd64 --output tool" \
                 "--member tool_linux_amd64 --extract-all" \
                 "--output tool --extract-all" \
                 ""; do
  rc=0
  # shellcheck disable=SC2086
  bash "$FV" --url "file://$SRC/tool_linux_amd64" --sums-url "file://$SRC/SUMS.good" \
    --dest "$WORK/modes" $bad_modes >"$WORK/modes.log" 2>&1 || rc=$?
  [ "$rc" -eq 2 ] || fail "набор режимов '$bad_modes' дал rc=$rc, ожидался 2"
  [ -z "$(ls -A "$WORK/modes" 2>/dev/null)" ] || fail "что-то установлено при наборе режимов '$bad_modes'"
done

echo "OK: fetch-verified ставит только сошедшееся — три режима установки, два источника суммы"
