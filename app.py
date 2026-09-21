import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# 1. Configuração de página
st.set_page_config(
    page_title="Sistema de Semijoias",
    page_icon="💎",
    layout="wide"
)

from telas.produtos import tela_produtos
from telas.importar_nf import tela_importar_nf
from telas.report import tela_report
from telas.compras import tela_compras
from telas.vendas import tela_vendas
from telas.clientes import tela_clientes
from telas.estoque_saldo import tela_estoque_saldo
from auth import autenticar_usuario, renderizar_usuario_sidebar, renderizar_botao_sair

# 2. Trava de Autenticação
autenticar_usuario()


def _renderizar_alerta_ambiente():
    """Exibe claramente no topo da sidebar qual ambiente está em uso."""
    bd = os.getenv("BD", "").strip().upper()

    if bd == "HML":
        titulo = "🟡 HOMOLOGAÇÃO"
        subtitulo = "Ambiente de testes"
        classe = "ambiente-hml"

    elif bd == "PRD":
        titulo = "🟢 PRODUÇÃO"
        subtitulo = "Atenção: dados reais"
        classe = "ambiente-prd"

    else:
        titulo = "⚠️ AMBIENTE NÃO CONFIGURADO"
        subtitulo = "Defina BD=HML ou BD=PRD no .env"
        classe = "ambiente-erro"

    st.markdown(
        f"""
        <div class="ambiente-box {classe}">
            <div class="ambiente-titulo">{titulo}</div>
            <div class="ambiente-subtitulo">{subtitulo}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _injetar_estilo_sidebar():
    """CSS que transforma a sidebar em um menu lateral escuro, com item ativo destacado."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
            background-color: #1c1f26;
        }

        [data-testid="stSidebar"] * {
            color: #e8e9ec;
        }

        /* ALERTA DO AMBIENTE */
        .ambiente-box {
            width: 100%;
            box-sizing: border-box;
            border-radius: 8px;
            padding: 0.75rem 0.6rem;
            margin: 0.2rem 0 1.1rem 0;
            text-align: center;
            line-height: 1.25;
        }

        .ambiente-titulo {
            font-size: 0.95rem;
            font-weight: 700;
            text-align: center;
            margin-bottom: 0.25rem;
        }

        .ambiente-subtitulo {
            font-size: 0.78rem;
            text-align: center;
            opacity: 0.95;
        }

        .ambiente-hml {
            background-color: #6b5d1f;
            border: 1px solid #b7a42d;
        }

        .ambiente-prd {
            background-color: #245c3a;
            border: 1px solid #3fa76a;
        }

        .ambiente-erro {
            background-color: #6b2929;
            border: 1px solid #c85b5b;
        }

        /* título "Menu" */
        .menu-titulo {
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            color: #7b8494;
            text-transform: uppercase;
            margin: 0.4rem 0 0.6rem 0.2rem;
        }

        /* botões de navegação */
        [data-testid="stSidebar"] div[data-testid="stButton"] button {
            width: 100%;
            display: flex;
            justify-content: flex-start;
            text-align: left;
            background-color: transparent;
            border: none;
            border-radius: 8px;
            padding: 0.55rem 0.8rem;
            font-weight: 500;
            font-size: 0.9rem;
            color: #c7cad1;
            margin-bottom: 0.15rem;
            transition: background-color 0.12s ease-in-out;
        }

        [data-testid="stSidebar"] div[data-testid="stButton"] button p,
        [data-testid="stSidebar"] div[data-testid="stButton"] button div {
            text-align: left;
            width: 100%;
        }

        [data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
            background-color: rgba(255, 255, 255, 0.07);
            color: #ffffff;
        }

        /* item ativo */
        [data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"] {
            background-color: #3b4252 !important;
            color: #ffffff !important;
            border: none !important;
            box-shadow: inset 3px 0 0 #8891a3;
        }

        [data-testid="stSidebar"] div[data-testid="stButton"] button[kind="primary"]:hover {
            background-color: #454e60 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# 3. Estrutura de páginas: rótulo (com ícone) -> função da tela
PAGINAS = {
    "📦 Produtos": tela_produtos,
    "🛍️ Compras": tela_compras,
    "💰 Vendas": tela_vendas,
    "📊 Estoque": tela_estoque_saldo,
    "👤 Clientes": tela_clientes,
    "📈 Relatórios": tela_report,
}

if "pagina_atual" not in st.session_state:
    st.session_state.pagina_atual = list(PAGINAS.keys())[0]

# 4. Sidebar
_injetar_estilo_sidebar()
renderizar_usuario_sidebar()

with st.sidebar:
    _renderizar_alerta_ambiente()

    st.markdown(
        '<div class="menu-titulo">Navegação</div>',
        unsafe_allow_html=True
    )

    for rotulo in PAGINAS:
        ativo = st.session_state.pagina_atual == rotulo

        if st.button(
            rotulo,
            key=f"nav_{rotulo}",
            use_container_width=True,
            type="primary" if ativo else "secondary",
        ):
            st.session_state.pagina_atual = rotulo
            st.rerun()

renderizar_botao_sair()

# 5. Cabeçalho e roteamento
st.title("Sistema de Semijoias - Bruno & AC")

PAGINAS[st.session_state.pagina_atual]()
