import streamlit as st
from datetime import date, datetime

from db import supabase
from servicos.supabase_admin import get_client
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao
from componentes.contador_quantidade import contador_quantidade

_dialog = getattr(st, "dialog", None) or st.experimental_dialog


def _fmt_qtd(valor) -> str:
    valor = float(valor or 0)
    return f"{valor:g}"


def _parse_data(valor):
    texto = str(valor or "")[:10]
    if not texto:
        return date.today()
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _primeiro_valor(row, nomes, padrao=None):
    for nome in nomes:
        if nome in row and row.get(nome) is not None:
            return row.get(nome)
    return padrao


def _signed_quantidade(row):
    """Normaliza a quantidade do registro para o sinal que afeta o estoque."""
    valor = float(_primeiro_valor(row, ["quantidade", "quantidade_ajuste"], 0) or 0)
    if valor < 0:
        return valor

    tipo = str(_primeiro_valor(row, ["tipo", "tipo_ajuste", "direcao"], "") or "").strip().casefold()
    if tipo in {"retirada", "retirar", "saida", "saída", "negativo", "remocao", "remoção"}:
        return -abs(valor)
    return abs(valor)


def _tipo_exibicao(row):
    signed = _signed_quantidade(row)
    if signed < 0:
        return "🔴 Retirada"
    return "🟢 Adição"


def _campo_data(row):
    return _primeiro_valor(
        row,
        ["data_ajuste", "data_movimento", "data", "criado_em", "created_at"],
        None,
    )



def _eh_ajuste_manual(movimento):
    """Identifica ajustes feitos manualmente no ledger.

    Movimentos de venda/compra ficam de fora. Também ignoramos os ajustes
    automáticos usados para estornar uma venda excluída, pois eles não são
    ajustes manuais feitos nesta tela.
    """
    if movimento.get("venda_id") is not None or movimento.get("compra_id") is not None:
        return False
    if str(movimento.get("tipo") or "").casefold() != "ajuste":
        return False

    motivo = str(movimento.get("motivo") or "").strip().casefold()
    if motivo.startswith("estorno da venda"):
        return False
    return True


def _carregar_ajustes():
    """Carrega os ajustes manuais diretamente de estoque_movimentos."""
    resposta = (
        supabase.table("estoque_movimentos")
        .select(
            "id, produto_id, tipo, quantidade, saldo_apos, data_movimento, "
            "criado_em, venda_id, compra_id, motivo"
        )
        .eq("tipo", "ajuste")
        .is_("venda_id", "null")
        .is_("compra_id", "null")
        .order("data_movimento", desc=True)
        .order("id", desc=True)
        .execute()
    )
    ajustes = [m for m in (resposta.data or []) if _eh_ajuste_manual(m)]

    produto_ids = list({a.get("produto_id") for a in ajustes if a.get("produto_id") is not None})
    produtos = {}
    if produto_ids:
        resposta_produtos = (
            supabase.table("produtos")
            .select("id, codigo_interno, descricao")
            .in_("id", produto_ids)
            .execute()
        )
        produtos = {p["id"]: p for p in (resposta_produtos.data or [])}

    for ajuste in ajustes:
        produto = produtos.get(ajuste.get("produto_id"), {})
        ajuste["_produto"] = produto
        ajuste["_tipo_exibicao"] = _tipo_exibicao(ajuste)
        ajuste["_quantidade_assinada"] = _signed_quantidade(ajuste)
        ajuste["_data_exibicao"] = _campo_data(ajuste)

    return ajustes


def _estoque_atual(sb, produto_id):
    resposta = sb.table("estoque").select("quantidade_atual").eq("produto_id", produto_id).execute()
    return float(resposta.data[0].get("quantidade_atual") or 0) if resposta.data else 0.0


