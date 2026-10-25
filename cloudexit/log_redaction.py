import logging
import re

# botocore builds its request signature from the caller's credentials and logs
# the material at DEBUG, so -vv would otherwise copy the access key ID, the
# session token and the signature into the console and into run.log, which is a
# file users attach to bug reports. Redacting the values keeps the surrounding
# request detail readable.
REDACTED = "REDACTED"

_SECRET_KEYS = (
    "authorization",
    "x-amz-security-token",
    "aws_secret_access_key",
    "aws_session_token",
    "client_secret",
)

_RULES = [
    (
        re.compile(
            rf"(?i)({re.escape(key)}['\"]?\s*[:=]\s*)(b?['\"][^'\"]*['\"]|[^,\r\n]*)"
        ),
        rf"\1{REDACTED}",
    )
    for key in _SECRET_KEYS
] + [
    (re.compile(r"(?i)(credential=)[^/\s,]+(/)"), rf"\1{REDACTED}\2"),
    (re.compile(r"(?i)(signature\s*[=:]\s*)[^\s,&'\"]+"), rf"\1{REDACTED}"),
]


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Redact the formatted text, because the secret often sits in an object
        # passed as a %s argument rather than in the format string itself.
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True
