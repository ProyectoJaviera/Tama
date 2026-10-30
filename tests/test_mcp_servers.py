import os

import pytest

from tama.mcp_servers import archivos, notas


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    root = tmp_path / "trabajo"
    monkeypatch.setenv("TAMA_WORKDIR", str(root))
    return root


def test_notes_create_and_search_ignoring_accents(data_dir):
    assert "creada" in notas.create_note("Compras", "Mañana compro filamento PLA")
    notas.create_note("Ideas", "Diseñar un soporte para el teléfono")

    found = notas.search_notes("filamento")
    assert "Compras" in found and "Ideas" not in found
    assert "Compras" in notas.search_notes("manana")  # sin tilde también encuentra
    assert (data_dir / "notas.db").exists()


def test_notes_empty_query_lists_recent(data_dir):
    notas.create_note("Primera", "uno")
    notas.create_note("Segunda", "dos")
    assert notas.search_notes().index("Segunda") < notas.search_notes().index("Primera")


def test_notes_search_handles_symbols(data_dir):
    notas.create_note("Fórmula", "a+b=c")
    assert "No hay notas" in notas.search_notes('"* OR (')  # no rompe la consulta FTS


def test_notes_update_and_delete(data_dir):
    notas.create_note("Viaje", "ir a Valparaíso")
    assert "actualizada" in notas.update_note(1, content="ir a Viña del Mar")
    assert "Viña" in notas.search_notes("viña")
    assert "Valparaíso" not in notas.search_notes("viña")
    assert "borrada" in notas.delete_note(1)
    assert "No hay notas" in notas.search_notes("viña")
    assert "No existe" in notas.delete_note(1)


def test_files_create_read_find(workdir):
    assert "creado" in archivos.create_file("docs/plan.md", "# Plan\nimprimir soporte")
    assert archivos.read_file("docs/plan.md").startswith("# Plan")
    assert "docs/plan.md" in archivos.find_files("*.md")
    assert "docs/plan.md" in archivos.find_files(text="SOPORTE")
    assert "No se encontraron" in archivos.find_files(text="nada-que-ver")


def test_files_create_does_not_overwrite(workdir):
    archivos.create_file("a.txt", "uno")
    assert "Ya existe" in archivos.create_file("a.txt", "dos")
    assert "reemplazado" in archivos.overwrite_file("a.txt", "dos")
    assert archivos.read_file("a.txt") == "dos"


@pytest.mark.parametrize(
    "path", ["../fuera.txt", "docs/../../fuera.txt", "C:/Windows/win.ini", "/etc/passwd"]
)
def test_files_never_leave_workdir(workdir, path):
    assert "carpeta de trabajo" in archivos.read_file(path)
    assert "carpeta de trabajo" in archivos.create_file(path, "x")
    assert not (workdir.parent / "fuera.txt").exists()


def test_files_symlink_escape_is_blocked(workdir, tmp_path):
    outside = tmp_path / "secreto.txt"
    outside.write_text("secreto", encoding="utf-8")
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, workdir / "enlace.txt")
    except OSError:
        pytest.skip("este Windows no permite crear enlaces simbólicos sin permisos")
    assert "sale de la carpeta" in archivos.read_file("enlace.txt")


def test_destructive_tools_are_annotated():
    import asyncio

    def annotations(server):
        tools = asyncio.run(server.list_tools())
        return {t.name: bool(t.annotations and t.annotations.destructive_hint) for t in tools}

    assert annotations(notas.server) == {
        "create_note": False,
        "search_notes": False,
        "update_note": True,
        "delete_note": True,
    }
    assert annotations(archivos.server) == {
        "find_files": False,
        "read_file": False,
        "create_file": False,
        "overwrite_file": True,
    }
