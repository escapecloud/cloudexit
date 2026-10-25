# cloudexit/terminal.py
import getpass
import sys
from rich.console import Console
from rich.markup import escape
from rich.style import Style
from time import sleep

from cloudexit import codes

console = Console()


NO_TERMINAL_MESSAGE = (
    "This run needs answers to prompts, but no terminal is attached. "
    "Use --non-interactive and set ESC_EXIT_STRATEGY and ESC_ASSESSMENT_TYPE "
    "(see --help)."
)


def ask(
    prompt: str, *, secret: bool = False, no_terminal_message: str | None = None
) -> str:
    # Every question goes through here, so a run without a terminal (CI, a
    # pipe, < /dev/null) ends with a message instead of an EOFError traceback.
    try:
        return getpass.getpass(prompt) if secret else input(prompt)
    except EOFError:
        console.print(f"[red]{no_terminal_message or NO_TERMINAL_MESSAGE}[/red]")
        sys.exit(codes.CONFIG)


def print_step(
    description: str, status: str = "pending", logs: str | None = None
) -> None:
    # Define styles for statuses
    ok_style = Style(color="green", bold=True)
    error_style = Style(color="red", bold=True)
    warning_style = Style(color="yellow", bold=True)
    # Map statuses to their visual representation
    status_map = {
        "ok": "[ ok ]",
        "error": "[ error ]",
        "warning": "[ warn ]",
        "pending": "[ ... ]",
    }

    # Handle the pending status with a spinner
    if status == "pending":
        with console.status(
            f"{escape(description):<50} [yellow]{status_map['pending']}[/yellow]",
            spinner="dots",
        ):
            sleep(2)
            print_step(description, status="ok")
    elif status == "ok":
        console.print(
            f"{description:<50} {status_map['ok']}",
            style=ok_style,
            markup=False,
            emoji=False,
        )
    elif status == "warning":
        console.print(
            f"{description:<50} {status_map['warning']}",
            style=warning_style,
            markup=False,
            emoji=False,
        )
        if logs:
            console.print(f"   ↳ {logs}", style="dim", markup=False, emoji=False)
    elif status == "error":
        console.print(
            f"{description:<50} {status_map['error']}",
            style=error_style,
            markup=False,
            emoji=False,
        )
        if logs:
            console.print(f"   ↳ {logs}", style="dim", markup=False, emoji=False)
