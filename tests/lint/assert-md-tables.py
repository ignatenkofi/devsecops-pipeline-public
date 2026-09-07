#!/usr/bin/env python3
"""Шапка GFM-таблицы и её разделитель имеют одинаковое число ячеек (#18).

ЗАЧЕМ. 2026-09-06 в `ARCHITECTURE.md` этого репозитория таблица стадий не
была таблицей: шапка несла шесть ячеек, разделитель — семь.

    | # | Стадия | Инструмент | Раннер | Триггер | Выход |
    |---|---|---|---|---|---|---|

По GFM разделитель обязан совпадать с шапкой по числу ячеек, иначе блок
таблицей не признаётся вовсе. Проверено исполнением настоящего рендерера
(`cmarkgfm.github_flavored_markdown_to_html`): десять строк выходили одним
абзацем с палками, `<table>` в выводе не было. В браузере это видно сразу, в
диффе — нет: строка отличается на три символа и выглядит нормальной.

ПОЧЕМУ ЛИНТ, А НЕ ВНИМАТЕЛЬНОСТЬ. Дефект приехал ПРАВКОЙ, которая чинила
соседнюю таблицу в этом же файле, и смена записала регрессию как пойманную —
починив не ту таблицу. То есть глазами класс уже не поймали один раз, а
запись об этом была ложной. Считать ячейки должен исполнитель.

ПОЧЕМУ НЕ MD056. Правило markdownlint сравнивает число ячеек СТРОК ТЕЛА с
шапкой, а разошлась здесь пара «шапка — разделитель»: тело было ровным, и
MD056 промолчал бы. Проверка ниже сравнивает именно эту пару.

Рендерер не нужен и не используется намеренно: сравнение числа ячеек — это
чистый разбор текста, а зависимость в CI ради него означала бы сеть в шаге,
который обязан работать всегда.

Границы. Блоки в ограждении (``` и ~~~) пропускаются: там markdown — пример,
а не разметка, и «сломанная» таблица внутри примера законна.

Использование:  assert-md-tables.py [корень репозитория]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# Ячейка разделителя: --- , :--- , ---: , :---:
DELIM_CELL_RE = re.compile(r"^:?-+:?$")


def cells(line: str) -> list[str]:
    """Ячейки строки таблицы. `\\|` — экранированная палка, не разделитель."""
    parts, buf, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf += "|"
            i += 2
            continue
        if ch == "|":
            parts.append(buf)
            buf = ""
            i += 1
            continue
        buf += ch
        i += 1
    parts.append(buf)
    # Ведущая и замыкающая палки дают пустые края — они не ячейки.
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return parts


def is_delimiter(line: str) -> bool:
    c = cells(line)
    return bool(c) and all(DELIM_CELL_RE.match(x.strip()) for x in c)


def check_text(rel: str, text: str) -> list[str]:
    problems: list[str] = []
    lines = text.splitlines()
    fence: str | None = None
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m:
            token = m.group(1)[0]
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
            continue
        if fence is not None:
            continue
        if "|" not in line or i + 1 >= len(lines):
            continue
        nxt = lines[i + 1]
        if "|" not in nxt or not is_delimiter(nxt) or is_delimiter(line):
            continue
        head, delim = len(cells(line)), len(cells(nxt))
        if head != delim:
            problems.append(
                f"{rel}:{i + 2}: разделитель таблицы несёт {delim} ячеек, "
                f"шапка ({rel}:{i + 1}) — {head}. GFM такой блок таблицей "
                f"не признаёт: он отрендерится абзацем с палками")
    return problems


_GOOD = "| a | b |\n|---|---|\n| 1 | 2 |\n"
_BAD = "| a | b |\n|---|---|---|\n| 1 | 2 |\n"
_ALIGNED = "| a | b |\n|:--|--:|\n| 1 | 2 |\n"
_ESCAPED = "| a \\| b | c |\n|---|---|\n| 1 | 2 |\n"
_IN_FENCE = "```\n| a | b |\n|---|---|---|\n```\n"
_NOT_A_TABLE = "просто строка с | палкой\nи вторая\n"


def selftest() -> list[str]:
    problems = []
    if check_text("ф", _GOOD):
        problems.append("самопроверка: находка в ЗДОРОВОЙ таблице")
    if not check_text("ф", _BAD):
        problems.append(
            "самопроверка: КРАСНАЯ ФИКСТУРА не поймана — шапка 2 ячейки, "
            "разделитель 3, а линт молчит")
    if check_text("ф", _ALIGNED):
        problems.append("самопроверка: разделитель с выравниванием принят за поломку")
    if check_text("ф", _ESCAPED):
        problems.append("самопроверка: экранированная палка сосчитана как ячейка")
    if check_text("ф", _IN_FENCE):
        problems.append("самопроверка: таблица внутри ограждения не пропущена")
    if check_text("ф", _NOT_A_TABLE):
        problems.append("самопроверка: не-таблица принята за таблицу")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    blind = selftest()
    if blind:
        for p in blind:
            print(f"::error::{p}", file=sys.stderr)
        return 1

    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "*.md"],
                         capture_output=True, text=True, check=True).stdout
    files = [f for f in out.split("\0") if f]
    if not files:
        # Ноль файлов — «проверить не смогли», а не «чисто».
        print(f"::error::под {root} не найдено ни одного .md в индексе",
              file=sys.stderr)
        return 1

    problems: list[str] = []
    for rel in files:
        problems += check_text(rel, (root / rel).read_text(encoding="utf-8"))

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: разошедшихся таблиц {len(problems)}", file=sys.stderr)
        return 1

    print(f"OK: {len(files)} файлов .md, шапки и разделители таблиц сходятся")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
