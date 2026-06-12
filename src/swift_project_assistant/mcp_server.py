"""MCP server exposing token-frugal Swift code intelligence tools.

AI agents use these tools to understand a Swift codebase structurally —
project layout, type outlines, symbol locations — and to pull in only the
specific source they need, instead of reading whole files into context.

Run with:  swift-project-mcp  (stdio transport)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from swift_project_assistant.parser import (
    find_symbol_source,
    outline_to_dict,
    parse_source,
)

DEFAULT_EXCLUDES = {".git", ".build", "Pods", "Carthage", "DerivedData", ".swiftpm"}

mcp = FastMCP("swift-project-assistant")


def _swift_files(project_path: str, exclude_folders: list[str] | None = None) -> list[Path]:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {project_path}")
    excludes = DEFAULT_EXCLUDES | set(exclude_folders or [])
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excludes and not d.startswith(".")]
        files.extend(Path(dirpath) / f for f in filenames if f.endswith(".swift"))
    return sorted(files)


def _read(file_path: str) -> str:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Not a file: {file_path}")
    return path.read_text(encoding="utf-8")


@mcp.tool()
def list_swift_files(project_path: str, exclude_folders: list[str] | None = None) -> str:
    """List every Swift file in a project, with line counts.

    Call this first when starting to work with an unfamiliar Swift project to
    learn its layout. Returns relative paths so follow-up calls can target
    specific files.
    """
    root = Path(project_path).expanduser().resolve()
    files = _swift_files(project_path, exclude_folders)
    entries = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").count("\n") + 1
        except (OSError, UnicodeDecodeError):
            lines = 0
        entries.append({"path": str(f.relative_to(root)), "lines": lines})
    return json.dumps({"root": str(root), "file_count": len(entries), "files": entries}, indent=1)


@mcp.tool()
def get_project_map(project_path: str, exclude_folders: list[str] | None = None) -> str:
    """Get a compact map of every type declared in a Swift project.

    Call this to understand what a project contains and where, without reading
    any source. Returns, per file, the declared types (classes, structs, enums,
    protocols, actors, extensions) and what they inherit/conform to. Use
    get_file_outline or get_symbol_source afterwards to drill into specifics.
    """
    root = Path(project_path).expanduser().resolve()
    project: dict[str, list[dict]] = {}
    for f in _swift_files(project_path, exclude_folders):
        try:
            outline = parse_source(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        decls = []

        def collect(types, prefix=""):
            for t in types:
                entry = {"kind": t.kind, "name": prefix + t.name}
                if t.inherits:
                    entry["inherits"] = t.inherits
                decls.append(entry)
                collect(t.nested, prefix + t.name + ".")

        collect(outline.types)
        if decls or outline.functions:
            entry: dict = {"types": decls}
            if outline.functions:
                entry["functions"] = [m.name for m in outline.functions]
            project[str(f.relative_to(root))] = entry
    return json.dumps(project, indent=1)


@mcp.tool()
def get_file_outline(file_path: str) -> str:
    """Get the structure of one Swift file without its implementation bodies.

    Call this instead of reading a file when you need to know what it declares:
    imports, types, conformances, property and method signatures, enum cases,
    and nested types. Roughly 10x fewer tokens than the raw source.
    """
    source = _read(file_path)
    return json.dumps(outline_to_dict(source, parse_source(source)), indent=1)


@mcp.tool()
def find_symbol(project_path: str, symbol: str, exclude_folders: list[str] | None = None) -> str:
    """Find where a type, function, property, or method is declared in a project.

    Call this when you know a symbol's name (e.g. "MovieViewModel" or
    "fetchDetails") but not which file defines it. Returns matching
    declarations with file path and line number.
    """
    root = Path(project_path).expanduser().resolve()
    matches = []
    for f in _swift_files(project_path, exclude_folders):
        try:
            source = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        outline = parse_source(source)
        rel = str(f.relative_to(root))

        def walk(types, prefix=""):
            for t in types:
                if t.name == symbol:
                    matches.append(
                        {"file": rel, "line": source.count("\n", 0, t.start) + 1,
                         "kind": t.kind, "name": prefix + t.name}
                    )
                for m in t.members:
                    if m.name == symbol:
                        matches.append(
                            {"file": rel, "kind": m.kind,
                             "name": f"{prefix}{t.name}.{m.name}", "declaration": m.declaration}
                        )
                walk(t.nested, prefix + t.name + ".")

        walk(outline.types)
        for m in outline.functions + outline.globals:
            if m.name == symbol:
                matches.append({"file": rel, "kind": m.kind, "name": m.name, "declaration": m.declaration})
    return json.dumps({"symbol": symbol, "matches": matches}, indent=1)


@mcp.tool()
def get_symbol_source(file_path: str, symbol: str) -> str:
    """Get the full source code of a single declaration from a Swift file.

    Call this when you need the actual implementation of one type or method
    rather than the whole file. `symbol` accepts a type name ("MovieViewModel"),
    a qualified member ("MovieViewModel.fetchMovies"), or a top-level function
    name. Combine with find_symbol to locate the file first.
    """
    source = _read(file_path)
    result = find_symbol_source(source, symbol)
    if result is None:
        return f"Symbol '{symbol}' not found in {file_path}. Use get_file_outline to see available symbols."
    return result


@mcp.tool()
def get_file_dependencies(file_path: str) -> str:
    """Get the imports of a Swift file plus the external type names it references.

    Call this to understand what a file depends on before changing it —
    which modules it imports and which types declared elsewhere it uses.
    """
    source = _read(file_path)
    outline = parse_source(source)
    declared: set[str] = set()

    def collect(types):
        for t in types:
            declared.add(t.name)
            collect(t.nested)

    collect(outline.types)
    inherited: set[str] = set()

    def collect_inherits(types):
        for t in types:
            inherited.update(i.split("<")[0].strip() for i in t.inherits)
            collect_inherits(t.nested)

    collect_inherits(outline.types)
    return json.dumps(
        {
            "imports": outline.imports,
            "declares": sorted(declared),
            "inherits_or_conforms_to": sorted(inherited - declared),
        },
        indent=1,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
