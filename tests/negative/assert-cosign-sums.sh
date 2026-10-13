#!/usr/bin/env bash
# Фикстура: проверка подписи файла сумм через cosign (шаг 4 в
# devsecops-pipeline#24).
#
# Почему боевой режим — keyless, а не ключевой. Ключевой режим cosign
# полностью офлайновый и различает подделку, поэтому соблазн написать
# фикстуру на нём велик. Это была бы ровно та ошибка, что уже записана в
# граблях портфеля: фикстура доказывала бы отказ на пути, которым прод не
# ходит. Апстримы подписывают keyless (Fulcio + Rekor), значит и отказ
# обязан проверяться keyless — то есть там, где sigstore достижим.
#
# Из ночного контейнера sigstore закрыт прокси (проверено 2026-08-04:
# `tuf: failed to download 10.root.json … Forbidden`), поэтому случаи 3–8
# зелёные только в CI. Скипа у них НЕТ намеренно: молча пропущенная
# проверка неотличима от пройденной, а это и есть болезнь, которую чинит
# #33. Локально фикстура честно краснеет. Сети не требует только случай 1.
#
# Формы подписи две, и отказать обязана каждая (devsecops-pipeline#98):
# пара .sig/.pem (случаи 3–5, syft до 1.52.0 включительно) и sigstore
# bundle (случаи 6–8, syft с 1.54.0; 1.53 не выпускалась). syft-sbom
# выбирает форму по версии, так что живы обе ветки.
#
# Вход: assert-cosign-sums.sh <путь к fetch_verified.sh> [<путь к cosign>]
set -euo pipefail

FV="${1:?нужен путь к fetch_verified.sh}"
COSIGN="${2:-${COSIGN_BIN:-}}"

# Пин самого верификатора. Скрипт, который сам себе качает cosign, проверять
# его нечем; поэтому cosign ставится тем же fetch_verified.sh с --sha256, а
# сюда приходит готовым.
COSIGN_VERSION="3.1.3"
COSIGN_SHA="4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71"

# Единственный инструмент портфеля, чей апстрим публикует подписи (замер
# 2026-08-04: у gitleaks их нет — 13 проб при живом контроле).
SYFT_VERSION="1.50.0"
SYFT_BASE="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}"
SUMS_URL="${SYFT_BASE}/syft_${SYFT_VERSION}_checksums.txt"
SIG_URL="${SUMS_URL}.sig"
CERT_URL="${SUMS_URL}.pem"
IDENTITY='^https://github\.com/anchore/syft/\.github/workflows/release\.yaml@refs/heads/main$'
ISSUER="https://token.actions.githubusercontent.com"
# Первые релизы syft только с bundle — 1.54.0 и 1.54.1 (список ассетов
# релизов, 2026-10-07); на 1.54.1 упал ночной бамп (devsecops-pipeline#102).
# Личность и издатель те же.
BUNDLE_VERSION="1.54.1"
BUNDLE_BASE="https://github.com/anchore/syft/releases/download/v${BUNDLE_VERSION}"
BUNDLE_SUMS_URL="${BUNDLE_BASE}/syft_${BUNDLE_VERSION}_checksums.txt"
BUNDLE_URL="${BUNDLE_SUMS_URL}.sigstore.json"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }

# Код возврата снимается БЕЗ пайпов и без подстановок после измеряемой
# команды: и то и другое затирает $?, и портфель на этом уже обжигался.
run_fv() { # -> rc в $RC, вывод в $OUT
  set +e
  OUT="$(bash "$FV" "$@" 2>&1)"
  RC=$?
  set -e
}

echo "1. конфигурация и сборка вызова cosign (сети не требуют)"

run_fv --url http://example.invalid/a.tgz --sums-url http://example.invalid/S \
       --dest "$TMP/d" --output a --cosign-bin /bin/true
[ "$RC" = "2" ] && ok "частичный набор cosign-флагов -> rc=2" \
                || bad "частичный набор: ожидал rc=2, получил $RC"

run_fv --url http://example.invalid/a.tgz --sha256 "$(printf 'a%.0s' $(seq 64))" \
       --dest "$TMP/d" --output a \
       --cosign-bin /bin/true --cosign-sig u --cosign-cert u \
       --cosign-identity i --cosign-issuer s
