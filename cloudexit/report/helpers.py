from pathlib import Path

from cloudexit.resources import resolve_inside, safe_relative

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def strategy_name(code, b):
    return b.get(f"strategy_name_{code}") or b["ev_dash"]


def strategy_definition(code, b):
    return b.get(f"strategy_def_{code}") or ""


def format_amount(value):
    return f"{float(value or 0):,.2f}"


def format_money(value, currency):
    amount = format_amount(value)
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount}" if symbol else f"{currency} {amount}"


def gib_number(size_bytes, places=1):
    # A size that is not zero never reads as 0.0
    gib = size_bytes / 2**30
    smallest = 10**-places
    if 0 < gib < smallest:
        return f"<{smallest:.{places}f}"
    return f"{gib:,.{places}f}"


def format_gib(size_bytes):
    if size_bytes is None:
        return "n/a"
    return f"{gib_number(size_bytes)} GiB"


def share_label(share):
    # A share that is not zero never reads as "0 %"
    if 0 < share < 0.5:
        return "<1 %"
    return f"{share:.0f} %"


def format_change(previous, current):
    if not previous or current is None:
        return "–"
    change = (float(current) - float(previous)) / float(previous) * 100
    return f"{'+' if change >= 0 else '−'}{abs(change):.1f} %"


def severity_label(severity, b):
    return b.get(f"risk_sev_{severity}", severity)


def cost_problem(context, b):
    # A message in place of the figures and the table, or None when the data
    # is usable.
    if not context.costs:
        if context.scope.kind == "tfstate":
            return b["cost_tfstate"]
        return b["tbl_cost_no_data"]
    if len({entry.currency for entry in context.costs}) > 1:
        return b["cost_mixed_currency"]
    return None


def initials(name):
    # The first letter of each of the first two words; "?" when there is none.
    letters = []
    for word in str(name or "").split():
        first = next((char for char in word if char.isalnum()), "")
        if first:
            letters.append(first.upper())
        if len(letters) == 2:
            break
    return "".join(letters) or "?"


REPORT_ASSETS = "assets"


def report_icon(report_path, icon_rel):
    # The page only points at files in its own folder: an icon the report
    # folder does not hold is left out, and the page draws a placeholder
    found = resolve_inside(Path(report_path) / REPORT_ASSETS, icon_rel)
    if found is not None and found.is_file():
        return f"{REPORT_ASSETS}/{safe_relative(icon_rel).as_posix()}"
    return None


def percent(share):
    # With a no-break space, so a figure and its sign stay together
    return share_label(share).replace(" ", " ")
