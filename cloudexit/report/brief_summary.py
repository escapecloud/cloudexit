from dataclasses import dataclass, field
from itertools import pairwise
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph

from .fonts import FONT_MONO
from .helpers import (
    cost_problem,
    format_change,
    format_money,
    severity_label,
    strategy_name,
)
from .palette import (
    HOTSPOT,
    HOTSPOT_BACKGROUND,
    HOTSPOT_COUNT,
    INK,
    MUTED,
    SEVERITIES,
)
from .table_style import data_table, summary_strip, text_style

# List lengths are capped so the page never runs onto a second one.
MAX_HOTSPOTS = 3
STRIP_WIDTHS = [2.6 * cm, 2.9 * cm, 3.9 * cm, 2.9 * cm, 3.7 * cm]

FINDING_AREA = text_style("BriefFindingArea", 8.5, 11, bold=True)
FINDING_TEXT = text_style("BriefFindingText", 8.5, 12)
FINDING_DETAILS = text_style("BriefFindingDetails", 7.5, 10, MUTED)
CAPTION = text_style("BriefCaption", 7.5, 10, MUTED)


@dataclass
class BriefFacts:
    scope_kind: str
    scope: str
    provider: str
    strategy: str
    resources: int
    types: int
    regions: int
    cost_problem: str | None = None
    costs: list = field(default_factory=list)
    currency: str = ""
    severity_counts: dict = field(default_factory=dict)
    most_severe: tuple | None = None
    types_with_alternatives: int = 0
    alternatives: int = 0
    hotspots: list = field(default_factory=list)
    version: str = ""
    completed: str = ""

    @property
    def risks(self):
        return sum(self.severity_counts.values())

    @property
    def critical_high(self):
        return self.severity_counts.get("critical", 0) + self.severity_counts.get(
            "high", 0
        )


def collect_brief_facts(context, b):
    severity_counts = {}
    for risk in context.risks:
        severity_counts[risk.severity] = severity_counts.get(risk.severity, 0) + 1
    most_severe = None
    if context.risks:
        top = context.risks[0]
        most_severe = (top.code, top.name, severity_label(top.severity, b))

    with_alternatives = [entry for entry in context.alternatives if entry.alternatives]
    hotspots = sorted(
        (entry for entry in context.alternatives if not entry.alternatives),
        key=lambda entry: (-entry.resources, entry.name.lower()),
    )
    problem = cost_problem(context, b)

    scope = context.scope
    if scope.kind == "aws":
        scope_text = scope.region or b["ev_dash"]
    elif scope.kind == "azure":
        scope_text = scope.resource_group or b["ev_dash"]
    else:
        scope_text = ""

    return BriefFacts(
        scope_kind=scope.kind,
        scope=scope_text,
        provider=context.provider,
        strategy=strategy_name(context.strategy_code, b),
        resources=context.total_resources,
        types=len(context.resources),
        regions=len(context.regions),
        cost_problem=problem,
        costs=(
            [(entry.month, entry.amount) for entry in context.costs]
            if problem is None
            else []
        ),
        currency=context.costs[0].currency if problem is None else "",
        severity_counts=severity_counts,
        most_severe=most_severe,
        types_with_alternatives=len(with_alternatives),
        alternatives=sum(len(entry.alternatives) for entry in with_alternatives),
        hotspots=[entry.name for entry in hotspots],
        version=context.provenance.run_short_id or b["ev_dash"],
        completed=context.completed,
    )


def _count(b, key, n):
    return b[f"{key}_one"] if n == 1 else b[f"{key}_many"].format(n=n)


def _join(items, b):
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])}{b['bs_and']}{items[-1]}"


def _capitalise(text):
    return text[:1].upper() + text[1:]


