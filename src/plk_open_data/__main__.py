"""Punkt wejścia dla ``python -m plk_open_data``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())