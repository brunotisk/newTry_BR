from datetime import date

import streamlit as st
from db import supabase
from telas.cadastros_gerais import tela_categorias
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao
from componentes.busca_produto import busca_produto
from componentes.foto_produto import campo_foto, salvar_foto
from servicos.produtos_manual import (
    cadastrar_produto_manual,
    atualizar_estoque_e_precos,
    preco_venda_sugerido,
)

# Compatibilidade com versões do Streamlit que usam experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog


def _fmt_moeda(v) -> str:
    return "R$ " + f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _carregar_produtos_para_busca():
    """Carrega os produtos e saldos usados pelo dropdown pesquisável."""
    produtos_resp = (
        supabase
        .table("produtos")
        .select("id, codigo_interno, descricao, categoria_id")
        .order("codigo_interno")
        .execute()
    )
    produtos = produtos_resp.data or []

    estoque_resp = (
        supabase
        .table("estoque")
        .select("produto_id, quantidade_atual")
        .execute()
    )
    saldos = {
        item.get("produto_id"): item.get("quantidade_atual", 0)
        for item in (estoque_resp.data or [])
    }

    for produto in produtos:
        produto["saldo"] = saldos.get(produto.get("id"), 0)

    return produtos


@st.cache_data(ttl=30, show_spinner=False)
def _produtos_busca_cache():
    return _carregar_produtos_para_busca()


@_dialog("📦 Produto", width="large")
def _dialog_produto(produto=None):
    """Popup único: cadastra um produto novo ou edita estoque e preços de um existente."""
    novo = produto is None
    sufixo = "novo" if novo else str(produto["id"])

    # Valores atuais (edição)
    saldo_atual, compra_atual, venda_atual = 0.0, 0.0, 0.0
    if not novo:
        linha = (
            supabase.table("estoque")
            .select("quantidade_atual, estoque_preco_ultima_compra, estoque_preco_venda_sugerida")
            .eq("produto_id", produto["id"])
            .execute()
            .data
            or [{}]
        )[0]
        saldo_atual = float(linha.get("quantidade_atual") or 0)
        compra_atual = float(linha.get("estoque_preco_ultima_compra") or 0)
        venda_atual = float(linha.get("estoque_preco_venda_sugerida") or 0)

    st.caption("Novo produto" if novo else f"{produto['codigo_interno']} — {produto.get('descricao') or ''}")

    categoria_id = None
    unidade = ""
    codigo = descricao = ""
    if novo:
        cats = (
            supabase.table("categorias_produtos")
            .select("id, categoria, banho")
            .eq("ativo", True)
            .order("ordem_exibicao")
            .execute()
            .data
            or []
        )
        rotulos = {c["id"]: f"{c['categoria']} | {c.get('banho') or '-'}" for c in cats}
        c1, c2 = st.columns([1, 2])
        codigo = c1.text_input("Código interno *", key=f"p_cod_{sufixo}")
        descricao = c2.text_input("Descrição *", key=f"p_desc_{sufixo}")
        c3, c4 = st.columns([2, 1])
        categoria_id = c3.selectbox(
            "Categoria | Banho (opcional)",
            list(rotulos),
            index=None,
            format_func=lambda i: rotulos[i],
            placeholder="Selecione...",
            key=f"p_cat_{sufixo}",
        )
        unidade = c4.text_input("Unidade (opcional)", value="UN", key=f"p_un_{sufixo}")

    c1, c2, c3 = st.columns(3)
    quantidade = c1.number_input(
        "Quantidade em estoque" if novo else "Quantidade em estoque (novo saldo)",
        min_value=0.0, step=1.0, value=saldo_atual, format="%g", key=f"p_qtd_{sufixo}",
    )
    preco_compra = c2.number_input(
        "Valor de compra (R$)", min_value=0.0, step=0.01, value=compra_atual,
        format="%.2f", key=f"p_compra_{sufixo}",
    )
    preco_venda = c3.number_input(
        "Valor de venda (R$)", min_value=0.0, step=0.01, value=venda_atual,
        format="%.2f", key=f"p_venda_{sufixo}",
        help="Deixe 0 para usar o sugerido (2× o valor de compra).",
    )
    sug = preco_venda_sugerido(preco_compra)
    if not preco_venda and sug:
        st.caption(f"Valor de venda sugerido (2× compra): **{_fmt_moeda(sug)}** — será usado ao salvar.")

    c4, c5 = st.columns([1, 3])
    data_mov = c4.date_input("Data do movimento", value=date.today(), format="DD/MM/YYYY", key=f"p_data_{sufixo}")
    motivo = ""
    if not novo:
        motivo = c5.text_input(
            "Motivo (opcional)", key=f"p_motivo_{sufixo}",
            placeholder="Ex.: contagem física, produto sem NF...",
        )

    arquivo_foto = campo_foto(
        f"p_foto_{sufixo}", None if novo else produto.get("foto_url"), "Foto do produto (opcional)"
    )

    if not novo and quantidade != saldo_atual:
        st.info(f"Será registrado um ajuste de {quantidade - saldo_atual:+g} (saldo {saldo_atual:g} → {quantidade:g}).")

    if st.button("💾 Salvar", type="primary", use_container_width=True, key=f"p_salvar_{sufixo}"):
        try:
            if novo:
                produto_id = cadastrar_produto_manual(
                    supabase, codigo, descricao, data_mov.isoformat(),
                    categoria_id=categoria_id, unidade=unidade, quantidade=quantidade,
                    preco_compra=preco_compra or None, preco_venda=preco_venda or None,
                )
                codigo_foto = codigo.strip()
                st.toast("Produto cadastrado!", icon="✅")
            else:
                atualizar_estoque_e_precos(
                    supabase, produto["id"], quantidade, data_mov.isoformat(),
                    preco_compra or None, preco_venda or None, motivo,
                )
                produto_id, codigo_foto = produto["id"], produto.get("codigo_interno")
                st.toast("Estoque e preços atualizados!", icon="✅")
            if arquivo_foto is not None:
                erro_foto = salvar_foto(supabase, produto_id, codigo_foto, arquivo_foto)
                if erro_foto:
                    st.toast(f"Salvo, mas a foto não foi enviada: {erro_foto}", icon="⚠️")
                else:
                    st.toast("Foto enviada!", icon="📷")
            _produtos_busca_cache.clear()
            st.rerun()
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Erro ao salvar: {e}")


