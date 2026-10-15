"""Utilidades, leituras em cache e estado compartilhados pelas telas mobile."""
from datetime import datetime

import streamlit as st

from db import supabase, buscar_todos

# Chaves de widgets que precisam sobreviver à troca de seção (o Streamlit descarta o
# estado de widgets que não foram desenhados na execução).
CHAVES_PERSISTENTES = [
    "m_canal", "m_feira", "m_forma", "m_status", "m_data",
    "m_cli_novo", "m_cli_id", "m_cli_nome", "m_cli_tel",
    "m_cli_busca", "m_cli_filtro_canal",
    "mh_periodo", "mh_canal", "mh_busca",
]



# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def fmt_moeda(valor) -> str:
    try:
        v = float(valor or 0)
    except (TypeError, ValueError):
        v = 0.0
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_data(valor):
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def fmt_data(valor) -> str:
    d = parse_data(valor)
    return d.strftime("%d/%m/%Y") if d else "-"


def digitos(texto) -> str:
    return "".join(ch for ch in str(texto or "") if ch.isdigit())


def format_phone(valor) -> str:
    d = digitos(valor)[:11]
    if len(d) <= 2:
        return f"({d}" if d else ""
    if len(d) <= 7:
        return f"({d[:2]}) {d[2:]}"
    return f"({d[:2]}) {d[2:7]}-{d[7:]}"


def escolha(rotulo: str, opcoes: list, key: str):
    """Botões de seleção única (st.pills) com radio horizontal como alternativa."""
    pills = getattr(st, "pills", None)
    if pills is not None:
        return pills(rotulo, opcoes, selection_mode="single", key=key)
    return st.radio(rotulo, opcoes, index=None, horizontal=True, key=key)



# ----------------------------------------------------------------------
# Leituras em cache
# ----------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def cadastros() -> dict:
    """Canais, status, formas de pagamento e feiras ativos (exceções não ficam em cache)."""
    def _lista(tabela, colunas, ordem_col):
        return (
            supabase.table(tabela)
            .select(colunas)
            .eq("ativo", True)
            .order("ordem_exibicao", nullsfirst=False)
            .order(ordem_col)
            .execute()
            .data
            or []
        )

    return {
        "canais": _lista("canais_venda", "id, nome", "nome"),
        "status": _lista("status_venda", "id, nome", "nome"),
        "formas": _lista("formas_pagamento", "id, descricao", "descricao"),
        "feiras": _lista("detalhes_feira", "id, nome_feira", "nome_feira"),
    }


@st.cache_data(ttl=30, show_spinner=False)
def produtos_em_estoque() -> list:
    linhas = buscar_todos(
        lambda: supabase.table("estoque")
        .select(
            "quantidade_atual, estoque_preco_venda_sugerida, "
            "produtos(id, codigo_interno, descricao, foto_url)"
        )
        .gt("quantidade_atual", 0)
        .order("produto_id")
    )
    produtos = []
    for linha in linhas:
        p = linha.get("produtos")
        if p:
            produtos.append({
                **p,
                "saldo": float(linha.get("quantidade_atual") or 0),
                "preco": float(linha.get("estoque_preco_venda_sugerida") or 0),
            })
    return produtos


@st.cache_data(ttl=60, show_spinner=False)
def lista_clientes() -> list:
    return buscar_todos(
        lambda: supabase.table("clientes")
        .select("id, nome, telefone, canal_id")
        .order("nome")
        .order("id")
    )


def limpar_caches_venda() -> None:
    produtos_em_estoque.clear()
    lista_clientes.clear()


# ----------------------------------------------------------------------
# Estado
# ----------------------------------------------------------------------
def processar_resets() -> None:
    """Executado ANTES de desenhar os widgets: limpa o cliente após uma venda salva."""
    ss = st.session_state
    if ss.pop("m_reset_cliente", False):
        ss["m_cli_novo"] = False
        ss["m_cli_id"] = None
        ss["m_cli_nome"] = ""
        ss["m_cli_tel"] = ""


def manter_estado() -> None:
    ss = st.session_state
    for chave in CHAVES_PERSISTENTES:
        if chave in ss:
            ss[chave] = ss[chave]
