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
               [--release-stage sbom]
    SKIP / EXTRA — через запятую, из окружения (inputs action'а)
Выход:
    строки `<стадия>=<режим>` в stdout (уходят в $GITHUB_OUTPUT)
    диагностика — в stderr
Коды:
    0 — разрешено, 2 — неизвестное имя стадии в skip/extra.

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


def known_stage_names(profiles_dir: Path, implemented: list[str], release: str) -> set[str]:
    """Имена стадий, известные конвейеру: реализованные ∪ ВСЕ профили.

    Читаются все профили, а не только текущий: `skip-stages: container` на
    классе library — не опечатка, а осмысленный no-op, и ронять его значило бы
    заставить потребителя знать чужой профиль наизусть. Точно так же имя
    стадии, объявленной в профиле, но ещё не реализованной (M1/M2), обязано
    приниматься: потребитель не должен ждать реализации, чтобы её выключить.
    """
    names = set(implemented)
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
    args = ap.parse_args(argv[1:])

    profiles_dir = args.profiles_dir or args.profile.parent
    implemented = [s for s in args.implemented.split(",") if s.strip()]
    release = args.release_stage.strip()

    profile = yaml.safe_load(args.profile.read_text(encoding="utf-8")) or {}
    stages = profile.get("stages") or {}

    skip = parse_list(os.environ.get("SKIP", ""))
    extra = parse_list(os.environ.get("EXTRA", ""))

    known = known_stage_names(profiles_dir, implemented, release)
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

    for name in implemented:
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

    accounted = set(implemented) | ({release} if release else set())
    pending = [s for s, m in stages.items() if s not in accounted and norm(m) != "off"]
    if pending:
        print(
            f"::notice::Стадии из профиля ещё не реализованы (ждут M1/M2): "
            f"{', '.join(sorted(pending))}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
