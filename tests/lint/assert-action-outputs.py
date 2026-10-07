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
две стороны:

  A. Множества совпадают. Напечатанный, но не объявленный ключ — тихая пустая
     строка у потребителя (собственно #19). Объявленный, но никогда не
     печатаемый — то же самое с другой стороны: манифест обещает значение,
     которого не будет.
  B. Объявленный выход ссылается на СВОЁ имя в `steps.<id>.outputs.<имя>`.
     Опечатка здесь даёт ровно тот же пустой результат, но выглядит как
     полностью объявленный выход.

Сторожатся все манифесты, которые объявляют выходы: pin-discover, pin-apply,
profile-resolve. Первая редакция сторожила один pin-discover — и удалённый
выход `sca` у profile-resolve не замечал ни этот линт, ни
assert-composite-hygiene, ни actionlint, ни assert-profile-resolve. Цена
там выше, чем у #19: `pipeline-light.yml` читает `steps.profile.outputs.sca`,
"" != 'off' — стадия работает, а Gate видит режим "", а не "B", и печатает
только предупреждение. Блокирующая по профилю стадия молча становилась
advisory у каждого потребителя `v1`.

Детектор обязан уметь краснеть, и это проверяется здесь же, а не верой.
Вместе с проходом он прогоняет себя дважды: на трёх заведомо испорченных
текстовых манифестах (ключ убран, ключ лишний, ссылка на чужое имя) — это
проверка сравнения, — и на мутантах НАСТОЯЩИХ манифестов в копии дерева
(выход стадии убран, стадия добавлена в `--implemented` без выхода, выход
`moved` у pin-apply убран) — это проверка всей цепочки «манифест → его же
код → ключи». Не пойман хоть один мутант, или мутация промахнулась мимо
файла, — линт падает как слепой.

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

REF_RE = re.compile(r"steps\.[A-Za-z0-9_-]+\.outputs\.([A-Za-z0-9_-]+)")


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
        out = Path(tmp) / "github-output"
        out.write_text("", encoding="utf-8")
        run = subprocess.run(
            # Те же флаги, с которыми Actions исполняет `shell: bash`.
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", steps[0]["run"]],
            cwd=tmp, capture_output=True, text=True, check=False,
            env={**os.environ, **env,
                 "GITHUB_ACTION_PATH": str(manifest.parent),
                 "GITHUB_OUTPUT": str(out),
                 "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}"},
        )
        keys = _keys(out.read_text(encoding="utf-8"))
    if run.returncode != 0 or not keys:
        raise _unknown(f"{manifest}: шаг {step_id} дал rc {run.returncode} и "
                       f"ключей {len(keys)}", run.stdout + run.stderr)
    return keys


# Манифест — его скрипт — как спросить у кода, какие ключи он печатает.
# Список ведётся руками: «этот манифест обязан сходиться со своим скриптом»
# — решение, а не свойство файловой системы (тот же принцип, что у
# REQUIRED_IN_BOTH в assert-twins.py). Новый action с `outputs:` — новая
# строка здесь.
#
# REPO_CLASS у profile-resolve — любой существующий класс: множество ключей
# от класса не зависит (resolve.py печатает строку на каждую стадию из
# `--implemented` и оба списка unimplemented при любом профиле).
PAIRS = (
    ("actions/pin-discover/action.yml", "actions/pin-tools/pins.py", discover_keys),
    ("actions/pin-apply/action.yml", "actions/pin-tools/pins.py", apply_keys),
    ("actions/profile-resolve/action.yml", "actions/profile-resolve/resolve.py",
     lambda manifest, _script: step_keys(
         manifest, "resolve", {"REPO_CLASS": "library", "SKIP": "", "EXTRA": ""})),
)


def check(manifest_rel: str, manifest_text: str, printed: set) -> list:
    problems = []
    declared = yaml.safe_load(manifest_text).get("outputs") or {}

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
        refs = REF_RE.findall(str((body or {}).get("value", "")))
        if refs and name not in refs:
            problems.append(
                f"{manifest_rel}: выход '{name}' берёт значение из {refs} — "
                f"ссылка на чужое имя раскрывается в пустую строку так же тихо"
            )
    return problems


# --- самопроверка: детектор обязан различать ------------------------------
_GOOD = ('name: t\noutputs:\n  alpha:\n    value: "${{ steps.run.outputs.alpha }}"\n'
         '  beta:\n    value: "${{ steps.run.outputs.beta }}"\n')
_MUTANTS = (
    ("ключ убран", 'name: t\noutputs:\n  alpha:\n    value: "${{ steps.run.outputs.alpha }}"\n'),
    ("ключ лишний", _GOOD + '  gamma:\n    value: "${{ steps.run.outputs.gamma }}"\n'),
    ("ссылка на чужое имя",
     'name: t\noutputs:\n  alpha:\n    value: "${{ steps.run.outputs.alpha }}"\n'
     '  beta:\n    value: "${{ steps.run.outputs.alpha }}"\n'),
)


def selftest() -> list:
    problems = []
    if check("фикстура", _GOOD, {"alpha", "beta"}):
        problems.append("самопроверка: детектор нашёл нарушение в ЗДОРОВОМ манифесте")
    for label, text in _MUTANTS:
        if not check("фикстура", text, {"alpha", "beta"}):
            problems.append(f"самопроверка: мутант «{label}» не пойман — детектор слеп")
    return problems


# Мутанты НАСТОЯЩИХ манифестов: (манифест, что сломано, regex, замена).
# Текстовые мутанты выше проверяют сравнение множеств и не видят деривацию:
# линт, который спрашивает не тот код или не тот манифест, прошёл бы их
# зелёным. Эти правят копию дерева и гоняют всю цепочку. Каждый — ровно
# дефект, мимо которого первая редакция прошла бы молча.
_REAL_MUTANTS = (
    ("actions/profile-resolve/action.yml", "выход стадии sca убран",
     r"(?m)^  sca:\n(?:    .*\n)+", ""),
    ("actions/profile-resolve/action.yml", "стадия в --implemented без выхода",
     r'(--implemented "[^"]+)"', r'\1,selftest-phantom"'),
    ("actions/pin-apply/action.yml", "выход moved убран",
     r"(?m)^  moved:\n(?:    .*\n)+", ""),
)
# Что читают деривации: манифесты и скрипты под actions/, профили —
# шаг profile-resolve (`${GITHUB_ACTION_PATH}/../../profiles`).
_TREE = ("actions", "profiles")


def audit(root: Path) -> list:
    problems = []
    for manifest_rel, script_rel, emitted in PAIRS:
        manifest, script = root / manifest_rel, root / script_rel
        if not manifest.is_file() or not script.is_file():
            # Пропавшая пара — «не смогли проверить», а не «чисто»: молчание
            # здесь неотличимо от здоровья, и это ровно тот класс, ради
            # которого детектор написан.
            raise SystemExit(f"::error::нет пары {manifest_rel} / {script_rel} — "
                             f"проверять нечем")
        problems += check(manifest_rel, manifest.read_text(encoding="utf-8"),
                          emitted(manifest, script))
    return problems


def selftest_real(root: Path) -> list:
    """Каждый мутант настоящего манифеста обязан добавить нарушение.

    Сравнение — с чистой копией, а не с нулём: если в самом дереве уже есть
    расхождение, о нём скажет основной проход, а здесь важно одно — видит ли
    детектор то, что внёс мутант. Мутация, не изменившая файл (regex
    промахнулся после правки манифеста), — тоже слепота: «не пойман» и «не
    было чего ловить» иначе неразличимы.
    """
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp)
        for sub in _TREE:
            shutil.copytree(root / sub, copy / sub,
                            ignore=shutil.ignore_patterns("__pycache__"))
        clean = set(audit(copy))
        for rel, label, pattern, repl in _REAL_MUTANTS:
            path = copy / rel
            original = path.read_text(encoding="utf-8")
            mutated, hits = re.subn(pattern, repl, original)
            if hits != 1:
                problems.append(f"самопроверка: мутация «{label}» промахнулась мимо "
                                f"{rel} (совпадений {hits}) — детектор не проверен")
                continue
            path.write_text(mutated, encoding="utf-8")
            try:
                caught = set(audit(copy)) - clean
            finally:
                path.write_text(original, encoding="utf-8")
            if not caught:
                problems.append(f"самопроверка: мутант «{label}» в {rel} не пойман "
                                f"— детектор слеп")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    # Проход и самопроверка — оба, всегда. Мутация промахивается и тогда,
    # когда её цель уже сломана в самом дереве (выход `sca` убран — убирать
    # нечего), и одно «детектор не проверен» без настоящего нарушения рядом
    # отправило бы читать не туда. Проход — первым: отказ деривации на
    # настоящем дереве должен назвать настоящий путь, а не путь копии.
    problems = audit(root)
    blind = selftest() + selftest_real(root)
    if problems or blind:
        for p in problems + blind:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: расхождений {len(problems)}, провалов самопроверки {len(blind)}",
              file=sys.stderr)
        return 1

    print(f"OK: {len(PAIRS)} манифест(ов) объявляют ровно то, что печатают их скрипты")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
