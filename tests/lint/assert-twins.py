#!/usr/bin/env python3
"""Детектор расхождения близнецов: приватный и публичный репо конвейера.

Из грабли ночи 03→04.08: гард на загрузку со сверкой sha256 построили в
публичном репо и не перенесли в приватный. Обе половины по отдельности
выглядели нормально, а портфель месяц качал и исполнял четыре бинаря без
проверки.

Первая версия этого детектора сравнивала одноимённые файлы и объявила две
находки, обе пустые (переформулированный комментарий; сознательное
подмножество стадий), а НАСТОЯЩЕЕ расхождение не увидела вовсе:
отсутствующий файл не «отличается». Отсюда два правила вместо одного.

    Правило 1. Общий скрипт обязан совпадать байт-в-байт.
        Только .py/.sh под actions/ и tests/ — это разделяемая логика.
        Манифесты (.yml) сознательно разные: репо реализуют разные наборы
        стадий, и требовать от них совпадения значило бы получать шум
        вместо находок. Контроль из правила 2 сравнивается так же, где бы
        он ни лежал (.github/scripts/): «обязан быть в обоих» без «обязан
        совпадать» ловил бы только пропажу файла, не его дрейф.

    Правило 2. Контроль обязан присутствовать в ОБОИХ.
        Список явный: отсутствие файла правилом 1 не ловится в принципе,
        а именно так расхождение и пряталось.

Оба правила проверяются на встроенных фикстурах перед проходом по репо —
детектор, не доказавший, что различает, не проверка.

Использование:  assert-twins.py <корень этого репо> <корень репо-близнеца>
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SHARED_ROOTS = ("actions", "tests")
SHARED_SUFFIXES = (".py", ".sh")

# Контроли, которые обязаны быть в обоих репо. Список ведётся руками
# сознательно: «обязателен» — это решение, а не свойство файловой системы.
REQUIRED_IN_BOTH = (
    "actions/fetch-verified/fetch_verified.sh",
    "tests/negative/assert-fetch-verified.sh",
    "tests/negative/assert-fetch-verified-wrapper.py",
    "tests/lint/assert-composite-hygiene.py",
    "tests/lint/assert-twins.py",
    # Разрешение профиля: skip-stages молча глотал опечатки, и узнать об
    # этом было неоткуда — скрипт жил в heredoc внутри action.yml.
    # Вынесен в файл и обязан быть в обоих: у обоих репо один и тот же
    # разбор входов, различаются только наборы стадий (--implemented).
    "actions/profile-resolve/resolve.py",
    "tests/negative/assert-profile-resolve.sh",
    # Вердикт Gate (#19). Обязателен в обоих: шаг Gate есть у каждой
    # половины конвейера, а класс дефекта — «не проверялось звучит как
    # проверено» — от репозитория не зависит. Фикстура извлекает тело шага
    # из воркфлоу ТОГО репо, где запущена, поэтому файл общий, а проверяет
    # он разное.
    "tests/negative/assert-gate-verdict.sh",
    # Проверка подписи файла сумм (cosign, #24). Обязательна в обоих с
    # 2026-08-09: боевая стадия, которая пойдёт через cosign (syft-sbom),
    # живёт в ПРИВАТНОМ репозитории и с переезда портфеля исполняется на
    # ферме раннеров. Пока фикстура была только здесь, она доказывала
    # достижимость sigstore с hosted-раннера GitHub — то есть не с той
    # поверхности, на которой работает прод.
    "tests/negative/assert-cosign-sums.sh",
    # Признак живости периодической джобы (#19). Обязателен в обоих: своё
    # расписание есть у каждого репо, и правило «сигнал под always()» без
    # линта держалось бы на памяти — то есть ломалось бы на следующей
    # добавленной джобе и ломалось молча.
    "tests/lint/assert-schedule-liveness.py",
    # Fork-guard на self-hosted job с pull_request-триггером (ADR 0005 п. 2,
    # #9). Обязателен в обоих: правило про ферму, а на ферму ходят оба —
    # приватный своими джобами, публичный через `inputs.runs-on` фасада;
    # второй эшелон, который держится на памяти, ломается молча.
    "tests/lint/assert-fork-guard.py",
    # Байткод в индексе (#18). Обязателен в обоих именно потому, что
    # расхождение было ОДНОСТОРОННИМ: правило `__pycache__/` жило в
    # .gitignore приватного репо, в публичном его не было, и две копии
    # `.pyc` пролежали там закоммиченными. Односторонняя гигиена — это и
    # есть тот молчаливый дрейф близнецов, ради которого написан этот файл.
    "tests/lint/assert-no-bytecode.py",
    # Шапка и разделитель GFM-таблицы (#18). Обязателен в обоих: доки есть у
    # каждого, а класс уже приезжал правкой, которая чинила соседнюю таблицу
    # в том же файле, и был записан как пойманный, будучи непойманным.
    "tests/lint/assert-md-tables.py",
    # Шаг «Ветка bump/tools + PR» ночного бампа (#44). Скрипт живёт вне
    # actions/ и tests/, поэтому правило 1 само его не видит — он и его гард
    # названы здесь явно: приватный ходил через скрипт с 1.5.0, публичный
    # звал gh напрямую, и паритет этого не стерёг никто.
    ".github/scripts/pr-upsert.sh",
    "tests/negative/assert-pr-upsert.sh",
)


def shared_files(root: Path) -> set[str]:
    found = set()
    for sub in SHARED_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in SHARED_SUFFIXES:
                found.add(str(path.relative_to(root)))
    return found


def compare(here: Path, twin: Path, required: tuple[str, ...] = REQUIRED_IN_BOTH) -> list[str]:
    problems = []

    mine, theirs = shared_files(here), shared_files(twin)
    # Обязательный контроль сравнивается байт-в-байт и там, где он лежит
    # вне actions/ и tests/: правило 2 ловит только пропажу файла.
    present = {rel for rel in required if (here / rel).is_file() and (twin / rel).is_file()}
    for rel in sorted((mine & theirs) | present):
        a = (here / rel).read_bytes()
        b = (twin / rel).read_bytes()
        if a != b:
            problems.append(
                f"{rel}: общий скрипт разошёлся с близнецом "
                f"({len(a)} и {len(b)} байт)"
            )

    for rel in required:
        missing = [
            name for name, root in (("здесь", here), ("у близнеца", twin))
            if not (root / rel).is_file()
        ]
        if missing:
            problems.append(
                f"{rel}: контроль обязан быть в обоих репо, нет {' и '.join(missing)}"
            )

    return problems


def self_test() -> None:
    # Синтетический контроль вне actions/ и tests/: правило 1 обязано
    # доходить и до него. Путь не берётся из REQUIRED_IN_BOTH намеренно —
    # самопроверка не должна зависеть от того, что сейчас в списке.
    outside = ".github/scripts/synthetic-control.sh"
    required = REQUIRED_IN_BOTH + (outside,)
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        for root in (a, b):
            for rel in required:
                # Каталоги выводятся из списка, а не перечисляются рядом с ним:
                # пока они были отдельным списком, добавление записи в
                # REQUIRED_IN_BOTH роняло САМОПРОВЕРКУ по FileNotFoundError —
                # то есть детектор ломался ровно при попытке его расширить.
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("общий текст\n", encoding="utf-8")

        if compare(a, b, required):
            raise SystemExit(
                f"САМОПРОВЕРКА ПРОВАЛЕНА: детектор ругается на одинаковые деревья: "
                f"{compare(a, b, required)}"
            )

        # (1) разошедшийся общий скрипт
        (b / "actions/fetch-verified/fetch_verified.sh").write_text(
            "другой текст\n", encoding="utf-8")
        if not any("разошёлся" in p for p in compare(a, b, required)):
            raise SystemExit("САМОПРОВЕРКА ПРОВАЛЕНА: расхождение файла не поймано")
        (b / "actions/fetch-verified/fetch_verified.sh").write_text(
            "общий текст\n", encoding="utf-8")

        # (2) контроль есть только с одной стороны — то, что и пряталось
        (b / "actions/fetch-verified/fetch_verified.sh").unlink()
        if not any("обязан быть в обоих" in p for p in compare(a, b, required)):
            raise SystemExit("САМОПРОВЕРКА ПРОВАЛЕНА: отсутствующий контроль не пойман")

        # (3) обязательный контроль ВНЕ actions/ и tests/ разошёлся — правило 1
        # обязано дотянуться и туда, иначе .github/scripts/ дрейфует молча
        (b / outside).write_text("другой текст\n", encoding="utf-8")
        if not any(f"{outside}: общий скрипт разошёлся" in p for p in compare(a, b, required)):
            raise SystemExit(
                "САМОПРОВЕРКА ПРОВАЛЕНА: расхождение обязательного контроля вне actions/tests не поймано")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    here, twin = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    for root in (here, twin):
        if not root.is_dir():
            print(f"::error::нет каталога {root}", file=sys.stderr)
            return 2

    self_test()

    mine = shared_files(here)
    if not mine:
        # Пусто — отказ, а не «расхождений нет».
        print(f"::error::под {here} не найдено ни одного общего скрипта", file=sys.stderr)
        return 1

    problems = compare(here, twin)
    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: расхождений {len(problems)}", file=sys.stderr)
        return 1

    common = len(mine & shared_files(twin))
    print(f"OK: близнецы сходятся — {common} общих скриптов байт-в-байт, "
          f"{len(REQUIRED_IN_BOTH)} обязательных контролей на месте")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
