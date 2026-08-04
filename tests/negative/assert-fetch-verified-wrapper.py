#!/usr/bin/env python3
"""Гард на ОБЁРТКУ fetch-verified, а не на её скрипт.

Негатив-фикстура `assert-fetch-verified.sh` гоняет только
`fetch_verified.sh`. Сборка аргументов в `action.yml` не проверялась ничем,
и дефект там жил ровно поэтому: строка `[ -n "$FV_EXTRACT_ALL" ] && ARGS+=…`
проверяла НЕПУСТОТУ, а вход объявлен булевым. Входы Actions приходят
строками, поэтому естественное `with: {extract-all: false}` включало режим
там, где потребитель его выключает — и вместо запрошенного `--member`
получалась распаковка архива целиком.

Детектор близнецов такое не ловит принципиально: он сверяет `.py`/`.sh`, а
манифесты у репозиториев сознательно разные.

Проверяется:
  1. `extract-all: false` НЕ включает режим (и не ломает --member);
  2. `extract-all: true` включает;
  3. пустое значение равно false;
  4. мусорное значение — отказ rc=2, а не «что-то из двух»;
  5. дефолт входа в манифесте — булев литерал, а не пустая строка.

Самопроверка: перед прогоном по манифесту тест исполняет ЗАВЕДОМО плохую
версию блока (проверка на непустоту) и падает, если та прошла.

Использование:  assert-fetch-verified-wrapper.py [корень репозитория]
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("нужен pyyaml: python3 -m pip install pyyaml")

BAD_BLOCK = """
set -euo pipefail
ARGS=(--url "$FV_URL" --dest "$FV_DEST")
[ -n "$FV_SUMS" ]   && ARGS+=(--sums-url "$FV_SUMS")
[ -n "$FV_SHA256" ] && ARGS+=(--sha256 "$FV_SHA256")
[ -n "$FV_MEMBER" ] && ARGS+=(--member "$FV_MEMBER")
[ -n "$FV_OUTPUT" ] && ARGS+=(--output "$FV_OUTPUT")
[ -n "$FV_EXTRACT_ALL" ] && ARGS+=(--extract-all)
bash "${GITHUB_ACTION_PATH}/fetch_verified.sh" "${ARGS[@]}"
"""


def build_fixture(work: Path) -> tuple[str, str]:
    """tar.gz с членом `tool` и вложенным каталогом + файл сумм; оба file://."""
    src = work / "src"
    src.mkdir()
    (src / "tool").write_text("#!/bin/sh\necho i-am-the-tool\n", encoding="utf-8")
    nested = src / "nested-dir"
    nested.mkdir()
    (nested / "inner").write_text("вложенный\n", encoding="utf-8")

    archive = work / "asset.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src / "tool", arcname="tool")
        tar.add(nested, arcname="nested-dir")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sums = work / "SUMS"
    sums.write_text(f"{digest}  asset.tar.gz\n", encoding="utf-8")
    return f"file://{archive}", f"file://{sums}"


def run_block(block: str, action_dir: Path, work: Path, dest: Path, **env_over):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "GITHUB_ACTION_PATH": str(action_dir),
        "FV_URL": env_over.pop("url"),
        "FV_SUMS": env_over.pop("sums", ""),
        "FV_SHA256": "",
        "FV_DEST": str(dest),
        "FV_MEMBER": "",
        "FV_OUTPUT": "",
        "FV_EXTRACT_ALL": "",
    }
    env.update({f"FV_{k.upper()}": v for k, v in env_over.items()})
    script = work / "block.sh"
    script.write_text(block, encoding="utf-8")
    proc = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


def check(block: str, action_dir: Path) -> list[str]:
    """Возвращает список нарушений контракта. Пусто — контракт соблюдён."""
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        url, sums = build_fixture(work)

        # 1. false не включает extract-all и не мешает --member
        d = work / "d1"
        rc, log = run_block(block, action_dir, work, d, url=url, sums=sums,
                            member="tool", extract_all="false")
        if rc != 0:
            problems.append(f"extract-all=false с --member отвергнут (rc={rc}): {log.strip()[:200]}")
        elif (d / "nested-dir").exists():
            problems.append("extract-all=false распаковал архив целиком — 'false' сработал как 'true'")
        elif not (d / "tool").is_file():
            problems.append("extract-all=false: член архива не распакован")

        # 2. true включает
        d = work / "d2"
        rc, log = run_block(block, action_dir, work, d, url=url, sums=sums,
                            extract_all="true")
        if rc != 0 or not (d / "nested-dir").exists():
            problems.append(f"extract-all=true не распаковал целиком (rc={rc}): {log.strip()[:200]}")

        # 3. пустое равно false
        d = work / "d3"
        rc, _ = run_block(block, action_dir, work, d, url=url, sums=sums,
                          member="tool", extract_all="")
        if rc != 0 or (d / "nested-dir").exists():
            problems.append("пустое extract-all не равно false")

        # 4. мусор — отказ, а не молчаливый выбор одного из двух
        d = work / "d4"
        rc, _ = run_block(block, action_dir, work, d, url=url, sums=sums,
                          member="tool", extract_all="maybe")
        if rc != 2:
            problems.append(f"мусорное extract-all='maybe' дало rc={rc}, ожидался 2")

    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    action_dir = root / "actions" / "fetch-verified"
    manifest = action_dir / "action.yml"
    if not manifest.is_file():
        print(f"::error::не найден {manifest}", file=sys.stderr)
        return 1

    # Самопроверка: заведомо плохой блок обязан быть пойман.
    # Плохой блок ломается в случае 1, но НЕ одним и тем же образом: с
    # --member он даёт rc=2 («задано 2 режима»), без него — тихую
    # распаковку целиком. Поэтому критерий — «случай 1 сработал», а не
    # конкретный текст: первая версия этого условия ждала второй симптом
    # и объявила самопроверку провалившейся на верно пойманном дефекте.
    bad = check(BAD_BLOCK, action_dir)
    if not any(p.startswith("extract-all=false") for p in bad):
        print("::error::САМОПРОВЕРКА ПРОВАЛЕНА: проверка на непустоту прошла как "
              f"корректная. Нашла: {bad or 'ничего'}", file=sys.stderr)
        return 1

    doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    steps = doc["runs"]["steps"]
    blocks = [s["run"] for s in steps if isinstance(s, dict) and "run" in s]
    if len(blocks) != 1:
        print(f"::error::ожидался ровно один run-блок, найдено {len(blocks)}", file=sys.stderr)
        return 1

    default = str(doc["inputs"]["extract-all"].get("default", ""))
    problems = check(blocks[0], action_dir)
    if default not in ("false", "true"):
        problems.append(
            f"дефолт extract-all — {default!r}; булев вход обязан объявлять "
            f"булев литерал, иначе манифест не документирует контракт")

    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nFAIL: нарушений контракта обёртки {len(problems)}", file=sys.stderr)
        return 1

    print("OK: обёртка fetch-verified соблюдает булев контракт extract-all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