def outlook_text(facts, b):
    parts = []
    if facts.critical_high:
        parts.append(_count(b, "bs_part_risks", facts.critical_high))
    if facts.hotspots:
        parts.append(_count(b, "bs_part_hotspots", len(facts.hotspots)))
    if not parts:
        return b["bs_outlook_good"]
    single = len(parts) == 1 and (facts.critical_high or len(facts.hotspots)) == 1
    key = "bs_outlook_attention_one" if single else "bs_outlook_attention"
    return _capitalise(b[key].format(parts=b["bs_and"].join(parts)))


def lead_text(facts, b):
    if facts.scope_kind == "tfstate":
        scope = b["bs_scope_tfstate"]
    else:
        scope = f"<b>{escape(facts.scope)}</b>"
    lead = b["bs_lead"].format(
        scope=scope,
        provider=escape(facts.provider),
        strategy=f"<b>{escape(facts.strategy)}</b>",
    )
    return f"{lead} {outlook_text(facts, b)}"


def severity_breakdown(facts, b):
    # "1 Critical, 3 High": non-zero counts, most severe first.
    return ", ".join(
        f"{facts.severity_counts[level]} {escape(severity_label(level, b))}"
        for level in SEVERITIES
        if facts.severity_counts.get(level)
    )


def risk_finding(facts, b):
    if not facts.risks:
        return b["bs_find_risks_none"]
    code, name, severity = facts.most_severe
    if facts.risks == 1:
        text = b["bs_find_risks_one"].format(
            severity=escape(severity), code=escape(code), name=escape(name)
        )
    else:
        text = b["bs_find_risks_many"].format(
            n=facts.risks,
            counts=severity_breakdown(facts, b),
            code=escape(code),
            name=escape(name),
            severity=escape(severity),
        )
    if not facts.critical_high:
        text = f"{text} {b['bs_find_risks_no_severe']}"
    return text


def _type_list(names, b):
    shown = [f"<b>{escape(name)}</b>" for name in names[:MAX_HOTSPOTS]]
    if len(names) > MAX_HOTSPOTS:
        return f"{', '.join(shown)}{b['bs_more'].format(k=len(names) - MAX_HOTSPOTS)}"
    return _join(shown, b)


def alternatives_finding(facts, b):
    if not facts.types:
        return b["alt_no_resources"]
    if not facts.hotspots:
        key = "bs_find_alts_all_one" if facts.types == 1 else "bs_find_alts_all"
        return b[key].format(n=facts.types, total=facts.alternatives)
    if not facts.types_with_alternatives:
        return b["bs_find_alts_none"].format(types=_type_list(facts.hotspots, b))
    key = (
        "bs_find_alts_some_one"
        if facts.types_with_alternatives == 1
        else "bs_find_alts_some"
    )
    return " ".join(
        (
            b[key].format(
                a=facts.types_with_alternatives,
                n=facts.types,
                total=facts.alternatives,
            ),
            b["bs_find_alts_missing"].format(types=_type_list(facts.hotspots, b)),
        )
    )


def cost_trend(values):
    # Month-on-month changes counted back from the latest month.
    steps = [float(current) - float(previous) for previous, current in pairwise(values)]
    if not steps or steps[-1] == 0:
        return None, 0
    rising = steps[-1] > 0
    count = 0
    for step in reversed(steps):
        if step == 0 or (step > 0) != rising:
            break
        count += 1
    if count < 2:
        return None, 0
    return ("rising" if rising else "falling"), count


def cost_finding(facts, b):
    if facts.cost_problem:
        return escape(facts.cost_problem)
    values = [cost for _month, cost in facts.costs]
    total = sum(values)
    latest_month, latest = facts.costs[-1]
    parts = [
        b["bs_find_costs"].format(
            total=format_money(total, facts.currency),
            average=format_money(total / len(values), facts.currency),
        )
    ]
    if len(facts.costs) > 1:
        previous_month, previous = facts.costs[-2]
        parts.append(
            b["bs_find_costs_latest"].format(
                month=latest_month,
                latest=format_money(latest, facts.currency),
                change=format_change(previous, latest),
                previous=previous_month,
            )
        )
    else:
        parts.append(
            b["bs_find_costs_latest_only"].format(
                month=latest_month, latest=format_money(latest, facts.currency)
            )
        )
    direction, months = cost_trend(values)
    if direction:
        parts.append(b[f"bs_find_costs_{direction}"].format(k=months))
    return " ".join(parts)


