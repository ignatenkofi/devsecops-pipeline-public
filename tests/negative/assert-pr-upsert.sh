#!/usr/bin/env bash
# assert-pr-upsert.sh — офлайн-гард на .github/scripts/pr-upsert.sh (#44).
#
# `gh` подменяется заглушкой на PATH: она пишет вызовы в журнал и отдаёт
# заранее заданный сценарий отказа. Проверяется ровно то различение, ради
# которого скрипт заведён: политика репозитория — отказ сразу с подсказкой,
# транзиент — повтор с ограниченным числом попыток, «PR уже есть» — переход
# к обновлению. Тексты ошибок — из настоящих падений nightly-bump
# (run 32097099959 — политика, run 31992816671 — HTTP 503), не из головы.
#
# Положительный контроль (форма 3 из #19) применён к самой фикстуре: среди
# случаев есть «успех с первого раза» и «прочий отказ», на которых ретрай
# ОБЯЗАН не сработать, — иначе гард не различает, а красит всё в один цвет.
#
# Вход: assert-pr-upsert.sh [<путь к pr-upsert.sh>]
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
SCRIPT="${1:-$ROOT/.github/scripts/pr-upsert.sh}"
[ -r "$SCRIPT" ] || { echo "FAIL: не найден $SCRIPT" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'STUB'
#!/usr/bin/env bash
# Заглушка gh. Все вызовы дописываются в $GH_STUB_LOG (одна строка на вызов).
#   GH_STUB_OPEN       — что отдаёт `pr list … --jq length` (число)
#   GH_STUB_CREATE_ERR — текст ошибки `pr create` (stderr, rc=1)
#   GH_STUB_CREATE_FAILS_LEFT — файл-счётчик: сколько первых `pr create`
#                        ещё падают с этим текстом; исчерпан — успех.
#                        Счётчик в файле, потому что каждый вызов заглушки —
#                        отдельный процесс.
printf '%s\n' "$*" >> "$GH_STUB_LOG"
case "${1:-} ${2:-}" in
  "pr list")
    printf '%s\n' "${GH_STUB_OPEN:-0}" ;;
  "pr create")
    left=0
    [ -f "$GH_STUB_CREATE_FAILS_LEFT" ] && left="$(cat "$GH_STUB_CREATE_FAILS_LEFT")"
    if [ "$left" -gt 0 ]; then
      echo "$(( left - 1 ))" > "$GH_STUB_CREATE_FAILS_LEFT"
      printf '%s\n' "${GH_STUB_CREATE_ERR:-}" >&2
      exit 1
    fi
    printf 'https://github.com/o/r/pull/999\n' ;;
  "pr edit")
    : ;;
esac
exit 0
STUB
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"
export GH_STUB_LOG="$TMP/calls.log"
export GH_STUB_CREATE_FAILS_LEFT="$TMP/fails-left"
# Пауза между попытками обнуляется: проверяется механика повтора, а не
# умение спать. Число попыток — явное, тесты ниже считают вызовы по нему.
export PR_UPSERT_DELAY=0
export PR_UPSERT_ATTEMPTS=3

BODY="$TMP/body.md"
printf 'тело PR\n' > "$BODY"

POLICY_ERR="pull request create failed: GraphQL: GitHub Actions is not permitted to create or approve pull requests (createPullRequest)"
TRANSIENT_ERR="pull request create failed: HTTP 503: No server is currently available (https://api.github.com/graphql)"
EXISTS_ERR="pull request create failed: GraphQL: A pull request already exists for ignatenkofi:bump/tools. (createPullRequest)"
OTHER_ERR="pull request create failed: GraphQL: Head sha can't be blank, Base sha can't be blank (createPullRequest)"

# run_case <open> <fails> <err> → rc в $RC, вывод в $OUT, вызовы в calls
# Код возврата снимается без пайпов и подстановок после измеряемой команды.
run_case() {
  : > "$GH_STUB_LOG"
  echo "$2" > "$GH_STUB_CREATE_FAILS_LEFT"
  set +e
  OUT="$(GH_STUB_OPEN="$1" GH_STUB_CREATE_ERR="$3" \
         bash "$SCRIPT" --head bump/tools --base main \
           --title "chore: bump" --body-file "$BODY" 2>&1)"
  RC=$?
  set -e
}
calls()   { cat "$GH_STUB_LOG"; }
creates() { grep -c '^pr create' "$GH_STUB_LOG" || true; }
edits()   { grep -c '^pr edit' "$GH_STUB_LOG" || true; }

