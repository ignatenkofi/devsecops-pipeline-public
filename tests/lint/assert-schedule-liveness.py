#!/usr/bin/env python3
"""У периодической задачи обязан быть признак живости, способный сработать (#19).

Провалившийся scheduled-workflow сам о себе не сообщает: расписание молчит
одинаково и когда всё хорошо, и когда джоба падает седьмую ночь подряд. В
этом портфеле лекарство одно — шаг `health-issue`, заводящий issue на отказе
и снимающий её на зелёном. Но лекарство работает, только если шаг ВЫПОЛНЯЕТСЯ
в тот прогон, ради которого написан.

Отсюда правило детектора, и в нём два условия, а не одно:

    у каждого workflow с триггером `schedule:` обязан быть хотя бы один шаг
    `health-issue` ПОД `if: always()`.

Второе условие не педантизм. Шаги job'а по умолчанию не выполняются после
отказа предыдущего, поэтому `health-issue` без `always()` докладывает только
об удачных ночах — то есть ровно о тех, о которых докладывать не надо.
Именно так у `nightly-bump` два из трёх сигналов (мажоры, пины без гарда)
не исполнялись ни разу за семь красных ночей: до них не доходило
управление. Третий, под `always()`, работал — им и виден отказ.

Если сигнал вынесен в отдельную джобу с `needs:` (сторож, читающий исход
основной), шагового `always()` мало: шаг исполняется, только если
исполняется джоба. Без статусной функции в `jobs.<id>.if` GitHub
подставляет неявный `success()`, и при отказе `needs` джоба пропускается
целиком. Статусная функция в любом месте условия неявный `success()`
снимает, и дальше исход решает само условие: `always() && X` — ровно `X`.

Отсюда правило для джобы с `needs:` (devsecops-pipeline-public#64). Её
`if` — без обёртки `${{ … }}` во всё условие, если она есть, и без
строковых литералов — режется по `&&`; один кусок обязан быть ровно
`always()`, остальные — не звать статусных функций (`success()`,
`failure()`, `cancelled()`, `always()`) и не упоминать `needs`, в любом
регистре. Тогда неявного `success()` нет, а прочее читает событие, ветку,
входы, но не исход `needs`: при отказе `needs` джоба исполняется ровно
тогда же, когда и без него. `always() && github.event_name == 'schedule'`
годится; `always() && success()`, `always() && !failure()` и `always() &&
needs.<id>.result == 'success'` — нет: при отказе `needs` такая джоба не
исполнится, `always()` рядом ничего не меняет. Подстроки мало:
`!always()` и литерал `'always()'` её содержат, а джобу при отказе не
запускают. Не годится и обёртка `${{ … }}` не во всё условие (`always()
&& ${{ X }}`): такой `if` GitHub читает строкой-шаблоном, текст вне
обёртки для него литерал, и `always()` там не вызов.

Правило сознательно строже GitHub. Не принимаются формы, где `always()`
не отдельный кусок между `&&` (`${{ !cancelled() }}`, `always() || X`,
`(always() && X)`), и любая статусная функция рядом с ним, а не только
читающая исход `needs` (`always() && !cancelled()`), — хотя такую джобу
GitHub при отказе `needs` исполнит. `always() || X` истинно всегда: `X`
мёртв, и джоба пойдёт на любом событии — за такой формой скорее опечатка
в `&&`, чем замысел. Контракт — одна явная форма, как и у шага.

Почему линт, а не соглашение: сквозное правило, которое держится на памяти,
ломается на СЛЕДУЮЩЕМ добавленном расписании, и ломается молча — новая
джоба просто не заводит issue, а отличить это от «отказов не было» нельзя.

Грабля YAML 1.1: ключ `on:` разбирается как булево `True`, поэтому инвентарь
расписаний обязан смотреть и `d.get("on")`, и `d.get(True)`. Наивная версия
отвечает «периодических джоб не найдено» на репозитории, где они есть, —
и это неотличимо от чистого прогона.

--------------------------------------------------------------------------
СТОРОЖ НА МЁРТВОМ РАННЕРЕ (warning, не ошибка)

Правило выше проверяет, что сигнал ЕСТЬ. Оно молчит о том, где он стоит, —
а это второй способ получить тот же отказ. Шаг `health-issue` исполняется
на раннере своей джобы; если джоба объявлена `runs-on: [self-hosted, …]`,
а раннеров с такой меткой нет (ферма стоит, разобрана, выпала из установки
App), прогон не падает и не доходит до `always()` — он висит в очереди и
через сутки закрывается `cancelled`. Health-issue не заводится никогда,
снаружи это «отказов не было». Так с 2026-10-06 стоят `nightly-bump` и
`tag-lag` приватного репо: оба формально «ok» по правилу выше, оба немы.

Отсюда второе правило — предупреждением: у периодического workflow, чьи
`health-issue` под `always()` все до одного стоят на self-hosted джобах,
нет hosted-дублёра, и его признак живости умирает вместе с раннером.
Лечится джобой-сторожем на `ubuntu-latest` (отдельная джоба в том же
workflow, `if: always()`, читает исход основной), либо осознанным
решением владельца не платить за hosted-минуты — тогда предупреждение и
есть его напоминание. Warning, а не ошибка: решение 2026-08-09 держит
приватные джобы на ферме намеренно (`assert-runner-labels.py`), и гард,
красящий это решение в отказ, был бы шире своего правила. `--strict`
поднимает предупреждение до ошибки (rc 1) там, где hosted-дублёр
обязателен. `runs-on` выражением (`${{ … }}`) или без hosted/self-hosted
литерала — «неизвестно», предупреждения нет: метку выбирает вызывающий.

--------------------------------------------------------------------------
СКВОЗНОЙ ИНВЕНТАРЬ (`--inventory`)

Заглавие #19 — «у КАЖДОЙ периодической джобы», а блокирующее правило выше
видит один механизм: `.github/workflows/**`. Остальные сканируются только
здесь, и по ним правило молчит по построению, а не по чистоте:

    launchd  — `StartCalendarInterval` / `StartInterval` в plist (мак);
    systemd  — `OnCalendar=` / `OnUnitActiveSec=` / `OnUnitInactiveSec=`
               в `*.timer`;
    RouterOS — `/system scheduler` в снятых экспортах устройств.

Сканируемыми механизмами перечень НЕ исчерпывается, и это главное, чего не
говорила прежняя редакция. Она называла механизмы «четырьмя» — то есть
объявляла своё покрытие полным, — и утверждение молча разошлось с
действительностью. Пятый механизм: расписание, заведённое как Routine
аккаунта Claude. Оно не оставляет в репозитории ни файла, ни строки, поэтому
файловый скан слеп к нему ПО ПОСТРОЕНИЮ: сканировать нечего, и никакая
чистота реализации этого не меняет.

Пятый механизм отличается от остальных четырёх ещё одним свойством, и оно
хуже слепоты: у workflow, plist, таймера и RouterOS-шедулера исчезновение
видно диффом, а рутина умеет исчезнуть целиком, не оставив следа нигде.
Замер 2026-09-06 (`list_triggers` дважды, разными фильтрами): рекуррентных
рутин на аккаунте ноль, при том что рутина `yinguo-lab PROTO-004 decay tick`
(крон `0 12 * * 3`) была пересоздана 13.08 и задокументирована паспортом в
`yinguo-lab#53`. Её артефакт `atlas/DECAY-CURVE.md` несёт две точки,
последняя — 12.08; пропущены тики 19.08, 26.08 и 02.09, и заметил это свип
через три пропуска. Ровно тот отказ, который ищет #19, в механизме, который
этот инструмент не покрывает.

Поэтому перечень механизмов объявлен ниже СТРУКТУРОЙ (`SCANNABLE` и
`UNSCANNABLE`), а не числом в прозе, и итог `--inventory` называет
непокрытый механизм отдельной строкой. Число в тексте устаревает молча;
расхождение структуры со списком сканеров ловит самопроверка.

Пять раз подряд этот инвентарь снимали руками, и пять раз он расходился с
репозиториями: 5 задач → 17 → «4 из 5» → «14 джоб», позже поправлено на 20.
Причина всегда одна — инвентарь по одному источнику инвентарём не является.
Поэтому здесь не таблица, а команда, которая её считает:

    assert-schedule-liveness.py --inventory КАТАЛОГ      # каталог клонов
    assert-schedule-liveness.py --inventory . --json     # машинно

Признак живости у неактионсовой периодики берётся не из головы: в портфеле
он существует в трёх формах. Две видны в исполняемом скрипте — вызов пульса
(`lib/heartbeat.sh`, `heartbeat_send`) либо запись маркера успеха
(`.last-success`, `liveness_mark` из `lib/liveness.sh`), который потом
кто-то проверяет. Третья — объявление в самом манифесте: ключ `X-Liveness=`
в systemd-юните (`X-*` systemd игнорирует) или `X-Liveness` в plist называет
артефакт свежести, который задача оставляет иначе — например, свой же TSV.
Объявление принимается на слово: инвентарь артефакт не проверяет, а лишь
перестаёт считать задачу слепой; проверка — команда, названная в объявлении.
Скрипт, который не делает ничего из этого, снаружи неотличим от
неработающего.

Цель таймера, указывающая на консольный скрипт venv
(`/opt/<продукт>/venv/bin/<имя> …`), разрешается через `[project.scripts]`
ближайшего `pyproject.toml` в модуль входа — иначе каждый сервис на Python
читался бы как «не смогли проверить», и это было бы вечно. Разоружённый
plist (`Disabled = true`) — то же третье состояние, что снятый комментарием
крон: не исполняется, признак неприменим, молчать нельзя.

Коды возврата — конвенция портфеля, три состояния вместо двух:

    0 — у каждой найденной периодической задачи признак живости есть;
    1 — есть задачи без признака (или, в режиме репозитория, нарушение
        блокирующего правила);
    2 — проверить не смогли (нечего сканировать, цель таймера не
        разрешается в файл репозитория). «Не смог проверить» обязано
        отличаться от «нашёл расхождение» кодом, иначе чинить пойдут не
        тот конец.

Использование:  assert-schedule-liveness.py [корень репозитория] [--strict]
                assert-schedule-liveness.py --inventory [каталог] [--json]
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - среда без pyyaml
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

# Оба репо конвейера зовут один и тот же action, но по-разному: публичный
# локально (`./actions/health-issue`), приватный по тегу
# (`ignatenkofi/devsecops-pipeline-public/actions/health-issue@v1`).
# Матчим по подстроке пути, а не по полной строке.
HEALTH_ACTION = "actions/health-issue"


def triggers(doc: dict) -> dict:
    """`on:` под YAML 1.1 — булев ключ True. Смотреть обязательно оба."""
    raw = doc.get("on")
    if raw is None:
        raw = doc.get(True)
    return raw if isinstance(raw, dict) else {}


# Что запрещено в кусках `if` джобы рядом с `always()` (шапка): любая
# статусная функция и контекст `needs`.
_STATUS_CALL_RX = re.compile(r"\b(?:success|failure|cancelled|always)\s*\(",
                             re.IGNORECASE)
_NEEDS_REF_RX = re.compile(r"\bneeds\b", re.IGNORECASE)

# Причины отказа `needs_job_flaw`. Самопроверка сверяет по ним, какая из
# проверок сработала: текст вердикта обязан быть верен для своей формы.
FLAW_NO_IF = "`if` нет: GitHub подставит `success()`"
FLAW_TEMPLATE = "обёртка `${{ … }}` не во всё условие"
FLAW_NO_ALWAYS = "нет `always()` целиком или операндом `&&`"
FLAW_NEIGHBOUR = "кроме `always()` в условии статусная функция или `needs`"


def needs_job_flaw(job: dict) -> str:
    """Чем `if` джобы не годится для сигнала при отказе её `needs` (правило
    — в шапке); пустая строка — годится. Без `needs:` годится любая:
    шаговый `always()` решает сам.

    Литералы вырезаются до разбора: `'always()'` — данные, а не вызов.
    Экранированная кавычка (`'it''s'`) режет литерал на два соседних, но
    всё, что между кавычками, вырезается и так — разбор её не различает.
    """
    if not (job or {}).get("needs"):
        return ""
    raw = (job or {}).get("if")
    if raw is None:
        return FLAW_NO_IF + ", и при отказе `needs` джоба пропустится"
    shown = "`if: " + " ".join(str(raw).split()) + "`"
    expr = str(raw).strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    if "${{" in expr:
        return (FLAW_TEMPLATE + " — GitHub читает такой `if` строкой-шаблоном, "
                "и текст вне обёртки для него не код: " + shown)
    parts = [p.strip() for p in re.sub(r"'[^']*'", "''", expr).split("&&")]
    if "always()" not in parts:
        return FLAW_NO_ALWAYS + ": " + shown
    if any(_STATUS_CALL_RX.search(p) or _NEEDS_REF_RX.search(p)
           for p in parts if p != "always()"):
        return FLAW_NEIGHBOUR + ": " + shown
    return ""


def check_workflow(rel: str, text: str) -> list:
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return [f"{rel}: не разбирается как YAML — {exc}"]
    if not isinstance(doc, dict) or "schedule" not in triggers(doc):
        return []

    seen_any, flawed = False, []
    for name, job in (doc.get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            if HEALTH_ACTION not in str((step or {}).get("uses", "")):
                continue
            seen_any = True
            if str((step or {}).get("if", "")).strip() != "always()":
                continue
            # Джобу проверяем ДО раннего выхода: иначе джоба с `needs:`
            # зачлась бы по шагу, который при отказе не исполнится.
            flaw = needs_job_flaw(job)
            if not flaw:
                return []  # нашли годный — этого достаточно
            flawed.append(f"{name}: {flaw}")

    if flawed:
        return [
            f"{rel}: health-issue под `if: always()` стоит только в джобах "
            f"с `needs:`, и `if` ни одной из них не по правилу шапки линта "
            f"({'; '.join(sorted(set(flawed)))}) — шаговый `always()` "
            f"исполняется, только если исполняется джоба"
        ]
    if seen_any:
        return [
            f"{rel}: у периодической джобы есть health-issue, но ни один "
            f"не под `if: always()` — сигнал доложит только об удачной ночи"
        ]
    return [
        f"{rel}: периодическая джоба без признака живости — провалившийся "
        f"scheduled-workflow сам о себе не сообщит (#19)"
    ]


SELF_HOSTED, HOSTED, UNKNOWN_RUNNER = "self-hosted", "hosted", "unknown"
_HOSTED_PREFIXES = ("ubuntu-", "windows-", "macos-")


def runner_kind(job: dict) -> str:
    """Где исполняется джоба: self-hosted / hosted / неизвестно.

    Литерал `self-hosted` среди меток — ферма; метка с префиксом образа
    GitHub (`ubuntu-`, `windows-`, `macos-`) — hosted; выражение `${{ … }}`,
    runner group без меток или пустой `runs-on` — неизвестно. Неизвестное
    предупреждения не даёт: метку выбирает вызывающий, и красить это
    значит шуметь на исправном фасаде (та же граница, что у
    assert-runner-labels.py).
    """
    raw = (job or {}).get("runs-on")
    if isinstance(raw, dict):
        raw = raw.get("labels")
    labels = raw if isinstance(raw, list) else [raw]
    labels = [str(x) for x in labels if x is not None]
    if not labels or any("${{" in x for x in labels):
        return UNKNOWN_RUNNER
    if any(x.strip() == "self-hosted" for x in labels):
        return SELF_HOSTED
    if any(x.strip().startswith(_HOSTED_PREFIXES) for x in labels):
        return HOSTED
    return UNKNOWN_RUNNER


def watchdog_warnings(rel: str, text: str) -> list:
    """Сторож на мёртвом раннере: все годные `health-issue` (под `always()`,
    в джобе с `needs:` — и с `if` джобы по правилу шапки) — на self-hosted
    джобах, hosted-дублёра нет. Предупреждение, не ошибка: см. шапку.
    Пусто, если годного сигнала нет вовсе (это ошибка check_workflow),
    если хоть один сигнал стоит на hosted или если раннер неизвестен.
    Hosted-сторож с `needs:`, чей `if` не по правилу (`needs_job_flaw`),
    дублёром не считается — правило то же, что у check_workflow: без `if`
    или с `always() && success()` при отказе основной джобы сторож
    пропускается вместе с ней."""
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict) or "schedule" not in triggers(doc):
        return []

    kinds = []
    for name, job in ((doc.get("jobs") or {}) or {}).items():
        for step in (job or {}).get("steps") or []:
            if HEALTH_ACTION not in str((step or {}).get("uses", "")):
                continue
            if (str((step or {}).get("if", "")).strip() == "always()"
                    and not needs_job_flaw(job)):
                kinds.append((str(name), runner_kind(job)))
    if not kinds or any(k != SELF_HOSTED for _, k in kinds):
        return []
    jobs = ", ".join(sorted({n for n, _ in kinds}))
    return [
        f"{rel}: признак живости стоит только на self-hosted ({jobs}), "
        f"hosted-дублёра нет — сторож на мёртвом раннере: без раннера прогон "
        f"висит в очереди, до `always()` не доходит, health-issue не "
        f"заводится, и отказ снаружи выглядит как «отказов не было» (#19)"
    ]


# --- сквозной инвентарь: четыре механизма -----------------------------------

OK, GAP, UNKNOWN, DISARMED = "ok", "gap", "unknown", "disarmed"


@dataclass
class Task:
    """Одна периодическая задача, кем бы она ни была заведена."""

    mechanism: str  # actions | launchd | systemd | routeros
    repo: str
    where: str  # манифест относительно корня репозитория
    name: str
    schedule: str
    target: str  # что исполняется ("" — неприменимо/не разрешилось)
    status: str  # ok | gap | unknown | disarmed
    signal: str  # чем именно наблюдается ("" — ничем)


# Расписание, снятое комментарием, — третье состояние, а не отсутствие
# задачи. Разоружённая периодика выглядит снаружи как «задачи нет», и
# ровно это случилось с двумя сторожами портфеля 13.08: крон закомментирован
# («исполняться негде»), сторожа три недели не ходят, а инвентарь по разбору
# YAML их не видит вовсе. Вердикта тут нет намеренно: снять расписание —
# законное решение владельца, и гард, красящий его в отказ, был бы шире
# своего правила. Инвентарь обязан лишь не молчать.
_DISARMED_RX = re.compile(r"^\s*#\s*(?:-\s*)?(?:cron|schedule)\s*:", re.MULTILINE)


# Формы признака живости, видимые в исполняемом скрипте. Порядок важен
# только для текста вердикта. Третья форма — объявление в манифесте —
# живёт не здесь, а в `declared_signal`.
SIGNAL_RULES = (
    ("пульс (heartbeat)", re.compile(r"heartbeat", re.IGNORECASE)),
    ("маркер успеха", re.compile(r"last[-_]success", re.IGNORECASE)),
    ("маркер живости (lib/liveness.sh)", re.compile(r"\bliveness_mark\b")),
)

# Объявленный признак: ключ манифеста, а не строка скрипта. Принимается на
# слово — см. шапку. Значение уходит в вердикт целиком, чтобы читатель видел,
# ЧТО объявлено и чем это проверить.
_DECLARED_KEY = "X-Liveness"
_DECLARED_RX = re.compile(r"^X-Liveness\s*=\s*(.+?)\s*$", re.MULTILINE)


def declared_signal(*texts: str) -> str:
    """Значение `X-Liveness=` в первом из текстов, где оно есть ("" — нет)."""
    for text in texts:
        m = _DECLARED_RX.search(code_lines(text))
        if m:
            return m.group(1)
    return ""


def code_lines(text: str) -> str:
    """Строки без сплошных комментариев.

    Скрипт, который лишь УПОМИНАЕТ пульс в комментарии, признака живости не
    имеет. Матчить по всему файлу — тот самый гард шире своего правила:
    `# TODO: добавить heartbeat` зачёлся бы как сигнал.
    """
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )


def script_signal(path: Path) -> tuple[str, str]:
    """(status, signal) для исполняемого файла задачи."""
    try:
        body = code_lines(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return UNKNOWN, ""
    for label, rx in SIGNAL_RULES:
        if rx.search(body):
            return OK, label
    return GAP, ""


def resolve_in_repo(root: Path, raw: str) -> Path | None:
    """Путь из манифеста → файл репозитория.

    Манифесты несут три формы: репо-относительную (`scripts/x.sh`),
    шаблонную (`__REPO_PATH__/scripts/x.sh`) и путь развёртывания
    (`/opt/<продукт>/bin/collect.sh`). Последняя в репозитории не
    существует — ищем по имени файла. Не нашли — честный UNKNOWN, а не
    «сигнала нет»: это разные утверждения.
    """
    if not raw:
        return None
    rel = raw.replace("__REPO_PATH__/", "").lstrip("/")
    direct = root / rel
    if direct.is_file():
        return direct
    hits = [p for p in root.rglob(Path(raw).name) if p.is_file()]
    if len(hits) == 1:
        return hits[0]
    venv = _VENV_BIN_RX.search(raw)
    return _console_script_module(root, venv.group(1)) if venv else None


_VENV_BIN_RX = re.compile(r"(?:^|/)(?:venv|\.venv|env)/bin/([\w.-]+)$")
_SCRIPTS_SECTION_RX = re.compile(
    r"^\[project\.scripts\][ \t]*\n(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_SCRIPT_ENTRY_RX = re.compile(r'^\s*"?([\w.-]+)"?\s*=\s*"([\w.]+):[\w.]+"')
_SKIP_DIRS = {".git", "venv", ".venv", "env", "node_modules", "__pycache__"}


def _console_script_module(root: Path, name: str) -> Path | None:
    """`venv/bin/<name>` → файл модуля из `[project.scripts]` (плоская или
    src-раскладка). Нет записи или файла — None, то есть честный UNKNOWN."""
    for pp in sorted(root.rglob("pyproject.toml")):
        if _SKIP_DIRS & set(pp.relative_to(root).parts):
            continue
        section = _SCRIPTS_SECTION_RX.search(
            pp.read_text(encoding="utf-8", errors="replace"))
        if not section:
            continue
        for ln in section.group(1).splitlines():
            entry = _SCRIPT_ENTRY_RX.match(ln)
            if not entry or entry.group(1) != name:
                continue
            parts = entry.group(2).split(".")
            for base in (pp.parent, pp.parent / "src"):
                mod = base.joinpath(*parts).with_suffix(".py")
                if mod.is_file():
                    return mod
                init = base.joinpath(*parts) / "__init__.py"
                if init.is_file():
                    return init
            return None
    return None


def scan_actions(root: Path, repo: str) -> list[Task]:
    tasks = []
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root))
        try:
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            # Тихо пропустить неразбираемый манифест значит убрать задачу из
            # инвентаря — снаружи это неотличимо от «задачи нет». Ровно тот
            # дефект, который ищет #19, только в самом инструменте.
            tasks.append(Task("actions", repo, rel, path.stem, "?", "", UNKNOWN,
                              f"манифест не разбирается: {type(exc).__name__}"))
            continue
        if not isinstance(doc, dict):
            continue
        if "schedule" not in triggers(doc):
            head = text.split("\njobs:")[0]
            if _DISARMED_RX.search(head):
                tasks.append(Task("actions", repo, rel,
                                  str(doc.get("name") or path.stem),
                                  "снято комментарием", "", DISARMED, ""))
            continue
        crons = [
            str(e.get("cron", "?"))
            for e in (triggers(doc).get("schedule") or [])
            if isinstance(e, dict)
        ]
        # Вердикт — из ТОГО ЖЕ check_workflow, что и блокирующее правило.
        # Вторая деривация одного правила заводит вторую правду, которая
        # расходится с первой молча.
        bad = check_workflow(rel, text)
        signal = "health-issue под always()"
        if not bad and watchdog_warnings(rel, text):
            # Статус остаётся ok — правило про наличие сигнала выполнено.
            # Но читатель инвентаря обязан видеть, что сигнал умирает
            # вместе с раннером: «ok» без этой оговорки и есть тот
            # ложный зелёный, который ищет #19.
            signal += "; только на self-hosted, hosted-дублёра нет"
        tasks.append(
            Task(
                "actions",
                repo,
                rel,
                str(doc.get("name") or path.stem),
                ", ".join(crons) or "?",
                "",
                GAP if bad else OK,
                "" if bad else signal,
            )
        )
    return tasks


def scan_launchd(root: Path, repo: str) -> list[Task]:
    tasks = []
    for path in sorted(root.rglob("*.plist")):
        try:
            d = plistlib.loads(path.read_bytes())
        except Exception:  # noqa: BLE001 — битый plist это не наша тема
            continue
        if not isinstance(d, dict):
            continue
        cal, every = d.get("StartCalendarInterval"), d.get("StartInterval")
        if cal is None and every is None:
            continue  # RunAtLoad-плист периодикой не является
        args = " ".join(str(a) for a in (d.get("ProgramArguments") or []))
        m = re.search(r"[\w./-]+\.(?:sh|py)", args)
        raw = m.group(0) if m else ""
        schedule = f"каждые {every} с" if every is not None else str(cal)
        declared = str(d.get(_DECLARED_KEY) or "")
        if d.get("Disabled") is True:
            # Разоружён ключом: launchd такой плист не грузит. Третье
            # состояние, как снятый комментарием крон, — не отказ и не дыра.
            status, signal = DISARMED, ""
            schedule = f"снято ключом Disabled ({schedule})"
        elif declared:
            status, signal = OK, f"объявлен: {declared}"
        else:
            script = resolve_in_repo(root, raw)
            status, signal = script_signal(script) if script else (UNKNOWN, "")
        tasks.append(
            Task(
                "launchd",
                repo,
                str(path.relative_to(root)),
                str(d.get("Label") or path.stem),
                schedule,
                raw,
                status,
                signal,
            )
        )
    return tasks


_PERIODIC_UNITS = ("OnCalendar", "OnUnitActiveSec", "OnUnitInactiveSec")


def scan_systemd(root: Path, repo: str) -> list[Task]:
    tasks = []
    for path in sorted(root.rglob("*.timer")):
        body = code_lines(path.read_text(encoding="utf-8", errors="replace"))
        sched = [
            ln.strip()
            for ln in body.splitlines()
            if ln.strip().startswith(_PERIODIC_UNITS)
        ]
        # Только `OnBootSec=` — одиночный выстрел при загрузке, не периодика.
        # Правило про периодические задачи, и гард шире своего правила шумит
        # на исправном репозитории.
        if not sched:
            continue
        unit = re.search(r"^Unit=(.+)$", body, re.MULTILINE)
        svc = root / (unit.group(1).strip() if unit else path.stem + ".service")
        if not svc.is_file():
            svc = path.with_name(unit.group(1).strip() if unit else path.stem + ".service")
        raw = ""
        svc_text = svc.read_text(encoding="utf-8", errors="replace") if svc.is_file() else ""
        ex = re.search(r"^ExecStart=(.+)$", code_lines(svc_text), re.MULTILINE)
        if ex:
            m = re.search(r"[\w./-]+\.(?:sh|py)", ex.group(1))
            raw = m.group(0) if m else ex.group(1).split()[0]
        declared = declared_signal(svc_text, body)
        if declared:
            status, signal = OK, f"объявлен: {declared}"
        else:
            script = resolve_in_repo(root, raw)
            status, signal = script_signal(script) if script else (UNKNOWN, "")
        tasks.append(
            Task(
                "systemd",
                repo,
                str(path.relative_to(root)),
                path.stem,
                "; ".join(sched),
                raw,
                status,
                signal,
            )
        )
    return tasks


def scan_routeros(root: Path, repo: str) -> list[Task]:
    """Планировщики RouterOS из снятых экспортов.

    Наблюдаемость здесь грубее и это сказано вслух: `run-count` в экспорт не
    попадает, его снимает отдельный харвестер по ssh. Поэтому признак
    определяется на уровне репозитория — есть ли в нём код, читающий
    `run-count`, — а не на уровне отдельного планировщика.
    """
    harvester = any(
        "run-count" in p.read_text(encoding="utf-8", errors="replace")
        for p in root.rglob("scripts/*.sh")
        if p.is_file()
    )
    tasks = []
    for path in sorted(root.rglob("*.rsc")):
        section = None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("/"):
                section = line.strip()
            if section != "/system scheduler" or not line.startswith("add "):
                continue
            name = re.search(r"name=(\S+)", line)
            interval = re.search(r"interval=(\S+)", line)
            if not interval or interval.group(1) in ("0s", "00:00:00"):
                continue  # одноразовый планировщик периодикой не является
            tasks.append(
                Task(
                    "routeros",
                    repo,
                    str(path.relative_to(root)),
                    name.group(1) if name else "?",
                    interval.group(1),
                    "",
                    OK if harvester else GAP,
                    "харвест run-count" if harvester else "",
                )
            )
    return tasks


# --- механизмы: что инструмент видит, и что не может увидеть в принципе ---
#
# Перечень объявлен структурой, а не числом в прозе и не порядком вызовов
# внутри scan_repo. Причина конкретная: прежняя редакция говорила «четыре
# механизма», перечисление читалось как полное, и пятый — периодика,
# заведённая рутиной аккаунта, — в него не попал. Число в комментарии
# устаревает молча; расхождение SCANNERS с SCANNABLE ловит самопроверка.

SCANNABLE = {
    "actions": "GitHub Actions `schedule:` в .github/workflows/**",
    "launchd": "launchd: StartCalendarInterval / StartInterval в plist (мак)",
    "systemd": "systemd: OnCalendar= / OnUnitActiveSec= / OnUnitInactiveSec=",
    "routeros": "RouterOS /system scheduler в снятых экспортах устройств",
}

# Механизмы, до которых файловому скану хода нет ПО ПОСТРОЕНИЮ. Держать их
# в структуре, а не в прозе, — единственный способ, которым инструмент может
# честно отчитаться о собственной слепоте: итог --inventory печатает эту
# строку рядом с числами, и «задач столько-то» перестаёт читаться как «во
# всём портфеле столько-то».
# Подробности и замер — в докстроке; здесь одна строка на механизм, потому
# что её печатает итог прогона.
UNSCANNABLE = {
    "routine": (
        "Routine аккаунта Claude: расписание вне репозиториев, в git ни файла "
        "ни строки — и единственный механизм, умеющий исчезнуть без диффа "
        "(замер 2026-09-06, yinguo-lab#53). Список снимает владелец или "
        "интерактивная сессия, на ферме инструмента нет"
    ),
}

# Диспетчер сканеров. Ключи обязаны совпадать со SCANNABLE — это и есть
# инвариант, который проверяет самопроверка: добавили сканер и не объявили
# механизм (или наоборот) — прогон краснеет, а не молчит.
SCANNERS = {
    "actions": scan_actions,
    "launchd": scan_launchd,
    "systemd": scan_systemd,
    "routeros": scan_routeros,
}


def unscannable_notices() -> list[str]:
    """Строки об исключённых механизмах. Пусто быть не может — см. UNSCANNABLE."""
    return [f"{name}: {why}" for name, why in sorted(UNSCANNABLE.items())]


def scan_repo(root: Path) -> list[Task]:
    repo = root.name
    tasks: list[Task] = []
    for scan in SCANNERS.values():
        tasks += scan(root, repo)
    return tasks


def clones(where: Path) -> list[Path]:
    if (where / ".git").exists():
        return [where]
    return sorted(p for p in where.iterdir() if (p / ".git").exists())


# --- самопроверка: детектор обязан различать ------------------------------
_GOOD = """
on:
  schedule:
    - cron: "0 3 * * *"
