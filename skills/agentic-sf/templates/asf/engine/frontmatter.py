"""YAML frontmatter: the `---` block a Markdown file may open with.

One file per agent (`asf/agents/<name>/agent.md`) means one file holds two
things for two readers — a mapping the engine parses and enforces, and prose
the model reads verbatim. This is the seam between them. `split` hands back
both halves; `render` in engine.prompts strips the first so the model never
sees the config.
"""

from __future__ import annotations

import yaml

FENCE = "---"


def split(text: str, where: str = "") -> tuple[dict, str]:
    """(frontmatter as a mapping, the body). No frontmatter: ({}, text).

    The block starts on the very first line and ends at the next line that is
    exactly `---`. An unterminated block, or one that is not a mapping, is
    refused — silently treating it as prose would hand the model the config.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != FENCE:
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == FENCE:
            raw = "\n".join(lines[1:index])
            meta = yaml.safe_load(raw) if raw.strip() else {}
            if meta is None:
                meta = {}
            if not isinstance(meta, dict):
                raise SystemExit(f"{where or 'frontmatter'}: the block between the `---` "
                                 f"lines must be a mapping")
            return meta, "\n".join(lines[index + 1:]).lstrip("\n")
    raise SystemExit(f"{where or 'frontmatter'}: opens with `---` but never closes it")


def body(text: str, where: str = "") -> str:
    return split(text, where)[1]
