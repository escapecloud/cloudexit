import logging
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from cloudexit.catalogue import store
from cloudexit.catalogue.db import connect
from cloudexit.egress.estimate import estimate_egress
from cloudexit.egress.model import GIB, load_egress_registry, new_row
from cloudexit.engine import (
    create_resource_inventory,
    generate_report,
    perform_risk_assessment,
)
from cloudexit.report.assets import copy_assets
from tests.support.html import html_blocks

# What a rendered case holds. The HTML report and each PDF have a text dump, so
# a change to any of them can be reviewed with a diff.
EXPECTED_FILES = {
    "always": ("report.pdf", "index.html", "texts/report.txt", "texts/index-html.txt"),
    "egress": ("egress.pdf", "texts/egress.txt"),
}
ASSESSMENT_TYPE = 1


class RenderError(Exception):
    pass


@dataclass
class RenderResult:
    case: str
    directory: Path
    files: list = field(default_factory=list)
    pages: dict = field(default_factory=dict)


def expected_files(scenario):
    files = list(EXPECTED_FILES["always"])
    if scenario.egress is not None:
        files += EXPECTED_FILES["egress"]
    return files


def frozen_datetime(moment):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    return FrozenDatetime


def pdf_dump(path):
    reader = PdfReader(str(path))
    parts = []
    for number, page in enumerate(reader.pages, start=1):
        lines = [line.rstrip() for line in (page.extract_text() or "").splitlines()]
        parts.append(f"--- page {number} ---\n" + "\n".join(lines))
    return "\n".join(parts) + "\n", len(reader.pages)


def _catalogue_types(catalogue_path, csp):
    uri = f"{Path(catalogue_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT code, id, status FROM resourcetype WHERE csp = ?", (csp,)
        ).fetchall()
    finally:
        conn.close()
    return {code: (type_id, status == "t") for code, type_id, status in rows}


def _check_types(scenario, catalogue_path):
    types = _catalogue_types(catalogue_path, scenario.csp)
    for resource in scenario.resources:
        code = resource["type"]
        if code not in types:
            raise RenderError(f"resource type {code} is not in the catalogue")
        if not types[code][1]:
            raise RenderError(f"resource type {code} is not active in the catalogue")
    return {code: type_id for code, (type_id, _active) in types.items()}


def _insert_inventory(scenario, report_path, type_ids):
    db_path = Path(report_path) / "data" / "assessment.db"
    with connect(db_path=db_path) as conn:
        conn.executemany(
            "INSERT INTO resource_inventory (resource_type, location, count) "
            "VALUES (?, ?, ?) ON CONFLICT(resource_type, location) "
            "DO UPDATE SET count = excluded.count",
            [
                (type_ids[item["type"]], item["region"], item["count"])
                for item in scenario.resources
            ],
        )
        costs = scenario.costs
        if costs:
            currencies = costs.get("currencies") or {}
            conn.executemany(
                "INSERT INTO cost_inventory (month, cost, currency) VALUES (?, ?, ?) "
                "ON CONFLICT(month) DO UPDATE SET cost = excluded.cost, "
                "currency = excluded.currency",
                [
                    (f"{month}-01", amount, currencies.get(month, costs["currency"]))
                    for month, amount in sorted(costs["months"].items())
                ],
            )
        conn.commit()


def _egress_rows(scenario):
    # What the collectors would have found, in the shape they return
    registry = load_egress_registry(scenario.csp)
    rows = []
    for index, item in enumerate(scenario.egress["resources"], start=1):
        key = item["type"].strip().lower() if scenario.csp == 1 else item["type"]
        if key not in registry:
            raise RenderError(f"egress type {item['type']} is not in the registry")
        entry = registry[key]
        row = new_row(
            f"/sample/{index}",
            item["name"],
            item["type"],
            entry["label"],
            entry["category"],
        )
        if item["size_gib"] is not None:
            row["size_bytes"] = round(item["size_gib"] * GIB)
        row["size_unknown"] = bool(item.get("unknown"))
        if item.get("tiers"):
            row["tier_bytes"] = {
                tier: round(gib * GIB) for tier, gib in item["tiers"].items()
            }
        row["flags"] = list(item.get("flags", []))
        row["notes"] = list(item.get("notes", []))
        rows.append(row)
    return rows, set(scenario.egress["archive_tiers"])


