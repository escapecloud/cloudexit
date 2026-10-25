# cloudexit/cli/prompts.py
import logging

from cloudexit.terminal import ask, console

logger = logging.getLogger(__name__)


def prompt_required_inputs(ask_assessment_type: bool = True) -> tuple[int, int]:
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
            console.print(
                f"{e} Please enter 1 or 3.", style="red", markup=False, emoji=False
            )

    # Standard only exists on a platform, so without an upload there is
    # nothing to choose
    if not ask_assessment_type:
        return exit_strategy, 1

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
            console.print(
                f"{e} Please enter 1 or 2.", style="red", markup=False, emoji=False
            )

    return exit_strategy, assessment_type


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
