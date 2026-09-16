import streamlit as st

# 1. Configuração de página
st.set_page_config(
    page_title="Sistema de Semijoias",
    page_icon="💎",
    layout="wide"
)

from telas.produtos import tela_produtos
from telas.categorias import tela_categorias
from telas.importar_nf import tela_importar_nf
from telas.report import tela_report
from telas.compras import tela_compras
from telas.vendas import tela_vendas
from telas.clientes import tela_clientes
from telas.estoque import tela_estoque
from auth import autenticar_usuario, renderizar_usuario_sidebar, renderizar_botao_sair

# 2. Trava de Autenticação
autenticar_usuario()


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

        /* título "Menu" */
        .menu-titulo {
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            color: #7b8494;
            text-transform: uppercase;
            margin: 0.4rem 0 0.6rem 0.2rem;
        }

        /* botões de navegação (um por página) */
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
        /* garante que o texto/ícone dentro do botão também fiquem à esquerda */
        [data-testid="stSidebar"] div[data-testid="stButton"] button p,
        [data-testid="stSidebar"] div[data-testid="stButton"] button div {
            text-align: left;
            width: 100%;
        }
        [data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
            background-color: rgba(255, 255, 255, 0.07);
            color: #ffffff;
        }

        /* item ativo -> renderizado com type="primary" */
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
    "📦 Listar Produtos": tela_produtos,
    "🛍️ Compras": tela_compras,
    "💰 Vendas": tela_vendas,
    "📊 Controle de Estoque": tela_estoque,
    "👤 Clientes": tela_clientes,
    "🏷️ Cadastrar Categorias": tela_categorias,
    "📄 Importar NF": tela_importar_nf,
    "📈 Relatórios": tela_report,
}

if "pagina_atual" not in st.session_state:
    st.session_state.pagina_atual = list(PAGINAS.keys())[0]

# 4. Sidebar: cartão do usuário (topo) -> menu de navegação -> botão Sair (rodapé)
_injetar_estilo_sidebar()
renderizar_usuario_sidebar()

with st.sidebar:
    st.markdown('<div class="menu-titulo">Navegação</div>', unsafe_allow_html=True)
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

# 5. Cabeçalho e roteamento da página selecionada
st.title("Sistema de Semijoias - Bruno I.")

PAGINAS[st.session_state.pagina_atual]()
