# core/utils.py
import os
import shutil
import logging

from cloudexit.catalogue.store import active_catalogue_path

from cloudexit.resources import asset_path

logger = logging.getLogger("core.engine.utils")

# Icon folder per cloud service provider id
PROVIDER_ICON_DIRS = {1: "azure", 2: "aws"}


def _png_only(src: str, names: list[str]) -> list[str]:
    # Keep directories so copytree still walks the whole tree, drop every
    # file that isn't a PNG. The renderers never load an icon SVG.
    return [
        name
        for name in names
        if not os.path.isdir(os.path.join(src, name))
        and not name.lower().endswith(".png")
    ]


def _icon_dirs(cloud_service_provider: int) -> list[str]:
    provider_dir = PROVIDER_ICON_DIRS.get(cloud_service_provider)

    if provider_dir is None:
        logger.warning(
            "Unknown cloud service provider %s, copying every provider icon set",
            cloud_service_provider,
        )
        return list(PROVIDER_ICON_DIRS.values())

    return [provider_dir]


def copy_assets(report_path: str, cloud_service_provider: int) -> None:
    assets_path = os.path.join(report_path, "assets")

    # Create the 'assets' directory if it doesn't exist
    os.makedirs(assets_path, exist_ok=True)

    for folder in ("css", "img"):
        src_path = asset_path(folder)
        dest_path = os.path.join(assets_path, folder)

        # Only copy if the destination doesn't already exist
        if not os.path.exists(dest_path):
            shutil.copytree(src_path, dest_path, dirs_exist_ok=True)

    # Only the assessed provider's icons travel with the report
    for folder in _icon_dirs(cloud_service_provider):
        src_path = asset_path("icons", folder)
        dest_path = os.path.join(assets_path, "icons", folder)

        # Only copy if the destination doesn't already exist
        if not os.path.exists(dest_path):
            shutil.copytree(src_path, dest_path, ignore=_png_only, dirs_exist_ok=True)

    # Copy the catalogue this run used to data/assessment.db
    db_src_path = str(active_catalogue_path())
    db_dest_dir = os.path.join(report_path, "data")
    db_dest_path = os.path.join(db_dest_dir, "assessment.db")

    # Create the 'data' directory if it doesn't exist
    os.makedirs(db_dest_dir, exist_ok=True)

    # Only copy if the destination doesn't already exist
    if not os.path.exists(db_dest_path):
        shutil.copyfile(db_src_path, db_dest_path)
