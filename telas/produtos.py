import streamlit as st
from db import supabase
from telas.categorias import tela_categorias
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao


def _secao_consulta():
    st.markdown("""
        <style>
        div[data-testid="stDivider"] {
            margin-top: -10px !important;
            margin-bottom: 10px !important;
        }
        </style>
    """, unsafe_allow_html=True)

    try:
        # 1. Buscar categorias cadastradas
        response_cat = (
            supabase
            .table("categorias_produtos")
            .select("id, categoria, banho, ordem_exibicao")
            .order("ordem_exibicao")
            .execute()
        )
        categorias = response_cat.data or []

        cat_dict = {
            cat["id"]: {
                "categoria": cat["categoria"],
                "banho": cat.get("banho") or "-",
                "rotulo_dropdown": f"{cat['categoria']} | {cat.get('banho') or '-'}"
            }
            for cat in categorias
        }
        cat_ids = list(cat_dict.keys())

        # Lista de categorias únicas (sem o banho) para o filtro
        categorias_unicas = list(dict.fromkeys([cat["categoria"] for cat in categorias if cat.get("categoria")]))
        opcoes_cat = ["Todas as Categorias", "Sem Categoria"] + categorias_unicas

        # Topo: Layout dos filtros em 3 colunas (1º Código, 2º Categoria, 3º Toggle)
        col_busca, col_cat_filtro, col_filtro = st.columns([3.5, 2.5, 2])

        with col_busca:
            busca_codigo = st.text_input(
                "Filtrar por código interno",
                placeholder="Digite o código...",
                label_visibility="collapsed"
            )

        with col_cat_filtro:
            categoria_selecionada = st.selectbox(
                "Filtrar Categoria",
                options=opcoes_cat,
                index=0,
                label_visibility="collapsed"
            )

        with col_filtro:
            filtrar_sem_cat = st.toggle("Apenas sem categoria", value=False)

        # Inicializa o estado da página
        if "pagina_atual_produtos" not in st.session_state:
            st.session_state.pagina_atual_produtos = 1

        # Reseta para a página 1 se qualquer filtro mudar
        chave_filtro = f"{busca_codigo}_{categoria_selecionada}_{filtrar_sem_cat}"
        if st.session_state.get("chave_filtro_ant") != chave_filtro:
            st.session_state.pagina_atual_produtos = 1
            st.session_state.chave_filtro_ant = chave_filtro

        itens_por_pagina = get_itens_por_pagina("produtos", 50)
        offset = (st.session_state.pagina_atual_produtos - 1) * itens_por_pagina

        # 2. Construção da query com contagem total
        query = supabase.table("produtos").select("id, codigo_interno, descricao, categoria_id, ultima_compra, nr_compras", count="exact")

        if filtrar_sem_cat:
            query = query.is_("categoria_id", "null")
        elif categoria_selecionada == "Sem Categoria":
            query = query.is_("categoria_id", "null")
        elif categoria_selecionada != "Todas as Categorias":
            ids_cat_sel = [cid for cid, info in cat_dict.items() if info["categoria"] == categoria_selecionada]
            if ids_cat_sel:
                query = query.in_("categoria_id", ids_cat_sel)

        if busca_codigo:
            query = query.ilike("codigo_interno", f"%{busca_codigo.strip()}%")

        # Primeira tentativa de busca
        response_prod = query.order("id").range(offset, offset + itens_por_pagina - 1).execute()
        total_itens = response_prod.count if response_prod.count is not None else 0

        # Trava de segurança: se o offset for maior que o total retornado, força volta para a página 1
        if offset >= total_itens and total_itens > 0:
            st.session_state.pagina_atual_produtos = 1
            offset = 0
            response_prod = query.order("id").range(offset, offset + itens_por_pagina - 1).execute()

        produtos = response_prod.data or []

        if not produtos:
            st.info("Nenhum produto encontrado para o filtro digitado.")
            return

        # 3. Paginação informativa no topo da tabela
        # O componente exibe a mesma informação da paginação inferior,
        # sem duplicar os controles de navegação.
        render_paginacao(
            "produtos",
            total_itens,
            itens_por_pagina=itens_por_pagina,
            mostrar_contagem_superior=True,
            mostrar_contagem_inferior=False,
            permitir_seletor=True,
        )

        # 4. Tabela dentro de Container
        with st.container(border=True):
            c_id, c_cod, c_desc, c_cat, c_banho, c_ult_compra, c_nr_compra, c_acao = st.columns([1, 2, 3, 2, 2, 2, 1, 1])

            c_id.markdown("**id**")
            c_cod.markdown("**codigo_interno**")
            c_desc.markdown("**descricao**")
            c_cat.markdown("**categoria**")
            c_banho.markdown("**banho**")
            c_ult_compra.markdown("**ultima_compra**")
            c_nr_compra.markdown("**compras**")
            c_acao.markdown("**Ações**")

            st.divider()

            for prod in produtos:
                col_id, col_cod, col_desc, col_cat, col_banho, col_ult_compra, col_nr_compra, col_acao = st.columns([1, 2, 3, 2, 2, 2, 1, 1])

                col_id.write(prod["id"])
                col_cod.write(prod.get("codigo_interno", "-"))
                col_desc.write(prod.get("descricao", "-"))

                cat_atual_id = prod.get("categoria_id")
                info_cat = cat_dict.get(cat_atual_id)

                if info_cat:
                    col_cat.write(info_cat["categoria"])
                    col_banho.write(info_cat["banho"])
                else:
                    col_cat.write("Sem Categoria")
                    col_banho.write("-")

                col_ult_compra.write(str(prod.get("ultima_compra")) if prod.get("ultima_compra") else "-")
                col_nr_compra.write(str(prod.get("nr_compras", 0)))

                if cat_ids:
                    with col_acao.popover("✏️"):
                        st.markdown("**Vincular Categoria**")
                        st.caption(f"Produto ID: {prod['id']}")

                        index_atual = cat_ids.index(cat_atual_id) if cat_atual_id in cat_ids else 0

                        nova_cat_id = st.selectbox(
                            "Categoria | Banho",
                            options=cat_ids,
                            format_func=lambda cid: cat_dict[cid]["rotulo_dropdown"],
                            index=index_atual,
                            key=f"select_cat_{prod['id']}"
                        )

                        if st.button("💾 Salvar", key=f"btn_save_{prod['id']}", use_container_width=True):
                            try:
                                supabase.table("produtos").update({"categoria_id": nova_cat_id}).eq("id", prod["id"]).execute()
                                st.toast("Categoria atualizada com sucesso!", icon="✅")
                                st.rerun()
                            except Exception as err:
                                st.error(f"Erro ao salvar: {err}")
                else:
                    col_acao.caption("-")

        # 5. Paginação reutilizável
        render_paginacao(
            "produtos",
            total_itens,
            itens_por_pagina=itens_por_pagina,
            mostrar_contagem_superior=False,
            mostrar_contagem_inferior=True,
            permitir_seletor=True,
        )

    except Exception as e:
        st.error(f"Erro ao consultar produtos: {e}")


def tela_produtos():
    # Cabeçalho principal acima das abas, conforme o layout solicitado.
    try:
        total_resp = supabase.table("produtos").select("id", count="exact").range(0, 0).execute()
        total_itens = total_resp.count if total_resp.count is not None else 0
    except Exception:
        total_itens = 0

    st.header(f"📦 Produtos ({total_itens} Itens)")

    aba_consulta, aba_categorias = st.tabs(["Consulta Produto", "Cadastrar categorias"])

    with aba_consulta:
        _secao_consulta()

    with aba_categorias:
        tela_categorias()