[ "$RC" = "2" ] && ok "cosign вместе с --sha256 -> rc=2" \
                || bad "cosign+--sha256: ожидал rc=2, получил $RC"
# Без этого случая cosign молча ничего не проверял бы при пине: подписывается
# файл сумм, а его в этом режиме нет вовсе.

# Bundle (#98). rc=2 даёт и «неизвестный аргумент», поэтому сверяется ещё и
# текст: отказ обязан быть отказом правила cosign-флагов.
cosign_rule() { # <что> — rc=2 и текст правила, иначе провал
  if [ "$RC" = "2" ] && printf '%s' "$OUT" | grep -q 'ровно одну форму подписи'; then
    ok "$1 -> rc=2"
  else
    bad "$1: ожидал rc=2 по правилу cosign-флагов, получил rc=$RC"
    printf '%s\n' "$OUT" | sed 's/^/       /'
  fi
}
run_fv --url http://example.invalid/a.tgz --sums-url http://example.invalid/S \
       --dest "$TMP/d" --output a \
       --cosign-bin /bin/true --cosign-sig u --cosign-cert u --cosign-bundle u \
       --cosign-identity i --cosign-issuer s
cosign_rule "обе формы подписи сразу (sig/cert и bundle)"
run_fv --url http://example.invalid/a.tgz --sums-url http://example.invalid/S \
       --dest "$TMP/d" --output a --cosign-bin /bin/true --cosign-bundle u
cosign_rule "bundle без личности и издателя"

# Сборка вызова verify-blob — заглушкой вместо cosign, по file://. Подпись
# заглушка не проверяет (это делают случаи 3–8 настоящим cosign): она пишет
# аргументы и отвечает кодом STUB_RC. Проверяется, ЧТО скрипт передаёт
# верификатору в каждой форме и что отказ верификатора — отказ установки.
cat > "$TMP/cosign-stub" <<'EOF'
#!/usr/bin/env bash
printf '%s ' "$@" > "$STUB_LOG"
exit "$STUB_RC"
EOF
chmod +x "$TMP/cosign-stub"
export STUB_LOG="$TMP/stub.log" STUB_RC=0
printf 'ассет\n' > "$TMP/asset.bin"
printf '%s  asset.bin\n' "$(sha256sum "$TMP/asset.bin" | awk '{print $1}')" > "$TMP/stub-sums.txt"
printf 'bundle\n' > "$TMP/stub.sigstore.json"
printf 'sig\n' > "$TMP/stub.sig"
printf 'pem\n' > "$TMP/stub.pem"
stub_fv() { # <dest> <флаги формы подписи...>
  local dest="$1"; shift
  : > "$STUB_LOG"
  run_fv --url "file://$TMP/asset.bin" --sums-url "file://$TMP/stub-sums.txt" \
         --dest "$dest" --output asset --cosign-bin "$TMP/cosign-stub" \
         --cosign-identity I --cosign-issuer S "$@"
}
stub_fv "$TMP/stub-b" --cosign-bundle "file://$TMP/stub.sigstore.json"
case "$RC $(cat "$STUB_LOG")" in
  "0 verify-blob --bundle "*"/SUMS.sigstore.json --certificate-identity-regexp I --certificate-oidc-issuer S "*"/SUMS ")
    ok "bundle: verify-blob --bundle <файл> с личностью и издателем" ;;
  *) bad "bundle: вызов verify-blob собран не так (rc=$RC): $(cat "$STUB_LOG")"
     printf '%s\n' "$OUT" | sed 's/^/       /' ;;
esac
stub_fv "$TMP/stub-s" --cosign-sig "file://$TMP/stub.sig" --cosign-cert "file://$TMP/stub.pem"
case "$RC $(cat "$STUB_LOG")" in
  "0 verify-blob --certificate "*"/SUMS.pem --signature "*"/SUMS.sig --certificate-identity-regexp I --certificate-oidc-issuer S "*"/SUMS ")
    ok "sig/cert: verify-blob --certificate/--signature с личностью и издателем" ;;
  *) bad "sig/cert: вызов verify-blob собран не так (rc=$RC): $(cat "$STUB_LOG")"
     printf '%s\n' "$OUT" | sed 's/^/       /' ;;
