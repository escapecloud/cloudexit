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
git clone --branch v2.0.0 https://github.com/escapecloud/cloudexit.git
cd cloudexit
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

That installs the `cloudexit` command. To run from the checkout instead, use `pip install -r requirements.txt` and `python3 main.py`. cloudexit is not published on PyPI.

Upgrading from 1.x? Install into a new virtual environment, not over the old one.

Run your first assessment:

```bash
# AWS — using your existing CLI profile
python3 main.py aws --profile default

# Azure — using your Azure CLI session
az login
python3 main.py azure --cli
```

Reports are written locally as HTML, PDF, and JSON. Full credential and permissions setup is covered in the [documentation](https://cloudexit.escapecloud.io).

## What you get

cloudexit produces a structured exit readiness report covering:

- **Resource Inventory** — every service in use within the defined scope
- **Cost Inventory** — monthly cost breakdown within the defined scope
- **Risk Assessment** — rule-based evaluation of exit feasibility across each identified service
- **Alternative Technologies** — open-source and portable replacements for each identified cloud service, with additional details such as company ownership and location

The [documentation](https://cloudexit.escapecloud.io/reports/overview.html) shows an example report.

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
| Interactive (manual credentials) | `python3 main.py aws` |
| AWS CLI profile | `python3 main.py aws --profile PROFILE` |
| JSON config file | `python3 main.py aws --config config.json` |
| Terraform/OpenTofu state file | `python3 main.py aws --tfstate infra.tfstate` |
| Non-interactive (env vars, CI) | `python3 main.py aws --non-interactive` |

**Azure**

| Mode | Command |
|---|---|
| Interactive (service principal) | `python3 main.py azure` |
| Azure CLI session | `python3 main.py azure --cli` |
| JSON config file | `python3 main.py azure --config config.json` |
| Terraform/OpenTofu state file | `python3 main.py azure --tfstate infra.tfstate` |
| Non-interactive (env vars, CI) | `python3 main.py azure --non-interactive` |

See the [configuration reference](https://cloudexit.escapecloud.io/config/config-schema.html) for required permissions and config file format.

## Connecting to a platform

Connecting to a platform is optional; without a host and an API key cloudexit runs fully offline. Create an **assessment key** on the assessment's page, then set `CLOUDEXIT_HOST` and `CLOUDEXIT_API_KEY_FILE` — a key file readable by you only, rather than a key in plain text. In CI add `--upload required`, so a missing key fails the job before anything is collected instead of quietly producing an offline report. `cloudexit status` checks the settings, the TLS chain and the key; `cloudexit config show` says where each setting came from.

> **Changed in 2.0:** `config.py` and the `HOST` / `KEY` environment variables are no longer read. See [Migrating from 1.x](./docs/migration-2.0.md).

## Documentation

| Topic | Page |
|---|---|
| Settings, precedence, config file, key files | [Platform settings →](https://cloudexit.escapecloud.io/config/platform-settings.html) |
| Keys, upload modes, the spool, what gets uploaded | [Connected Mode →](https://cloudexit.escapecloud.io/connected-mode/upload-modes.html) |
| Running in CI/CD | [Non-Interactive Mode →](https://cloudexit.escapecloud.io/non-interactive/overview.html) |
| The catalogue dataset | [Catalogue →](https://cloudexit.escapecloud.io/catalogue/overview.html) |
| Exit codes | [Exit codes →](https://cloudexit.escapecloud.io/reference/exit-codes.html) |
| Troubleshooting | [Troubleshooting →](https://cloudexit.escapecloud.io/troubleshooting/common-errors.html) |
| Migrating from 1.x | [docs/migration-2.0.md](./docs/migration-2.0.md) |
| Release history | [CHANGELOG.md](./CHANGELOG.md) |

## Data Landscape & Egress Estimation

Add `--egress` to any assessment to measure how much data lives in the assessed scope and estimate the one-time internet egress fee for moving it out — based on the provider's tiered list prices, with no additional permissions required.

```bash
python3 main.py azure --cli --egress
python3 main.py aws --profile PROFILE --egress
```

See the [egress reference](https://cloudexit.escapecloud.io/egress/overview.html) for details.

## Infrastructure-as-Code State Scan

Instead of connecting to a cloud account, cloudexit can build the assessment from a local **Terraform / OpenTofu state file** — no credentials, no API calls, nothing leaves your machine.

```bash
python3 main.py aws --tfstate infra.tfstate
python3 main.py azure --tfstate infra.tfstate
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

The Action is at `v1`; a 2.0 version is coming. Until then, pass the 2.0 flags to the CLI directly, as below.

In a pipeline that must upload, add `--upload=required` so a missing key fails the job before anything is collected, `--strict` to fail on platform warnings, and `--output json` to read the result from stdout.

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

The public contract is the `cloudexit` command, `python -m cloudexit` and `cloudexit.cli:main`. Everything else under `cloudexit.*` is internal and may move in any 2.x release.

## License

© 2024–2026 EscapeCloud. cloudexit is licensed under the **GNU Affero General Public License v3 (AGPL-3.0)**.  
See [LICENSE](./LICENSE) for details.

A commercial licence is available through the EscapeCloud enterprise package for organizations that cannot use AGPL-licensed software; see [escapecloud.io](https://escapecloud.io).

EscapeCloud, cloudexit and the EscapeCloud logo are trademarks. The AGPL-3.0 licence does not grant any right to use them; modified versions must not use them in a way that suggests they are official.
