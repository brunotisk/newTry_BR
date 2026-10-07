"""Tela de Relatórios.

Duas abas:
  - Reports - Geral: visões de Compras, Vendas e Estoque calculadas direto do banco.
  - Reports - BI: link para o painel externo de BI (a antiga tela de relatórios).
"""
import os
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from db import supabase, buscar_todos

load_dotenv()

_TTL = 300  # segundos de cache dos dados (botão "Atualizar dados" limpa antes)

MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

FAIXAS_IDADE = {
    "idade-verde": "0-60 dias",
    "idade-amarela": ">60-120 dias",
    "idade-laranja": ">120-180 dias",
    "idade-vermelha": ">180 dias",
}
CORES_IDADE = ["#4ade80", "#fde047", "#fb923c", "#f87171"]

# Cor padrão de cada "estilo" de relatório: todos os gráficos de barras de Compras
# usam COR_COMPRAS e todos os de Vendas usam COR_VENDAS. Para trocar o tema, basta
# alterar estes dois valores.
COR_COMPRAS = "#2563eb"  # azul
COR_VENDAS = "#16a34a"   # verde


# ----------------------------------------------------------------------
# Formatação e utilitários
# ----------------------------------------------------------------------
def _fmt_num(valor, casas=0) -> str:
    return (
        f"{float(valor or 0):,.{casas}f}"
        .replace(",", "X").replace(".", ",").replace("X", ".")
    )


def _fmt_moeda(valor) -> str:
    return f"R$ {_fmt_num(valor, 2)}"


def _fmt_qtd(valor) -> str:
    return f"{float(valor or 0):g}".replace(".", ",")


def _fmt_pct(valor) -> str:
    return f"{_fmt_num(valor, 1)}%"


def _num(valor) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_data(valor):
    try:
        return date.fromisoformat(str(valor or "")[:10])
    except ValueError:
        return None


def _no_periodo(dt, ini, fim) -> bool:
    if ini is None and fim is None:
        return True  # "Todo período": inclui até registros sem data
    if dt is None:
        return False
    if ini is not None and dt < ini:
        return False
    if fim is not None and dt > fim:
        return False
    return True


# ----------------------------------------------------------------------
# Carga de dados (cacheada)
# ----------------------------------------------------------------------
def _todos(tabela: str, colunas: str, ordem: str = "id"):
    return buscar_todos(lambda: supabase.table(tabela).select(colunas).order(ordem))


@st.cache_data(ttl=_TTL, show_spinner="Carregando compras...")
def _carregar_compras():
    return _todos(
        "compras",
        "id, numero_nf, data_emissao, valor_produtos, valor_desconto, valor_total, fornecedor_id",
    )


@st.cache_data(ttl=_TTL, show_spinner="Carregando itens de compra...")
def _carregar_compras_itens():
    return _todos("compras_itens", "id, produto_id, quantidade, valor_total, compras(data_emissao)")


@st.cache_data(ttl=_TTL, show_spinner=False)
def _carregar_fornecedores():
    return _todos("fornecedores", "id, razao_social, nome_fantasia")


@st.cache_data(ttl=_TTL, show_spinner=False)
def _carregar_produtos():
    return _todos("produtos", "id, codigo_interno, descricao, categoria_id")


@st.cache_data(ttl=_TTL, show_spinner=False)
def _carregar_categorias():
    return _todos("categorias_produtos", "id, categoria, banho")


@st.cache_data(ttl=_TTL, show_spinner="Carregando vendas...")
def _carregar_vendas():
    return _todos(
        "vendas",
        "id, quantidade, valor_final, comissao_valor, data_venda,"
        " produtos(id, codigo_interno, descricao, categoria_id),"
        " canais_venda(nome), status_venda(nome), formas_pagamento(descricao)",
    )


@st.cache_data(ttl=_TTL, show_spinner=False)
def _carregar_custos():
    """produto_id -> preço da última compra (usado como custo unitário estimado)."""
    linhas = _todos("estoque", "produto_id, estoque_preco_ultima_compra", ordem="produto_id")
    return {l["produto_id"]: _num(l.get("estoque_preco_ultima_compra")) for l in linhas}


def _rotulos_categoria() -> dict:
    return {
        c["id"]: f"{c['categoria']} | {c.get('banho') or '-'}"
        for c in _carregar_categorias()
    }