esac
STUB_RC=1
stub_fv "$TMP/stub-f" --cosign-bundle "file://$TMP/stub.sigstore.json"
STUB_RC=0
if [ "$RC" = "1" ] && printf '%s' "$OUT" | grep -q 'подпись файла сумм НЕ прошла' \
   && [ ! -e "$TMP/stub-f/asset" ]; then
  ok "bundle: отказ верификатора -> rc=1, ничего не установлено"
else
  bad "bundle: отказ верификатора не стал отказом установки (rc=$RC)"
fi

echo "2. cosign под рукой"
if [ -z "$COSIGN" ] || [ ! -x "$COSIGN" ]; then
  COSIGN="$TMP/cosign"
  bash "$FV" --url "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-amd64" \
             --sha256 "$COSIGN_SHA" --dest "$TMP" --output cosign >/dev/null
fi
"$COSIGN" version >/dev/null 2>&1 && ok "cosign исполняется" || bad "cosign не запускается"

echo "3. положительный контроль: настоящая подпись syft принимается"
run_fv --url "${SYFT_BASE}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" \
       --sums-url "$SUMS_URL" --dest "$TMP/good" --member syft \
       --cosign-bin "$COSIGN" --cosign-sig "$SIG_URL" --cosign-cert "$CERT_URL" \
       --cosign-identity "$IDENTITY" --cosign-issuer "$ISSUER"
if [ "$RC" = "0" ] && [ -x "$TMP/good/syft" ]; then
  ok "подпись принята, бинарь установлен"
else
  # Вывод печатается ЦЕЛИКОМ, а не последней строкой. Ночь 14.09
  # (devsecops-pipeline#70) оставила в логе ровно «положительный контроль
  # упал (rc=22): » — последняя строка была пустой, потому что curl молчал,
  # и отличить недоступный апстрим от сломанной проверки подписи было не по
  # чему. Хвост в одну строку — это не диагностика, а её видимость.
  bad "положительный контроль упал (rc=$RC)"
  printf '%s\n' "$OUT" | sed 's/^/       /'
fi
# Контроль обязателен: проверка, отвергающая ВСЁ, прошла бы случаи 4 и 5 и
# выглядела бы работающей, заблокировав при этом любую загрузку.

echo "4. подделанный файл сумм отвергается"
# Материал для случаев 4 и 5 фикстура берёт сама, в обход fetch_verified.sh
# (её и проверяем). Загрузка идёт той же политикой, что и в проде: повтор
# транзиента, отказ говорящий. Прежняя форма `curl -sfL` под `set -e`
# уносила ВЕСЬ шаг с кодом 22 и без единой строки о том, что случилось, —
# именно так выглядела ночь 14.09 (devsecops-pipeline#70).
get() { # <url> <куда>
  curl -fL -sS --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 600 \
       -o "$2" "$1" \
    || { echo "::error::фикстура: не скачался $1 — апстрим недоступен, проверка подписи тут ни при чём" >&2; exit 1; }
}
get "$SUMS_URL" "$TMP/sums.txt"
get "$SIG_URL"  "$TMP/sums.sig"
get "$CERT_URL" "$TMP/sums.pem"
sed 's/^0/1/' "$TMP/sums.txt" > "$TMP/sums-bad.txt"
cmp -s "$TMP/sums.txt" "$TMP/sums-bad.txt" && bad "порча не изменила файл — замер недействителен"
printf 'не настоящий ассет\n' > "$TMP/fake.tgz"
run_fv --url "file://$TMP/fake.tgz" --sums-url "file://$TMP/sums-bad.txt" \
       --dest "$TMP/bad" --output fake \
       --cosign-bin "$COSIGN" --cosign-sig "file://$TMP/sums.sig" \
       --cosign-cert "file://$TMP/sums.pem" \
       --cosign-identity "$IDENTITY" --cosign-issuer "$ISSUER"
if [ "$RC" = "1" ] && printf '%s' "$OUT" | grep -q 'подпись файла сумм НЕ прошла'; then
  ok "подделанный файл сумм -> rc=1, отказ именно по подписи"
else
  bad "подделка: ожидал rc=1 и отказ по подписи, получил rc=$RC"
fi
[ -e "$TMP/bad/fake" ] && bad "файл установлен несмотря на отказ подписи" \
                       || ok "ничего не установлено"

