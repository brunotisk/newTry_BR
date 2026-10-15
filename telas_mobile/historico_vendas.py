"""Tela mobile: histórico de vendas (somente consulta).

Filtros por período, canal e cliente, resumo do que foi filtrado e a lista de itens
vendidos, do mais recente ao mais antigo, com "Mostrar mais".
"""
from datetime import date, timedelta

import streamlit as st

from db import supabase, buscar_todos
from telas_mobile.comum import cadastros, fmt_data, fmt_moeda

LIMITE_PAGINA = 20
TODOS_CANAIS = "Todos os canais"
PERIODOS = ["Hoje", "7 dias", "Este mês", "Este ano", "Tudo", "Escolher datas"]

COLUNAS_LISTA = (
    "id, data_venda, cliente, quantidade, valor_lista, valor_desconto, valor_final, "
    "produtos(codigo_interno, descricao), canais_venda(nome), "
    "status_venda(nome), formas_pagamento(descricao)"
)


def _intervalo(periodo: str):
    """(inicio, fim) do período; None em um dos lados = sem limite."""
    hoje = date.today()
    if periodo == "Hoje":
        return hoje, hoje
    if periodo == "7 dias":
        return hoje - timedelta(days=6), hoje
    if periodo == "Este mês":
        return hoje.replace(day=1), hoje
    if periodo == "Este ano":
        return hoje.replace(month=1, day=1), hoje
    if periodo == "Escolher datas":
        escolhido = st.date_input(
            "Datas",
            value=(hoje - timedelta(days=6), hoje),
            max_value=hoje,
            format="DD/MM/YYYY",
            key="mh_datas",
        )
        if isinstance(escolhido, (tuple, list)):
            if len(escolhido) == 2:
                return escolhido[0], escolhido[1]
            if len(escolhido) == 1:  # usuário escolheu só o primeiro dia do intervalo
                return escolhido[0], escolhido[0]
            return None, None
        return escolhido, escolhido
    return None, None


def _aplicar_filtros(query, inicio, fim, canal_id, busca):
    if inicio:
        query = query.gte("data_venda", inicio)
    if fim:
        query = query.lte("data_venda", fim)
    if canal_id is not None:
        query = query.eq("canal_venda_id", canal_id)
    if busca:
        like = busca.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.ilike("cliente", f"%{like}%")
    return query


@st.cache_data(ttl=30, show_spinner=False)
def _totais(inicio, fim, canal_id, busca) -> dict:
    """Soma de TODAS as vendas dos filtros (não só da página exibida)."""
    linhas = buscar_todos(
        lambda: _aplicar_filtros(
            supabase.table("vendas").select("valor_final, comissao_valor"),
            inicio, fim, canal_id, busca,
        ).order("id")
    )
    return {
        "itens": len(linhas),
        "valor": sum(float(v.get("valor_final") or 0) for v in linhas),
        "comissao": sum(float(v.get("comissao_valor") or 0) for v in linhas),
    }


def _card_venda(v: dict) -> None:
    produto = v.get("produtos") or {}
    partes = [
        (v.get("canais_venda") or {}).get("nome"),
        (v.get("formas_pagamento") or {}).get("descricao"),
        (v.get("status_venda") or {}).get("nome"),
    ]
    with st.container(border=True):
        col_cli, col_valor = st.columns([3, 2], vertical_alignment="center")
        col_cli.markdown(f"**{v.get('cliente') or 'Sem cliente'}**")
        col_valor.markdown(f"**{fmt_moeda(v.get('valor_final'))}**")
        st.caption(
            f"{float(v.get('quantidade') or 0):g}× {produto.get('descricao') or '-'} "
            f"({produto.get('codigo_interno') or '-'})"
        )
        linha = " · ".join(p for p in partes if p)
        desconto = float(v.get("valor_desconto") or 0)
        if desconto:
            linha += f"{' · ' if linha else ''}desc. {fmt_moeda(desconto)}"
        if linha:
            st.caption(linha)


def tela_historico_vendas_mobile() -> None:
    ss = st.session_state
    try:
        cad = cadastros()
    except Exception as err:
        st.error(f"Não foi possível carregar os cadastros: {err}")
        return
    canais = {c["nome"]: c["id"] for c in cad["canais"]}

    ss.setdefault("mh_periodo", "Este mês")  # padrão definido pelo estado (sem index=, que conflita com o estado salvo)
    col_periodo, col_canal = st.columns(2)
    periodo = col_periodo.selectbox("Período", PERIODOS, key="mh_periodo")
    canal_nome = col_canal.selectbox("Canal", [TODOS_CANAIS] + list(canais), key="mh_canal")
    inicio, fim = _intervalo(periodo)
    busca = st.text_input(
        "Cliente", placeholder="🔎 Buscar por cliente…", label_visibility="collapsed", key="mh_busca"
    ).strip()

    inicio_iso = inicio.isoformat() if inicio else None
    fim_iso = fim.isoformat() if fim else None
    canal_id = canais.get(canal_nome)

    chave = f"{periodo}|{inicio_iso}|{fim_iso}|{canal_id}|{busca.casefold()}"
    if ss.get("mh_chave") != chave:
        ss["mh_chave"] = chave
        ss["mh_limite"] = LIMITE_PAGINA
    limite = ss.setdefault("mh_limite", LIMITE_PAGINA)

    try:
        totais = _totais(inicio_iso, fim_iso, canal_id, busca)
        resp = (
            _aplicar_filtros(
                supabase.table("vendas").select(COLUNAS_LISTA, count="exact"),
                inicio_iso, fim_iso, canal_id, busca,
            )
            .order("data_venda", desc=True)
            .order("id", desc=True)
            .range(0, limite - 1)
            .execute()
        )
    except Exception as err:
        st.error(f"Erro ao consultar as vendas: {err}")
        return

    col_itens, col_total = st.columns(2)
    col_itens.metric("Itens vendidos", totais["itens"])
    col_total.metric("Total vendido", fmt_moeda(totais["valor"]))
    if totais["comissao"]:
        st.caption(f"Comissão no período: {fmt_moeda(totais['comissao'])}")

    vendas = resp.data or []
    total = resp.count if resp.count is not None else len(vendas)
    if not vendas:
        st.info("Nenhuma venda encontrada para os filtros escolhidos.")
        return

    dia_anterior = None
    for v in vendas:
        dia = v.get("data_venda")
        if dia != dia_anterior:
            st.markdown(f"##### 📅 {fmt_data(dia)}")
            dia_anterior = dia
        _card_venda(v)

    if total > limite:
        if st.button(f"Mostrar mais ({total - limite} restantes)", use_container_width=True, key="mh_mais"):
            ss["mh_limite"] = limite + LIMITE_PAGINA
            st.rerun()
    else:
        st.caption(f"{total} item(ns) exibidos.")
