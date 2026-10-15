"""Interface simplificada para celular: vendas, clientes e histórico de vendas.

Ponto de entrada separado do app.py (mesmo repositório, mesmo banco, mesmo login):

    streamlit run app_mobile.py

Estrutura:
    app_mobile.py                      menu de navegação e roteamento (este arquivo)
    telas_mobile/comum.py              utilidades, leituras em cache e estado compartilhado
    telas_mobile/vendas.py             nova venda
    telas_mobile/clientes.py           consulta e cadastro de clientes
    telas_mobile/historico_vendas.py   histórico de vendas

Requer Streamlit >= 1.36.
"""
import streamlit as st

# set_page_config precisa ser o primeiro comando do Streamlit.
st.set_page_config(
    page_title="Semijoias · Mobile",
    page_icon="💎",
    layout="centered",
    initial_sidebar_state="collapsed",
)

from auth import autenticar_usuario, renderizar_logout_sidebar  # noqa: E402
from telas_mobile.clientes import tela_clientes_mobile  # noqa: E402
from telas_mobile.comum import manter_estado, processar_resets  # noqa: E402
from telas_mobile.historico_vendas import tela_historico_vendas_mobile  # noqa: E402
from telas_mobile.vendas import tela_vendas_mobile  # noqa: E402

# Rótulo do menu -> tela. A ordem aqui é a ordem do menu.
ROTAS = {
    "💰 Venda": tela_vendas_mobile,
    "👤 Clientes": tela_clientes_mobile,
    "🧾 Histórico": tela_historico_vendas_mobile,
}
CHAVE_MENU = "m_aba"


def _css() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding: 1rem .8rem 4rem; max-width: 640px;}
        .stButton > button, .stLinkButton > a {min-height: 3rem; font-size: 1rem;}
        input, textarea {font-size: 16px !important;}  /* evita o zoom automático no iOS */
        </style>
        """,
        unsafe_allow_html=True,
    )


def _menu_navegacao(opcoes: list) -> str:
    """Menu de navegação no topo: controle segmentado (Streamlit >= 1.40) ou radio
    horizontal. Sempre devolve uma opção válida (tocar na ativa não a desmarca)."""
    ss = st.session_state
    ultimo = f"{CHAVE_MENU}__ultimo"
    padrao = ss.get(ultimo, opcoes[0])
    if padrao not in opcoes:
        padrao = opcoes[0]
    if ss.get(CHAVE_MENU) not in opcoes:
        ss[CHAVE_MENU] = padrao

    segmentado = getattr(st, "segmented_control", None)
    if segmentado is None:
        escolhida = st.radio(
            "Menu", opcoes, horizontal=True, key=CHAVE_MENU, label_visibility="collapsed"
        )
    else:
        def _nao_desmarcar():
            if ss.get(CHAVE_MENU) is None:
                ss[CHAVE_MENU] = ss.get(ultimo, opcoes[0])

        escolhida = segmentado(
            "Menu", opcoes, selection_mode="single", key=CHAVE_MENU,
            on_change=_nao_desmarcar, label_visibility="collapsed",
        )
    if escolhida not in opcoes:
        escolhida = padrao
    ss[ultimo] = escolhida
    return escolhida


def main() -> None:
    autenticar_usuario()
    renderizar_logout_sidebar()
    _css()
    processar_resets()
    manter_estado()

    escolhida = _menu_navegacao(list(ROTAS))
    st.divider()
    ROTAS[escolhida]()


main()
