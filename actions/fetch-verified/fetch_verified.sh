#!/usr/bin/env bash
# Скачать релизный ассет и НЕ отдать его дальше, пока сумма не сошлась.
#
# Зачем отдельный скрипт, а не строка в каждой стадии: стадии качали
# инструменты формой `curl … | tar -xz`, где артефакт вообще не появляется
# на диске — сверять нечего даже при желании (#24). Плюс три копии одной
# логики в этом портфеле уже стоили дефекта (devsecops-pipeline#32),
# поэтому копия ровно одна и живёт здесь.
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
#               проверяемая ключом не из того же места (cosign, #24 шаг 4).
#
# Использование:
#   fetch_verified.sh --url URL --sums-url URL --member NAME --dest DIR
#   fetch_verified.sh --url URL --sums-url URL --dest DIR --output NAME
#
#   --member  распаковать из tar.gz ровно этот файл
#   --output  положить скачанное как есть под этим именем (голый бинарь)
#   --sums-url  файл контрольных сумм апстрима (формат `sha256  имя`)
#
# Имя, по которому сумма ищется в файле сумм, берётся из basename --url:
# апстримы перечисляют ассеты именно так.
set -euo pipefail

URL="" SUMS_URL="" MEMBER="" DEST="" OUTPUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --url)      URL="$2"; shift 2 ;;
    --sums-url) SUMS_URL="$2"; shift 2 ;;
    --member)   MEMBER="$2"; shift 2 ;;
    --dest)     DEST="$2"; shift 2 ;;
    --output)   OUTPUT="$2"; shift 2 ;;
    *) echo "::error::fetch-verified: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done

[ -n "$URL" ]      || { echo "::error::fetch-verified: не задан --url" >&2; exit 2; }
[ -n "$SUMS_URL" ] || { echo "::error::fetch-verified: не задан --sums-url" >&2; exit 2; }
[ -n "$DEST" ]     || { echo "::error::fetch-verified: не задан --dest" >&2; exit 2; }
if [ -n "$MEMBER" ] && [ -n "$OUTPUT" ]; then
  echo "::error::fetch-verified: --member и --output взаимоисключающие" >&2; exit 2
fi
if [ -z "$MEMBER" ] && [ -z "$OUTPUT" ]; then
  echo "::error::fetch-verified: нужен либо --member (архив), либо --output (голый бинарь)" >&2; exit 2
fi

mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ASSET="$(basename "$URL")"

# Загрузка В ФАЙЛ, а не в пайп: пайп — и есть причина, по которой сверять
# было нечего.
curl -sfL -o "$WORK/$ASSET" "$URL"
curl -sfL -o "$WORK/SUMS"   "$SUMS_URL"

expected="$(awk -v a="$ASSET" '$2 == a || $2 == "*" a {print $1; exit}' "$WORK/SUMS")"
if [ -z "$expected" ]; then
  # Отсутствие записи — отказ, а не «проверять нечего». Иначе апстрим,
  # переименовавший ассет, молча отключил бы проверку.
  echo "::error::fetch-verified: в $SUMS_URL нет записи для $ASSET — отказываюсь ставить" >&2
  echo "имена в файле сумм:" >&2
  awk '{print "  " $2}' "$WORK/SUMS" >&2
  exit 1
fi

actual="$(sha256sum "$WORK/$ASSET" | awk '{print $1}')"
if [ "$expected" != "$actual" ]; then
  echo "::error::fetch-verified: сумма $ASSET не сошлась — отказываюсь ставить" >&2
  echo "  ожидалось $expected" >&2
  echo "  получено  $actual" >&2
  exit 1
fi

# Распаковка/установка — только после сверки. Порядок принципиален: файл,
# который не проверен, не должен становиться исполняемым.
if [ -n "$MEMBER" ]; then
  tar -xzf "$WORK/$ASSET" -C "$DEST" "$MEMBER"
  chmod +x "$DEST/$MEMBER"
  echo "fetch-verified: $ASSET сверен (sha256 $actual), распакован $MEMBER"
else
  mv "$WORK/$ASSET" "$DEST/$OUTPUT"
  chmod +x "$DEST/$OUTPUT"
  echo "fetch-verified: $ASSET сверен (sha256 $actual), установлен как $OUTPUT"
fi