@st.cache_data(ttl=_TTL, show_spinner="Calculando estoque e idade dos produtos...")
def _carregar_estoque_visao():
    """Itens com saldo > 0, com idade média (mesmo cálculo FIFO da tela de Estoque)."""
    from telas.estoque_saldo import (
        _carregar_estoque_completo,
        _carregar_historico_compras_completo,
        _carregar_movimentos_estoque_completo,
        _calcular_idade_produto,
        _classificar_idade,
    )

    linhas = _carregar_estoque_completo()
    historico = _carregar_historico_compras_completo()
    movimentos = _carregar_movimentos_estoque_completo()

    compras_por_produto, movimentos_por_produto = {}, {}
    for c in historico:
        compras_por_produto.setdefault(c.get("produto_id"), []).append(c)
    for m in movimentos:
        movimentos_por_produto.setdefault(m.get("produto_id"), []).append(m)

    categoria_por_produto = {p["id"]: p.get("categoria_id") for p in _carregar_produtos()}
    rotulos = _rotulos_categoria()

    itens = []
    for linha in linhas:
        prod = linha.get("produtos")
        if not prod:
            continue
        saldo = _num(linha.get("quantidade_atual"))
        if saldo <= 0:
            continue
        pid = prod["id"]
        idade, _ = _calcular_idade_produto(pid, saldo, compras_por_produto, movimentos_por_produto)
        itens.append({
            "id": pid,
            "codigo": prod.get("codigo_interno") or "-",
            "produto": prod.get("descricao") or "-",
            "categoria": rotulos.get(categoria_por_produto.get(pid), "Sem categoria"),
            "saldo": saldo,
            "preco_compra": _num(linha.get("estoque_preco_ultima_compra")),
            "preco_venda": _num(linha.get("estoque_preco_venda_sugerida")),
            "idade": idade,
            "classe_idade": _classificar_idade(idade)["classe"],
        })
    return itens


# ----------------------------------------------------------------------
# Gráficos
# ----------------------------------------------------------------------
def _serie_mensal(df: pd.DataFrame, col_data: str, col_valor: str) -> pd.DataFrame:
    """Soma por mês, preenchendo meses sem movimento com zero."""
    d = df.dropna(subset=[col_data]).copy()
    if d.empty:
        return pd.DataFrame(columns=["rotulo", "valor", "texto"])
    d["periodo"] = pd.to_datetime(d[col_data]).dt.to_period("M")
    serie = d.groupby("periodo")[col_valor].sum()
    serie = serie.reindex(pd.period_range(serie.index.min(), serie.index.max(), freq="M"), fill_value=0.0)
    out = pd.DataFrame({
        "rotulo": [f"{MESES_ABREV[p.month - 1]}/{str(p.year)[2:]}" for p in serie.index],
        "valor": serie.values,
    })
    out["texto"] = out["valor"].map(_fmt_moeda)
    return out