jobs:
  bump:
    steps:
      - uses: ./actions/pin-discover
      - name: итог
        if: always()
        uses: ./actions/health-issue
"""
_MUTANTS = (
    ("сигнала нет вовсе", _GOOD.replace("        uses: ./actions/health-issue", "        run: echo")),
    ("сигнал не под always()", _GOOD.replace("        if: always()\n", "")),
)
# Не-периодический workflow с теми же шагами трогать нельзя: правило про
# расписание, а гард шире своего правила шумит на исправном репозитории.
_OUT_OF_SCOPE = _GOOD.replace("  schedule:\n    - cron: \"0 3 * * *\"", "  push:\n    branches: [main]")

# Сигнал в отдельной джобе с `needs:` (devsecops-pipeline-public#64). Шаг
# под always() во всех вариантах — меняется только `if` джобы: ровно там
# расходятся прежний код (его не читал), проверка подстрокой и правило.
_NEEDS_IF = "    if: always() && github.event_name == 'schedule'\n"
_NEEDS_GOOD = """
on:
  schedule:
    - cron: "0 3 * * *"
jobs:
  bump:
    steps:
      - uses: ./actions/pin-discover
  health:
    needs: bump
""" + _NEEDS_IF + """\
    steps:
      - if: always()
        uses: ./actions/health-issue
