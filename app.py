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
from auth import autenticar_usuario, renderizar_logout_sidebar

# 2. Trava de Autenticação e Menu do Usuário
autenticar_usuario()
renderizar_logout_sidebar()

# 3. Interface e Navegação
st.title("Sistema de Semijoias - Bruno I.")
st.sidebar.title("Menu")

opcao = st.sidebar.radio(
    "Navegação",
    [
        "📦 Listar Produtos",
        "🛍️ Compras",
        "💰 Vendas",
        "📊 Controle de Estoque",
        "👤 Clientes",
        "🏷️ Cadastrar Categorias",
        "📄 Importar NF",
        "📈 Relatórios",
    ]
)

# 4. Roteamento de Páginas
if opcao == "📦 Listar Produtos":
    tela_produtos()

elif opcao == "🛍️ Compras":
    tela_compras()

elif opcao == "💰 Vendas":
    tela_vendas()

elif opcao == "📊 Controle de Estoque":
    tela_estoque()

elif opcao == "👤 Clientes":
    tela_clientes()

elif opcao == "🏷️ Cadastrar Categorias":
    tela_categorias()

elif opcao == "📄 Importar NF":
    tela_importar_nf()

elif opcao == "📈 Relatórios":
    tela_report()