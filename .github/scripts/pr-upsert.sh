#!/usr/bin/env bash
# pr-upsert.sh — завести PR из ветки или обновить уже открытый, различая
# ПОЧЕМУ не получилось (#44).
#
# ДВЕ БОЛЕЗНИ В ОДНОМ КРАСНОМ. Шаг «Ветка bump/tools + PR» в nightly-bump
# падал на `gh pr create` восемь ночей, и сторож живости (#43) заводил на
# каждое падение одинаковый комментарий. При этом 11, 12, 15, 16, 18.08 —
# «GitHub Actions is not permitted to create or approve pull requests»
# (настройка репозитория, код бессилен, ретрай бессмыслен), а 17.08 —
# «HTTP 503: No server is currently available» (GitHub прилёг, повтор через
# минуту прошёл бы). Снаружи обе выглядели одинаково: красная джоба.
# Сигнал, который не отличает «нужен человек» от «нужна минута», приучает
# себя игнорировать.
#
# ЧТО ЗДЕСЬ. Три класса отказа, по нарастанию цены:
#   * политика   — падаем сразу, `::error::` называет чекбокс и его путь;
#     повторять нечего, менять код нечего;
#   * транзиент  — HTTP 5xx / таймаут / обрыв: повтор с растущей паузой,
#     каждая попытка видна в логе (`::warning::`), исчерпание — отказ;
#   * прочее     — падаем сразу, текст ошибки в `::error::` дословно.
#
# ПОЧЕМУ ЗДЕСЬ РЕТРАЙ ЗАПИСИ ДОПУСТИМ, а в health-issue.sh — нет. Там повтор
# `issue create` после «ответ потерялся, запрос прошёл» заводит вторую issue
# по той же теме. Здесь GitHub сам отказывает во втором PR на ту же пару
# head/base («A pull request already exists»), и этот отказ ниже
# переводится в `pr edit`. Дубль невозможен by construction, поэтому повтор
# безопасен.
#
# usage:
#   pr-upsert.sh --head <ветка> --base <ветка> --title <строка> --body-file <файл>
#
# Требует GH_TOKEN (или уже залогиненный gh). Число попыток и стартовая пауза
# (секунды, дальше удвоение) — в env ради тестов: гарнитура гоняет тот же
# код с нулевой паузой.
set -euo pipefail

PR_UPSERT_ATTEMPTS="${PR_UPSERT_ATTEMPTS:-3}"
PR_UPSERT_DELAY="${PR_UPSERT_DELAY:-30}"

HEAD="" BASE="" TITLE="" BODY_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --head)      HEAD="$2"; shift 2 ;;
        --base)      BASE="$2"; shift 2 ;;
        --title)     TITLE="$2"; shift 2 ;;
        --body-file) BODY_FILE="$2"; shift 2 ;;
        *) echo "::error::pr-upsert: неизвестный аргумент $1" >&2; exit 2 ;;
    esac
done
for v in HEAD BASE TITLE BODY_FILE; do
    if [ -z "${!v}" ]; then
        echo "::error::pr-upsert: не задан --$(echo "$v" | tr '[:upper:]_' '[:lower:]-')" >&2
        exit 2
    fi
done
[ -r "$BODY_FILE" ] || { echo "::error::pr-upsert: тело PR не прочитано: $BODY_FILE" >&2; exit 2; }

# classify <stderr-текст> → policy | transient | exists | other
#
# Паттерны — из настоящих падений (run 32097099959, run 31992816671), а не
# из документации. Регекс политики привязан к формулировке GitHub; сменится
# формулировка — отказ станет «прочим», то есть по-прежнему красным, только
# без подсказки. Молча пройти он не может.
classify() {
    local text="$1"
    if grep -qiE "not permitted to create or approve pull requests" <<<"$text"; then
        echo policy
    elif grep -qiE "a pull request (for branch .* )?already exists" <<<"$text"; then
        echo exists
    elif grep -qiE "HTTP 5[0-9]{2}|No server is currently available|Gateway Timeout|timed? ?out|connection reset|temporarily unavailable|EOF" <<<"$text"; then
        echo transient
    else
        echo other
    fi
}

# run_gh <что> <argv...> — вызов gh с классификацией отказа.
# stdout gh уходит наружу (URL созданного PR, число из pr list), stderr — в
# файл, чтобы разобрать текст ошибки и не подмешать его в stdout.
# Возврат: 0 — успех; 3 — «PR уже есть» (только для create); 1 — отказ,
# сообщение уже напечатано.
run_gh() {
    local what="$1"; shift
    local attempt=1 delay="$PR_UPSERT_DELAY" err kind
    err="$(mktemp)"
    while :; do
        if gh "$@" 2>"$err"; then
            rm -f "$err"; return 0
        fi
        kind="$(classify "$(cat "$err")")"
        case "$kind" in
            policy)
                echo "::error::pr-upsert: ${what} — GITHUB_TOKEN запрещено создавать pull request'ы." \
                     "Это настройка репозитория, не код воркфлоу: Settings → Actions → General →" \
                     "Workflow permissions → «Allow GitHub Actions to create and approve pull requests»." \
                     "Проверка: gh api repos/\$GITHUB_REPOSITORY/actions/permissions/workflow →" \
                     "can_approve_pull_request_reviews обязано быть true. Повтор бессмыслен, не повторяю." \
                     "Ветка ${HEAD} при этом запушена — диф не потерян, а невидим. Подробности: #44." >&2
                rm -f "$err"; return 1 ;;
            exists)
                echo "::notice::pr-upsert: ${what} — PR из ${HEAD} уже существует, перехожу к обновлению" >&2
                rm -f "$err"; return 3 ;;
            transient)
                if [ "$attempt" -ge "$PR_UPSERT_ATTEMPTS" ]; then
                    echo "::error::pr-upsert: ${what} — транзиентный отказ GitHub не прошёл за ${attempt} попыт(ок);" \
                         "последняя ошибка: $(tr '\n' ' ' < "$err")." \
                         "Это не политика и не код — следующая ночь, скорее всего, пройдёт." >&2
                    rm -f "$err"; return 1
                fi
                echo "::warning::pr-upsert: ${what} — попытка ${attempt} не прошла ($(tr '\n' ' ' < "$err")); повтор через ${delay}s" >&2
                sleep "$delay"
                delay=$(( delay * 2 )); attempt=$(( attempt + 1 )) ;;
            *)
                echo "::error::pr-upsert: ${what} — отказ: $(tr '\n' ' ' < "$err")" >&2
                rm -f "$err"; return 1 ;;
        esac
    done
}

existing="$(run_gh "gh pr list" pr list --head "$HEAD" --base "$BASE" --state open --json number --jq 'length')" \
    || exit 1

if [ "$existing" = "0" ]; then
    rc=0
    run_gh "gh pr create" pr create --title "$TITLE" --body-file "$BODY_FILE" \
        --base "$BASE" --head "$HEAD" || rc=$?
    case "$rc" in
        0) echo "pr-upsert: PR из ${HEAD} в ${BASE} создан"; exit 0 ;;
        3) ;;  # гонка: PR появился между list и create — обновляем
        *) exit 1 ;;
    esac
fi

run_gh "gh pr edit" pr edit "$HEAD" --title "$TITLE" --body-file "$BODY_FILE" || exit 1
echo "::notice::pr-upsert: PR из ${HEAD} уже открыт — обновлён"
