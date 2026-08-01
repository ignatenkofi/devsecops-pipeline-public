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
printf '%s\n' "$*" >> "$GH_STUB_LOG"
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

run_case() {  # run_case <issues-json> <argv...>
    : > "$GH_STUB_LOG"
    GH_STUB_ISSUES="$1" bash "$SCRIPT" "${@:2}" >"$TMP/out" 2>&1
    echo "$?" > "$TMP/rc"
}
calls() { cat "$GH_STUB_LOG"; }

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

exit "$rc"