def _chart_mensal(df: pd.DataFrame, titulo_valor: str, cor: str) -> alt.Chart:
    """Barras por mês com o valor exato (R$) em cima de cada barra, a 45°.

    O texto usa a própria cor da série, em negrito, com um contorno claro por
    trás (legível nos temas claro e escuro e sobre as barras vizinhas). Os
    rótulos inclinados são paralelos entre si, então não se sobrepõem entre si.
    """
    topo = max(float(df["valor"].max()) * 1.4, 1.0)  # folga no alto para os labels

    base = alt.Chart(df).encode(
        x=alt.X("rotulo:N", sort=df["rotulo"].tolist(), title=None, axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("valor:Q", title=None, axis=alt.Axis(format="~s"), scale=alt.Scale(domain=[0, topo])),
    )
    barras = base.mark_bar(color=cor).encode(
        tooltip=[alt.Tooltip("rotulo:N", title="Mês"), alt.Tooltip("texto:N", title=titulo_valor)],
    )
    estilo_texto = dict(
        angle=315, align="left", baseline="middle", dx=6, fontSize=14, fontWeight="bold",
    )
    # Contorno claro atrás do texto: a 45° o label de uma barra baixa pode passar por
    # cima da barra vizinha (mais alta); o contorno mantém o texto legível sobre ela.
    contorno = base.mark_text(
        color="white", stroke="white", strokeWidth=3, strokeJoin="round", **estilo_texto
    ).encode(text="texto:N")
    rotulos = base.mark_text(color=cor, **estilo_texto).encode(text="texto:N")
    # padding à direita: o label da última barra se estende para a direita.
    return (barras + contorno + rotulos).properties(
        height=340, padding={"left": 5, "top": 10, "right": 80, "bottom": 5}
    )


def _grafico_mensal(df: pd.DataFrame, titulo_valor: str, cor: str):
    if df.empty:
        st.caption("Sem dados com data no período.")
        return
    st.altair_chart(_chart_mensal(df, titulo_valor, cor))


def _top_agrupado(df: pd.DataFrame, col_grupo: str, col_valor: str, n: int = 10) -> pd.DataFrame:
    g = df.groupby(col_grupo)[col_valor].sum().sort_values(ascending=False).head(n)
    out = pd.DataFrame({"rotulo": g.index.astype(str), "valor": g.values})
    out["texto"] = out["valor"].map(_fmt_moeda)
    return out


def _grafico_horizontal(df: pd.DataFrame, titulo_valor: str, cor: str | None = None):
    if df.empty:
        st.caption("Sem dados no período.")
        return
    chart = alt.Chart(df).mark_bar(**({"color": cor} if cor else {})).encode(
        y=alt.Y("rotulo:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)),
        x=alt.X("valor:Q", title=None, axis=alt.Axis(format="~s")),
        tooltip=[alt.Tooltip("rotulo:N", title="Item"), alt.Tooltip("texto:N", title=titulo_valor)],
    ).properties(height=max(140, 30 * len(df) + 20))
    st.altair_chart(chart)


def _kpi(coluna, rotulo: str, valor: str, ajuda: str | None = None, detalhe: str | None = None):
    with coluna:
        with st.container(border=True):
            st.metric(rotulo, valor, delta=detalhe, delta_color="off", help=ajuda)


# ----------------------------------------------------------------------
# Visão: Compras
# ----------------------------------------------------------------------
def _visao_compras(ini, fim):
    try:
        fornecedores = {
            f["id"]: f.get("nome_fantasia") or f.get("razao_social") or "-"
            for f in _carregar_fornecedores()
        }
        produtos = {p["id"]: p for p in _carregar_produtos()}

        linhas = []
        for c in _carregar_compras():
            dt = _parse_data(c.get("data_emissao"))
            if not _no_periodo(dt, ini, fim):
                continue
            linhas.append({
                "data": dt,
                "fornecedor": fornecedores.get(c.get("fornecedor_id"), "Sem fornecedor"),
                "valor_produtos": _num(c.get("valor_produtos")),
                "desconto": _num(c.get("valor_desconto")),
                "valor_total": _num(c.get("valor_total")),
            })
        df = pd.DataFrame(linhas, columns=["data", "fornecedor", "valor_produtos", "desconto", "valor_total"])

        itens = []
        for it in _carregar_compras_itens():
            dt = _parse_data((it.get("compras") or {}).get("data_emissao"))
            if not _no_periodo(dt, ini, fim):
                continue
            prod = produtos.get(it.get("produto_id"), {})
            itens.append({
                "codigo": prod.get("codigo_interno") or "-",
                "produto": prod.get("descricao") or "-",
                "quantidade": _num(it.get("quantidade")),
                "valor_total": _num(it.get("valor_total")),
            })
        df_it = pd.DataFrame(itens, columns=["codigo", "produto", "quantidade", "valor_total"])
    except Exception as e:
        st.error(f"Erro ao carregar dados de compras: {e}")
        return

    if df.empty:
        st.info("Nenhuma compra no período selecionado.")
        return

    total_nf = len(df)
    valor_comprado = df["valor_total"].sum()
    descontos = df["desconto"].sum()
    bruto = df["valor_produtos"].sum()

    c1, c2, c3 = st.columns(3)
    _kpi(c1, "Notas fiscais", _fmt_num(total_nf))
    _kpi(c2, "Valor comprado", _fmt_moeda(valor_comprado))
    _kpi(c3, "Ticket médio por NF", _fmt_moeda(valor_comprado / total_nf))
    c4, c5, c6 = st.columns(3)
    _kpi(c4, "Peças compradas", _fmt_qtd(df_it["quantidade"].sum()))
    _kpi(
        c5, "Descontos obtidos", _fmt_moeda(descontos),
        detalhe=f"{_fmt_pct(descontos / bruto * 100 if bruto else 0)} do valor dos produtos",
    )
    _kpi(c6, "Fornecedores", _fmt_num(df["fornecedor"].nunique()))

    st.markdown("---")
    st.subheader("Valor comprado por mês")
    _grafico_mensal(
        _serie_mensal(df, "data", "valor_total"), "Valor comprado", COR_COMPRAS
    )

    st.subheader("Produtos mais comprados")
    if df_it.empty:
        st.caption("Sem itens de compra no período.")
        return
    g = (
        df_it.groupby(["codigo", "produto"], as_index=False)
        .agg(pecas=("quantidade", "sum"), valor=("valor_total", "sum"))
        .sort_values("pecas", ascending=False)
        .head(10)
    )
    st.dataframe(
        pd.DataFrame({
            "Cód. Produto": g["codigo"],
            "Produto": g["produto"],
            "Peças": g["pecas"].map(_fmt_qtd),
            "Valor": g["valor"].map(_fmt_moeda),
            "Preço médio": [_fmt_moeda(v / p) if p else "-" for v, p in zip(g["valor"], g["pecas"])],
        }),
        hide_index=True,
        use_container_width=True,
    )


# ----------------------------------------------------------------------
# Visão: Vendas
# ----------------------------------------------------------------------
def _visao_vendas(ini, fim):
    try:
        categorias = _rotulos_categoria()
        custos = _carregar_custos()

        linhas = []
        for v in _carregar_vendas():
            dt = _parse_data(v.get("data_venda"))
            if not _no_periodo(dt, ini, fim):
                continue
            prod = v.get("produtos") or {}
            qtd = _num(v.get("quantidade"))
            custo_unit = custos.get(prod.get("id"), 0.0)
            linhas.append({
                "data": dt,
                "quantidade": qtd,
                "valor_final": _num(v.get("valor_final")),
                "comissao": _num(v.get("comissao_valor")),
                "canal": (v.get("canais_venda") or {}).get("nome") or "Sem canal",
                "forma": (v.get("formas_pagamento") or {}).get("descricao") or "Não informado",
                "codigo": prod.get("codigo_interno") or "-",
                "produto": prod.get("descricao") or "-",
                "categoria": categorias.get(prod.get("categoria_id"), "Sem categoria"),
                "custo": custo_unit * qtd,
                "tem_custo": custo_unit > 0,
            })
        df = pd.DataFrame(linhas, columns=[
            "data", "quantidade", "valor_final", "comissao",
            "canal", "forma", "codigo", "produto", "categoria", "custo", "tem_custo",
        ])
    except Exception as e:
        st.error(f"Erro ao carregar dados de vendas: {e}")
        return

    if df.empty:
        st.info("Nenhuma venda no período selecionado.")
        return

    total_vendas = len(df)
    faturamento = df["valor_final"].sum()

    com_custo = df[df["tem_custo"]]
    fat_com_custo = com_custo["valor_final"].sum()
    margem = fat_com_custo - com_custo["custo"].sum()
    margem_pct = margem / fat_com_custo * 100 if fat_com_custo else 0
    cobertura = fat_com_custo / faturamento * 100 if faturamento else 0

    c1, c2, c3 = st.columns(3)
    _kpi(c1, "Faturamento", _fmt_moeda(faturamento))
    _kpi(c2, "Vendas", _fmt_num(total_vendas))
    _kpi(c3, "Ticket médio", _fmt_moeda(faturamento / total_vendas))
    c4, c5, c6 = st.columns(3)
    _kpi(c4, "Peças vendidas", _fmt_qtd(df["quantidade"].sum()))
    _kpi(
        c5, "Margem bruta estimada", _fmt_moeda(margem),
        ajuda=(
            "Faturamento menos o custo estimado (quantidade vendida × preço da última "
            "compra do produto). Só considera vendas de produtos com preço de compra "
            f"conhecido ({_fmt_pct(cobertura)} do faturamento do período)."
        ),
        detalhe=f"{_fmt_pct(margem_pct)} sobre o faturamento com custo",
    )
    _kpi(c6, "Comissões", _fmt_moeda(df["comissao"].sum()))

    st.markdown("---")
    st.subheader("Faturamento por mês")
    _grafico_mensal(_serie_mensal(df, "data", "valor_final"), "Faturamento", COR_VENDAS)

    col_c, col_d, col_e = st.columns(3)
    with col_c:
        st.subheader("Por canal de venda")
        _grafico_horizontal(_top_agrupado(df, "canal", "valor_final", 8), "Faturamento", COR_VENDAS)
    with col_d:
        st.subheader("Por categoria")
        _grafico_horizontal(_top_agrupado(df, "categoria", "valor_final", 10), "Faturamento", COR_VENDAS)
    with col_e:
        st.subheader("Por forma de pagamento")
        _grafico_horizontal(_top_agrupado(df, "forma", "valor_final", 8), "Faturamento", COR_VENDAS)

    st.subheader("Produtos que mais faturam")
    g = (
        df.groupby(["codigo", "produto"], as_index=False)
        .agg(pecas=("quantidade", "sum"), fat=("valor_final", "sum"))
        .sort_values("fat", ascending=False)
        .head(10)
    )
    st.dataframe(
        pd.DataFrame({
            "Cód. Produto": g["codigo"],
            "Produto": g["produto"],
            "Peças": g["pecas"].map(_fmt_qtd),
            "Faturamento": g["fat"].map(_fmt_moeda),
        }),
        hide_index=True,
        use_container_width=True,
    )


# ----------------------------------------------------------------------
# Visão: Estoque
# ----------------------------------------------------------------------
def _visao_estoque():
    st.caption("O estoque é uma foto de hoje (somente produtos com saldo maior que zero); o filtro de período não se aplica.")
    try:
        itens = _carregar_estoque_visao()
    except ImportError as e:
        st.error(
            "Não consegui importar os cálculos da tela de Estoque (`telas/estoque_saldo.py`): "
            f"{e}. Ajuste o caminho do import em `_carregar_estoque_visao`."
        )
        return
    except Exception as e:
        st.error(f"Erro ao carregar estoque: {e}")
        return

    if not itens:
        st.info("Nenhum produto com estoque maior que zero.")
        return

    pecas = sum(i["saldo"] for i in itens)
    custo_total = sum(i["saldo"] * i["preco_compra"] for i in itens)
    venda_total = sum(i["saldo"] * i["preco_venda"] for i in itens)
    margem = venda_total - custo_total
    margem_pct = margem / venda_total * 100 if venda_total else 0
    parado = sum(i["saldo"] * i["preco_compra"] for i in itens if i["classe_idade"] == "idade-vermelha")
    parado_pct = parado / custo_total * 100 if custo_total else 0

    c1, c2, c3 = st.columns(3)
    _kpi(c1, "Peças em estoque", _fmt_qtd(pecas))
    _kpi(c2, "Produtos distintos", _fmt_num(len(itens)))
    _kpi(c3, "Valor a custo (última compra)", _fmt_moeda(custo_total))
    c4, c5, c6 = st.columns(3)
    _kpi(c4, "Valor a venda sugerida", _fmt_moeda(venda_total))
    _kpi(c5, "Margem potencial", _fmt_moeda(margem), detalhe=f"{_fmt_pct(margem_pct)} sobre a venda sugerida")
    _kpi(
        c6, "Capital parado (>180 dias)", _fmt_moeda(parado),
        ajuda="Valor a custo dos produtos cuja idade média do estoque passa de 180 dias.",
        detalhe=f"{_fmt_pct(parado_pct)} do valor a custo",
    )

    sem_custo = sum(1 for i in itens if i["preco_compra"] <= 0)
    if sem_custo:
        st.caption(f"⚠️ {sem_custo} produto(s) em estoque estão sem preço de última compra e entram com custo zero.")

    st.markdown("---")
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Valor a custo por idade")
        linhas = []
        for classe, rotulo in FAIXAS_IDADE.items():
            grupo = [i for i in itens if i["classe_idade"] == classe]
            valor = sum(i["saldo"] * i["preco_compra"] for i in grupo)
            linhas.append({
                "faixa": rotulo,
                "valor": valor,
                "texto": _fmt_moeda(valor),
                "detalhe": f"{len(grupo)} produtos · {_fmt_qtd(sum(i['saldo'] for i in grupo))} peças",
            })
        df_idade = pd.DataFrame(linhas)
        chart = alt.Chart(df_idade).mark_bar().encode(
            x=alt.X("faixa:N", sort=list(FAIXAS_IDADE.values()), title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("valor:Q", title=None, axis=alt.Axis(format="~s")),
            color=alt.Color(
                "faixa:N",
                scale=alt.Scale(domain=list(FAIXAS_IDADE.values()), range=CORES_IDADE),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("faixa:N", title="Idade"),
                alt.Tooltip("texto:N", title="Valor a custo"),
                alt.Tooltip("detalhe:N", title="Itens"),
            ],
        ).properties(height=280)
        st.altair_chart(chart)

    with col_b:
        st.subheader("Valor a custo por categoria")
        df_itens = pd.DataFrame(itens)
        df_itens["valor_custo"] = df_itens["saldo"] * df_itens["preco_compra"]
        _grafico_horizontal(_top_agrupado(df_itens, "categoria", "valor_custo", 10), "Valor a custo")

    st.subheader("Maiores valores parados (>180 dias)")
    parados = sorted(
        (i for i in itens if i["classe_idade"] == "idade-vermelha"),
        key=lambda i: i["saldo"] * i["preco_compra"],
        reverse=True,
    )[:10]
    if not parados:
        st.caption("Nenhum produto com mais de 180 dias em estoque. 🎉")
        return
    st.dataframe(
        pd.DataFrame({
            "Cód. Produto": [i["codigo"] for i in parados],
            "Produto": [i["produto"] for i in parados],
            "Qtde": [_fmt_qtd(i["saldo"]) for i in parados],
            "Idade": [f"{i['idade']:.0f} dias" if i["idade"] is not None else "-" for i in parados],
            "Valor a custo": [_fmt_moeda(i["saldo"] * i["preco_compra"]) for i in parados],
        }),
        hide_index=True,
        use_container_width=True,
    )


# ----------------------------------------------------------------------
# Abas
# ----------------------------------------------------------------------
def _filtro_periodo():
    """Filtro de período (Compras e Vendas). Retorna (ini, fim) ou None se inválido."""
    hoje = date.today()
    opcoes = ["Todo período", "Ano atual", "Últimos 12 meses", "Últimos 90 dias", "Mês atual", "Personalizado"]

    col_p, col_a, col_b, col_btn = st.columns([2, 1.5, 1.5, 1.4], vertical_alignment="bottom")
    with col_p:
        escolha = st.selectbox("Período (compras e vendas)", opcoes, key="rep_periodo")

    ini, fim = None, hoje
    if escolha == "Todo período":
        fim = None
    elif escolha == "Ano atual":
        ini = date(hoje.year, 1, 1)
    elif escolha == "Últimos 12 meses":
        ini = hoje - timedelta(days=365)
    elif escolha == "Últimos 90 dias":
        ini = hoje - timedelta(days=90)
    elif escolha == "Mês atual":
        ini = date(hoje.year, hoje.month, 1)
    else:
        with col_a:
            ini = st.date_input("De", value=date(hoje.year, hoje.month, 1), key="rep_data_ini", format="DD/MM/YYYY")
        with col_b:
            fim = st.date_input("Até", value=hoje, key="rep_data_fim", format="DD/MM/YYYY")

    with col_btn:
        if st.button("🔄 Atualizar dados", use_container_width=True, key="rep_btn_atualizar"):
            st.cache_data.clear()
            st.rerun()

    if ini is not None and fim is not None and ini > fim:
        st.error("A data inicial não pode ser depois da data final.")
        return None
    return ini, fim


def _aba_geral():
    periodo = _filtro_periodo()
    if periodo is None:
        return
    ini, fim = periodo

    # Navegação por rádio (e não st.tabs) para só consultar o banco da visão aberta.
    visao = st.radio(
        "Visão",
        ["🛍️ Compras", "💰 Vendas", "📦 Estoque"],
        horizontal=True,
        key="rep_visao",
        label_visibility="collapsed",
    )

    if visao == "🛍️ Compras":
        _visao_compras(ini, fim)
    elif visao == "💰 Vendas":
        _visao_vendas(ini, fim)
    else:
        _visao_estoque()


def _aba_bi():
    url_report = os.getenv("URL_REPORT")

    if not url_report:
        st.error(
            "⚠️ A URL do painel de relatórios não foi configurada no arquivo `.env`."
        )
        return

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


def tela_report():
    st.header("📊 Relatórios e Analíticos")

    aba_geral, aba_bi = st.tabs(["Reports - Geral", "Reports - BI"])

    with aba_geral:
        _aba_geral()

    with aba_bi:
        _aba_bi()
