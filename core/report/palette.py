import re
from dataclasses import dataclass

WHITE = "#FFFFFF"
INK = "#1A1F24"
SUBTITLE = "#4A5560"
MUTED = "#6B7680"
RULE = "#C9D0D6"
HAIRLINE = "#E3E7EA"
HOTSPOT = "#E8A33D"
HOTSPOT_BACKGROUND = "#FDF6EC"
HOTSPOT_COUNT = "#B45309"
PROVENANCE_BACKGROUND = "#F5F7F8"

# Fixed: the pip count and the word carry the severity without colour.
SEVERITIES = ("critical", "high", "medium", "low")
SEVERITY_COLORS = {
    "critical": "#6E1A14",
    "high": "#B8322A",
    "medium": "#E8A33D",
    "low": "#4A7FB5",
}
SEVERITY_PIPS = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# Header row text sits on the colour's own 0.9 tint; checking the slightly
# darker 0.88 tint covers it.
OWN_TINT_AMOUNT = 0.88


@dataclass(frozen=True)
class Appearance:
    header_footer_color: str = "#005470"
    header_row_color: str = "#005470"
    heading1_color: str = "#005470"
    heading2_color: str = "#005470"


DEFAULT_APPEARANCE = Appearance()

HEX_PATTERN = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value):
    match = HEX_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"{value} is not a valid hex colour")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return f"#{digits.upper()}"


def _channels(value):
    digits = normalize_hex(value)[1:]
    return [int(digits[index : index + 2], 16) / 255 for index in (0, 2, 4)]


def relative_luminance(value):
    def linear(channel):
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in _channels(value))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def tint(value, amount):
    channels = (channel + (1 - channel) * amount for channel in _channels(value))
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in channels)


def contrast_pairs(appearance=DEFAULT_APPEARANCE):
    # (what, text colour, background, minimum ratio): WCAG 2.x asks 4.5:1 for
    # normal text and 3:1 for large text.
    own = tint(appearance.header_row_color, OWN_TINT_AMOUNT)
    return (
        ("body text", INK, WHITE, 4.5),
        ("secondary text", SUBTITLE, WHITE, 4.5),
        ("muted text", MUTED, WHITE, 4.5),
        ("chapter heading", appearance.heading1_color, WHITE, 3.0),
        ("section heading", appearance.heading2_color, WHITE, 4.5),
        ("table header", appearance.header_row_color, own, 4.5),
        ("page number and footer", MUTED, WHITE, 4.5),
        ("attention count", HOTSPOT_COUNT, HOTSPOT_BACKGROUND, 4.5),
        ("provenance note", SUBTITLE, PROVENANCE_BACKGROUND, 4.5),
        ("band and rules", appearance.header_footer_color, WHITE, 3.0),
    )


def contrast_failures(appearance=DEFAULT_APPEARANCE):
    return [
        (what, round(contrast_ratio(text, background), 2), minimum)
        for what, text, background, minimum in contrast_pairs(appearance)
        if contrast_ratio(text, background) < minimum
    ]
