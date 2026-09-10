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
echo "--- Стадии, объявленные профилем и НЕ исполняемые этим конвейером ---"
echo "    (#19: про них печатался только ::notice::, которого вердикт Gate"
echo "     не видел — то есть «blocking-находок нет» звучало и тогда, когда"
echo "     блокирующей по профилю стадии в конвейере нет вовсе)"

unimpl_of() { # unimpl_of <ключ> <SKIP> <класс> [доп. аргументы]
  local key="$1" skip="$2" cls="$3"; shift 3
  SKIP="$skip" EXTRA="" python3 "$resolve" "$profiles/$cls.yml" "$profiles" \
    --implemented "$IMPL" "$@" 2>/dev/null | sed -n "s/^${key}=//p"
}

# library объявляет `sast-sonar: B`, а исполняющего шага у стадии нет ни в
# одном воркфлоу обоих репо — ровно тот случай, ради которого выход заведён.
got="$(unimpl_of unimplemented-blocking "" library)"
if printf '%s' "$got" | tr ',' '\n' | grep -qx 'sast-sonar'; then
  echo "  ok   нереализованная BLOCKING-стадия названа отдельно"; n_ok=$((n_ok + 1))
else
  echo "  FAIL sast-sonar (B в library) не попал в unimplemented-blocking: '$got'"; fail=1
fi

# Тот же профиль объявляет `pii-gate: A`. Общий список её несёт, список
# блокирующих — нет: иначе advisory и blocking снова неразличимы.
got_all="$(unimpl_of unimplemented "" library)"
if printf '%s' "$got_all" | tr ',' '\n' | grep -qx 'pii-gate' &&
   ! printf '%s' "$got" | tr ',' '\n' | grep -qx 'pii-gate'; then
  echo "  ok   нереализованная advisory-стадия в общем списке, но не среди blocking"; n_ok=$((n_ok + 1))
else
  echo "  FAIL pii-gate (A в library): всего='$got_all' blocking='$got'"; fail=1
fi

# Выключил сам — знает сам. Строка про такую стадию приучала бы пропускать
# весь вердикт.
if ! unimpl_of unimplemented "pii-gate" library | tr ',' '\n' | grep -qx 'pii-gate'; then
  echo "  ok   явный skip вычитается из списка"; n_ok=$((n_ok + 1))
else
  echo "  FAIL skip=pii-gate всё равно попал в unimplemented"; fail=1
fi

# Release-стадия — вне контракта B/A/off, вердикта Gate про неё нет. Первая
# редакция фильтровала по `!= off`, и light-конвейер (`--release-stage` ему
# не передаётся) объявлял `sbom` неисполняемым.
if ! unimpl_of unimplemented "" library | tr ',' '\n' | grep -qx 'sbom'; then
  echo "  ok   release-стадия не считается неисполняемой"; n_ok=$((n_ok + 1))
else
  echo "  FAIL sbom (режим R) попал в unimplemented"; fail=1
fi

# Контроль, обязанный молчать: стадия, которую конвейер исполняет, в списке
# делать нечего. Без него правило «всё, чего нет в implemented» прошло бы
# проверку, даже если бы список складывался из чего попало.
if ! unimpl_of unimplemented "" library | tr ',' '\n' | grep -qx 'secrets'; then
  echo "  ok   реализованная стадия в списке не появляется"; n_ok=$((n_ok + 1))
else
  echo "  FAIL secrets (реализована) попала в unimplemented"; fail=1
fi


# Стадия по событию (`--event-stages`, приватный ADR 0007 / #35): исполняет
# её другой воркфлоу этого же репо на своём триггере, поэтому в вердикте
# PR-конвейера она не «неисполняемая» — но только там, где флаг передан.
# Публичный light-конвейер флага не передаёт и исполняет её не больше, чем
# pii-gate: у него она в списке остаётся, и это правда с другой стороны.
echo
echo "--- Стадия по событию: свой режим, не «неисполняемая» там, где исполняется ---"
event_mode() { # event_mode <SKIP> <EXTRA> <класс>
  SKIP="$1" EXTRA="$2" python3 "$resolve" "$profiles/$3.yml" "$profiles" \
    --implemented "$IMPL" --event-stages mobile-scan 2>/dev/null | sed -n 's/^mobile-scan=//p'
}
got="$(event_mode "" "" app-client)"
if [ "$got" = "A" ]; then
  echo "  ok   app-client: mobile-scan=A отдельной строкой"; n_ok=$((n_ok + 1))
else
  echo "  FAIL app-client: mobile-scan ожидал A, получил '$got'"; fail=1
fi
got="$(event_mode "" "" library)"
if [ "$got" = "off" ]; then
  echo "  ok   library: mobile-scan=off (артефакта Xcode Cloud нет)"; n_ok=$((n_ok + 1))
else
  echo "  FAIL library: mobile-scan ожидал off, получил '$got'"; fail=1
fi
got="$(event_mode "mobile-scan" "" app-client)"
if [ "$got" = "off" ]; then
  echo "  ok   skip выключает стадию по событию"; n_ok=$((n_ok + 1))
else
  echo "  FAIL skip=mobile-scan не выключил: '$got'"; fail=1
fi
got="$(event_mode "" "mobile-scan" library)"
if [ "$got" = "A" ]; then
  echo "  ok   extra включает стадию по событию как advisory"; n_ok=$((n_ok + 1))
else
  echo "  FAIL extra=mobile-scan не включил: '$got'"; fail=1
fi
if ! unimpl_of unimplemented "" app-client --event-stages mobile-scan | tr ',' '\n' | grep -qx 'mobile-scan'; then
  echo "  ok   с флагом стадия по событию не считается неисполняемой"; n_ok=$((n_ok + 1))
else
  echo "  FAIL mobile-scan попал в unimplemented при --event-stages"; fail=1
fi
if unimpl_of unimplemented "" app-client | tr ',' '\n' | grep -qx 'mobile-scan'; then
  echo "  ok   без флага (light) стадия по событию честно в списке неисполняемых"; n_ok=$((n_ok + 1))
else
  echo "  FAIL без --event-stages mobile-scan (A в app-client) не попал в unimplemented"; fail=1
fi
# Имя стадии по событию — известное: skip по нему не должен падать как опечатка.
out="$(SKIP="mobile-scan" EXTRA="" python3 "$resolve" "$profiles/library.yml" "$profiles" \
       --implemented "$IMPL" --event-stages mobile-scan 2>"$TMP_ERR")"; rc=$?
if [ "$rc" = "0" ]; then
  echo "  ok   имя стадии по событию известно валидации (rc=0)"; n_ok=$((n_ok + 1))
else
  echo "  FAIL skip=mobile-scan отвергнут как опечатка (rc=$rc)"; sed 's/^/       /' "$TMP_ERR"; fail=1
fi

echo
if [ "$fail" = "0" ]; then
  echo "OK: $n_ok/$n_ok — опечатка падает, верное имя проходит, режимы разрешаются."
else
  echo "ПРОВАЛ: profile-resolve либо глотает опечатки, либо ругается на верные имена."
fi
exit "$fail"
