#!/usr/bin/env bash
# Гард на механику бампа пинов (#20). Общая часть ночного обновления живёт в
# actions/pin-tools/pins.py и вызывается из ОБОИХ конвейеров — значит ошибка
# здесь тиражируется на семь пинов сразу.
#
# Три класса, каждый из которых тихий:
#   * прочитать не тот пин. В actions/docs-lint/action.yml их ДВА, а рядом с
#     версиями лежат нечисловые default'ы (severity-floor у semgrep, sha256 у
#     osv-scanner). Регекс по первому `default:` дал бы неверный пин молча.
#   * применить мажор. Мажор меняет CLI, на который стоят наши actions;
#     применённый автоматически, он ломает стадию ночью и без человека.
#   * принять бэкпорт за обновление. `releases/latest` у GitHub — самый
#     свежий по ДАТЕ, а не по номеру; релиз в старую ветку откатил бы пин.
#
# Сеть здесь запрещена: апстрим подаётся файлом через --upstream-from. Гард,
# который нельзя прогнать офлайн, не прогоняют вовсе.
#
# usage: tests/negative/assert-pins.sh
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
PINS="$root/actions/pin-tools/pins.py"
[ -f "$PINS" ] || { echo "::error::нет $PINS"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0; n_ok=0
ok()   { echo "  ok:   $*"; n_ok=$((n_ok + 1)); }
bad()  { echo "  FAIL: $*" >&2; fail=1; }

# --- фикстура: файл с ДВУМЯ пинами и нечисловым соседом --------------------
mkdir -p "$TMP/actions/two"
cat > "$TMP/actions/two/action.yml" <<'YML'
name: two-pins
inputs:
  target:
    required: false
    default: "."
  alpha-version:
    description: "первый пин"
    required: false
    default: "1.2.3"
  beta-version:
    description: "второй пин"
    required: false
    default: "0.9.0"
  severity-floor:
    required: false
    default: "ERROR"
runs:
  using: composite
YML

cat > "$TMP/spec.json" <<'JSON'
[{"name":"alpha","file":"actions/two/action.yml","input":"alpha-version","source":"github","id":"x/alpha"},
 {"name":"beta","file":"actions/two/action.yml","input":"beta-version","source":"github","id":"x/beta"}]
JSON

disc() { # disc <json апстрима> -> stdout discover
    printf '%s' "$1" > "$TMP/up.json"
    ( cd "$TMP" && python3 "$PINS" discover --spec spec.json --upstream-from up.json ) 2>"$TMP/err"
}
val() { printf '%s\n' "$1" | grep "^$2=" | cut -d= -f2-; }

echo "Механика бампа пинов: читает ли она тот пин и применяет ли то, что можно?"
echo
echo "--- чтение: якорь — имя входа, а не порядок в файле ---"

out=$(disc '{"alpha":"1.2.3","beta":"0.9.0"}')
res=$(val "$out" result)
case "$res" in
    *'"alpha"'*'"cur": "1.2.3"'*) ok "alpha прочитан как 1.2.3" ;;
    *) bad "alpha прочитан неверно: $res" ;;
esac
case "$res" in
    *'"beta"'*'"cur": "0.9.0"'*) ok "beta прочитан как 0.9.0 (второй пин в том же файле)" ;;
    *) bad "beta прочитан неверно: $res" ;;
esac
[ "$(val "$out" needs-bump)" = "false" ] && ok "нет обновлений — бампать нечего" \
    || bad "объявил бамп там, где версии совпадают"

echo
echo "--- НЕГАТИВ: несуществующий вход обязан падать, а не брать соседний ---"
cat > "$TMP/spec-bad.json" <<'JSON'
[{"name":"alpha","file":"actions/two/action.yml","input":"gamma-version","source":"github","id":"x/g"}]
JSON
printf '{"alpha":"2.0.0"}' > "$TMP/up.json"
if ( cd "$TMP" && python3 "$PINS" discover --spec spec-bad.json --upstream-from up.json ) \
        >/dev/null 2>"$TMP/err"; then
    bad "несуществующий вход прошёл молча — значит взят чужой пин"
else
    grep -q "ожидал ровно один пин" "$TMP/err" \
        && ok "несуществующий вход падает и называет причину" \
        || bad "упал, но не о том: $(head -1 "$TMP/err")"
fi

echo
echo "--- НЕГАТИВ: бэкпорт не двигает пин ---"
out=$(disc '{"alpha":"1.1.0","beta":"0.9.0"}')
res=$(val "$out" result)
case "$res" in
    *'"alpha"'*'"target": "1.2.3"'*) ok "апстрим ниже пина — цель осталась 1.2.3" ;;
    *) bad "бэкпорт откатил пин: $res" ;;
esac
[ "$(val "$out" needs-bump)" = "false" ] && ok "бэкпорт не считается обновлением" \
    || bad "бэкпорт объявлен обновлением"
grep -q "ниже пина" "$TMP/err" && ok "бэкпорт назван вслух в warning" \
    || bad "бэкпорт проглочен молча"

echo
echo "--- НЕГАТИВ: мажор сообщается, но НЕ применяется ---"
out=$(disc '{"alpha":"2.0.0","beta":"0.9.0"}')
res=$(val "$out" result)
case "$res" in
    *'"alpha"'*'"target": "1.2.3"'*) ok "мажор оставлен на текущем пине" ;;
    *) bad "мажор применён автоматически: $res" ;;
