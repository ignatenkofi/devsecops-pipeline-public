#!/usr/bin/env python3
"""Общая механика ночного бампа пинов инструментов (#20, ADR 0006).

ЗАЧЕМ ОДНА КОПИЯ. Приватный и публичный конвейеры оба пинят версии
инструментов внутри composite actions и оба нуждаются в ночном обновлении.
Разложить одинаковую логику по двум репозиториям значило бы завести третью
расходящуюся копию — этот портфель за такое уже платил (#32: гард построили
в публичном и не перенесли в приватный, месяц качали бинари без сверки).
Поэтому механика живёт здесь, а вызывается по полному пути `@v1` — тем же
способом, каким поехал `health-issue`.

ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ. Здесь: снять текущий пин, спросить апстрим,
отличить мажор от минора, применить. Здесь НЕТ валидации кандидата — она
инструмент-специфична (негатив-фикстуры, возня с sha256 у osv-scanner,
чей апстрим не публикует файл сумм) и остаётся у каждого репозитория своя.
Обобщать её значило бы получить универсальный интерфейс к трём разным
задачам — ровно тот рефакторинг, который ломает работающее.

ЯКОРЬ — ИМЯ ВХОДА, не «первый default в файле». Формы пинов разные:
`actions/docs-lint/action.yml` держит ДВА пина (markdownlint-version и
lychee-version), у `semgrep` рядом лежит нечисловой `severity-floor`, у
`osv-scanner` — `sha256`. Регекс по первому `default:` дал бы тихо неверный
пин в трёх файлах из семи.

Использование:
    pins.py discover --spec SPEC.json [--upstream-from FILE] [--github-token T]
    pins.py apply    --spec SPEC.json --targets TARGETS.json

Формат спеки (массив):
    [{"name": "markdownlint",
      "file": "actions/docs-lint/action.yml",
      "input": "markdownlint-version",
      "source": "npm",           # github | pypi | npm
      "id": "markdownlint-cli2"}]

`--upstream-from` — файл {"имя": "тег"} вместо сетевых запросов. Существует
ради тестов: сеть в тестах запрещена, а гард, который нельзя прогнать
офлайн, не прогоняют вовсе. Боевые прогоны флаг не передают.

Значение здесь — ТЕГ в том виде, в каком его вернул бы апстрим
(`lychee-v0.24.2`, `v1.2.3`, `0.73.0`), а не готовая версия: фикстура обязана
входить в тот же разбор, что и боевой путь. Пока она подавала уже
нормализованные версии, дефект #32 был для неё невидим по построению.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["pin_regex", "read_pin", "write_pin", "classify", "as_tuple"]


def pin_regex(input_name: str) -> re.Pattern:
    """Регекс на `default:` КОНКРЕТНОГО входа action.yml.

    Между именем входа и его `default:` могут стоять `description:` и
    `required:`, но НЕ может начаться следующий вход. Защит от «уехать к
    соседу» здесь ДВЕ, и они независимы:

      * отступ `[^\\S\\n]{4,}` — тело входа, имя входа стоит на двух пробелах;
      * ленивый повтор `*?` — совпадение встаёт на ПЕРВОМ `default:`.

    Проверено мутациями: снятие любой ОДНОЙ ничего не ломает — вторая держит.
    Снятие обеих сразу читает `severity-floor: "ERROR"` как версию, и гард
    `tests/negative/assert-pins.sh` краснеет шестью ассертами. Формулировка
    «защищает ленивость» была бы неверной, поэтому написано как измерено.
    """
    return re.compile(
        r"(?P<head>^[^\S\n]{2}" + re.escape(input_name) + r":[^\S\n]*\n"
        r"(?:[^\S\n]{4,}\S[^\n]*\n)*?"
        r"[^\S\n]{4,}default:[^\S\n]*\")(?P<ver>[^\"]*)(?P<tail>\")",
        re.MULTILINE,
    )


def read_pin(path: Path, input_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    matches = list(pin_regex(input_name).finditer(text))
    if len(matches) != 1:
        raise SystemExit(
            f"::error::{path}: ожидал ровно один пин у входа '{input_name}', "
            f"нашёл {len(matches)}. Молча взять первый нельзя: в этом "
            f"репозитории есть файлы с несколькими пинами."
        )
    return matches[0].group("ver")


def write_pin(path: Path, input_name: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, n = pin_regex(input_name).subn(
        lambda m: m.group("head") + new + m.group("tail"), text
    )
    if n != 1:
        raise SystemExit(
            f"::error::{path}: при записи пина '{input_name}' совпадений {n}, ожидал 1"
        )
    path.write_text(updated, encoding="utf-8")


def as_tuple(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version))


# Апстрим отдаёт ТЕГ, а не версию, и форма тега — свойство чужого репозитория.
# `lychee` тегает `lychee-v0.24.2`, кто-то — `v1.2.3`, кто-то `syft/v1.51.1`,
# кто-то голое `0.73.0`. Прежняя нормализация была `tag_name.lstrip("v")` и
# снимала только ведущую `v`: для `lychee-v0.24.2` она не делала НИЧЕГО, тег
# доезжал до `classify` целиком, и сравнение мажоров `cur.split(".")[0] !=
# new.split(".")[0]` читалось как `"0" != "lychee-v0"` — вечный фантомный
# мажор. Пин 0.24.2 не двигался с тех пор ни разу, а ночь послушно писала
# `MAJORS: lychee 0.24.2 -> lychee-v0.24.2` и звала человека разобрать
# changelog обновления, которого нет.
#
# Отсюда два правила, и второе важнее первого.
#
# 1. Нормализация — ОДНА функция, и через неё проходит версия из ЛЮБОГО
#    источника, включая `--upstream-from`. Вторая деривация того же пути —
#    способ завести вторую правду: пока фикстура подавала уже готовые
#    версии, она проверяла `classify` на входе, которого боевой путь не
#    видит, и дефект был для неё невидим по построению.
# 2. Тег, из которого версия не извлекается, — ОТКАЗ, а не «обновлений нет».
#    Тихо оставить пин значило бы объявить ночь успешной, ничего не
#    проверив, — ровно то, против чего написан обработчик недоступного
#    апстрима ниже.
#
# Требование «хотя бы одна точка» намеренное: оно отделяет версию от
# датированных тегов вида `release-2024-01-05`, у которых наше сравнение
# мажоров смысла не имеет. Такой тег честнее отвергнуть, чем разобрать
# наугад.
_TAG_RE = re.compile(r"^(?:.*[-_/])?v?(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?)$")


def normalize_version(raw: str, name: str = "") -> str:
    """Тег апстрима -> голая версия. Не смог — падаем, а не угадываем."""
    match = _TAG_RE.match(raw.strip())
    if not match:
        who = f"{name}: " if name else ""
        raise SystemExit(
            f"::error::{who}из тега {raw!r} не извлекается версия. "
            f"Это не «обновлений нет» — это несостоявшаяся проверка."
        )
    return match.group(1)


def _get_json(url: str, token: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": "devsecops-pipeline-pins"}
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def latest_upstream(entry: dict, token: str = "") -> str:
    source, ident = entry["source"], entry["id"]
    if source == "github":
        # Нормализация НЕ здесь: она общая для всех источников и живёт в
        # cmd_discover, иначе у `--upstream-from` был бы свой разбор версии.
        return _get_json(
            f"https://api.github.com/repos/{ident}/releases/latest", token
        )["tag_name"]
    if source == "pypi":
        return _get_json(f"https://pypi.org/pypi/{ident}/json")["info"]["version"]
    if source == "npm":
        return _get_json(f"https://registry.npmjs.org/{ident}")["dist-tags"]["latest"]
    raise SystemExit(f"::error::неизвестный источник версий: {source!r}")


def classify(cur: str, new: str, name: str, autobump: bool = True) -> dict:
    """Разложить пару (текущий, апстрим) в решение о том, что применять.

    Монотонность проверяется отдельно и ПЕРВОЙ: `releases/latest` у GitHub —
    самый свежий по ДАТЕ, а не по номеру, поэтому бэкпорт в старую ветку
    делает `new` НИЖЕ действующего пина. Без этой проверки такой релиз
    прошёл бы как обычный минорный бамп и откатил пин молча.
    """
    backport = False
    if as_tuple(new) < as_tuple(cur):
        backport = True
        new = cur
    is_major = cur.split(".")[0] != new.split(".")[0]
    # Мажор остаётся на текущем пине: он меняет поведение инструмента, а
    # иногда и CLI, на который стоят наши actions. Разбирает человек.
    target = cur if (is_major or cur == new) else new
    # autobump=false — инструмент, у которого НЕТ негатив-фикстуры. Дрейф по
    # нему сообщается (иначе он тихо стареет — ровно то, с чего началась
    # #20), но пин не двигается: правило этого репозитория — «гард до
    # автобампа, а не после», и бамп без проверки «инструмент всё ещё
    # находит эталонную находку» уже приводил к молча ослепшей стадии.
    if not autobump:
        target = cur
    return {
        "name": name, "cur": cur, "new": new, "target": target,
        "is_major": is_major, "backport": backport, "autobump": autobump,
        "moves": target != cur,
    }


def cmd_discover(args) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    fake = {}
    if args.upstream_from:
        fake = json.loads(Path(args.upstream_from).read_text(encoding="utf-8"))

    result, minors, majors, backports, manual = {}, [], [], [], []
    for entry in spec:
        name = entry["name"]
        cur = read_pin(Path(entry["file"]), entry["input"])
        if fake:
            if name not in fake:
                raise SystemExit(f"::error::в --upstream-from нет записи для {name!r}")
            new = fake[name]
        else:
            try:
                new = latest_upstream(entry, args.github_token)
            except (urllib.error.URLError, KeyError, ValueError) as exc:
                # Недоступный апстрим — это отказ, а не «новых версий нет».
                # Молча оставить пин значило бы объявить ночь успешной, ничего
                # не проверив: самый частый способ для проверки выглядеть
                # зелёной.
                raise SystemExit(
                    f"::error::{name}: апстрим не опрошен ({exc}). "
                    f"Это не «обновлений нет» — это несостоявшаяся проверка."
                ) from exc
        # Одна точка нормализации на оба источника (#32). Стоять она обязана
        # ЗДЕСЬ, после развилки: если нормализовать только в latest_upstream,
        # офлайн-фикстура пойдёт мимо неё и перестанет видеть этот класс.
        new = normalize_version(new, name)
        info = classify(cur, new, name, entry.get("autobump", True))
        result[name] = info
        if info["backport"]:
            backports.append(f"{name}: latest ниже пина {cur} — пин не трогаем")
        elif not info["autobump"] and cur != new:
            # Дрейф есть, но применять его некому доверить: нет гарда.
            manual.append(f"{name} {cur} -> {new}")
        elif info["is_major"] and cur != new:
            majors.append(f"{name} {cur} -> {new}")
        elif info["moves"]:
            minors.append(f"{name} {cur} -> {info['target']}")

    for line in backports:
        print(f"::warning::{line}", file=sys.stderr)

    out = {
        "result": json.dumps(result, ensure_ascii=False, sort_keys=True),
        "targets": json.dumps(
            {k: v["target"] for k, v in result.items()}, ensure_ascii=False, sort_keys=True
        ),
        "needs-bump": "true" if minors else "false",
        "has-major": "true" if majors else "false",
        "minors": "; ".join(minors),
        "majors": "; ".join(majors),
        "has-manual": "true" if manual else "false",
        "manual": "; ".join(manual),
    }
    for key, value in out.items():
        print(f"{key}={value}")
    return 0


def cmd_apply(args) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    moved = 0
    for entry in spec:
        name = entry["name"]
        if name not in targets:
            raise SystemExit(f"::error::в targets нет записи для {name!r}")
        path = Path(entry["file"])
        cur = read_pin(path, entry["input"])
        if cur == targets[name]:
            continue
        write_pin(path, entry["input"], targets[name])
        print(f"{path}:{entry['input']} {cur} -> {targets[name]}", file=sys.stderr)
        moved += 1
    print(f"moved={moved}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="механика бампа пинов инструментов")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="снять пины и опросить апстрим")
    d.add_argument("--spec", required=True)
    d.add_argument("--upstream-from", default="",
                   help="файл {имя: версия} вместо сети (для тестов)")
    d.add_argument("--github-token", default="")
    d.set_defaults(func=cmd_discover)

    a = sub.add_parser("apply", help="записать целевые версии в пины")
    a.add_argument("--spec", required=True)
    a.add_argument("--targets", required=True)
    a.set_defaults(func=cmd_apply)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
