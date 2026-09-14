import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def tela_report():
    st.header("📊 Relatórios e Analíticos")

    url_report = os.getenv("URL_REPORT")

    if not url_report:
        st.error(
            "⚠️ A URL do painel de relatórios não foi configurada no arquivo `.env`."
        )
        return

    # CSS corrigido com width: 100%
    st.markdown(
        """
        <style>
        div[data-testid="stLinkButton"] {
            width: 100% !important;
            display: flex !important;
            justify-content: center !important;
        }

        /* Estilo e largura fixa do botão */
        div[data-testid="stLinkButton"] a {
            background-color: #2e7d32 !important; /* Verde */
            color: #ffffff !important;
            border-color: #2e7d32 !important;
            width: 250px !important;               /* Largura do botão */
            text-align: center;
            font-weight: bold;
        }
        
        div[data-testid="stLinkButton"] a:hover {
            background-color: #1b5e20 !important; /* Verde escuro */
            border-color: #1b5e20 !important;
        }
        </style>
    """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown("### 📈 Painel Externo de Business Intelligence")
        st.write(
            "Acesse nossa plataforma externa para visualizar gráficos detalhados, "
            "métricas de vendas, análise de estoque e indicadores operacionais."
        )

        st.link_button(
            "🔗 Abrir Relatórios",
            url_report,
            use_container_width=False,
        )