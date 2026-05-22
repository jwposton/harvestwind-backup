"""HarvestWind backup: Docker client and Borg/cloud server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("harvestwind-backup")
except PackageNotFoundError:
    # Editable checkout without install (e.g. raw PYTHONPATH)
    __version__ = "0.0.0+dev"
