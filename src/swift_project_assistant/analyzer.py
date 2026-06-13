"""SourceKitten-backed Swift structure analysis.

Runs `sourcekitten structure` on Swift files and turns its JSON output into
compact outlines for the MCP tools. SourceKitten uses SourceKit (the Swift
compiler's tooling library), so names, types, and signatures are accurate.

Note: SourceKitten offsets are byte offsets into the UTF-8 source, so all
slicing here happens on bytes, not str.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field

SUB = "key.substructure"
KIND = "key.kind"
NAME = "key.name"
TYPENAME = "key.typename"
OFFSET = "key.offset"
LENGTH = "key.length"
INHERITED = "key.inheritedtypes"
ACCESSIBILITY = "key.accessibility"

# Swift access levels from least to most visible. Used to filter a file down
# to its interface (get_public_interface).
ACCESS_ORDER = ["private", "fileprivate", "internal", "package", "public", "open"]

TYPE_KINDS = {
    "source.lang.swift.decl.class": "class",
    "source.lang.swift.decl.struct": "struct",
    "source.lang.swift.decl.enum": "enum",
    "source.lang.swift.decl.protocol": "protocol",
    "source.lang.swift.decl.actor": "actor",
    "source.lang.swift.decl.extension": "extension",
    "source.lang.swift.decl.extension.class": "extension",
    "source.lang.swift.decl.extension.struct": "extension",
    "source.lang.swift.decl.extension.enum": "extension",
    "source.lang.swift.decl.extension.protocol": "extension",
}

METHOD_KINDS = {
    "source.lang.swift.decl.function.method.instance": "func",
    "source.lang.swift.decl.function.method.static": "static func",
    "source.lang.swift.decl.function.method.class": "class func",
}

PROPERTY_KINDS = {
    "source.lang.swift.decl.var.instance": "",
    "source.lang.swift.decl.var.static": "static ",
    "source.lang.swift.decl.var.class": "class ",
}

FREE_FUNCTION_KIND = "source.lang.swift.decl.function.free"
GLOBAL_VAR_KIND = "source.lang.swift.decl.var.global"
CONSTRUCTOR_KIND = "source.lang.swift.decl.function.constructor"
DESTRUCTOR_KIND = "source.lang.swift.decl.function.destructor"
SUBSCRIPT_KIND = "source.lang.swift.decl.function.subscript"
ENUMCASE_KIND = "source.lang.swift.decl.enumcase"
ENUMELEMENT_KIND = "source.lang.swift.decl.enumelement"
TYPEALIAS_KIND = "source.lang.swift.decl.typealias"
ASSOCIATEDTYPE_KIND = "source.lang.swift.decl.associatedtype"
PARAMETER_KIND = "source.lang.swift.decl.var.parameter"

_IMPORT_RE = re.compile(
    r"^[ \t]*(?:@\w+[ \t]+)?import[ \t]+(?:\w+[ \t]+)?([\w.]+)", re.MULTILINE
)

_BUILTIN_TYPES = {
    "String", "Int", "Int8", "Int16", "Int32", "Int64", "UInt", "UInt8", "UInt16",
    "UInt32", "UInt64", "Double", "Float", "Bool", "Character", "Void", "Any",
    "AnyObject", "Never", "Array", "Dictionary", "Set", "Optional", "Result",
    "Error", "Data", "Date", "URL", "UUID", "Decimal", "Self",
}


class SourceKittenNotFoundError(RuntimeError):
    pass


@dataclass
class Member:
    kind: str  # "method" | "property" | "case" | "initializer" | "typealias" | ...
    name: str  # base name without parameter labels (for matching)
    declaration: str  # readable signature
    accessibility: str | None = None  # "public" | "private" | ... | None if unannotated


@dataclass
class TypeDecl:
    kind: str  # class | struct | enum | protocol | extension | actor
    name: str
    inherits: list[str]
    offset: int  # byte offset of the declaration
    length: int  # byte length including the body
    members: list[Member] = field(default_factory=list)
    member_items: list[dict] = field(default_factory=list)  # raw items, for source lookup
    nested: list["TypeDecl"] = field(default_factory=list)
    accessibility: str | None = None  # "public" | "private" | ... | None if unannotated


@dataclass
class FileAnalysis:
    source: bytes
    imports: list[str]
    types: list[TypeDecl]
    functions: list[Member]
    globals: list[Member]
    function_items: list[dict] = field(default_factory=list)

    def line_of(self, offset: int) -> int:
        return self.source.count(b"\n", 0, offset) + 1

    def slice(self, offset: int, length: int) -> str:
        text = self.source[offset : offset + length].decode("utf-8", errors="replace")
        return textwrap.dedent(text).strip("\n")


def run_sourcekitten(file_path: str) -> dict:
    """Run `sourcekitten structure` on a file and return the parsed JSON."""
    if shutil.which("sourcekitten") is None:
        raise SourceKittenNotFoundError(
            "SourceKitten is required but was not found on PATH. "
            "Install it with `brew install sourcekitten` (macOS) and make sure "
            "Xcode command line tools are available."
        )
    result = subprocess.run(
        ["sourcekitten", "structure", "--file", file_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SourceKitten failed for {file_path}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _base_name(name: str) -> str:
    return name.split("(")[0]


def _access(item: dict) -> str | None:
    """Short access level ("public", "private", …) or None if unannotated."""
    acc = item.get(ACCESSIBILITY)
    return acc.rsplit(".", 1)[-1] if acc else None


def _function_signature(item: dict, keyword: str = "func") -> str:
    """Build a readable signature like `func fetch(for category: Category) -> [Movie]`."""
    full_name = item.get(NAME, "")
    base = _base_name(full_name)
    labels: list[str] = []
    if "(" in full_name and full_name.endswith(")"):
        inner = full_name[len(base) + 1 : -1]
        labels = [p for p in inner.split(":") if p != ""] if inner else []

    params = [
        (p.get(NAME), p.get(TYPENAME))
        for p in item.get(SUB, [])
        if p.get(KIND) == PARAMETER_KIND
    ]
    parts = []
    for i, (pname, ptype) in enumerate(params):
        label = labels[i] if i < len(labels) else (pname or "_")
        internal = pname or "_"
        ptype = ptype or "Any"
        if label == internal:
            parts.append(f"{label}: {ptype}")
        else:
            parts.append(f"{label} {internal}: {ptype}")

    signature = f"{keyword} {base}({', '.join(parts)})" if keyword else f"{base}({', '.join(parts)})"
    return_type = item.get(TYPENAME)
    if return_type and return_type != "Void":
        signature += f" -> {return_type}"
    return signature


def _property_declaration(item: dict, prefix: str = "") -> str:
    name = item.get(NAME, "?")
    typename = item.get(TYPENAME)
    return f"{prefix}{name}: {typename}" if typename else f"{prefix}{name}"


def _parse_members(item: dict) -> tuple[list[Member], list[dict]]:
    members: list[Member] = []
    raw_items: list[dict] = []
    for child in item.get(SUB, []):
        kind = child.get(KIND, "")
        name = child.get(NAME, "")
        acc = _access(child)
        if kind in METHOD_KINDS:
            members.append(Member("method", _base_name(name), _function_signature(child, METHOD_KINDS[kind]), acc))
            raw_items.append(child)
        elif kind == CONSTRUCTOR_KIND:
            members.append(Member("initializer", "init", _function_signature(child, ""), acc))
            raw_items.append(child)
        elif kind == DESTRUCTOR_KIND:
            members.append(Member("deinitializer", "deinit", "deinit", acc))
            raw_items.append(child)
        elif kind == SUBSCRIPT_KIND:
            members.append(Member("subscript", "subscript", _function_signature(child, ""), acc))
            raw_items.append(child)
        elif kind in PROPERTY_KINDS:
            members.append(Member("property", name, _property_declaration(child, PROPERTY_KINDS[kind]), acc))
            raw_items.append(child)
        elif kind == ENUMCASE_KIND:
            for element in child.get(SUB, []):
                if element.get(KIND) == ENUMELEMENT_KIND:
                    members.append(Member("case", _base_name(element.get(NAME, "")), f"case {element.get(NAME, '')}", acc))
                    raw_items.append(element)
        elif kind == TYPEALIAS_KIND:
            members.append(Member("typealias", name, f"typealias {name}", acc))
            raw_items.append(child)
        elif kind == ASSOCIATEDTYPE_KIND:
            members.append(Member("associatedtype", name, f"associatedtype {name}", acc))
            raw_items.append(child)
    return members, raw_items


def _parse_type(item: dict) -> TypeDecl:
    members, raw_items = _parse_members(item)
    decl = TypeDecl(
        kind=TYPE_KINDS[item.get(KIND, "")],
        name=item.get(NAME, "?"),
        inherits=[t.get(NAME, "") for t in item.get(INHERITED, [])],
        offset=item.get(OFFSET, 0),
        length=item.get(LENGTH, 0),
        members=members,
        member_items=raw_items,
        accessibility=_access(item),
    )
    decl.nested = [
        _parse_type(child) for child in item.get(SUB, []) if child.get(KIND) in TYPE_KINDS
    ]
    return decl


def analyze_structure(source: bytes, structure: dict) -> FileAnalysis:
    """Turn raw `sourcekitten structure` JSON into a FileAnalysis."""
    types: list[TypeDecl] = []
    functions: list[Member] = []
    globals_: list[Member] = []
    function_items: list[dict] = []

    for item in structure.get(SUB, []):
        kind = item.get(KIND, "")
        if kind in TYPE_KINDS:
            types.append(_parse_type(item))
        elif kind == FREE_FUNCTION_KIND:
            functions.append(Member("function", _base_name(item.get(NAME, "")), _function_signature(item), _access(item)))
            function_items.append(item)
        elif kind == GLOBAL_VAR_KIND:
            globals_.append(Member("global", item.get(NAME, ""), _property_declaration(item), _access(item)))
            function_items.append(item)

    text = source.decode("utf-8", errors="replace")
    return FileAnalysis(
        source=source,
        imports=_IMPORT_RE.findall(text),
        types=types,
        functions=functions,
        globals=globals_,
        function_items=function_items,
    )


def analyze_file(file_path: str) -> FileAnalysis:
    with open(file_path, "rb") as f:
        source = f.read()
    return analyze_structure(source, run_sourcekitten(file_path))


def outline_to_dict(analysis: FileAnalysis) -> dict:
    """Compact, JSON-friendly representation of a file's structure."""

    def type_dict(t: TypeDecl) -> dict:
        d: dict = {"kind": t.kind, "name": t.name, "line": analysis.line_of(t.offset)}
        if t.inherits:
            d["inherits"] = t.inherits
        if t.members:
            d["members"] = [m.declaration for m in t.members]
        if t.nested:
            d["nested_types"] = [type_dict(n) for n in t.nested]
        return d

    result: dict = {
        "imports": analysis.imports,
        "types": [type_dict(t) for t in analysis.types],
    }
    if analysis.functions:
        result["functions"] = [m.declaration for m in analysis.functions]
    if analysis.globals:
        result["globals"] = [m.declaration for m in analysis.globals]
    return result


