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
#  14. файл сумм ЗАГЛАВНЫМИ (PowerShell Get-FileHash, certutil) → сходится
#  15. --member, которого в архиве нет → отказ, а не «успех с пустым dest»
#  16. установлены ИМЕННО сверенные байты, а не скачанные повторно
#  17. TLS не отключён: самоподписанный https обязан быть отвергнут
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

# ------------------------------------------------------------------ 14
# sha256sum печатает строчными, а файл сумм, перегенерированный на Windows
# (Get-FileHash, certutil), приходит заглавными. Без нормализации регистра
# нетронутый ассет отвергался бы как подменённый.
printf '%s  tool_linux_amd64\n' "$(printf '%s' "$real_sum" | tr 'a-f' 'A-F')" > "$SRC/SUMS.upper"
rc=0
bash "$FV" --url "file://$SRC/tool_linux_amd64" --sums-url "file://$SRC/SUMS.upper" \
  --dest "$WORK/upper" --output tool >"$WORK/upper.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || fail "сумма ЗАГЛАВНЫМИ отвергнута (rc=$rc): $(cat "$WORK/upper.log")"
[ -x "$WORK/upper/tool" ] || fail "сумма заглавными сошлась, а бинарь не установлен"

# ------------------------------------------------------------------ 15
# Сумма сошлась, а установка провалилась — отдельный исход, и он обязан
# быть отказом. Пока такого случая не было, снятие `set -e` в шапке
# оставляло фикстуру зелёной: шаг печатал «распакован» при пустом dest.
rc=0
bash "$FV" --url "file://$SRC/tool.tar.gz" --sums-url "file://$SRC/SUMS.tar" \
  --dest "$WORK/nomember" --member нет-такого-члена >"$WORK/nomember.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "отсутствующий член архива дал успех — провал установки не отличается от установки"
[ -z "$(ls -A "$WORK/nomember" 2>/dev/null)" ] || fail "что-то установлено при отсутствующем члене"

# ------------------------------------------------------------------ 16
# Наружу обязаны уехать ИМЕННО сверенные байты. Сверить одну загрузку и
# отдать другую — не выдумка: «качать сразу в dest, не копировать между
# ФС» выглядит как безобидный рефактор, а сверка при этом остаётся на
# месте и продолжает сверять первую загрузку.
#
# Сервер отдаёт разное содержимое на первый и последующие GET ассета.
# Правильная реализация качает ассет один раз, поэтому установлен будет
# первый вариант; повторная загрузка принесёт второй.
cat > "$WORK/flip.py" <<'PY'
import hashlib, http.server, socketserver
FIRST = b'#!/bin/sh\necho verified-bytes\n'
LATER = b'#!/bin/sh\necho SWAPPED\n'
seen = {"n": 0}

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("/SUMS"):
            body = (hashlib.sha256(FIRST).hexdigest() + "  asset\n").encode()
        else:
            seen["n"] += 1
            body = FIRST if seen["n"] == 1 else LATER
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

srv = socketserver.TCPServer(("127.0.0.1", 0), H)
print(srv.server_address[1], flush=True)
srv.serve_forever()
PY
python3 "$WORK/flip.py" > "$WORK/flip.port" 2>"$WORK/flip.err" &
flip_pid=$!
trap 'kill "$flip_pid" 2>/dev/null || true; rm -rf "$WORK"' EXIT
for _ in $(seq 1 50); do [ -s "$WORK/flip.port" ] && break; sleep 0.1; done
port="$(head -1 "$WORK/flip.port")"
[ -n "$port" ] || fail "сервер фикстуры не поднялся: $(cat "$WORK/flip.err")"
rc=0
bash "$FV" --url "http://127.0.0.1:$port/asset" --sums-url "http://127.0.0.1:$port/SUMS" \
  --dest "$WORK/bytes" --output tool >"$WORK/bytes.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || fail "честная загрузка по http отвергнута (rc=$rc): $(cat "$WORK/bytes.log")"
got="$("$WORK/bytes/tool")"
[ "$got" = "verified-bytes" ] \
  || fail "установлены НЕ сверенные байты (получено '$got') — сверка и установка смотрят на разные загрузки"

# ------------------------------------------------------------------ 17
# Проверка сертификата не имеет права быть отключённой: `-k` в curl —
# правка на одну букву, которую делают «чтобы обойти TLS-инспектор», и
# она молча снимает защиту транспорта. Все остальные случаи ходят по
# file:// и такую правку не различают вовсе.
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$WORK/k.pem" -out "$WORK/c.pem" \
  -days 1 -subj "/CN=127.0.0.1" >/dev/null 2>&1 || fail "не удалось сгенерировать сертификат"
cat > "$WORK/tls.py" <<'PY'
import http.server, socketserver, ssl, sys

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"whatever"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(sys.argv[1], sys.argv[2])
srv = socketserver.TCPServer(("127.0.0.1", 0), H)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
print(srv.server_address[1], flush=True)
srv.serve_forever()
PY
python3 "$WORK/tls.py" "$WORK/c.pem" "$WORK/k.pem" > "$WORK/tls.port" 2>"$WORK/tls.err" &
tls_pid=$!
trap 'kill "$flip_pid" "$tls_pid" 2>/dev/null || true; rm -rf "$WORK"' EXIT
for _ in $(seq 1 50); do [ -s "$WORK/tls.port" ] && break; sleep 0.1; done
tls_port="$(head -1 "$WORK/tls.port")"
[ -n "$tls_port" ] || fail "https-сервер фикстуры не поднялся: $(cat "$WORK/tls.err")"
# Пин ОБЯЗАН совпадать с тем, что сервер отдаёт: иначе отказ пришёл бы от
# несошедшейся суммы, и случай не различал бы «TLS проверен» и «TLS снят,
# но сумма не та» — то есть был бы зелёным при снятом `-k`. Проверено
# мутацией: первая версия этого случая ровно так и промолчала.
tls_body_sum="$(printf 'whatever' | sha256sum | awk '{print $1}')"
rc=0
bash "$FV" --url "https://127.0.0.1:$tls_port/asset" --sha256 "$tls_body_sum" \
  --dest "$WORK/tls" --output tool >"$WORK/tls.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "самоподписанный сертификат принят — проверка TLS отключена"
[ ! -e "$WORK/tls/tool" ] || fail "файл установлен с недоверенного https"

echo "OK: fetch-verified ставит только сверенные байты — 17 случаев, три режима, два источника суммы"
