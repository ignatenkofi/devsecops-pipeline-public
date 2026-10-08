#!/usr/bin/env python3
"""Манифест обязан объявлять ровно те выходы, которые печатает его скрипт.

Из пойманной грабли (#19). `pins.py discover` с самого начала печатал в
`$GITHUB_OUTPUT` восемь ключей, а `actions/pin-discover/action.yml` объявлял
шесть: `has-manual` и `manual` не были объявлены никогда.

Почему это не поймалось ничем. Необъявленный выход composite action — не
ошибка сборки и не предупреждение: у потребителя `steps.<id>.outputs.<имя>`
просто раскрывается в ПУСТУЮ СТРОКУ. В `nightly-bump.yml` это выглядело так:

    state: ${{ steps.versions.outputs.has-manual == 'true' && 'open' || 'close' }}

Сравнение "" с 'true' ложно всегда, значит ветка `open` недостижима, значит
признак живости «пины без гарда отстали от апстрима» не мог сработать НИ
ПРИ КАКОМ состоянии мира. Сигнал, не способный сработать, хуже
отсутствующего: он занимает место настоящего.

Детектор смотрит на стык двух артефактов, каждый из которых по отдельности
корректен, — поэтому ни линт YAML, ни тесты скрипта его не видят. Проверяются
три стороны:

  A. Множества совпадают. Напечатанный, но не объявленный ключ — тихая пустая
     строка у потребителя (собственно #19). Объявленный, но никогда не
     печатаемый — то же самое с другой стороны: манифест обещает значение,
     которого не будет.
  B. Объявленный выход ссылается на СВОЁ имя в `steps.<id>.outputs.<имя>`.
     Опечатка здесь даёт ровно тот же пустой результат, но выглядит как
     полностью объявленный выход.
  C. И на СВОЙ шаг: `<id>` — тот шаг `runs.steps` этого манифеста, который
     печатает ключи. `steps.run.outputs.sca` при шаге `id: resolve` — та же
     пустая строка, а A и B её не видят: имя совпадает, множество тоже.

Сторожатся ВСЕ манифесты под `actions/` с непустым `outputs:`, и это тоже
проверяется, а не обещается: манифест с выходами, которого нет в файле пар,
— нарушение с его именем. Первая редакция сторожила один pin-discover — и
удалённый выход `sca` у profile-resolve не замечал ни этот линт, ни
assert-composite-hygiene, ни actionlint, ни assert-profile-resolve. Цена там
выше, чем у #19: `pipeline-light.yml` читает `steps.profile.outputs.sca`,
"" != 'off' — стадия работает, а Gate видит режим "", а не "B", и печатает
только предупреждение. Блокирующая по профилю стадия молча становилась
advisory у каждого потребителя `v1`. Вторая сторожила список в коде, и
манифест вне списка проходил так же молча.

Файл общий с близнецом и обязан совпадать байт-в-байт
(tests/lint/assert-twins.py). Различается только то, ЧТО сторожить: пары
«манифест → скрипт → как спросить у кода его ключи» и мутанты настоящих
манифестов живут в `tests/lint/action-outputs.yml`, своём у каждого репо
(`.yml` правилом 1 близнецов не сверяется — намеренно). Пара, ключи которой
офлайн не вывести, записывается там как `unverified` с причиной и
печатается предупреждением «не проверено», а не пропускается молча; ссылки
B и C у неё сверяются всё равно.

Детектор обязан уметь краснеть, и это проверяется здесь же, а не верой.
Вместе с проходом он прогоняет себя: на заведомо испорченных текстовых
манифестах (ключ убран, ключ лишний, ссылка на чужое имя, ссылка на чужой
шаг) — это проверка сравнения; на дереве с манифестом вне списка — это
проверка охвата; на шагах-фикстурах (упал, напечатав ключи; отработал, не
напечатав; пишет во все файлы команд) — это проверка исполнения; и на
мутантах НАСТОЯЩИХ манифестов из файла пар в копии дерева — это проверка всей
цепочки «манифест → его же код → ключи». Не пойман хоть один мутант,
мутация промахнулась мимо файла или у пары нет ни одного мутанта, — линт
падает как слепой.

Использование:  assert-action-outputs.py [корень репозитория]
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - среда без pyyaml
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

# Пары и мутанты этого репо. Путь от корня проверяемого дерева, а не от
# этого файла: данные описывают дерево, которое сторожат.
DATA_REL = "tests/lint/action-outputs.yml"

REF_RE = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")

# Ссылку на шаг в другой форме — индексной (`steps['run'].outputs.x`,
# `steps.run.outputs['x']`), фильтром (`steps.*`) — REF_RE не разбирает, и
# проверки имени и шага её не видят: чужой шаг в такой форме давал «OK»
# (ревью #61). Поэтому `steps`, который REF_RE не разобрал целиком, —
# нарушение, а не тишина.
STEPS_RE = re.compile(r"(?<![\w.-])steps\b", re.IGNORECASE)


def unparsed_steps(value: str) -> list:
    """Ссылки на steps в `value:`, которые REF_RE не разбирает целиком."""
    found = []
    for token in STEPS_RE.finditer(value):
        ref = REF_RE.match(value, token.start())
        if ref is None or value[ref.end():ref.end() + 1] in (".", "["):
            found.append(value[token.start():].split("}}")[0].strip())
    return found


# Файлы команд, через которые шаг Actions влияет на СЛЕДУЮЩИЕ шаги джобы.
# Исполняя шаг манифеста, линт подменяет все пять, а не один GITHUB_OUTPUT:
# унаследованный путь внутри Actions — файл настоящего шага selftest, и
# правка манифеста, начавшая писать в GITHUB_ENV, тихо меняла бы окружение
# самой проверки.
FILE_COMMANDS = ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STATE",
                 "GITHUB_STEP_SUMMARY")


def _keys(text: str) -> set:
    return {line.split("=", 1)[0] for line in text.splitlines() if "=" in line}


def _unknown(what: str, detail: str) -> SystemExit:
    # Отказ деривации — «не смогли проверить», а не «нарушений нет»: пустое
    # множество ключей сошлось бы с пустым `outputs:` и выглядело бы здоровьем.
    return SystemExit(f"::error::{what} — множество выходов неизвестно, "
                      f"это не «нарушений нет».\n{detail}")


def _pins_keys(pins: Path, *args: str) -> set:
    """Ключи, которые подкоманда pins.py печатает на минимальной фикстуре.

    Спрашиваем сам скрипт, а не список в этом файле: вторая деривация того
    же множества протухла бы ровно так же, как протух манифест.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "actions" / "x").mkdir(parents=True)
        (root / "actions" / "x" / "action.yml").write_text(
            'name: x\ninputs:\n  a-version:\n    default: "1.2.3"\nruns:\n'
            "  using: composite\n",
            encoding="utf-8",
        )
        (root / "spec.json").write_text(
            json.dumps([{"name": "a", "file": "actions/x/action.yml",
                         "input": "a-version", "source": "github", "id": "x/a"}]),
            encoding="utf-8",
        )
        # Один файл на обе подкоманды: и `--upstream-from` у discover, и
        # `--targets` у apply — это {имя: версия}. 1.2.3 -> 1.2.4, чтобы apply
        # прошёл путь записи, а не только «двигать нечего».
        (root / "versions.json").write_text(json.dumps({"a": "1.2.4"}), encoding="utf-8")
        run = subprocess.run(
            [sys.executable, str(pins), *args],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if run.returncode != 0:
            raise _unknown(f"{pins}: {args[0]} не отработал на фикстуре", run.stderr)
        return _keys(run.stdout)


def discover_keys(manifest: Path, pins: Path) -> set:
    return _pins_keys(pins, "discover", "--spec", "spec.json",
                      "--upstream-from", "versions.json")


def apply_keys(manifest: Path, pins: Path) -> set:
    return _pins_keys(pins, "apply", "--spec", "spec.json", "--targets", "versions.json")


def step_keys(manifest: Path, step_id: str, env: dict) -> set:
    """Ключи, которые шаг `id: <step_id>` манифеста пишет в $GITHUB_OUTPUT.

    Исполняется тело шага из САМОГО манифеста, а не скрипт с аргументами,
    переписанными сюда: `--implemented` у profile-resolve — свойство
    манифеста (у близнецов наборы стадий разные), и копия списка в линте
    протухла бы так же тихо, как манифест. Имён стадий здесь нет ни одного.

    `python3` в PATH подменяется интерпретатором этого линта: шаг проверяет
    `import yaml` и иначе ставит pyyaml через pip — локальный прогон линта
    не должен ничего ставить в чужое окружение. pyyaml у этого
    интерпретатора есть: без него линт не дошёл бы досюда.
    """
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    steps = [s for s in ((data.get("runs") or {}).get("steps") or [])
             if isinstance(s, dict) and s.get("id") == step_id]
    if len(steps) != 1 or steps[0].get("shell") != "bash" or not steps[0].get("run"):
        raise _unknown(f"{manifest}: нет ровно одного bash-шага id: {step_id}",
                       "исполнять нечего")
    with tempfile.TemporaryDirectory() as tmp:
        shim = Path(tmp) / "bin"
        shim.mkdir()
        (shim / "python3").write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8")
        (shim / "python3").chmod(0o755)
        commands = {name: Path(tmp) / name.lower() for name in FILE_COMMANDS}
        for path in commands.values():
            path.write_text("", encoding="utf-8")
        run = subprocess.run(
            # Те же флаги, с которыми Actions исполняет `shell: bash`.
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", steps[0]["run"]],
            cwd=tmp, capture_output=True, text=True, check=False,
            # Подменённое — последним: `env` пары не может вернуть шагу
            # настоящие файлы команд.
            env={**os.environ, **env,
                 "GITHUB_ACTION_PATH": str(manifest.parent),
                 **{name: str(path) for name, path in commands.items()},
                 "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}"},
        )
        keys = _keys(commands["GITHUB_OUTPUT"].read_text(encoding="utf-8"))
    # Оба условия, а не одно: шаг, упавший ПОСЛЕ печати части ключей, отдал
    # бы неполное множество, и сверка с ним выглядела бы здоровьем.
    if run.returncode != 0 or not keys:
        raise _unknown(f"{manifest}: шаг {step_id} дал rc {run.returncode} и "
                       f"ключей {len(keys)}", run.stdout + run.stderr)
    return keys


# Как спросить у кода, какие ключи он печатает. Имя способа — поле `keys`
# пары в файле данных; `unverified` — ключи офлайн не вывести, сверяются
# только ссылки B и C, а пара печатается предупреждением «не проверено».
DERIVATIONS = {
    "pins-discover": lambda pair, manifest, script: discover_keys(manifest, script),
    "pins-apply": lambda pair, manifest, script: apply_keys(manifest, script),
    "step": lambda pair, manifest, script: step_keys(
        manifest, pair["step"], {k: str(v) for k, v in (pair.get("env") or {}).items()}),
}
UNVERIFIED = "unverified"


def validate_data(data) -> list:
    """Форма файла пар. Ошибка формы — отказ проверять, а не «чисто»."""
    if not isinstance(data, dict):
        return [f"{DATA_REL}: ожидался словарь с ключами pairs и mutants"]
    problems = []
    pairs, mutants = data.get("pairs"), data.get("mutants")
    if not isinstance(pairs, list) or not pairs:
        return [f"{DATA_REL}: pairs пуст или не список — сторожить нечего"]
    if not isinstance(mutants, list):
        return [f"{DATA_REL}: mutants не список"]
    manifests = set()
    for i, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            problems.append(f"{DATA_REL}: pairs[{i}] не словарь")
            continue
        where = f"{DATA_REL}: pairs[{i}] ({pair.get('manifest')})"
        for field in ("manifest", "script", "keys", "step"):
            if not isinstance(pair.get(field), str) or not pair.get(field):
                problems.append(f"{where}: нет поля {field}")
        if pair.get("keys") not in (*DERIVATIONS, UNVERIFIED):
            problems.append(f"{where}: keys={pair.get('keys')!r} — способа нет; "
                            f"есть {sorted(DERIVATIONS)} и {UNVERIFIED}")
        if not isinstance(pair.get("env", {}), dict):
            problems.append(f"{where}: env обязан быть словарём «переменная: значение»")
        if pair.get("keys") == UNVERIFIED and not str(pair.get("reason") or "").strip():
            problems.append(f"{where}: unverified без reason — «не проверено» "
                            f"без причины неотличимо от забытого")
        if pair.get("manifest") in manifests:
            problems.append(f"{where}: манифест в списке дважды")
        manifests.add(pair.get("manifest"))
    covered = set()
    for i, mutant in enumerate(mutants):
        where = f"{DATA_REL}: mutants[{i}]"
        if not isinstance(mutant, dict):
            problems.append(f"{where} не словарь")
            continue
        for field in ("manifest", "label", "pattern"):
            if not isinstance(mutant.get(field), str) or not mutant.get(field):
                problems.append(f"{where}: нет поля {field}")
        if not isinstance(mutant.get("repl"), str):
            problems.append(f"{where}: repl обязан быть строкой (пустая — удаление)")
        if mutant.get("manifest") not in manifests:
            problems.append(f"{where}: манифест {mutant.get('manifest')} не из pairs")
        try:
            re.compile(str(mutant.get("pattern")))
        except re.error as exc:
            problems.append(f"{where}: pattern не компилируется ({exc})")
        covered.add(mutant.get("manifest"))
    # Пара без мутанта не доказана ничем: её сверка могла бы молчать на
    # любом дефекте, и самопроверка бы этого не увидела.
    for manifest in sorted(m for m in manifests - covered if isinstance(m, str)):
        problems.append(f"{DATA_REL}: у пары {manifest} нет ни одного мутанта — "
                        f"самопроверка её не доказывает")
    return problems


def load_data(root: Path) -> dict:
    path = root / DATA_REL
    if not path.is_file():
        # Пропавший файл пар — «проверять нечем», а не «чисто».
        raise SystemExit(f"::error::нет {DATA_REL} — пар «манифест → скрипт» этого "
                         f"репо нет, проверять нечем")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SystemExit(f"::error::{DATA_REL} не разбирается: {exc}")
    problems = validate_data(data)
    if problems:
        raise SystemExit("\n".join(f"::error::{p}" for p in problems))
    return data


def check(manifest_rel: str, manifest_text: str, printed, producer: str) -> list:
    """Нарушения манифеста. `printed is None` — ключи не выведены (unverified):
    множества не сравниваются, ссылки на имя и шаг — сверяются."""
    problems = []
    doc = yaml.safe_load(manifest_text) or {}
    declared = doc.get("outputs") or {}
    ids = [s["id"] for s in ((doc.get("runs") or {}).get("steps") or [])
           if isinstance(s, dict) and s.get("id")]

    if producer not in ids:
        problems.append(
            f"{manifest_rel}: шага id: {producer}, который по {DATA_REL} печатает "
            f"ключи, в runs.steps нет (есть: {', '.join(ids) or 'ни одного'})"
        )
    if printed is not None:
        for missing in sorted(printed - set(declared)):
            problems.append(
                f"{manifest_rel}: скрипт печатает '{missing}', манифест его не объявляет "
                f"— у потребителя это пустая строка, а не ошибка (#19)"
            )
        for extra in sorted(set(declared) - printed):
            problems.append(
                f"{manifest_rel}: объявлен выход '{extra}', которого скрипт не печатает "
                f"— манифест обещает значение, которого не будет"
            )
    for name, body in sorted(declared.items()):
        value = str((body or {}).get("value", ""))
        refs = REF_RE.findall(value)
        for form in unparsed_steps(value):
            problems.append(
                f"{manifest_rel}: выход '{name}' ссылается на шаг в форме «{form}», "
                f"которую линт не разбирает, — имя и шаг не сверены; пишите "
                f"steps.<id>.outputs.<имя>"
            )
        if refs and name not in [out for _, out in refs]:
            problems.append(
                f"{manifest_rel}: выход '{name}' берёт значение из "
                f"{[out for _, out in refs]} — ссылка на чужое имя раскрывается в "
                f"пустую строку так же тихо"
            )
        for step_id, _ in refs:
            if step_id not in ids:
                problems.append(
                    f"{manifest_rel}: выход '{name}' ссылается на шаг '{step_id}', "
                    f"которого в runs.steps нет (есть: {', '.join(ids) or 'ни одного'}) "
                    f"— у потребителя пустая строка"
                )
            elif step_id != producer:
                problems.append(
                    f"{manifest_rel}: выход '{name}' берёт значение из шага "
                    f"'{step_id}', а ключи печатает шаг '{producer}'"
                )
    return problems


def unlisted(root: Path, listed: set) -> list:
    """Манифесты с непустым `outputs:`, которых нет в файле пар."""
    problems = []
    found = sorted({*root.glob("actions/**/action.yml"), *root.glob("actions/**/action.yaml")})
    for manifest in found:
        rel = manifest.relative_to(root).as_posix()
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if isinstance(doc, dict) and doc.get("outputs") and rel not in listed:
            problems.append(
                f"{rel}: объявляет выходы, но в {DATA_REL} его нет — сверять его "
                f"некому. Новый action с outputs: — новая пара (или unverified с "
                f"причиной)"
            )
    return problems


def audit(root: Path, data: dict) -> tuple:
    """(нарушения, пары «не проверено»)."""
    problems, skipped = [], []
    problems += unlisted(root, {pair["manifest"] for pair in data["pairs"]})
    for pair in data["pairs"]:
        manifest, script = root / pair["manifest"], root / pair["script"]
        if not manifest.is_file() or not script.is_file():
            # Пропавшая пара — «не смогли проверить», а не «чисто»: молчание
            # здесь неотличимо от здоровья, и это ровно тот класс, ради
            # которого детектор написан.
            raise SystemExit(f"::error::нет пары {pair['manifest']} / {pair['script']} — "
                             f"проверять нечем")
        if pair["keys"] == UNVERIFIED:
            printed = None
            skipped.append(f"{pair['manifest']}: множество выходов не сверено со "
                           f"скриптом — {' '.join(str(pair['reason']).split())} "
                           f"Сверены только ссылки value: на имя и шаг.")
        else:
            printed = DERIVATIONS[pair["keys"]](pair, manifest, script)
        problems += check(pair["manifest"], manifest.read_text(encoding="utf-8"),
                          printed, pair["step"])
    return problems, skipped


# --- самопроверка: детектор обязан различать ------------------------------
_STEPS = '\nruns:\n  using: composite\n  steps:\n    - id: run\n      shell: bash\n      run: "true"\n'
# Тот же источник `run` и соседний шаг `prep` перед ним: ссылка на
# существующий, но не тот шаг.
_TWO_STEPS = ('\nruns:\n  using: composite\n  steps:\n    - id: prep\n      shell: bash\n'
              '      run: "true"\n    - id: run\n      shell: bash\n      run: "true"\n')
_ALPHA = 'name: t\noutputs:\n  alpha:\n    value: "${{ steps.run.outputs.alpha }}"'
_GOOD = _ALPHA + '\n  beta:\n    value: "${{ steps.run.outputs.beta }}"' + _STEPS
_MUTANTS = (
    ("ключ убран", _ALPHA + _STEPS),
    ("ключ лишний", _ALPHA + '\n  beta:\n    value: "${{ steps.run.outputs.beta }}"'
     '\n  gamma:\n    value: "${{ steps.run.outputs.gamma }}"' + _STEPS),
    ("ссылка на чужое имя",
     _ALPHA + '\n  beta:\n    value: "${{ steps.run.outputs.alpha }}"' + _STEPS),
    ("ссылка на несуществующий шаг",
     _ALPHA + '\n  beta:\n    value: "${{ steps.other.outputs.beta }}"' + _STEPS),
    ("ссылка на соседний шаг",
     _ALPHA + '\n  beta:\n    value: "${{ steps.prep.outputs.beta }}"' + _TWO_STEPS),
    ("шаг-источник переименован", _GOOD.replace("    - id: run\n", "    - id: main\n")),
    # Индексный синтаксис: REF_RE его не разбирает, и до unparsed_steps оба
    # мутанта давали «OK» (ревью #61).
    ("индекс по шагу",
     _ALPHA + '\n  beta:\n    value: "${{ steps[\'prep\'].outputs.beta }}"' + _TWO_STEPS),
    ("индекс по имени выхода",
     _ALPHA + '\n  beta:\n    value: "${{ steps.prep.outputs[\'beta\'] }}"' + _TWO_STEPS),
)


def selftest_check() -> list:
    problems = []
    for label, text in (("здоровый", _GOOD),
                        ("здоровый с соседним шагом", _GOOD.replace(_STEPS, _TWO_STEPS)),
                        ("здоровый с inputs.steps рядом",
                         _GOOD.replace("steps.run.outputs.beta }}",
                                       "steps.run.outputs.beta || inputs.steps }}"))):
        if check("фикстура", text, {"alpha", "beta"}, "run"):
            problems.append(f"самопроверка: детектор нашёл нарушение в манифесте "
                            f"«{label}» — ложная тревога")
    for label, text in _MUTANTS:
        if not check("фикстура", text, {"alpha", "beta"}, "run"):
            problems.append(f"самопроверка: мутант «{label}» не пойман — детектор слеп")
    # unverified: множества не сравниваются, а ссылки на имя и шаг — да.
    text = dict(_MUTANTS)
    for label in ("ссылка на чужое имя", "ссылка на соседний шаг", "индекс по шагу"):
        if not check("фикстура", text[label], None, "run"):
            problems.append(f"самопроверка: у пары unverified мутант «{label}» не пойман")
    if check("фикстура", text["ключ убран"], None, "run"):
        problems.append("самопроверка: у пары unverified сравнились множества, "
                        "которых нет")
    return problems


def selftest_unlisted() -> list:
    """Охват — через audit(), а не через unlisted() напрямую: проверка, которую
    проход перестал звать, иначе оставалась бы зелёной."""
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, text in (("listed", _GOOD), ("stray", _GOOD), ("quiet", "name: q" + _STEPS)):
            (root / "actions" / name).mkdir(parents=True)
            (root / "actions" / name / "action.yml").write_text(text, encoding="utf-8")
        found, _ = audit(root, {"pairs": [{
            "manifest": "actions/listed/action.yml", "script": "actions/listed/action.yml",
            "keys": UNVERIFIED, "step": "run", "reason": "фикстура"}], "mutants": []})
        if not any(p.startswith("actions/stray/action.yml:") for p in found):
            problems.append("самопроверка: манифест с выходами вне списка не пойман — "
                            "охват держится на памяти")
        if any(p.startswith(("actions/listed/", "actions/quiet/")) for p in found):
            problems.append(f"самопроверка: охват ругается на манифест в списке или "
                            f"без выходов: {found}")
    return problems


def selftest_steps() -> list:
    """step_keys: отказ шага — «неизвестно», файлы команд — не настоящие."""
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # Файлы команд «настоящей джобы»: шаг, исполненный линтом, обязан
        # писать мимо них.
        job = {name: base / f"job-{name.lower()}" for name in FILE_COMMANDS}
        saved = {name: os.environ.get(name) for name in FILE_COMMANDS}

        def run(body: str):
            manifest = base / "action.yml"
            manifest.write_text(yaml.safe_dump(
                {"name": "t", "runs": {"using": "composite", "steps": [
                    {"id": "run", "shell": "bash", "run": body}]}}), encoding="utf-8")
            for path in job.values():
                path.write_text("", encoding="utf-8")
            os.environ.update({name: str(path) for name, path in job.items()})
            try:
                return step_keys(manifest, "run", {})
            except SystemExit as exc:
                return exc
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

        got = run('echo "alpha=1" >> "$GITHUB_OUTPUT"\nexit 3\n')
        if not isinstance(got, SystemExit):
            problems.append(f"самопроверка: шаг упал (rc 3), напечатав ключи {sorted(got)}, "
                            f"а линт их принял — отказ шага не учтён")
        got = run('echo "ключей нет"\n')
        if not isinstance(got, SystemExit):
            problems.append("самопроверка: шаг отработал без ключей, а линт принял "
                            "пустое множество")
        got = run('echo "alpha=1" >> "$GITHUB_OUTPUT"\n'
                  'echo "LEAK=1" >> "$GITHUB_ENV"\necho /leak >> "$GITHUB_PATH"\n'
                  'echo "leak=1" >> "$GITHUB_STATE"\necho "# leak" >> "$GITHUB_STEP_SUMMARY"\n')
        if got != {"alpha"}:
            problems.append(f"самопроверка: здоровый шаг дал {got!r} вместо {{'alpha'}}")
        touched = sorted(name for name, path in job.items()
                         if path.read_text(encoding="utf-8"))
        if touched:
            problems.append(f"самопроверка: шаг дописал в {', '.join(touched)} "
                            f"настоящей джобы — файлы команд не подменены")
    return problems


def selftest_data() -> list:
    """Форма файла пар: пробелы в данных — отказ, а не тишина."""
    problems = []
    pair = {"manifest": "actions/a/action.yml", "script": "actions/a/a.py",
            "keys": "step", "step": "run"}
    mutant = {"manifest": "actions/a/action.yml", "label": "x", "pattern": "x", "repl": ""}
    if validate_data({"pairs": [pair], "mutants": [mutant]}):
        problems.append("самопроверка: годный файл пар отвергнут")
    for label, data in (
        ("пара без мутанта", {"pairs": [pair], "mutants": []}),
        ("unverified без причины", {"pairs": [{**pair, "keys": UNVERIFIED}],
                                    "mutants": [mutant]}),
        ("неизвестный способ", {"pairs": [{**pair, "keys": "guess"}], "mutants": [mutant]}),
        ("пустой pairs", {"pairs": [], "mutants": []}),
    ):
        if not validate_data(data):
            problems.append(f"самопроверка: файл пар «{label}» принят")
    return problems


def selftest() -> list:
    return selftest_check() + selftest_unlisted() + selftest_steps() + selftest_data()


# Что читают деривации: манифесты и скрипты под actions/, профили —
# шаг profile-resolve (`${GITHUB_ACTION_PATH}/../../profiles`).
_TREE = ("actions", "profiles")


def selftest_real(root: Path, data: dict) -> list:
    """Каждый мутант настоящего манифеста обязан добавить нарушение.

    Мутанты — из файла пар этого репо: у близнецов манифесты разные, и
    общий файл не может знать, что в них ломать. Сравнение — с чистой
    копией, а не с нулём: если в самом дереве уже есть расхождение, о нём
    скажет основной проход, а здесь важно одно — видит ли детектор то, что
    внёс мутант. Мутация, не изменившая файл (regex промахнулся после правки
    манифеста), — тоже слепота: «не пойман» и «не было чего ловить» иначе
    неразличимы.
    """
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp)
        for sub in _TREE:
            if (root / sub).is_dir():
                shutil.copytree(root / sub, copy / sub,
                                ignore=shutil.ignore_patterns("__pycache__"))
        clean = set(audit(copy, data)[0])
        for mutant in data["mutants"]:
            rel, label = mutant["manifest"], mutant["label"]
            path = copy / rel
            original = path.read_text(encoding="utf-8")
            mutated, hits = re.subn(mutant["pattern"], mutant["repl"], original)
            if hits != 1:
                problems.append(f"самопроверка: мутация «{label}» промахнулась мимо "
                                f"{rel} (совпадений {hits}) — детектор не проверен")
                continue
            path.write_text(mutated, encoding="utf-8")
            try:
                caught = set(audit(copy, data)[0]) - clean
            finally:
                path.write_text(original, encoding="utf-8")
            if not caught:
                problems.append(f"самопроверка: мутант «{label}» в {rel} не пойман "
                                f"— детектор слеп")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    data = load_data(root)

    # Проход и самопроверка — оба, всегда. Мутация промахивается и тогда,
    # когда её цель уже сломана в самом дереве (выход `sca` убран — убирать
    # нечего), и одно «детектор не проверен» без настоящего нарушения рядом
    # отправило бы читать не туда. Проход — первым: отказ деривации на
    # настоящем дереве должен назвать настоящий путь, а не путь копии.
    problems, skipped = audit(root, data)
    blind = selftest() + selftest_real(root, data)
    for s in skipped:
        print(f"::warning::не проверено: {s}", file=sys.stderr)
    if problems or blind:
        for p in problems + blind:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: расхождений {len(problems)}, провалов самопроверки {len(blind)}",
              file=sys.stderr)
        return 1

    checked = len(data["pairs"]) - len(skipped)
    print(f"OK: {checked} манифест(ов) объявляют ровно то, что печатают их скрипты"
          + (f"; не проверено {len(skipped)} (warning выше, причина — в {DATA_REL})"
             if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
