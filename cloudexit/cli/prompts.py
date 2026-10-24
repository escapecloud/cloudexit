# cloudexit/cli/prompts.py
import getpass
import sys
import logging
from rich.console import Console
from rich.style import Style
from time import sleep

from cloudexit import codes

logger = logging.getLogger("main.utils")
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


def prompt_required_inputs() -> tuple[int, int]:
    while True:
        try:
            exit_strategy = int(
                ask(
                    "Enter Exit Strategy (1 for 'Repatriation to On-Premises', 3 for 'Migration to Alternate Cloud'): "
                ).strip()
            )
            if exit_strategy not in [1, 3]:
                raise ValueError("Invalid exit strategy.")
            # logger.info(f"Exit Strategy selected: {exit_strategy}")
            break
        except ValueError as e:
            logger.debug(f"Invalid exit strategy input: {e}")
            console.print(f"[red]{e} Please enter 1 or 3.[/red]")

    while True:
        try:
            assessment_type = int(
                ask("Enter Assessment Type (1 for 'Basic', 2 for 'Standard'): ").strip()
            )
            if assessment_type not in [1, 2]:
                raise ValueError("Invalid assessment type.")
            # logger.info(f"Assessment Type selected: {assessment_type}")
            break
        except ValueError as e:
            logger.debug(f"Invalid assessment type input: {e}")
            console.print(f"[red]{e} Please enter 1 or 2.[/red]")

    return exit_strategy, assessment_type


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
            f"{description:<50} [yellow]{status_map['pending']}[/yellow]",
            spinner="dots",
        ):
            sleep(2)
            print_step(description, status="ok")
    elif status == "ok":
        console.print(f"{description:<50} {status_map['ok']}", style=ok_style)
    elif status == "warning":
        console.print(f"{description:<50} {status_map['warning']}", style=warning_style)
        if logs:
            console.print(f"   ↳ {logs}", style="dim")
    elif status == "error":
        console.print(f"{description:<50} {status_map['error']}", style=error_style)
        if logs:
            console.print(f"   ↳ {logs}", style="dim")


ascii_art = r"""
      _                 _           _ _
     | |               | |         (_) |
  ___| | ___  _   _  __| | _____  ___| |_
 / __| |/ _ \| | | |/ _` |/ _ \ \/ / | __|
| (__| | (_) | |_| | (_| |  __/>  <| | |_
 \___|_|\___/ \__,_|\__,_|\___/_/\_\_|\__|


"""


def print_help_message():
    console.print("EscapeCloud - Community Edition", style="bold cyan")
    console.print("[green]Run the script with one of the following options:[/green]\n")
    console.print("  python3 main.py aws")
    console.print("  python3 main.py aws --config config/aws.json")
    console.print("  python3 main.py aws --profile PROFILE")
    console.print("  python3 main.py aws --name 'DMS System' ")
    console.print("  python3 main.py aws --tfstate infra.tfstate")
    console.print("  python3 main.py azure")
    console.print("  python3 main.py azure --config config/azure.json")
    console.print("  python3 main.py azure --cli")
    console.print("  python3 main.py azure --name 'DMS System'")
    console.print("  python3 main.py azure --tfstate infra.tfstate --dry-run")
    console.print("  python3 main.py config path")
    console.print("  python3 main.py config show")
