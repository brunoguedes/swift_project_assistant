"""Tests for the in-file cached markdown summary.

`run_sourcekitten` is monkeypatched with the fixture structure from
test_analyzer, so the full cache lifecycle runs without SourceKitten.
"""

import os
from datetime import datetime, timezone

from swift_project_assistant import summary
from swift_project_assistant.analyzer import analyze_structure
from tests.test_analyzer import SOURCE, SOURCE_BYTES, STRUCTURE


def make_analysis():
    return analyze_structure(SOURCE_BYTES, STRUCTURE)


def test_render_markdown():
    md = summary.render_markdown(make_analysis(), "MovieViewModel.swift")
    assert md.startswith("# MovieViewModel.swift")
    assert "**Imports:** Foundation, SwiftUI" in md
    assert "## class MovieViewModel: ObservableObject" in md
    assert "- `func fetchMovies(for category: Category) -> [Movie]`" in md
    assert "### enum MovieViewModel.Category: String" in md
    assert "- `case nowPlaying`" in md
    assert "## Functions" in md
    assert "- `func makeDefaultViewModel() -> MovieViewModel`" in md


def test_block_roundtrip():
    md = summary.render_markdown(make_analysis(), "MovieViewModel.swift")
    generated = datetime(2026, 6, 12, 22, 30, 0, 123456, tzinfo=timezone.utc)
    block = summary.build_block(md, generated)
    source = block + SOURCE

    parsed = summary.extract_block(source)
    assert parsed is not None
    parsed_generated, parsed_md, body_start = parsed
    assert parsed_generated == generated
    assert parsed_md == md
    assert source[body_start:] == SOURCE
    assert summary.strip_block(source) == SOURCE


def test_no_block():
    assert summary.extract_block(SOURCE) is None
    assert summary.strip_block(SOURCE) == SOURCE


def test_sanitize_comment_delimiters():
    block = summary.build_block("contains */ and /* inside\n", datetime.now(timezone.utc))
    # The block must contain exactly one closing delimiter — its own.
    assert block.count("*/") == 1


def write_sample(tmp_path):
    path = tmp_path / "MovieViewModel.swift"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def test_cache_lifecycle(tmp_path, monkeypatch):
    calls = {"count": 0}

    def fake_sourcekitten(file_path):
        calls["count"] += 1
        return STRUCTURE

    monkeypatch.setattr(summary, "run_sourcekitten", fake_sourcekitten)
    path = write_sample(tmp_path)

    # First call: generates and writes the block into the file.
    md1 = summary.get_summary(path)
    assert calls["count"] == 1
    content = path.read_text(encoding="utf-8")
    assert content.startswith(summary.BLOCK_START)
    assert content.endswith(SOURCE)  # original code is intact below the block

    # Second call: served from the in-file cache, SourceKitten not re-run.
    md2 = summary.get_summary(path)
    assert calls["count"] == 1
    assert md2 == md1

    # refresh=True forces regeneration and replaces (not stacks) the block.
    summary.get_summary(path, refresh=True)
    assert calls["count"] == 2
    assert path.read_text(encoding="utf-8").count(summary.BLOCK_START) == 1

    # Editing the file invalidates the cache.
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n// edited\n")
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))
    assert summary.cached_summary(path) is None
    summary.get_summary(path)
    assert calls["count"] == 3


def test_llm_overview_included(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "run_sourcekitten", lambda p: STRUCTURE)
    monkeypatch.setattr(summary, "generate_overview", lambda md, src: "Fetches movies for the UI.")
    path = write_sample(tmp_path)

    md = summary.get_summary(path)
    assert "## Overview\n\nFetches movies for the UI." in md
    # The overview sits between the title and the structural sections.
    assert md.index("# MovieViewModel.swift") < md.index("## Overview") < md.index("## class MovieViewModel")
    # Cached read returns the same enriched summary.
    assert summary.get_summary(path) == md


