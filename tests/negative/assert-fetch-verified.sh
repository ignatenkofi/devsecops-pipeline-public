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

echo "OK: fetch-verified ставит только сошедшееся и отказывает в обоих режимах"
