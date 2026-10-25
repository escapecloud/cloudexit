from xml.sax.saxutils import escape

from .brief_summary import collect_brief_facts, finding_rows, lead_text
from .helpers import severity_label
from .notices import notices_html
from .palette import SEVERITIES
from .sections import egress_note, glossary_entries, scope_boundary, scope_strategy
from .wording import BLOCKS


def _version(text):
    return f"<code>{escape(text)}</code>"


def html_sections(context, b=BLOCKS):
    # The same wording as the PDF; strings that carry markup are already
    # escaped, so the template may mark them safe.
    facts = collect_brief_facts(context, b)
    provenance = context.provenance

    if provenance.catalogue_version and provenance.catalogue_sha256:
        catalogue = (
            f"{provenance.catalogue_version} · (sha256 "
            f"{provenance.catalogue_sha256[:12]})"
        )
    else:
        catalogue = provenance.catalogue_version or b["ev_catalogue_unknown"]

    return {
        "title": b["ev_executive_report"],
        "brief": {
            "heading": b["bs_heading"],
            "lead": lead_text(facts, b),
            "findings_heading": b["bs_findings_heading"],
            "headers": (b["bs_th_area"], b["bs_th_finding"], b["bs_th_details"]),
            "rows": [
                {
                    "area": area,
                    "finding": finding,
                    "details": details,
                    "attention": attention,
                }
                for area, finding, details, attention in finding_rows(facts, b)
            ],
            "caption": b["bs_caption"].format(
                version=_version(facts.version), completed=escape(facts.completed)
            ),
        },
        "provenance": {
            "heading": b["ev_provenance_heading"],
            "rows": [
                (
                    b["ev_collected_by"],
                    b["ev_collected_by_value"].format(
                        version=provenance.collector_version
                    ),
                ),
                (b["ev_collected_at"], provenance.collected_date),
                *([(b["ev_run_id"], provenance.run_id)] if provenance.run_id else []),
                (b["ev_catalogue_label"], catalogue),
                (
                    b["ev_platform_label"],
                    (
                        b["ev_platform_uploaded"]
                        if provenance.uploaded
                        else b["ev_platform_none"]
                    ),
                ),
            ],
            "platform_url": provenance.platform_url,
            "document_id": context.document_id,
        },
        "costs_egress_note": egress_note(context, b),
        "introduction": {
            "heading": b["es_heading_introduction"],
            "purpose_heading": b["es_heading_purpose"],
            "purpose": b["es_purpose_block"],
            "scope_heading": b["es_heading_scope"],
            "scope": [scope_boundary(context, b), scope_strategy(context, b)],
            "method_heading": b["es_heading_method"],
            "method": [b["es_method_lead"], b["es_method_phases_lead"]],
            "phases": b["es_method_phases"],
            "method_catalogue": b["es_method_catalogue"],
            "limitations_heading": b["es_heading_limitations"],
            "limitations": [b["err_limitations"], b["err_limitations_data"]],
        },
        "severity": {
            "heading": b["es_heading_severity"],
            "lead": [b["es_severity_lead"], b["es_severity_note"]],
            "headers": (b["risk_th_severity"], b["es_severity_th_meaning"]),
            "rows": [
                (severity_label(level, b), b[f"es_severity_{level}"])
                for level in SEVERITIES
            ],
        },
        "glossary": {
            "heading": b["es_heading_glossary"],
            "lead": b["es_glossary_block"],
            "entries": [
                (entry["term"], entry["definition"])
                for entry in sorted(
                    glossary_entries(context, b), key=lambda e: e["term"].lower()
                )
            ],
        },
        "closing": {
            "heading": b[
                "fa_heading_uploaded" if provenance.uploaded else "fa_heading"
            ],
            "uploaded": provenance.uploaded,
            "lead": b["fa_uploaded"] if provenance.uploaded else b["fa_lead"],
            "points": [] if provenance.uploaded else b["fa_items"],
            "how_heading": b["fa_how_heading"],
            "how": b["fa_how"],
            "command": b["fa_command"],
            "sites": [
                ("EscapeCloud", "https://escapecloud.io"),
                ("ExitCloud", "https://exitcloud.io"),
            ],
            "url": provenance.platform_url,
            "assessment_id": provenance.assessment_id,
        },
        "notices": notices_html(
            context.cloud_service_provider, provenance.generated_at, b
        ),
    }