echo "5. чужая личность отвергается (подпись валидна, подписант не тот)"
run_fv --url "file://$TMP/fake.tgz" --sums-url "file://$TMP/sums.txt" \
       --dest "$TMP/who" --output fake \
       --cosign-bin "$COSIGN" --cosign-sig "file://$TMP/sums.sig" \
       --cosign-cert "file://$TMP/sums.pem" \
       --cosign-identity '^https://github\.com/зло/зло@refs/heads/main$' \
       --cosign-issuer "$ISSUER"
if [ "$RC" = "1" ] && printf '%s' "$OUT" | grep -q 'подпись файла сумм НЕ прошла'; then
  ok "неожиданная личность -> отказ"
else
  bad "чужая личность: ожидал rc=1, получил rc=$RC"
fi
# Это и есть причина, по которой личность и издатель обязательны: без них
# verify-blob принимает подпись любого, кто получил сертификат Fulcio.

echo "6. bundle: настоящая подпись syft ${BUNDLE_VERSION} принимается"
run_fv --url "${BUNDLE_BASE}/syft_${BUNDLE_VERSION}_linux_amd64.tar.gz" \
       --sums-url "$BUNDLE_SUMS_URL" --dest "$TMP/good-bundle" --member syft \
       --cosign-bin "$COSIGN" --cosign-bundle "$BUNDLE_URL" \
       --cosign-identity "$IDENTITY" --cosign-issuer "$ISSUER"
if [ "$RC" = "0" ] && [ -x "$TMP/good-bundle/syft" ]; then
  ok "bundle принят, бинарь установлен"
else
  bad "положительный контроль bundle упал (rc=$RC)"
  printf '%s\n' "$OUT" | sed 's/^/       /'
fi
# Контроль обязателен по той же причине, что случай 3: проверка, отвергающая
# всё, прошла бы случаи 7 и 8.

echo "7. bundle: подделанный файл сумм отвергается"
get "$BUNDLE_SUMS_URL" "$TMP/bsums.txt"
get "$BUNDLE_URL" "$TMP/bsums.sigstore.json"
# Первый знак первой строки меняется всегда (0 -> 1, остальное -> 0), а не
# «если строка начинается с 0»: у чужого файла сумм такой строки может не быть.
awk 'NR == 1 { $0 = (substr($0, 1, 1) == "0" ? "1" : "0") substr($0, 2) } { print }' \
  "$TMP/bsums.txt" > "$TMP/bsums-bad.txt"
cmp -s "$TMP/bsums.txt" "$TMP/bsums-bad.txt" && bad "порча не изменила файл — замер недействителен"
run_fv --url "file://$TMP/fake.tgz" --sums-url "file://$TMP/bsums-bad.txt" \
       --dest "$TMP/bad-bundle" --output fake \
       --cosign-bin "$COSIGN" --cosign-bundle "file://$TMP/bsums.sigstore.json" \
       --cosign-identity "$IDENTITY" --cosign-issuer "$ISSUER"
if [ "$RC" = "1" ] && printf '%s' "$OUT" | grep -q 'подпись файла сумм НЕ прошла'; then
  ok "подделанный файл сумм -> rc=1, отказ именно по подписи"
else
  bad "bundle, подделка: ожидал rc=1 и отказ по подписи, получил rc=$RC"
fi
[ -e "$TMP/bad-bundle/fake" ] && bad "файл установлен несмотря на отказ подписи" \
                              || ok "ничего не установлено"

echo "8. bundle: чужая личность отвергается (подпись валидна, подписант не тот)"
run_fv --url "file://$TMP/fake.tgz" --sums-url "file://$TMP/bsums.txt" \
       --dest "$TMP/who-bundle" --output fake \
       --cosign-bin "$COSIGN" --cosign-bundle "file://$TMP/bsums.sigstore.json" \
       --cosign-identity '^https://github\.com/зло/зло@refs/heads/main$' \
       --cosign-issuer "$ISSUER"
if [ "$RC" = "1" ] && printf '%s' "$OUT" | grep -q 'подпись файла сумм НЕ прошла'; then
  ok "неожиданная личность -> отказ"
else
  bad "bundle, чужая личность: ожидал rc=1, получил rc=$RC"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "assert-cosign-sums: зелёный"
else
  echo "assert-cosign-sums: провалов $fails"
  exit 1
fi
