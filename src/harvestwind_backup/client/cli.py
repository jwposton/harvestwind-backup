#!/usr/bin/env python3

import sys
from pathlib import Path

from ..config import ClientConfig
from ..logging_setup import setup_logging
from .runner import ClientRunner


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: harvestwind-backup-client <config.yml>")
        sys.exit(2)

    config_path = Path(sys.argv[1])
    setup_logging("client", Path("/var/log/harvestwind-backup"))
    config = ClientConfig.from_yaml(config_path)
    ok = ClientRunner(config).run()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
