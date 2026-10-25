# Changelog

All notable changes to cloudexit are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and cloudexit uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The public contract is the `cloudexit` command, `python -m cloudexit` and
`cloudexit.cli:main`. Everything under `cloudexit.*` is internal and may move in
any 2.x release.

## [2.0.0] - unreleased

Upgrading from 1.x? Follow the [migration guide](./docs/migration-2.0.md).

### Breaking

- `config.py` is no longer read. Settings come from flags, `CLOUDEXIT_*`
  environment variables or `~/.config/cloudexit/config.toml`. Under
  `python -m cloudexit` the current directory is first on `sys.path`, so a
  `config.py` there used to be imported and executed at start-up; it no longer
  is. When one is present and nothing else supplies a host or a key, the run
  logs a single warning saying where to move the values. The file is never
  imported, opened or parsed.
- The `HOST` and `KEY` environment variables are no longer read. Use
  `CLOUDEXIT_HOST` and `CLOUDEXIT_API_KEY_FILE` (or `CLOUDEXIT_API_KEY`). The
  same single warning covers them; their values are never read or logged.
- A run that relied on either source now runs offline, or fails with a
  configuration error under `--upload required`.
- Platform uploads use API v2. A 1.x platform key does not work; create a new
  assessment key on the assessment page.
- The sync document is `raw_data/sync.json` in the shape defined by
  [`docs/api-v2/`](./docs/api-v2/), replacing 1.x `payload.json`.
- Exit codes 10–15 are reserved for upload outcomes and 130 for Ctrl+C, so a
  pipeline that treated any non-zero exit as a collection failure needs
  updating. See
  [Exit codes](https://cloudexit.escapecloud.io/reference/exit-codes.html).
- The catalogue dataset lives in the user data directory and updates itself. A
  `./datasets/data.db` from 1.x is no longer picked up implicitly; install it
  with `cloudexit catalogue import datasets/data.db`.
- The package is `cloudexit/`. 1.x `core.*` and `utils.*` imports are gone.
- There is no `egress.html`. The egress section is part of `index.html`, with
  `egress.pdf` as its printable form.

### Added

- `--upload auto|required|off` (also `CLOUDEXIT_UPLOAD` and `upload` in the
  config file), so a pipeline can insist that an upload happens rather than
  falling back to an offline run.
- A config file with profiles: `[default]` and `[profiles.<name>]`, selected
  with `--config-profile` or `CLOUDEXIT_PROFILE`. Template:
  [`config.example.toml`](./config.example.toml).
- Key files: `--api-key-file`, `CLOUDEXIT_API_KEY_FILE` and `api_key_file`, with
  a warning when the file is readable by group or others.
- `cloudexit status` — the resolved settings with their sources, the TLS chain,
  the key (kind, organisation, assessment, scopes, expiry) and the platform's
  own `/meta`, plus the spool and a catalogue comparison. `--output json` prints
  the same as one JSON object.
- `cloudexit config path` and `cloudexit config show` — where settings are
  looked up, and every resolved value with its source. Keys are masked.
- `cloudexit catalogue status|update|import` — inspect, download or install the
  catalogue dataset, including on an air-gapped machine (`--sha256` verifies the
  file). `--catalogue FILE` uses a file for one run without installing it, and
  `CLOUDEXIT_CATALOGUE_URL` points the download at a mirror.
- `cloudexit upload` — upload sync documents, or folders of them, for example
  from an air-gapped machine. With an account key it takes `--assessment-id`, or
  `--name` and `--exit-strategy`.
- A spool for uploads that could not be delivered, with
  `cloudexit spool list|flush|purge`. Retries honour `Retry-After`, carry one
  idempotency key per sync so a repeat is never charged twice, and stay within
  `--upload-budget` seconds. `--no-flush` leaves earlier syncs alone and
  `--spool-on-error` also keeps syncs the platform refused.
- `--emit-payload DIR` — write a second copy of the sync document for review or
  for a later upload.
- `--strict` — fail the run when the platform reports warnings, after the report
  has been written.
- `--output json` — one JSON object on stdout at the end of a run, with the step
  output on stderr.
- `--egress-names plain|hashed` — send egress resource names as they are, or as
  HMAC-SHA256 hashes with a per-scope salt that stays on your machine.
- `--ca-bundle` and `CLOUDEXIT_CA_BUNDLE` for private certificate authorities;
  `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `HTTPS_PROXY` and `NO_PROXY` are
  honoured. Certificate verification cannot be turned off.
- `--timeout` and `--upload-budget` for slow links.
- Exit strategy 2, Hybrid Cloud Adoption, can be chosen in the interactive
  prompt and with `ESC_EXIT_STRATEGY`, matching the JSON config, the catalogue
  and the reports.
- This changelog, and the [2.0 migration guide](./docs/migration-2.0.md).

### Changed

- `index.html` is one self-contained file. Fonts, icons and styles are bundled
  and nothing is fetched from the network, so the report opens on an air-gapped
  machine and can be attached to an email.
- `report.pdf` is the Basic Executive Summary, set in IBM Plex, opening with a
  cover and a document information page that record the cloudexit version, the
  run ID, the catalogue version and hash, and either the platform link or the
  statement that the data was self-collected. The same facts are in the PDF
  metadata and in a `provenance` object in the JSON report. Both PDFs end with
  the Legal Notices.
- The local report is always the Basic report, computed on your machine. What a
  platform computes stays on the platform; after an upload the local report
  records only the link.
- A failed upload still writes the report, with a short banner, and the run
  exits with the upload code once everything else has finished. Collection
  failures take precedence over upload codes.
- Platform messages name the host rather than a product, an offline run is
  reported as a normal mode instead of a warning, and the assessment type is
  only asked when an upload will happen.
- `-vv` no longer writes request signing details or credentials to the console
  or to `run.log`: botocore's signing and credential loggers stay quiet, and a
  filter on both handlers redacts `Authorization` values, credentials, session
  tokens and signatures.
- Egress figures that leave unsized resources out are marked with a `+`, and an
  estimate where nothing could be sized shows `–` rather than a zero.
- A half-configured platform (a host without a key, or the reverse) is a
  configuration error instead of a silent offline run.
- Loggers are named after their module, so `run.log` says which part of the CLI
  wrote each line.
- Dependencies refreshed; `requires-python` is `>=3.12`.
- Installation is from a release tag with `pip install .`. cloudexit is not
  published on PyPI.
- Data Landscape & Egress Estimation and the Infrastructure-as-Code State Scan
  are no longer marked alpha.
- The platform, catalogue, upload and report reference moved from the README to
  the [documentation site](https://cloudexit.escapecloud.io), so it is written
  once. The README keeps a short pointer and a links table.

### Removed

- `config.py` and the `HOST` / `KEY` environment variables as settings sources
  (see Breaking).
- The end-of-run promotional block in the terminal, and the "Next Steps" page of
  the egress PDF. The terminal states what happened and where the outputs are.
- The two-second artificial pause behind the progress spinner.
- `egress.html` as a separate file, and the shared icon assets that were fetched
  per run.

### Deprecated

- Boolean `upload` values in the config file and the environment. `true` and
  `false` still map to `auto` and `off`, but write the mode instead.

## Earlier releases

1.x release notes are on the
[releases page](https://github.com/escapecloud/cloudexit/releases).
