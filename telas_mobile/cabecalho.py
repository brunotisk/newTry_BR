"""Cabeçalho do app mobile: marca e título da tela.

O visual (.m-topo, .m-marca, .m-titulo) está em telas_mobile/estilo.py.
O tema e o botão Sair ficam na aba Configurações (telas_mobile/configuracoes.py)."""
import html

import streamlit as st


def cabecalho(titulo: str) -> None:
    st.markdown(
        '<div class="m-topo">'
        '<div class="m-marca">Semijoias</div>'
        f'<div class="m-titulo">{html.escape(titulo)}</div>'
        '<div class="m-topo-linha"></div>'
        "</div>",
        unsafe_allow_html=True,
    )
