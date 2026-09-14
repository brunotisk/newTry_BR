import streamlit as st
from db import supabase


def autenticar_usuario():
    """
    Gerencia a sessão e bloqueia a execução da aplicação
    caso o usuário não esteja autenticado.
    """
    if "user" not in st.session_state:
        st.session_state.user = None

    if st.session_state.user is not None:
        return st.session_state.user

    st.title("🔒 Acesso Restrito")

    with st.form("form_login"):
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        btn_entrar = st.form_submit_button("Entrar", use_container_width=True)

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


def renderizar_logout_sidebar():
    """Exibe o e-mail do usuário logado e o botão de logout na barra lateral."""
    if st.session_state.get("user"):
        with st.sidebar:
            st.write(f"👤 **{st.session_state.user.email}**")
            if st.button("Sair", use_container_width=True):
                logout()