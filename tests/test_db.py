import importlib
import sys
import types

import pytest


def _carregar_db(monkeypatch):
    sys.modules.pop("db", None)
    fake_supabase = types.ModuleType("supabase")
    fake_supabase.Client = object
    fake_supabase.create_client = lambda url, key: (url, key)
    monkeypatch.setitem(sys.modules, "supabase", fake_supabase)
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    return importlib.import_module("db")


def test_db_rejeita_ambiente_invalido(monkeypatch):
    monkeypatch.setenv("BD", "")
    with pytest.raises(RuntimeError, match="BD inválido"):
        _carregar_db(monkeypatch)


def test_db_cria_client_com_credenciais_do_ambiente(monkeypatch):
    monkeypatch.setenv("BD", "HML")
    monkeypatch.setenv("SUPABASE_URL_HOMOLOG", "http://hml")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY_HOMOLOG", "key")
    db = _carregar_db(monkeypatch)
    assert db.supabase == ("http://hml", "key")


def test_buscar_todos_pagina_e_para_no_ultimo_lote(monkeypatch):
    db = _carregar_db(monkeypatch)
    chamadas = []

    class Q:
        def range(self, inicio, fim):
            chamadas.append((inicio, fim))
            self.inicio = inicio
            return self

        def execute(self):
            if self.inicio == 0:
                return types.SimpleNamespace(data=list(range(3)))
            return types.SimpleNamespace(data=[3])

    assert db.buscar_todos(lambda: Q(), tamanho_pagina=3) == [0, 1, 2, 3]
    assert chamadas == [(0, 2), (3, 5)]