def _secao_consulta():
    st.markdown("""
        <style>
        div[data-testid="stDivider"] {
            margin-top: -10px !important;
            margin-bottom: 10px !important;
        }
        div[data-testid="stCustomComponentV1"] {
            position: relative;
            z-index: 1000;
        }
        </style>
    """, unsafe_allow_html=True)

    try:
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

        categorias_unicas = list(dict.fromkeys([cat["categoria"] for cat in categorias if cat.get("categoria")]))
        opcoes_cat = ["Todas as Categorias", "Sem Categoria"] + categorias_unicas

        # Layout: Produto e Categoria na mesma linha, conforme o layout definido.
        # Produto ocupa a coluna menor; Categoria ocupa a coluna maior.
        col_busca, col_cat_filtro = st.columns([1, 1])

        modo_descricao = st.session_state.get("filtro_produto_buscar_descricao", False)
        filtrar_sem_cat = st.session_state.get("produtos_apenas_sem_categoria", False)
        filtrar_sem_foto = st.session_state.get("produtos_apenas_sem_foto", False)
        mostrar_descricao_site = st.session_state.get("produtos_mostrar_descricao_site", False)

        with col_busca:
            produtos_busca = _produtos_busca_cache()

        with col_cat_filtro:
            categoria_selecionada = st.selectbox(
                "Categoria",
                options=opcoes_cat,
                index=0,
                key="produtos_categoria_filtro",
            )

        if filtrar_sem_cat or categoria_selecionada == "Sem Categoria":
            produtos_busca = [p for p in produtos_busca if p.get("categoria_id") is None]
        elif categoria_selecionada != "Todas as Categorias":
            ids_cat_sel = [cid for cid, info in cat_dict.items() if info["categoria"] == categoria_selecionada]
            produtos_busca = [p for p in produtos_busca if p.get("categoria_id") in ids_cat_sel]

        chave_busca = "filtro_produto_termo"
        termo_atual = st.session_state.get(chave_busca, "")

        with col_busca:
            resultado_busca = busca_produto(
                produtos_busca,
                label="Produto",
                key="filtro_produto",
                buscar_descricao=modo_descricao,
                mostrar_saldo=True,
                valor_selecionado=st.session_state.get("produtos_produto_id"),
                termo=termo_atual,
            )

        # O toggle "Buscar descrição" agora vive dentro do próprio componente
        # (ao lado do rótulo "Produto"); depois da chamada acima já refletimos
        # aqui o modo mais recente (inclusive um clique que tenha disparado
        # esta mesma execução).
        modo_descricao = st.session_state.get("filtro_produto_buscar_descricao", modo_descricao)

        with col_cat_filtro:
            col_toggle_cat, col_toggle_foto, col_toggle_desc_site = st.columns([1, 1, 1])
            with col_toggle_cat:
                st.toggle(
                    "Apenas sem categoria",
                    value=filtrar_sem_cat,
                    key="produtos_apenas_sem_categoria",
                )
            with col_toggle_foto:
                st.toggle(
                    "Apenas sem foto",
                    value=filtrar_sem_foto,
                    key="produtos_apenas_sem_foto",
                    help="Exibe somente produtos sem foto cadastrada.",
                )
            with col_toggle_desc_site:
                mostrar_descricao_site = st.toggle(
                    "Descrição do site",
                    value=mostrar_descricao_site,
                    key="produtos_mostrar_descricao_site",
                    help="Mostra a descrição do site quando cadastrada; caso contrário, mantém a descrição original.",
                )

        # O componente é um iframe. Quando o dropdown abre ele precisa crescer
        # para manter a lista clicável; este pequeno espaço negativo devolve
        # a altura extra à página para que a tabela não seja deslocada.
        if st.session_state.get("filtro_produto_aberto", False):
            st.markdown(
                "<div style='height:0; margin-bottom:-288px;'></div>",
                unsafe_allow_html=True,
            )

        if resultado_busca is not None:
            st.session_state["produtos_produto_id"] = resultado_busca
        elif st.session_state.get("filtro_produto_termo", "") != termo_atual:
            st.session_state["produtos_produto_id"] = None

        produto_id_filtro = st.session_state.get("produtos_produto_id")

        # Se o produto selecionado deixou de pertencer ao filtro de categoria,
        # limpa a seleção para não filtrar a tabela por um item invisível.
        ids_visiveis = {p.get("id") for p in produtos_busca}
        if produto_id_filtro is not None and produto_id_filtro not in ids_visiveis:
            st.session_state["produtos_produto_id"] = None
            produto_id_filtro = None

        # Inicializa o estado da página
        if "pagina_atual_produtos" not in st.session_state:
            st.session_state.pagina_atual_produtos = 1

        # Define a paginação antes de montar a query.
        # O offset precisa existir mesmo quando há filtros de produto/categoria.
        itens_por_pagina = get_itens_por_pagina("produtos", 50)
        offset = (st.session_state.pagina_atual_produtos - 1) * itens_por_pagina

        # Reseta para a página 1 se qualquer filtro mudar
        chave_filtro = f"{produto_id_filtro}_{categoria_selecionada}_{filtrar_sem_cat}_{filtrar_sem_foto}_{modo_descricao}"
        if st.session_state.get("chave_filtro_ant") != chave_filtro:
            st.session_state.pagina_atual_produtos = 1
            st.session_state.chave_filtro_ant = chave_filtro

        # 2. Construção da query com contagem total
        query = supabase.table("produtos").select("id, codigo_interno, foto_url, descricao, produto_descricao_site, categoria_id, ultima_compra, nr_compras", count="exact")

        if filtrar_sem_cat or categoria_selecionada == "Sem Categoria":
            query = query.is_("categoria_id", "null")
        elif categoria_selecionada != "Todas as Categorias":
            ids_cat_sel = [cid for cid, info in cat_dict.items() if info["categoria"] == categoria_selecionada]
            if ids_cat_sel:
                query = query.in_("categoria_id", ids_cat_sel)

        if filtrar_sem_foto:
            # Inclui URLs nulas e strings vazias.
            query = query.or_("foto_url.is.null,foto_url.eq.")

        if produto_id_filtro is not None:
            query = query.eq("id", produto_id_filtro)

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
        larguras = [1, 2, 1, 3, 2, 2, 2, 1, 1.6]
        with st.container(border=True):
            c_id, c_cod, c_foto, c_desc, c_cat, c_banho, c_ult_compra, c_nr_compra, c_acao = st.columns(larguras)

            c_id.markdown("**id**")
            c_cod.markdown("**codigo_interno**")
            c_foto.markdown("**foto**")
            c_desc.markdown("**descricao**")
            c_cat.markdown("**categoria**")
            c_banho.markdown("**banho**")
            c_ult_compra.markdown("**ultima_compra**")
            c_nr_compra.markdown("**compras**")
            c_acao.markdown("**Ações**")

            st.divider()

            for prod in produtos:
                col_id, col_cod, col_foto, col_desc, col_cat, col_banho, col_ult_compra, col_nr_compra, col_acao = st.columns(larguras)

                col_id.write(prod["id"])
                col_cod.write(prod.get("codigo_interno", "-"))
                foto_url = prod.get("foto_url")
                if foto_url:
                    col_foto.image(foto_url, width=64)
                else:
                    col_foto.write("—")
                descricao_original = prod.get("descricao")
                descricao_site = prod.get("produto_descricao_site")
                descricao_exibida = (
                    descricao_site.strip()
                    if mostrar_descricao_site and isinstance(descricao_site, str) and descricao_site.strip()
                    else descricao_original
                )
                col_desc.write(descricao_exibida or "-")

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

                col_pop, col_est = col_acao.columns(2)
                if cat_ids:
                    with col_pop.popover("✏️"):
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
                    col_pop.caption("-")

                if col_est.button("📦", key=f"btn_estoque_{prod['id']}", help="Editar estoque, preços e foto"):
                    _dialog_produto(prod)

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

    if st.button("➕ Novo produto", key="produtos_btn_novo"):
        _dialog_produto(None)

    aba_consulta, aba_categorias = st.tabs(["Consulta Produto", "Cadastrar categorias"])

    with aba_consulta:
        _secao_consulta()

    with aba_categorias:
        tela_categorias()
