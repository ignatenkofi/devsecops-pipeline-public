#!/usr/bin/env python3
"""Пин semgrep конвейера равен semgrep, запечённому в раннер (A-devsecops-02).

Пин `actions/semgrep/action.yml` (`inputs.version.default`) зеркалится в
образе фермы `polygon-iac` (`ansible/roles/base/tasks/main.yml`,
`semgrep==…`): на noble системный pip закрыт PEP 668, и при расхождении
стадия `sast-semgrep` у КАЖДОГО потребителя на ферме падает, а не
доустанавливает версию (devsecops-pipeline#62 — полсуток красных прогонов).

До 2026-10-02 сверка была ручной — две команды `gh api` в
`docs/runbooks/release.md` приватного репо — и сравнивала пин с РОЛЬЮ, а не
с тем, что реально запечено: дрейф `main` публичного 1.177.0 против роли
1.176.1 прожил 14.09–24.09 без сигнала, а 2026-10-02 обе команды печатали
1.178.0 при активном шаблоне v6 с 1.176.1. Истина о запечённом — только у
самого раннера фермы: `semgrep --version` в его PATH. Этот скрипт и
сравнивает пин с ним; гоняет его ночная джоба приватного репо на ферме и
заводит health-issue на расхождении.

Коды возврата — контракт портфеля «чисто / нарушение / не смог проверить»:
    0 — все пины равны запечённой версии;
    1 — хотя бы один пин отличается (или пин не разобрался: это тоже
        расхождение, которое уронит потребителей, а не «неизвестно»);
    2 — запечённую версию взять неоткуда (semgrep не в PATH при --probe,
        --baked не задан) — НЕ «чисто».

Использование:
    assert-semgrep-pin.py [--probe | --baked VER] [--github-output] <action.yml>...
    assert-semgrep-pin.py --selftest

Несколько action.yml — несколько пинов с одной запечённой версией: ночная
джоба даёт и `v1` (то, что исполняют потребители сейчас), и `main` (то,
что приедет следующим релизом) — расхождение второго предупреждает ДО
переезда тега. `--github-output` дописывает `state=open|close`, `baked=`
и `verdict=` в $GITHUB_OUTPUT для health-issue.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# default пина — строго в блоке inputs.version; `version:` встречается и в
# прозе description, поэтому ловится именно пара «version: … default:».
PIN_RE = re.compile(
    r"^  version:\n(?:^    [^\n]*\n)*?^    default:\s*\"?([0-9][0-9A-Za-z.\-]*)\"?\s*$",
    re.M,
)
VER_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")


def read_pin(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = PIN_RE.search(text)
    return m.group(1) if m else None


def probe_baked() -> str | None:
    if shutil.which("semgrep") is None:
        return None
    try:
        out = subprocess.run(
            ["semgrep", "--version"], capture_output=True, text=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = VER_RE.search(out)
    return m.group(1) if m else None


def verdict(pins: dict[str, str | None], baked: str | None) -> tuple[int, list[str]]:
    """(rc, строки отчёта). Чистая функция — её и гоняет --selftest."""
    lines = []
    if baked is None:
        lines.append("запечённая версия semgrep НЕИЗВЕСТНА (semgrep не в PATH / --baked не задан)")
        for label, pin in pins.items():
            lines.append(f"- {label}: пин {pin or '<не разобран>'} — сверить нечем")
        return 2, lines
    rc = 0
    lines.append(f"запечённый semgrep: {baked}")
    for label, pin in pins.items():
        if pin is None:
            rc = 1
            lines.append(f"- {label}: пин НЕ РАЗОБРАН (inputs.version.default) — потребители упадут на установке")
        elif pin == baked:
            lines.append(f"- {label}: пин {pin} = запечённому")
        else:
            rc = 1
            lines.append(f"- {label}: пин {pin} ≠ запечённому {baked} — sast-semgrep на ферме упадёт (#62)")
    return rc, lines


def selftest() -> int:
    fails = []

    def check(name, cond):
        print(("  ok: " if cond else "  FAIL: ") + name)
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as d:
        good = Path(d, "good.yml")
        good.write_text(
            "name: semgrep\ndescription: >-\n  версия пинована, version: в прозе\n"
            "inputs:\n  sarif-path:\n    required: false\n    default: sarif/semgrep.sarif\n"
            "  version:\n    required: false\n    default: \"1.179.0\"\n"
            "  severity-floor:\n    default: \"ERROR\"\n",
            encoding="utf-8",
        )
        check("пин читается из inputs.version.default, не из прозы", read_pin(good) == "1.179.0")
        nopin = Path(d, "nopin.yml")
        nopin.write_text("name: semgrep\ninputs:\n  version:\n    required: false\n", encoding="utf-8")
        check("default отсутствует → пин None", read_pin(nopin) is None)
        check("файла нет → None, не исключение", read_pin(Path(d, "absent.yml")) is None)

    rc, _ = verdict({"v1": "1.179.0"}, "1.179.0")
    check("равны → rc 0", rc == 0)
    rc, lines = verdict({"v1": "1.178.0", "main": "1.179.0"}, "1.176.1")
    check("оба отличаются → rc 1, оба названы", rc == 1 and sum("≠" in l for l in lines) == 2)
    rc, lines = verdict({"v1": "1.176.1", "main": "1.179.0"}, "1.176.1")
    check("v1 равен, main ушёл вперёд → rc 1 (сигнал ДО переезда тега)", rc == 1 and "main" in "".join(l for l in lines if "≠" in l))
    rc, _ = verdict({"v1": None}, "1.176.1")
    check("пин не разобран → rc 1, не 2: это уронит потребителей", rc == 1)
    rc, lines = verdict({"v1": "1.179.0"}, None)
    check("запечённая неизвестна → rc 2, не «чисто»", rc == 2 and "НЕИЗВЕСТНА" in lines[0])
    check("probe: версия вытаскивается из «1.179.0» и из «semgrep 1.179.0»",
          VER_RE.search("semgrep 1.179.0\n") is not None and VER_RE.search("1.179.0").group(1) == "1.179.0")

    print("OK: selftest assert-semgrep-pin" if not fails else f"ПРОВАЛ: {len(fails)} фикстур", file=sys.stderr if fails else sys.stdout)
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    baked = None
    probe = False
    gh_out = False
    files: list[str] = []
    it = iter(argv)
    for a in it:
        if a == "--probe":
            probe = True
        elif a == "--baked":
            baked = next(it, None)
        elif a == "--github-output":
            gh_out = True
        else:
            files.append(a)
    if not files:
        print(__doc__, file=sys.stderr)
        return 2
    if probe:
        baked = probe_baked()
    pins = {f: read_pin(Path(f)) for f in files}
    rc, lines = verdict(pins, baked)
    print("\n".join(lines))
    if gh_out and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"state={'close' if rc == 0 else 'open'}\n")
            fh.write(f"baked={baked or ''}\n")
            delim = "verdict-" + os.urandom(4).hex()
            fh.write(f"verdict<<{delim}\n" + "\n".join(lines) + f"\n{delim}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
