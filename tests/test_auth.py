from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from streamlit.testing.v1 import AppTest

import auth

SCRIPT = '''
from auth import autenticar_usuario, renderizar_logout_sidebar
autenticar_usuario()
renderizar_logout_sidebar()
import streamlit as st
st.write("CONTEUDO PROTEGIDO")
'''


def _app():
    return AppTest.from_string(SCRIPT, default_timeout=10)


def _textos(at):
    return [m.value for m in at.markdown]


@pytest.fixture
def auth_sb(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(auth, "supabase", mock)
    monkeypatch.setenv("SKIP_AUTH", "false")
    return mock


def test_sem_login_bloqueia_conteudo(auth_sb):
    at = _app().run()
    assert len(at.text_input) == 2
    assert at.text_input[0].label == "E-mail"
    assert at.text_input[1].label == "Senha"
    assert "CONTEUDO PROTEGIDO" not in _textos(at)
    assert any("Acesso Restrito" in getattr(m, "value", "") for m in at.markdown)


def test_login_com_sucesso_normaliza_email(auth_sb):
    auth_sb.auth.sign_in_with_password.return_value = SimpleNamespace(
        user=SimpleNamespace(email="bruno@teste.com")
    )
    at = _app().run()
    at.text_input[0].set_value("  bruno@teste.com ")
    at.text_input[1].set_value("123456")
    at.button[0].click().run()
    auth_sb.auth.sign_in_with_password.assert_called_once_with(
        {"email": "bruno@teste.com", "password": "123456"}
    )
    assert at.session_state["user"].email == "bruno@teste.com"


def test_login_invalido_nao_libera_conteudo(auth_sb):
    auth_sb.auth.sign_in_with_password.side_effect = Exception("invalid")
    at = _app().run()
    at.text_input[0].set_value("a@b.com")
    at.text_input[1].set_value("errada")
    at.button[0].click().run()
    assert any("E-mail ou senha inválidos." == e.value for e in at.error)
    assert "CONTEUDO PROTEGIDO" not in _textos(at)


def test_logout_limpa_sessao_mesmo_se_supabase_falhar(auth_sb):
    auth_sb.auth.sign_out.side_effect = Exception("rede")
    at = _app()
    at.session_state["user"] = SimpleNamespace(email="bruno@teste.com")
    at.run()
    at.sidebar.button[0].click().run()
    assert at.session_state["user"] is None