"""


def _with_job_if(cond: str) -> str:
    return _NEEDS_GOOD.replace(_NEEDS_IF, f"    if: {cond}\n" if cond else "")


# (метка, `if` джобы, причина). Вердикт обязан назвать и причину, и этот
# `if` как есть: текст, верный не для своей формы, читателя путает.
_NEEDS_MUTANTS = (
    ("джоба с needs: без if", "", FLAW_NO_IF),
    ("if джобы с needs: без always()", "github.event_name == 'schedule'", FLAW_NO_ALWAYS),
    # Подстрока `always()` в трёх ниже есть, а джобу при отказе они не запускают.
    ("!always() в if джобы", "${{ !always() }}", FLAW_NO_ALWAYS),
    ("always() строковым литералом", "github.event_name == 'always()'", FLAW_NO_ALWAYS),
    ("&& always() && внутри литерала", "github.event_name == 'a && always() && b'",
     FLAW_NO_ALWAYS),
    # `always()` на месте, но рядом статусная функция или `needs`: при отказе
    # `needs` джоба не исполнится (с `!always()` — никогда). Регистр и
    # пробел до скобки отмывкой не служат.
    ("always() && success()", "always() && success()", FLAW_NEIGHBOUR),
    ("always() && !failure()", "always() && !failure()", FLAW_NEIGHBOUR),
    ("always() && !always()", "always() && !always()", FLAW_NEIGHBOUR),
    ("always() && needs.<id>.result", "always() && needs.bump.result == 'success'",
     FLAW_NEIGHBOUR),
    ("always() && NEEDS.<id>.outputs", "always() && NEEDS.bump.outputs.changed == 'true'",
     FLAW_NEIGHBOUR),
    ("статусная функция в верхнем регистре, пробел до скобки", "always() && SUCCESS ()",
     FLAW_NEIGHBOUR),
    # Строка-шаблон: `always()` вне обёртки — литерал, неявный success() на месте.
    ("always() слева от обёртки ${{ }}", "always() && ${{ github.event_name == 'schedule' }}",
     FLAW_TEMPLATE),
    ("always() справа от обёртки ${{ }}",
     "${{ github.event_name == 'schedule' }} && always() && github.ref == 'refs/heads/main'",
     FLAW_TEMPLATE),
    # Строже GitHub намеренно (шапка): эти четыре GitHub при отказе `needs` исполнит.
    ("!cancelled() вместо always()", "${{ !cancelled() }}", FLAW_NO_ALWAYS),
    ("always() || …", "always() || github.event_name == 'schedule'", FLAW_NO_ALWAYS),
    ("always() в скобках", "(always() && github.event_name == 'schedule')", FLAW_NO_ALWAYS),
    ("always() && !cancelled()", "always() && !cancelled()", FLAW_NEIGHBOUR),
)
_NEEDS_CONTROLS = (
    ("always() && … в if джобы", "always() && github.event_name == 'schedule'"),
    ("if джобы в обёртке ${{ }}", "${{ always() && github.event_name == 'schedule' }}"),
    ("обёртка ${{ }} без пробелов", "${{always()}}"),
    ("обёртка ${{ }} блочным скаляром |",
     "|\n      ${{ always() && github.event_name == 'schedule' }}"),
    ("always() вторым операндом", "github.event_name == 'schedule' && always()"),
    ("always() между литералами",
     "github.event_name == 'schedule' && always() && github.ref == 'refs/heads/main'"),
    ("if: always() у джобы", "always()"),
    ("needs внутри имени входа — не контекст", "always() && inputs.needs_check != 'true'"),
)


def _with_runner(text: str, runs_on: str) -> str:
    return text.replace("  bump:\n", f"  bump:\n    runs-on: {runs_on}\n")


# Сторож на мёртвом раннере: сигнал есть и под always() — правило выше
# довольно, — но стоит он на self-hosted джобе, и дублёра на hosted нет.
_SELF_HOSTED_ONLY = _with_runner(_GOOD, "[self-hosted, polygon]")
_SELF_HOSTED_DICT = _with_runner(_GOOD, "{labels: [self-hosted, polygon]}")
# Контроли, на которых предупреждения быть не должно.
_HOSTED_ONLY = _with_runner(_GOOD, "ubuntu-latest")
_RUNNER_EXPR = _with_runner(_GOOD, "${{ inputs.runs-on }}")
_SELF_HOSTED_NO_SCHEDULE = _with_runner(_OUT_OF_SCOPE, "[self-hosted, polygon]")
_SELF_HOSTED_WITH_HOSTED_WATCHDOG = _SELF_HOSTED_ONLY + """\
  watchdog:
    runs-on: ubuntu-latest
    needs: bump
    if: always()
    steps:
      - if: always()
        uses: ./actions/health-issue
