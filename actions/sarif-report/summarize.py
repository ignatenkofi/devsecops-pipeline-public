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

Контракт теперь четырёхзначный, а не двузначный:

* файл читается           → число находок;
* файл пустой (0 байт)    → `skip`. Это НЕ авария: стадия, неприменимая к
  репозиторию, пишет пустой SARIF намеренно (docs-lint без единого `.md`);
* инструмент сам сообщил о сбое (`runs[].invocations[].executionSuccessful:
  false`, поле обязательное в SARIF 2.1.0) → `**не выполнено**` с числом
  находок в отчёте, причина из его уведомлений и `::warning::`. Прежде такой
  отчёт давал строку с нулём — неотличимо от чистой проверки. Выход 0:
  падение на сбое инструмента — новое условие красного прогона, а это уже не
  фикс, а смена контракта (ADR 0006), решение за владельцем;
* файл не читается        → **выход 1**. Отчёт, который приёмник не может
  прочитать, — дефект стадии, а не «ноль находок».

Сбоем считается только явный `false`. Отчёт без `invocations` или без поля
судится как прежде, по числу находок; уведомления уровня `error` при
`executionSuccessful: true` прогон «невыполненным» не делают — успех
объявляет инструмент, приёмник его не перепроверяет.

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


def load_runs(path: Path) -> list:
    """run'ы SARIF-файла. Бросает — значит файл не отчёт."""
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"корень SARIF — {type(doc).__name__}, ожидался объект")
    runs = doc.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError(f"`runs` — {type(runs).__name__}, ожидался список")
    return runs


def count_results(runs: list) -> int:
    """Число находок во всех run'ах. Бросает — значит файл не отчёт."""
    return sum(len(run.get("results", []) or []) for run in runs)


def _field(obj: object, *keys: str) -> object:
    """obj[k1][k2]… или None, если по дороге не объект: необязательные поля
    отчёта не превращают читаемый файл в нечитаемый."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _reason(invocation: dict) -> str:
    """Причина сбоя одной строкой — из уведомлений самого инструмента."""
    notes = [
        note
        for key in ("toolExecutionNotifications", "toolConfigurationNotifications")
        if isinstance(invocation.get(key), list)
        for note in invocation[key]
        if isinstance(note, dict)
    ]
    texts = []
    for note in [n for n in notes if n.get("level") == "error"] or notes:
        text = _field(note, "message", "text")
        if isinstance(text, str) and text.strip():
            texts.append(" ".join(text.split()))
    if texts:
        why = "; ".join(texts[:3])
    elif isinstance(invocation.get("exitCode"), int):
        why = f"код выхода {invocation['exitCode']}, без пояснений"
    else:
        why = "причину инструмент не назвал"
    return why if len(why) <= 300 else why[:299] + "…"


def tool_failures(runs: list) -> list[str]:
    """Сбои, о которых инструмент сообщил сам: `executionSuccessful: false`."""
    failures = []
    for run in runs:
        invocations = _field(run, "invocations")
        if not isinstance(invocations, list):
            continue
        tool = _field(run, "tool", "driver", "name")
        tool = tool if isinstance(tool, str) and tool else "инструмент"
        failures += [
            f"{tool}: {_reason(inv)}"
            for inv in invocations
            if isinstance(inv, dict) and inv.get("executionSuccessful") is False
        ]
    return failures


def _command_data(text: str) -> str:
    """Экранирование данных workflow-команды: `%`, CR и LF."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


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
    failed: list[tuple[str, str]] = []

    for path in files:
        if path.stat().st_size == 0:
            out.append(f"| {path.name} | — (skip) |")
            continue
        try:
            runs = load_runs(path)
            found = count_results(runs)
            failures = tool_failures(runs)
        except Exception as exc:  # noqa: BLE001 — любая нечитаемость равносильна
            out.append(f"| {path.name} | **не читается** |")
            broken.append((path.name, f"{type(exc).__name__}: {exc}"))
            continue
        if failures:
            out.append(f"| {path.name} | **не выполнено** (в отчёте {found}) |")
            failed += [(path.name, why) for why in failures]
        else:
            out.append(f"| {path.name} | {found} |")

    if failed:
        out += ["", "### Инструмент сообщил о сбое", ""]
        out += [
            "Число в строке — что успело попасть в отчёт; ноль здесь значит "
            "«не проверено», а не «чисто».",
            "",
        ]
        out += [f"- `{name}` — {why}" for name, why in failed]

    if broken:
        out += ["", "### Нечитаемые SARIF", ""]
        out += [f"- `{name}` — {why}" for name, why in broken]

    emit(out)

    for name, why in failed:
        print(
            "::warning::sarif-report: "
            + _command_data(f"{name} — инструмент не выполнился ({why})")
        )

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
