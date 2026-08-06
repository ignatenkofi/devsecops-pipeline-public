#!/usr/bin/env bash
# Внешне проверяемый признак живости для периодических джоб.
#
# Провалившийся scheduled-workflow не сообщает о себе никому: он краснеет во
# вкладке Actions, куда никто не заходит, пока чего-нибудь не хватится. Ровно
# так nightly-bump падал три ночи подряд, а обнаружился только сплошной
# сверкой. Отказ обязан оставлять артефакт, который видно снаружи, — issue.
#
# usage:
#   health-issue.sh open    "<заголовок>" "<тело>"   # завести или обновить
#   health-issue.sh close   "<заголовок>" "<текст>"  # закрыть, если открыт
#   health-issue.sh failure "<заголовок>" "<тело>"   # синоним open
#   health-issue.sh success "<заголовок>" "<текст>"  # синоним close
#
# Синонимы `success`/`failure` существуют ради одного вызова у потребителя:
# `state: ${{ job.status }}` в шаге с `if: always()` закрывает обе ветки сразу
# вместо пары шагов `if: failure()` / `if: success()`. Меньше копипасты —
# меньше мест, где ветку «закрыть» забыли подключить, а незакрытая issue
# приучает игнорировать сигнал не хуже, чем его отсутствие.
# `cancelled` и `skipped` осознанно не делают ничего: отмена — не отказ.
#
# Идентификация — по точному заголовку: одна тема отказа = одна issue, ночь за
# ночью обновляется комментарием, а не плодится. Требует GH_TOKEN и
# permissions: issues: write. Репозиторий берётся из GH_REPO, если задан:
# composite action зовут из чужого workspace, и полагаться на git remote
# рабочего каталога там нельзя.
#
# ПОЧЕМУ ЗДЕСЬ РЕТРАЙ, И ТОЛЬКО НА ЧТЕНИИ. 2026-08-05 сторож пульса
# homenet-iac упал на `HTTP 504: 504 Gateway Timeout (…/graphql)` — GraphQL
# отдал ошибку на дедуп-запросе. Под `set -e` это уронило шаг, шаг уронил
# джобу, а джоба через свой же `if: always()` завела issue «сторож пульса
# падает». То есть транзиентный сбой чужого API стал ложной тревогой в том
# самом канале, который заведён, чтобы тревогам верили. Сигнал, кричащий на
# сетевую икоту, приучает себя игнорировать ровно так же, как молчащий.
#
# Ретраится ИСКЛЮЧИТЕЛЬНО чтение списка issue: запрос идемпотентный, повтор
# ничего не меняет. Записи (`create`/`comment`/`close`) повторять нельзя —
# «ответ потерялся, но запрос прошёл» неотличимо от «запрос не прошёл», и
# повтор удвоит комментарий или заведёт вторую issue по той же теме. Порча
# сигнала дублями — это то, против чего написана дедупликация по заголовку;
# чинить её ретраем было бы обменом одной болезни на другую. Отказ записи
# остаётся отказом и виден снаружи как красная джоба.
#
# Исчерпанные попытки — тоже отказ: канал сигнализации недоступен, и молчать
# об этом нельзя.
set -euo pipefail

# Число попыток и стартовая пауза (секунды, дальше удвоение). Вынесены в env
# ради тестов: гарнитура гоняет тот же код с нулевой паузой.
HEALTH_ISSUE_READ_ATTEMPTS="${HEALTH_ISSUE_READ_ATTEMPTS:-3}"
HEALTH_ISSUE_READ_DELAY="${HEALTH_ISSUE_READ_DELAY:-2}"

# list_open_issues — JSON открытых issue на stdout; при отказе rc=1.
#
# stderr `gh` собирается в отдельный файл, а не сливается в stdout: слияние
# подмешало бы предупреждение gh в JSON, который дальше разбирает python, и
# сломало бы разбор ровно в тот момент, когда всё остальное работает.
list_open_issues() {
    _hi_attempt=1
    _hi_delay="$HEALTH_ISSUE_READ_DELAY"
    _hi_err="$(mktemp)"
    while :; do
        if _hi_out="$(gh issue list --state open --limit 100 \
                          --json number,title 2>"$_hi_err")"; then
            printf '%s' "$_hi_out"
            rm -f "$_hi_err"
            return 0
        fi
        _hi_why="$(tr '\n' ' ' < "$_hi_err")"
        if [ "$_hi_attempt" -ge "$HEALTH_ISSUE_READ_ATTEMPTS" ]; then
            echo "::error::здоровье джобы: список issue не прочитан за" \
                 "${_hi_attempt} попыт(ок), последняя ошибка: ${_hi_why}" >&2
            rm -f "$_hi_err"
            return 1
        fi
        echo "::warning::здоровье джобы: попытка ${_hi_attempt} прочитать" \
             "список issue не прошла (${_hi_why}); повтор через ${_hi_delay}s" >&2
        sleep "$_hi_delay"
        _hi_delay=$(( _hi_delay * 2 ))
        _hi_attempt=$(( _hi_attempt + 1 ))
    done
}

verb="${1:?usage: health-issue.sh open|close|success|failure <title> <body>}"
title="${2:?нужен заголовок}"
body="${3-}"

case "$verb" in
  failure) verb=open ;;
  success) verb=close ;;
  cancelled|skipped)
    echo "::notice::здоровье джобы: статус ${verb} — не отказ и не успех, ничего не меняем"
    exit 0 ;;
esac

# Фильтруем на своей стороне: точное совпадение заголовка, без подстрок —
# «похожую» issue закрывать нельзя.
number="$(
  list_open_issues \
    | python3 -c '
import json, sys
want = sys.argv[1]
for item in json.load(sys.stdin):
    if item["title"] == want:
        print(item["number"])
        break
' "$title"
)"

case "$verb" in
  open)
    if [ -n "$number" ]; then
      gh issue comment "$number" --body "$body"
      echo "::warning::здоровье джобы: обновлена issue #${number} — ${title}"
    else
      url="$(gh issue create --title "$title" --body "$body")"
      echo "::warning::здоровье джобы: заведена issue ${url} — ${title}"
    fi
    ;;
  close)
    if [ -n "$number" ]; then
      gh issue close "$number" --comment "$body"
      echo "::notice::здоровье джобы: issue #${number} закрыта — причина ушла"
    else
      echo "::notice::здоровье джобы: открытых issue по теме нет"
    fi
    ;;
  *)
    echo "::error::неизвестная команда ${verb} (ожидалось open|close|success|failure)"
    exit 2
    ;;
esac
