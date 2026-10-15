"""Cabeçalho e barra de navegação inferior do app mobile."""
import streamlit as st

from auth import logout

CHAVE_MENU = "m_aba"


def _container(key: str):
    """Container com chave (Streamlit >= 1.39) para o CSS conseguir mirar nele."""
    try:
        return st.container(key=key)
    except TypeError:
        return st.container()


def _ir_para(nome: str) -> None:
    st.session_state[CHAVE_MENU] = nome


def aba_atual(nomes: list) -> str:
    ss = st.session_state
    if ss.get(CHAVE_MENU) not in nomes:
        ss[CHAVE_MENU] = nomes[0]
    return ss[CHAVE_MENU]


def cabecalho(titulo: str) -> None:
    """Título da tela e botão Sair (no lugar da barra lateral)."""
    with _container("m_topo"):
        col_titulo, col_sair = st.columns([3, 1], vertical_alignment="center")
        col_titulo.markdown(f"### {titulo}")
        if col_sair.button("Sair", key="m_sair"):
            logout()


def barra_inferior(abas: dict, atual: str) -> None:
    """abas: {nome: ícone}. A barra é 'fixed' via CSS; a posição no código não importa."""
    with _container("m_nav"):
        colunas = st.columns(len(abas))
        for coluna, (nome, icone) in zip(colunas, abas.items()):
            coluna.button(
                nome,
                icon=icone,
                key=f"m_nav_{nome}",
                type="primary" if nome == atual else "secondary",
                use_container_width=True,
                on_click=_ir_para,
                args=(nome,),
            )
