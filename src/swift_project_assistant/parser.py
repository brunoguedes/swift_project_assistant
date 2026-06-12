"""Pure-Python Swift outline parser.

Extracts the structure of a Swift file (imports, type declarations, members)
without compiling it or requiring SourceKitten/Xcode, so it runs anywhere —
including Linux CI boxes and AI-agent sandboxes.

The approach: build a "code mask" of the source where comments and string
literals are blanked out (lengths preserved), then run regexes and brace
matching against the mask while slicing actual text from the original source.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field

TYPE_KINDS = ("class", "struct", "enum", "protocol", "extension", "actor")

_MODIFIERS = (
    r"(?:(?:public|private|internal|fileprivate|open|final|static|class|"
    r"override|required|convenience|indirect|dynamic|lazy|weak|unowned|"
    r"mutating|nonmutating|optional|@\w+(?:\([^)]*\))?)\s+)*"
)

_TYPE_DECL_RE = re.compile(
    rf"(?P<head>{_MODIFIERS}(?P<kind>{'|'.join(TYPE_KINDS)})\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.]*)"
    r"(?:<[^{{\n]*?>)?\s*(?::\s*(?P<inherits>[^{{\n]+?))?\s*)\{"
)

_IMPORT_RE = re.compile(r"^[ \t]*(?:@\w+[ \t]+)?import[ \t]+(?:\w+[ \t]+)?([\w.]+)", re.MULTILINE)

_FUNC_RE = re.compile(rf"^[ \t]*{_MODIFIERS}func\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*|`[^`]+`)", re.MULTILINE)
_INIT_RE = re.compile(rf"^[ \t]*{_MODIFIERS}(?P<name>init\??|deinit|subscript)\b", re.MULTILINE)
_PROP_RE = re.compile(rf"^[ \t]*{_MODIFIERS}(?P<binding>var|let)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_CASE_RE = re.compile(r"^[ \t]*case\s+(?P<body>[^\n{]+)", re.MULTILINE)
_TYPEALIAS_RE = re.compile(rf"^[ \t]*{_MODIFIERS}typealias\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[^\n]*", re.MULTILINE)


@dataclass
class Member:
    kind: str  # "method" | "property" | "case" | "initializer" | "typealias"
    name: str
    declaration: str


@dataclass
class TypeDecl:
    kind: str  # class | struct | enum | protocol | extension | actor
    name: str
    inherits: list[str]
    start: int  # offset of declaration head in source
    end: int  # offset one past the closing brace
    members: list[Member] = field(default_factory=list)
    nested: list["TypeDecl"] = field(default_factory=list)
    body_start: int = 0  # offset just after the opening brace
    body_end: int = 0  # offset of the closing brace


@dataclass
class FileOutline:
    imports: list[str]
    types: list[TypeDecl]
    functions: list[Member]  # top-level functions
    globals: list[Member]  # top-level vars/lets


def _mask_source(source: str) -> str:
    """Return source with comments and string literals blanked (same length)."""
    out = list(source)
    i, n = 0, len(source)

    def blank(start: int, stop: int) -> None:
        for j in range(start, stop):
            if out[j] != "\n":
                out[j] = " "

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            end = n if end == -1 else end
            blank(i, end)
            i = end
        elif ch == "/" and nxt == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if source.startswith("/*", j):
                    depth += 1
                    j += 2
                elif source.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif ch == '"':
            if source.startswith('"""', i):
                end = source.find('"""', i + 3)
                end = n if end == -1 else end + 3
                blank(i, end)
                i = end
            else:
                j = i + 1
                while j < n and source[j] not in ('"', "\n"):
                    j += 2 if source[j] == "\\" else 1
                j = min(j + 1, n)
                blank(i, j)
                i = j
        else:
            i += 1
    return "".join(out)