def test_llm_failure_falls_back_to_structural(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(summary, "run_sourcekitten", lambda p: STRUCTURE)

    def boom(md, src):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(summary, "generate_overview", boom)
    path = write_sample(tmp_path)

    md = summary.get_summary(path)
    assert "## Overview" not in md
    assert "## class MovieViewModel" in md
    assert "ollama unreachable" in capsys.readouterr().err


def test_written_file_mtime_matches_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "run_sourcekitten", lambda p: STRUCTURE)
    path = write_sample(tmp_path)
    summary.get_summary(path)

    parsed = summary.extract_block(path.read_text(encoding="utf-8"))
    generated = parsed[0]
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    assert generated >= mtime  # the write itself must not invalidate the cache
    assert summary.cached_summary(path) is not None


# --- storage modes -------------------------------------------------------


def test_configured_storage_default_and_aliases(monkeypatch):
    monkeypatch.delenv("SUMMARY_STORAGE", raising=False)
    assert summary.configured_storage() is summary.SummaryStorage.SAME_FILE

    for value, expected in [
        ("off", summary.SummaryStorage.OFF),
        ("none", summary.SummaryStorage.OFF),
        ("same-file", summary.SummaryStorage.SAME_FILE),
        ("inline", summary.SummaryStorage.SAME_FILE),
        ("standalone", summary.SummaryStorage.STANDALONE),
        ("sidecar", summary.SummaryStorage.STANDALONE),
        ("MD", summary.SummaryStorage.STANDALONE),
    ]:
        monkeypatch.setenv("SUMMARY_STORAGE", value)
        assert summary.configured_storage() is expected


def test_configured_storage_rejects_unknown(monkeypatch):
    monkeypatch.setenv("SUMMARY_STORAGE", "bogus")
    try:
        summary.configured_storage()
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown SUMMARY_STORAGE")


def test_storage_off_never_writes(tmp_path, monkeypatch):
    calls = {"count": 0}

    def fake_sourcekitten(file_path):
        calls["count"] += 1
        return STRUCTURE

    monkeypatch.setattr(summary, "run_sourcekitten", fake_sourcekitten)
    monkeypatch.setenv("SUMMARY_STORAGE", "off")
    path = write_sample(tmp_path)

    md1 = summary.get_summary(path)
    # Source file is untouched and no sidecar appears.
    assert path.read_text(encoding="utf-8") == SOURCE
    assert not summary.sidecar_path(path).exists()
    # No caching: every call regenerates.
    md2 = summary.get_summary(path)
    assert md2 == md1
    assert calls["count"] == 2


def test_storage_standalone_writes_sidecar_and_caches(tmp_path, monkeypatch):
    calls = {"count": 0}

    def fake_sourcekitten(file_path):
        calls["count"] += 1
        return STRUCTURE

    monkeypatch.setattr(summary, "run_sourcekitten", fake_sourcekitten)
    monkeypatch.setenv("SUMMARY_STORAGE", "standalone")
    path = write_sample(tmp_path)
    md_path = summary.sidecar_path(path)
    assert md_path.name == "MovieViewModel.md"

    # First call writes the sidecar and leaves the .swift file untouched.
    md1 = summary.get_summary(path)
    assert calls["count"] == 1
    assert path.read_text(encoding="utf-8") == SOURCE
    assert md_path.exists()
    raw = md_path.read_text(encoding="utf-8")
    assert raw.startswith("<!-- swift-project-assistant:summary")
    assert "do not edit -->" in raw
    # The returned markdown has no provenance header.
    assert md1.startswith("# MovieViewModel.swift")

    # Second call: served from the sidecar cache, SourceKitten not re-run.
    md2 = summary.get_summary(path)
    assert calls["count"] == 1
    assert md2 == md1

    # Editing the source (newer mtime) invalidates the sidecar.
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))
    assert summary.cached_summary(path) is None
    summary.get_summary(path)
    assert calls["count"] == 2


def test_storage_modes_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "run_sourcekitten", lambda p: STRUCTURE)
    path = write_sample(tmp_path)

    # same-file cache must not satisfy a standalone read, and vice versa.
    monkeypatch.setenv("SUMMARY_STORAGE", "same-file")
    summary.get_summary(path)
    assert summary.cached_summary(path, summary.SummaryStorage.STANDALONE) is None

    monkeypatch.setenv("SUMMARY_STORAGE", "standalone")
    assert summary.get_summary(path).startswith("# MovieViewModel.swift")
    assert summary.sidecar_path(path).exists()
