"""Tests for the SourceKitten output transformation.

SourceKitten itself isn't available in CI, so these tests feed
`analyze_structure` a fixture shaped exactly like `sourcekitten structure`
JSON (same key names, byte offsets) for the sample source below.
"""

from swift_project_assistant.analyzer import (
    analyze_structure,
    find_symbol_source,
    outline_to_dict,
    referenced_types,
)

SOURCE = """import Foundation
import SwiftUI

final class MovieViewModel: ObservableObject {
    @Published var movies: [Movie] = []
    private let service: MovieService

    init(service: MovieService) {
        self.service = service
    }

    func fetchMovies(for category: Category) async throws -> [Movie] {
        return try await service.load(category)
    }

    enum Category: String {
        case nowPlaying
        case upcoming
    }
}

func makeDefaultViewModel() -> MovieViewModel {
    MovieViewModel(service: LiveMovieService())
}
"""

SOURCE_BYTES = SOURCE.encode()


def offset(snippet: str) -> int:
    return SOURCE.index(snippet)


def span(start_snippet: str, end_snippet: str) -> tuple[int, int]:
    start = offset(start_snippet)
    end = SOURCE.index(end_snippet, start) + len(end_snippet)
    return start, end - start


CLASS_OFFSET, CLASS_LENGTH = span("class MovieViewModel", "}\n}")
FETCH_OFFSET, FETCH_LENGTH = span("func fetchMovies", "load(category)\n    }")
INIT_OFFSET, INIT_LENGTH = span("init(service:", "self.service = service\n    }")
FREE_OFFSET, FREE_LENGTH = span("func makeDefaultViewModel", "LiveMovieService())\n}")
ENUM_OFFSET, ENUM_LENGTH = span("enum Category", "case upcoming\n    }")

STRUCTURE = {
    "key.substructure": [
        {
            "key.kind": "source.lang.swift.decl.class",
            "key.name": "MovieViewModel",
            "key.offset": CLASS_OFFSET,
            "key.length": CLASS_LENGTH,
            "key.inheritedtypes": [{"key.name": "ObservableObject"}],
            "key.substructure": [
                {
                    "key.kind": "source.lang.swift.decl.var.instance",
                    "key.name": "movies",
                    "key.typename": "[Movie]",
                    "key.offset": offset("var movies"),
                    "key.length": len("var movies: [Movie] = []"),
                },
                {
                    "key.kind": "source.lang.swift.decl.var.instance",
                    "key.name": "service",
                    "key.typename": "MovieService",
                    "key.offset": offset("let service"),
                    "key.length": len("let service: MovieService"),
                },
                {
                    "key.kind": "source.lang.swift.decl.function.constructor",
                    "key.name": "init(service:)",
                    "key.offset": INIT_OFFSET,
                    "key.length": INIT_LENGTH,
                    "key.substructure": [
                        {
                            "key.kind": "source.lang.swift.decl.var.parameter",
                            "key.name": "service",
                            "key.typename": "MovieService",
                        }
                    ],
                },
                {
                    "key.kind": "source.lang.swift.decl.function.method.instance",
                    "key.name": "fetchMovies(for:)",
                    "key.typename": "[Movie]",
                    "key.offset": FETCH_OFFSET,
                    "key.length": FETCH_LENGTH,
                    "key.substructure": [
                        {
                            "key.kind": "source.lang.swift.decl.var.parameter",
                            "key.name": "category",
                            "key.typename": "Category",
                        }
                    ],
                },
                {
                    "key.kind": "source.lang.swift.decl.enum",
                    "key.name": "Category",
                    "key.offset": ENUM_OFFSET,
                    "key.length": ENUM_LENGTH,
                    "key.inheritedtypes": [{"key.name": "String"}],
                    "key.substructure": [
                        {
                            "key.kind": "source.lang.swift.decl.enumcase",
                            "key.offset": offset("case nowPlaying"),
                            "key.length": len("case nowPlaying"),
                            "key.substructure": [
                                {
                                    "key.kind": "source.lang.swift.decl.enumelement",
                                    "key.name": "nowPlaying",
                                    "key.offset": offset("nowPlaying"),
                                    "key.length": len("nowPlaying"),
                                }
                            ],
                        },
                        {
                            "key.kind": "source.lang.swift.decl.enumcase",
                            "key.offset": offset("case upcoming"),
                            "key.length": len("case upcoming"),
                            "key.substructure": [
                                {
                                    "key.kind": "source.lang.swift.decl.enumelement",
                                    "key.name": "upcoming",
                                    "key.offset": offset("upcoming"),
                                    "key.length": len("upcoming"),
                                }
                            ],
                        },
                    ],
                },
            ],
        },
        {
            "key.kind": "source.lang.swift.decl.function.free",
            "key.name": "makeDefaultViewModel()",
            "key.typename": "MovieViewModel",
            "key.offset": FREE_OFFSET,
            "key.length": FREE_LENGTH,
        },
    ]
}


