#!/usr/bin/env python3
"""Разрешить профиль класса в режимы стадий: B (blocking) / A (advisory) / off.

Вынесен из heredoc в action.yml сознательно. Пока скрипт жил внутри YAML, его
нельзя было запустить иначе как целым прогоном GitHub Actions — то есть
написать на него гард было нечем, и дефект `skip-stages` (ниже) прожил
незамеченным ровно поэтому.

ОБЩИЙ ФАЙЛ ДВУХ РЕПО. Приватный и публичный конвейеры реализуют РАЗНЫЕ наборы
стадий, поэтому набор не зашит в код, а приходит из манифеста
(`--implemented`). Так скрипт остаётся байт-в-байт одинаковым в обоих репо —
чего и требует `tests/lint/assert-twins.py`, заведённый после того, как гард
построили в одном репо и забыли перенести во второй.

Вход:
    resolve.py <профиль> <каталог-профилей> --implemented a,b,c
               [--release-stage sbom] [--event-stages mobile-scan]
    SKIP / EXTRA — через запятую, из окружения (inputs action'а)
Выход:
    строки `<стадия>=<режим>` в stdout (уходят в $GITHUB_OUTPUT)
    диагностика — в stderr
Коды:
    0 — разрешено, 2 — неизвестное имя стадии в skip/extra.

ТРИ КЛАССА СТАДИЙ. `--implemented` — стадии, которые исполняет воркфлоу
PR/push этого репо, их режимы читает Gate. `--release-stage` — стадия вне
контракта B/A/off (режим R), её читает release-джоб. `--event-stages` —
стадии по событию (mobile-scan над артефактом Xcode Cloud, ADR 0007
приватного репо): контракт B/A/off тот же, но исполняет их ДРУГОЙ воркфлоу
этого же репо на своём триггере (`repository_dispatch`), и вердикт
PR-конвейера не должен объявлять их неисполняемыми — иначе каждый PR
клиентского репо носил бы предупреждение о стадии, которая исполняется.
Объявить такую стадию в `--implemented` нельзя по той же причине с другой
стороны: Gate счёл бы её своей и ждал бы от неё исход шага, которого в
PR-прогоне нет.

ПОЧЕМУ ВАЛИДАЦИЯ ИМЁН. `skip-stages` раньше не проверял, что переданное имя
вообще существует: неизвестное просто не совпадало ни с чем и молча
игнорировалось. Потребитель, написавший `skip-stages: course-lnt`, получал
прогон, в котором стадия ВСЁ ЕЩЁ работает, — а был уверен, что выключил её.
Отказ при этом выглядел бы как отказ самой стадии, и искать причину пришлось
бы не там. Опечатка обязана падать сразу и называть себя.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


def norm(value: object) -> str:
    """YAML 1.1 разбирает голое `off` как False — нормализуем обратно."""
    return "off" if value is False else str(value)


def parse_list(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def known_stage_names(profiles_dir: Path, implemented: list[str], release: str,
                      event: list[str]) -> set[str]:
    """Имена стадий, известные конвейеру: реализованные ∪ ВСЕ профили.

    Читаются все профили, а не только текущий: `skip-stages: container` на
    классе library — не опечатка, а осмысленный no-op, и ронять его значило бы
    заставить потребителя знать чужой профиль наизусть. Точно так же имя
    стадии, объявленной в профиле, но ещё не реализованной (M1/M2), обязано
    приниматься: потребитель не должен ждать реализации, чтобы её выключить.
    """
    names = set(implemented) | set(event)
    if release:
        names.add(release)
    for path in sorted(profiles_dir.glob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # сломанный профиль — отдельная беда
            print(f"::warning::профиль {path.name} не разобран: {exc}", file=sys.stderr)
            continue
        names |= set((data.get("stages") or {}).keys())
    return names


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="разрешение профиля класса в режимы стадий")
    ap.add_argument("profile", type=Path)
    ap.add_argument("profiles_dir", type=Path, nargs="?", default=None)
    ap.add_argument("--implemented", required=True,
                    help="через запятую: стадии, реализованные в ЭТОМ репо")
    ap.add_argument("--release-stage", default="",
                    help="стадия вне контракта B/A/off (режим R, release-only)")
    ap.add_argument("--event-stages", default="",
                    help="через запятую: стадии по событию — исполняет другой "
                         "воркфлоу этого репо, контракт B/A/off тот же")
    args = ap.parse_args(argv[1:])

    profiles_dir = args.profiles_dir or args.profile.parent
    implemented = [s for s in args.implemented.split(",") if s.strip()]
    release = args.release_stage.strip()
    event = [s for s in args.event_stages.split(",") if s.strip()]

    profile = yaml.safe_load(args.profile.read_text(encoding="utf-8")) or {}
    stages = profile.get("stages") or {}

    skip = parse_list(os.environ.get("SKIP", ""))
    extra = parse_list(os.environ.get("EXTRA", ""))

    known = known_stage_names(profiles_dir, implemented, release, event)
    unknown = sorted((skip | extra) - known)
    if unknown:
        print(
            f"::error::неизвестные имена стадий в skip-stages/extra-stages: "
            f"{', '.join(unknown)}. Известные: {', '.join(sorted(known))}. "
            f"Молча проигнорировать нельзя: потребитель считал бы стадию "
            f"выключенной, а она бы работала.",
            file=sys.stderr,
        )
        return 2

    # Стадии по событию разрешаются тем же контрактом, что и реализованные:
    # skip выключает, extra включает как advisory. Разница — не в режиме,
    # а в том, КТО его читает: не Gate PR-конвейера, а воркфлоу события.
    for name in implemented + event:
        mode = norm(stages.get(name, "off"))
        if name in skip:
            mode = "off"
        elif name in extra and mode == "off":
            mode = "A"
        if mode not in ("B", "A", "off"):
            mode = "off"
        print(f"{name}={mode}")

    # Release-стадия живёт вне контракта B/A/off: её отдельно читает
    # release-джоб, и любой режим кроме R для неё означает «выключено».
    if release:
        mode = norm(stages.get(release, "off"))
        if release in skip or mode != "R":
            mode = "off"
        print(f"{release}={mode}")

    # Стадии, объявленные профилем, которых ЭТОТ конвейер не исполняет.
    # Уходят ВЫХОДОМ, а не только диагностикой. Причина в самой issue #19:
    # «зелёный honest-skip печатает ::notice::, который никто не читает», —
    # и вердикт Gate его действительно не видел. Соседний класс «стадия
    # отработала, но правил под язык не нашлось» (sarif/.inapplicable) текст
    # вердикта МЕНЯЕТ; этот, где стадия не выполнялась вовсе, не менял, так
    # что «blocking-находок нет» печаталось и при блокирующей по профилю
    # стадии, которой в конвейере нет вообще (profiles/library.yml,
    # `sast-sonar: B` — исполнителя нет ни в одном воркфлоу обоих репо).
    #
    # Режим стадии сохраняется отдельным выходом: нереализованная B и
    # нереализованная A — разные новости, а один общий список делает их
    # неразличимыми ровно там, где разница и важна.
    #
    # Явный `skip-stages` из списка вычитается: потребитель, выключивший
    # стадию сам, уже знает, что её нет; строка про неё приучала бы
    # пропускать весь вердикт.
    # Стадия по событию учтена: её исполняет другой воркфлоу этого репо, и
    # «не исполняется» про неё — неправда. Конвейер, которому `--event-stages`
    # не передан (публичный light), её и правда не исполняет — там она
    # в списке остаётся, и это тоже правда.
    accounted = set(implemented) | set(event) | ({release} if release else set())
    pending = {
        s: norm(m)
        for s, m in stages.items()
        # Режим R (release-стадия) сюда не идёт: она живёт вне контракта
        # B/A/off, её читает release-джоб, и Gate про неё вердикта не выносит.
        # Первая редакция фильтровала по `!= "off"` — и light-конвейер, куда
        # `--release-stage` не передаётся, объявлял `sbom` неисполняемым.
        if s not in accounted and norm(m) in ("B", "A") and s not in skip
    }
    print(f"unimplemented={','.join(sorted(pending))}")
    print(f"unimplemented-blocking={','.join(sorted(s for s, m in pending.items() if m == 'B'))}")
    if pending:
        print(
            f"::notice::Стадии профиля, которые этот конвейер не исполняет: "
            f"{', '.join(sorted(pending))}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
