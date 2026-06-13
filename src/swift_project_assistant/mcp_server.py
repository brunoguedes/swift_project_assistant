"""MCP server exposing token-frugal Swift code intelligence tools.

AI agents use these tools to understand a Swift codebase structurally —
project layout, type outlines, symbol locations — and to pull in only the
specific source they need, instead of reading whole files into context.

Analysis is powered by SourceKitten (the Swift compiler's tooling library),
so it must run on a machine with SourceKitten installed:
`brew install sourcekitten`.

Run with:  swift-project-mcp  (stdio transport)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from swift_project_assistant.analyzer import (
    FileAnalysis,
    TypeDecl,
    analyze_file,
    analyze_structure,
    extract_doc_comments,
    find_symbol_source,
    format_type_interface,
    outline_to_dict,
    public_interface_to_dict,
    referenced_type_names_in_text,
    referenced_types,
    run_sourcekitten,
)
from swift_project_assistant.summary import extract_block, get_summary

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


def _resolve_file(file_path: str) -> Path:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Not a file: {file_path}")
    return path


def _analyze(file_path: str) -> FileAnalysis:
    return analyze_file(str(_resolve_file(file_path)))


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
            lines = f.read_bytes().count(b"\n") + 1
        except OSError:
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
    project: dict[str, dict] = {}
    for f in _swift_files(project_path, exclude_folders):
        try:
            analysis = analyze_file(str(f))
        except (OSError, RuntimeError) as exc:
            project[str(f.relative_to(root))] = {"error": str(exc)}
            continue
        decls = []

        def collect(types, prefix=""):
            for t in types:
                entry = {"kind": t.kind, "name": prefix + t.name}
                if t.inherits:
                    entry["inherits"] = t.inherits
                decls.append(entry)
                collect(t.nested, prefix + t.name + ".")

        collect(analysis.types)
        if decls or analysis.functions:
            entry: dict = {"types": decls}
            if analysis.functions:
                entry["functions"] = [m.name for m in analysis.functions]
            project[str(f.relative_to(root))] = entry
    return json.dumps(project, indent=1)


@mcp.tool()
def get_file_outline(file_path: str) -> str:
    """Get the structure of one Swift file as JSON with line numbers.

    Prefer get_file_summary when you just need to understand what a file
    contains; call this tool when you additionally need line numbers or
    machine-readable JSON (e.g. to target a follow-up get_symbol_source
    call). Returns imports, types, conformances, property and method
    signatures, enum cases, and nested types — no implementation bodies.
    Roughly 10x fewer tokens than the raw source.
    """
    return json.dumps(outline_to_dict(_analyze(file_path)), indent=1)


@mcp.tool()
def get_public_interface(file_path: str, min_access: str = "internal") -> str:
    """Get a Swift file's interface — its types and members with the internals hidden.

    Like get_file_outline, but filtered by access level so you see only what a
    declaration exposes, not how it works. Call this to understand the intent
    and contract of a file's types without the noise of private helpers and
    stored implementation state.

    `min_access` is the least-visible level to keep:
      - "internal" (default): drop `private` and `fileprivate` members/types;
        keep everything the rest of the module can use. Best for app code,
        where most declarations are unannotated (i.e. internal).
      - "public": keep only the `public`/`open` surface — the strict
        library-public API. (Use for frameworks; on app code it's often empty.)
      - "fileprivate" / "private" / "package": other thresholds if needed.

    For the body of one specific declaration, use get_symbol_source /
    get_implementation instead.
    """
    return json.dumps(public_interface_to_dict(_analyze(file_path), min_access), indent=1)


@mcp.tool()
def find_symbol(project_path: str, symbol: str, exclude_folders: list[str] | None = None) -> str:
    """Find where a type, function, property, or method is declared in a project.

    Call this when you know a symbol's name (e.g. "MovieViewModel" or
    "fetchMovies") but not which file defines it. Returns matching
    declarations with file path and line number.
    """
    root = Path(project_path).expanduser().resolve()
    matches = []
    for f in _swift_files(project_path, exclude_folders):
        try:
            analysis = analyze_file(str(f))
        except (OSError, RuntimeError):
            continue
        rel = str(f.relative_to(root))

        def walk(types, prefix=""):
            for t in types:
                if t.name == symbol:
                    matches.append(
                        {"file": rel, "line": analysis.line_of(t.offset),
                         "kind": t.kind, "name": prefix + t.name}
                    )
                for m in t.members:
                    if m.name == symbol:
                        matches.append(
                            {"file": rel, "kind": m.kind,
                             "name": f"{prefix}{t.name}.{m.name}", "declaration": m.declaration}
                        )
                walk(t.nested, prefix + t.name + ".")

        walk(analysis.types)
        for m in analysis.functions + analysis.globals:
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
    result = find_symbol_source(_analyze(file_path), symbol)
    if result is None:
        return f"Symbol '{symbol}' not found in {file_path}. Use get_file_outline to see available symbols."
    return result


@mcp.tool()
def get_implementation(project_path: str, symbol: str, exclude_folders: list[str] | None = None) -> str:
    """Get the full source of a declaration by name, searching the whole project.

    Like get_symbol_source, but you don't need to know which file the symbol
    lives in — call this when you have a name ("MovieViewModel",
    "MovieViewModel.fetchMovies", or a top-level function) but not its file.
    Returns the complete source (signature and body), each match prefixed with
    a `// <relative path>` comment. If the same name is declared in several
    files, all are returned. Use find_symbol first if you only need locations.
    """
    root = Path(project_path).expanduser().resolve()
    matches: list[str] = []
    for f in _swift_files(project_path, exclude_folders):
        try:
            source = find_symbol_source(analyze_file(str(f)), symbol)
        except (OSError, RuntimeError):
            continue
        if source is not None:
            matches.append(f"// {f.relative_to(root)}\n{source}")
    if not matches:
        return (
            f"Symbol '{symbol}' not found in {root}. Use find_symbol to search "
            "for similar names, or get_project_map to see what's declared."
        )
    return "\n\n".join(matches)


@mcp.tool()
def get_file_summary(file_path: str, refresh: bool = False) -> str:
    """Get a markdown summary of a Swift file: imports, types, member signatures.

    This is the primary tool for understanding a Swift file — call it whenever
    you need to know what a file contains, before reaching for get_file_outline
    or reading the source. Summaries are cached: if the cache is at or newer
    than the file's last modification, it is returned instantly without
    re-running SourceKitten. Otherwise the summary is regenerated. Set
    refresh=true to force regeneration (e.g. after making significant edits).

    Where the cache lives is set by the SUMMARY_STORAGE environment variable:
      - same-file (default): a comment block at the top of the .swift file;
        regenerating rewrites the block in place, leaving the code untouched.
      - standalone: a sibling <name>.md file (e.g. Foo.swift -> Foo.md); the
        .swift file is never modified.
      - off: nothing is written; the summary is regenerated on every call.

    If the server is configured with SUMMARY_LLM (ollama[:model] or
    claude-cli[:model]), regenerated summaries also include an LLM-written
    prose Overview section.
    """
    return get_summary(_resolve_file(file_path), refresh=refresh)


@mcp.tool()
def get_file_dependencies(file_path: str) -> str:
    """Get the imports of a Swift file plus the external type names it references.

    Call this to understand what a file depends on before changing it —
    which modules it imports and which types declared elsewhere it uses.
    """
    path = _resolve_file(file_path)
    structure = run_sourcekitten(str(path))
    analysis = analyze_structure(path.read_bytes(), structure)
    declared: set[str] = set()

    def collect(types):
        for t in types:
            declared.add(t.name)
            collect(t.nested)

    collect(analysis.types)
    return json.dumps(
        {
            "imports": analysis.imports,
            "declares": sorted(declared),
            "references": referenced_types(structure, declared),
        },
        indent=1,
    )


@mcp.tool()
def get_doc_comments(file_path: str) -> str:
    """Get the authored doc comments (/// and /** */) for a file's declarations.

    Returns a map of qualified name (e.g. "MovieViewModel.fetchMovies") to the
    documentation written above it. This is the highest-signal, lowest-token
    description of a file's intent — pair it with get_public_interface to get
    "the contract plus why it exists". Undocumented declarations are omitted.
    """
    return json.dumps(extract_doc_comments(_analyze(file_path)), indent=1)


@mcp.tool()
def find_references(project_path: str, symbol: str, exclude_folders: list[str] | None = None) -> str:
    """Find every place a name is used across a project (call sites, usages).

    Complements find_symbol (which finds where things are *declared*): this
    finds where they are *used*. Returns each hit as file + line number + the
    trimmed source line, so you can assess the impact of a change without
    reading whole files. Matching is textual on the identifier (the last
    component of `symbol`), so results may include unrelated same-named
    symbols; it ignores any cached summary block at the top of a file.
    """
    root = Path(project_path).expanduser().resolve()
    name = symbol.split(".")[-1].split("(")[0]
    word = re.compile(rf"\b{re.escape(name)}\b")
    cap = 400
    refs: list[dict] = []
    truncated = False
    for f in _swift_files(project_path, exclude_folders):
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parsed = extract_block(text)
        skip_until = text[: parsed[2]].count("\n") + 1 if parsed else 0
        for i, line in enumerate(text.split("\n"), start=1):
            if i < skip_until:
                continue
            if word.search(line):
                refs.append({"file": str(f.relative_to(root)), "line": i, "text": line.strip()})
                if len(refs) >= cap:
                    truncated = True
                    break
        if truncated:
            break
    result = {"symbol": symbol, "identifier": name, "count": len(refs), "references": refs}
    if truncated:
        result["truncated"] = f"stopped at {cap} matches; narrow the search"
    return json.dumps(result, indent=1)


def _project_analyses(project_path: str, exclude_folders: list[str] | None) -> dict[str, FileAnalysis]:
    """Parse every Swift file once; returns {relative_path: FileAnalysis}."""
    root = Path(project_path).expanduser().resolve()
    out: dict[str, FileAnalysis] = {}
    for f in _swift_files(project_path, exclude_folders):
        try:
            out[str(f.relative_to(root))] = analyze_file(str(f))
        except (OSError, RuntimeError):
            continue
    return out


@mcp.tool()
def get_context_bundle(
    project_path: str,
    symbol: str,
    min_access: str = "internal",
    max_references: int = 20,
    exclude_folders: list[str] | None = None,
) -> str:
    """Assemble focused context for working on a symbol: its source + contracts.

    Returns the full source of `symbol` followed by the interfaces (signatures,
    not bodies) of the project-declared types it references. This gives an LLM
    the focal code plus just enough of its surroundings to reason and edit
    safely — in one call, instead of many file reads. Types referenced but not
    declared in the project (framework types) are listed by name only.
    """
    analyses = _project_analyses(project_path, exclude_folders)

    focal_src: str | None = None
    focal_rel = ""
    focal_analysis: FileAnalysis | None = None
    also_in: list[str] = []
    for rel, a in analyses.items():
        src = find_symbol_source(a, symbol)
        if src is None:
            continue
        if focal_src is None:
            focal_src, focal_rel, focal_analysis = src, rel, a
        else:
            also_in.append(rel)
    if focal_src is None or focal_analysis is None:
        return f"Symbol '{symbol}' not found in {project_path}. Use find_symbol or get_project_map."

    # Index every declared type so references can be resolved to interfaces.
    index: dict[str, tuple[str, TypeDecl]] = {}
    for rel, a in analyses.items():
        def collect(types: list[TypeDecl]) -> None:
            for t in types:
                index.setdefault(t.name, (rel, t))
                collect(t.nested)
        collect(a.types)

    declared_here = {name for name in index if index[name][0] == focal_rel}
    refs = referenced_type_names_in_text(focal_src, declared_here)

    parts = [f"// ===== {symbol}  ({focal_rel}) ====="]
    if also_in:
        parts.append(f"// (also declared in: {', '.join(also_in)})")
    parts.append(focal_src)

    included: list[str] = []
    external: list[str] = []
    for name in refs:
        if name not in index:
            external.append(name)
            continue
        if len(included) >= max_references:
            continue
        rel, t = index[name]
        parts.append(f"// ----- interface: {name}  ({rel}) -----\n{format_type_interface(t, min_access)}")
        included.append(name)

    footer: list[str] = []
    if external:
        footer.append(f"// external types (not declared in project): {', '.join(external)}")
    overflow = [n for n in refs if n in index][max_references:]
    if overflow:
        footer.append(f"// {len(overflow)} more referenced types omitted (raise max_references): {', '.join(overflow)}")
    return "\n\n".join(parts) + ("\n\n" + "\n".join(footer) if footer else "")


@mcp.tool()
def find_types(
    project_path: str,
    inherits: str | None = None,
    kind: str | None = None,
    exclude_folders: list[str] | None = None,
) -> str:
    """Find types across a project by what they conform to / subclass, or by kind.

    Use this to gather the right set of files for a task in one query — e.g.
    every `View` (inherits="View"), every `ObservableObject`, every conformer
    of a protocol, or every `enum` (kind="enum"). `inherits` matches the type's
    inheritance clause, which covers both superclasses and protocol
    conformances (SourceKitten does not distinguish them). Returns file, line,
    kind, qualified name, and the inheritance list for each match.
    """
    root = Path(project_path).expanduser().resolve()
    matches: list[dict] = []
    for rel, a in _project_analyses(project_path, exclude_folders).items():
        def walk(types: list[TypeDecl], prefix: str = "") -> None:
            for t in types:
                if (kind is None or t.kind == kind) and (inherits is None or inherits in t.inherits):
                    entry = {"file": rel, "line": a.line_of(t.offset), "kind": t.kind, "name": prefix + t.name}
                    if t.inherits:
                        entry["inherits"] = t.inherits
                    matches.append(entry)
                walk(t.nested, prefix + t.name + ".")
        walk(a.types)
    return json.dumps({"inherits": inherits, "kind": kind, "matches": matches}, indent=1)


@mcp.tool()
def get_dependents(project_path: str, type_name: str, exclude_folders: list[str] | None = None) -> str:
    """Find which files reference a type — the reverse of get_file_dependencies.

    Call this before changing or renaming a type to see the blast radius: the
    list of files that use it (excluding the file that declares it). Returns
    just file paths, so it's a very cheap impact check; follow up with
    find_references for the exact lines.
    """
    root = Path(project_path).expanduser().resolve()
    dependents: list[str] = []
    for f in _swift_files(project_path, exclude_folders):
        try:
            structure = run_sourcekitten(str(f))
            analysis = analyze_structure(f.read_bytes(), structure)
        except (OSError, RuntimeError):
            continue
        declared: set[str] = set()

        def collect(types: list[TypeDecl]) -> None:
            for t in types:
                declared.add(t.name)
                collect(t.nested)

        collect(analysis.types)
        if type_name in declared:
            continue
        if type_name in referenced_types(structure, declared):
            dependents.append(str(f.relative_to(root)))
    return json.dumps({"type": type_name, "dependent_files": dependents}, indent=1)


@mcp.tool()
def get_outlines(paths: list[str], exclude_folders: list[str] | None = None) -> str:
    """Get outlines for many files (or whole folders) in a single call.

    The batch form of get_file_outline: pass a list of file and/or directory
    paths and get back every file's structure keyed by path. Directories are
    expanded to the Swift files under them. Use this to map a folder in one
    round trip instead of one call per file.
    """
    result: dict[str, dict] = {}
    for p in paths:
        path = Path(p).expanduser().resolve()
        if path.is_dir():
            for f in _swift_files(str(path), exclude_folders):
                try:
                    result[str(f)] = outline_to_dict(analyze_file(str(f)))
                except (OSError, RuntimeError) as exc:
                    result[str(f)] = {"error": str(exc)}
        elif path.is_file():
            try:
                result[str(path)] = outline_to_dict(analyze_file(str(path)))
            except (OSError, RuntimeError) as exc:
                result[str(path)] = {"error": str(exc)}
        else:
            result[str(path)] = {"error": "not found"}
    return json.dumps(result, indent=1)


@mcp.tool()
def changed_files_context(
    project_path: str,
    git_ref: str = "HEAD",
    interface_only: bool = False,
    exclude_folders: list[str] | None = None,
) -> str:
    """Outline the Swift files changed versus a git ref — focused diff context.

    Call this to feed an LLM only the surface of what changed (for review,
    continuing work, or writing a PR description) instead of the whole repo.
    Returns each changed file's outline (or its public interface when
    interface_only=true), plus any deleted files. `git_ref` defaults to HEAD
    (working tree vs last commit); pass a branch or commit to diff against it.
    """
    root = Path(project_path).expanduser().resolve()
    proc = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", git_ref, "--", "*.swift"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return json.dumps({"error": f"git diff failed: {proc.stderr.strip()}"}, indent=1)
    changed: dict[str, dict] = {}
    deleted: list[str] = []
    for rel in filter(None, proc.stdout.splitlines()):
        fp = root / rel
        if not fp.exists():
            deleted.append(rel)
            continue
        try:
            analysis = analyze_file(str(fp))
        except (OSError, RuntimeError) as exc:
            changed[rel] = {"error": str(exc)}
            continue
        changed[rel] = public_interface_to_dict(analysis) if interface_only else outline_to_dict(analysis)
    return json.dumps({"git_ref": git_ref, "changed": changed, "deleted": deleted}, indent=1)


@mcp.tool()
def search_declarations(project_path: str, pattern: str, exclude_folders: list[str] | None = None) -> str:
    """Search declaration signatures across a project with a regular expression.

    Token-cheap structural discovery for when you know the shape but not the
    name — e.g. pattern="-> \\[Workout\\]" to find functions returning
    [Workout], or "@Published" / "async throws". Matches type headers and
    member/function signatures. Returns file, line/qualified name, and the
    matching declaration.
    """
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return json.dumps({"error": f"invalid regex: {exc}"}, indent=1)
    matches: list[dict] = []
    for rel, a in _project_analyses(project_path, exclude_folders).items():
        def walk(types: list[TypeDecl], prefix: str = "") -> None:
            for t in types:
                header = f"{t.kind} {prefix}{t.name}"
                if t.inherits:
                    header += ": " + ", ".join(t.inherits)
                if regex.search(header):
                    matches.append({"file": rel, "line": a.line_of(t.offset), "declaration": header})
                for m in t.members:
                    if regex.search(m.declaration):
                        matches.append({"file": rel, "name": f"{prefix}{t.name}.{m.name}", "declaration": m.declaration})
                walk(t.nested, prefix + t.name + ".")
        walk(a.types)
        for m in a.functions + a.globals:
            if regex.search(m.declaration):
                matches.append({"file": rel, "declaration": m.declaration})
    return json.dumps({"pattern": pattern, "match_count": len(matches), "matches": matches}, indent=1)


def main() -> None:
    load_dotenv()  # pick up SUMMARY_LLM etc. from a .env in the working directory
    mcp.run()


if __name__ == "__main__":
    main()
