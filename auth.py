import os
import streamlit as st
from db import supabase


def _skip_auth() -> bool:
    return os.getenv("SKIP_AUTH", "false").lower() == "true"


def _injetar_estilo():
    """Injeta o CSS que dá o visual de tela dividida (formulário + painel decorativo)."""
    st.markdown(
        """
        <style>
        /* Esconde chrome padrão do Streamlit na tela de login */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        [data-testid="stSidebar"] {display: none;}
        [data-testid="stAppViewContainer"] {background: #ffffff;}
        .block-container {
            padding: 0 !important;
            max-width: 100% !important;
        }

        /* Linha que contém as duas "colunas" vira o split screen */
        [data-testid="stHorizontalBlock"] {
            min-height: 100vh;
            gap: 0 !important;
        }

        /* Coluna 1 = formulário */
        [data-testid="stHorizontalBlock"] > div:nth-child(1) {
            background-color: #ffffff;
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 4rem 8vw;
        }

        /* Coluna 2 = painel decorativo (sem foto real, apenas formas/gradiente) */
        [data-testid="stHorizontalBlock"] > div:nth-child(2) {
            position: relative;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            text-align: center;
            color: #f5f6fa;
            background: linear-gradient(150deg, #1e2530 0%, #262f3d 45%, #0f141c 100%);
            overflow: hidden;
            padding: 3rem;
        }
        [data-testid="stHorizontalBlock"] > div:nth-child(2)::before {
            content: "";
            position: absolute;
            width: 480px;
            height: 480px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.05);
            top: -120px;
            right: -140px;
        }
        [data-testid="stHorizontalBlock"] > div:nth-child(2)::after {
            content: "";
            position: absolute;
            width: 320px;
            height: 320px;
            border-radius: 50%;
            border: 1px solid rgba(255, 255, 255, 0.12);
            bottom: -100px;
            left: -80px;
        }
        .painel-conteudo {
            position: relative;
            z-index: 1;
            max-width: 380px;
        }
        .painel-conteudo h2 {
            font-size: 1.8rem;
            font-weight: 600;
            margin-bottom: 0.6rem;
            letter-spacing: 0.3px;
        }
        .painel-conteudo p {
            font-size: 0.95rem;
            color: rgba(245, 246, 250, 0.75);
            line-height: 1.5;
        }
        .painel-icone {
            font-size: 2.4rem;
            margin-bottom: 1.2rem;
        }

        /* Cabeçalho do formulário */
        .form-header h1 {
            font-size: 1.7rem;
            font-weight: 700;
            color: #1c1f26;
            margin-bottom: 0.2rem;
        }
        .form-header p {
            color: #7b8494;
            font-size: 0.92rem;
            margin-bottom: 2rem;
        }

        /* Inputs */
        .stTextInput label {
            font-size: 0.85rem;
            font-weight: 600;
            color: #444b57;
        }
        .stTextInput input {
            border-radius: 8px;
            border: 1px solid #dfe2e8;
            padding: 0.6rem 0.8rem;
        }
        .stTextInput input:focus {
            border-color: #3b4252;
            box-shadow: 0 0 0 1px #3b4252;
        }

        /* Botão de login */
        div[data-testid="stFormSubmitButton"] button {
            background-color: #1c1f26;
            color: #ffffff;
            border-radius: 8px;
            border: none;
            padding: 0.65rem 0;
            font-weight: 600;
            margin-top: 0.5rem;
            transition: background-color 0.15s ease-in-out;
        }
        div[data-testid="stFormSubmitButton"] button:hover {
            background-color: #3b4252;
            color: #ffffff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def autenticar_usuario():
    """
    Gerencia a sessão e bloqueia a execução da aplicação
    caso o usuário não esteja autenticado.
    """
    if "user" not in st.session_state:
        st.session_state.user = None

    # Bypass de login para desenvolvimento local
    if _skip_auth():
        if st.session_state.user is None:
            st.session_state.user = type("DevUser", (), {"email": "dev@local"})()
        return st.session_state.user

    if st.session_state.user is not None:
        return st.session_state.user

    st.set_page_config(layout="wide")
    _injetar_estilo()

    col_form, col_painel = st.columns([5, 4])

    with col_form:
        st.markdown(
            """
            <div class="form-header">
                <h1>Bem-vindo de volta</h1>
                <p>Entre com suas credenciais para acessar o sistema.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("form_login"):
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type="password")
            btn_entrar = st.form_submit_button("Entrar →", use_container_width=True)

            if btn_entrar:
                try:
                    resposta = supabase.auth.sign_in_with_password({
                        "email": email.strip(),
                        "password": senha
                    })
                    st.session_state.user = resposta.user
                    st.success("Login efetuado com sucesso!")
                    st.rerun()
                except Exception:
                    st.error("E-mail ou senha inválidos.")

    with col_painel:
        st.markdown(
            """
            <div class="painel-conteudo">
                <div class="painel-icone">🔐</div>
                <h2>Acesso Restrito</h2>
                <p>
                    Esta área é reservada a usuários autorizados.
                    Faça login para continuar utilizando a plataforma.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Interrompe o carregamento do restante da página se não estiver logado
    st.stop()


def logout():
    """Realiza o logout no Supabase e limpa o estado da sessão."""
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state.user = None
    st.rerun()


def _injetar_estilo_usuario_sidebar():
    """CSS do cartão de usuário exibido no topo da sidebar."""
    st.markdown(
        """
        <style>
        .user-card {
            display: flex;
            align-items: center;
            gap: 0.7rem;
            padding: 0.9rem 0.2rem 1.1rem 0.2rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            margin-bottom: 0.8rem;
        }
        .user-avatar {
            width: 38px;
            height: 38px;
            min-width: 38px;
            border-radius: 50%;
            background: #3b4252;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.95rem;
        }
        .user-texto {
            overflow: hidden;
        }
        .user-nome {
            font-size: 0.85rem;
            font-weight: 600;
            color: #ffffff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            display: block;
        }
        .user-papel {
            font-size: 0.72rem;
            color: #9aa0ab;
        }

        /* Botão "Sair" discreto, alinhado com o cartão */
        div[data-testid="stSidebar"] div[data-testid="stButton"] button {
            background-color: transparent;
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #c7cad1;
            font-size: 0.8rem;
            padding: 0.3rem 0;
            border-radius: 6px;
        }
        div[data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
            background-color: rgba(255, 255, 255, 0.08);
            color: #ffffff;
            border-color: rgba(255, 255, 255, 0.3);
        }

        /* divisor sutil acima do botão Sair, no rodapé do menu */
        [data-testid="stSidebar"] hr {
            border-color: rgba(255, 255, 255, 0.08);
            margin: 1.1rem 0 0.9rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def renderizar_usuario_sidebar():
    """Exibe o cartão (avatar + e-mail) do usuário logado no topo da barra lateral."""
    if _skip_auth():
        with st.sidebar:
            st.caption("🧪 Modo dev — autenticação desativada (SKIP_AUTH)")
        return

    if st.session_state.get("user"):
        email = st.session_state.user.email
        inicial = email[0].upper() if email else "?"

        with st.sidebar:
            _injetar_estilo_usuario_sidebar()
            st.markdown(
                f"""
                <div class="user-card">
                    <div class="user-avatar">{inicial}</div>
                    <div class="user-texto">
                        <span class="user-nome">{email}</span>
                        <span class="user-papel">Usuário logado</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def renderizar_botao_sair():
    """Exibe o botão de logout — chame no final do conteúdo da barra lateral."""
    if _skip_auth():
        return

    if st.session_state.get("user"):
        with st.sidebar:
            st.divider()
            if st.button("↩ Sair", use_container_width=True, key="btn_logout_sidebar"):
                logout()


# Mantido por compatibilidade, caso algo ainda importe o nome antigo.
def renderizar_logout_sidebar():
    renderizar_usuario_sidebar()
    renderizar_botao_sair()
