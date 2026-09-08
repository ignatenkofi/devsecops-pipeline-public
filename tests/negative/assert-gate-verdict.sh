#!/usr/bin/env bash
# assert-gate-verdict.sh — вердикт шага Gate обязан отличать «проверено и
# чисто» от «не проверялось».
#
# ЗАЧЕМ. У «ничего не проверено» два класса, и до 2026-09-08 вердикт знал
# только один. Первый — стадия отработала, но правил под язык репозитория
# не нашлось (sarif/.inapplicable, #33): текст вердикта менялся. Второй —
# стадия объявлена профилем, но конвейер её не исполняет: про неё
# resolve.py печатал ::notice::, то есть ровно тот канал, который issue #19
# называет непрочитанным. Профиль library объявляет `sast-sonar: B`, шага
# под неё нет ни в одном воркфлоу обоих репо — и вердикт говорил
# «blocking-находок нет».
#
# ЧТО ЭТО ЗА ТЕСТ. Не копия кода вердикта, а прогон САМОГО тела шага,
# извлечённого из воркфлоу: копия разошлась бы с оригиналом молча и
# зеленела бы на сломанном конвейере. Из YAML берётся шаг, чьё имя
# начинается на «Gate», и исполняется в песочнице с подставленным
# окружением.
#
# usage: tests/negative/assert-gate-verdict.sh [путь-к-воркфлоу]
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
wf="${1:-}"
if [ -z "$wf" ]; then
  for cand in "$root/.github/workflows/pipeline-light.yml" "$root/.github/workflows/pipeline.yml"; do
    [ -f "$cand" ] && { wf="$cand"; break; }
  done
fi
[ -f "$wf" ] || { echo "::error::не найден воркфлоу конвейера"; exit 2; }

body="$(python3 - "$wf" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for job in (d.get("jobs") or {}).values():
    for step in (job.get("steps") or []):
        if str(step.get("name", "")).startswith("Gate"):
            print(step["run"], end="")
            sys.exit(0)
sys.exit(3)
PY
)" || { echo "::error::шаг Gate не найден в $wf — извлекать нечего"; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
printf '%s\n' "$body" > "$work/gate.sh"
mkdir -p "$work/sarif"

fail=0
n_ok=0

# run_case <описание> <.inapplicable или пусто> <U_ALL> <U_BLOCKING> <ожидаемый-grep> <запрещённый-grep>
run_case() {
  local desc="$1" inap="$2" uall="$3" ublock="$4" want="$5" deny="$6"
  local out rc
  if [ -n "$inap" ]; then printf '%s\n' "$inap" > "$work/sarif/.inapplicable"
  else : > "$work/sarif/.inapplicable"; fi
  out="$(cd "$work" && M_SECRETS=B M_SEMGREP=B M_SCA=B \
        M_DOCS_LINT=A M_CONTAINER=A M_COURSE_LINT=off \
        O_SECRETS=success O_SEMGREP=success O_SCA=success \
        O_DOCS_LINT=success O_CONTAINER=success O_COURSE_LINT=skipped \
        U_ALL="$uall" U_BLOCKING="$ublock" bash gate.sh 2>&1)"
  rc=$?
  if [ "$rc" != "0" ]; then
    echo "  FAIL $desc — вердикт вернул rc=$rc на чистом прогоне"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1; return
  fi
  if ! printf '%s' "$out" | grep -qF "$want"; then
    echo "  FAIL $desc — в вердикте нет '$want':"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1; return
  fi
  if [ -n "$deny" ] && printf '%s' "$out" | grep -qF "$deny"; then
    echo "  FAIL $desc — вердикт содержит запрещённое '$deny':"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1; return
  fi
  echo "  ok   $desc"; n_ok=$((n_ok + 1))
}

echo "gate: отличает ли вердикт «чисто» от «не проверялось»? ($(basename "$wf"))"
echo

# Контроль, без которого остальное ничего не значит: на полностью
# проверенном прогоне вердикт обязан быть коротким и без оговорок.
run_case "всё проверено — вердикт без оговорок" \
  "" "" "" "gate: blocking-находок нет" "СРЕДИ ПРОВЕРЕННОГО"

run_case "стадия без применимых правил названа" \
  "sast-semgrep" "" "" "не проверялось: sast-semgrep" ""

run_case "неисполняемая стадия названа в вердикте" \
  "" "pii-gate,sast-sonar" "" "объявлено профилем, но не исполняется: pii-gate, sast-sonar" ""

run_case "неисполняемая BLOCKING названа отдельным warning" \
  "" "pii-gate,sast-sonar" "sast-sonar" "::warning::blocking-стадии профиля этот конвейер не исполняет: sast-sonar" ""

run_case "оба класса непроверенного попадают в одну строку" \
  "sast-semgrep" "pii-gate" "" "не проверялось: sast-semgrep; объявлено профилем, но не исполняется: pii-gate" ""

# Блокирующая стадия, которая ОТРАБОТАЛА и нашла проблемы, обязана ронять
# job — иначе тест доказал бы лишь то, что вердикт разговорчив.
out="$(cd "$work" && M_SECRETS=B M_SEMGREP=B M_SCA=B \
      M_DOCS_LINT=A M_CONTAINER=A M_COURSE_LINT=off \
      O_SECRETS=failure O_SEMGREP=success O_SCA=success \
      O_DOCS_LINT=success O_CONTAINER=success O_COURSE_LINT=skipped \
      U_ALL="" U_BLOCKING="" bash gate.sh 2>&1)"
if [ "$?" != "0" ] && printf '%s' "$out" | grep -q 'merge заблокирован'; then
  echo "  ok   находка blocking-стадии по-прежнему роняет job"; n_ok=$((n_ok + 1))
else
  echo "  FAIL находка blocking-стадии не уронила job:"; printf '%s\n' "$out" | sed 's/^/       /'; fail=1
fi

echo
if [ "$fail" = "0" ]; then
  echo "OK: $n_ok/$n_ok — вердикт различает проверенное, вхолостую и неисполняемое."
else
  echo "ПРОВАЛ: вердикт Gate называет непроверенное проверенным."
fi
exit "$fail"
