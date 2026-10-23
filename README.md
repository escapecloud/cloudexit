<p align="center">
  <img src="./docs/images/Logo.png" alt="EscapeCloud" width="400" />
</p>

<p align="center">
  <strong>Know your exit before you're locked in.</strong><br />
  Open-source cloud exit assessment CLI. Runs locally, no account required.
</p>

<p align="center">
  <a href="https://opensource.org/licenses/AGPL-3.0"><img src="https://img.shields.io/badge/license-AGPL--3.0-green" alt="License"></a>
  <img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.escapecloud.io%2Fpublic%2Fbadges%2Fcloud-providers&style=flat" alt="Cloud providers">
  <img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.escapecloud.io%2Fpublic%2Fbadges%2Faws-services&style=flat" alt="AWS services">
  <img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.escapecloud.io%2Fpublic%2Fbadges%2Fazure-services&style=flat" alt="Azure services">
  <img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.escapecloud.io%2Fpublic%2Fbadges%2Falternative-technologies&style=flat" alt="Alternative technologies">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#cloud-providers">Providers</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#cicd">CI/CD</a> ·
  <a href="#ecosystem">Ecosystem</a> ·
  <a href="https://cloudexit.escapecloud.io">Docs</a>
</p>

---

## What is cloudexit?

cloudexit is a free, open-source CLI that assesses your **cloud exit readiness**. It connects to your AWS or Azure account with read-only credentials, builds a full resource and cost inventory, scores your vendor lock-in risk across service categories, and produces an HTML, PDF, and JSON report — all locally, with no account required.

