# utils.py
import os
import logging
import json
from rich.console import Console
from rich.style import Style
from time import sleep
from datetime import datetime

logger = logging.getLogger("main.utils")
console = Console()

def load_config(file_path):
    try:
        #logger.info(f"Attempting to load config file from {file_path}")
        with open(file_path, "r") as f:
            config = json.load(f)
        #logger.info("Config file loaded successfully.")
        return config
    except Exception as e:
        logger.error(f"Error loading config file: {e}", exc_info=True)
        console.print(f"[red]Error loading config file: {e}[/red]")
        return None

def prompt_required_inputs():
    while True:
        try:
            exit_strategy = int(
                input(
                    "Enter Exit Strategy (1 for 'Repatriation to On-Premises', 3 for 'Migration to Alternate Cloud'): "
                ).strip()
            )
            if exit_strategy not in [1, 3]:
                raise ValueError("Invalid exit strategy.")
            #logger.info(f"Exit Strategy selected: {exit_strategy}")
            break
        except ValueError as e:
            logger.warning(f"Invalid exit strategy input: {e}")
            console.print(f"[red]{e} Please enter 1 or 3.[/red]")

    #while True:
    #    try:
    #        assessment_type = int(
    #            input(
    #                "Enter Assessment Type (1 for 'Basic', 2 for 'Basic+'): "
    #            ).strip()
    #        )
    #        if assessment_type not in [1, 2]:
    #            raise ValueError("Invalid assessment type.")
    #        logger.info(f"Assessment Type selected: {assessment_type}")
    #        break
    #    except ValueError as e:
    #        logger.warning(f"Invalid assessment type input: {e}")
    #        console.print(f"[red]{e} Please enter 1 or 2.[/red]")
    #
    #return exit_strategy, assessment_type
    return exit_strategy

def print_step(description, status="pending", logs=None):
    # Define styles for statuses
    ok_style = Style(color="green", bold=True)
    error_style = Style(color="red", bold=True)
    pending_style = Style(color="yellow", bold=True)

    # Map statuses to their visual representation
    status_map = {
        "ok": "[ ok ]",
        "error": "[ error ]",
        "pending": "[ ... ]",
    }

    # Handle the pending status with a spinner
    if status == "pending":
        with console.status(f"{description:<50} [yellow]{status_map['pending']}[/yellow]", spinner="dots"):
            sleep(2)
            print_step(description, status="ok")
    elif status == "ok":
        console.print(f"{description:<50} {status_map['ok']}", style=ok_style)
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

def create_directory(base_path="reports"):
    # Generate the main directory with a timestamp
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    directory_path = os.path.join(base_path, timestamp)

    # Create the main directory
    os.makedirs(directory_path, exist_ok=True)

    # Create the raw_data subdirectory within the main directory
    raw_data_path = os.path.join(directory_path, "raw_data")
    os.makedirs(raw_data_path, exist_ok=True)

    return directory_path, raw_data_path

def print_help_message():
    console.print("EscapeCloud - Community Edition", style="bold cyan")
    console.print("[green]Run the script with one of the following options:[/green]\n")
    console.print("  python3 main.py aws")
    console.print("  python3 main.py aws --config config/aws.json")
    console.print("  python3 main.py aws --profile PROFILE")
    console.print("  python3 main.py azure")
    console.print("  python3 main.py azure --config config/azure.json")
    console.print("  python3 main.py azure --cli")
