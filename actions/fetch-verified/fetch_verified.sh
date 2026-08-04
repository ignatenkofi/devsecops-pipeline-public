#!/usr/bin/env bash
# Скачать релизный ассет и НЕ отдать его дальше, пока сумма не сошлась.
#
# Зачем отдельный скрипт, а не строка в каждой стадии: стадии качали
# инструменты формой `curl … | tar -xz`, где артефакт вообще не появляется
# на диске — сверять нечего даже при желании (devsecops-pipeline#24).
# Плюс три копии одной логики в этом портфеле уже стоили дефекта
# (devsecops-pipeline#32), поэтому копия ровно одна и живёт здесь.
#
# Скрипт лежит в ОБОИХ репо конвейера байт-в-байт. Это не копипаста, а
# инвариант: расхождение ловит tests/lint/assert-twins.sh, потому что
# ночь 03→04.08 показала цену расхождения — гард построили в публичном
# близнеце и не перенесли в приватный, и месяц там качалось без сверки.
#
# Границы того, что это даёт (повторено сознательно, чтобы не завести
# ложное чувство закрытого вопроса — та же формулировка в
# njuska-auto-bot/deploy/update.sh):
#
#   защищает  — от битой загрузки, кривого зеркала, рассинхрона «latest»
#               с ассетом, подмены на пути от апстрима до раннера;
#   НЕ защищает — от скомпрометированного релизного конвейера апстрима:
#               файл сумм едет оттуда же, и кто подменит бинарь, подменит
#               и сумму. Настоящая устойчивость к подмене — подпись,
#               проверяемая ключом не из того же места (cosign — шаг 4
#               в devsecops-pipeline#24).
#
# Использование (ровно один режим установки и ровно один источник суммы):
#   fetch_verified.sh --url URL (--sums-url URL | --sha256 HEX) --dest DIR --member NAME
#   fetch_verified.sh --url URL (--sums-url URL | --sha256 HEX) --dest DIR --output NAME
#   fetch_verified.sh --url URL (--sums-url URL | --sha256 HEX) --dest DIR --extract-all
#
#   --member      распаковать из tar.gz ровно этот файл и сделать исполняемым
#   --output      положить скачанное как есть под этим именем (голый бинарь)
#   --extract-all распаковать архив целиком; исполняемым не делает ничего —
#                 для архивов с вложенной раскладкой, где путь к бинарю
#                 ищет вызывающий (lychee кладёт его в подкаталог с
#                 target-triple в имени)
#   --sums-url  файл контрольных сумм апстрима (формат `sha256  имя`)
#   --sha256    ожидаемая сумма пином в НАШЕМ репозитории
#
# Два источника ожидаемой суммы — не удобство, а разные модели доверия.
#
#   --sums-url  дёшев в сопровождении: апстрим публикует файл сумм рядом с
#               каждым релизом, бамп версии не требует ничего больше.
#               Но файл едет с того же хоста, что и ассет.
#   --sha256    пин лежит в нашем репозитории и ревьюится как код, поэтому
#               ловит ЗАМЕНУ уже опубликованного ассета — то, чего файл
#               сумм апстрима не ловит принципиально. Ценой того, что бамп
#               версии обязан пересчитать пин.
#
# Пин нужен там, где апстрим файла сумм не публикует вовсе — osv-scanner,
# devsecops-pipeline-public#13. Это trust on first use, и подписью его
# называть нельзя.
#
# Имя, по которому сумма ищется в файле сумм, берётся из basename --url:
# апстримы перечисляют ассеты именно так.
set -euo pipefail

URL="" SUMS_URL="" SHA256="" MEMBER="" DEST="" OUTPUT="" EXTRACT_ALL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --url)         URL="$2"; shift 2 ;;
    --sums-url)    SUMS_URL="$2"; shift 2 ;;
    --sha256)      SHA256="$2"; shift 2 ;;
    --member)      MEMBER="$2"; shift 2 ;;
    --dest)        DEST="$2"; shift 2 ;;
    --output)      OUTPUT="$2"; shift 2 ;;
    --extract-all) EXTRACT_ALL=1; shift ;;
    *) echo "::error::fetch-verified: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done

[ -n "$URL" ]  || { echo "::error::fetch-verified: не задан --url" >&2; exit 2; }
[ -n "$DEST" ] || { echo "::error::fetch-verified: не задан --dest" >&2; exit 2; }
if [ -n "$SUMS_URL" ] && [ -n "$SHA256" ]; then
  echo "::error::fetch-verified: --sums-url и --sha256 взаимоисключающие" >&2; exit 2
