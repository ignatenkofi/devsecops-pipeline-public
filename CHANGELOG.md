# Changelog

Потребители живут на floating-теге `v1` (двигается только после зелёного
selftest). Формат — [Keep a Changelog], версии — semver.

## [Unreleased]

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
