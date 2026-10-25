# cloudexit/cli/parser.py
import argparse

from cloudexit.version import __version__


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="EscapeCloud - Community Edition",
        epilog=(
            "Example usage:\n"
            "  python3 main.py aws                        # Use manual input for AWS\n"
            "  python3 main.py aws --config config.json   # Use a configuration file for AWS\n"
            "  python3 main.py aws --profile PROFILE      # Use an AWS CLI profile\n"
            "  python3 main.py aws --name 'DMS System'    # Use a pre-defined assessment name\n"
            "  python3 main.py azure                      # Use manual input for Azure\n"
            "  python3 main.py azure --config config.json # Use a configuration file for Azure\n"
            "  python3 main.py azure --cli                # Use Azure CLI credentials\n"
            "  python3 main.py azure --name 'DMS System'  # Use a pre-defined assessment name\n"
            "  python3 main.py aws --config config.json --dry-run  # Local report, no remote sync\n"
            "  python3 main.py azure --config config.json --dry-run\n"
            "  python3 main.py aws --config config.json --egress    # Estimate egress data volume\n"
            "  python3 main.py azure --config config.json --egress\n"
            "  python3 main.py aws --tfstate infra.tfstate          # Assess a Terraform/OpenTofu state file\n"
            "  python3 main.py azure --tfstate infra.tfstate --dry-run\n"
            "  python3 main.py config path                # Print the config file location\n"
            "  python3 main.py config show                # Show resolved settings and their sources\n"
            "  python3 main.py status                     # Check the platform settings, TLS and key\n"
            "  python3 main.py catalogue status           # Show the installed and latest catalogue\n"
            "  python3 main.py catalogue update           # Download the latest catalogue\n"
            "  python3 main.py catalogue import FILE.db.gz  # Install a catalogue offline\n"
            "  python3 main.py aws --tfstate infra.tfstate --catalogue FILE.db.gz\n"
            "  python3 main.py aws --config config.json --emit-payload out/   # Copy sync.json to out/\n"
            "  python3 main.py aws --config config.json --egress --egress-names hashed\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"cloudexit v{__version__}",
        help="Show the CLI version and exit.",
    )

    subparsers = parser.add_subparsers(
        dest="cloud_provider",
        help="Specify the cloud provider (aws or azure), or `upload`, `spool`, `status`, `config` or `catalogue`.",
    )

    # Shared options available on every subcommand (e.g. `aws --verbose`)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v for INFO, -vv for DEBUG + third-party).",
    )

    # Platform options: flags > CLOUDEXIT_* environment > config file
    platform = argparse.ArgumentParser(add_help=False)
    platform.add_argument(
        "--host", type=str, help="Platform host (https://, or http:// for localhost)."
    )
    platform.add_argument(
        "--api-key-file",
        type=str,
        help="Path to a file containing the platform API key.",
    )
    platform.add_argument(
        "--config-profile",
        type=str,
        help="Profile to use from the cloudexit config file.",
    )
    platform.add_argument(
        "--assessment-id", type=str, help="Existing platform assessment to use."
    )
    platform.add_argument(
        "--ca-bundle", type=str, help="Path to a CA bundle for TLS verification."
    )
    platform.add_argument(
        "--timeout", type=float, help="Platform request timeout in seconds."
    )
    platform.add_argument(
        "--upload-budget",
        type=float,
        help="Seconds an upload may spend retrying before it is spooled (default 300).",
    )

    # What happens to an upload that does not get through
    delivery = argparse.ArgumentParser(add_help=False)
    delivery.add_argument(
        "--spool-on-error",
        action="store_true",
        help="Also spool uploads the platform refused, for a later manual retry.",
    )
    modes = argparse.ArgumentParser(add_help=False)
    modes.add_argument(
        "--upload",
        choices=("auto", "required", "off"),
        help=(
            "auto uploads when a platform is configured (the default), required "
            "fails before collecting when none is, off never uploads."
        ),
    )
    modes.add_argument(
        "--strict",
        action="store_true",
        help="Fail with exit code 10 when the platform reports warnings.",
    )
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="json prints one JSON object on stdout at the end; the step output goes to stderr.",
    )
    flushing = argparse.ArgumentParser(add_help=False)
    flushing.add_argument(
        "--no-flush",
        action="store_true",
        help="Do not deliver spooled syncs of earlier runs before this one.",
    )

    # Run with a catalogue file instead of the installed one (not installed)
    catalogue_flag = argparse.ArgumentParser(add_help=False)
    catalogue_flag.add_argument(
        "--catalogue",
        type=str,
        help="Use this catalogue file (.db or .db.gz) for the run without installing it.",
    )

    # Options for the sync document every assessment writes
    sync_flags = argparse.ArgumentParser(add_help=False)
    sync_flags.add_argument(
        "--emit-payload",
        metavar="DIR",
        type=str,
        help="Also write a copy of the sync document to DIR/<sync id>.json.",
    )
    sync_flags.add_argument(
        "--egress-names",
        choices=("plain", "hashed"),
        default="plain",
        help="Send egress resource names as they are, or as salted hashes.",
    )

    # Subparser for AWS
    aws_parser = subparsers.add_parser(
        "aws",
        parents=[
            common,
            platform,
            delivery,
            flushing,
            modes,
            output,
            catalogue_flag,
            sync_flags,
        ],
        help="Perform an AWS assessment.",
    )
    aws_group = aws_parser.add_mutually_exclusive_group(required=False)
    aws_group.add_argument(
        "--config", type=str, help="Path to the configuration file (JSON format)."
    )
    aws_group.add_argument(
        "--profile",
        type=str,
        help="AWS profile name to use credentials from ~/.aws/credentials.",
    )
    aws_group.add_argument(
        "--tfstate",
        type=str,
        help=(
            "Path to a Terraform/OpenTofu state file. Builds the inventory from "
            "the state instead of the AWS APIs; no credentials are used."
        ),
    )
    aws_parser.add_argument(
        "--name", type=str, help="Assessment Name (Optional / Max. 50 characters)."
    )
    aws_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Run without prompts; read all inputs from environment variables "
            "(for CI use), for example ESC_EXIT_STRATEGY (1, 2 or 3) and "
            "ESC_ASSESSMENT_TYPE (1 or 2)."
        ),
    )
    aws_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run a local assessment without remote sync. The sync document "
            "raw_data/sync.json is written in every run."
        ),
    )
    aws_parser.add_argument(
        "--egress",
        action="store_true",
        help=(
            "Estimate how much data lives in the region and " "would need to move out."
        ),
    )

    # Subparser for Azure
    azure_parser = subparsers.add_parser(
        "azure",
        parents=[
            common,
            platform,
            delivery,
            flushing,
            modes,
            output,
            catalogue_flag,
            sync_flags,
        ],
        help="Perform an Azure assessment.",
    )
    azure_group = azure_parser.add_mutually_exclusive_group(required=False)
    azure_group.add_argument(
        "--config", type=str, help="Path to the configuration file (JSON format)."
    )
    azure_group.add_argument(
        "--cli",
        action="store_true",
        help="Use Azure CLI credentials for authentication.",
    )
    azure_group.add_argument(
        "--tfstate",
        type=str,
        help=(
            "Path to a Terraform/OpenTofu state file. Builds the inventory from "
            "the state instead of the Azure APIs; no credentials are used."
        ),
    )
    azure_parser.add_argument(
        "--name", type=str, help="Assessment Name (Optional / Max. 50 characters)."
    )
    azure_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Run without prompts; read all inputs from environment variables "
            "(for CI use), for example ESC_EXIT_STRATEGY (1, 2 or 3) and "
            "ESC_ASSESSMENT_TYPE (1 or 2)."
        ),
    )
    azure_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run a local assessment without remote sync. The sync document "
            "raw_data/sync.json is written in every run."
        ),
    )
    azure_parser.add_argument(
        "--egress",
        action="store_true",
        help=(
            "Estimate how much data lives in the resource group and "
            "would need to move out."
        ),
    )

    # Subparsers for uploading sync documents and for the spool
    upload_parser = subparsers.add_parser(
        "upload",
        parents=[common, platform, delivery],
        help="Upload sync.json files (or folders of them), for example from an air-gapped machine.",
    )
    upload_parser.add_argument(
        "paths", nargs="+", help="Sync documents, or folders that hold them."
    )
    upload_parser.add_argument(
        "--name",
        type=str,
        help="Assessment name when an account key creates the assessment.",
    )
    upload_parser.add_argument(
        "--exit-strategy",
        type=int,
        choices=(1, 2, 3),
        help="Exit strategy when an account key creates the assessment (1, 2 or 3).",
    )

    spool_parser = subparsers.add_parser(
        "spool", help="Inspect and deliver syncs that could not be uploaded."
    )
    spool_commands = spool_parser.add_subparsers(dest="spool_command", required=True)
    spool_commands.add_parser(
        "list", parents=[common, platform], help="List the spooled syncs."
    )
    spool_commands.add_parser(
        "flush",
        parents=[common, platform],
        help="Upload the spooled syncs, oldest first.",
    )
    purge_parser = spool_commands.add_parser(
        "purge", parents=[common, platform], help="Delete spooled syncs."
    )
    purge_parser.add_argument(
        "--older-than", type=str, help="Only entries older than this, such as 30d."
    )
    purge_parser.add_argument(
        "--yes", action="store_true", help="Delete without asking."
    )

    status_parser = subparsers.add_parser(
        "status",
        parents=[common, platform],
        help="Check the settings, the TLS chain, the key and the platform.",
    )
    status_parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="json prints one JSON object on stdout.",
    )

    # Subparser for the config helpers
    config_parser = subparsers.add_parser(
        "config", help="Inspect where settings come from."
    )
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser(
        "path", parents=[common], help="Print the config file location."
    )
    config_commands.add_parser(
        "show",
        parents=[common, platform],
        help="Show every resolved setting and where it came from.",
    )

    # Subparser for the catalogue dataset
    catalogue_parser = subparsers.add_parser(
        "catalogue", help="Manage the catalogue dataset."
    )
    catalogue_commands = catalogue_parser.add_subparsers(
        dest="catalogue_command", required=True
    )
    catalogue_commands.add_parser(
        "status",
        parents=[common, platform],
        help="Show the installed and the latest catalogue.",
    )
    update_parser = catalogue_commands.add_parser(
        "update", parents=[common], help="Download the latest (or a given) catalogue."
    )
    update_parser.add_argument(
        "--version", type=str, help="Install this version listed in the manifest."
    )
    import_parser = catalogue_commands.add_parser(
        "import", parents=[common], help="Install a catalogue file (air-gapped use)."
    )
    import_parser.add_argument("file", type=str, help="Path to a .db.gz (or .db) file.")
    import_parser.add_argument(
        "--sha256", type=str, help="Expected SHA-256 of the file."
    )

    return parser.parse_args()
