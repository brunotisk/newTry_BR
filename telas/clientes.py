import streamlit as st
from datetime import datetime

from db import supabase
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao
from componentes.campo_mascarado import campo_mascarado

_dialog = getattr(st, "dialog", None) or st.experimental_dialog


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _fmt_date(value):
    parsed = _parse_date(value)
    return parsed.strftime("%d/%m/%Y") if parsed else "-"


def _parse_date_br(value):
    """Converte uma data digitada no componente para date.

    Campo vazio (inclusive máscara vazia) significa NULL. Aceita também
    separadores/espaços que o componente possa devolver durante o rerun.
    """
    if value is None:
        return None
    texto = str(value).strip()
    if not texto:
        return None

    # O componente de máscara trabalha com DD/MM/YYYY. Retiramos qualquer
    # separador para evitar falha quando o browser devolver espaços extras.
    digitos = "".join(ch for ch in texto if ch.isdigit())
    if not digitos:
        return None
    if len(digitos) != 8:
        return None

    try:
        return datetime.strptime(digitos, "%d%m%Y").date()
    except (ValueError, TypeError):
        return None


def _format_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:11]
    if len(digits) <= 2:
        return f"({digits}" if digits else ""
    if len(digits) <= 7:
        return f"({digits[:2]}) {digits[2:]}"
    return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"



def _limpar_filtros():
    st.session_state["clientes_filtro_nome"] = ""
    st.session_state["clientes_filtro_canal"] = "Todos os canais"
    reset_paginacao("clientes")


@_dialog("👤 Cliente", width="large")
def _dialog_cliente(cliente=None):
    editando = bool(cliente)
    titulo = "Editar cliente" if editando else "Adicionar cliente"
    st.markdown(f"### {titulo}")

    try:
        canais_resp = (
            supabase.table("canais_venda")
            .select("id, nome")
            .eq("ativo", True)
            .order("nome")
            .execute()
        )
        canais = canais_resp.data or []
    except Exception as err:
        st.error(f"Erro ao carregar canais: {err}")
        return

    canal_ids = [c["id"] for c in canais]
    canal_labels = {c["id"]: c["nome"] for c in canais}
    cliente_id = cliente.get("id") if cliente else "novo"

    telefone_inicial = _format_phone(cliente.get("telefone") if cliente else "")

    col_nome, col_tel = st.columns([2.2, 1.3])
    with col_nome:
        nome = st.text_input(
            "Nome *",
            value=cliente.get("nome") or "" if cliente else "",
            key=f"cliente_nome_input_{cliente_id}",
        )
    with col_tel:
        telefone = campo_mascarado(
            "Telefone (opcional)",
            value=telefone_inicial,
            tipo="telefone",
            placeholder="(xx) xxxxx-xxxx",
            key=f"cliente_telefone_mask_{cliente_id}",
        )

    col_canal, col_nasc, col_cas = st.columns([1.4, 1.2, 1.2])
    with col_canal:
        atual_canal = cliente.get("canal_id") if cliente else None
        opcoes_canal = [None] + canal_ids
        canal_id = st.selectbox(
            "Canal",
            options=opcoes_canal,
            index=opcoes_canal.index(atual_canal) if atual_canal in opcoes_canal else 0,
            format_func=lambda x: "-" if x is None else canal_labels[x],
            key=f"cliente_canal_input_{cliente_id}",
        )
    with col_nasc:
        nascimento = campo_mascarado(
            "Data de nascimento (opcional)",
            value=_fmt_date(cliente.get("data_nascimento")) if cliente else "",
            tipo="data",
            placeholder="DD/MM/YYYY",
            key=f"cliente_nascimento_mask_{cliente_id}",
        )
    with col_cas:
        casamento = campo_mascarado(
            "Data de casamento (opcional)",
            value=_fmt_date(cliente.get("data_casamento")) if cliente else "",
            tipo="data",
            placeholder="DD/MM/YYYY",
            key=f"cliente_casamento_mask_{cliente_id}",
        )

    st.markdown("<div style='height: 18px'></div>", unsafe_allow_html=True)
    col_salvar, col_cancelar = st.columns(2)
    salvar = col_salvar.button(
        "💾 Salvar", type="primary", use_container_width=True, key=f"salvar_cliente_{cliente_id}"
    )
    cancelar = col_cancelar.button(
        "Cancelar", use_container_width=True, key=f"cancelar_cliente_{cliente_id}"
    )

    if cancelar:
        st.rerun()

    if not salvar:
        return

    nome = nome.strip()
    if not nome:
        st.error("Informe o nome do cliente.")
        return

    # Todos os campos, exceto nome, são opcionais.
    # Normalizamos valores vazios/None para permitir salvar NULL no banco.
    telefone = str(telefone or "").strip()
    nascimento = str(nascimento or "").strip()
    casamento = str(casamento or "").strip()

    # Campo de casamento/nascimento é opcional. Qualquer máscara vazia
    # (sem dígitos) deve ser tratada como NULL.
    if not any(ch.isdigit() for ch in nascimento):
        nascimento = ""
    if not any(ch.isdigit() for ch in casamento):
        casamento = ""

    nascimento_data = _parse_date_br(nascimento)
    casamento_data = _parse_date_br(casamento)

    if nascimento and not nascimento_data:
        st.error("Data de nascimento inválida. Use o formato DD/MM/YYYY.")
        return
    if casamento and not casamento_data:
        st.error("Data de casamento inválida. Use o formato DD/MM/YYYY.")
        return

    dados = {
        "nome": nome,
        "telefone": _format_phone(telefone) or None,
        "canal_id": canal_id if canal_id is not None else None,
        "data_nascimento": nascimento_data.isoformat() if nascimento_data else None,
        "data_casamento": casamento_data.isoformat() if casamento_data else None,
    }

    try:
        if editando:
            supabase.table("clientes").update(dados).eq("id", cliente["id"]).execute()
            st.toast("Cliente atualizado com sucesso!", icon="✅")
        else:
            supabase.table("clientes").insert(dados).execute()
            st.toast("Cliente cadastrado com sucesso!", icon="✅")
        st.rerun()
    except Exception as err:
        st.error(f"Erro ao salvar cliente: {err}")


