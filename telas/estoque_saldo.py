import html
import streamlit as st
from db import supabase
from componentes.paginacao import render_paginacao, reset_paginacao, get_itens_por_pagina
from componentes.busca_produto import busca_produto, filtrar_por_termo
from componentes.ordenacao import ordenacao

def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 3 em vez de 3.0)."""
    valor = float(valor or 0)
    return f"{valor:g}"


def _injetar_estilo_tabela_estoque():
    """CSS da tabela de estoque: descrição sem quebra de linha (com ...
    e tooltip no hover) e o botão de preço de venda centralizado, com
    largura fixa (não varia conforme o tamanho do valor), o que também
    mantém um respiro constante antes da coluna 'Data Ult. Compra'."""
    st.markdown(
        """
        <style>
        .cel-truncada {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
        }
        div[class*="st-key-venda_btn_wrap_"] div[data-testid="stButton"] {
            display: flex;
            justify-content: center;
        }
        div[class*="st-key-venda_btn_wrap_"] div[data-testid="stButton"] button {
            width: 140px;
            min-width: 140px;
            max-width: 140px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _celula_truncada(texto: str) -> str:
    """HTML de uma célula que não quebra linha: corta com "..." quando não
    cabe e mostra o texto completo no title (tooltip ao passar o mouse)."""
    texto_seguro = html.escape(str(texto or "-"))
    return f'<div class="cel-truncada" title="{texto_seguro}">{texto_seguro}</div>'

# Compatibilidade com versões do Streamlit que usam experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog


@_dialog("Ajustar preço de venda")
def _dialog_editar_preco_venda(produto_id, codigo_interno, descricao, preco_atual, preco_original=None, flag_ajuste=False):
    """Permite editar o preço de venda sugerido e registra o ajuste no estoque."""
    st.markdown(f"**{codigo_interno} — {descricao}**")
    st.caption(f"Preço de venda atual: {_fmt_moeda(preco_atual)}")

    if flag_ajuste and preco_original is not None:
        st.info(f"Preço original antes do primeiro ajuste: {_fmt_moeda(preco_original)}")

    preco_novo = st.number_input(
        "Novo preço de venda",
        min_value=0.0,
        value=float(preco_atual or 0),
        step=0.01,
        format="%.2f",
        key=f"estoque_preco_venda_edit_{produto_id}",
    )

    col_cancelar, col_salvar = st.columns(2)

    with col_cancelar:
        if st.button(
            "Cancelar",
            key=f"cancelar_preco_venda_{produto_id}",
            use_container_width=True,
        ):
            st.rerun()

    with col_salvar:
        if st.button(
            "💾 Salvar alteração",
            type="primary",
            key=f"salvar_preco_venda_{produto_id}",
            use_container_width=True,
        ):
            novo_valor = float(preco_novo)
            valor_atual = float(preco_atual or 0)

            try:
                # Primeira edição: grava o preço atual como original.
                # O filtro pela flag impede que uma segunda edição sobrescreva
                # o valor original mesmo em caso de concorrência.
                primeira_edicao = (
                    supabase.table("estoque")
                    .update({
                        "estoque_preco_venda_sugerida": novo_valor,
                        "estoque_flag_ajuste_preco_venda": True,
                        "estoque_preco_venda_original": valor_atual,
                    })
                    .eq("produto_id", produto_id)
                    .eq("estoque_flag_ajuste_preco_venda", False)
                    .execute()
                )

                if not primeira_edicao.data:
                    # Produto já ajustado: altera somente o preço atual.
                    (
                        supabase.table("estoque")
                        .update({
                            "estoque_preco_venda_sugerida": novo_valor,
                            "estoque_flag_ajuste_preco_venda": True,
                        })
                        .eq("produto_id", produto_id)
                        .execute()
                    )

            except Exception as e:
                st.error(f"Erro ao salvar o novo preço de venda: {e}")
                return

            st.success("Preço de venda atualizado.")
            st.rerun()

def _secao_estoque_atual():
    try:
        # Estoque + produto embutido (join via FK estoque.produto_id -> produtos.id)
        response = (
            supabase.table("estoque")
            .select(
                "produto_id, quantidade_atual, estoque_preco_ultima_compra, "
                "estoque_preco_venda_sugerida, estoque_preco_venda_original, "
                "estoque_flag_ajuste_preco_venda, estoque_ultima_compra, "
                "produtos(id, codigo_interno, descricao)"
            )
            .execute()
        )
        linhas_brutas = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar estoque: {e}")
        return

    # Achata a estrutura (produto embutido) e ignora linhas órfãs
    itens = []
    for linha in linhas_brutas:
        prod = linha.get("produtos")
        if not prod:
            continue
        itens.append({
            "id": prod["id"],
            "codigo_interno": prod.get("codigo_interno") or "-",
            "descricao": prod.get("descricao") or "-",
            "saldo": float(linha.get("quantidade_atual") or 0),
            "preco_compra": float(linha.get("estoque_preco_ultima_compra") or 0),
            "preco_venda": float(linha.get("estoque_preco_venda_sugerida") or 0),
            "preco_venda_original": linha.get("estoque_preco_venda_original"),
            "flag_ajuste_preco_venda": bool(linha.get("estoque_flag_ajuste_preco_venda", False)),
            "data_ultima_compra": linha.get("estoque_ultima_compra"),
        })

    # Custo unitário estimado: preço da última compra armazenado em estoque.
    custo_unitario = {
        item["id"]: item["preco_compra"]
        for item in itens
    }

    # Classificação de status: sem estoque mínimo configurável, só zerado/negativo vs. OK
    for item in itens:
        if item["saldo"] <= 0:
            item["status"] = "🔴 Zerado" if item["saldo"] == 0 else "🔴 Negativo"
        else:
            item["status"] = "🟢 OK"

    # KPIs
    # Consideram todos os itens carregados, antes dos filtros da tabela.
    qtde_pecas_estoque = sum(i["saldo"] for i in itens)
    qtde_distinta_pecas = len({i["id"] for i in itens})
    valor_estoque_compra = sum(
        i["saldo"] * i["preco_compra"] for i in itens
    )
    valor_estoque_venda = sum(
        i["saldo"] * i["preco_venda"] for i in itens
    )

    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    with col_kpi1:
        with st.container(border=True):
            st.caption("Qtde. Peças Estoque")
            st.title(_fmt_qtd(qtde_pecas_estoque))
    with col_kpi2:
        with st.container(border=True):
            st.caption("Qtde. Distinta Peças")
            st.title(f"{qtde_distinta_pecas}")
    with col_kpi3:
        with st.container(border=True):
            st.caption("Valor Estoque Compra")
            st.title(_fmt_moeda(valor_estoque_compra))
    with col_kpi4:
        with st.container(border=True):
            st.caption("Valor Estoque Venda")
            st.title(_fmt_moeda(valor_estoque_venda))

    st.caption(
        "Valores calculados pela quantidade em estoque multiplicada pelo preço de compra "
        "da última compra e pelo preço de venda sugerido."
    )
    st.markdown("---")

    # Filtros e ordenação
    col_busca, col_ordenar, col_toggles = st.columns([50, 35, 15])

    with col_busca:
        # Mesmo componente pesquisável usado em produtos.py: dropdown com
        # busca por código (prefixo) ou descrição, alternável dentro do
        # próprio componente. Reaproveita a lista "itens" desta tela, já que
        # as chaves batem (id, codigo_interno, descricao, saldo).
        produto_id_selecionado = busca_produto(
            produtos=itens,
            label="Buscar produto",
            placeholder="Digite o código ou a descrição...",
            key="estoque_busca_produto",
            mostrar_saldo=True,
        )

    with col_ordenar:
        # Colunas oferecidas para ordenar NESTA tela — é este parâmetro que
        # torna o componente `ordenacao` reutilizável em outras telas/tabelas,
        # cada uma passando seu próprio conjunto de campos.
        colunas_ordenacao = [
            {"chave": "codigo_interno", "rotulo": "Cod. Produto"},
            {"chave": "saldo", "rotulo": "Qtde. Estoque"},
            {"chave": "preco_venda", "rotulo": "Preço Venda"},
            {"chave": "data_ultima_compra", "rotulo": "Data Ult. Compra"},
        ]
        campo_ordenacao, ordem_decrescente = ordenacao(
            colunas=colunas_ordenacao,
            campo_padrao="codigo_interno",
            key="estoque_ordenacao",
        )

    with col_toggles:
        # Os dois toggles ficam empilhados (um embaixo do outro) na mesma
        # coluna, já que juntos ocupam só 15% da largura da barra.
        mostrar_negativo = st.toggle("🔴 Mostrar zerado/negativo", value=False)
        mostrar_somente_editados = st.toggle("✏️ Somente editados", value=False)

    # Termo digitado e modo de busca (código/descrição) ficam disponíveis em
    # session_state depois da chamada acima, mesmo quando o usuário ainda não
    # selecionou um produto específico na lista.
    termo_busca_produto = st.session_state.get("estoque_busca_produto_termo", "")
    buscar_descricao_produto = st.session_state.get("estoque_busca_produto_buscar_descricao", False)

    if produto_id_selecionado is not None:
        # Produto específico escolhido no dropdown: mostra só ele.
        itens = [i for i in itens if i["id"] == produto_id_selecionado]
    elif termo_busca_produto:
        # Ainda digitando (sem selecionar): mesma regra de busca do
        # componente, aplicada à listagem completa da tela.
        itens = filtrar_por_termo(itens, termo_busca_produto, buscar_descricao_produto)

    if mostrar_negativo:
        itens = [i for i in itens if i["status"] != "🟢 OK"]
    else:
        itens = [i for i in itens if i["status"] == "🟢 OK"]

    if mostrar_somente_editados:
        itens = [i for i in itens if i.get("flag_ajuste_preco_venda")]

    # Ordenação escolhida pelo usuário (crescente/decrescente pelo campo selecionado)
    if campo_ordenacao == "data_ultima_compra":
        # Itens sem data de compra sempre vão para o final, independente da direção
        def _chave_ordenacao(item):
            data = item["data_ultima_compra"]
            return (data is None, data or "")
    elif campo_ordenacao == "codigo_interno":
        def _chave_ordenacao(item):
            return item["codigo_interno"].lower()
    else:
        def _chave_ordenacao(item):
            return item[campo_ordenacao]

    itens.sort(key=_chave_ordenacao, reverse=ordem_decrescente)

    # Detecta mudança nos filtros/ordenação e volta para a primeira página.
    filtro_atual = (
        produto_id_selecionado,
        termo_busca_produto.strip().lower(),
        bool(buscar_descricao_produto),
        bool(mostrar_negativo),
        bool(mostrar_somente_editados),
        campo_ordenacao,
        bool(ordem_decrescente),
    )
    if st.session_state.get("estoque_filtro_atual") != filtro_atual:
        st.session_state["estoque_filtro_atual"] = filtro_atual
        reset_paginacao("estoque_atual")

    total_itens = len(itens)
    itens_por_pagina = get_itens_por_pagina("estoque_atual", 50)
    total_paginas = max(1, (total_itens + itens_por_pagina - 1) // itens_por_pagina)
    pagina_atual = min(int(st.session_state.get("pagina_atual_estoque_atual", 1)), total_paginas)
    st.session_state["pagina_atual_estoque_atual"] = max(1, pagina_atual)

    st.caption(
        f"Exibindo página {st.session_state['pagina_atual_estoque_atual']} de {total_paginas} "
        f"({total_itens} registros no total)."
    )

    inicio = (st.session_state["pagina_atual_estoque_atual"] - 1) * itens_por_pagina
    itens_pagina = itens[inicio:inicio + itens_por_pagina]

    if not itens_pagina:
        st.info("Nenhum produto encontrado para o filtro selecionado.")
        return

    _injetar_estilo_tabela_estoque()

    with st.container(border=True):
        c_cod, c_desc, c_saldo, c_compra, c_venda, c_espaco, c_data = st.columns(
            [1.4, 2.3, 1.3, 1.5, 1.5, 0.3, 1.6]
        )
        c_cod.markdown("**Cod. Produto**")
        c_desc.markdown("**Desc. Produto**")
        c_saldo.markdown("**Qtde. Estoque**")
        c_compra.markdown("**Preço Compra**")
        c_venda.markdown("**Preço Venda**")
        c_data.markdown("**Data Ult. Compra**")

        st.divider()

        for item in itens_pagina:
            col_cod, col_desc, col_saldo, col_compra, col_venda, col_espaco, col_data = st.columns(
                [1.4, 2.3, 1.3, 1.5, 1.5, 0.3, 1.6],
                vertical_alignment="center",
            )
            col_cod.write(item["codigo_interno"])

            col_desc.markdown(_celula_truncada(item["descricao"]), unsafe_allow_html=True)

            col_saldo.write(_fmt_qtd(item["saldo"]))
            col_compra.write(_fmt_moeda(item["preco_compra"]))

            # Preço de venda clicável, com a mesma interface da versão Lapis.
            # O hover do próprio botão mostra o preço original quando o produto
            # já passou pelo primeiro ajuste.
            rotulo_preco = _fmt_moeda(item["preco_venda"])
            if item["flag_ajuste_preco_venda"]:
                rotulo_preco = f"✏️ {rotulo_preco}"

            if item.get("flag_ajuste_preco_venda") and item.get("preco_venda_original") is not None:
                preco_original_fmt = _fmt_moeda(item["preco_venda_original"])
                help_edicao = (
                    f"Editar preço de venda. "
                    f"Preço original antes do primeiro ajuste: {preco_original_fmt}"
                )
            else:
                help_edicao = "Clique para editar o preço de venda."

            with col_venda.container(key=f"venda_btn_wrap_{item['id']}"):
                clicou_preco_venda = st.button(
                    rotulo_preco,
                    key=f"editar_preco_venda_{item['id']}",
                    help=help_edicao,
                )
            if clicou_preco_venda:
                _dialog_editar_preco_venda(
                    item["id"],
                    item["codigo_interno"],
                    item["descricao"],
                    item["preco_venda"],
                    item.get("preco_venda_original"),
                    item.get("flag_ajuste_preco_venda", False),
                )

            data_compra = item["data_ultima_compra"]
            if data_compra:
                try:
                    data_compra = str(data_compra)[:10]
                    ano, mes, dia = data_compra.split("-")
                    data_compra = f"{dia}/{mes}/{ano}"
                except (ValueError, AttributeError):
                    pass
            else:
                data_compra = "-"
            col_data.write(data_compra)

    render_paginacao(
        "estoque_atual",
        total_itens,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
        permitir_seletor=True,
    )


def render_estoque_saldo():
    _secao_estoque_atual()


def tela_estoque_saldo():
    """Página principal de Estoque. Mantém as três visões como abas internas."""
    st.header("📊 Controle de Estoque")

    aba_atual, aba_mov, aba_ajuste = st.tabs(
        ["Estoque atual", "Movimentações", "Ajuste manual"]
    )

    with aba_atual:
        render_estoque_saldo()

    with aba_mov:
        from telas.estoque_movimentacao import render_estoque_movimentacao
        render_estoque_movimentacao()

    with aba_ajuste:
        from telas.estoque_ajuste import tela_estoque_ajuste
        tela_estoque_ajuste()
