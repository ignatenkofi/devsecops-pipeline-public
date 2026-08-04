#!/usr/bin/env python3
"""Два детектора гигиены Actions-манифестов, каждый — из пойманной грабли.

Детектор A. `uses: ./…` внутри composite action.
    Локальный uses резолвится относительно рабочего каталога ПОТРЕБИТЕЛЯ,
    а не репозитория, где лежит action. В selftest, где потребитель и есть
    этот репозиторий, такой вызов проходит; у любого другого потребителя
    ломается. Из composite надо звать скрипт по `${GITHUB_ACTION_PATH}/…`.

Детектор B. Шаг, собирающий диагностику, без `if: always()`.
    Шаги composite action и шаги job'а по умолчанию не выполняются после
    отказа предыдущего. Пока приёмник не мог упасть, вопрос не стоял;
    как только выше появился гард, артефакт с логами перестал уезжать
    ровно в тех прогонах, где он нужен для разбора.

Оба детектора обязаны УМЕТЬ КРАСНЕТЬ, и это проверяется здесь же, а не
верой: перед проходом по репозиторию скрипт прогоняет себя на встроенных
заведомо плохих манифестах и падает, если не нашёл в них нарушений.
Детектор, который не доказал, что различает, — не проверка (03→04.08:
две наспех написанные версии этих же детекторов молчали, первая приняла
за нарушение переформулированный комментарий, вторая нашла `if: always()`
в комментарии к СОСЕДНЕМУ шагу и объявила чисто).

Использование:  assert-composite-hygiene.py [корень репозитория]
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - среда без pyyaml
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

# Шаги, которые существуют ради разбора упавшего прогона. Если такой шаг
# не первый — он обязан быть под always(), иначе исчезает вместе с тем
# отказом, который объясняет.
#
# `/actions/sarif-report` — не украшение списка: в обоих конвейерах
# диагностику собирает именно он, а `upload-artifact` и запись в
# `$GITHUB_STEP_SUMMARY` спрятаны ВНУТРЬ него. Детектор, знающий только
# два первых маркера, пропустил бы снятие `if: always()` с реального
# приёмника — то есть был бы слеп ровно к своему боевому случаю.
DIAGNOSTIC_MARKERS = (
    "actions/upload-artifact",
    "GITHUB_STEP_SUMMARY",
    "/actions/sarif-report",
)


def step_text(step: dict) -> str:
    """Текст шага БЕЗ его условия: `if` проверяется отдельно."""
    return "\n".join(
        str(v) for k, v in step.items() if k != "if" and v is not None
    )


def always_guarded(step: dict) -> bool:
    cond = str(step.get("if", ""))
    return "always()" in cond


def triggers(doc: dict) -> dict:
    """Секция `on` воркфлоу.

    YAML 1.1 разбирает голое `on:` как булев ключ True, поэтому одного
    `doc.get("on")` мало: у половины воркфлоу ключ окажется под `True`.
    """
    for key in ("on", True):
        value = doc.get(key)
        if isinstance(value, dict):
            return value
    return {}


def check_steps(where: str, steps: list, local_uses_forbidden: bool) -> list[str]:
    problems = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        body = step_text(step)

        if local_uses_forbidden:
            uses = str(step.get("uses", ""))
            if uses.startswith("./") or uses.startswith("../"):
                problems.append(
                    f"{where}: шаг #{i + 1} зовёт локальный `uses: {uses}` — "
                    f"резолвится относительно чекаута потребителя, не этого репо"
                )

        # Первый шаг не может быть пропущен из-за отказа предыдущего:
        # предыдущего нет. Требовать always() от него — шум.
        if i > 0 and any(m in body for m in DIAGNOSTIC_MARKERS):
            if not always_guarded(step):
                problems.append(
                    f"{where}: шаг #{i + 1} собирает диагностику, но без "
                    f"`if: always()` — исчезнет ровно на упавшем прогоне"
                )
    return problems


def check_manifest(path: Path, text: str) -> list[str]:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return []
    problems = []

    runs = doc.get("runs")
    if isinstance(runs, dict) and str(runs.get("using", "")) == "composite":
        steps = runs.get("steps") or []
        problems += check_steps(str(path), steps, local_uses_forbidden=True)

    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        # Локальный `./` запрещён не только в composite: у переиспользуемого
        # воркфлоу (`on: workflow_call`) он резолвится точно так же — против
        # чекаута ВЫЗЫВАЮЩЕГО. У обычного воркфлоу этого репо (push/PR)
        # `./` законен и постоянно используется, поэтому различаем по
        # триггеру, а не по факту «это workflow».
        reusable = "workflow_call" in triggers(doc)
        for name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if reusable:
                uses = str(job.get("uses", ""))
                if uses.startswith("./") or uses.startswith("../"):
                    problems.append(
                        f"{path} job:{name} зовёт локальный `uses: {uses}` из "
                        f"переиспользуемого воркфлоу — резолвится у вызывающего"
                    )
            steps = job.get("steps") or []
            problems += check_steps(f"{path} job:{name}", steps,
                                    local_uses_forbidden=reusable)

    return problems


# --------------------------------------------------------------------------
# Доказательство, что детекторы различают. Фикстуры заведомо плохие; если
# проход по ним вернул пусто — сломан детектор, а не репозиторий.
BAD_COMPOSITE_USES = """
name: bad
runs:
  using: composite
  steps:
    - shell: bash
      run: echo one
    - uses: ./actions/other
