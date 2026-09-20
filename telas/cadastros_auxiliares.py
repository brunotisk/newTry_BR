import streamlit as st

from db import supabase
from servicos.listas_venda import listar, criar, atualizar


def _secao_lista(tabela: str, titulo: str, placeholder: str):
    """Cadastro genérico para canais de venda e status de venda."""
    st.subheader(titulo)

    with st.form(f"form_novo_{tabela}", clear_on_submit=True):
        nome_novo = st.text_input(
            "Novo item",
            placeholder=placeholder,
            label_visibility="collapsed",
        )
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
                "Ativo",
                value=item["ativo"],
                key=f"{tabela}_ativo_{item['id']}",
            )

            if col_acao.button(
                "💾",
                key=f"{tabela}_salvar_{item['id']}",
                use_container_width=True,
            ):
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


def _secao_formas_pagamento():
    tabela = "formas_pagamento"
    st.subheader("Formas de Pagamento")

    with st.form("form_nova_forma_pagamento", clear_on_submit=True):
        descricao = st.text_input(
            "Descrição",
            placeholder="Ex.: Pix, Dinheiro, Cartão de crédito...",
        )
        salvar = st.form_submit_button("➕ Adicionar")

    if salvar:
        if descricao.strip():
            try:
                supabase.table(tabela).insert(
                    {"descricao": descricao.strip()}
                ).execute()
                st.success(f"'{descricao.strip()}' adicionada!")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao adicionar forma de pagamento: {e}")
        else:
            st.warning("Informe a descrição.")

    st.markdown("---")

    try:
        itens = (
            supabase.table(tabela)
            .select("id, descricao, ativo")
            .order("descricao")
            .execute()
        ).data or []
    except Exception as e:
        st.error(f"Erro ao carregar formas de pagamento: {e}")
        return

    if not itens:
        st.info("Nenhuma forma de pagamento cadastrada ainda.")
        return

    with st.container(border=True):
        c_desc, c_ativo, c_acao = st.columns([4, 1.5, 1])
        c_desc.markdown("**Descrição**")
        c_ativo.markdown("**Ativo**")
        c_acao.markdown("**Ações**")

        st.divider()

        for item in itens:
            col_desc, col_ativo, col_acao = st.columns([4, 1.5, 1])

            nova_descricao = col_desc.text_input(
                "Descrição",
                value=item["descricao"],
                key=f"forma_pagamento_descricao_{item['id']}",
                label_visibility="collapsed",
            )
            novo_ativo = col_ativo.toggle(
                "Ativo",
                value=item["ativo"],
                key=f"forma_pagamento_ativo_{item['id']}",
            )

            if col_acao.button(
                "💾",
                key=f"forma_pagamento_salvar_{item['id']}",
                use_container_width=True,
            ):
                try:
                    supabase.table(tabela).update(
                        {
                            "descricao": nova_descricao.strip(),
                            "ativo": novo_ativo,
                        }
                    ).eq("id", item["id"]).execute()
                    st.toast("Atualizado com sucesso!", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao salvar: {e}")


def _secao_detalhes_feira():
    tabela = "detalhes_feira"
    st.subheader("Detalhes Feira")

    with st.form("form_nova_feira", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            nome = st.text_input("Nome da feira")
            endereco = st.text_input("Endereço da feira")
        with col2:
            pessoa_contato = st.text_input("Pessoa de contato")
            tel_contato = st.text_input("Telefone de contato")

        salvar = st.form_submit_button("➕ Adicionar")

    if salvar:
        if not nome.strip():
            st.warning("Informe o nome da feira.")
        else:
            try:
                supabase.table(tabela).insert(
                    {
                        "nome_feira": nome.strip(),
                        "endereco_feira": endereco.strip(),
                        "pessoa_contato_feira": pessoa_contato.strip(),
                        "tel_contato_feira": tel_contato.strip(),
                    }
                ).execute()
                st.success(f"Feira '{nome.strip()}' adicionada!")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao adicionar feira: {e}")

    st.markdown("---")

    try:
        itens = (
            supabase.table(tabela)
            .select(
                "id, nome_feira, endereco_feira, "
                "pessoa_contato_feira, tel_contato_feira, ativo"
            )
            .order("nome_feira")
            .execute()
        ).data or []
    except Exception as e:
        st.error(f"Erro ao carregar detalhes das feiras: {e}")
        return

    if not itens:
        st.info("Nenhuma feira cadastrada ainda.")
        return

    with st.container(border=True):
        for item in itens:
            st.markdown(f"**Feira #{item['id']}**")

            col1, col2 = st.columns(2)
            with col1:
                novo_nome = st.text_input(
                    "Nome da feira",
                    value=item["nome_feira"] or "",
                    key=f"feira_nome_{item['id']}",
                )
                novo_endereco = st.text_input(
                    "Endereço",
                    value=item["endereco_feira"] or "",
                    key=f"feira_endereco_{item['id']}",
                )
            with col2:
                nova_pessoa = st.text_input(
                    "Pessoa de contato",
                    value=item["pessoa_contato_feira"] or "",
                    key=f"feira_pessoa_{item['id']}",
                )
                novo_telefone = st.text_input(
                    "Telefone de contato",
                    value=item["tel_contato_feira"] or "",
                    key=f"feira_telefone_{item['id']}",
                )

            col_ativo, col_acao = st.columns([4.5, 1])
            with col_ativo:
                novo_ativo = st.toggle(
                    "Ativo",
                    value=item["ativo"],
                    key=f"feira_ativo_{item['id']}",
                )
            with col_acao:
                salvar_item = st.button(
                    "💾 Salvar",
                    key=f"feira_salvar_{item['id']}",
                    use_container_width=True,
                )

            if salvar_item:
                try:
                    supabase.table(tabela).update(
                        {
                            "nome_feira": novo_nome.strip(),
                            "endereco_feira": novo_endereco.strip(),
                            "pessoa_contato_feira": nova_pessoa.strip(),
                            "tel_contato_feira": novo_telefone.strip(),
                            "ativo": novo_ativo,
                        }
                    ).eq("id", item["id"]).execute()
                    st.toast("Feira atualizada com sucesso!", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao salvar feira: {e}")

            st.divider()


def tela_cadastros_auxiliares():
    st.header("🧩 Cadastros Auxiliares")
    st.caption("Gerencie as listas de apoio usadas nos formulários de venda.")

    aba_canais, aba_status, aba_feiras, aba_formas = st.tabs(
        [
            "Canais de Venda",
            "Status de Venda",
            "Detalhes Feira",
            "Formas de Pagamento",
        ]
    )

    with aba_canais:
        _secao_lista(
            "canais_venda",
            "Canais de Venda",
            "Ex.: Salão, Feira, Online...",
        )

    with aba_status:
        _secao_lista(
            "status_venda",
            "Status de Venda",
            "Ex.: Pendente, Pago, Cancelado...",
        )

    with aba_feiras:
        _secao_detalhes_feira()

    with aba_formas:
        _secao_formas_pagamento()