echo "1. положительный контроль: PR создаётся с первой попытки"
run_case 0 0 ""
if [ "$RC" = 0 ] && [ "$(creates)" = 1 ] && [ "$(edits)" = 0 ]; then
  ok "rc=0, один pr create, без edit"
else bad "ожидался rc=0 и ровно один create: rc=$RC, вызовы: $(calls | tr '\n' '|')"; fi

echo "2. политика репозитория: отказ сразу, без повтора, с подсказкой"
run_case 0 99 "$POLICY_ERR"
if [ "$RC" != 0 ] && [ "$(creates)" = 1 ]; then
  ok "rc=$RC после ОДНОЙ попытки — ретрай на политику не тратится"
else bad "политика должна валить сразу: rc=$RC, create=$(creates)"; fi
if grep -q '::error::' <<<"$OUT" \
   && grep -q 'can_approve_pull_request_reviews' <<<"$OUT" \
   && grep -q 'Allow GitHub Actions to create and approve pull requests' <<<"$OUT"; then
  ok "::error:: называет чекбокс и поле API для проверки"
else bad "подсказка неполная: $OUT"; fi
if ! grep -q '::warning::' <<<"$OUT"; then
  ok "ни одного ::warning:: — политика не выглядит как икота сети"
else bad "политика помечена как транзиент: $OUT"; fi

echo "3. транзиент: два 503 подряд пережиты повтором"
run_case 0 2 "$TRANSIENT_ERR"
if [ "$RC" = 0 ] && [ "$(creates)" = 3 ]; then
  ok "rc=0 после трёх попыток create"
else bad "ожидались 3 create и rc=0: rc=$RC, create=$(creates)"; fi
if [ "$(grep -c '::warning::' <<<"$OUT")" = 2 ]; then
  ok "каждая неудачная попытка видна в логе (2 × ::warning::)"
else bad "попытки невидимы: $OUT"; fi

echo "4. транзиент, который не проходит: попытки ограничены"
run_case 0 99 "$TRANSIENT_ERR"
if [ "$RC" != 0 ] && [ "$(creates)" = "$PR_UPSERT_ATTEMPTS" ]; then
  ok "rc=$RC ровно после ${PR_UPSERT_ATTEMPTS} попыток"
else bad "кап попыток не соблюдён: rc=$RC, create=$(creates)"; fi
if grep -q 'транзиентный отказ' <<<"$OUT" && ! grep -q 'can_approve' <<<"$OUT"; then
  ok "::error:: называет отказ транзиентным, а не политикой"
else bad "исчерпание не отличимо от политики: $OUT"; fi

echo "5. PR уже открыт: обновление вместо второго PR"
run_case 1 0 ""
if [ "$RC" = 0 ] && [ "$(creates)" = 0 ] && [ "$(edits)" = 1 ]; then
  ok "pr edit без create"
else bad "ожидался только edit: rc=$RC, вызовы: $(calls | tr '\n' '|')"; fi

echo "6. гонка: list не видел PR, create говорит «уже есть» → edit"
run_case 0 99 "$EXISTS_ERR"
if [ "$RC" = 0 ] && [ "$(creates)" = 1 ] && [ "$(edits)" = 1 ]; then
  ok "один create, затем edit, rc=0"
else bad "гонка не переведена в edit: rc=$RC, вызовы: $(calls | tr '\n' '|')"; fi

echo "7. прочий отказ: сразу красный, текст дословно, без повтора"
run_case 0 99 "$OTHER_ERR"
if [ "$RC" != 0 ] && [ "$(creates)" = 1 ] && grep -q "Head sha can't be blank" <<<"$OUT"; then
  ok "rc=$RC после одной попытки, текст ошибки в ::error::"
else bad "прочий отказ обработан неверно: rc=$RC, create=$(creates), $OUT"; fi

echo "8. конфигурационные отказы (gh не зовётся)"
: > "$GH_STUB_LOG"
set +e
bash "$SCRIPT" --head bump/tools --base main --title t >/dev/null 2>&1; rc_args=$?
bash "$SCRIPT" --head bump/tools --base main --title t --body-file "$TMP/нет" >/dev/null 2>&1; rc_body=$?
set -e
if [ "$rc_args" = 2 ] && [ "$rc_body" = 2 ] && [ ! -s "$GH_STUB_LOG" ]; then
  ok "нет --body-file → rc=2; нечитаемое тело → rc=2; gh не вызывался"
else bad "конфигурационный отказ: rc=$rc_args/$rc_body, вызовы: $(calls | tr '\n' '|')"; fi

echo
if [ "$fails" -gt 0 ]; then
  echo "assert-pr-upsert: провалов $fails"
  exit 1
fi
echo "assert-pr-upsert: зелёный"