"""

BAD_DIAGNOSTIC_STEP = """
name: bad
runs:
  using: composite
  steps:
    - shell: bash
      # тут в комментарии написано `if: always()`, и это НЕ условие шага
      run: echo one
    - uses: actions/upload-artifact@v4
      with:
        name: logs
"""

GOOD_MANIFEST = """
name: good
runs:
  using: composite
  steps:
    - shell: bash
      run: echo one
    - uses: actions/upload-artifact@v4
      if: always()
      with:
        name: logs
"""

# Голое `on:` — YAML 1.1 отдаёт его булевым ключом True. Фикстура написана
# именно так специально: разбор триггеров обязан это переживать, иначе
# правило для переиспользуемых воркфлоу молча не применится ни разу.
BAD_REUSABLE_LOCAL_USES = """
name: bad-reusable
on:
  workflow_call:
jobs:
  stage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./actions/scanner
"""

# Приёмник отчётов — не upload-artifact и не запись в summary: и то и
# другое спрятано ВНУТРЬ composite. Снятие always() с этого шага детектор
# обязан видеть.
BAD_REPORT_STEP = """
name: bad-report
on:
  workflow_call:
jobs:
  stage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: owner/repo/actions/sarif-report@v1
"""

# Обычный воркфлоу репозитория (push/PR) — `./` в нём законен и
# используется постоянно. Ложное срабатывание здесь стоило бы дороже
# пропуска: детектор, ругающийся на исправный selftest, отключат.
GOOD_LOCAL_USES_IN_PLAIN_WORKFLOW = """
name: selftest-like
on:
  pull_request:
jobs:
  smoke:
    uses: ./.github/workflows/pipeline.yml
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./actions/scanner
"""


def self_test() -> None:
    cases = [
        ("локальный uses в composite", BAD_COMPOSITE_USES, "локальный `uses:"),
        ("диагностика без always()", BAD_DIAGNOSTIC_STEP, "без `if: always()`"),
        ("локальный uses в переиспользуемом воркфлоу",
         BAD_REUSABLE_LOCAL_USES, "локальный `uses:"),
        ("приёмник отчётов без always()", BAD_REPORT_STEP, "без `if: always()`"),
    ]
    for name, fixture, needle in cases:
        found = check_manifest(Path(f"<фикстура {name}>"), fixture)
        if not any(needle in p for p in found):
            sys.exit(
                f"САМОПРОВЕРКА ПРОВАЛЕНА: детектор «{name}» не увидел нарушения "
                f"в заведомо плохом манифесте. Нашёл: {found or 'ничего'}"
            )
    for name, fixture in (("composite", GOOD_MANIFEST),
                          ("обычный воркфлоу с ./", GOOD_LOCAL_USES_IN_PLAIN_WORKFLOW)):
        clean = check_manifest(Path(f"<фикстура good {name}>"), fixture)
        if clean:
            sys.exit(f"САМОПРОВЕРКА ПРОВАЛЕНА: детектор ругается на чистый "
                     f"манифест «{name}»: {clean}")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    self_test()

    targets = sorted(
        list(root.glob("actions/*/action.yml"))
        + list(root.glob("actions/*/action.yaml"))
        + list(root.glob(".github/workflows/*.yml"))
        + list(root.glob(".github/workflows/*.yaml"))
    )
    if not targets:
        # Пусто — это отказ, а не «нарушений нет»: скрипт, запущенный не из
        # того каталога, обязан сказать об этом, а не отчитаться зелёным.
        print(f"::error::манифестов не найдено под {root} — проверять нечего", file=sys.stderr)
        return 1

    problems = []
    for path in targets:
        rel = path.relative_to(root)
        try:
            problems += check_manifest(rel, path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{rel}: не разбирается как YAML — {exc}")

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: нарушений {len(problems)} в {len(targets)} манифестах", file=sys.stderr)
        return 1

    print(f"OK: {len(targets)} манифестов чисты (локальный uses в composite, "
          f"диагностика под always())")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
