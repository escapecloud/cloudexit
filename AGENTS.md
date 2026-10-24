# AGENTS.md

## What this is

cloudexit is an offline-first CLI. It collects cloud inventory and cost data with the user's own credentials, builds a local report, and can optionally upload the results to a platform through API v2. Cloud credentials stay on the user's machine and are never sent anywhere.

## Setup

- Develop on Python 3.14. CI also tests 3.12 and 3.13 on Linux and 3.14 on macOS and Windows, because `requires-python` is `>=3.12`: don't use anything newer than 3.12 (for example `uuid.uuid7` only behind a fallback).
- Create and activate a virtualenv, then `pip install -r requirements-dev.txt`.
- Run the CLI with `python3 main.py`, or `pip install -e .` and use the `cloudexit` command.
- Local configs go in `config/` (start from a `*_example.json` template). They are never committed.

## Lint and format

CI runs exactly these, with the tool versions pinned in `requirements-dev.txt`:

```bash
ruff check main.py cloudexit core utils tests
black --check main.py cloudexit core utils tests
```

Fix with `ruff check --fix main.py cloudexit core utils tests` and `black main.py cloudexit core utils tests`. Don't add a `# noqa` or an ignore without a code comment saying why.

## Tests

```bash
python -m unittest discover -s tests -t .
```

- Add or update tests with every behaviour change. Tests sit in sub-directories that mirror the source layout (`tests/platform/`, `tests/report/`, ...); shared builders are in `tests/support/` and data files in `tests/fixtures/`.
- Always discover with `-t .`: without it `tests/` itself becomes the top-level directory and `tests/platform/` would be imported as `platform`, shadowing the standard library.
- Tests never call real cloud APIs or real platforms. Mock `boto3`, the Azure SDK and HTTP.
- Tests that cannot run on a platform (POSIX file modes on Windows) are skipped with `unittest.skipIf` or `skipUnless` and a reason, never deleted.
- The Ubuntu 3.14 job runs the suite under `coverage` and prints the report. There is no threshold.

## Branches

- Name branches `task/<snake_case>`, one topic per branch.
- During 2.0 development, branch from and target `release/2.0`. `main` only gets hotfixes and Dependabot PRs (Dependabot always targets `main`).
- Before each release candidate, bring `main` into `release/2.0` with a PR from a branch `task/sync_main` (`git merge origin/main`). Merge it with a merge commit, not a squash.
- After the 2.0 release, branch from and target `main`.

## Commits

One line, lowercase, no trailing period, plain words about what changed, for example `load the egress resource registry from master data`.

## Pull requests

- A sentence-case title.
- A description with a Summary, a Changes section grouped by area, and a closing line on user-visible impact.
- State compatibility explicitly: config, exit codes, report output, the dataset and the API v2 definition copy.
- Wait for PR Checks to pass, then squash-merge (except `task/sync_main`, see Branches).

## Never commit

- Real values in `config.py`.
- Anything in `config/` except the `*_example.json` templates.
- `*.tfstate` files.
- `datasets/` and `reports/`.
- `.env` files, or credentials of any kind.

## Compatibility rules

- The CLI must keep working fully offline, without an account.
- `docs/api-v2/` is a vendored copy of the API v2 definition. Never edit it here; the version check test enforces this.
- The dataset is read only by column name. The CLI must tolerate extra tables and columns.

## Security

Never log API keys, cloud credentials, the `Authorization` header or full request and response payloads. Never add telemetry.
