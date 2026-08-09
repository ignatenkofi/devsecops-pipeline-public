#!/usr/bin/env python3
"""Сколько файлов semgrep РЕАЛЬНО просканировал.

Повод — devsecops-pipeline#33. На Swift-репо (`atlas-native`) стадия
`sast-semgrep` была зелёной, просканировав ноль файлов: в offline-паке
объявлены только `python` и `javascript/typescript`, правил под Swift нет.
Semgrep честно печатает `Ran 9 rules on 0 files: 0 findings`, выходит с
нулём, SARIF пуст — и гейт сообщает «blocking-находок нет». Снаружи это
неотличимо от «код проверен и чист».

Почему отдельный файл, а не строка в `action.yml`: код внутри heredoc в
YAML нельзя запустить иначе как целым прогоном Actions, то есть на него
нельзя написать фикстуру. Ровно на этом в этом репозитории уже погорело
разрешение профиля (`skip-stages` молча глотал опечатку), поэтому
проверяемая логика живёт в файле, а `action.yml` её зовёт.

Почему `paths.scanned`, а не текст `Ran N rules on M files`: разбор
человекочитаемого вывода ломается на бампе версии молча — markdownlint
между 0.13 и 0.23 добавил токен в строку находки, и парсер стал давать
ноль, неотличимый от чистого прогона. `paths.scanned` — часть
машинного контракта `--json-output`.

Различаются ТРИ состояния, а не два:

  N > 0    стадия применима и отработала;
  N == 0   правил под язык репозитория нет — стадия ничего не проверила;
  ошибка   применимость неизвестна (нет отчёта, битый JSON, нет ключа).

Третье намеренно не сливается со вторым: «не смогли определить» — это не
«неприменимо», и рапортовать по нему что-либо о здоровье кода нельзя.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

USAGE = "usage: applicability.py <semgrep-json-report>"


def scanned_count(report: Path) -> int:
    """Число просканированных файлов из JSON-отчёта semgrep.

    Бросает, если отчёта нет либо в нём нет `paths.scanned`. Пустой
    список — валидный ответ «ноль», а вот ОТСУТСТВИЕ ключа ответом не
    является: на semgrep 1.172.0 ключ присутствует всегда (проверено и на
    применимом, и на неприменимом репозитории), поэтому его пропажа
    означает смену контракта, а не отсутствие находок.
    """
    data = json.loads(report.read_text(encoding="utf-8"))
    paths = data.get("paths")
    if not isinstance(paths, dict):
        raise KeyError("в отчёте semgrep нет объекта paths")
    if "scanned" not in paths:
        raise KeyError("в отчёте semgrep нет paths.scanned")
    scanned = paths["scanned"]
    if not isinstance(scanned, list):
        raise TypeError(f"paths.scanned не список, а {type(scanned).__name__}")
    return len(scanned)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        count = scanned_count(Path(argv[1]))
    except Exception as exc:  # noqa: BLE001 — наружу уходит код 2, причина в stderr
        print(f"applicability: применимость неизвестна: {exc}", file=sys.stderr)
        return 2
    print(count)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
