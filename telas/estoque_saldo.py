import html
from datetime import date
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


def _renderizar_kpi_grupo(titulo: str, item1: tuple[str, str], item2: tuple[str, str]) -> None:
    """Renderiza um card de KPI com dois números lado a lado sob um título
    comum (ex.: "Quantidade de Produtos" -> Peças em estoque | Distintos).

    É HTML puro via st.markdown (não usa st.container/st.columns por dentro)
    de propósito: o padding do card fica 100% sob nosso controle, sem
    depender de sobrescrever o CSS interno que o Streamlit gera para
    data-testid="stVerticalBlockBorderWrapper" — essa dependência é frágil e
    muda de comportamento entre versões do Streamlit.
    """
    rotulo1, valor1 = item1
    rotulo2, valor2 = item2
    st.markdown(
        f"""
        <div class="kpi-grupo-card">
            <div class="kpi-grupo-titulo">{html.escape(titulo)}</div>
            <div class="kpi-grupo-linha">
                <div class="kpi-grupo-item">
                    <div class="kpi-grupo-rotulo">{html.escape(rotulo1)}</div>
                    <div class="kpi-grupo-valor">{html.escape(valor1)}</div>
                </div>
                <div class="kpi-grupo-divisor"></div>
                <div class="kpi-grupo-item">
                    <div class="kpi-grupo-rotulo">{html.escape(rotulo2)}</div>
                    <div class="kpi-grupo-valor">{html.escape(valor2)}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _injetar_estilo_tabela_estoque():
    """CSS da tabela de estoque e dos indicadores de idade."""
    st.markdown(
        """
        <style>
        .cel-truncada {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
        }

        /* KPIs de estoque agrupados (quantidade / valor). Cards em HTML puro
           (não usam st.container/st.columns internos), então o padding aqui
           é garantido — não depende de sobrescrever CSS interno do Streamlit. */
        .kpi-grupo-card {
            border: 1px solid rgba(128, 128, 128, 0.35);
            border-radius: 0.5rem;
            padding: 1.1rem 1.1rem 1.6rem 1.1rem;
            box-sizing: border-box;
            min-height: 104px;
        }
        .kpi-grupo-titulo {
            text-align: center;
            font-weight: 700;
            font-size: 0.92rem;
            opacity: 0.9;
            margin-bottom: 0.7rem;
        }
        .kpi-grupo-linha {
            display: flex;
            align-items: stretch;
        }
        .kpi-grupo-item {
            flex: 1 1 0;
            text-align: center;
            min-width: 0;
        }
        .kpi-grupo-divisor {
            width: 1px;
            background: rgba(128, 128, 128, 0.25);
            margin: 0 0.75rem;
        }
        .kpi-grupo-rotulo {
            font-size: 0.8rem;
            opacity: 0.75;
            margin-bottom: 0.25rem;
        }
        .kpi-grupo-valor {
            font-size: 1.8rem;
            font-weight: 700;
            line-height: 1.15;
            white-space: nowrap;
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
        .idade-produto {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            font-weight: 700;
            white-space: nowrap;
            cursor: help;
            padding: 5px 10px;
            border-radius: 7px;
            border: 1px solid transparent;
            line-height: 1.2;
        }
        .idade-dot {
            width: 11px;
            height: 11px;
            border-radius: 50%;
            display: inline-block;
            flex: 0 0 11px;
            border: 1px solid rgba(0, 0, 0, 0.18);
        }
        .idade-verde {
            background: #166534;
            border-color: #22c55e;
            color: #ffffff;
        }
        .idade-verde .idade-dot { background: #4ade80; }
        .idade-amarela {
            background: #854d0e;
            border-color: #facc15;
            color: #ffffff;
        }
        .idade-amarela .idade-dot { background: #fde047; }
        .idade-laranja {
            background: #9a3412;
            border-color: #fb923c;
            color: #ffffff;
        }
        .idade-laranja .idade-dot { background: #fb923c; }
        .idade-vermelha {
            background: #991b1b;
            border-color: #f87171;
            color: #ffffff;
        }
        .idade-vermelha .idade-dot { background: #f87171; }

        .idade-legenda {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 7px 10px;
            border-radius: 7px;
            font-size: 0.88rem;
            font-weight: 600;
            white-space: nowrap;
        }
        .idade-legenda .idade-dot {
            width: 10px;
            height: 10px;
            flex-basis: 10px;
        }
        .idade-legenda-verde { background: #166534; color: #fff; }
        .idade-legenda-amarela { background: #854d0e; color: #fff; }
        .idade-legenda-laranja { background: #9a3412; color: #fff; }
        .idade-legenda-vermelha { background: #991b1b; color: #fff; }

        /* KPIs de idade são filtros clicáveis. */
        div[class*="st-key-filtro_idade_"] button {
            width: 100%;
            min-height: 46px;
            border-radius: 8px;
            font-weight: 700;
            white-space: nowrap;
        }
        div[class*="st-key-filtro_idade_verde_"] button {
            background: #166534;
            color: #ffffff;
            border: 1px solid #22c55e;
        }
        div[class*="st-key-filtro_idade_amarela_"] button {
            background: #854d0e;
            color: #ffffff;
            border: 1px solid #facc15;
        }
        div[class*="st-key-filtro_idade_laranja_"] button {
            background: #9a3412;
            color: #ffffff;
            border: 1px solid #fb923c;
        }
        div[class*="st-key-filtro_idade_vermelha_"] button {
            background: #991b1b;
            color: #ffffff;
            border: 1px solid #f87171;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _classificar_idade(idade_dias):
    """Retorna classe CSS e cores para as faixas de idade do estoque.

    Faixas adotadas: 0-60 (verde), >60-120 (amarelo), >120-180
    (laranja) e >180 (vermelho).
    """
    if idade_dias is None:
        return {
            "classe": "idade-verde",
            "cor_fundo": "#f0fdf4",
            "cor_borda": "#86efac",
            "rotulo": "Sem idade",
        }
    idade = float(idade_dias)
    if idade <= 60:
        return {
            "classe": "idade-verde",
            "cor_fundo": "#f0fdf4",
            "cor_borda": "#86efac",
            "rotulo": "0-60 dias",
        }
    if idade <= 120:
        return {
            "classe": "idade-amarela",
            "cor_fundo": "#fefce8",
            "cor_borda": "#fde047",
            "rotulo": ">60-120 dias",
        }
    if idade <= 180:
        return {
            "classe": "idade-laranja",
            "cor_fundo": "#fff7ed",
            "cor_borda": "#fdba74",
            "rotulo": ">120-180 dias",
        }
    return {
        "classe": "idade-vermelha",
        "cor_fundo": "#fef2f2",
        "cor_borda": "#fca5a5",
        "rotulo": ">180 dias",
    }


def _formatar_data(data_valor):
    if not data_valor:
        return "-"
    try:
        texto = str(data_valor)[:10]
        ano, mes, dia = texto.split("-")
        return f"{dia}/{mes}/{ano}"
    except (ValueError, AttributeError):
        return str(data_valor)


def _carregar_historico_compras_completo():
    """Carrega os itens de compras com a data e NF da compra, em lotes."""
    tamanho_lote = 1000
    offset = 0
    linhas = []
    while True:
        response = (
            supabase.table("compras_itens")
            .select("id, compra_id, produto_id, quantidade, compras(numero_nf, data_emissao, criado_em)")
            .order("produto_id")
            .range(offset, offset + tamanho_lote - 1)
            .execute()
        )
        lote = response.data or []
        linhas.extend(lote)
        if len(lote) < tamanho_lote:
            break
        offset += tamanho_lote
    return linhas


def _carregar_movimentos_estoque_completo():
    """Carrega o ledger de estoque para reconstruir as camadas remanescentes."""
    tamanho_lote = 1000
    offset = 0
    linhas = []
    while True:
        response = (
            supabase.table("estoque_movimentos")
            .select("id, produto_id, tipo, quantidade, data_movimento, criado_em, compra_id, motivo")
            .order("id")
            .range(offset, offset + tamanho_lote - 1)
            .execute()
        )
        lote = response.data or []
        linhas.extend(lote)
        if len(lote) < tamanho_lote:
            break
        offset += tamanho_lote
    return linhas


def _data_evento(data_movimento, criado_em=None):
    """Transforma data e timestamp em chave ordenável, priorizando a data do movimento."""
    data = str(data_movimento or "")[:10]
    criado = str(criado_em or "")
    return (data, criado)


def _calcular_idade_produto(produto_id, saldo_atual, compras_por_produto, movimentos_por_produto):
    """Calcula a idade do estoque por camadas, usando FIFO.

    Cada compra cria uma camada na data da NF. Saídas e ajustes negativos
    consomem as camadas mais antigas. Ajustes/entradas positivas sem compra
    criam uma camada própria na data do movimento. A idade média é ponderada
    pela quantidade que permanece em cada camada.
    """
    eventos = []

    for compra in compras_por_produto.get(produto_id, []):
        compra_ref = compra.get("compras") or {}
        data_emissao = str(compra_ref.get("data_emissao") or "")[:10]
        criado_em = compra_ref.get("criado_em") or data_emissao
        try:
            quantidade = float(compra.get("quantidade") or 0)
        except (TypeError, ValueError):
            quantidade = 0.0
        if quantidade <= 0 or not data_emissao:
            continue
        eventos.append({
            "ordem": _data_evento(data_emissao, criado_em),
            "data": data_emissao,
            "quantidade": quantidade,
            "restante": quantidade,
            "tipo": "compra",
            "compra_id": compra.get("compra_id"),
            "numero_nf": compra_ref.get("numero_nf") or "-",
            "numero_item": compra.get("numero_item"),
        })

    # Movimentos ligados a uma compra já foram representados pelo item da
    # compra e, portanto, não devem criar uma segunda camada.
    for movimento in movimentos_por_produto.get(produto_id, []):
        if movimento.get("compra_id") is not None:
            continue
        tipo = str(movimento.get("tipo") or "").lower()
        try:
            quantidade = float(movimento.get("quantidade") or 0)
        except (TypeError, ValueError):
            quantidade = 0.0
        data_mov = str(movimento.get("data_movimento") or movimento.get("criado_em") or "")[:10]
        if not data_mov or quantidade == 0:
            continue
        if tipo == "entrada" or (tipo == "ajuste" and quantidade > 0):
            eventos.append({
                "ordem": _data_evento(data_mov, movimento.get("criado_em")),
                "data": data_mov,
                "quantidade": abs(quantidade),
                "restante": abs(quantidade),
                "tipo": "ajuste",
                "compra_id": None,
                "numero_nf": "Ajuste",
                "numero_item": None,
            })
        elif tipo == "saida" or (tipo == "ajuste" and quantidade < 0):
            eventos.append({
                "ordem": _data_evento(data_mov, movimento.get("criado_em")),
                "data": data_mov,
                "quantidade": -abs(quantidade),
                "restante": 0.0,
                "tipo": "saida",
                "compra_id": None,
                "numero_nf": "Saída",
                "numero_item": None,
            })

    eventos.sort(key=lambda e: e["ordem"])
    camadas = []
    for evento in eventos:
        if evento["quantidade"] > 0:
            camadas.append(evento)
            continue

        a_consumir = abs(evento["quantidade"])
        for camada in camadas:
            if a_consumir <= 0:
                break
            disponivel = float(camada.get("restante") or 0)
            if disponivel <= 0:
                continue
            consumo = min(disponivel, a_consumir)
            camada["restante"] = disponivel - consumo
            a_consumir -= consumo

    # O ledger é a fonte da verdade para o saldo. Se houver alguma diferença
    # histórica entre as camadas e o saldo atual, ajustamos o excedente pelas
    # camadas mais antigas para que a média represente o estoque exibido.
    saldo_positivo = max(float(saldo_atual or 0), 0.0)
    restante_calculado = sum(float(c.get("restante") or 0) for c in camadas)
    diferenca = restante_calculado - saldo_positivo
    if diferenca > 0.000001:
        for camada in camadas:
            if diferenca <= 0:
                break
            disponivel = float(camada.get("restante") or 0)
            consumo = min(disponivel, diferenca)
            camada["restante"] = disponivel - consumo
            diferenca -= consumo
    elif diferenca < -0.000001:
        # Saldo maior que as camadas históricas: trata o excedente como uma
        # camada sem origem de compra, com idade zero.
        camadas.append({
            "ordem": ("9999-12-31", "9999-12-31"),
            "data": date.today().isoformat(),
            "quantidade": abs(diferenca),
            "restante": abs(diferenca),
            "tipo": "ajuste",
            "compra_id": None,
            "numero_nf": "Saldo sem origem de compra",
            "numero_item": None,
        })

    hoje = date.today()
    peso_total = 0.0
    soma_idade = 0.0
    for camada in camadas:
        restante = float(camada.get("restante") or 0)
        if restante <= 0:
            continue
        try:
            data_camada = date.fromisoformat(str(camada["data"])[:10])
        except (ValueError, TypeError):
            continue
        idade = max((hoje - data_camada).days, 0)
        camada["idade_dias"] = idade
        peso_total += restante
        soma_idade += restante * idade

    idade_media = (soma_idade / peso_total) if peso_total > 0 else None

    # Mantém todas as compras no tooltip, inclusive as já consumidas, para que
    # o usuário consiga enxergar o histórico de idades.
    compras_tooltip = []
    for camada in camadas:
        if camada.get("tipo") != "compra":
            continue
        try:
            data_camada = date.fromisoformat(str(camada["data"])[:10])
            idade = max((hoje - data_camada).days, 0)
        except (ValueError, TypeError):
            idade = 0
        compras_tooltip.append({
            "data": camada.get("data"),
            "data_fmt": _formatar_data(camada.get("data")),
            "idade_dias": idade,
            "quantidade": float(camada.get("quantidade") or 0),
            "restante": float(camada.get("restante") or 0),
            "numero_nf": camada.get("numero_nf") or "-",
        })

    compras_tooltip.sort(key=lambda x: x["data"] or "", reverse=True)
    return idade_media, compras_tooltip


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

    if preco_original is not None and flag_ajuste:
        # O botão de retorno fica na área superior direita do diálogo,
        # alinhado com a informação do preço atual.
        col_atual, col_revert_btn = st.columns([2.2, 1.3], vertical_alignment="center")
        with col_atual:
            st.caption(f"Preço de venda atual: {_fmt_moeda(preco_atual)}")
        with col_revert_btn:
            if st.button(
                "↩️ Retornar ao Original",
                key=f"revert_preco_venda_{produto_id}",
                help="Restaura o preço de venda sugerido para o valor original e remove a marcação de editado.",
                use_container_width=True,
            ):
                valor_original = round(float(preco_original), 2)
                try:
                    supabase.table("estoque").update({
                        "estoque_preco_venda_sugerida": valor_original,
                        "estoque_flag_ajuste_preco_venda": False,
                    }).eq("produto_id", produto_id).execute()
                except Exception as e:
                    st.error(f"Erro ao reverter o preço de venda: {e}")
                    return
                st.success("Preço de venda revertido ao original.")
                st.rerun()

        st.info(f"Preço original antes do primeiro ajuste: {_fmt_moeda(preco_original)}")
    else:
        st.caption(f"Preço de venda atual: {_fmt_moeda(preco_atual)}")
        if preco_original is not None:
            st.caption(
                f"Preço original de referência: {_fmt_moeda(preco_original)} "
                "(atualmente igual ao original)"
            )

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
            novo_valor = round(float(preco_novo), 2)
            valor_atual = round(float(preco_atual or 0), 2)
            valor_original = (
                round(float(preco_original), 2)
                if preco_original is not None
                else None
            )

            try:
                if valor_original is not None:
                    # O item já teve um preço original gravado no passado.
                    # Se o novo valor for igual ao original, desmarca a flag de ajuste.
                    # Caso contrário, mantém a flag ativa.
                    flag_ajustado = (novo_valor != valor_original)
                    supabase.table("estoque").update({
                        "estoque_preco_venda_sugerida": novo_valor,
                        "estoque_flag_ajuste_preco_venda": flag_ajustado,
                    }).eq("produto_id", produto_id).execute()

                else:
                    # Primeiro ajuste do produto:
                    if novo_valor == valor_atual:
                        st.info("Nenhuma alteração de valor realizada.")
                        st.rerun()
                        return

                    supabase.table("estoque").update({
                        "estoque_preco_venda_sugerida": novo_valor,
                        "estoque_flag_ajuste_preco_venda": True,
                        "estoque_preco_venda_original": valor_atual,
                    }).eq("produto_id", produto_id).execute()

            except Exception as e:
                st.error(f"Erro ao salvar o novo preço de venda: {e}")
                return

            st.success("Preço de venda atualizado.")
            st.rerun()

def _carregar_estoque_completo():
    """Carrega todo o estoque em lotes para não ficar limitado ao máximo
    de linhas retornado pelo PostgREST/Supabase em uma única consulta."""
    tamanho_lote = 1000
    offset = 0
    linhas = []

    while True:
        response = (
            supabase.table("estoque")
            .select(
                "produto_id, quantidade_atual, estoque_preco_ultima_compra, "
                "estoque_preco_venda_sugerida, estoque_preco_venda_original, "
                "estoque_flag_ajuste_preco_venda, estoque_ultima_compra, "
                "produtos(id, codigo_interno, descricao)"
            )
            .order("produto_id")
            .range(offset, offset + tamanho_lote - 1)
            .execute()
        )

        lote = response.data or []
        linhas.extend(lote)

        if len(lote) < tamanho_lote:
            break

        offset += tamanho_lote

    return linhas


def _secao_estoque_atual():
    try:
        # Não usar .execute() sem paginação aqui: o Supabase/PostgREST pode
        # limitar uma resposta a 1000 registros. Isso fazia produtos com IDs
        # mais altos, como o produto 1173, desaparecerem da busca e dos
        # filtros mesmo existindo normalmente na tabela estoque.
        linhas_brutas = _carregar_estoque_completo()
    except Exception as e:
        st.error(f"Erro ao carregar estoque: {e}")
        return

    # Carrega o histórico uma única vez para calcular a idade de cada produto
    # sem fazer uma consulta por linha da tabela.
    try:
        historico_compras = _carregar_historico_compras_completo()
        movimentos_estoque = _carregar_movimentos_estoque_completo()
    except Exception as e:
        st.error(f"Erro ao carregar histórico de compras/movimentações: {e}")
        return

    compras_por_produto = {}
    for compra in historico_compras:
        compras_por_produto.setdefault(compra.get("produto_id"), []).append(compra)

    movimentos_por_produto = {}
    for movimento in movimentos_estoque:
        movimentos_por_produto.setdefault(movimento.get("produto_id"), []).append(movimento)

    # Achata a estrutura (produto embutido), calcula idade e ignora linhas órfãs.
    itens = []
    for linha in linhas_brutas:
        prod = linha.get("produtos")
        if not prod:
            continue
        produto_id = prod["id"]
        saldo = float(linha.get("quantidade_atual") or 0)
        idade_media, compras_idade = _calcular_idade_produto(
            produto_id,
            saldo,
            compras_por_produto,
            movimentos_por_produto,
        )
        itens.append({
            "id": produto_id,
            "codigo_interno": prod.get("codigo_interno") or "-",
            "descricao": prod.get("descricao") or "-",
            "saldo": saldo,
            "preco_compra": float(linha.get("estoque_preco_ultima_compra") or 0),
            "preco_venda": float(linha.get("estoque_preco_venda_sugerida") or 0),
            "preco_venda_original": linha.get("estoque_preco_venda_original"),
            "flag_ajuste_preco_venda": bool(linha.get("estoque_flag_ajuste_preco_venda", False)),
            "data_ultima_compra": linha.get("estoque_ultima_compra"),
            "idade_media": idade_media,
            "compras_idade": compras_idade,
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

    col_kpi_qtde, col_kpi_valor = st.columns(2)
    with col_kpi_qtde:
        _renderizar_kpi_grupo(
            "📦 Quantidade de Produtos",
            ("Peças em estoque", _fmt_qtd(qtde_pecas_estoque)),
            ("Produtos distintos", f"{qtde_distinta_pecas}"),
        )

    with col_kpi_valor:
        _renderizar_kpi_grupo(
            "💰 Valor de Estoque",
            ("Preço de compra", _fmt_moeda(valor_estoque_compra)),
            ("Preço de venda", _fmt_moeda(valor_estoque_venda)),
        )

    # Indicador-resumo da idade do estoque: quantidade de produtos em cada faixa.
    # A contagem considera os produtos distintos exibidos no estoque completo,
    # antes dos filtros da tabela.
    contagem_idade = {
        "idade-verde": 0,
        "idade-amarela": 0,
        "idade-laranja": 0,
        "idade-vermelha": 0,
    }
    for item_kpi in itens:
        classe_kpi = _classificar_idade(item_kpi.get("idade_media"))["classe"]
        contagem_idade[classe_kpi] = contagem_idade.get(classe_kpi, 0) + 1

    total_com_idade = sum(contagem_idade.values())
    def _pct_idade(qtd):
        return (qtd / total_com_idade * 100) if total_com_idade else 0

    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)
    st.markdown("**Idade do estoque**")

    # O KPI funciona como filtro da tabela. Clicar em uma faixa aplica o filtro;
    # clicar novamente na mesma faixa remove o filtro.
    filtro_idade_selecionado = st.session_state.get("estoque_filtro_idade")
    kpi_idade = st.columns(4)
    faixas_idade = [
        ("idade-verde", "🟢", "0-60 dias", "verde"),
        ("idade-amarela", "🟡", ">60-120 dias", "amarela"),
        ("idade-laranja", "🟠", ">120-180 dias", "laranja"),
        ("idade-vermelha", "🔴", ">180 dias", "vermelha"),
    ]

    for col_kpi, (classe_faixa, emoji, rotulo_faixa, sufixo) in zip(kpi_idade, faixas_idade):
        qtd_faixa = contagem_idade.get(classe_faixa, 0)
        selecionado = filtro_idade_selecionado == classe_faixa
        rotulo_kpi = (
            f"✓ {emoji} {rotulo_faixa}: {qtd_faixa} ({_pct_idade(qtd_faixa):.1f}%)"
            if selecionado
            else f"{emoji} {rotulo_faixa}: {qtd_faixa} ({_pct_idade(qtd_faixa):.1f}%)"
        )

        with col_kpi:
            if st.button(
                rotulo_kpi,
                key=f"filtro_idade_{sufixo}_kpi",
                help=(
                    f"Filtrar estoque por {rotulo_faixa}. "
                    "Clique novamente para remover o filtro."
                ),
                use_container_width=True,
            ):
                if filtro_idade_selecionado == classe_faixa:
                    st.session_state["estoque_filtro_idade"] = None
                else:
                    st.session_state["estoque_filtro_idade"] = classe_faixa
                reset_paginacao("estoque_atual")
                st.rerun()

    if filtro_idade_selecionado:
        rotulos_filtro = {
            "idade-verde": "0-60 dias",
            "idade-amarela": ">60-120 dias",
            "idade-laranja": ">120-180 dias",
            "idade-vermelha": ">180 dias",
        }
        st.caption(
            f"Filtro de idade ativo: **{rotulos_filtro.get(filtro_idade_selecionado, filtro_idade_selecionado)}**. "
            "Clique novamente no indicador para limpar."
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
            {"chave": "idade_media", "rotulo": "Idade Produto"},
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

    # Filtros de estoque e ajuste de preço:
    # - "Mostrar zerado/negativo": exibe SOMENTE itens com saldo <= 0 (zerados ou negativos).
    # - Padrão (ambos desligados): exibe somente itens com estoque positivo (🟢 OK).
    # - "Somente editados": restringe aos itens que tiveram preço ajustado manualmente.
    if mostrar_negativo:
        itens = [i for i in itens if i["saldo"] <= 0]
    elif not mostrar_somente_editados:
        itens = [i for i in itens if i["status"] == "🟢 OK"]

    if mostrar_somente_editados:
        itens = [i for i in itens if i.get("flag_ajuste_preco_venda")]

    # Filtro selecionado pelo KPI de idade.
    filtro_idade_selecionado = st.session_state.get("estoque_filtro_idade")
    if filtro_idade_selecionado:
        itens = [
            i for i in itens
            if _classificar_idade(i.get("idade_media"))["classe"] == filtro_idade_selecionado
        ]

    # Ordenação escolhida pelo usuário (crescente/decrescente pelo campo selecionado)
    if campo_ordenacao == "data_ultima_compra":
        # Itens sem data de compra sempre vão para o final, independente da direção
        def _chave_ordenacao(item):
            data = item["data_ultima_compra"]
            return (data is None, data or "")
    elif campo_ordenacao == "idade_media":
        def _chave_ordenacao(item):
            # Produtos sem idade ficam no final.
            idade = item.get("idade_media")
            return (idade is None, idade if idade is not None else 0)
    elif campo_ordenacao == "codigo_interno":
        def _chave_ordenacao(item):
            return item["codigo_interno"].lower()
    else:
        def _chave_ordenacao(item):
            return item[campo_ordenacao]

    itens.sort(key=_chave_ordenacao, reverse=ordem_decrescente)

    if not itens:
        if mostrar_negativo:
            st.info("Não há produtos com estoque zerado ou negativo.")
        else:
            st.info("Nenhum produto encontrado para o filtro selecionado.")
        return

    # Detecta mudança nos filtros/ordenação e volta para a primeira página.
    filtro_atual = (
        produto_id_selecionado,
        termo_busca_produto.strip().lower(),
        bool(buscar_descricao_produto),
        bool(mostrar_negativo),
        bool(mostrar_somente_editados),
        filtro_idade_selecionado,
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
        c_cod, c_desc, c_saldo, c_compra, c_venda, c_idade, c_data = st.columns(
            [1.4, 2.3, 1.3, 1.5, 1.5, 1.5, 1.6]
        )
        c_cod.markdown("**Cod. Produto**")
        c_desc.markdown("**Desc. Produto**")
        c_saldo.markdown("**Qtde. Estoque**")
        c_compra.markdown("**Preço Compra**")
        c_venda.markdown("**Preço Venda**")
        c_idade.markdown("**Idade Produto**")
        c_data.markdown("**Data Ult. Compra**")

        st.divider()

        for item in itens_pagina:
            classificacao = _classificar_idade(item.get("idade_media"))
            idade_media = item.get("idade_media")
            if idade_media is None:
                idade_rotulo = "-"
            else:
                idade_rotulo = f"{idade_media:.0f} dias"

            linhas_tooltip = []
            compras_idade = item.get("compras_idade") or []
            if len(compras_idade) > 1:
                linhas_tooltip.append("Histórico de compras:")
            elif len(compras_idade) == 1:
                linhas_tooltip.append("Compra:")

            for compra in compras_idade:
                linhas_tooltip.append(
                    f"NF {compra['numero_nf']} · {compra['data_fmt']} · "
                    f"idade {compra['idade_dias']} dias · "
                    f"qtde { _fmt_qtd(compra['quantidade']) } · "
                    f"restante { _fmt_qtd(compra['restante']) }"
                )

            if not linhas_tooltip:
                linhas_tooltip.append("Sem histórico de compra disponível.")

            tooltip_texto = "\n".join(linhas_tooltip)
            tooltip_idade = html.escape(tooltip_texto, quote=True)

            # Cada produto continua em uma linha normal. Somente o indicador
            # "Idade Produto" recebe a cor da faixa, evitando pintar a linha inteira.
            col_cod, col_desc, col_saldo, col_compra, col_venda, col_idade, col_data = st.columns(
                [1.4, 2.3, 1.3, 1.5, 1.5, 1.5, 1.6],
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

                # Indicador da idade média. O tooltip detalha a idade de cada
                # compra quando houver histórico múltiplo.
            indicador_idade = (
                f'<span class="idade-produto" title="{tooltip_idade}">'
                f'<span class="idade-dot {classificacao["classe"]}"></span>'
                f'{html.escape(idade_rotulo)}'
                f'</span>'
            )
            col_idade.markdown(indicador_idade, unsafe_allow_html=True)

            col_data.write(_formatar_data(item["data_ultima_compra"]))
            st.markdown(
                '<div style="height:1px;background:rgba(128,128,128,.16);margin:4px 0;"></div>',
                unsafe_allow_html=True,
            )

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
