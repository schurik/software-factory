"""Tests import the TEMPLATE modules directly — the same files install.py stamps.

`templates/adws` is not a package root anyone installs, so it goes on sys.path
here rather than being pip-installed. Nothing in this directory is stamped into
a consuming repo: install.py copies `templates/`, and this lives beside it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "templates" / "adws"))
