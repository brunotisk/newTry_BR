import streamlit as st
from listas_venda import listar, criar, atualizar


def _secao_lista(tabela: str, titulo: str, placeholder: str):
    st.subheader(titulo)

    with st.form(f"form_novo_{tabela}", clear_on_submit=True):
        nome_novo = st.text_input(f"Novo item", placeholder=placeholder, label_visibility="collapsed")
        salvar = st.form_submit_button("➕ Adicionar")

    if salvar:
        if nome_novo.strip():
            try:
                criar(tabela, nome_novo)
                st.success(f"'{nome_novo.strip()}' adicionado!")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao adicionar: {e}")
        else:
            st.warning("Informe um nome.")

    st.markdown("---")

    try:
        itens = listar(tabela, apenas_ativos=False)
    except Exception as e:
        st.error(f"Erro ao carregar lista: {e}")
        return

    if not itens:
        st.info("Nenhum item cadastrado ainda.")
        return

    with st.container(border=True):
        c_nome, c_ativo, c_acao = st.columns([4, 1.5, 1])
        c_nome.markdown("**Nome**")
        c_ativo.markdown("**Ativo**")
        c_acao.markdown("**Ações**")

        st.divider()

        for item in itens:
            col_nome, col_ativo, col_acao = st.columns([4, 1.5, 1])

            novo_nome = col_nome.text_input(
                "Nome",
                value=item["nome"],
                key=f"{tabela}_nome_{item['id']}",
                label_visibility="collapsed",
            )
            novo_ativo = col_ativo.toggle(
                "Ativo", value=item["ativo"], key=f"{tabela}_ativo_{item['id']}"
            )

            if col_acao.button("💾", key=f"{tabela}_salvar_{item['id']}", use_container_width=True):
                try:
                    atualizar(tabela, item["id"], novo_nome, novo_ativo)
                    st.toast("Atualizado com sucesso!", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao salvar: {e}")

    st.caption(
        "Desative um item para que ele deixe de aparecer nos formulários de venda, "
        "sem apagar o histórico de vendas que já usaram esse valor."
    )


def tela_cadastros_auxiliares():
    st.header("🧩 Cadastros Auxiliares")
    st.caption("Gerencie as listas de apoio usadas nos formulários de venda.")

    aba_canais, aba_status = st.tabs(["Canais de Venda", "Status de Venda"])

    with aba_canais:
        _secao_lista("canais_venda", "Canais de Venda", "Ex.: Salão, Feira, Online...")

    with aba_status:
        _secao_lista("status_venda", "Status de Venda", "Ex.: Pendente, Pago, Cancelado...")
