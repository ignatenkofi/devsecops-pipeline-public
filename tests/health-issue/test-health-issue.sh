#!/usr/bin/env bash
# test-health-issue.sh — офлайн-проверка признака живости (devsecops-pipeline#19).
#
# `gh` подменяется заглушкой на PATH: она пишет вызовы в журнал и отдаёт
# заранее заданный список открытых issue. Проверяется ровно то поведение, на
# которое опираются потребители: одна тема отказа = одна issue, точное
# совпадение заголовка, отмена — не отказ.
#
# Форма 3 из #19 («положительный контроль») применена к самой проверке: тесты
# ниже обязаны падать на заведомо неверном поведении, поэтому среди них есть
# случай, где «похожий» заголовок НЕ должен считаться совпадением.

set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
SCRIPT="$ROOT/actions/health-issue/health-issue.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rc=0
ok()   { echo "  ok: $*"; }
fail() { echo "  FAIL: $*" >&2; rc=1; }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'STUB'
#!/usr/bin/env bash
# Заглушка gh: список открытых issue берётся из $GH_STUB_ISSUES (JSON),
# все вызовы дописываются в $GH_STUB_LOG.
#
# Отказы: подкоманда из $GH_STUB_FAIL_CMD падает первые N раз, где N лежит в
# файле $GH_STUB_FAILS_LEFT (счётчик в файле, а не в переменной, потому что
# каждый вызов заглушки — отдельный процесс). Текст ошибки — настоящий, из
# упавшего прогона homenet-iac (run 31014586787).
printf '%s\n' "$*" >> "$GH_STUB_LOG"
if [ "${1:-}" = "issue" ] && [ "${2:-}" = "${GH_STUB_FAIL_CMD:-}" ]; then
    left=0
    [ -f "$GH_STUB_FAILS_LEFT" ] && left="$(cat "$GH_STUB_FAILS_LEFT")"
    if [ "$left" -gt 0 ]; then
        echo "$(( left - 1 ))" > "$GH_STUB_FAILS_LEFT"
        echo "HTTP 504: 504 Gateway Timeout (https://api.github.com/graphql)" >&2
        exit 1
    fi
fi
if [ "${1:-}" = "issue" ] && [ "${2:-}" = "list" ]; then
    printf '%s' "${GH_STUB_ISSUES:-[]}"
elif [ "${1:-}" = "issue" ] && [ "${2:-}" = "create" ]; then
    printf 'https://github.com/o/r/issues/999\n'
fi
exit 0
STUB
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"
export GH_STUB_LOG="$TMP/calls.log"
export GH_STUB_FAILS_LEFT="$TMP/fails-left"

# Пауза между попытками чтения обнуляется: проверяется механика повтора, а не
# умение спать. Число попыток оставлено дефолтным — тесты ниже опираются
# именно на него.
export HEALTH_ISSUE_READ_DELAY=0

run_case() {  # run_case <issues-json> <argv...>
    : > "$GH_STUB_LOG"
    echo "${STUB_FAILS:-0}" > "$GH_STUB_FAILS_LEFT"
    GH_STUB_ISSUES="$1" GH_STUB_FAIL_CMD="${STUB_FAIL_CMD:-}" \
        bash "$SCRIPT" "${@:2}" >"$TMP/out" 2>&1
    echo "$?" > "$TMP/rc"
}
calls() { cat "$GH_STUB_LOG"; }
out()   { cat "$TMP/out"; }

TITLE="nightly-bump: ночной прогон падает"

# --- 1. отказ без открытой issue — заводится новая ---------------------------
run_case '[]' failure "$TITLE" "тело"
if calls | grep -q "^issue create"; then ok "отказ без issue — заводится новая"
else fail "ожидался issue create, вызовы: $(calls | tr '\n' '|')"; fi

# --- 2. отказ при уже открытой — комментарий, а не вторая issue --------------
run_case "[{\"number\":7,\"title\":\"$TITLE\"}]" failure "$TITLE" "тело"
if calls | grep -q "^issue comment 7" && ! calls | grep -q "^issue create"; then
    ok "повторный отказ — комментарий в ту же issue"
else fail "ожидался issue comment 7 без create, вызовы: $(calls | tr '\n' '|')"; fi

# --- 3. успех при открытой — закрывается -------------------------------------
run_case "[{\"number\":7,\"title\":\"$TITLE\"}]" success "$TITLE" "ушло"
if calls | grep -q "^issue close 7"; then ok "успех закрывает открытую issue"
else fail "ожидался issue close 7, вызовы: $(calls | tr '\n' '|')"; fi