def _access_rank(acc: str | None, fallback: int) -> int:
    """Visibility rank of an access level; `fallback` when unannotated/unknown.

    Unannotated members (e.g. enum cases) inherit their enclosing type's rank,
    which the caller passes as `fallback`.
    """
    if acc is None:
        return fallback
    try:
        return ACCESS_ORDER.index(acc)
    except ValueError:
        return fallback


def public_interface_to_dict(analysis: FileAnalysis, min_access: str = "internal") -> dict:
    """A file's outline filtered to its interface, hiding implementation internals.

    Keeps only declarations whose access level is at least `min_access`. The
    default "internal" drops `private`/`fileprivate` (the implementation
    details most files want hidden) while keeping everything the rest of the
    module can see. "public" yields the strict library-public surface.
    """
    if min_access not in ACCESS_ORDER:
        raise ValueError(f"min_access must be one of {ACCESS_ORDER}, got {min_access!r}")
    threshold = ACCESS_ORDER.index(min_access)
    internal_rank = ACCESS_ORDER.index("internal")

    def type_dict(t: TypeDecl, parent_rank: int) -> dict | None:
        rank = _access_rank(t.accessibility, parent_rank)
        if rank < threshold:
            return None
        d: dict = {"kind": t.kind, "name": t.name, "line": analysis.line_of(t.offset)}
        if t.inherits:
            d["inherits"] = t.inherits
        members = [m.declaration for m in t.members if _access_rank(m.accessibility, rank) >= threshold]
        if members:
            d["members"] = members
        nested = [nd for n in t.nested if (nd := type_dict(n, rank)) is not None]
        if nested:
            d["nested_types"] = nested
        return d

    result: dict = {
        "min_access": min_access,
        "imports": analysis.imports,
        "types": [td for t in analysis.types if (td := type_dict(t, internal_rank)) is not None],
    }
    functions = [m.declaration for m in analysis.functions if _access_rank(m.accessibility, internal_rank) >= threshold]
    if functions:
        result["functions"] = functions
    globals_ = [m.declaration for m in analysis.globals if _access_rank(m.accessibility, internal_rank) >= threshold]
    if globals_:
        result["globals"] = globals_
    return result


