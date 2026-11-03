"""Tela de configurações do app mobile: tema, conta (Sair) e ambiente."""
import ipaddress
import os
from urllib.parse import urlparse

import streamlit as st
from docs.version import APP_VERSION

from auth import logout
from tema import seletor_tema_persistente


def _host_atual() -> str:
    """Host pelo qual o app está sendo acessado (sem a porta)."""
    try:
        host = st.context.headers.get("Host", "") or ""
    except Exception:
        host = ""
    return host.split(":")[0].lower()


def _eh_ip_privado(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def detectar_ambiente() -> tuple[str, str]:
    """Retorna (nome do ambiente, detalhe de como foi detectado).

    Se a variável APP_ENV existir no .env / secrets (ex.: producao, homologacao,
    desenvolvimento), ela manda. Sem ela, o ambiente é deduzido pelo host."""
    host = _host_atual()

    manual = (os.getenv("APP_ENV") or "").strip()
    if manual:
        return manual.capitalize(), "definido em APP_ENV"

    if host in ("localhost", "127.0.0.1", "::1") or _eh_ip_privado(host):
        return "Local (desenvolvimento)", f"host {host}"
    if host.endswith(".streamlit.app"):
        return "Streamlit Cloud", f"host {host}"
    if host:
        return "Servidor", f"host {host}"
    return "Não identificado", "host indisponível"


def _linha(rotulo: str, valor: str) -> None:
    st.caption(rotulo)
    st.markdown(f"**{valor}**")


def tela_configuracoes_mobile() -> None:
    # --- Aparência ---
    with st.container(border=True):
        st.subheader("🎨 Aparência")
        seletor_tema_persistente()
        st.caption(
            "Automático segue o tema do celular. A escolha vale enquanto o app "
            "estiver aberto."
        )

    # --- Conta ---
    usuario = st.session_state.get("user")
    with st.container(border=True):
        st.subheader("👤 Conta")
        _linha("Usuário logado", getattr(usuario, "email", None) or "-")
        if st.button("Sair da conta", use_container_width=True, type="primary"):
            logout()

    # --- Ambiente ---
    ambiente, detalhe = detectar_ambiente()
    supabase_host = urlparse(os.getenv("SUPABASE_URL") or "").hostname or "-"
    with st.container(border=True):
        st.subheader("🖥️ Ambiente")
        _linha("Rodando em", ambiente)
        st.caption(f"Detectado por: {detalhe}")
        _linha("Banco de dados (Supabase)", supabase_host)
        _linha("Versão do deploy", f"v{APP_VERSION}")
        _linha("Streamlit", st.__version__)
