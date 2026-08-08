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
# `tuf: failed to download 10.root.json … Forbidden`), поэтому случаи 3 и 4
# зелёные только в CI. Скипа у них НЕТ намеренно: молча пропущенная
# проверка неотличима от пройденной, а это и есть болезнь, которую чинит
# #33. Локально фикстура честно краснеет.
#
# Вход: assert-cosign-sums.sh <путь к fetch_verified.sh> [<путь к cosign>]
set -euo pipefail

FV="${1:?нужен путь к fetch_verified.sh}"
COSIGN="${2:-${COSIGN_BIN:-}}"

# Пин самого верификатора. Скрипт, который сам себе качает cosign, проверять
# его нечем; поэтому cosign ставится тем же fetch_verified.sh с --sha256, а
# сюда приходит готовым.
COSIGN_VERSION="2.4.1"
COSIGN_SHA="8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b"

# Единственный инструмент портфеля, чей апстрим публикует подписи (замер
# 2026-08-04: у gitleaks их нет — 13 проб при живом контроле).
SYFT_VERSION="1.50.0"
SYFT_BASE="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}"
SUMS_URL="${SYFT_BASE}/syft_${SYFT_VERSION}_checksums.txt"
SIG_URL="${SUMS_URL}.sig"
CERT_URL="${SUMS_URL}.pem"
IDENTITY='^https://github\.com/anchore/syft/\.github/workflows/release\.yaml@refs/heads/main$'
ISSUER="https://token.actions.githubusercontent.com"

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

echo "1. конфигурационные отказы (сети не требуют)"

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
  bad "положительный контроль упал (rc=$RC): ${OUT##*$'\n'}"
fi
# Контроль обязателен: проверка, отвергающая ВСЁ, прошла бы случаи 4 и 5 и
# выглядела бы работающей, заблокировав при этом любую загрузку.

echo "4. подделанный файл сумм отвергается"
curl -sfL -o "$TMP/sums.txt" "$SUMS_URL"
curl -sfL -o "$TMP/sums.sig" "$SIG_URL"
curl -sfL -o "$TMP/sums.pem" "$CERT_URL"
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

echo
if [ "$fails" -eq 0 ]; then
  echo "assert-cosign-sums: зелёный"
else
  echo "assert-cosign-sums: провалов $fails"
  exit 1
fi
