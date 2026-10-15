"""Tela mobile: consulta, cadastro e edição de clientes."""
from datetime import date

import streamlit as st

from db import supabase, buscar_todos
from telas_mobile.comum import cadastros, digitos, fmt_data, fmt_moeda, format_phone, lista_clientes, parse_data

LIMITE_CLIENTES = 20


def _vendas_do_cliente(cliente: dict) -> list:
    """Mesma regra da tela de clientes: vínculo por cliente_id + vendas antigas pelo nome."""
    colunas = (
        "id, data_venda, quantidade, valor_final, "
        "produtos(codigo_interno, descricao), formas_pagamento(descricao)"
    )
    por_id = buscar_todos(
        lambda: supabase.table("vendas").select(colunas)
        .eq("cliente_id", cliente["id"]).order("id")
    )
    vendas = {v["id"]: v for v in por_id}

    nome = (cliente.get("nome") or "").strip()
    if nome:
        like = nome.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        candidatas = buscar_todos(
            lambda: supabase.table("vendas").select(colunas.replace("id, ", "id, cliente, ", 1))
            .is_("cliente_id", "null").ilike("cliente", f"%{like}%").order("id")
        )
        for v in candidatas:
            if (v.get("cliente") or "").strip().casefold() == nome.casefold():
                vendas.setdefault(v["id"], v)

    return sorted(vendas.values(), key=lambda v: (str(v.get("data_venda") or ""), v["id"]), reverse=True)


def _render_compras(cliente: dict) -> None:
    try:
        vendas = _vendas_do_cliente(cliente)
    except Exception as err:
        st.error(f"Erro ao consultar as compras: {err}")
        return
    if not vendas:
        st.info("Este cliente ainda não possui compras.")
        return
    total = sum(float(v.get("valor_final") or 0) for v in vendas)
    ultima = max((parse_data(v.get("data_venda")) for v in vendas if v.get("data_venda")), default=None)
    c1, c2, c3 = st.columns(3)
    c1.metric("Itens", len(vendas))
    c2.metric("Total", fmt_moeda(total))
    c3.metric("Última", ultima.strftime("%d/%m/%y") if ultima else "-")
    st.dataframe(
        [
            {
                "Data": fmt_data(v.get("data_venda")),
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Qtd": v.get("quantidade"),
                "Valor": fmt_moeda(v.get("valor_final")),
            }
            for v in vendas[:30]
        ],
        use_container_width=True,
        hide_index=True,
    )
    if len(vendas) > 30:
        st.caption(f"Mostrando as 30 compras mais recentes de {len(vendas)}.")


def _form_cliente(cli, cad: dict) -> None:
    ss = st.session_state
    novo = cli == "novo"
    dados = {} if novo else cli
    sufixo = "novo" if novo else str(dados["id"])

    st.markdown("##### ➕ Novo cliente" if novo else "##### ✏️ Editar cliente")
    canais = {c["id"]: c["nome"] for c in cad["canais"]}
    feiras = {f["id"]: f["nome_feira"] for f in cad["feiras"]}

    nome = st.text_input("Nome *", value=dados.get("nome") or "", key=f"mc_nome_{sufixo}")
    telefone = st.text_input(
        "Telefone (opcional)", value=dados.get("telefone") or "",
        placeholder="(11) 91234-5678", key=f"mc_tel_{sufixo}",
    )
    opcoes_canal = list(canais)
    atual = dados.get("canal_id")
    canal_id = st.selectbox(
        "Canal *", opcoes_canal,
        index=opcoes_canal.index(atual) if atual in opcoes_canal else None,
        placeholder="Escolha o canal",
        format_func=lambda i: canais[i], key=f"mc_canal_{sufixo}",
    )
    e_feira = canal_id is not None and canais[canal_id].strip().casefold() == "feira"
    feira_id = None
    if e_feira:
        opcoes_feira = list(feiras)
        atual_f = dados.get("detalhe_feira_id")
        feira_id = st.selectbox(
            "Feira *", opcoes_feira,
            index=opcoes_feira.index(atual_f) if atual_f in opcoes_feira else None,
            placeholder="Escolha a feira",
            format_func=lambda i: feiras[i], key=f"mc_feira_{sufixo}",
        )

    kw = {"min_value": date(1900, 1, 1), "max_value": date.today(), "format": "DD/MM/YYYY"}
    nasc = st.date_input("Nascimento (opcional)", value=parse_data(dados.get("data_nascimento")),
                         key=f"mc_nasc_{sufixo}", **kw)
    casamento = st.date_input("Casamento (opcional)", value=parse_data(dados.get("data_casamento")),
                              key=f"mc_cas_{sufixo}", **kw)

    col_salvar, col_cancelar = st.columns(2)
    if col_cancelar.button("Cancelar", use_container_width=True, key=f"mc_cancel_{sufixo}"):
        ss["m_cli_edit"] = None
        st.rerun()
    if not col_salvar.button("💾 Salvar", type="primary", use_container_width=True, key=f"mc_save_{sufixo}"):
        return

    nome = nome.strip()
    if not nome:
        st.error("Informe o nome do cliente.")
        return
    if canal_id is None:
        st.error("Selecione o canal.")
        return
    if e_feira and feira_id is None:
        st.error('Selecione a feira: é obrigatória quando o canal é "Feira".')
        return

    payload = {
        "nome": nome,
        "telefone": format_phone(telefone) or None,
        "canal_id": canal_id,
        "detalhe_feira_id": feira_id if e_feira else None,
        "data_nascimento": nasc.isoformat() if nasc else None,
        "data_casamento": casamento.isoformat() if casamento else None,
    }
    try:
        if novo:
            supabase.table("clientes").insert(payload).execute()
        else:
            supabase.table("clientes").update(payload).eq("id", dados["id"]).execute()
    except Exception as err:
        msg = str(err)
        if "duplicate key value violates unique constraint" in msg:
            st.error(
                f'Já existe um cliente chamado "{nome}" no canal "{canais[canal_id]}". '
                "Se for a mesma pessoa, use o cadastro existente (a unificação fica no sistema completo)."
            )
        else:
            st.error(f"Erro ao salvar cliente: {err}")
        return

    lista_clientes.clear()
    ss["m_cli_edit"] = None
    ss["m_flash_cli"] = "✅ Cliente cadastrado!" if novo else "✅ Cliente atualizado!"
    st.rerun()


