#!/usr/bin/env bash
# Гард на profile-resolve: опечатка в skip-stages ОБЯЗАНА падать.
#
# Дефект, ради которого этот файл существует. `skip-stages` не проверял, что
# имя стадии вообще существует: неизвестное просто не совпадало ни с чем и
# молча игнорировалось. Потребитель, написавший `skip-stages: course-lnt`,
# получал прогон, где стадия ВСЁ ЕЩЁ работает, — будучи уверен, что выключил
# её. Отказ выглядел бы как отказ самой стадии, и причину искали бы не там.
#
# Найдено 2026-08-05 при заведении security.yml в sqst-core: там
# `skip-stages: course-lint` — единственное, что удерживает прогон от падения
# на отсутствующем RO-PAT, и ничто не сказало бы, что имя написано неверно.
#
# ОБЩИЙ ФАЙЛ ДВУХ РЕПО (tests/lint/assert-twins.py): проверяется разбор входов,
# а не набор стадий конкретного репозитория, поэтому --implemented здесь
# фиксированный — три стадии, которые есть в обоих.
#
# usage: tests/negative/assert-profile-resolve.sh
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
resolve="$root/actions/profile-resolve/resolve.py"
profiles="$root/profiles"
IMPL="secrets,sast-semgrep,sca"

[ -f "$resolve" ] || { echo "::error::нет $resolve"; exit 1; }

fail=0
n_ok=0
TMP_ERR="$(mktemp)"
trap 'rm -f "$TMP_ERR"' EXIT

check() { # check <ожидаемый-код> <описание> <SKIP> <EXTRA> <класс>
  local want="$1" desc="$2" skip="$3" extra="$4" cls="$5"
  local out rc
  out="$(SKIP="$skip" EXTRA="$extra" python3 "$resolve" "$profiles/$cls.yml" "$profiles" \
         --implemented "$IMPL" 2>"$TMP_ERR")"
  rc=$?
  if [ "$rc" = "$want" ]; then
    echo "  ok   $desc (rc=$rc)"; n_ok=$((n_ok + 1))
  else
    echo "  FAIL $desc — ожидал rc=$want, получил rc=$rc"
    sed 's/^/       /' "$TMP_ERR"
    fail=1
  fi
}

echo "profile-resolve: падает ли он на опечатке?"
echo
echo "--- НЕГАТИВНЫЕ: обязан отвергнуть ---"
check 2 "опечатка в skip-stages (course-lnt)"   "course-lnt"  ""            "course-content"
check 2 "опечатка в extra-stages (sast-semgrp)" ""            "sast-semgrp" "library"
check 2 "выдуманная стадия"                     "нет-такой"   ""            "library"
check 2 "опечатка среди верных имён"            "sca,secrts"  ""            "library"

echo
echo "--- ПОЗИТИВНЫЕ контроли: обязан принять ---"
echo "    (без них «падает» ничего не значило бы: скрипт, падающий всегда,"
echo "     тоже «падает на опечатке»)"
check 0 "пустые skip/extra"                     ""            ""            "library"
check 0 "верное имя реализованной стадии"       "sca"         ""            "library"
check 0 "имя из ДРУГОГО профиля (осмысленный no-op)" "course-lint" ""       "library"
check 0 "нереализованная стадия из профиля"     "pii-gate"    ""            "docs-shelf"

echo
echo "--- Разрешение режимов не сломалось ---"
out="$(SKIP="" EXTRA="" python3 "$resolve" "$profiles/library.yml" "$profiles" \
       --implemented "$IMPL" 2>/dev/null)"
if printf '%s\n' "$out" | grep -qE '^secrets=(B|A|off)$' &&
   printf '%s\n' "$out" | grep -qE '^sca=(B|A|off)$'; then
  echo "  ok   выдаёт режимы для реализованных стадий"; n_ok=$((n_ok + 1))
else
  echo "  FAIL выход не похож на набор режимов:"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1
fi

out="$(SKIP="sca" EXTRA="" python3 "$resolve" "$profiles/library.yml" "$profiles" \
       --implemented "$IMPL" 2>/dev/null)"
if printf '%s\n' "$out" | grep -qx 'sca=off'; then
  echo "  ok   skip действительно выключает стадию"; n_ok=$((n_ok + 1))
else
  echo "  FAIL skip=sca не выключил стадию:"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1
fi

# Release-стадия живёт вне контракта B/A/off. Проверяется здесь же, потому что
# ветка `--release-stage` есть только у приватного близнеца, а файл общий:
# сломав её правкой в публичном репо, узнать об этом было бы неоткуда.
out="$(SKIP="" EXTRA="" python3 "$resolve" "$profiles/library.yml" "$profiles" \
       --implemented "$IMPL" --release-stage sbom 2>/dev/null)"
if printf '%s\n' "$out" | grep -qE '^sbom=(R|off)$'; then
  echo "  ok   release-стадия выдаётся отдельной строкой"; n_ok=$((n_ok + 1))
else
  echo "  FAIL release-стадия не выдана:"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1
fi

echo
if [ "$fail" = "0" ]; then
  echo "OK: $n_ok/$n_ok — опечатка падает, верное имя проходит, режимы разрешаются."
else
  echo "ПРОВАЛ: profile-resolve либо глотает опечатки, либо ругается на верные имена."
fi
exit "$fail"
