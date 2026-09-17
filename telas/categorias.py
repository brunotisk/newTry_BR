import streamlit as st
from db import supabase

def tela_categorias():

    # Inicializa o estado de edição
    if "categoria_editando" not in st.session_state:
        st.session_state.categoria_editando = None

    cat_edit = st.session_state.categoria_editando
    modo_edicao = cat_edit is not None

    # Título dinâmico do formulário
    if modo_edicao:
        st.subheader(f"Editar categoria (ID: {cat_edit['id']})")
    else:
        st.subheader("Nova categoria")

    # Formulário de cadastro / edição
    with st.form("form_categoria"):
        categoria = st.text_input(
            "Categoria",
            value=cat_edit["categoria"] if modo_edicao else "",
            placeholder="Ex.: Brinco"
        )

        banho = st.text_input(
            "Banho",
            value=cat_edit["banho"] if (modo_edicao and cat_edit.get("banho")) else "",
            placeholder="Ex.: Ouro 18K"
        )

        ativo = st.checkbox(
            "Ativo",
            value=cat_edit["ativo"] if modo_edicao else True
        )

        ordem_exibicao = st.number_input(
            "Ordem de exibição",
            min_value=0,
            value=int(cat_edit["ordem_exibicao"]) if modo_edicao else 1,
            step=1
        )

        salvar = st.form_submit_button(
            "💾 Atualizar categoria" if modo_edicao else "💾 Salvar categoria"
        )

    # Botão para cancelar a edição (caso queira voltar a criar uma nova)
    if modo_edicao:
        if st.button("❌ Cancelar edição"):
            st.session_state.categoria_editando = None
            st.rerun()

    # Processamento do formulário
    if salvar:
        if not categoria.strip():
            st.error("Informe a categoria.")
            return

        try:
            dados = {
                "categoria": categoria.strip(),
                "banho": banho.strip(),
                "ativo": ativo,
                "ordem_exibicao": ordem_exibicao
            }

            if modo_edicao:
                # Atualização (UPDATE)
                response = (
                    supabase
                    .table("categorias_produtos")
                    .update(dados)
                    .eq("id", cat_edit["id"])
                    .execute()
                )

                if response.data:
                    st.success(f"Categoria '{categoria}' atualizada com sucesso!")
                    st.session_state.categoria_editando = None
                    st.rerun()
            else:
                # Inserção (INSERT)
                response = (
                    supabase
                    .table("categorias_produtos")
                    .insert(dados)
                    .execute()
                )

                if response.data:
                    st.success(f"Categoria '{categoria}' cadastrada com sucesso!")
                    st.rerun()

        except Exception as e:
            st.error(f"Erro ao salvar categoria: {e}")

    st.divider()
    st.subheader("Categorias cadastradas")

    try:
        response = (
            supabase
            .table("categorias_produtos")
            .select("*")
            .order("ordem_exibicao")
            .execute()
        )

        categorias = response.data

        if categorias:
            # Cabeçalho da tabela customizada
            col_id, col_cat, col_banho, col_ativo, col_ordem, col_acoes = st.columns([1, 3, 3, 1, 2, 1])
            
            with col_id:
                st.markdown("**id**")
            with col_cat:
                st.markdown("**categoria**")
            with col_banho:
                st.markdown("**banho**")
            with col_ativo:
                st.markdown("**ativo**")
            with col_ordem:
                st.markdown("**ordem_exibicao**")
            with col_acoes:
                st.markdown("**Ações**")

            st.divider()

            # Renderização de cada linha com botão de edição
            for item in categorias:
                c1, c2, c3, c4, c5, c6 = st.columns([1, 3, 3, 1, 2, 1])
                
                c1.write(item["id"])
                c2.write(item["categoria"])
                c3.write(item.get("banho") or "-")
                c4.write("✅" if item["ativo"] else "❌")
                c5.write(item["ordem_exibicao"])
                
                # Botão do lápis para carregar os dados no formulário
                if c6.button("✏️", key=f"edit_{item['id']}"):
                    st.session_state.categoria_editando = item
                    st.rerun()

        else:
            st.info("Nenhuma categoria cadastrada.")

    except Exception as e:
        st.error(f"Erro ao consultar categorias: {e}")