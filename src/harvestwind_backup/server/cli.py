#!/usr/bin/env python3

import os
import sys
from pathlib import Path

from ..config import ServerConfig
from ..logging_setup import setup_logging
from .runner import ServerRunner


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: harvestwind-backup-server <config.yml>")
        sys.exit(2)

    if not os.environ.get("BORG_PASSPHRASE"):
        print(
            "Error: BORG_PASSPHRASE must be set (e.g. via "
            "/etc/harvestwind-backup/environment)."
        )
        sys.exit(1)

    config_path = Path(sys.argv[1])
    setup_logging("server", Path("/var/log/harvestwind-backup"))
    config = ServerConfig.from_yaml(config_path)
    ok = ServerRunner(config).run()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
