import re

ACRONYMS = (
    "DORA",
    "FINMA",
    "PRA",
    "ECB",
    "EBA",
    "EBF",
    "ESMA",
    "EIOPA",
    "ESAs",
    "CTPP",
    "ICT",
    "GDPR",
    "NIS2",
    "FCA",
    "BaFin",
    "MaRisk",
    "BAIT",
    "SS2/21",
)

PHRASES = (
    "regulation",
    "regulatory",
    "regulator",
    "regulated",
    "supervisor",
    "supervisory",
    "competent authority",
    "financial entity",
    "outsourcing",
    "compliance",
    "compliant",
    "article 28",
    "art. 28",
    "circular",
    "european banking federation",
    "cloud banking forum",
    "european central bank",
    "control demand",
    "rating grid",
    "concentration risk",
    "third-party service provider",
    "regulatory framework",
    "2022/2554",
)

# Branding of other products' reports. The platforms are named once, on the
# closing page, and that page is checked separately.
FOREIGN_BRANDS = (
    "Community Edition",
    "Generated with exitcloud.io",
    "exitcloud.io document",
)


def find_terms(text):
    found = [
        acronym
        for acronym in ACRONYMS
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(acronym)}(?![A-Za-z0-9])", text)
    ]
    lowered = text.lower()
    found += [phrase for phrase in PHRASES if phrase in lowered]
    found += [brand for brand in FOREIGN_BRANDS if brand.lower() in lowered]
    return found


# Terms the report no longer uses: (pattern, allowed contexts).
RETIRED_TERMS = (
    (r"snapshot", ()),
    (r"\bsync\b|synchroni[sz]", ()),
    (
        r"(?<!data )(?<!risk )\bcatalogue\b",
        ("The catalogue of resource types",),
    ),
    (r"\bhot ?spots?\b", ()),
    (r"\bExit Report\b|\bERR\b(?!-)|Egress (?:& Data Landscape )?Report", ()),
)
AMERICAN = re.compile(
    r"\b(?!(?:un)?sized?\b|sizes\b|sizing\b|seize|prize|capsize)"
    r"\w+(?:iz(?:e|es|ed|ing|ation|ations|er|ers)|yz(?:e|es|ed|ing|er))\b"
    r"|\b(?:catalogs?|behaviors?|behavioral|centers?|centered|colors?|colored"
    r"|favors?|favorable|licenses?|modeling|modeled|labeled|labeling|analyze\w*)\b",
    re.IGNORECASE,
)
# Proper names and data from the provider may keep their own spelling.
AMERICAN_ALLOWED = ("Resource Center", "Azure Monitor")

# Words that stay lower case inside an English Title Case label.
TITLE_SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "vs.",
    "with",
}


def is_title_case(label):
    # Short words stay lower case except as the first word; "vs." may also open
    # a label. Placeholders, numbers and symbols are skipped, and a hyphenated
    # compound only needs its first part capitalised.
    words = re.sub(r"\{[^}]*\}|<[^>]+>", " ", label).split()
    for index, word in enumerate(words):
        core = word.strip("(),.:;·–—’'\"+/")
        if not core or not core[0].isalpha():
            continue
        if word.lower().rstrip(",") in TITLE_SMALL_WORDS:
            if index and word != word.lower():
                return False
            if index == 0 and word.lower() != "vs." and not word[0].isupper():
                return False
            continue
        if not core[0].isupper():
            return False
    return True


def retired_term_hits(text):
    hits = []
    for pattern, allowed in RETIRED_TERMS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            context = text[max(0, match.start() - 40) : match.end() + 40]
            if not any(phrase in context for phrase in allowed):
                hits.append(context)
    return hits


def american_hits(text):
    hits = []
    for match in AMERICAN.finditer(text):
        context = text[max(0, match.start() - 30) : match.end() + 30]
        if not any(name in context for name in AMERICAN_ALLOWED):
            hits.append(context)
    return hits
