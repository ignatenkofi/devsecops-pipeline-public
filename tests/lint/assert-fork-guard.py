#!/usr/bin/env python3
"""Детектор: self-hosted джоба на pull_request без fork-guard (ADR 0005).

Правило ADR 0005, п. 2: self-hosted никогда не исполняет код форков. Раннер
фермы polygon-iac стоит внутри домашней сети, и job из чужого форка на нём —
это исполнение произвольного кода за периметром. Первый эшелон — само
событие (тяжёлые стадии не запускаются на `pull_request`), второй — условие
в job, которое отбрасывает PR из форка. Второй эшелон держался на памяти:
в этом репозитории он не стоял ни на одной PR-триггерной self-hosted джобе,
и узнать об этом было неоткуда — так и нашлось при написании детектора.

Что считается нарушением. Workflow с триггером `pull_request` или
`pull_request_target`, в котором есть job с `runs-on`, разрешающимся в
литерал `self-hosted` или метку с префиксом `polygon`, и у этой job нет
`if:` с одной из принятых форм guard'а:

    github.event.pull_request.head.repo.fork == false
    github.event.pull_request.head.repo.full_name == github.repository

Первая — форма polygon-iac (`tests/shell/test-workflow-guards.sh`): на
событиях без pull_request выражение сравнивает null с false и по правилам
приведения типов GitHub даёт true, так что push и schedule она не трогает.
Форма из текста ADR 0005 — `github.event.repository.fork == false` —
guard'ом НЕ считается намеренно: `repository` там — базовый репозиторий, и
для PR из форка в не-форк это выражение истинно всегда; оно защищает от
другого случая (workflow, унесённый форком), не от этого.

Что нарушением НЕ считается. Workflow только с `workflow_call`: событие
выбирает вызывающий, и guard принадлежит его манифесту — такие self-hosted
job без `if:` печатаются `::notice::` как совет, не как отказ. Выражение в
`runs-on` (`${{ inputs.runs-on }}`) — метку выбирает вызывающий, литерала
нет. Hosted-метки — вне правила: код форка на hosted-раннере GitHub — норма.

Детектор обязан УМЕТЬ КРАСНЕТЬ, и это доказывается здесь же: перед проходом
по репозиторию он гоняет себя на встроенных манифестах — плохом (guard'а
нет), хорошем (guard есть), вне области (только push), workflow_call
(совет, не отказ) — и падает, если различить их не смог.

Коды возврата — конвенция портфеля: 0 чисто, 1 нарушение, 2 не смогли
проверить (нет каталога workflows / манифест не разбирается).

Использование:  assert-fork-guard.py [корень репозитория]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - среда без pyyaml
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

PR_EVENTS = ("pull_request", "pull_request_target")
SELF_HOSTED_RX = re.compile(r"^(self-hosted|polygon\S*)$")
GUARD_FORMS = (
    "pull_request.head.repo.fork == false",
    "pull_request.head.repo.full_name == github.repository",
)


def triggers(doc: dict) -> list[str]:
    """Имена событий `on:` — под YAML 1.1 ключ приходит как True."""
    raw = doc.get("on")
    if raw is None:
        raw = doc.get(True)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    return []


def self_hosted_labels(job: dict) -> list[str]:
    """Литералы `runs-on`, разрешающиеся в self-hosted. Выражения — пусто."""
    ro = job.get("runs-on")
    if ro is None:
        return []
    if isinstance(ro, dict):  # runs-on: {group: …, labels: …}
        ro = ro.get("labels") or []
    labels = [ro] if isinstance(ro, str) else [str(x) for x in ro] if isinstance(ro, list) else []
    return [x for x in labels if "${{" not in x and SELF_HOSTED_RX.match(x.strip())]


def has_guard(job: dict) -> bool:
    cond = str(job.get("if", ""))
    return any(form in cond for form in GUARD_FORMS)


def check_workflow(rel: str, text: str) -> tuple[list[str], list[str]]:
    """(нарушения, советы) по одному манифесту."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return [f"{rel}: манифест не разбирается в словарь"], []
    events = triggers(doc)
    pr = any(e in PR_EVENTS for e in events)
    call_only = events == ["workflow_call"]
    problems, advice = [], []
    for name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict) or not self_hosted_labels(job) or has_guard(job):
            continue
        if pr:
            problems.append(
                f"{rel}::{name}: self-hosted job на pull_request без fork-guard — "
                f"добавить `if: github.event.pull_request.head.repo.fork == false` (ADR 0005)")
        elif call_only:
            advice.append(
                f"{rel}::{name}: reusable self-hosted job без fork-guard — событие выбирает "
                f"вызывающий; guard в самой job закрыл бы всех потребителей разом")
    return problems, advice


