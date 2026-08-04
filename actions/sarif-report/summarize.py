#!/usr/bin/env python3
"""Сводка SARIF-файлов стадии в таблицу job summary — и гард на их читаемость.

Логика жила инлайном в `action.yml` и молча деградировала в двух местах:

* нечитаемый SARIF попадал в таблицу как `?` (`python3 … 2>/dev/null ||
  echo "?"`), прогон оставался зелёным. То есть сломанный converter стадии
  выглядел как стадия без находок;
* каталог вообще без `*.sarif` печатал фантомную строку `| *.sarif | —
  (skip) |` — bash без `nullglob` отдаёт неразвернувшийся шаблон, а он не
  проходит `[ -s ]`. Стадия, упавшая ДО записи отчёта, была неотличима от
  стадии, выключенной профилем.

Второе опаснее: `sarif-report` вызывается с `if: always()`, ровно чтобы
поймать аварийный прогон, — и именно там показывал «skip».

Контракт теперь трёхзначный, а не двузначный:

* файл читается           → число находок;
* файл пустой (0 байт)    → `skip`. Это НЕ авария: стадия, неприменимая к
  репозиторию, пишет пустой SARIF намеренно (docs-lint без единого `.md`);
* файл не читается        → **выход 1**. Отчёт, который приёмник не может
  прочитать, — дефект стадии, а не «ноль находок».

Каталог без SARIF-файлов остаётся зелёным: action переиспользуют, и знать
за вызывающего, ждал ли он файлов, здесь нельзя. Но состояние называется
вслух (`::warning::` + строка в сводке), а не маскируется под skip.

Вход:  --sarif-dir DIR  каталог с отчётами стадий
       --out FILE       куда ДОПИСАТЬ markdown (обычно $GITHUB_STEP_SUMMARY);
                        без флага таблица уходит в stdout
Выход: workflow-команды (`::warning::` / `::error::`) — в stdout, чтобы их
       видел раннер. Поэтому таблица пишется в файл, а не в stdout: перенаправь
       весь stdout в summary — и команды уедут туда же вместо лога.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def count_results(path: Path) -> int:
    """Число находок во всех run'ах SARIF. Бросает — значит файл не отчёт."""
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"корень SARIF — {type(doc).__name__}, ожидался объект")
    runs = doc.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError(f"`runs` — {type(runs).__name__}, ожидался список")
    return sum(len(run.get("results", []) or []) for run in runs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sarif-dir", required=True)
    parser.add_argument("--out", help="файл для markdown; по умолчанию stdout")
    args = parser.parse_args()

    sarif_dir = Path(args.sarif_dir)
    files = sorted(sarif_dir.glob("*.sarif"))

    def emit(lines: list[str]) -> None:
        text = "\n".join(lines) + "\n"
        if args.out:
            with open(args.out, "a", encoding="utf-8") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)

    out = ["## DevSecOps pipeline — сводка стадий", ""]

    if not files:
        # Не skip и не ошибка: состояние «отчётов нет» называется прямо.
        out += [
            "SARIF-файлов в каталоге нет — **ни одна стадия не записала отчёт**.",
            "",
            f"Каталог: `{sarif_dir}`.",
        ]
        emit(out)
        print(f"::warning::sarif-report: в {sarif_dir} нет ни одного *.sarif")
        return 0

    out += ["| SARIF | находок |", "|---|---|"]
    broken: list[tuple[str, str]] = []

    for path in files:
        if path.stat().st_size == 0:
            out.append(f"| {path.name} | — (skip) |")
            continue
        try:
            out.append(f"| {path.name} | {count_results(path)} |")
        except Exception as exc:  # noqa: BLE001 — любая нечитаемость равносильна
            out.append(f"| {path.name} | **не читается** |")
            broken.append((path.name, f"{type(exc).__name__}: {exc}"))

    if broken:
        out += ["", "### Нечитаемые SARIF", ""]
        out += [f"- `{name}` — {why}" for name, why in broken]

    emit(out)

    if not broken:
        return 0

    for name, why in broken:
        print(f"::error::sarif-report: {name} не разбирается как SARIF — {why}")
    print(
        "sarif-report: отчёт стадии не читается. Это дефект самой стадии "
        "(converter оборвался или записал не SARIF), а не отсутствие находок — "
        "поэтому прогон падает, а не показывает ноль.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