def tela_clientes_mobile() -> None:
    ss = st.session_state
    try:
        cad = cadastros()
    except Exception as err:
        st.error(f"Não foi possível carregar os cadastros: {err}")
        return

    if ss.get("m_cli_edit") is not None:
        _form_cliente(ss["m_cli_edit"], cad)
        return

    flash = ss.pop("m_flash_cli", None)
    if flash:
        st.success(flash)

    canais = {c["id"]: c["nome"] for c in cad["canais"]}
    busca = st.text_input("Buscar", placeholder="🔎 Nome do cliente…", label_visibility="collapsed", key="m_cli_busca")
    col_canal, col_novo = st.columns([3, 2], vertical_alignment="bottom")
    filtro_canal = col_canal.selectbox(
        "Canal", ["Todos os canais"] + list(canais.values()), label_visibility="collapsed", key="m_cli_filtro_canal"
    )
    if col_novo.button("➕ Novo", use_container_width=True, key="m_cli_novo_btn"):
        ss["m_cli_edit"] = "novo"
        st.rerun()

    chave = f"{busca.strip().casefold()}|{filtro_canal}"
    if ss.get("m_cli_chave") != chave:
        ss["m_cli_chave"] = chave
        ss["m_cli_limite"] = LIMITE_CLIENTES
    limite = ss.setdefault("m_cli_limite", LIMITE_CLIENTES)

    try:
        query = supabase.table("clientes").select(
            "id, nome, telefone, canal_id, data_nascimento, data_casamento, detalhe_feira_id", count="exact"
        )
        if busca.strip():
            query = query.ilike("nome", f"%{busca.strip()}%")
        if filtro_canal != "Todos os canais":
            canal_id = next((i for i, n in canais.items() if n == filtro_canal), None)
            if canal_id is not None:
                query = query.eq("canal_id", canal_id)
        resp = query.order("nome").order("id").range(0, limite - 1).execute()
    except Exception as err:
        st.error(f"Erro ao consultar clientes: {err}")
        return

    clientes = resp.data or []
    total = resp.count if resp.count is not None else len(clientes)
    st.caption(f"{total} cliente(s)")
    if not clientes:
        st.info("Nenhum cliente encontrado.")
        return

    for cli in clientes:
        with st.container(border=True):
            st.markdown(f"**{cli.get('nome') or '-'}**")
            partes = [cli.get("telefone"), canais.get(cli.get("canal_id"))]
            st.caption(" · ".join(p for p in partes if p) or "sem telefone")
            col_zap, col_edit = st.columns(2)
            fone = digitos(cli.get("telefone"))
            if len(fone) >= 10:
                col_zap.link_button("💬 WhatsApp", f"https://wa.me/55{fone}", use_container_width=True)
            if col_edit.button("✏️ Editar", key=f"m_cli_ed_{cli['id']}", use_container_width=True):
                ss["m_cli_edit"] = cli
                st.rerun()
            if st.toggle("🛒 Ver compras", key=f"m_cli_comp_{cli['id']}"):
                _render_compras(cli)

    if total > limite:
        if st.button(f"Mostrar mais ({total - limite} restantes)", use_container_width=True, key="m_cli_mais"):
            ss["m_cli_limite"] = limite + LIMITE_CLIENTES
            st.rerun()