"""


def _with_watchdog_if(cond: str) -> str:
    """Тот же сторож с другим `if` джобы ("" — без `if`)."""
    return _SELF_HOSTED_WITH_HOSTED_WATCHDOG.replace(
        "    needs: bump\n    if: always()\n",
        "    needs: bump\n" + (f"    if: {cond}\n" if cond else ""))


_WATCHDOG_MUTANTS = (
    ("сигнал только на self-hosted (список меток)", _SELF_HOSTED_ONLY),
    ("сигнал только на self-hosted (labels: в словаре)", _SELF_HOSTED_DICT),
    # Сторож, чей `if` не по правилу: при отказе `bump` он пропускается
    # вместе с ней — дублёром он не является.
    ("hosted-сторож с needs: без if джобы", _with_watchdog_if("")),
    ("hosted-сторож: always() && success()", _with_watchdog_if("always() && success()")),
    ("hosted-сторож: always() && !failure()", _with_watchdog_if("always() && !failure()")),
    ("hosted-сторож: always() && needs.<id>.result",
     _with_watchdog_if("always() && needs.bump.result == 'success'")),
)
_WATCHDOG_CONTROLS = (
    ("раннер не указан", _GOOD),
    ("сигнал на hosted", _HOSTED_ONLY),
    ("runs-on выражением", _RUNNER_EXPR),
    ("self-hosted без расписания", _SELF_HOSTED_NO_SCHEDULE),
    ("self-hosted плюс hosted-дублёр", _SELF_HOSTED_WITH_HOSTED_WATCHDOG),
    ("hosted-дублёр с always() && событием",
     _with_watchdog_if("always() && github.event_name == 'schedule'")),
    ("сигнала нет вовсе — это ошибка, не предупреждение", _MUTANTS[0][1]),
)

_TIMER_PERIODIC = "[Timer]\nOnBootSec=90\nOnUnitInactiveSec=15min\n"
_TIMER_ONESHOT = "[Timer]\nOnBootSec=90\n"
_DISARMED_WF = """
on:
  workflow_dispatch:
  # schedule:
  #   - cron: "0 3 * * *"