def analysis():
    return analyze_structure(SOURCE_BYTES, STRUCTURE)


def test_imports():
    assert analysis().imports == ["Foundation", "SwiftUI"]


def test_types_and_inheritance():
    a = analysis()
    assert len(a.types) == 1
    vm = a.types[0]
    assert (vm.kind, vm.name) == ("class", "MovieViewModel")
    assert vm.inherits == ["ObservableObject"]


def test_member_signatures():
    vm = analysis().types[0]
    decls = {m.name: m.declaration for m in vm.members}
    assert decls["movies"] == "movies: [Movie]"
    assert decls["service"] == "service: MovieService"
    assert decls["init"] == "init(service: MovieService)"
    assert decls["fetchMovies"] == "func fetchMovies(for category: Category) -> [Movie]"


def test_nested_enum():
    vm = analysis().types[0]
    assert len(vm.nested) == 1
    category = vm.nested[0]
    assert (category.kind, category.name) == ("enum", "Category")
    assert [m.name for m in category.members if m.kind == "case"] == ["nowPlaying", "upcoming"]


def test_top_level_function():
    a = analysis()
    assert [f.declaration for f in a.functions] == ["func makeDefaultViewModel() -> MovieViewModel"]


def test_outline_to_dict():
    d = outline_to_dict(analysis())
    assert d["imports"] == ["Foundation", "SwiftUI"]
    vm = d["types"][0]
    assert vm["name"] == "MovieViewModel"
    assert vm["line"] == 4
    assert "func fetchMovies(for category: Category) -> [Movie]" in vm["members"]
    assert vm["nested_types"][0]["name"] == "Category"


def test_find_symbol_source_type():
    src = find_symbol_source(analysis(), "MovieViewModel")
    assert src.startswith("class MovieViewModel")
    assert src.endswith("}")
    assert "fetchMovies" in src


def test_find_symbol_source_method_qualified():
    src = find_symbol_source(analysis(), "MovieViewModel.fetchMovies")
    assert src.startswith("func fetchMovies")
    assert "service.load(category)" in src
    assert "init" not in src


def test_find_symbol_source_method_with_labels():
    assert find_symbol_source(analysis(), "MovieViewModel.fetchMovies(for:)") == find_symbol_source(
        analysis(), "MovieViewModel.fetchMovies"
    )


def test_find_symbol_source_unqualified_member():
    src = find_symbol_source(analysis(), "fetchMovies")
    assert src.startswith("func fetchMovies")


def test_find_symbol_source_nested_type():
    src = find_symbol_source(analysis(), "Category")
    assert src.startswith("enum Category")


def test_find_symbol_source_free_function():
    src = find_symbol_source(analysis(), "makeDefaultViewModel")
    assert "LiveMovieService" in src


def test_find_symbol_source_missing():
    assert find_symbol_source(analysis(), "DoesNotExist") is None


def test_referenced_types():
    declared = {"MovieViewModel", "Category"}
    refs = referenced_types(STRUCTURE, declared)
    assert "Movie" in refs
    assert "MovieService" in refs
    assert "ObservableObject" in refs
    # Declared and builtin types are excluded.
    assert "MovieViewModel" not in refs
    assert "String" not in refs
