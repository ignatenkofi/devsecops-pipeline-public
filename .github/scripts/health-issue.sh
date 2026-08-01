#!/usr/bin/env bash
# Внешне проверяемый признак живости для периодических джоб.
#
# Провалившийся scheduled-workflow не сообщает о себе никому: он краснеет во
# вкладке Actions, куда никто не заходит, пока чего-нибудь не хватится. Ровно
# так nightly-bump падал три ночи подряд, а обнаружился только сплошной
# сверкой. Отказ обязан оставлять артефакт, который видно снаружи, — issue.
#
# usage:
#   health-issue.sh open  "<заголовок>" "<тело>"   # завести или обновить
#   health-issue.sh close "<заголовок>" "<текст>"  # закрыть, если открыт
#
# Идентификация — по точному заголовку: одна тема отказа = одна issue, ночь за
# ночью обновляется комментарием, а не плодится. Требует GH_TOKEN и
# permissions: issues: write.
set -euo pipefail

verb="${1:?usage: health-issue.sh open|close <title> <body>}"
title="${2:?нужен заголовок}"
body="${3-}"

# Фильтруем на своей стороне: точное совпадение заголовка, без подстрок —
# «похожую» issue закрывать нельзя.
number="$(
  gh issue list --state open --limit 100 --json number,title \
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
    echo "::error::неизвестная команда ${verb} (ожидалось open|close)"
    exit 2
    ;;
esac