jobs:
  x:
    steps:
      - run: echo
"""
_BROKEN_WF = "on: [\n  незакрытая последовательность\n"
_SCRIPT_LIVE = "#!/bin/sh\n. lib/heartbeat.sh\nheartbeat_send ran\n"
_SCRIPT_COMMENT_ONLY = "#!/bin/sh\n# TODO: прикрутить heartbeat\necho ok\n"
_SCRIPT_BLIND = "#!/bin/sh\necho ok\n"
_SCRIPT_MARK = "#!/bin/sh\n. scripts/lib/liveness.sh\nliveness_mark demo\n"
_SCRIPT_MARK_COMMENT = "#!/bin/sh\n# liveness_mark demo — когда-нибудь\necho ok\n"
_SERVICE_DECLARED = (
    "[Service]\nExecStart=__REPO_PATH__/scripts/live.sh\n"
    "X-Liveness=artifact /var/lib/x/x.tsv; проверка: x.sh --check\n"
)
_PYPROJECT_SCRIPTS = "[project]\nname = \"foo\"\n\n[project.scripts]\nfoo-svc = \"foo_pkg.cli:main\"\n"
_SERVICE_VENV = "[Service]\nExecStart=/opt/foo/venv/bin/foo-svc sync\n"


def _fixture_repo(tmp: Path) -> Path:
    """Синтетический клон: по одной задаче каждого механизма, все живые."""
    root = tmp / "fixture-repo"
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/nightly.yml").write_text(_GOOD, encoding="utf-8")
    (root / "scripts/systemd").mkdir(parents=True)
    (root / "scripts/live.sh").write_text(_SCRIPT_LIVE, encoding="utf-8")
    (root / "scripts/systemd/t.timer").write_text(_TIMER_PERIODIC, encoding="utf-8")
    (root / "scripts/systemd/t.service").write_text(
        "[Service]\nExecStart=__REPO_PATH__/scripts/live.sh\n", encoding="utf-8")
    (root / "scripts/launchd").mkdir(parents=True)
    plistlib.dump(
        {"Label": "fx", "StartInterval": 900,
         "ProgramArguments": ["/bin/bash", "-lc", 'cd "__REPO_PATH__" && scripts/live.sh']},
        (root / "scripts/launchd/fx.plist").open("wb"),
    )
    (root / ".git").mkdir()
    return root


def selftest() -> list:
    import shutil
    import tempfile

    problems = []
    if check_workflow("фикстура", _GOOD):
        problems.append("самопроверка: детектор нашёл нарушение в ЗДОРОВОМ workflow")
    if check_workflow("фикстура", _OUT_OF_SCOPE):
        problems.append("самопроверка: детектор трогает workflow БЕЗ расписания")
    for label, text in _MUTANTS:
        if not check_workflow("фикстура", text):
            problems.append(f"самопроверка: мутант «{label}» не пойман — детектор слеп")

    # Сигнал в джобе с `needs:`: мутант обязан упасть именно на `if` джобы
    # (текст про `needs:`) со своей причиной и этим `if`, контроль — пройти.
    for label, cond, why in _NEEDS_MUTANTS:
        found = [p for p in check_workflow("фикстура", _with_job_if(cond)) if "`needs:`" in p]
        if not found:
            problems.append(f"самопроверка: мутант «{label}» не пойман — джоба "
                            "с needs: зачтена по always() шага")
        elif why not in found[0] or (cond and f"`if: {cond}`" not in found[0]):
            problems.append(f"самопроверка: мутант «{label}» пойман, но вердикт "
                            f"не тот: ждали «{why}» и его `if` — {found[0]}")
    for label, cond in _NEEDS_CONTROLS:
        if check_workflow("фикстура", _with_job_if(cond)):
            problems.append(f"самопроверка: нарушение на контроле «{label}» — "
                            "детектор шире своего правила")

    # Сторож на мёртвом раннере: предупреждение обязано сработать на
    # self-hosted-сигнале без дублёра и промолчать на каждом контроле.
    # Блокирующее правило при этом мутанта не трогает: сигнал у него есть.
    for label, text in _WATCHDOG_MUTANTS:
        if not watchdog_warnings("фикстура", text):
            problems.append(f"самопроверка: мутант «{label}» не предупреждён — "
                            "детектор дублёра слеп")
        if check_workflow("фикстура", text):
            problems.append(f"самопроверка: мутант «{label}» принят за нарушение "
                            "блокирующего правила — warning стал ошибкой")
    for label, text in _WATCHDOG_CONTROLS:
        if watchdog_warnings("фикстура", text):
            problems.append(f"самопроверка: предупреждение на контроле «{label}» — "
                            "детектор дублёра шире своего правила")

    # Таксономия механизмов обязана совпадать с набором сканеров. Без этого
    # контроля можно добавить сканер и не объявить механизм (итог соврёт об
    # области охвата) либо объявить механизм, которого никто не сканирует
    # (итог соврёт в обратную сторону) — и то и другое молча.
    if set(SCANNERS) != set(SCANNABLE):
        problems.append(
            "самопроверка: сканеры и объявленные механизмы разошлись — "
            f"только в SCANNERS {sorted(set(SCANNERS) - set(SCANNABLE))}, "
            f"только в SCANNABLE {sorted(set(SCANNABLE) - set(SCANNERS))}")
    # Механизм не может быть одновременно сканируемым и несканируемым.
    if set(SCANNABLE) & set(UNSCANNABLE):
        problems.append(
            "самопроверка: механизм объявлен и сканируемым, и нет — "
            f"{sorted(set(SCANNABLE) & set(UNSCANNABLE))}")
    # Пустой UNSCANNABLE вернул бы инструмент к прежнему дефекту: итог снова
    # читался бы как полный обход портфеля.
    if not unscannable_notices():
        problems.append(
            "самопроверка: непокрытые механизмы не объявлены — итог "
            "--inventory снова читается как полный обход портфеля")

    tmp = Path(tempfile.mkdtemp(prefix="sched-liveness-"))
    try:
        root = _fixture_repo(tmp)
        tasks = scan_repo(root)
        by_mech = {t.mechanism for t in tasks}
        # Сканер, выдающий механизм вне таксономии, ломает смысл итога.
        if not by_mech <= set(SCANNABLE):
            problems.append(
                "самопроверка: сканер выдал необъявленный механизм — "
                f"{sorted(by_mech - set(SCANNABLE))}")
        if by_mech != {"actions", "systemd", "launchd"}:
            problems.append(
                f"самопроверка: механизмы фикстуры собрались как {sorted(by_mech)}")
        if any(t.status != OK for t in tasks):
            problems.append(
                "самопроверка: в ЗДОРОВОЙ фикстуре найдены задачи без признака "
                f"живости — {[(t.name, t.status) for t in tasks if t.status != OK]}")

        # Мутант: скрипт лишь упоминает пульс в комментарии.
        (root / "scripts/live.sh").write_text(_SCRIPT_COMMENT_ONLY, encoding="utf-8")
        if all(t.status == OK for t in scan_repo(root) if t.mechanism != "actions"):
            problems.append("самопроверка: комментарий про heartbeat зачтён за сигнал")

        # Мутант: сигнала нет вовсе.
        (root / "scripts/live.sh").write_text(_SCRIPT_BLIND, encoding="utf-8")
        if not [t for t in scan_repo(root) if t.status == GAP and t.mechanism != "actions"]:
            problems.append("самопроверка: скрипт без признака живости не пойман")

        # Контроль «вне области»: одиночный выстрел при загрузке — не периодика.
        (root / "scripts/systemd/t.timer").write_text(_TIMER_ONESHOT, encoding="utf-8")
        if [t for t in scan_repo(root) if t.mechanism == "systemd"]:
            problems.append("самопроверка: таймер без периодичности принят за периодику")

        # Контроль «не смогли проверить» отличается от «сигнала нет».
        (root / "scripts/systemd/t.timer").write_text(_TIMER_PERIODIC, encoding="utf-8")
        (root / "scripts/systemd/t.service").write_text(
            "[Service]\nExecStart=/opt/чужое/неведомое.sh\n", encoding="utf-8")
        st = [t.status for t in scan_repo(root) if t.mechanism == "systemd"]
        if st != [UNKNOWN]:
            problems.append(
                f"самопроверка: неразрешимая цель дала {st}, а обязана дать unknown")

        # Три состояния Actions, которые снаружи одинаково выглядят как
        # «периодической задачи здесь нет».
        wf = root / ".github" / "workflows"
        (wf / "disarmed.yml").write_text(_DISARMED_WF, encoding="utf-8")
        (wf / "broken.yml").write_text(_BROKEN_WF, encoding="utf-8")
        (wf / "eventful.yml").write_text(_OUT_OF_SCOPE, encoding="utf-8")
        acts = {t.where: t.status for t in scan_repo(root) if t.mechanism == "actions"}
        if acts.get(".github/workflows/disarmed.yml") != DISARMED:
            problems.append("самопроверка: снятое комментарием расписание не опознано")
        if acts.get(".github/workflows/broken.yml") != UNKNOWN:
            problems.append("самопроверка: неразбираемый манифест исчез из инвентаря")
        if ".github/workflows/eventful.yml" in acts:
            problems.append("самопроверка: workflow без расписания попал в инвентарь")
        (wf / "farm.yml").write_text(_SELF_HOSTED_ONLY, encoding="utf-8")
        farm = [t for t in scan_repo(root) if t.where == ".github/workflows/farm.yml"]
        if [t.status for t in farm] != [OK] or "hosted-дублёра нет" not in farm[0].signal:
            problems.append(
                "самопроверка: инвентарь не называет сигнал на self-hosted без "
                f"дублёра — {[(t.status, t.signal) for t in farm]}")
        for name in ("disarmed.yml", "broken.yml", "eventful.yml", "farm.yml"):
            (wf / name).unlink()

        # Третья форма в скрипте: liveness_mark — сигнал; в комментарии — нет.
        (root / "scripts/systemd/t.service").write_text(
            "[Service]\nExecStart=__REPO_PATH__/scripts/live.sh\n", encoding="utf-8")
        (root / "scripts/live.sh").write_text(_SCRIPT_MARK, encoding="utf-8")
        sysd = [t for t in scan_repo(root) if t.mechanism == "systemd"]
        if [t.status for t in sysd] != [OK] or "liveness" not in sysd[0].signal:
            problems.append(
                f"самопроверка: liveness_mark не зачтён за сигнал — {[(t.status, t.signal) for t in sysd]}")
        (root / "scripts/live.sh").write_text(_SCRIPT_MARK_COMMENT, encoding="utf-8")
        if [t.status for t in scan_repo(root) if t.mechanism == "systemd"] != [GAP]:
            problems.append("самопроверка: liveness_mark в комментарии зачтён за сигнал")

        # Объявленный признак: слепой скрипт + X-Liveness в юните — есть;
        # без ключа — нет. Ключ в комментарии — не объявление.
        (root / "scripts/live.sh").write_text(_SCRIPT_BLIND, encoding="utf-8")
        (root / "scripts/systemd/t.service").write_text(_SERVICE_DECLARED, encoding="utf-8")
        sysd = [t for t in scan_repo(root) if t.mechanism == "systemd"]
        if [t.status for t in sysd] != [OK] or not sysd[0].signal.startswith("объявлен: artifact"):
            problems.append(
                f"самопроверка: X-Liveness в юните не зачтён — {[(t.status, t.signal) for t in sysd]}")
        (root / "scripts/systemd/t.service").write_text(
            _SERVICE_DECLARED.replace("X-Liveness=", "# X-Liveness="), encoding="utf-8")
        if [t.status for t in scan_repo(root) if t.mechanism == "systemd"] != [GAP]:
            problems.append("самопроверка: закомментированный X-Liveness зачтён за объявление")

        # Консольный скрипт venv → модуль из [project.scripts]: живой модуль —
        # есть, слепой — нет, без записи — не смогли проверить.
        (root / "svc/foo_pkg").mkdir(parents=True)
        (root / "svc/pyproject.toml").write_text(_PYPROJECT_SCRIPTS, encoding="utf-8")
        (root / "svc/foo_pkg/cli.py").write_text("def main():\n    heartbeat_send()\n", encoding="utf-8")
        (root / "scripts/systemd/t.service").write_text(_SERVICE_VENV, encoding="utf-8")
        sysd = [t for t in scan_repo(root) if t.mechanism == "systemd"]
        if [t.status for t in sysd] != [OK]:
            problems.append(
                f"самопроверка: venv/bin/<скрипт> не разрешён через pyproject — {[(t.status, t.signal) for t in sysd]}")
        (root / "svc/foo_pkg/cli.py").write_text("def main():\n    pass\n", encoding="utf-8")
        if [t.status for t in scan_repo(root) if t.mechanism == "systemd"] != [GAP]:
            problems.append("самопроверка: слепой модуль venv-скрипта не пойман")
        (root / "svc/pyproject.toml").write_text("[project]\nname = \"foo\"\n", encoding="utf-8")
        if [t.status for t in scan_repo(root) if t.mechanism == "systemd"] != [UNKNOWN]:
            problems.append("самопроверка: venv-скрипт без записи в pyproject дал не unknown")

        # Разоружённый plist: Disabled=true — снято (даже при слепой цели);
        # Disabled=false — обычная задача, и слепая цель остаётся дырой.
        for fname, disabled in (("off.plist", True), ("on.plist", False)):
            plistlib.dump(
                {"Label": fname, "StartInterval": 900, "Disabled": disabled,
                 "ProgramArguments": ["/bin/bash", "-lc", 'cd "__REPO_PATH__" && scripts/live.sh']},
                (root / "scripts/launchd" / fname).open("wb"),
            )
        pl = {t.name: t for t in scan_repo(root) if t.mechanism == "launchd"}
        if pl["off.plist"].status != DISARMED or "Disabled" not in pl["off.plist"].schedule:
            problems.append(
                f"самопроверка: plist с Disabled=true не опознан как снятый — {pl['off.plist']}")
        if pl["on.plist"].status != GAP:
            problems.append(
                f"самопроверка: plist с Disabled=false прочитан как {pl['on.plist'].status}, а не gap")
        # Объявление в plist — та же третья форма.
        plistlib.dump(
            {"Label": "decl.plist", "StartInterval": 900, "X-Liveness": "artifact /tmp/x",
             "ProgramArguments": ["/bin/bash", "-lc", 'cd "__REPO_PATH__" && scripts/live.sh']},
            (root / "scripts/launchd/decl.plist").open("wb"),
        )
        pl = {t.name: t for t in scan_repo(root) if t.mechanism == "launchd"}
        if pl["decl.plist"].status != OK or not pl["decl.plist"].signal.startswith("объявлен"):
            problems.append(f"самопроверка: X-Liveness в plist не зачтён — {pl['decl.plist']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


# --- режимы ----------------------------------------------------------------

def run_repo_mode(root: Path, strict: bool = False) -> int:
    """Блокирующее правило. Контракт прежний: Actions, exit 0/1.

    Предупреждения (сторож на мёртвом раннере) печатаются всегда и rc не
    меняют; `strict=True` делает их ошибкой."""
    files = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    if not files:
        # Ноль манифестов — «не смогли проверить», а не «чисто»: ровно так
        # выглядел бы промах путём, и вердикт был бы зелёным.
        print(f"::error::манифестов не найдено под {root}/.github/workflows",
              file=sys.stderr)
        return 1

    problems, warnings, scheduled = [], [], 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(root))
        try:
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            doc = {}
        if isinstance(doc, dict) and "schedule" in triggers(doc):
            scheduled += 1
        problems += check_workflow(rel, text)
        warnings += watchdog_warnings(rel, text)

    if strict:
        problems += warnings
        warnings = []
    for w in warnings:
        print(f"::warning::{w}", file=sys.stderr)

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: нарушений {len(problems)}", file=sys.stderr)
        return 1

    # Число печатается намеренно: «0 периодических» — это тоже ответ, и
    # читатель должен увидеть его, а не только слово OK. Предупреждения —
    # тоже числом: «OK» при двух немых сторожах без этой оговорки читался
    # бы как «всё под наблюдением».
    print(f"OK: периодических workflow {scheduled} из {len(files)}, "
          f"у каждого health-issue под always()"
          + (f"; сторожей только на self-hosted без hosted-дублёра "
             f"{len(warnings)} (warning, не ошибка; --strict делает ошибкой)"
             if warnings else ""))

    # Периодика этого же репозитория, заведённая не через Actions, — справкой,
    # не вердиктом: блокирующее правило про Actions, и расширять его молча
    # значит красить чужой репозиторий своим прогоном.
    other = [t for t in scan_repo(root) if t.mechanism != "actions"]
    if other:
        gaps = [t for t in other if t.status not in (OK, DISARMED)]
        print(f"::notice::вне Actions в этом репозитории периодических задач "
              f"{len(other)}, без признака живости {len(gaps)} — "
              f"сквозной вердикт: --inventory")
    return 0


def run_inventory(where: Path, as_json: bool) -> int:
    roots = clones(where)
    if not roots:
        print(f"::error::под {where} не нашлось ни одного клона (.git) — "
              f"проверить нечего", file=sys.stderr)
        return 2

    tasks: list[Task] = []
    for root in roots:
        tasks += scan_repo(root)

    if as_json:
        print(json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2))
    else:
        width = max((len(f"{t.repo}/{t.where}") for t in tasks), default=20)
        mark = {OK: "есть", GAP: "НЕТ", UNKNOWN: "?", DISARMED: "снято"}
        for t in sorted(tasks, key=lambda t: (t.mechanism, t.repo, t.where)):
            print(f"{t.mechanism:<9} {t.repo + '/' + t.where:<{width}} "
                  f"{mark[t.status]:<5} {t.schedule}"
                  + (f"  [{t.signal}]" if t.signal else ""))

    gaps = [t for t in tasks if t.status == GAP]
    unknown = [t for t in tasks if t.status == UNKNOWN]
    disarmed = [t for t in tasks if t.status == DISARMED]
    live = len(tasks) - len(gaps) - len(unknown) - len(disarmed)
    print(f"\nитого: клонов {len(roots)}, периодических задач "
          f"{len(tasks) - len(disarmed)} — признак есть у {live}, нет у "
          f"{len(gaps)}, не смогли проверить {len(unknown)}; "
          f"разоружённых расписаний {len(disarmed)}", file=sys.stderr)
    # Область охвата печатается рядом с числами, а не в --help. Итог без неё
    # читается как «во всём портфеле столько-то периодических задач», и
    # именно так пятый механизм оставался невидимым: его отсутствие в
    # таблице неотличимо от «таких задач нет».
    print(f"::notice::просканированы механизмы: {', '.join(sorted(SCANNABLE))}",
          file=sys.stderr)
    for line in unscannable_notices():
        print(f"::notice::НЕ просканирован и не может быть — {line}",
              file=sys.stderr)
    for t in disarmed:
        print(f"::notice::{t.repo}/{t.where}: расписание {t.schedule} — "
              f"задача не исполняется, признак живости к ней неприменим",
              file=sys.stderr)
    if gaps:
        return 1
    # Ноль находок при неразрешённых целях — не «чисто»: часть задач просто
    # не проверена, и молчание об этом было бы тем же дефектом, что ищет #19.
    return 2 if unknown else 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?", default=".",
                    help="корень репозитория (режим блокирующего правила)")
    ap.add_argument("--inventory", metavar="КАТАЛОГ", nargs="?", const=".",
                    help="сквозной инвентарь: клон или каталог клонов")
    ap.add_argument("--json", action="store_true", help="машинный вывод инвентаря")
    ap.add_argument("--strict", action="store_true",
                    help="сторож только на self-hosted без hosted-дублёра — "
                         "ошибка (rc 1), а не предупреждение")
    args = ap.parse_args()

    blind = selftest()
    if blind:
        for p in blind:
            print(f"::error::{p}", file=sys.stderr)
        return 1

    if args.inventory is not None:
        return run_inventory(Path(args.inventory).resolve(), args.json)
    return run_repo_mode(Path(args.root).resolve(), strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
