# devsecops-pipeline-public

Публичный компаньон приватного [devsecops-pipeline](https://github.com/ignatenkofi/devsecops-pipeline):
переиспользуемый GitHub Actions workflow с **лёгкими security-стадиями** для
публичных репозиториев. Подключается одной парой строк, копий по репозиториям
не заводит.

Репозиторий публичный сознательно и служит двойную роль:
1. **инструмент** — публичные репо портфеля вызывают его и получают тот же
   набор сканов, что и приватные;
2. **учебный экспонат** — живой пример конвейера «shift-left security» для
   занятий по кибербезопасности (см. [`docs/LESSON.md`](docs/LESSON.md)).

## Быстрый старт

Добавьте в свой репозиторий `.github/workflows/security.yml` (полный
пример — [`examples/security.yml`](examples/security.yml)):

```yaml
name: security
on:
  pull_request:
  push:
    branches: [main]
jobs:
  pipeline:
    uses: ignatenkofi/devsecops-pipeline-public/.github/workflows/pipeline-light.yml@v1
    with:
      repo-class: library   # library | service | docs-shelf | course-content
```

Всё. На каждый PR и push в `main` прогонятся три стадии; отчёты — артефактом
и таблицей в job summary.

## Что делает конвейер

Три лёгкие стадии, порядок подчинён принципу «дешёвое и смертельное — раньше»:

| # | Стадия | Инструмент | Что ищет |
|---|---|---|---|
| 1 | **secrets** | [gitleaks](https://github.com/gitleaks/gitleaks) | утёкшие ключи, токены, пароли в коде и истории |
| 2 | **sast-semgrep** | [Semgrep](https://semgrep.dev) + offline-правила | опасные паттерны кода (инъекции внутри файла, небезопасные API) |
| 3 | **sca** | [osv-scanner](https://github.com/google/osv-scanner) | известные уязвимости (CVE) в зависимостях |

Каждая стадия пишет [SARIF](https://sarifweb.azurewebsites.net/) — единый
формат результатов статического анализа. Отчёты собираются в артефакт
прогона и в сводку job summary.

## Профили классов

Один и тот же конвейер ведёт себя по-разному в зависимости от типа репо —
задаётся входом `repo-class`. Профили ([`profiles/`](profiles/)) — это
данные, а не код: матрица «класс × стадия × режим».

- **B** (blocking) — находка красит статус-чек, merge блокируется;
- **A** (advisory) — результат виден, merge не блокируется;
- **off** — стадия не запускается для этого класса.

| Класс | secrets | sast-semgrep | sca | Для кого |
|---|---|---|---|---|
| `library` | B | B | B | библиотеки, инструменты |
| `service` | B | B | B | сервисы, приложения |
| `docs-shelf` | B | off | off | полки документов (кода почти нет) |
| `course-content` | B | A | A | учебный контент (примеры кода намеренно неидеальны) |

Точечные отклонения — входы `skip-stages` / `extra-stages`; суппрессии
инструментов — конвенционные файлы у потребителя (`.gitleaks.toml`,
`.semgrepignore`).

## Границы честности

Здесь **только лёгкие стадии** — те, что работают на обычных GitHub-раннерах
без стендов. Тяжёлые (SonarQube-анализ, DAST, нагрузочные прогоны) требуют
self-hosted инфраструктуры и по security-инварианту доступны **только
приватным** репозиториям — они живут в приватной части и здесь сознательно
отсутствуют.

Важно и для урока: статический анализ уровня «AST + паттерны» **не** ловит
межпроцедурные taint-уязвимости целиком (SQLi, command injection, path
traversal, XSS, SSRF в полном объёме) — для этого нужны data-flow движки
(коммерческие тиры) и runtime-проверки (DAST). Конвейер закрывает
taint-класс частично: Semgrep ловит intra-file паттерны, а полнота честно
не обещается. Подробнее — [`docs/LESSON.md`](docs/LESSON.md).

## Синхронизация с приватной частью

Лёгкие стадии здесь — это тот же набор, что в приватном `devsecops-pipeline`.
Чтобы копии не разъезжались (ровно та боль, против которой конвейер и
создан), пины версий инструментов и offline-правила держатся в синхроне
nightly-задачей; приватная часть ссылается на этот публичный workflow
(private→public разрешён), так что источник лёгких стадий один.

## Версионирование

Потребители пинуются на floating-тег `@v1` (последний совместимый релиз
`v1.x.y`); `@main` использовать не нужно. Тег двигается только после
зелёного `selftest`.

## Как это устроено

```
.github/workflows/
  pipeline-light.yml   # переиспользуемый workflow (workflow_call) — точка входа
  selftest.yml         # CI самого репо: валидация профилей + позитив/негатив прогоны
actions/               # composite actions, по одной на инструмент
  gitleaks/ · semgrep/ (+rules/) · osv-scanner/ · sarif-report/ · profile-resolve/
profiles/              # матрица класс × стадия (данные)
examples/security.yml  # копипаст-адаптер для потребителя
docs/LESSON.md         # учебный разбор: зачем shift-left и что каждая стадия значит
```

Лицензия — [MIT](LICENSE).
