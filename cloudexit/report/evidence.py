from xml.sax.saxutils import escape, quoteattr

from reportlab.platypus import Paragraph, Spacer

from .flow import chapter_break
from .table_style import FOOTNOTE, KV_MONO, KV_VALUE, key_value_table, mono, text_style


def _short(value, head=4, tail=4):
    if not isinstance(value, str) or len(value) <= head + tail + 1:
        return value or ""
    return f"{value[:head]}…{value[-tail:]}"


def _section_heading(appearance):
    # Not an outline heading: the style name is not Heading1 or Heading2.
    return text_style(
        "EvidenceSectionHeading",
        10.5,
        14,
        appearance.heading2_color,
        bold=True,
    ).clone("EvidenceSectionHeading", spaceBefore=12, spaceAfter=4, keepWithNext=1)


def _mono_value(text):
    return Paragraph(escape(text), KV_MONO) if text else None


def build_document_table(
    context, b, document_id=None, document_type=None, companion_id=None
):
    provenance = context.provenance
    rows = [
        (
            b["ev_document_id"],
            Paragraph(escape(document_id or context.document_id), KV_MONO),
        ),
        (b["ev_document_type"], document_type or b["ev_executive_report"]),
        *(
            [(b["ev_companion_document"], Paragraph(escape(companion_id), KV_MONO))]
            if companion_id
            else []
        ),
        (b["ev_language"], b["language_name"]),
        (
            b["ev_collected_by"],
            b["ev_collected_by_value"].format(version=provenance.collector_version),
        ),
        (b["ev_collected_at"], provenance.collected_date),
        (b["ev_generated_at"], provenance.generated_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    return key_value_table(rows, context.appearance)


def _not_assessed_text(scope, b):
    parts = [
        _counted(b, key, n)
        for key, n in (
            ("ev_not_assessed_other", scope.not_assessed_other),
            ("ev_not_assessed_unmapped", scope.not_assessed_unmapped),
        )
        if n
    ]
    return ", ".join(parts)


def _counted(b, key, n):
    return b[f"{key}_one"] if n == 1 else b[f"{key}_many"].format(n=n)


def scope_rows(context, b):
    # (label, text, monospaced), the same for the PDF and the HTML report
    scope = context.scope
    values = dict(scope.rows)
    rows = [(b["ev_cloud_provider"], scope.provider or b["ev_dash"], False)]
    if scope.kind == "aws":
        if values.get("account"):
            rows.append((b["ev_account_id"], values["account"], True))
        rows.append((b["ev_region"], values.get("region") or b["ev_dash"], False))
    elif scope.kind == "azure":
        rows.append((b["ev_tenant_id"], values.get("tenant") or b["ev_dash"], True))
        rows.append(
            (b["ev_subscription_id"], values.get("subscription") or b["ev_dash"], True)
        )
        rows.append(
            (
                b["ev_resource_group"],
                values.get("resource_group") or b["ev_dash"],
                False,
            )
        )
    else:
        file_text = values.get("file") or b["ev_dash"]
        if values.get("sha256"):
            file_text = f"{file_text} (SHA-256 {_short(values['sha256'])})"
        rows.append((b["ev_file"], file_text, False))
        if values.get("lineage"):
            state = values["lineage"]
            if values.get("serial") is not None:
                state = f"{state} (serial {values['serial']})"
            rows.append((b["ev_state"], state, True))
        for key, label in (
            ("subscriptions", "ev_subscriptions"),
            ("resource_groups", "ev_resource_groups"),
            ("regions", "ev_regions"),
        ):
            if key in values:
                rows.append((b[label], values[key] or b["ev_not_determinable"], False))
        if scope.not_assessed:
            rows.append((b["ev_not_assessed"], _not_assessed_text(scope, b), False))
    source = b["ev_source_tfstate" if scope.kind == "tfstate" else "ev_source_live"]
    rows.append(
        (
            b["ev_source_label"],
            b["ev_source_value"].format(source=source, completed=context.completed),
            False,
        )
    )
    return rows


def _scope_rows(context, b):
    return [
        (label, _mono_value(text) if mono and text != b["ev_dash"] else text)
        for label, text, mono in scope_rows(context, b)
    ]


def build_scope_table(context, b):
    return key_value_table(_scope_rows(context, b), context.appearance)


def link_markup(url, color):
    return f'<a href={quoteattr(url)} color="{color}">{escape(url)}</a>'


def platform_failure(provenance, b):
    # What happened to an upload that failed, as it was true when the report was written
    if provenance.upload_reason:
        text = b["ev_platform_failed"].format(
            reason=provenance.upload_reason.rstrip(". ")
        )
    else:
        text = b["ev_platform_failed_no_reason"]
    if provenance.upload_spooled:
        text = f"{text} {b['ev_platform_kept']}"
    return text


def build_provenance_table(context, b):
    provenance = context.provenance
    if provenance.catalogue_version and provenance.catalogue_sha256:
        catalogue = b["ev_catalogue_value"].format(
            version=escape(provenance.catalogue_version),
            sha=mono(escape(provenance.catalogue_sha256[:12])),
        )
    elif provenance.catalogue_version:
        catalogue = escape(provenance.catalogue_version)
    else:
        catalogue = escape(b["ev_catalogue_unknown"])

    rows = []
    if provenance.run_id:
        rows.append((b["ev_run_id"], Paragraph(escape(provenance.run_id), KV_MONO)))
    rows.append((b["ev_catalogue_label"], Paragraph(catalogue, KV_VALUE)))
    if provenance.uploaded:
        text = escape(b["ev_platform_uploaded"])
        if provenance.platform_url:
            text = f"{text}: {link_markup(provenance.platform_url, context.appearance.heading2_color)}"
        elif provenance.assessment_id:
            text = f"{text} · {escape(b['fa_uploaded_id'].format(assessment_id=provenance.assessment_id))}"
        rows.append((b["ev_platform_label"], Paragraph(text, KV_VALUE)))
    elif provenance.upload_failed:
        rows.append((b["ev_platform_label"], platform_failure(provenance, b)))
    else:
        rows.append((b["ev_platform_label"], b["ev_platform_none"]))
    return key_value_table(rows, context.appearance)


def build_document_information_page(
    context, b, document_id=None, document_type=None, companion_id=None
):
    heading = _section_heading(context.appearance)
    return [
        Paragraph(b["ev_document_information_heading"], heading),
        build_document_table(context, b, document_id, document_type, companion_id),
        Paragraph(b["ev_assessment_scope_heading"], heading),
        build_scope_table(context, b),
        Paragraph(b["ev_provenance_heading"], heading),
        build_provenance_table(context, b),
        Spacer(1, 6),
        Paragraph(
            escape(
                b["ev_stored_footnote"].format(
                    run=context.provenance.run_short_id or b["ev_dash"]
                )
            ),
            FOOTNOTE,
        ),
        chapter_break(),
    ]