def footprint_finding(facts, b):
    if not facts.resources:
        return b["tbl_res_no_resources"]
    return b["bs_find_footprint"].format(
        resources=_count(b, "bs_count_resources", facts.resources),
        types=_count(b, "bs_count_types", facts.types),
        regions=_count(b, "bs_count_regions", facts.regions),
    )


def finding_rows(facts, b):
    # (area, finding markup, details section, attention marker)
    return [
        (
            b["bs_area_risks"],
            risk_finding(facts, b),
            b["es_heading_risk_inventory"],
            facts.critical_high > 0,
        ),
        (
            b["bs_area_alternatives"],
            alternatives_finding(facts, b),
            b["es_heading_alternative_technologies"],
            bool(facts.hotspots),
        ),
        (
            b["bs_area_costs"],
            cost_finding(facts, b),
            b["es_heading_costs_overview"],
            False,
        ),
        (
            b["bs_area_footprint"],
            footprint_finding(facts, b),
            b["es_heading_resource_inventory"],
            False,
        ),
    ]


def _headline_figures(facts, b):
    costs = (
        format_money(sum(cost for _month, cost in facts.costs), facts.currency)
        if facts.costs
        else b["ev_dash"]
    )
    alternatives = (
        b["alt_stat_types_value"].format(a=facts.types_with_alternatives, n=facts.types)
        if facts.types
        else b["ev_dash"]
    )
    return summary_strip(
        [
            (b["res_stat_resources"], str(facts.resources), INK),
            (b["res_stat_types"], str(facts.types), INK),
            (b["bs_stat_costs"], costs, INK),
            (b["risk_stat_unique"], str(facts.risks), INK),
            (
                b["alt_stat_types"],
                alternatives,
                HOTSPOT_COUNT if facts.hotspots else INK,
            ),
        ],
        widths=STRIP_WIDTHS,
    )


def _findings_table(facts, b, appearance):
    rows = [[b["bs_th_area"], b["bs_th_finding"], b["bs_th_details"]]]
    extra = []
    for area, finding, details, attention in finding_rows(facts, b):
        index = len(rows)
        rows.append(
            [
                Paragraph(escape(area), FINDING_AREA),
                Paragraph(finding, FINDING_TEXT),
                Paragraph(escape(details), FINDING_DETAILS),
            ]
        )
        if attention:
            extra.extend(
                [
                    ("LINEBEFORE", (0, index), (0, index), 3, HexColor(HOTSPOT)),
                    (
                        "BACKGROUND",
                        (0, index),
                        (-1, index),
                        HexColor(HOTSPOT_BACKGROUND),
                    ),
                ]
            )
    return data_table(rows, [2.9 * cm, 10.1 * cm, 3.0 * cm], appearance, extra)


def build_brief_summary(facts, styles, appearance, b):
    lead_style = styles["ContentStyle"].clone(
        "BriefLead", fontSize=10, leading=15.5, spaceAfter=12
    )
    version = f'<font name="{FONT_MONO}">{escape(facts.version)}</font>'
    return [
        Paragraph(b["bs_heading"], styles["Heading1"]),
        Paragraph(lead_text(facts, b), lead_style),
        _headline_figures(facts, b),
        Paragraph(b["bs_findings_heading"], styles["Heading2"]),
        _findings_table(facts, b, appearance),
        Paragraph(
            b["bs_caption"].format(version=version, completed=escape(facts.completed)),
            CAPTION,
        ),
    ]