def _matching_brace(mask: str, open_idx: int) -> int:
    """Index of the brace matching mask[open_idx] (which must be '{'), or -1."""
    depth = 0
    for i in range(open_idx, len(mask)):
        if mask[i] == "{":
            depth += 1
        elif mask[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _top_level_spans(mask: str, start: int, end: int) -> list[tuple[int, int]]:
    """Spans within [start, end) that are at brace-depth 0 relative to start."""
    spans = []
    depth = 0
    span_start = start
    for i in range(start, end):
        if mask[i] == "{":
            if depth == 0:
                spans.append((span_start, i + 1))  # include the '{' so decl heads match
            depth += 1
        elif mask[i] == "}":
            depth -= 1
            if depth == 0:
                span_start = i + 1
    if depth == 0:
        spans.append((span_start, end))
    return spans


def _clean_decl(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip("{").strip()


def _decl_line(source: str, mask: str, match_start: int) -> str:
    """Full declaration text from match start to the opening brace / '=' / EOL."""
    line_end = len(source)
    depth = 0
    for i in range(match_start, len(mask)):
        c = mask[i]
        if c == "(" or c == "[" or c == "<":
            depth += 1
        elif c == ")" or c == "]" or c == ">":
            depth -= 1
        elif depth <= 0 and (c == "{" or c == "=" or c == "\n"):
            line_end = i
            break
    return _clean_decl(source[match_start:line_end])


def _parse_members(source: str, mask: str, start: int, end: int, kind: str) -> list[Member]:
    found: list[tuple[int, Member]] = []
    for span_start, span_end in _top_level_spans(mask, start, end):
        chunk = mask[span_start:span_end]
        for m in _FUNC_RE.finditer(chunk):
            found.append((span_start + m.start(), Member("method", m.group("name"), _decl_line(source, mask, span_start + m.start()))))
        for m in _INIT_RE.finditer(chunk):
            found.append((span_start + m.start(), Member("initializer", m.group("name"), _decl_line(source, mask, span_start + m.start()))))
        for m in _PROP_RE.finditer(chunk):
            found.append((span_start + m.start(), Member("property", m.group("name"), _decl_line(source, mask, span_start + m.start()))))
        for m in _TYPEALIAS_RE.finditer(chunk):
            found.append((span_start + m.start(), Member("typealias", m.group("name"), _decl_line(source, mask, span_start + m.start()))))
        if kind == "enum":
            for m in _CASE_RE.finditer(chunk):
                first = m.group("body").split("(")[0].split(",")[0].split("=")[0].strip()
                found.append((span_start + m.start(), Member("case", first, _decl_line(source, mask, span_start + m.start()))))
    return [member for _, member in sorted(found, key=lambda pair: pair[0])]


def _parse_types(source: str, mask: str, start: int, end: int) -> list[TypeDecl]:
    types: list[TypeDecl] = []
    pos = start
    while pos < end:
        m = _TYPE_DECL_RE.search(mask, pos, end)
        if not m:
            break
        open_brace = m.end() - 1
        close_brace = _matching_brace(mask, open_brace)
        if close_brace == -1 or close_brace > end:
            pos = m.end()
            continue
        inherits_raw = m.group("inherits") or ""
        decl = TypeDecl(
            kind=m.group("kind"),
            name=m.group("name"),
            inherits=[p.strip() for p in inherits_raw.split(",") if p.strip()],
            start=m.start(),
            end=close_brace + 1,
            body_start=open_brace + 1,
            body_end=close_brace,
        )
        decl.members = _parse_members(source, mask, decl.body_start, decl.body_end, decl.kind)
        decl.nested = _parse_types(source, mask, decl.body_start, decl.body_end)
        types.append(decl)
        pos = close_brace + 1
    return types


def parse_source(source: str) -> FileOutline:
    """Parse Swift source text into a structural outline."""
    mask = _mask_source(source)
    types = _parse_types(source, mask, 0, len(source))

    # Top-level (file scope) functions and globals: outside every type body.
    functions: list[Member] = []
    globals_: list[Member] = []
    type_spans = [(t.start, t.end) for t in types]

    def in_type(idx: int) -> bool:
        return any(s <= idx < e for s, e in type_spans)

    for m in _FUNC_RE.finditer(mask):
        if not in_type(m.start()):
            functions.append(Member("function", m.group("name"), _decl_line(source, mask, m.start())))
    for m in _PROP_RE.finditer(mask):
        if not in_type(m.start()):
            globals_.append(Member("global", m.group("name"), _decl_line(source, mask, m.start())))

    return FileOutline(
        imports=_IMPORT_RE.findall(mask),
        types=types,
        functions=functions,
        globals=globals_,
    )


def parse_file(path: str) -> FileOutline:
    with open(path, encoding="utf-8") as f:
        return parse_source(f.read())


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def outline_to_dict(source: str, outline: FileOutline) -> dict:
    """Compact, JSON-friendly representation of an outline."""

    def type_dict(t: TypeDecl) -> dict:
        d: dict = {"kind": t.kind, "name": t.name, "line": _line_of(source, t.start)}
        if t.inherits:
            d["inherits"] = t.inherits
        if t.members:
            d["members"] = [m.declaration for m in t.members]
        if t.nested:
            d["nested_types"] = [type_dict(n) for n in t.nested]
        return d

    result: dict = {"imports": outline.imports, "types": [type_dict(t) for t in outline.types]}
    if outline.functions:
        result["functions"] = [m.declaration for m in outline.functions]
    if outline.globals:
        result["globals"] = [m.declaration for m in outline.globals]
    return result


def find_symbol_source(source: str, symbol: str) -> str | None:
    """Return the source code of a declaration.

    `symbol` may be a type name ("MovieViewModel"), a qualified member
    ("MovieViewModel.fetchMovies"), or a top-level function name.
    """
    mask = _mask_source(source)
    types = _parse_types(source, mask, 0, len(source))

    type_name, _, member_name = symbol.partition(".")

    def walk(decls: list[TypeDecl]) -> TypeDecl | None:
        for t in decls:
            if t.name == type_name or t.name == symbol:
                return t
            found = walk(t.nested)
            if found:
                return found
        return None

    decl = walk(types)
    if decl and not member_name:
        return textwrap.dedent(source[decl.start : decl.end])

    # Search for a member (function/initializer/property) by name.
    search_ranges = (
        [(decl.body_start, decl.body_end)] if decl else [(0, len(source))]
    )
    target = member_name or symbol
    for lo, hi in search_ranges:
        for regex in (_FUNC_RE, _INIT_RE, _PROP_RE):
            for m in regex.finditer(mask, lo, hi):
                if m.group("name") != target:
                    continue
                brace = mask.find("{", m.end(), hi)
                newline = mask.find("\n", m.end(), hi)
                if brace != -1 and (newline == -1 or brace < newline + 200):
                    close = _matching_brace(mask, brace)
                    if close != -1:
                        return textwrap.dedent(source[m.start() : close + 1])
                end = newline if newline != -1 else hi
                return textwrap.dedent(source[m.start() : end])
    return None
