"""Retiradas pessoais: baixa de estoque para uso próprio.

Não é venda: não grava em `vendas`, então não entra em faturamento, ticket, comissão nem
margem. Cada retirada é uma linha de `estoque_movimentos` com tipo = 'retirada_pessoal',
quantidade negativa e o custo unitário (preço da última compra) do momento. Toda gravação
passa por `servicos/estoque.py`.
"""
import re
import uuid
from datetime import date, timedelta

import streamlit as st

from db import supabase, buscar_todos
from servicos.supabase_admin import get_client
from servicos.estoque import (
    PREFIXO_ESTORNO_RETIRADA,
    TIPO_RETIRADA_PESSOAL,
    SaldoInsuficienteError,
    estornar_retirada_pessoal,
    obter_custo_unitario,
    obter_saldo,
    registrar_retirada_pessoal,
)
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao

_dialog = getattr(st, "dialog", None) or st.experimental_dialog

CHAVE_CARRINHO = "retirada_carrinho"
CHAVE_AVISO_ITEM = "retirada_aviso_item"
CHAVE_MSG_OK = "retirada_msg_ok"


# ----------------------------------------------------------------------
# Formatação
# ----------------------------------------------------------------------
def _fmt_qtd(valor) -> str:
    return f"{float(valor or 0):g}".replace(".", ",")


def _fmt_moeda(valor) -> str:
    return "R$ " + f"{float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data_br(valor) -> str:
    texto = str(valor or "")[:10]
    try:
        return date.fromisoformat(texto).strftime("%d/%m/%Y")
    except ValueError:
        return "-"


# ----------------------------------------------------------------------
# Dados
# ----------------------------------------------------------------------
def _periodo(opcao: str, de: date, ate: date):
    hoje = date.today()
    if opcao == "Mês atual":
        return date(hoje.year, hoje.month, 1), hoje
    if opcao == "Últimos 30 dias":
        return hoje - timedelta(days=30), hoje
    if opcao == "Ano atual":
        return date(hoje.year, 1, 1), hoje
    if opcao == "Personalizado":
        return de, ate
    return None, None  # Todo período


def _carregar_retiradas(ini, fim) -> list:
    """Retiradas no período, com o produto e a marca de 'estornada' de cada uma."""
    def consulta():
        q = (
            supabase.table("estoque_movimentos")
            .select("id, produto_id, quantidade, data_movimento, motivo, realizado_por, custo_unitario")
            .eq("tipo", TIPO_RETIRADA_PESSOAL)
            .order("data_movimento", desc=True)
            .order("id", desc=True)
        )
        if ini:
            q = q.gte("data_movimento", ini.isoformat())
        if fim:
            q = q.lte("data_movimento", fim.isoformat())
        return q

    movimentos = list(buscar_todos(consulta))

    estornos = buscar_todos(
        lambda: supabase.table("estoque_movimentos")
        .select("id, motivo")
        .eq("tipo", "ajuste")
        .like("motivo", f"{PREFIXO_ESTORNO_RETIRADA}%")
        .order("id")
    )
    estornadas = set()
    for e in estornos:
        achado = re.search(r"#(\d+)\s*$", str(e.get("motivo") or ""))
        if achado:
            estornadas.add(int(achado.group(1)))

    ids = sorted({m["produto_id"] for m in movimentos if m.get("produto_id") is not None})
    produtos = {}
    for i in range(0, len(ids), 200):
        resp = (
            supabase.table("produtos")
            .select("id, codigo_interno, descricao, foto_url")
            .in_("id", ids[i:i + 200])
            .execute()
        )
        produtos.update({p["id"]: p for p in (resp.data or [])})

    for m in movimentos:
        m["_produto"] = produtos.get(m.get("produto_id"), {})
        m["_estornada"] = m["id"] in estornadas
        m["_qtd"] = abs(float(m.get("quantidade") or 0))
    return movimentos


def _buscar_produto(codigo: str):
    resp = (
        supabase.table("produtos")
        .select("id, codigo_interno, descricao, foto_url")
        .eq("codigo_interno", codigo.strip())
        .execute()
    )
    return resp.data[0] if resp.data else None


