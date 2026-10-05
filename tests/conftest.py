import os
import sys
import types

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "chave-de-teste")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "chave-de-teste")

from fakes import FakeSupabase, NFE_XML  # noqa: E402


def _buscar_todos(montar_query, tamanho_pagina=1000):
    todos = []
    inicio = 0
    while True:
        lote = montar_query().range(inicio, inicio + tamanho_pagina - 1).execute().data or []
        todos.extend(lote)
        if len(lote) < tamanho_pagina:
            break
        inicio += tamanho_pagina
    return todos


_db_fake = types.ModuleType("db")
_db_fake.supabase = FakeSupabase()
_db_fake.buscar_todos = _buscar_todos
sys.modules["db"] = _db_fake


@pytest.fixture
def sb(monkeypatch):
    return FakeSupabase()


@pytest.fixture
def nfe_xml():
    return NFE_XML


@pytest.fixture
def nota():
    from servicos.compras_import import parse_nfe_string
    return parse_nfe_string(NFE_XML)


@pytest.fixture
def xml_path(tmp_path, nfe_xml):
    path = tmp_path / "nota.xml"
    path.write_text(nfe_xml, encoding="utf-8")
    return str(path)


def _patch_service_clients(monkeypatch, sb):
    import servicos.compras_import as compras
    import servicos.vendas_import as vendas
    import servicos.estoque as estoque
    monkeypatch.setattr(compras, "get_client", lambda: sb)
    monkeypatch.setattr(vendas, "get_client", lambda: sb)
    monkeypatch.setattr(estoque, "get_client", lambda: sb)


@pytest.fixture
def service_sb(monkeypatch, sb):
    _patch_service_clients(monkeypatch, sb)
    return sb
