import argparse
import sys
from html import escape
from pathlib import Path

from cloudexit.catalogue.store import active_catalogue_path
from tests.samples.render import render_cases
from tests.samples.scenarios import ScenarioError, load_scenarios

DOCUMENTS = (
    ("report.pdf", "Executive Summary"),
    ("egress.pdf", "Egress & Data Landscape"),
)
PAGE = (595, 842)

STYLE = """
body { font: 14px/1.45 system-ui, sans-serif; margin: 2rem; color: #1a1f24; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: .45rem .7rem;
         border-bottom: 1px solid #e3e7ea; }
th { background: #f5f7f8; }
a { color: #005470; }
.failed { color: #b8322a; font-weight: 600; }
.muted { color: #6b7680; }
"""


def parse(argv):
    parser = argparse.ArgumentParser(
        prog="python -m tests.samples",
        description="Render the sample assessments through the real report pipeline.",
    )
    parser.add_argument("--case", nargs="+", metavar="NAME", help="cases to render")
    parser.add_argument("--out", default="samples", help="output folder")
    parser.add_argument(
        "--catalogue", help="catalogue to render with (default: the installed one)"
    )
    parser.add_argument(
        "--sheets", action="store_true", help="contact sheets, needs PyMuPDF"
    )
    return parser.parse_args(argv)


def load_pymupdf():
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return None
    return pymupdf


def write_sheets(result, pymupdf):
    # Four pages to a sheet, two by two
    sheets, number = [], 0
    for name, _title in DOCUMENTS:
        if name not in result.pages:
            continue
        source = pymupdf.open(str(result.directory / name))
        for first in range(0, source.page_count, 4):
            number += 1
            last = min(first + 4, source.page_count)
            sheet = pymupdf.open()
            page = sheet.new_page(width=2 * PAGE[0], height=2 * PAGE[1])
            for slot, index in enumerate(range(first, last)):
                x, y = (slot % 2) * PAGE[0], (slot // 2) * PAGE[1]
                page.show_pdf_page(
                    pymupdf.Rect(x, y, x + PAGE[0], y + PAGE[1]), source, index
                )
            file = f"sheet-{number}.png"
            page.get_pixmap(dpi=110).save(str(result.directory / file))
            sheets.append((f"{name} {first + 1}–{last}", file))
        source.close()
    return sheets


def link(case, file, label):
    return f'<a href="{escape(case)}/{escape(file)}">{escape(label)}</a>'


def overview(scenarios, results, failures, sheets):
    by_case = {result.case: result for result in results}
    rows = []
    for case, scenario in scenarios.items():
        result = by_case.get(case)
        if result is None:
            rows.append(
                f"<tr><td>{escape(case)}</td><td>{escape(scenario.name)}</td>"
                f'<td>{escape(scenario.provider)}</td><td colspan="3" class="failed">'
                f"{escape(failures.get(case, 'not rendered'))}</td></tr>"
            )
            continue
        pages = ", ".join(
            f"{title}: {result.pages[name]}"
            for name, title in DOCUMENTS
            if name in result.pages
        )
        links = [
            link(case, name, title)
            for name, title in (
                ("report.pdf", "Executive Summary PDF"),
                ("index.html", "HTML report"),
                ("egress.pdf", "Egress PDF"),
            )
            if name in result.files
        ]
        links += [link(case, file, label) for label, file in sheets.get(case, [])]
        rows.append(
            f"<tr><td>{escape(case)}</td><td>{escape(scenario.name)}</td>"
            f"<td>{escape(scenario.provider)}</td><td>{escape(scenario.purpose)}</td>"
            f"<td>{escape(pages)}</td><td>{'<br>'.join(links)}</td></tr>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Sample Reports</title><style>{STYLE}</style></head><body>"
        '<h1>Sample reports</h1><p class="muted">Fictional data, rendered through '
        "the real report pipeline.</p><table><thead><tr><th>Case</th><th>Name</th>"
        "<th>Provider</th><th>Purpose</th><th>Pages</th><th>Open</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>\n"
    )


def main(argv=None):
    args = parse(argv)
    try:
        scenarios = load_scenarios()
    except ScenarioError as exc:
        print(f"Scenario error: {exc}", file=sys.stderr)
        return 2

    if args.case:
        unknown = [case for case in args.case if case not in scenarios]
        if unknown:
            print(
                f"No such case: {', '.join(unknown)}. Cases: {', '.join(scenarios)}",
                file=sys.stderr,
            )
            return 2
        scenarios = {case: scenarios[case] for case in scenarios if case in args.case}

    catalogue = Path(args.catalogue) if args.catalogue else active_catalogue_path()
    if not catalogue.is_file():
        print(
            f"No catalogue at {catalogue}. Run cloudexit once so that the "
            "catalogue is downloaded, or pass --catalogue PATH.",
            file=sys.stderr,
        )
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results, failures = render_cases(scenarios.values(), out, catalogue_path=catalogue)

    sheets = {}
    if args.sheets:
        pymupdf = load_pymupdf()
        if pymupdf is None:
            print("PyMuPDF is not installed: no contact sheets (pip install pymupdf).")
        else:
            sheets = {result.case: write_sheets(result, pymupdf) for result in results}

    (out / "index.html").write_text(
        overview(scenarios, results, failures, sheets), encoding="utf-8"
    )
    for result in results:
        print(f"rendered {result.case}")
    for message in failures.values():
        print(f"FAILED {message}", file=sys.stderr)
    print(f"Overview: {out / 'index.html'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