fi
if [ -z "$SUMS_URL" ] && [ -z "$SHA256" ]; then
  echo "::error::fetch-verified: нужен либо --sums-url, либо --sha256" >&2; exit 2
fi
# Режим установки ровно один. Считаем заданные, а не перечисляем пары:
# пар при трёх режимах уже три, и добавление четвёртого молча оставило бы
# дыру.
modes=0
[ -n "$MEMBER" ]      && modes=$((modes + 1))
[ -n "$OUTPUT" ]      && modes=$((modes + 1))
[ -n "$EXTRACT_ALL" ] && modes=$((modes + 1))
if [ "$modes" -ne 1 ]; then
  echo "::error::fetch-verified: нужен ровно один режим установки (--member | --output | --extract-all), задано $modes" >&2
  exit 2
fi

mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ASSET="$(basename "$URL")"

# Загрузка В ФАЙЛ, а не в пайп: пайп — и есть причина, по которой сверять
# было нечего.
curl -sfL -o "$WORK/$ASSET" "$URL"

if [ -n "$SHA256" ]; then
  # Кривой пин обязан быть отдельной ошибкой, а не «сумма не сошлась»:
  # иначе опечатка в пине выглядит как срабатывание защиты, и чинить
  # пойдут не то.
  expected="$(printf '%s' "$SHA256" | tr 'A-F' 'a-f')"
  case "$expected" in
    *[!0-9a-f]* | "") echo "::error::fetch-verified: --sha256 не шестнадцатеричный: $SHA256" >&2; exit 2 ;;
  esac
  [ "${#expected}" -eq 64 ] || {
    echo "::error::fetch-verified: --sha256 длиной ${#expected}, ожидалось 64" >&2; exit 2; }
  source_desc="пин в репозитории"
else
  curl -sfL -o "$WORK/SUMS" "$SUMS_URL"
  # Регистр нормализуется и здесь, а не только в ветке пина: sha256sum
  # печатает строчными, а файл сумм, перегенерированный через PowerShell
  # Get-FileHash или certutil, приходит ЗАГЛАВНЫМИ. Без этого нетронутый
  # ассет отвергался бы текстом «сумма не сошлась» — то есть штатное
  # расхождение регистра было бы неотличимо от подмены бинаря, и разбирать
  # пошли бы не туда.
  expected="$(awk -v a="$ASSET" '$2 == a || $2 == "*" a {print tolower($1); exit}' "$WORK/SUMS")"
  if [ -z "$expected" ]; then
    # Отсутствие записи — отказ, а не «проверять нечего». Иначе апстрим,
    # переименовавший ассет, молча отключил бы проверку.
    echo "::error::fetch-verified: в $SUMS_URL нет записи для $ASSET — отказываюсь ставить" >&2
    echo "имена в файле сумм:" >&2
    awk '{print "  " $2}' "$WORK/SUMS" >&2
    exit 1
  fi
  source_desc="$SUMS_URL"
fi

actual="$(sha256sum "$WORK/$ASSET" | awk '{print $1}')"
if [ "$expected" != "$actual" ]; then
  echo "::error::fetch-verified: сумма $ASSET не сошлась — отказываюсь ставить" >&2
  echo "  ожидалось $expected (источник: $source_desc)" >&2
  echo "  получено  $actual" >&2
  exit 1
fi

# Распаковка/установка — только после сверки. Порядок принципиален: файл,
# который не проверен, не должен становиться исполняемым.
if [ -n "$MEMBER" ]; then
  tar -xzf "$WORK/$ASSET" -C "$DEST" "$MEMBER"
  chmod +x "$DEST/$MEMBER"
  echo "fetch-verified: $ASSET сверен (sha256 $actual), распакован $MEMBER"
elif [ -n "$EXTRACT_ALL" ]; then
  tar -xzf "$WORK/$ASSET" -C "$DEST"
  echo "fetch-verified: $ASSET сверен (sha256 $actual), распакован целиком в $DEST"
else
  mv "$WORK/$ASSET" "$DEST/$OUTPUT"
  chmod +x "$DEST/$OUTPUT"
  echo "fetch-verified: $ASSET сверен (sha256 $actual), установлен как $OUTPUT"
fi