# --- 4. успех без открытой — ничего не трогаем -------------------------------
run_case '[]' success "$TITLE" "ушло"
if ! calls | grep -qE "^issue (close|create|comment)"; then
    ok "успех без открытой issue ничего не делает"
else fail "лишние вызовы: $(calls | tr '\n' '|')"; fi

# --- 5. похожий заголовок — НЕ совпадение (положительный контроль) -----------
# Если бы фильтр брал подстроку, чужая issue была бы закрыта этим прогоном.
run_case "[{\"number\":7,\"title\":\"$TITLE (архив)\"}]" success "$TITLE" "ушло"
if ! calls | grep -q "^issue close"; then
    ok "похожий заголовок не считается совпадением — чужая issue цела"
else fail "закрыта issue с чужим заголовком: $(calls | tr '\n' '|')"; fi

# --- 6. отмена — не отказ ----------------------------------------------------
run_case "[]" cancelled "$TITLE" "тело"
if [ "$(cat "$TMP/rc")" = "0" ] && ! calls | grep -qE "^issue (create|close|comment)"; then
    ok "cancelled ничего не меняет и не падает"
else fail "cancelled должен быть no-op, rc=$(cat "$TMP/rc"), вызовы: $(calls | tr '\n' '|')"; fi

# --- 7. неизвестный глагол — явная ошибка, а не тихий no-op ------------------
run_case '[]' сомнительно "$TITLE" "тело"
if [ "$(cat "$TMP/rc")" = "2" ]; then ok "неизвестный глагол — exit 2"
else fail "ожидался exit 2, получен $(cat "$TMP/rc")"; fi

# --- 8. синонимы open/close продолжают работать ------------------------------
run_case '[]' open "$TITLE" "тело"
if calls | grep -q "^issue create"; then ok "глагол open работает (обратная совместимость)"
else fail "open обязан заводить issue"; fi

# --- 9. транзиентный 504 на чтении переживается повтором ---------------------
# Регресс homenet-iac run 31014586787: GraphQL отдал 504 на дедуп-запросе,
# шаг упал, джоба покраснела и завела ложную issue «сторож пульса падает».
STUB_FAIL_CMD=list STUB_FAILS=2 run_case '[]' failure "$TITLE" "тело"
if [ "$(cat "$TMP/rc")" = "0" ] && calls | grep -q "^issue create"; then
    ok "два 504 подряд на чтении пережиты повтором, issue заведена"
else fail "транзиентный отказ чтения не пережит: rc=$(cat "$TMP/rc"), вызовы: $(calls | tr '\n' '|')"; fi

# --- 10. исчерпанные попытки — отказ, а не «issue не нашлось» ----------------
# Самый опасный из возможных исходов: посчитать недоступное API за пустой
# список и завести ВТОРУЮ issue по теме, у которой уже есть открытая.
STUB_FAIL_CMD=list STUB_FAILS=99 run_case "[{\"number\":7,\"title\":\"$TITLE\"}]" \
    failure "$TITLE" "тело"
if [ "$(cat "$TMP/rc")" != "0" ] && ! calls | grep -qE "^issue (create|comment|close)"; then
    ok "недоступное чтение — отказ наружу, ни одной записи вслепую"
else fail "ожидался отказ без записей: rc=$(cat "$TMP/rc"), вызовы: $(calls | tr '\n' '|')"; fi

# --- 11. число попыток чтения ограничено -------------------------------------
# Кап заявлен в шапке скрипта; заявленный и не проверенный кап — обещание,
# на которое читатель полагается зря.
STUB_FAIL_CMD=list STUB_FAILS=99 run_case '[]' failure "$TITLE" "тело"
attempts="$(calls | grep -c "^issue list")"
if [ "$attempts" = "3" ]; then ok "чтение повторяется ровно 3 раза и сдаётся"
else fail "ожидалось 3 попытки чтения, сделано $attempts"; fi

# --- 12. запись НЕ ретраится (осознанная граница) ----------------------------
# Повтор неидемпотентной записи после «ответ потерян, но запрос прошёл» даёт
# дубль issue — то есть чинит транзиентный сбой ценой порчи сигнала.
STUB_FAIL_CMD=create STUB_FAILS=1 run_case '[]' failure "$TITLE" "тело"
creates="$(calls | grep -c "^issue create")"
if [ "$(cat "$TMP/rc")" != "0" ] && [ "$creates" = "1" ]; then
    ok "отказ записи не повторяется и виден снаружи"
else fail "ожидался один create и ненулевой rc: rc=$(cat "$TMP/rc"), create×$creates"; fi

exit "$rc"
