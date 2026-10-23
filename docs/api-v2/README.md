# API v2 definition

The definition of `/api/v2`, the contract between the cloudexit CLI and the hosts (EscapeCloud and ExitCloud). It is the single source of truth: both hosts and the CLI carry an identical copy.

| File | What it is |
| --- | --- |
| `VERSION` | The version of the definition. |
| `openapi.yaml` | The API (OpenAPI 3.1): endpoints, headers, request and response schemas, limits. |
| `errors.yaml` | The error and warning code registry, with HTTP status, retry flag and the CLI exit code. |
| `vectors/scope_key.json` | Test vectors for the canonical `scope_key`. |
| `vectors/content_sha256.json` | Test vectors for the canonical content form and `content_sha256`. |
| `fixtures/catalogue.json` | The catalogue the examples run against. |
| `examples/*.json` | One scenario per file: the fixtures it needs, a request and what to expect. |
| `make_manifest.py`, `MANIFEST.sha256` | The file hashes that keep copies identical. |

## Versions

- `VERSION` is `MAJOR.MINOR.PATCH`. The major is the path (`/api/v2`).
- Additive changes (new optional request fields, new response fields, new enum values, new error or warning codes, new endpoints) raise the **minor** version. Fixes to wording and examples raise the patch.
- A breaking change is a new major: `/api/v3` paths and a new definition.
- `schema_version` in a request body is `2.<minor>`. `GET /meta` lists the minors a host accepts.

## Keeping copies identical

**Every copy must keep the same `VERSION` and the same files.** After any change run

```bash
python docs/api-v2/make_manifest.py
```

and commit `MANIFEST.sha256` with the change. A copy whose files and manifest disagree is not a faithful copy.

## Examples

Each file in `examples/` is one scenario that a harness replays against a host.

### An example file

```json
{
  "name": "…", "description": "…",
  "host": "escapecloud | exitcloud",
  "fixtures": {"catalogue": "standard", "assessments": [], "keys": [], "credits": 0},
  "before": [{"request": {}, "expect": {}, "save": {"name": "/json/pointer"}}],
  "request": {"method": "POST", "path": "/api/v2/syncs", "headers": {}, "body": {}},
  "expect": {"status": 201}
}
```

- `host` is optional. Without it the example runs on every host.
- `before` steps run first. A step may `save` values of its response (JSON pointers) as placeholders.
- `request` may instead have `raw_body` (a string sent as it is), or `pad_bytes_to` (the harness adds an unknown field so the body is that many bytes long).
- `expect` has `status`, `headers` (exact), `headers_present`, `headers_differ`, `body_subset`, `exists` (JSON pointers that must exist), `absent` and `lengths` (JSON pointer to list length).
- `body_subset` matches objects by their keys and lists by containment: every expected item must equal or be a subset of some actual item, in any order.

### Fixtures

- `catalogue`: `"standard"` (`fixtures/catalogue.json`), or `{"use": "standard", "version": "…"}` to change fields. The host installs it as its active catalogue.
- `assessments`: `{name, provider, exit_strategy, assessment_type, scope_key}`. `scope_key` is what the first sync already recorded (null before it). The assessments are created in order.
- `keys`: `{name, kind, assessment?, scopes?, state?}`. `kind` is `assessment` (with the `assessment` it is bound to; scope `syncs:write`) or `account`. `state` is `active` (default), `revoked` or `expired`.
- `credits`: the credit balance of the tenant (ExitCloud only).

### Placeholders

- A key is `{{<key name>}}`, the raw key. `{{<key name>|corrupt}}` is the same key with a wrong checksum.
- An assessment is `{{<assessment name>_id}}`.
- `{{now}}`, `{{now-300}}`, `{{now+60}}`: the current time in epoch seconds. A value that is only a placeholder keeps its type, so these become integers.
- Names saved by a `before` step.
