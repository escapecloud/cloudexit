# Migrating from 1.x to 2.0

2.0 changes where settings come from, how uploads work and what a run writes.
Nothing about your cloud credentials or the required read-only permissions
changes. A run with no platform configured still works fully offline.

Read [Before you start](#before-you-start), then work through the steps that
apply to you. The full list of changes is in [CHANGELOG.md](../CHANGELOG.md).

## Before you start

- 2.0 needs Python 3.12 or newer.
- Nothing is read from `config.py` or from the `HOST` / `KEY` environment
  variables any more. If you use either, do [step 2](#2-move-your-settings)
  before your first 2.0 run.
- If you upload to a platform, you need a new API key: see
  [step 5](#5-get-a-new-platform-key).

## 1. Install into a new virtual environment

**Recommended.** 1.x and 2.0 pin different dependency versions, and `pip` leaves
the old ones in place when it upgrades, which can leave a partially upgraded
Azure SDK behind.

```bash
git clone --branch v2.0.0 https://github.com/escapecloud/cloudexit.git cloudexit-2.0
cd cloudexit-2.0
python3 -m venv .venv && source .venv/bin/activate
pip install .
cloudexit --version
```

To run from the checkout without installing, use
`pip install -r requirements.txt` and `python3 main.py` instead.

### Upgrading in place

If you upgrade over an existing environment and a run stops with

```
The Azure SDK in this environment is incomplete (cannot import azure.mgmt.<module>.<name>).
```

reinstall the package the message names, pinned as this installation asks for
it:

```bash
pip install --force-reinstall --no-deps 'azure-mgmt-resource==<version from the message>'
```

The message always names the exact package and version. If several are affected,
a new virtual environment is faster than repairing them one at a time.

## 2. Move your settings

`config.py` is **removed** in 2.0, not deprecated: it is never imported, opened
or parsed. The same is true of the `HOST` and `KEY` environment variables. When
one of them is still present and nothing else supplies a host or a key, the run
logs one warning telling you where to move the values — their contents are never
read or logged.

A 1.x `config.py` looked like this:

```python
HOST = "platform.example.com"
KEY = "your-platform-key"
```

Pick one of the two replacements.

**A config file** (`cloudexit config path` prints the location —
`~/.config/cloudexit/config.toml` on Linux and macOS,
`%APPDATA%\cloudexit\config.toml` on Windows):

```toml
[default]
host = "platform.example.com"
api_key_file = "~/.config/cloudexit/api-key"
upload = "auto"
```

Keep the key in its own file rather than in the config file:

```bash
mkdir -p ~/.config/cloudexit
install -m 600 /dev/null ~/.config/cloudexit/api-key
printf '%s' 'YOUR-NEW-KEY' > ~/.config/cloudexit/api-key
chmod 600 ~/.config/cloudexit/config.toml
```

**Environment variables**, which suit CI better:

```bash
export CLOUDEXIT_HOST=platform.example.com
export CLOUDEXIT_API_KEY_FILE=/run/secrets/cloudexit-key
# or, when a file is not possible:
export CLOUDEXIT_API_KEY=…
```

Precedence is flags, then `CLOUDEXIT_*`, then the config file. Check the result
without running an assessment:

```bash
cloudexit config show   # every value and where it came from; keys are masked
```

Then delete the old `config.py` and unset `HOST` and `KEY`, so the warning stops
and nothing is left lying around with a key in it.

> `HOST` and `KEY` are generic names that other tools also set. In 1.x, having
> both set was enough to make cloudexit treat them as a platform and upload to
> it. That is the main reason they are gone.

## 3. Review your exit codes

1.x used a small range of exit codes. 2.0 reserves 10–15 for upload outcomes and
130 for Ctrl+C, and the local report is always written before an upload code is
returned.

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | An unexpected error. |
| 2 | A configuration problem, including a half-configured platform. |
| 3–9 | Credentials, permissions, inventory, costs, risk, report, egress. |
| 10–15 | Upload outcomes: rejected, key, unreachable, quota, target, unsupported. |
| 130 | Interrupted with Ctrl+C. |

The full table is in the
[documentation](https://cloudexit.escapecloud.io/reference/exit-codes.html). Two
consequences for pipelines:

- A job that treated any non-zero exit as "the assessment failed" now also fails
  on an upload problem, even though the report exists. Decide per code which
  ones should fail the job.
- A job that must upload should pass `--upload required`, so a missing key fails
  it before any cloud API is called rather than producing a quiet offline report.

## 4. Expect the Basic report locally

The local report is always the **Basic** report, computed on your machine,
whether or not a platform is configured. Scoring and the richer reports live on
the platform; after an upload the local report records the link to them and
nothing else.

Each run writes:

| File | What it is |
| --- | --- |
| `index.html` | One self-contained HTML report. Fonts, icons and styles are bundled and nothing is fetched from the network. |
| `report.pdf` | The Basic Executive Summary. |
| `egress.pdf` | Written when `--egress` ran. |
| `raw_data/sync.json` | The sync document (see [step 6](#6-payloadjson-becomes-syncjson)). |
| `raw_data/assessment_result.json` | The local results as JSON. |
| `raw_data/upload.json` | Written when an upload was attempted. |
| `run.log` | The run's log. |

There is no separate `egress.html` any more: the Egress & Data Landscape section
is part of `index.html`, and `egress.pdf` is its printable form. If you archived
`egress.html`, archive `index.html` instead.

## 5. Get a new platform key

Uploads use API v2, and 1.x keys do not work with it. Create an **assessment
key** on the assessment's page in the platform and use it for every run of that
scope — one key per assessment.

An **account key** is the other kind: it can create assessments, so
`cloudexit upload` with one needs `--assessment-id`, or `--name` and
`--exit-strategy`, to say where a sync belongs.

Check a key before you rely on it:

```bash
cloudexit status        # key kind, organisation, assessment, scopes, expiry, TLS chain
```

`cloudexit status` warns from 14 days before a key expires. If your platform
uses a private certificate authority, point `--ca-bundle` or
`CLOUDEXIT_CA_BUNDLE` at the bundle; verification cannot be turned off.

## 6. `payload.json` becomes `sync.json`

1.x wrote `payload.json`. 2.0 writes `raw_data/sync.json` in the shape defined by
the vendored API v2 definition in [`docs/api-v2/`](./api-v2/). It holds the
scope, the resource inventory, the monthly costs and, when `--egress` ran, the
egress estimate. It never contains credentials and is built with no network
connection, so you can read it before anything is uploaded.

```bash
cloudexit aws --config config.json --dry-run                   # local report plus sync.json, no upload
cloudexit aws --config config.json --emit-payload out/         # also write out/<sync id>.json
cloudexit upload raw_data/sync.json                            # upload it later, or from another machine
```

If you had a script that read `payload.json`, point it at `raw_data/sync.json`
and check the field names against the definition in `docs/api-v2/` — the shape
is not the 1.x one.

## 7. Stop updating the dataset by hand

In 1.x the assessment dataset was a file in the checkout, under `datasets/`, that
you refreshed by pulling the repository.

2.0 keeps a **catalogue** in your user data directory and checks for a newer one
at the start of each run, using a signed manifest. If the check fails, the run
continues with the installed catalogue and says so. The previous version is kept
alongside the current one.

| Where | Path |
| --- | --- |
| Linux | `$XDG_DATA_HOME/cloudexit/catalogue/`, else `~/.local/share/cloudexit/catalogue/` |
| macOS | `~/.local/share/cloudexit/catalogue/` |
| Windows | `%LOCALAPPDATA%\cloudexit\catalogue\` |

```bash
cloudexit catalogue status                # installed and latest
cloudexit catalogue update                # download the latest, or --version V
cloudexit catalogue import FILE.db.gz     # install a file, with --sha256 to verify
```

Your old `datasets/data.db` is **not** picked up automatically. If you want to
keep using it, install it on purpose:

```bash
cloudexit catalogue import datasets/data.db
```

On a machine with no outbound access, either import a file as above or pass
`--catalogue FILE` to use one for a single run without installing it. Set
`CLOUDEXIT_CATALOGUE_URL` to download from a mirror of your own.

## 8. Run it once and compare

```bash
cloudexit config show
cloudexit status                       # only meaningful with a platform configured
cloudexit aws --profile default --dry-run
```

`--dry-run` makes a complete local report and the sync document without
contacting a platform, which is the safest first 2.0 run. When the report looks
right, drop `--dry-run`.

## Still stuck?

- [Troubleshooting](https://cloudexit.escapecloud.io/troubleshooting/common-errors.html)
  covers TLS, proxies, timeouts and the common exit codes.
- `-v` adds the interaction narrative and `-vv` adds debug output, including
  third-party libraries. Neither prints credentials, and `run.log` in the report
  folder holds the same lines.
- [Open an issue](https://github.com/escapecloud/cloudexit/issues) with the
  command you ran, the exit code, and the request ID if the platform returned
  one.