# --- самопроверка: детектор обязан различать ------------------------------
_BAD = """
on: [push, pull_request]
jobs:
  a:
    runs-on: [self-hosted, polygon]
    steps: [{run: echo}]
"""
_GOOD = _BAD.replace("    runs-on: [self-hosted, polygon]\n",
                     "    runs-on: [self-hosted, polygon]\n"
                     "    if: github.event.pull_request.head.repo.fork == false\n")
_GOOD_FULL_NAME = _BAD.replace(
    "    runs-on: [self-hosted, polygon]\n",
    "    runs-on: [self-hosted, polygon]\n"
    "    if: github.event.pull_request.head.repo.full_name == github.repository\n")
_ADR_TEXT_FORM = _BAD.replace("    runs-on: [self-hosted, polygon]\n",
                              "    runs-on: [self-hosted, polygon]\n"
                              "    if: github.event.repository.fork == false\n")
_PUSH_ONLY = _BAD.replace("on: [push, pull_request]", "on: [push]")
_HOSTED = _BAD.replace("[self-hosted, polygon]", "ubuntu-latest")
_EXPR = _BAD.replace("[self-hosted, polygon]", "${{ inputs.runs-on }}")
_CALL_ONLY = _BAD.replace("on: [push, pull_request]", "on: [workflow_call]")
_YAML11 = _BAD.replace("on: [push, pull_request]", "on:\n  pull_request:\n  push:")


def selftest() -> list[str]:
    out = []
    p, a = check_workflow("bad", _BAD)
    if not p:
        out.append("самопроверка: self-hosted job на pull_request без guard'а не поймана")
    for label, text in (("guard fork==false", _GOOD), ("guard full_name", _GOOD_FULL_NAME)):
        if check_workflow("good", text)[0]:
            out.append(f"самопроверка: здоровый манифест ({label}) признан нарушением")
    if not check_workflow("adr-form", _ADR_TEXT_FORM)[0]:
        out.append("самопроверка: `repository.fork == false` зачтён за guard, а он не защищает")
    for label, text in (("push-only", _PUSH_ONLY), ("hosted", _HOSTED), ("выражение", _EXPR)):
        if check_workflow("scope", text)[0]:
            out.append(f"самопроверка: манифест вне области ({label}) признан нарушением")
    p, a = check_workflow("call", _CALL_ONLY)
    if p or not a:
        out.append("самопроверка: workflow_call обязан давать совет, а не нарушение")
    if not check_workflow("yaml11", _YAML11)[0]:
        out.append("самопроверка: `on:` как ключ True (YAML 1.1) не прочитан")
    return out


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    blind = selftest()
    if blind:
        for line in blind:
            print(f"::error::{line}", file=sys.stderr)
        return 1
    wf = root / ".github" / "workflows"
    files = sorted(wf.glob("*.y*ml"))
    if not files:
        print(f"::error::манифестов не найдено под {wf} — проверить нечего", file=sys.stderr)
        return 2
    problems, advice, unreadable = [], [], 0
    for path in files:
        try:
            p, a = check_workflow(str(path.relative_to(root)), path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(f"::error::{path.relative_to(root)}: YAML не разбирается: {exc}", file=sys.stderr)
            unreadable += 1
            continue
        problems += p
        advice += a
    for line in advice:
        print(f"::notice::{line}", file=sys.stderr)
    for line in problems:
        print(f"::error::{line}", file=sys.stderr)
    if problems:
        return 1
    if unreadable:
        return 2
    print(f"OK: fork-guard — манифестов {len(files)}, self-hosted job на pull_request без "
          f"guard'а нет (советов по reusable: {len(advice)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