# ----------------------------------------------------------------------
# Nova retirada (várias peças de uma vez)
# ----------------------------------------------------------------------
def _adicionar_ao_carrinho(codigo: str, qtd: int) -> None:
    """Valida o produto e o saldo e coloca o item no carrinho (soma se já estiver lá)."""
    st.session_state.pop(CHAVE_AVISO_ITEM, None)
    codigo = (codigo or "").strip()
    if not codigo:
        st.session_state[CHAVE_AVISO_ITEM] = ("warning", "Informe o código interno do produto.")
        return
    try:
        produto = _buscar_produto(codigo)
        if not produto:
            st.session_state[CHAVE_AVISO_ITEM] = ("warning", f"Produto não encontrado: {codigo}.")
            return
        saldo = obter_saldo(supabase, produto["id"])
        carrinho = st.session_state[CHAVE_CARRINHO]
        existente = next((i for i in carrinho if i["produto_id"] == produto["id"]), None)
        total = int(qtd) + (existente["qtd"] if existente else 0)
        if total > saldo:
            st.session_state[CHAVE_AVISO_ITEM] = (
                "warning",
                f"{produto['codigo_interno']}: saldo disponível {_fmt_qtd(saldo)}, "
                f"e a retirada pediria {_fmt_qtd(total)}.",
            )
            return
        if existente:
            existente["qtd"] = total
            # o campo "Qtd" da linha guarda o valor antigo em session_state; atualiza junto
            st.session_state[f"retirada_qtd_{produto['id']}"] = total
        else:
            carrinho.append({
                "produto_id": produto["id"],
                "codigo": produto["codigo_interno"],
                "descricao": produto.get("descricao") or "-",
                "foto_url": produto.get("foto_url"),
                "qtd": int(qtd),
                "saldo": saldo,
                "custo": obter_custo_unitario(supabase, produto["id"]),
            })
        st.session_state[CHAVE_AVISO_ITEM] = (
            "success", f"{produto['codigo_interno']} — {produto.get('descricao') or '-'} adicionado."
        )
    except Exception as e:
        st.session_state[CHAVE_AVISO_ITEM] = ("error", f"Erro ao buscar o produto: {e}")


@_dialog("📤 Nova retirada pessoal", width="large")
def _dialog_nova_retirada():
    carrinho = st.session_state.setdefault(CHAVE_CARRINHO, [])

    st.caption(
        "Informe o código e a quantidade e tecle Enter (ou clique em Adicionar) para cada peça. "
        "Depois confira a lista e registre tudo de uma vez."
    )
    with st.form("retirada_form_item", clear_on_submit=True):
        c_cod, c_qtd, c_btn = st.columns([3, 1.3, 1.5], vertical_alignment="bottom")
        codigo = c_cod.text_input("Código interno do produto", placeholder="Ex.: 11389")
        qtd = c_qtd.number_input("Qtd", min_value=1, value=1, step=1)
        adicionar = c_btn.form_submit_button("➕ Adicionar", use_container_width=True)
    if adicionar:
        _adicionar_ao_carrinho(codigo, int(qtd))

    aviso = st.session_state.get(CHAVE_AVISO_ITEM)
    if aviso:
        getattr(st, aviso[0])(aviso[1])

    if not carrinho:
        st.info("Nenhuma peça na retirada ainda.")
        return

    colunas = [0.9, 1.3, 3.0, 1.1, 1.0, 1.3, 0.7]
    with st.container(border=True):
        for col, titulo in zip(
            st.columns(colunas, vertical_alignment="center"),
            ["Foto", "Código", "Produto", "Qtd", "Saldo", "Custo unit.", ""],
        ):
            col.markdown(f"**{titulo}**")

        remover = None
        for item in carrinho:
            c_foto, c_cod, c_prod, c_qtd, c_saldo, c_custo, c_rm = st.columns(
                colunas, vertical_alignment="center"
            )
            with c_foto:
                if item.get("foto_url"):
                    st.image(item["foto_url"], width=48)
                else:
                    st.caption("sem foto")
            c_cod.write(item["codigo"])
            c_prod.write(item["descricao"])
            chave_qtd = f"retirada_qtd_{item['produto_id']}"
            if chave_qtd not in st.session_state:  # o valor vive no session_state (sem `value=`)
                st.session_state[chave_qtd] = int(item["qtd"])
            item["qtd"] = int(c_qtd.number_input(
                "Qtd", min_value=1, max_value=max(1, int(item["saldo"])),
                step=1, key=chave_qtd, label_visibility="collapsed",
            ))
            c_saldo.write(_fmt_qtd(item["saldo"]))
            c_custo.write(_fmt_moeda(item["custo"]) if item["custo"] is not None else "sem custo")
            if c_rm.button("🗑️", key=f"retirada_rm_{item['produto_id']}", help="Tirar da retirada"):
                remover = item
        if remover is not None:
            carrinho.remove(remover)
            st.rerun()

    pecas = sum(i["qtd"] for i in carrinho)
    custo = sum(i["qtd"] * (i["custo"] or 0) for i in carrinho)
    sem_custo = sum(1 for i in carrinho if i["custo"] is None)
    st.caption(
        f"{len(carrinho)} produto(s) · {pecas} peça(s) · custo estimado {_fmt_moeda(custo)}"
        + (f" · {sem_custo} sem custo cadastrado" if sem_custo else "")
    )

    c_data, c_obs = st.columns([1.3, 3])
    data_mov = c_data.date_input("Data da retirada", value=date.today(), key="retirada_data")
    obs = c_obs.text_input("Observação (opcional)", placeholder="Ex.: uso pessoal, presente...", key="retirada_obs")

    c_reg, c_limpar = st.columns([3, 1])
    if c_limpar.button("Limpar lista", use_container_width=True, key="retirada_limpar"):
        st.session_state[CHAVE_CARRINHO] = []
        st.session_state.pop(CHAVE_AVISO_ITEM, None)
        st.rerun()

    if c_reg.button(
        f"📤 Registrar retirada ({pecas} peça(s))", type="primary",
        use_container_width=True, key="retirada_registrar",
    ):
        sb = get_client()
        operacao_id = str(uuid.uuid4())
        restantes, falhas, registrados = [], [], 0
        for item in carrinho:
            try:
                registrar_retirada_pessoal(
                    sb, item["produto_id"], item["qtd"], data_mov.isoformat(),
                    motivo=obs.strip() or None, custo_unitario=item["custo"], operacao_id=operacao_id,
                )
                registrados += 1
            except SaldoInsuficienteError as e:
                falhas.append(f"{item['codigo']}: {e}")
                restantes.append(item)
            except Exception as e:
                falhas.append(f"{item['codigo']}: {type(e).__name__}: {e}")
                restantes.append(item)

        st.session_state[CHAVE_CARRINHO] = restantes
        if falhas:
            st.error(
                f"Registrados: {registrados}. Não registrados: {len(falhas)} (continuam na lista):\n\n"
                + "\n".join(f"- {f}" for f in falhas)
            )
            return
        st.session_state.pop(CHAVE_AVISO_ITEM, None)
        st.session_state[CHAVE_MSG_OK] = f"Retirada registrada: {registrados} produto(s), {pecas} peça(s)."
        st.rerun()


