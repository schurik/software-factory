"""Prompt rendering: load system/user refs from config, replace {{placeholders}}."""

from __future__ import annotations

from pathlib import Path

from . import frontmatter


def render(template_path: str | Path, variables: dict[str, str]) -> str:
    """The file's prose with its placeholders filled. A leading YAML
    frontmatter block (an agent.md's config half) is not prose: it is
    stripped, so the model never reads the engine's settings."""
    text = frontmatter.body(Path(template_path).read_text(), str(template_path))
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def save(directory: str | Path, name: str, content: str) -> Path:
    """Save the exact prompt sent, before execution — the audit copy."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    return path
