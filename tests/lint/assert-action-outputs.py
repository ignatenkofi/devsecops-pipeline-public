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

Детектор обязан уметь краснеть, и это проверяется здесь же, а не верой:
перед проходом он прогоняет себя на трёх заведомо испорченных манифестах
(ключ убран, ключ лишний, ссылка на чужое имя) и падает, если хоть одного
нарушения не нашёл.

Использование:  assert-action-outputs.py [корень репозитория]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - среда без pyyaml
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

# Пары «манифест — как получить ключи, которые печатает его скрипт».
# Список ведётся руками: «этот манифест обязан сходиться со своим скриптом»
# — решение, а не свойство файловой системы (тот же принцип, что у
# REQUIRED_IN_BOTH в assert-twins.py).
PAIRS = (("actions/pin-discover/action.yml", "actions/pin-tools/pins.py"),)

REF_RE = re.compile(r"steps\.[A-Za-z0-9_-]+\.outputs\.([A-Za-z0-9_-]+)")


def emitted_keys(pins: Path) -> set:
    """Ключи, которые discover печатает на минимальной фикстуре.

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
        (root / "up.json").write_text(json.dumps({"a": "1.2.4"}), encoding="utf-8")
        run = subprocess.run(
            [sys.executable, str(pins), "discover", "--spec", "spec.json",
             "--upstream-from", "up.json"],
            cwd=root, capture_output=True, text=True,
        )
        if run.returncode != 0:
            raise SystemExit(
                f"::error::{pins}: discover не отработал на фикстуре — "
                f"множество выходов неизвестно, это не «нарушений нет».\n{run.stderr}"
            )
        return {line.split("=", 1)[0] for line in run.stdout.splitlines() if "=" in line}


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


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    blind = selftest()
    if blind:
        for p in blind:
            print(f"::error::{p}", file=sys.stderr)
        return 1

    problems, checked = [], 0
    for manifest_rel, pins_rel in PAIRS:
        manifest, pins = root / manifest_rel, root / pins_rel
        if not manifest.is_file() or not pins.is_file():
            # Пропавшая пара — «не смогли проверить», а не «чисто»: молчание
            # здесь неотличимо от здоровья, и это ровно тот класс, ради
            # которого детектор написан.
            print(f"::error::нет пары {manifest_rel} / {pins_rel} — проверять нечем",
                  file=sys.stderr)
            return 1
        problems += check(manifest_rel, manifest.read_text(encoding="utf-8"),
                          emitted_keys(pins))
        checked += 1

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: расхождений {len(problems)}", file=sys.stderr)
        return 1

    print(f"OK: {checked} манифест(ов) объявляют ровно то, что печатают их скрипты")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