esac
[ "$(val "$out" has-major)" = "true" ] && ok "мажор сообщён отдельным флагом" \
    || bad "мажор не сообщён"
[ "$(val "$out" needs-bump)" = "false" ] && ok "мажор не поднимает needs-bump" \
    || bad "мажор поехал бы в PR как обычный бамп"

echo
echo "--- ПОЗИТИВ: минор применяется (без него всё выше — просто «ничего не делает») ---"
out=$(disc '{"alpha":"1.3.0","beta":"0.9.0"}')
[ "$(val "$out" needs-bump)" = "true" ] && ok "минор поднимает needs-bump" \
    || bad "минор не распознан"
case "$(val "$out" targets)" in
    *'"alpha": "1.3.0"'*) ok "цель альфы — новая версия" ;;
    *) bad "цель не обновилась: $(val "$out" targets)" ;;
esac

echo
echo "--- apply: пишет ровно свой пин и не трогает соседний ---"
printf '%s' "$(val "$out" targets)" > "$TMP/targets.json"
( cd "$TMP" && python3 "$PINS" apply --spec spec.json --targets targets.json ) >/dev/null 2>&1
after=$(grep -c 'default: "1.3.0"' "$TMP/actions/two/action.yml")
[ "$after" = "1" ] && ok "альфа записана" || bad "альфа не записана"
grep -q 'default: "0.9.0"' "$TMP/actions/two/action.yml" \
    && ok "бета не тронута" || bad "затёрт соседний пин"
grep -q 'default: "ERROR"' "$TMP/actions/two/action.yml" \
    && ok "нечисловой сосед не тронут" || bad "затёрт severity-floor"
grep -q 'default: "\."' "$TMP/actions/two/action.yml" \
    && ok "target не тронут" || bad "затёрт target"

echo
echo "--- НЕГАТИВ: autobump=false сообщается, но не применяется ---"
# Инструмент без негатив-фикстуры обязан попадать в отчёт о дрейфе (иначе он
# тихо стареет — с этого началась #20) и НЕ попадать в применение (иначе бамп
# идёт без гарда — с этого началась поломка docs-lint).
# Своя фикстура, а не общая: блок apply выше уже переписал общую, и ассерт
# на «цель осталась текущей» читал бы значение, записанное предыдущим шагом.
# Тест, зависящий от порядка блоков, ломается при первой же перестановке.
mkdir -p "$TMP/actions/manual"
sed 's/^name: two-pins/name: manual-fixture/' "$TMP/actions/two/action.yml" \
    | sed 's/default: "1.3.0"/default: "1.2.3"/' > "$TMP/actions/manual/action.yml"
cat > "$TMP/spec-manual.json" <<'JSON'
[{"name":"alpha","file":"actions/manual/action.yml","input":"alpha-version","source":"github","id":"x/a","autobump":false},
 {"name":"beta","file":"actions/manual/action.yml","input":"beta-version","source":"github","id":"x/b"}]
JSON
printf '{"alpha":"1.9.0","beta":"0.9.0"}' > "$TMP/up.json"
out=$( cd "$TMP" && python3 "$PINS" discover --spec spec-manual.json --upstream-from up.json 2>/dev/null )
case "$(val "$out" targets)" in
    *'"alpha": "1.2.3"'*) ok "autobump=false — цель осталась текущей" ;;
    *) bad "пин без гарда поехал автоматически: $(val "$out" targets)" ;;
esac
[ "$(val "$out" needs-bump)" = "false" ] && ok "autobump=false не поднимает needs-bump" \
    || bad "пин без гарда уехал бы в PR"
[ "$(val "$out" has-manual)" = "true" ] && ok "дрейф без гарда сообщён отдельно" \
    || bad "дрейф без гарда проглочен — инструмент стареет молча"
case "$(val "$out" manual)" in
    *"alpha 1.2.3 -> 1.9.0"*) ok "в списке ручных названы обе версии" ;;
    *) bad "список ручных бесполезен: $(val "$out" manual)" ;;
esac

echo
echo "--- боевая спека этого репозитория читается ---"
if [ -f "$root/.github/pins.json" ]; then
    names=$(python3 -c "
import json,sys
print(' '.join(e['name'] for e in json.load(open('$root/.github/pins.json'))))")
    up=$(python3 -c "
import json
spec=json.load(open('$root/.github/pins.json'))
print(json.dumps({e['name']:'0.0.0' for e in spec}))")
    printf '%s' "$up" > "$TMP/up-real.json"
    if ( cd "$root" && python3 "$PINS" discover --spec .github/pins.json \
            --upstream-from "$TMP/up-real.json" ) >/dev/null 2>"$TMP/err"; then
        ok "боевые пины читаются: $names"
    else
        bad "боевая спека не читается: $(head -2 "$TMP/err")"
    fi
else
    echo "  skip: .github/pins.json ещё нет"
fi

echo
if [ "$fail" -eq 0 ]; then
    echo "OK: $n_ok/$n_ok — читается нужный пин, мажор и бэкпорт не применяются, минор применяется."
else
    echo "ПРОВАЛ: механика бампа либо берёт не тот пин, либо применяет то, что нельзя."
fi
exit "$fail"
