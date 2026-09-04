#!/usr/bin/env python3
"""У периодической джобы обязан быть признак живости, способный сработать (#19).

Провалившийся scheduled-workflow сам о себе не сообщает: расписание молчит
одинаково и когда всё хорошо, и когда джоба падает седьмую ночь подряд. В
этом портфеле лекарство одно — шаг `health-issue`, заводящий issue на отказе
и снимающий её на зелёном. Но лекарство работает, только если шаг ВЫПОЛНЯЕТСЯ
в тот прогон, ради которого написан.

Отсюда правило детектора, и в нём два условия, а не одно:

    у каждого workflow с триггером `schedule:` обязан быть хотя бы один шаг
    `health-issue` ПОД `if: always()`.

Второе условие не педантизм. Шаги job'а по умолчанию не выполняются после
отказа предыдущего, поэтому `health-issue` без `always()` докладывает только
об удачных ночах — то есть ровно о тех, о которых докладывать не надо.
Именно так у `nightly-bump` два из трёх сигналов (мажоры, пины без гарда)
не исполнялись ни разу за семь красных ночей: до них не доходило
управление. Третий, под `always()`, работал — им и виден отказ.

Почему линт, а не соглашение: сквозное правило, которое держится на памяти,
ломается на СЛЕДУЮЩЕМ добавленном расписании, и ломается молча — новая
джоба просто не заводит issue, а отличить это от «отказов не было» нельзя.

Грабля YAML 1.1: ключ `on:` разбирается как булево `True`, поэтому инвентарь
расписаний обязан смотреть и `d.get("on")`, и `d.get(True)`. Наивная версия
отвечает «периодических джоб не найдено» на репозитории, где они есть, —
и это неотличимо от чистого прогона.

Использование:  assert-schedule-liveness.py [корень репозитория]
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - среда без pyyaml
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

# Оба репо конвейера зовут один и тот же action, но по-разному: публичный
# локально (`./actions/health-issue`), приватный по тегу
# (`ignatenkofi/devsecops-pipeline-public/actions/health-issue@v1`).
# Матчим по подстроке пути, а не по полной строке.
HEALTH_ACTION = "actions/health-issue"


def triggers(doc: dict) -> dict:
    """`on:` под YAML 1.1 — булев ключ True. Смотреть обязательно оба."""
    raw = doc.get("on")
    if raw is None:
        raw = doc.get(True)
    return raw if isinstance(raw, dict) else {}


def check_workflow(rel: str, text: str) -> list:
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return [f"{rel}: не разбирается как YAML — {exc}"]
    if not isinstance(doc, dict) or "schedule" not in triggers(doc):
        return []

    seen_any = False
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if HEALTH_ACTION not in str((step or {}).get("uses", "")):
                continue
            seen_any = True
            if str((step or {}).get("if", "")).strip() == "always()":
                return []  # нашли годный — этого достаточно

    if seen_any:
        return [
            f"{rel}: у периодической джобы есть health-issue, но ни один "
            f"не под `if: always()` — сигнал доложит только об удачной ночи"
        ]
    return [
        f"{rel}: периодическая джоба без признака живости — провалившийся "
        f"scheduled-workflow сам о себе не сообщит (#19)"
    ]


# --- самопроверка: детектор обязан различать ------------------------------
_GOOD = """
on:
  schedule:
    - cron: "0 3 * * *"
jobs:
  bump:
    steps:
      - uses: ./actions/pin-discover
      - name: итог
        if: always()
        uses: ./actions/health-issue
"""
_MUTANTS = (
    ("сигнала нет вовсе", _GOOD.replace("        uses: ./actions/health-issue", "        run: echo")),
    ("сигнал не под always()", _GOOD.replace("        if: always()\n", "")),
)
# Не-периодический workflow с теми же шагами трогать нельзя: правило про
# расписание, а гард шире своего правила шумит на исправном репозитории.
_OUT_OF_SCOPE = _GOOD.replace("  schedule:\n    - cron: \"0 3 * * *\"", "  push:\n    branches: [main]")


def selftest() -> list:
    problems = []
    if check_workflow("фикстура", _GOOD):
        problems.append("самопроверка: детектор нашёл нарушение в ЗДОРОВОМ workflow")
    if check_workflow("фикстура", _OUT_OF_SCOPE):
        problems.append("самопроверка: детектор трогает workflow БЕЗ расписания")
    for label, text in _MUTANTS:
        if not check_workflow("фикстура", text):
            problems.append(f"самопроверка: мутант «{label}» не пойман — детектор слеп")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    blind = selftest()
    if blind:
        for p in blind:
            print(f"::error::{p}", file=sys.stderr)
        return 1

    files = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    if not files:
        # Ноль манифестов — «не смогли проверить», а не «чисто»: ровно так
        # выглядел бы промах путём, и вердикт был бы зелёным.
        print(f"::error::манифестов не найдено под {root}/.github/workflows",
              file=sys.stderr)
        return 1

    problems, scheduled = [], 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(root))
        try:
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            doc = {}
        if isinstance(doc, dict) and "schedule" in triggers(doc):
            scheduled += 1
        problems += check_workflow(rel, text)

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: нарушений {len(problems)}", file=sys.stderr)
        return 1

    # Число печатается намеренно: «0 периодических» — это тоже ответ, и
    # читатель должен увидеть его, а не только слово OK.
    print(f"OK: периодических workflow {scheduled} из {len(files)}, "
          f"у каждого health-issue под always()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
