#!/usr/bin/env python3
"""В репозитории нет закоммиченного байткода Python (#18).

Две копии `.pyc` пролежали в ПУБЛИЧНОМ репозитории конвейера незамеченными:
`actions/pin-tools/__pycache__/pins.cpython-311.pyc` и
`tests/lint/__pycache__/assert-composite-hygiene.cpython-311.pyc`. Попали они
туда одним и тем же способом — локальный импорт `pins.py` создаёт
`__pycache__/` рядом с исходником, а `.gitignore` публичного репо, в отличие
от приватного близнеца, правила про байткод не имел.

Почему это не косметика:

1. Байткод в дереве делает `git status` грязным после любого прогона, а
   грязное дерево — то состояние, в котором `git add -A` цепляет чужое.
   Ровно этот класс разбирался в портфеле отдельно (правило чистоты дерева).
2. Публичный репозиторий потребляется по тегу другими репозиториями. Артефакт
   сборки, лежащий рядом с исходником и не сверяемый ни с чем, — лишняя
   поверхность там, где её быть не должно.
3. Расхождение близнецов было МОЛЧАЛИВЫМ: правило существовало в приватном
   репо и отсутствовало в публичном, и заметить это можно было только
   наткнувшись работой. `assert-twins.py` сверяет `.py`/`.sh` под `actions/`
   и `tests/` — `.gitignore` и `.pyc` в его область не входят.

Гард смотрит на git-индекс, а не на файловую систему: `.gitignore` защищает
от нового `git add`, но уже отслеживаемый файл он не расследует, и именно
поэтому одного `.gitignore` мало.

Использование:  assert-no-bytecode.py [корень репозитория]
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

# `.pyo` — исторический, `.pyd` — расширение Windows; ловим класс, а не один
# суффикс, чтобы гард не оказался уже своего правила.
BYTECODE = re.compile(r"(^|/)__pycache__/|\.py[cod]$")


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def offenders(files: list[str]) -> list[str]:
    return sorted(f for f in files if BYTECODE.search(f))


def selftest() -> list[str]:
    """Гард обязан уметь провалиться: заводим репозиторий с байткодом."""
    problems = []
    if offenders(["actions/pin-tools/pins.py", "README.md", "tests/lint/x.sh"]):
        problems.append("самопроверка: гард ловит здоровое дерево")
    with tempfile.TemporaryDirectory(prefix="no-bytecode-") as tmp:
        root = Path(tmp)
        subprocess.run(["git", "-C", tmp, "init", "-q"], check=True)
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "m.cpython-311.pyc").write_bytes(b"\x00\x00")
        (root / "keep.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", tmp, "add", "-A", "-f"], check=True)
        found = offenders(tracked(root))
        if not found:
            problems.append(
                "самопроверка: КРАСНАЯ ФИКСТУРА не поймана — в дереве лежит "
                "__pycache__/m.cpython-311.pyc, а гард молчит")
        if "keep.py" in found:
            problems.append("самопроверка: гард считает исходник байткодом")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()

    blind = selftest()
    if blind:
        for p in blind:
            print(f"::error::{p}", file=sys.stderr)
        return 1

    bad = offenders(tracked(root))
    if bad:
        for f in bad:
            print(f"::error::закоммичен байткод: {f} — "
                  f"убрать `git rm --cached` и закрыть правилом в .gitignore",
                  file=sys.stderr)
        print(f"\nFAIL: файлов байткода в индексе {len(bad)}", file=sys.stderr)
        return 1

    print("OK: байткода в индексе нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