def _alterar_estoque_e_movimento(sb, produto_id, delta, data_movimento):
    """Aplica um delta no estoque e registra o movimento correspondente."""
    saldo_anterior = _estoque_atual(sb, produto_id)
    novo_saldo = saldo_anterior + float(delta)

    sb.table("estoque").upsert(
        {"produto_id": produto_id, "quantidade_atual": novo_saldo},
        on_conflict="produto_id",
    ).execute()

    sb.table("estoque_movimentos").insert(
        {
            "produto_id": produto_id,
            "tipo": "ajuste",
            "quantidade": float(delta),
            "saldo_apos": novo_saldo,
            "data_movimento": data_movimento,
        }
    ).execute()


def _salvar_novo_ajuste(produto_id, signed_quantidade, data_movimento, descricao_ajuste=""):
    """Cria o ajuste somente no ledger e atualiza o saldo do produto."""
    sb = get_client()
    _alterar_estoque_e_movimento(
        sb,
        produto_id,
        signed_quantidade,
        data_movimento,
    )
    # A descrição do ajuste é armazenada no campo motivo do ledger.
    if descricao_ajuste.strip():
        resposta = (
            sb.table("estoque_movimentos")
            .select("id")
            .eq("produto_id", produto_id)
            .eq("tipo", "ajuste")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        if resposta.data:
            sb.table("estoque_movimentos").update({"motivo": descricao_ajuste.strip()}).eq("id", resposta.data[0]["id"]).execute()


def _quantidade_movimento_com_sinal(movimento):
    """Converte a quantidade do ledger para o efeito real sobre o saldo."""
    quantidade = float(movimento.get("quantidade") or 0)
    tipo = str(movimento.get("tipo") or "").casefold()
    if tipo == "entrada":
        return abs(quantidade)
    if tipo == "saida":
        return -abs(quantidade)
    # Ajustes já são gravados com sinal (+/-).
    return quantidade


def _recalcular_produto(sb, produto_id):
    """Recalcula saldo_apos de todos os movimentos do produto em ordem cronológica."""
    resposta = (
        sb.table("estoque_movimentos")
        .select("id, tipo, quantidade, saldo_apos, data_movimento, criado_em, venda_id, compra_id, motivo")
        .eq("produto_id", produto_id)
        .order("data_movimento", desc=False)
        .order("criado_em", desc=False)
        .order("id", desc=False)
        .execute()
    )
    movimentos = resposta.data or []
    saldo = 0.0
    for movimento in movimentos:
        saldo += _quantidade_movimento_com_sinal(movimento)
        sb.table("estoque_movimentos").update({"saldo_apos": saldo}).eq("id", movimento["id"]).execute()

    sb.table("estoque").upsert(
        {"produto_id": produto_id, "quantidade_atual": saldo},
        on_conflict="produto_id",
    ).execute()
    return saldo


def _atualizar_ajuste(ajuste, produto_id_novo, signed_novo, data_nova, descricao_ajuste=""):
    """Edita o próprio movimento de ajuste e recalcula os saldos afetados."""
    sb = get_client()
    ajuste_id = ajuste.get("id")
    produto_id_antigo = ajuste.get("produto_id")

    if not ajuste_id:
        raise ValueError("Ajuste sem ID para edição.")
    if not produto_id_antigo:
        raise ValueError("Ajuste sem produto vinculado.")

    payload = {
        "produto_id": produto_id_novo,
        "quantidade": float(signed_novo),
        "tipo": "ajuste",
        "data_movimento": data_nova,
        "venda_id": None,
        "compra_id": None,
        "motivo": descricao_ajuste.strip() or None,
    }
    sb.table("estoque_movimentos").update(payload).eq("id", ajuste_id).execute()

    # Se o produto mudou, ambos os saldos precisam ser recalculados.
    _recalcular_produto(sb, produto_id_antigo)
    if produto_id_novo != produto_id_antigo:
        _recalcular_produto(sb, produto_id_novo)


@_dialog("🛠️ Ajuste de estoque", width="large")
def _dialog_ajuste(ajuste=None):
    novo = ajuste is None
    ajuste = ajuste or {}
    produto_atual = ajuste.get("_produto") or {}
    codigo_atual = produto_atual.get("codigo_interno") or ""

    signed_atual = _signed_quantidade(ajuste) if not novo else 0
    retirada_atual = signed_atual < 0
    quantidade_atual = max(1.0, abs(signed_atual))
    data_atual = _parse_data(_campo_data(ajuste)) if not novo else date.today()

    id_ref = "novo" if novo else str(ajuste.get("id"))

    st.caption("Novo ajuste" if novo else f"Ajuste #{ajuste.get('id')}")

    with st.container(border=True):
        # ================================================================
        # LINHA 1 — Código 40% | Motivo 20% | Quantidade 20% | Data 20%
        # ================================================================
        col_codigo, col_tipo, col_quantidade, col_data = st.columns([4, 2, 2, 2])

        with col_codigo:
            codigo = st.text_input(
                "Código interno do produto",
                value=codigo_atual,
                placeholder="Ex.: 11389",
                key=f"ajuste_codigo_{id_ref}",
            )

        with col_tipo:
            opcoes_tipo = ["Retirada", "Adição"]
            indice_tipo = 0 if retirada_atual else 1
            tipo_selecionado = st.selectbox(
                "Tipo",
                opcoes_tipo,
                index=indice_tipo,
                key=f"ajuste_tipo_{id_ref}",
            )

        with col_quantidade:
            estado_qtd = contador_quantidade(
                valor=int(quantidade_atual),
                min_valor=1,
                bloqueado=True,
                label="Quantidade",
                key=f"contador_ajuste_{id_ref}",
            )
            quantidade = max(1, int(estado_qtd.get("valor", quantidade_atual)))

        with col_data:
            data_movimento = st.date_input(
                "Data do ajuste",
                value=data_atual,
                key=f"ajuste_data_{id_ref}",
            )

        # ================================================================
        # LINHA 2 — Descrição do produto
        # ================================================================
        produto = None
        saldo_atual = None
        if codigo.strip():
            try:
                resp_prod = (
                    supabase.table("produtos")
                    .select("id, codigo_interno, descricao")
                    .eq("codigo_interno", codigo.strip())
                    .execute()
                )
                produto = resp_prod.data[0] if resp_prod.data else None
                if produto:
                    saldo_atual = _estoque_atual(supabase, produto["id"])
            except Exception as e:
                st.error(f"Erro ao buscar produto: {e}")

        # A barra de status fica antes da descrição, para o usuário saber
        # imediatamente se o produto foi localizado e qual é o saldo atual.
        if produto:
            st.success(
                f"Produto encontrado — {produto.get('descricao') or '-'} — saldo atual: **{_fmt_qtd(saldo_atual)}**"
            )
        elif codigo.strip():
            st.warning("Produto não encontrado — favor verificar o código informado.")
        else:
            st.info("Favor selecionar um produto para ajuste")

        st.markdown("**Descrição do ajuste**")
        descricao_ajuste_atual = str(ajuste.get("motivo") or "")
        descricao_ajuste = st.text_input(
            "Descrição do ajuste",
            value=descricao_ajuste_atual,
            placeholder="Ex.: Peça ganha, retirada pessoal...",
            label_visibility="collapsed",
            key=f"ajuste_descricao_{id_ref}",
        )

        st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
        salvar = st.button(
            "💾 Registrar ajuste" if novo else "💾 Salvar alterações",
            type="primary",
            use_container_width=True,
            key=f"ajuste_salvar_{id_ref}",
        )

    if not salvar:
        return

    if not codigo.strip() or not produto:
        st.error("Favor selecionar um produto para ajuste")
        return
    quantidade = max(1, int(quantidade))
    signed_novo = -float(quantidade) if tipo_selecionado == "Retirada" else float(quantidade)

    try:
        if novo:
            _salvar_novo_ajuste(
                produto["id"],
                signed_novo,
                data_movimento.isoformat(),
                descricao_ajuste,
            )
            st.success("Ajuste registrado com sucesso e estoque atualizado!")
        else:
            _atualizar_ajuste(
                ajuste,
                produto["id"],
                signed_novo,
                data_movimento.isoformat(),
                descricao_ajuste,
            )
            st.success("Ajuste atualizado com sucesso!")
        st.rerun()
    except Exception as e:
        st.error(f"Erro ao salvar ajuste: {e}")

def _secao_listagem():
    try:
        ajustes = _carregar_ajustes()
    except Exception as e:
        st.error(f"Erro ao carregar ajustes de estoque: {e}")
        return

    tipos = ["Todos", "Retirada", "Adição"]
    col_filtro, col_espaco, col_adicionar = st.columns([2, 3, 2])
    with col_filtro:
        tipo_filtro = st.selectbox("Tipo", tipos, key="estoque_ajustes_filtro_tipo")
    with col_espaco:
        st.empty()
    with col_adicionar:
        st.markdown("<div style='margin-top:1.85rem;'></div>", unsafe_allow_html=True)
        if st.button(
            "➕ Adicionar ajuste",
            type="secondary",
            use_container_width=True,
            key="estoque_ajustes_btn_adicionar",
        ):
            _dialog_ajuste(None)

    if tipo_filtro != "Todos":
        alvo = "Retirada" if tipo_filtro == "Retirada" else "Adição"
        ajustes = [a for a in ajustes if a["_tipo_exibicao"] == ("🔴 Retirada" if alvo == "Retirada" else "🟢 Adição")]

    assinatura = tipo_filtro
    if st.session_state.get("estoque_ajustes_assinatura") != assinatura:
        st.session_state["estoque_ajustes_assinatura"] = assinatura
        reset_paginacao("estoque_ajustes")

    total = len(ajustes)
    itens_por_pagina = get_itens_por_pagina("estoque_ajustes", 50)
    total_paginas = max(1, (total + itens_por_pagina - 1) // itens_por_pagina)
    pagina = int(st.session_state.get("pagina_atual_estoque_ajustes", 1))
    pagina = min(max(1, pagina), total_paginas)
    st.session_state["pagina_atual_estoque_ajustes"] = pagina

    render_paginacao(
        "estoque_ajustes",
        total,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=True,
        mostrar_contagem_inferior=False,
        permitir_seletor=True,
    )

    if not ajustes:
        st.info("Nenhum ajuste encontrado para o filtro selecionado.")
        return

    inicio = (pagina - 1) * itens_por_pagina
    pagina_ajustes = ajustes[inicio:inicio + itens_por_pagina]

    with st.container(border=True):
        c_data, c_tipo, c_codigo, c_produto, c_qtd, c_acoes = st.columns(
            [1.2, 1.2, 1.2, 2.8, 0.9, 0.8]
        )
        c_data.markdown("**Data**")
        c_tipo.markdown("**Tipo**")
        c_codigo.markdown("**Código**")
        c_produto.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_acoes.markdown("**Ações**")
        st.divider()

        for ajuste in pagina_ajustes:
            col_data, col_tipo, col_codigo, col_produto, col_qtd, col_acoes = st.columns(
                [1.2, 1.2, 1.2, 2.8, 0.9, 0.8]
            )
            col_data.write(str(ajuste.get("_data_exibicao") or "-")[:10])
            col_tipo.write(ajuste["_tipo_exibicao"])

            produto = ajuste.get("_produto") or {}
            col_codigo.write(produto.get("codigo_interno") or "-")
            col_produto.write(produto.get("descricao") or "-")
            col_qtd.write(_fmt_qtd(abs(ajuste["_quantidade_assinada"])))

            if col_acoes.button(
                "✏️",
                key=f"editar_ajuste_{ajuste.get('id')}",
                help="Editar ajuste",
                use_container_width=True,
            ):
                _dialog_ajuste(ajuste)

    render_paginacao(
        "estoque_ajustes",
        total,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
        permitir_seletor=True,
    )


def tela_estoque_ajuste():
    st.subheader("🛠️ Ajuste manual de estoque")
    st.caption(
        "Use para perdas, quebras, extravios ou correções após contagem física — "
        "situações que não vêm de uma compra ou venda registrada."
    )
    _secao_listagem()