def _estimate_egress(scenario, report_path, raw_data_path):
    rows, archive_tiers = _egress_rows(scenario)
    with (
        patch(
            "cloudexit.providers.aws.egress.collect_aws_egress",
            return_value=(rows, archive_tiers),
        ),
        patch(
            "cloudexit.providers.azure.egress.collect_azure_egress",
            return_value=(rows, archive_tiers),
        ),
    ):
        result = estimate_egress(
            scenario.csp,
            scenario.provider_details(),
            raw_data_path,
            report_path=report_path,
            name=scenario.name,
            exit_strategy=scenario.exit_strategy,
            assessment_type=ASSESSMENT_TYPE,
        )
    if not result["success"]:
        raise RenderError(f"egress estimate failed: {result['logs']}")
    return result["json_path"]


def _write_dumps(scenario, directory, result):
    texts = directory / "texts"
    texts.mkdir()
    for pdf, dump in (("report.pdf", "report.txt"), ("egress.pdf", "egress.txt")):
        if (directory / pdf).is_file():
            text, pages = pdf_dump(directory / pdf)
            (texts / dump).write_text(text, encoding="utf-8")
            result.pages[pdf] = pages
    source = (directory / "index.html").read_text(encoding="utf-8")
    (texts / "index-html.txt").write_text(html_blocks(source), encoding="utf-8")


def _render(scenario, directory, catalogue_path):
    type_ids = _check_types(scenario, catalogue_path)
    report_path = str(directory)
    raw_data_path = str(directory / "raw_data")
    (directory / "raw_data").mkdir(parents=True)
    details = scenario.provider_details()

    if scenario.tfstate:
        # The real inventory stage, which also copies the assets and the catalogue
        result = create_resource_inventory(
            scenario.csp, details, report_path, raw_data_path
        )
        if not result["success"]:
            raise RenderError(f"resource inventory failed: {result['logs']}")
    else:
        copy_assets(report_path, scenario.csp)
        _insert_inventory(scenario, report_path, type_ids)

    egress_json = None
    if scenario.egress is not None:
        egress_json = _estimate_egress(scenario, report_path, raw_data_path)

    risks = perform_risk_assessment(scenario.exit_strategy, report_path)
    if not risks["success"]:
        raise RenderError(f"risk assessment failed: {risks['logs']}")

    report = generate_report(
        scenario.csp,
        details,
        scenario.exit_strategy,
        ASSESSMENT_TYPE,
        scenario.name,
        report_path,
        raw_data_path,
        upload=scenario.upload,
        sync_id=scenario.run_id,
        egress_json_path=egress_json,
    )
    if not report["success"]:
        raise RenderError(f"report failed: {report['logs']}")
    if report.get("egress_pdf_logs"):
        raise RenderError(f"egress.pdf failed: {report['egress_pdf_logs']}")


def _legacy_copy(catalogue_path, directory):
    # The dataset of an older release: the same tables, no catalogue_meta
    copy = Path(directory) / "legacy_catalogue.db"
    shutil.copy(catalogue_path, copy)
    conn = sqlite3.connect(copy)
    try:
        conn.execute("DROP TABLE IF EXISTS catalogue_meta")
        conn.commit()
    finally:
        conn.close()
    return copy


def render_case(scenario, out_dir, *, catalogue_path):
    directory = Path(out_dir) / scenario.case
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    previous = store._active_override
    clock = frozen_datetime(scenario.clock)
    try:
        with ExitStack() as stack:
            if scenario.catalogue == "legacy":
                scratch = stack.enter_context(tempfile.TemporaryDirectory())
                catalogue_path = _legacy_copy(catalogue_path, scratch)
            store.set_active_catalogue(catalogue_path)
            # The warnings of the pipeline are about the sample data, not news
            stack.callback(logging.disable, logging.root.manager.disable)
            logging.disable(logging.WARNING)
            for module in ("report.context", "engine", "egress.estimate"):
                stack.enter_context(patch(f"cloudexit.{module}.datetime", clock))
            _render(scenario, directory, catalogue_path)
    except RenderError as exc:
        raise RenderError(f"{scenario.case}: {exc}") from exc
    finally:
        store.set_active_catalogue(previous)

    result = RenderResult(scenario.case, directory)
    _write_dumps(scenario, directory, result)
    result.files = [
        name for name in expected_files(scenario) if (directory / name).is_file()
    ]
    return result


def render_cases(scenarios, out_dir, *, catalogue_path):
    # A case that fails is reported, and the others still render
    results, failures = [], {}
    for scenario in scenarios:
        try:
            results.append(
                render_case(scenario, out_dir, catalogue_path=catalogue_path)
            )
        except Exception as exc:
            failures[scenario.case] = (
                str(exc)
                if isinstance(exc, RenderError)
                else f"{scenario.case}: {exc!r}"
            )
    return results, failures
