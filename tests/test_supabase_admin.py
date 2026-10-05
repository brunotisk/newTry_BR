from unittest.mock import Mock

import pytest


def _reload(monkeypatch):
    import servicos.supabase_admin as mod
    monkeypatch.setattr(mod, "load_dotenv", lambda: None)
    return mod


def test_get_client_seleciona_hml(monkeypatch):
    mod = _reload(monkeypatch)
    monkeypatch.setenv("BD", "HML")
    monkeypatch.setenv("SUPABASE_URL_HOMOLOG", "http://hml")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY_HOMOLOG", "key-hml")
    fake = Mock(name="client")
    monkeypatch.setattr(mod, "create_client", lambda url, key: (url, key))
    assert mod.get_client() == ("http://hml", "key-hml")


def test_get_client_seleciona_prd(monkeypatch):
    mod = _reload(monkeypatch)
    monkeypatch.setenv("BD", "PRD")
    monkeypatch.setenv("SUPABASE_URL_PRD", "http://prd")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY_PRD", "key-prd")
    monkeypatch.setattr(mod, "create_client", lambda url, key: (url, key))
    assert mod.get_client() == ("http://prd", "key-prd")


def test_get_client_rejeita_bd_invalido(monkeypatch):
    mod = _reload(monkeypatch)
    monkeypatch.setenv("BD", "TESTE")
    with pytest.raises(RuntimeError, match="BD inválido"):
        mod.get_client()


def test_get_client_rejeita_url_ausente(monkeypatch):
    mod = _reload(monkeypatch)
    monkeypatch.setenv("BD", "HML")
    monkeypatch.delenv("SUPABASE_URL_HOMOLOG", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY_HOMOLOG", "key")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        mod.get_client()


def test_get_client_rejeita_service_key_ausente(monkeypatch):
    mod = _reload(monkeypatch)
    monkeypatch.setenv("BD", "PRD")
    monkeypatch.setenv("SUPABASE_URL_PRD", "http://prd")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY_PRD", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
        mod.get_client()
