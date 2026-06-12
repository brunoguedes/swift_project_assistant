"""In-file cached markdown summaries for Swift files.

The summary of a Swift file is stored inside the file itself, as a comment
block at the very top:

    /* swift-project-assistant:summary
    Generated: 2026-06-12T22:30:00.123456+00:00

    # MovieViewModel.swift
    ...markdown...
    */

The `Generated` timestamp makes the cache self-validating: if it is equal to
or later than the file's modification time, the summary is current and can be
returned without running SourceKitten again. After writing the block we set
the file's mtime to exactly the generated timestamp, so the write itself
doesn't invalidate the cache — only a real edit does.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from swift_project_assistant.analyzer import (
    FileAnalysis,
    TypeDecl,
    analyze_structure,
    run_sourcekitten,
)

BLOCK_START = "/* swift-project-assistant:summary"
BLOCK_END = "*/"

_GENERATED_RE = re.compile(r"^Generated:\s*(\S+)", re.MULTILINE)


def render_markdown(analysis: FileAnalysis, file_name: str) -> str:
    """Render a file analysis as a markdown summary."""
    lines: list[str] = [f"# {file_name}", ""]
    if analysis.imports:
        lines += [f"**Imports:** {', '.join(analysis.imports)}", ""]

    def emit(t: TypeDecl, level: int, prefix: str = "") -> None:
        title = f"{t.kind} {prefix}{t.name}"
        if t.inherits:
            title += f": {', '.join(t.inherits)}"
        lines.append(f"{'#' * level} {title}")
        lines.append("")
        for m in t.members:
            lines.append(f"- `{m.declaration}`")
        if t.members:
            lines.append("")
        for nested in t.nested:
            emit(nested, min(level + 1, 6), f"{prefix}{t.name}.")

    for t in analysis.types:
        emit(t, 2)
    if analysis.functions:
        lines += ["## Functions", ""]
        lines += [f"- `{m.declaration}`" for m in analysis.functions]
        lines.append("")
    if analysis.globals:
        lines += ["## Globals", ""]
        lines += [f"- `{m.declaration}`" for m in analysis.globals]
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _sanitize(markdown: str) -> str:
    # Comment delimiters inside the block would break it (Swift block
    # comments nest), so defuse any that appear in the rendered markdown.
    return markdown.replace("/*", "/ *").replace("*/", "* /")


def build_block(markdown: str, generated: datetime) -> str:
    return f"{BLOCK_START}\nGenerated: {generated.isoformat()}\n\n{_sanitize(markdown)}{BLOCK_END}\n"


def extract_block(source: str) -> tuple[datetime, str, int] | None:
    """Parse the summary block at the top of a file.

    Returns (generated, markdown, body_start_offset) or None if the file has
    no valid summary block.
    """
    if not source.startswith(BLOCK_START):
        return None
    end = source.find(BLOCK_END)
    if end == -1:
        return None
    header = source[:end]
    m = _GENERATED_RE.search(header)
    if not m:
        return None
    try:
        generated = datetime.fromisoformat(m.group(1))
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)

    md_start = header.find("\n\n")
    markdown = header[md_start + 2 :].rstrip() + "\n" if md_start != -1 else ""

    body_start = end + len(BLOCK_END)
    if source[body_start : body_start + 1] == "\n":
        body_start += 1
    return generated, markdown, body_start


def strip_block(source: str) -> str:
    parsed = extract_block(source)
    return source[parsed[2] :] if parsed else source


def cached_summary(path: Path) -> str | None:
    """The stored summary, or None if absent or older than the file's mtime."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parsed = extract_block(source)
    if parsed is None:
        return None
    generated, markdown, _ = parsed
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if generated >= mtime:
        return markdown
    return None


def update_summary(path: Path) -> str:
    """Regenerate the summary via SourceKitten and write it into the file."""
    structure = run_sourcekitten(str(path))
    source_bytes = path.read_bytes()
    analysis = analyze_structure(source_bytes, structure)
    markdown = render_markdown(analysis, path.name)

    body = strip_block(source_bytes.decode("utf-8", errors="replace"))
    generated = datetime.now(timezone.utc)
    path.write_text(build_block(markdown, generated) + body, encoding="utf-8")

    # Pin the mtime to the generated timestamp so the freshly written cache
    # validates as current ("generated >= mtime") until the file is edited.
    timestamp = generated.timestamp()
    os.utime(path, (timestamp, timestamp))
    return markdown


def get_summary(path: Path, refresh: bool = False) -> str:
    if not refresh:
        cached = cached_summary(path)
        if cached is not None:
            return cached
    return update_summary(path)