def _buscar_resumo_compras():
    resumo = {}
    try:
        response = supabase.table("vendas").select("cliente, data_venda").execute()
        for venda in response.data or []:
            nome = (venda.get("cliente") or "").strip().casefold()
            if not nome:
                continue
            item = resumo.setdefault(nome, {"qtde": 0, "ultima": None})
            item["qtde"] += 1
            data = _parse_date(venda.get("data_venda"))
            if data and (item["ultima"] is None or data > item["ultima"]):
                item["ultima"] = data
    except Exception:
        pass
    return resumo


def _secao_clientes():
    try:
        canais_resp = (
            supabase.table("canais_venda")
            .select("id, nome")
            .eq("ativo", True)
            .order("nome")
            .execute()
        )
        canais = canais_resp.data or []
        canal_labels = {c["id"]: c["nome"] for c in canais}

        col_nome, col_canal, col_limpar, col_adicionar = st.columns([3.0, 2.0, 1.2, 1.5])

        with col_nome:
            filtro_nome = st.text_input(
                "Nome", key="clientes_filtro_nome", placeholder="Digite para buscar...", label_visibility="collapsed"
            )
        with col_canal:
            opcoes_canal = ["Todos os canais"] + [c["nome"] for c in canais]
            filtro_canal = st.selectbox(
                "Canal", options=opcoes_canal, key="clientes_filtro_canal", label_visibility="collapsed"
            )
        with col_limpar:
            st.button("Limpar filtros", use_container_width=True, on_click=_limpar_filtros)
        with col_adicionar:
            if st.button("➕ Adicionar cliente", type="secondary", use_container_width=True):
                _dialog_cliente()

        chave_filtro = f"{filtro_nome.strip().casefold()}|{filtro_canal}"
        if st.session_state.get("clientes_chave_filtro") != chave_filtro:
            st.session_state["clientes_chave_filtro"] = chave_filtro
            reset_paginacao("clientes")

        query = supabase.table("clientes").select(
            "id, nome, telefone, canal_id, data_nascimento, data_casamento", count="exact"
        )
        if filtro_nome.strip():
            query = query.ilike("nome", f"%{filtro_nome.strip()}%")
        if filtro_canal != "Todos os canais":
            canal_selecionado = next((c["id"] for c in canais if c["nome"] == filtro_canal), None)
            if canal_selecionado is not None:
                query = query.eq("canal_id", canal_selecionado)

        total_resp = query.order("nome").range(0, 0).execute()
        total = total_resp.count if total_resp.count is not None else 0
        st.header(f"👤 Clientes ({total} Clientes)")
        itens_por_pagina = get_itens_por_pagina("clientes")
        pagina = render_paginacao(
            "clientes", total, itens_por_pagina,
            mostrar_contagem_superior=True, mostrar_contagem_inferior=False, permitir_seletor=True
        )
        offset = (pagina - 1) * get_itens_por_pagina("clientes")

        response = query.order("nome").range(offset, offset + get_itens_por_pagina("clientes") - 1).execute()
        clientes = response.data or []

        if not clientes:
            st.info("Nenhum cliente encontrado para os filtros informados.")
            return

        resumo = _buscar_resumo_compras()

        with st.container(border=True):
            cab = st.columns([2.3, 1.4, 1.2, 1.2, 1.2, 1.2, 1.0, 0.6])
            for col, texto in zip(cab, ["Nome", "Telefone", "Canal", "Nascimento", "Casamento", "Última compra", "Compras", "Ações"]):
                col.markdown(f"**{texto}**")
            st.divider()

            for cliente in clientes:
                nome_key = (cliente.get("nome") or "").strip().casefold()
                stats = resumo.get(nome_key, {})
                cols = st.columns([2.3, 1.4, 1.2, 1.2, 1.2, 1.2, 1.0, 0.6])
                cols[0].write(cliente.get("nome") or "-")
                cols[1].write(cliente.get("telefone") or "-")
                cols[2].write(canal_labels.get(cliente.get("canal_id"), "-"))
                cols[3].write(_fmt_date(cliente.get("data_nascimento")))
                cols[4].write(_fmt_date(cliente.get("data_casamento")))
                cols[5].write(_fmt_date(stats.get("ultima")))
                cols[6].write(str(stats.get("qtde", 0)))
                if cols[7].button("✏️", key=f"editar_cliente_{cliente['id']}"):
                    _dialog_cliente(cliente)

        render_paginacao(
            "clientes", total, get_itens_por_pagina("clientes"),
            mostrar_contagem_superior=False, mostrar_contagem_inferior=True, permitir_seletor=True
        )

    except Exception as err:
        st.error(f"Erro ao consultar clientes: {err}")


def tela_clientes():
    _secao_clientes()
