"""Cached markdown summaries for Swift files.

Where a summary is stored is controlled by the SUMMARY_STORAGE environment
variable, with three modes:

    SUMMARY_STORAGE=same-file    # (default) store inside the .swift file
    SUMMARY_STORAGE=standalone   # store in a sidecar .md file next to it
    SUMMARY_STORAGE=off          # never write; always regenerate fresh

**same-file** stores the summary inside the Swift file itself, as a comment
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

**standalone** writes the summary to a sibling `<name>.md` file (e.g.
`MovieViewModel.swift` -> `MovieViewModel.md`), leaving the source untouched.
The cache is current when the `.md` file's mtime is at or after the `.swift`
file's mtime; editing the source makes the source newer and invalidates it.

**off** never touches the filesystem: every call regenerates the summary and
returns it without caching.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from swift_project_assistant.analyzer import (
    FileAnalysis,
    TypeDecl,
    analyze_structure,
    run_sourcekitten,
)
from swift_project_assistant.llm import generate_overview

BLOCK_START = "/* swift-project-assistant:summary"
BLOCK_END = "*/"

_GENERATED_RE = re.compile(r"^Generated:\s*(\S+)", re.MULTILINE)

# Sidecar files (standalone mode) carry the same provenance as an invisible
# HTML comment so a reader knows the file is generated, while it stays out of
# the rendered markdown. It is stripped before the summary is returned.
_STANDALONE_HEADER = "<!-- swift-project-assistant:summary"
_STANDALONE_HEADER_RE = re.compile(
    rf"\A{re.escape(_STANDALONE_HEADER)}[^\n]*-->\n\n"
)


class SummaryStorage(str, Enum):
    """Where get_summary persists a regenerated summary."""

    OFF = "off"
    SAME_FILE = "same-file"
    STANDALONE = "standalone"


_STORAGE_ALIASES = {
    "off": SummaryStorage.OFF,
    "none": SummaryStorage.OFF,
    "disabled": SummaryStorage.OFF,
    "same-file": SummaryStorage.SAME_FILE,
    "same_file": SummaryStorage.SAME_FILE,
    "samefile": SummaryStorage.SAME_FILE,
    "in-file": SummaryStorage.SAME_FILE,
    "infile": SummaryStorage.SAME_FILE,
    "inline": SummaryStorage.SAME_FILE,
    "file": SummaryStorage.SAME_FILE,
    "standalone": SummaryStorage.STANDALONE,
    "sidecar": SummaryStorage.STANDALONE,
    "separate": SummaryStorage.STANDALONE,
    "markdown": SummaryStorage.STANDALONE,
    "md": SummaryStorage.STANDALONE,
}


def configured_storage() -> SummaryStorage:
    """Parse SUMMARY_STORAGE; default to same-file when unset."""
    raw = os.getenv("SUMMARY_STORAGE", "").strip().lower()
    if not raw:
        return SummaryStorage.SAME_FILE
    try:
        return _STORAGE_ALIASES[raw]
    except KeyError:
        raise ValueError(
            f"Unknown SUMMARY_STORAGE {raw!r}. "
            "Use 'off', 'same-file', or 'standalone'."
        ) from None


def sidecar_path(path: Path) -> Path:
    """The standalone `.md` summary path for a Swift file."""
    return path.with_suffix(".md")


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


def _cached_same_file(path: Path) -> str | None:
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


def _cached_standalone(path: Path) -> str | None:
    md_path = sidecar_path(path)
    try:
        content = md_path.read_text(encoding="utf-8")
        md_mtime = md_path.stat().st_mtime
        src_mtime = path.stat().st_mtime
    except (OSError, UnicodeDecodeError):
        return None
    if md_mtime >= src_mtime:
        return _STANDALONE_HEADER_RE.sub("", content, count=1)
    return None


def cached_summary(path: Path, storage: SummaryStorage | None = None) -> str | None:
    """The stored summary, or None if absent, stale, or storage is off."""
    if storage is None:
        storage = configured_storage()
    if storage is SummaryStorage.OFF:
        return None
    if storage is SummaryStorage.STANDALONE:
        return _cached_standalone(path)
    return _cached_same_file(path)


def _insert_overview(markdown: str, overview: str) -> str:
    head, _, rest = markdown.partition("\n\n")
    return f"{head}\n\n## Overview\n\n{overview.strip()}\n\n{rest}"


def _generate_markdown(path: Path) -> str:
    """Run SourceKitten and render the markdown summary, with optional overview.

    When SUMMARY_LLM is configured, an LLM-written prose overview is added to
    the structural summary; LLM failures are logged and skipped so the
    structural summary always succeeds.
    """
    structure = run_sourcekitten(str(path))
    source_bytes = path.read_bytes()
    analysis = analyze_structure(source_bytes, structure)
    markdown = render_markdown(analysis, path.name)

    body = strip_block(source_bytes.decode("utf-8", errors="replace"))

    try:
        overview = generate_overview(markdown, body)
    except Exception as exc:  # noqa: BLE001 - any backend failure is non-fatal
        print(f"swift-project-assistant: LLM overview skipped: {exc}", file=sys.stderr)
        overview = None
    if overview:
        markdown = _insert_overview(markdown, overview)
    return markdown


def _write_same_file(path: Path, markdown: str) -> None:
    body = strip_block(path.read_bytes().decode("utf-8", errors="replace"))
    generated = datetime.now(timezone.utc)
    path.write_text(build_block(markdown, generated) + body, encoding="utf-8")
    # Pin the mtime to the generated timestamp so the freshly written cache
    # validates as current ("generated >= mtime") until the file is edited.
    timestamp = generated.timestamp()
    os.utime(path, (timestamp, timestamp))


def _write_standalone(path: Path, markdown: str) -> None:
    generated = datetime.now(timezone.utc)
    header = (
        f"{_STANDALONE_HEADER} generated {generated.isoformat()}; "
        f"auto-generated from {path.name}, do not edit -->\n\n"
    )
    sidecar_path(path).write_text(header + markdown, encoding="utf-8")


def update_summary(path: Path, storage: SummaryStorage | None = None) -> str:
    """Regenerate the summary and persist it according to the storage mode."""
    if storage is None:
        storage = configured_storage()
    markdown = _generate_markdown(path)
    if storage is SummaryStorage.SAME_FILE:
        _write_same_file(path, markdown)
    elif storage is SummaryStorage.STANDALONE:
        _write_standalone(path, markdown)
    # SummaryStorage.OFF: regenerate and return without writing anything.
    return markdown


def get_summary(path: Path, refresh: bool = False) -> str:
    storage = configured_storage()
    if not refresh:
        cached = cached_summary(path, storage)
        if cached is not None:
            return cached
    return update_summary(path, storage)