# ----------------------------------------------------------------------
# Estorno
# ----------------------------------------------------------------------
@_dialog("↩️ Estornar retirada", width="small")
def _dialog_estornar(mov: dict):
    produto = mov.get("_produto") or {}
    st.write(f"**{produto.get('codigo_interno') or '-'}** — {produto.get('descricao') or '-'}")
    st.write(f"Retirada de **{_fmt_qtd(mov['_qtd'])}** peça(s) em {_data_br(mov.get('data_movimento'))}.")
    st.caption(
        "As peças voltam ao estoque por um ajuste automático. A retirada original continua no "
        "histórico, marcada como estornada."
    )
    data_est = st.date_input("Data do estorno", value=date.today(), key=f"estorno_data_{mov['id']}")
    if st.button("Confirmar estorno", type="primary", use_container_width=True, key=f"estorno_ok_{mov['id']}"):
        try:
            estornar_retirada_pessoal(get_client(), mov["id"], data_est.isoformat())
        except Exception as e:
            st.error(f"Não foi possível estornar: {e}")
            return
        st.session_state[CHAVE_MSG_OK] = "Retirada estornada; as peças voltaram ao estoque."
        st.rerun()


# ----------------------------------------------------------------------
# Tela
# ----------------------------------------------------------------------
def tela_estoque_retirada():
    st.subheader("📤 Retiradas pessoais")
    st.caption(
        "Baixa de estoque para uso próprio. Não é venda: não entra em faturamento, ticket, comissão "
        "nem margem. Cada retirada guarda o custo (preço da última compra) do momento."
    )

    msg = st.session_state.pop(CHAVE_MSG_OK, None)
    if msg:
        st.success(msg)

    hoje = date.today()
    c_per, c_de, c_ate, c_busca, c_nova = st.columns([1.6, 1.2, 1.2, 1.6, 1.5], vertical_alignment="bottom")
    with c_per:
        opcao = st.selectbox(
            "Período",
            ["Mês atual", "Últimos 30 dias", "Ano atual", "Todo período", "Personalizado"],
            key="retiradas_periodo",
        )
    de, ate = date(hoje.year, hoje.month, 1), hoje
    if opcao == "Personalizado":
        with c_de:
            de = st.date_input("De", value=de, key="retiradas_de", format="DD/MM/YYYY")
        with c_ate:
            ate = st.date_input("Até", value=ate, key="retiradas_ate", format="DD/MM/YYYY")
    with c_busca:
        busca = st.text_input("Código do produto", placeholder="Filtrar...", key="retiradas_busca").strip()
    with c_nova:
        if st.button("➕ Nova retirada", type="primary", use_container_width=True, key="retiradas_nova"):
            _dialog_nova_retirada()

    ini, fim = _periodo(opcao, de, ate)
    if ini and fim and ini > fim:
        st.error("A data inicial não pode ser depois da data final.")
        return

    try:
        retiradas = _carregar_retiradas(ini, fim)
    except Exception as e:
        st.error(f"Erro ao carregar as retiradas: {e}")
        return

    if busca:
        retiradas = [
            m for m in retiradas
            if busca.casefold() in str((m.get("_produto") or {}).get("codigo_interno") or "").casefold()
        ]

    validas = [m for m in retiradas if not m["_estornada"]]
    pecas = sum(m["_qtd"] for m in validas)
    custo_total = sum(m["_qtd"] * float(m.get("custo_unitario") or 0) for m in validas)
    sem_custo = sum(1 for m in validas if m.get("custo_unitario") is None)

    k1, k2, k3, k4 = st.columns(4)
    for coluna, rotulo, valor in (
        (k1, "Retiradas", str(len(validas))),
        (k2, "Peças retiradas", _fmt_qtd(pecas)),
        (k3, "Custo retirado", _fmt_moeda(custo_total)),
        (k4, "Produtos distintos", str(len({m["produto_id"] for m in validas}))),
    ):
        with coluna:
            with st.container(border=True):
                st.metric(rotulo, valor)
    if sem_custo:
        st.caption(f"⚠️ {sem_custo} retirada(s) sem custo cadastrado entram com custo zero no total.")

    assinatura = f"{opcao}|{de}|{ate}|{busca}"
    if st.session_state.get("estoque_retiradas_assinatura") != assinatura:
        st.session_state["estoque_retiradas_assinatura"] = assinatura
        reset_paginacao("estoque_retiradas")

    total = len(retiradas)
    por_pagina = get_itens_por_pagina("estoque_retiradas", 50)
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = int(st.session_state.get("pagina_atual_estoque_retiradas", 1))
    pagina = min(max(1, pagina), total_paginas)
    st.session_state["pagina_atual_estoque_retiradas"] = pagina

    render_paginacao(
        "estoque_retiradas", total, itens_por_pagina=por_pagina,
        mostrar_contagem_superior=True, mostrar_contagem_inferior=False, permitir_seletor=True,
    )

    if not retiradas:
        st.info("Nenhuma retirada encontrada para os filtros selecionados.")
        return

    colunas = [1.1, 1.2, 3.0, 0.9, 1.2, 1.3, 2.4, 0.8]
    inicio = (pagina - 1) * por_pagina
    with st.container(border=True):
        for col, titulo in zip(
            st.columns(colunas),
            ["Data", "Cód. Produto", "Produto", "Qtd.", "Custo unit.", "Custo total", "Observação", "Ações"],
        ):
            col.markdown(f"**{titulo}**")
        st.divider()

        for mov in retiradas[inicio:inicio + por_pagina]:
            produto = mov.get("_produto") or {}
            c_data, c_cod, c_prod, c_qtd, c_cu, c_ct, c_obs, c_acao = st.columns(colunas, vertical_alignment="center")
            c_data.write(_data_br(mov.get("data_movimento")))
            c_cod.write(produto.get("codigo_interno") or "-")
            c_prod.write(produto.get("descricao") or "-")
            c_qtd.write(_fmt_qtd(mov["_qtd"]))
            custo = mov.get("custo_unitario")
            c_cu.write(_fmt_moeda(custo) if custo is not None else "-")
            c_ct.write(_fmt_moeda(mov["_qtd"] * float(custo)) if custo is not None else "-")
            c_obs.write(("↩️ estornada" if mov["_estornada"] else (mov.get("motivo") or "-")))
            if mov["_estornada"]:
                c_acao.button("🔒", key=f"retirada_bloq_{mov['id']}", disabled=True,
                              help="Retirada já estornada.", use_container_width=True)
            elif c_acao.button("↩️", key=f"retirada_est_{mov['id']}", help="Estornar retirada",
                               use_container_width=True):
                _dialog_estornar(mov)

    render_paginacao(
        "estoque_retiradas", total, itens_por_pagina=por_pagina,
        mostrar_contagem_superior=False, mostrar_contagem_inferior=True, permitir_seletor=True,
    )
