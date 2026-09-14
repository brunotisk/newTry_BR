import streamlit as st

from telas.produtos import tela_produtos
from telas.categorias import tela_categorias
from telas.importar_nf import tela_importar_nf
from auth import autenticar_usuario, renderizar_logout_sidebar


autenticar_usuario()
renderizar_logout_sidebar()

st.set_page_config(
    page_title="Sistema de Semijoias",
    layout="wide"
)


st.title("💎 Sistema de Semijoias")

st.sidebar.title("Menu")

opcao = st.sidebar.radio(
    "Navegação",
    [
        "📦 Listar Produtos",
        "🏷️ Cadastrar Categorias",
        "📄 Importar NF"
    ]
)


if opcao == "📦 Listar Produtos":
    tela_produtos()

elif opcao == "🏷️ Cadastrar Categorias":
    tela_categorias()

elif opcao == "📄 Importar NF":
    tela_importar_nf()