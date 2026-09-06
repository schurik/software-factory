#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""make_config — generate adws/adw_sssf_config/sssf.config.yaml with great defaults.

Usage:
    uv run <skill>/scripts/make_config.py [--harness pi|claude_code] [--force]

The roster is harness-specific — model shapes, tool vocabulary, and the
`harness_options` block all differ — so the harness is asked if the flag is not
given, exactly as `install.py` asks it.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness                                    # noqa: E402  (path set above)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", help="which coding-agent harness this roster runs "
                                          "on; asked interactively if omitted")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dest = Path.cwd() / "adws" / "adw_sssf_config" / "sssf.config.yaml"
    if dest.exists() and not args.force:
        print(f"{dest} already exists — use --force to overwrite")
        return 1
    harness = _harness.choose(args.harness)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_harness.render_config(harness))
    print(f"wrote {dest} for the {harness} harness")
    # The prompts are harness-specific too, and this script does not stamp them.
    print("note: the prompts in adws/adw_data/prompt_engineering/ are stamped per "
          "harness by install.py — check they match this roster.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
