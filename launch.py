"""Portable checkout launcher; additional arguments go to `spc5tool gui`."""
import subprocess
import sys
from pathlib import Path


def main():
    return subprocess.call(
        [sys.executable, "-m", "spc5tool", "gui", "--open-browser", *sys.argv[1:]],
        cwd=Path(__file__).resolve().parent,
    )


if __name__ == "__main__":
    raise SystemExit(main())