It is the open-source foundation of the [EscapeCloud](https://escapecloud.io) ecosystem.

## Quick Start

**Prerequisites:** Python 3.12+, and either an AWS CLI profile or an Azure CLI session.

```bash
git clone git@github.com:escapecloud/cloudexit.git
cd cloudexit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Run your first assessment:

```bash
# AWS — using your existing CLI profile
python main.py aws --profile default

# Azure — using your Azure CLI session
az login
python main.py azure --cli
```

Reports are written locally as HTML, PDF, and JSON. Full credential and permissions setup is covered in the [documentation](https://cloudexit.escapecloud.io).

## What you get

cloudexit produces a structured exit readiness report covering:

- **Resource Inventory** — every service in use within the defined scope
- **Cost Inventory** — monthly cost breakdown within the defined scope
- **Risk Assessment** — rule-based evaluation of exit feasibility across each identified service
- **Alternative Technologies** — open-source and portable replacements for each identified cloud service, with additional details such as company ownership and location

![Report](./docs/images/Report.png)

## Cloud Providers

| Provider | Required permissions |
|---|---|
| Amazon Web Services | [AWS permissions →](https://cloudexit.escapecloud.io/cloud-providers/aws.html) |
| Microsoft Azure | [Azure permissions →](https://cloudexit.escapecloud.io/cloud-providers/azure.html) |

See the [permission reference](https://cloudexit.escapecloud.io/cloud-providers/required-permissions.html) for details.

> Google Cloud Platform & Oracle Cloud Infrastructure is on the roadmap.

## Configuration

The CLI supports multiple input modes for both AWS and Azure.

**AWS**

| Mode | Command |
|---|---|
| Interactive (manual credentials) | `python main.py aws` |
| AWS CLI profile | `python main.py aws --profile PROFILE` |
| JSON config file | `python main.py aws --config config.json` |
| Terraform/OpenTofu state file | `python main.py aws --tfstate infra.tfstate` |
| Non-interactive (env vars, CI) | `python main.py aws --non-interactive` |

**Azure**

| Mode | Command |
|---|---|
| Interactive (service principal) | `python main.py azure` |
| Azure CLI session | `python main.py azure --cli` |
| JSON config file | `python main.py azure --config config.json` |
| Terraform/OpenTofu state file | `python main.py azure --tfstate infra.tfstate` |
| Non-interactive (env vars, CI) | `python main.py azure --non-interactive` |

See the [configuration reference](https://cloudexit.escapecloud.io/config/config-schema.html) for required permissions and config file format.

Want to see how a regulatory-aligned report looks (DORA / FINMA / UK PRA)? Run with `--dry-run` and send the output `raw_data/sync.json` to request_report@escapecloud.io — we'll generate a sample you can share with your risk or compliance team.

## Platform settings

Connecting to a platform is optional. Without any settings cloudexit runs fully offline. Each value is resolved separately, highest precedence first:

1. **Flags**: `--host`, `--api-key-file`, `--config-profile`, `--assessment-id`, `--ca-bundle`, `--timeout`
2. **Environment**: `CLOUDEXIT_HOST`, `CLOUDEXIT_API_KEY_FILE`, `CLOUDEXIT_API_KEY`, `CLOUDEXIT_PROFILE`, `CLOUDEXIT_ASSESSMENT_ID`, `CLOUDEXIT_UPLOAD`, `CLOUDEXIT_CA_BUNDLE`, `CLOUDEXIT_TIMEOUT`
3. **Config file**: `~/.config/cloudexit/config.toml` (`$XDG_CONFIG_HOME/cloudexit/config.toml`, or `%APPDATA%\cloudexit\config.toml` on Windows), with a `[default]` table and optional `[profiles.<name>]` tables. See [`config.example.toml`](./config.example.toml).
4. **Deprecated**: the `HOST` and `KEY` environment variables (only when both are set) and `config.py`. Both still work and print a warning.

Prefer a key file (`--api-key-file`, `CLOUDEXIT_API_KEY_FILE` or `api_key_file`) over a key in plain text, and keep it readable by you only. The CLI warns when the config file or a key file is readable by group or others. `http://` hosts are rejected, except `localhost` and `127.0.0.1`.

```bash
cloudexit config path   # where the config file is looked up
cloudexit config show   # every resolved value and its source (keys are masked)
```

## Catalogue dataset

cloudexit assesses against a catalogue dataset that it keeps up to date. It is stored in your data directory (`~/.local/share/cloudexit/catalogue/`, `$XDG_DATA_HOME/cloudexit/catalogue/`, or `%LOCALAPPDATA%\cloudexit\catalogue\` on Windows) together with the previous version. Each run checks for a newer catalogue first. If that check fails, the run continues with the installed catalogue and says so; without any catalogue it stops.

```bash
cloudexit catalogue status                # installed and latest catalogue
cloudexit catalogue update                # download the latest (or --version V)
cloudexit catalogue import FILE.db.gz     # install a file on an air-gapped machine (--sha256 to verify)
cloudexit aws --tfstate infra.tfstate --catalogue FILE.db.gz   # use a file for one run, without installing it
```

Set `CLOUDEXIT_CATALOGUE_URL` to download from a mirror instead of the default location. A `./datasets/data.db` from an earlier version is copied to the data directory once.

## Sync document

Every run writes `raw_data/sync.json` next to the report: the run's result in the shape defined by the API v2 definition in [`docs/api-v2/`](./docs/api-v2/) (a vendored copy; do not edit it here). It holds the scope (an AWS account and region, an Azure subscription, or a Terraform state), the resource inventory, the monthly costs and, when `--egress` ran, the egress estimate. It never contains credentials and is built without a network connection.

```bash
cloudexit aws --config config.json --emit-payload out/        # also write out/<sync id>.json
cloudexit aws --config config.json --egress --egress-names hashed   # send egress names as salted hashes
```

With `--egress-names hashed` each egress resource name is replaced by an HMAC-SHA256 with a random per-scope salt that stays on your machine (`~/.local/share/cloudexit/salts/`), so the same name hashes the same way in later runs of the same scope.

## The report

Every run writes the same three files: `report.pdf`, `index.html` and `raw_data/assessment_result.json`. The PDF is the Basic Executive Summary, set in IBM Plex (SIL Open Font License, in `assets/fonts/`). It opens with a cover and a document information page that record where the data came from: the cloudexit version, the run ID, the data catalogue version and hash, and either a link to the platform's reports or the statement that the data was self-collected. The same facts are in the PDF metadata and in a `provenance` object in the JSON report. The last page says what the full assessment on a platform adds.

## Reports and uploads

The local report is always the Basic report, computed on your machine, whether or not a platform is configured. Anything the platform computes stays on the platform: after an upload the local report only records the link (also stored in `raw_data/upload.json`). If an upload fails, the report is still written, with a short "Not uploaded: <reason>" banner, and the run exits with an upload code (10 or 12 for now) after everything else has finished. Problems in the collection stages take precedence over upload codes.

## Data Landscape & Egress Estimation (alpha)

Add `--egress` to any assessment to measure how much data lives in the assessed scope and estimate the one-time internet egress fee for moving it out — based on the provider's tiered list prices, with no additional permissions required.

```bash
python main.py azure --cli --egress
python main.py aws --profile PROFILE --egress
```

See the [egress reference](https://cloudexit.escapecloud.io/egress/overview.html) for details.

## Infrastructure-as-Code State Scan (alpha)

Instead of connecting to a cloud account, cloudexit can build the assessment from a local **Terraform / OpenTofu state file** — no credentials, no API calls, nothing leaves your machine.

```bash
python main.py aws --tfstate infra.tfstate
python main.py azure --tfstate infra.tfstate
```

If your state lives in a remote backend (S3, Azure Storage, Terraform Cloud, …), export it first:

```bash
terraform state pull > infra.tfstate
```

See the [infrastructure-as-code reference](https://cloudexit.escapecloud.io/tfstate/overview.html) for details.

## CI/CD

cloudexit runs headlessly in CI pipelines via `--non-interactive` and environment variables. A ready-made GitHub Action is available:

```yaml
- uses: escapecloud/cloudexit-action@v1
  with:
    provider: aws
  env:
    AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
    AWS_DEFAULT_REGION: us-east-1
    ESC_EXIT_STRATEGY: 1
    ESC_ASSESSMENT_TYPE: 1
```

[View on GitHub Marketplace →](https://github.com/marketplace/actions/github-action-for-cloudexit)

## Ecosystem

cloudexit is the open-source, offline-first tier of a broader platform.

| Tier | Description |
|---|---|
| **cloudexit** (this repo) | CLI, offline Basic assessment, local reports |
| **[exitcloud.io](https://exitcloud.io)** | Lightweight platform for individuals, SMEs, and MSPs — adds scoring, history, and richer reports |
| **[escapecloud.io](https://escapecloud.io)** | Enterprise platform with advanced reporting, governance, and regulatory evidence (DORA, FINMA, UK PRA) |

Running cloudexit offline always produces a Basic assessment. Connect it to exitcloud.io or escapecloud.io for full scoring and report features.

## Contributing

Contributions are welcome — bug reports, documentation improvements, new service mappings, and pull requests.

See the [contribution guidelines](https://cloudexit.escapecloud.io/contributing/how-to-contribute.html) for details. Working rules for contributors and AI agents (setup, checks, branches, commits, pull requests) are in [AGENTS.md](./AGENTS.md).

## License

cloudexit is licensed under the **GNU Affero General Public License v3 (AGPL-3.0)**.  
See [LICENSE](./LICENSE) for details.