def find_symbol_source(analysis: FileAnalysis, symbol: str) -> str | None:
    """Source of a declaration by name.

    `symbol` may be a type name ("MovieViewModel"), a qualified member
    ("MovieViewModel.fetchMovies"), or a top-level function/global name.
    Method names match with or without parameter labels, so both
    "fetchMovies" and "fetchMovies(for:)" work.
    """
    type_name, _, member_name = symbol.partition(".")

    def matches(member: Member, target: str) -> bool:
        return member.name == _base_name(target)

    def walk(decls: list[TypeDecl]) -> TypeDecl | None:
        for t in decls:
            if t.name in (type_name, symbol):
                return t
            found = walk(t.nested)
            if found:
                return found
        return None

    decl = walk(analysis.types)
    if decl and not member_name:
        return analysis.slice(decl.offset, decl.length)

    target = member_name or symbol

    def member_source(t: TypeDecl) -> str | None:
        for member, item in zip(t.members, t.member_items):
            if matches(member, target) and OFFSET in item:
                return analysis.slice(item[OFFSET], item[LENGTH])
        for nested in t.nested:
            found = member_source(nested)
            if found:
                return found
        return None

    if decl:
        return member_source(decl)

    for member, item in zip(analysis.functions + analysis.globals, analysis.function_items):
        if matches(member, target) and OFFSET in item:
            return analysis.slice(item[OFFSET], item[LENGTH])
    for t in analysis.types:
        found = member_source(t)
        if found:
            return found
    return None


def referenced_types(structure: dict, declared: set[str]) -> list[str]:
    """Type identifiers referenced anywhere in the file but declared elsewhere."""
    found: set[str] = set()

    def walk(item: dict) -> None:
        typename = item.get(TYPENAME)
        if typename:
            found.update(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", typename))
        for inherited in item.get(INHERITED, []):
            name = inherited.get(NAME, "")
            found.update(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", name))
        for child in item.get(SUB, []):
            walk(child)

    walk(structure)
    return sorted(found - declared - _BUILTIN_TYPES)
