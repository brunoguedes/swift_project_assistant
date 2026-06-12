from swift_project_assistant.parser import (
    find_symbol_source,
    outline_to_dict,
    parse_source,
)

SAMPLE = '''
import Foundation
import SwiftUI
@testable import MyApp

/// A view model. The string "class Fake {" should be ignored.
// class CommentedOut {
final class MovieViewModel: ObservableObject, Identifiable {
    @Published var movies: [Movie] = []
    private let service: MovieService
    static let shared = MovieViewModel(service: MovieService())

    init(service: MovieService) {
        self.service = service
    }

    func fetchMovies(for category: Category) async throws -> [Movie] {
        let url = "https://example.com/{notatype}"
        if movies.isEmpty {
            return try await service.load(category)
        }
        return movies
    }

    private func reset() {
        movies = []
    }

    enum Category: String, CaseIterable {
        case nowPlaying = "now_playing"
        case upcoming
    }
}

struct Movie: Codable {
    let id: Int
    var title: String

    var displayTitle: String {
        title.uppercased()
    }
}

protocol MovieService {
    func load(_ category: MovieViewModel.Category) async throws -> [Movie]
}

extension Movie {
    func summary() -> String { "\\(id): \\(title)" }
}

func makeDefaultViewModel() -> MovieViewModel {
    MovieViewModel(service: LiveMovieService())
}

let globalConstant = 42
'''


def outline():
    return parse_source(SAMPLE)


def test_imports():
    assert outline().imports == ["Foundation", "SwiftUI", "MyApp"]


def test_top_level_types():
    names = [(t.kind, t.name) for t in outline().types]
    assert ("class", "MovieViewModel") in names
    assert ("struct", "Movie") in names
    assert ("protocol", "MovieService") in names
    assert ("extension", "Movie") in names
    # Commented-out and string-literal declarations are ignored.
    assert all(t.name not in ("CommentedOut", "Fake") for t in outline().types)


def test_inheritance():
    vm = next(t for t in outline().types if t.name == "MovieViewModel")
    assert vm.inherits == ["ObservableObject", "Identifiable"]


def test_members():
    vm = next(t for t in outline().types if t.name == "MovieViewModel")
    by_kind = {}
    for m in vm.members:
        by_kind.setdefault(m.kind, []).append(m.name)
    assert "fetchMovies" in by_kind["method"]
    assert "reset" in by_kind["method"]
    assert "movies" in by_kind["property"]
    assert "service" in by_kind["property"]
    assert "init" in by_kind["initializer"]
    # Members of nested bodies (like the if-statement) must not leak in.
    assert "url" not in by_kind.get("property", [])


def test_nested_enum_cases():
    vm = next(t for t in outline().types if t.name == "MovieViewModel")
    nested = {t.name: t for t in vm.nested}
    assert "Category" in nested
    cases = [m.name for m in nested["Category"].members if m.kind == "case"]
    assert cases == ["nowPlaying", "upcoming"]


def test_computed_property_accessors_excluded():
    movie = next(t for t in outline().types if t.name == "Movie" and t.kind == "struct")
    props = [m.name for m in movie.members if m.kind == "property"]
    assert set(props) == {"id", "title", "displayTitle"}


def test_protocol_requirements():
    proto = next(t for t in outline().types if t.name == "MovieService")
    assert [m.name for m in proto.members if m.kind == "method"] == ["load"]


def test_top_level_functions_and_globals():
    o = outline()
    assert [f.name for f in o.functions] == ["makeDefaultViewModel"]
    assert [g.name for g in o.globals] == ["globalConstant"]


def test_outline_to_dict_shape():
    d = outline_to_dict(SAMPLE, outline())
    assert d["imports"][0] == "Foundation"
    vm = next(t for t in d["types"] if t["name"] == "MovieViewModel")
    assert vm["kind"] == "class"
    assert vm["line"] > 1
    assert any("func fetchMovies" in m for m in vm["members"])


def test_find_symbol_source_type():
    src = find_symbol_source(SAMPLE, "Movie")
    assert src is not None
    assert src.startswith("struct Movie")
    assert src.rstrip().endswith("}")
    assert "displayTitle" in src


def test_find_symbol_source_method():
    src = find_symbol_source(SAMPLE, "MovieViewModel.fetchMovies")
    assert src is not None
    assert src.startswith("func fetchMovies")
    assert "service.load" in src
    assert "reset" not in src


def test_find_symbol_source_top_level_function():
    src = find_symbol_source(SAMPLE, "makeDefaultViewModel")
    assert src is not None
    assert "LiveMovieService" in src


def test_find_symbol_source_missing():
    assert find_symbol_source(SAMPLE, "DoesNotExist") is None
