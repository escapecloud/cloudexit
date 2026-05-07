# utils/aws.py
import logging
import shutil
import subprocess

logger = logging.getLogger("main.utils.aws")


def is_aws_cli_installed() -> bool:
    return shutil.which("aws") is not None


def is_aws_profile_valid(profile: str) -> bool:
    try:
        subprocess.run(
            ["aws", "configure", "list", "--profile", profile],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False
