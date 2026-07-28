# Changelog

Потребители живут на floating-теге `v1` (двигается только после зелёного
selftest). Формат — [Keep a Changelog], версии — semver.

## [Unreleased]

### Added
- **Nightly-самообновление пинов** (`nightly-bump.yml`, приватный #8):
  ночной workflow сверяет пины gitleaks/semgrep/osv-scanner с апстримом
  (GitHub Releases / PyPI), валидирует новые версии на негатив-фикстурах
  selftest ДО правки пинов и открывает/обновляет PR `bump/tools` с таблицей
  «было → стало». Политика: минорные — пачкой, мажорные — с разбором
  changelog (README «Версионирование»).
- **M1-правила offline-набора semgrep** (приватный #8): intra-file path
  traversal (`py-open-request-path`, `js-fs-request-path`) и SSRF
  (`py-requests-request-url`, `js-fetch-request-url`) — только прямой поток
  «request-параметр → sink» одним выражением, без претензии на taint-анализ
  («Границы честности»). Selftest дополнен негатив-фикстурами на все четыре
  правила и позитивом на безопасных аналогах (allowlist-маппинг, статический
  URL) — ложных срабатываний нет.
- Класс `vuln-demo` — намеренно уязвимое демо (sqst-vulnerable-app): все
  лёгкие стадии advisory (в т.ч. secrets), витрина находок без блокировок.
  Санкционированное исключение из инварианта «secrets = B» в selftest.

## [1.0.0] — 2026-07-22

### Added
- Публичный компаньон приватного `devsecops-pipeline`: лёгкие стадии как
  переиспользуемый workflow для публичных репозиториев (обход ограничения
  GitHub public→private reusable workflow).
- `pipeline-light.yml` (workflow_call, `repo-class` + `skip-stages` /
  `extra-stages`) — точка входа.
- Composite actions: `profile-resolve`, `gitleaks`, `semgrep` (+offline-rules),
  `osv-scanner`, `sarif-report` (перенесены из приватной части, тот же набор).
- Профили `library` / `service` / `docs-shelf` / `course-content`.
- `selftest.yml`: валидация профилей + позитив/негатив-прогоны стадий.
- Учебный слой: `docs/LESSON.md` (разбор shift-left и границы taint-анализа),
  `examples/security.yml`, образовательный README. MIT-лицензия.
