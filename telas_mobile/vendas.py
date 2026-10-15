"""Tela mobile: lançar uma nova venda (itens + dados da venda)."""
import uuid

import streamlit as st

from servicos.vendas_import import (
    calcular_comissao_venda,
    get_client,
    inserir_venda_e_baixar_estoque,
)
from telas_mobile.comum import (
    cadastros, escolha, fmt_moeda, format_phone, limpar_caches_venda,
    lista_clientes, produtos_em_estoque,
)

MOTIVO_SAIDA = "Venda Manual"  # mesmo motivo da tela de vendas do sistema completo


def _rotulo_produto(p: dict) -> str:
    return f"{p['codigo_interno']} — {p['descricao']} (saldo {p['saldo']:g})"


def _form_item() -> None:
    ss = st.session_state
    itens = ss.setdefault("m_itens", [])
    n = ss.setdefault("m_form_n", 0)

    produtos = produtos_em_estoque()
    por_id = {p["id"]: p for p in produtos}

    pid = st.selectbox(
        "Produto",
        options=list(por_id),
        index=None,
        placeholder="Digite o código ou a descrição…",
        format_func=lambda i: _rotulo_produto(por_id[i]),
        key=f"m_prod_{n}",
    )
    if pid is None:
        if not produtos:
            st.warning("Nenhum produto com saldo em estoque.")
        return

    p = por_id[pid]
    foto = str(p.get("foto_url") or "").strip()
    if foto.startswith("http"):
        st.image(foto, width=96)

    no_carrinho = sum(i["quantidade"] for i in itens if i["produto"]["id"] == pid)
    disponivel = p["saldo"] - no_carrinho
    st.caption(f"Saldo: {p['saldo']:g}" + (f" · já na venda: {no_carrinho:g}" if no_carrinho else ""))

    sufixo = f"{n}_{pid}"
    qtd = int(st.number_input("Quantidade", min_value=1, value=1, step=1, key=f"m_qtd_{sufixo}"))
    preco = float(st.number_input(
        "Preço unitário (R$)", min_value=0.0, value=float(p["preco"]),
        step=1.0, format="%.2f", key=f"m_preco_{sufixo}",
    ))
    col_desc, col_tipo = st.columns([2, 1])
    desc = float(col_desc.number_input(
        "Desconto", min_value=0.0, value=0.0, step=1.0, format="%.2f", key=f"m_desc_{sufixo}",
    ))
    tipo = col_tipo.radio("Em", ["R$", "%"], horizontal=True, key=f"m_tipo_{sufixo}")

    desconto_valor = round(qtd * preco * desc / 100, 2) if tipo == "%" else desc
    valor_final = qtd * preco - desconto_valor
    st.markdown(f"**Total do item: {fmt_moeda(valor_final)}**")

    if st.button("➕ Adicionar à venda", type="primary", use_container_width=True, key=f"m_add_{sufixo}"):
        if qtd > disponivel:
            st.error(f"Quantidade ({qtd}) maior que o saldo disponível ({disponivel:g}).")
            return
        if valor_final < 0:
            st.error("O desconto não pode ser maior que o valor do item.")
            return
        itens.append({
            "uid": str(uuid.uuid4()),
            "produto": p,
            "quantidade": float(qtd),
            "valor_lista": preco,
            "valor_desconto": float(desconto_valor),
            "valor_final": float(valor_final),
        })
        ss["m_form_n"] = n + 1  # novo conjunto de widgets: formulário volta limpo
        st.rerun()


def _render_carrinho() -> None:
    ss = st.session_state
    itens = ss.get("m_itens", [])
    if not itens:
        return
    st.markdown("##### 🧾 Itens da venda")
    for item in itens:
        p = item["produto"]
        with st.container(border=True):
            col_txt, col_rm = st.columns([5, 1], vertical_alignment="center")
            col_txt.markdown(f"**{p['codigo_interno']}** — {p['descricao']}")
            detalhe = f"{item['quantidade']:g} × {fmt_moeda(item['valor_lista'])}"
            if item["valor_desconto"]:
                detalhe += f" − desc. {fmt_moeda(item['valor_desconto'])}"
            col_txt.caption(f"{detalhe} = **{fmt_moeda(item['valor_final'])}**")
            if col_rm.button("✕", key=f"m_rm_{item['uid']}", help="Remover item"):
                ss["m_itens"] = [i for i in itens if i["uid"] != item["uid"]]
                st.rerun()
    total = sum(i["valor_final"] for i in itens)
    st.markdown(f"### Total: {fmt_moeda(total)}")


def _resolver_cliente(sb, h: dict):
    """(cliente_id, nome). Cliente novo: reaproveita o mesmo nome no mesmo canal (regra de
    unicidade da tela de clientes) ou cria um novo com canal, feira e telefone."""
    if not h["cliente_novo"]:
        cli = h["cliente_existente"]
        return cli["id"], cli["nome"]

    nome = h["nome_novo"].strip()
    like = nome.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    achados = (
        sb.table("clientes").select("id, nome")
        .eq("canal_id", h["canal_id"]).ilike("nome", like)
        .execute().data or []
    )
    for c in achados:
        if (c.get("nome") or "").strip().casefold() == nome.casefold():
            return c["id"], c["nome"]

    dados = {"nome": nome, "canal_id": h["canal_id"]}
    if h["feira_id"]:
        dados["detalhe_feira_id"] = h["feira_id"]
    telefone = format_phone(h["tel_novo"])
    if telefone:
        dados["telefone"] = telefone
    resp = sb.table("clientes").insert(dados).execute()
    return resp.data[0]["id"], nome


def _salvar_venda(h: dict) -> None:
    ss = st.session_state
    itens = list(ss.get("m_itens", []))
    sb = get_client()

    try:
        cliente_id, cliente_nome = _resolver_cliente(sb, h)
    except Exception as err:
        st.error(f"Não foi possível cadastrar/localizar o cliente: {err}")
        return

    header = {
        "canal_venda_id": h["canal_id"],
        "status_id": h["status_id"],
        "data_venda": h["data"].isoformat(),
        "cliente": cliente_nome,
        "cliente_id": cliente_id,  # vínculo real (a tela de clientes agradece)
        "forma_pagamento_id": h["forma_id"],
        "detalhe_feira_id": h["feira_id"],
    }

    sucesso, falhas, itens_falhos = 0, [], []
    for item in itens:
        p = item["produto"]
        try:
            pct, valor_comissao = calcular_comissao_venda(sb, h["canal_id"], item["valor_final"])
            inserir_venda_e_baixar_estoque(
                sb,
                {
                    **header,
                    "produto_id": p["id"],
                    "quantidade": float(item["quantidade"]),
                    "valor_lista": float(item["valor_lista"]),
                    "valor_desconto": float(item["valor_desconto"]),
                    "valor_final": float(item["valor_final"]),
                    "comissao_percentual": pct,
                    "comissao_valor": valor_comissao,
                },
                p["id"],
                motivo_saida=MOTIVO_SAIDA,
            )
            sucesso += 1
        except Exception as exc:
            itens_falhos.append(item)
            falhas.append(f"{p.get('codigo_interno') or p['id']}: {exc}")

    limpar_caches_venda()

    if falhas:
        # Só o que falhou continua no carrinho (o que já gravou não duplica ao tentar de novo).
        ss["m_itens"] = itens_falhos
        st.warning(
            f"{sucesso} de {len(itens)} item(ns) registrados (estoque atualizado). "
            f"{len(falhas)} falharam e continuam na lista:\n\n" + "\n".join(f"- {f}" for f in falhas)
        )
        return

    ss["m_itens"] = []
    ss["m_form_n"] = ss.get("m_form_n", 0) + 1
    ss["m_reset_cliente"] = True
    ss["m_flash"] = f"✅ {sucesso} item(ns) registrados e estoque atualizado!"
    st.rerun()


def _render_dados_venda(cad: dict) -> None:
    """Cabeçalho da venda + botão salvar. Só aparece quando há itens."""
    st.markdown("##### 📝 Dados da venda")
    canais = {c["nome"]: c["id"] for c in cad["canais"]}
    formas = {f["descricao"]: f["id"] for f in cad["formas"]}
    status = {s["nome"]: s["id"] for s in cad["status"]}
    feiras = {f["nome_feira"]: f["id"] for f in cad["feiras"]}

    canal = escolha("Canal", list(canais), "m_canal")
    feira = None
    if canal and canal.strip().casefold() == "feira":
        feira = st.selectbox("Feira", list(feiras), index=None, placeholder="Escolha a feira", key="m_feira")
    forma = escolha("Pagamento", list(formas), "m_forma")
    status_nome = st.selectbox("Status", list(status), key="m_status") if status else None
    try:
        data = st.date_input("Data da venda", format="DD/MM/YYYY", key="m_data")
    except TypeError:  # Streamlit antigo, sem o parâmetro format
        data = st.date_input("Data da venda", key="m_data")

    cliente_novo = st.toggle("Cliente novo", key="m_cli_novo")
    cliente_existente, nome_novo, tel_novo = None, "", ""
    if cliente_novo:
        nome_novo = st.text_input("Nome do cliente *", key="m_cli_nome")
        tel_novo = st.text_input("Telefone (opcional)", key="m_cli_tel", placeholder="(11) 91234-5678")
    else:
        canais_nome_por_id = {c["id"]: c["nome"] for c in cad["canais"]}
        clientes = lista_clientes()
        por_id = {c["id"]: c for c in clientes}
        cid = st.selectbox(
            "Cliente",
            options=list(por_id),
            index=None,
            placeholder="Digite o nome…",
            format_func=lambda i: (
                f"{por_id[i]['nome']} · {canais_nome_por_id.get(por_id[i].get('canal_id'), '-')}"
            ),
            key="m_cli_id",
        )
        cliente_existente = por_id.get(cid)

    if st.button("💾 Salvar venda", type="primary", use_container_width=True, key="m_salvar"):
        erro = None
        if not canal:
            erro = "Selecione o canal."
        elif canal.strip().casefold() == "feira" and not feira:
            erro = "Selecione a feira."
        elif not forma:
            erro = "Selecione a forma de pagamento."
        elif not status_nome:
            erro = "Selecione o status."
        elif not data:
            erro = "Informe a data da venda."
        elif cliente_novo and not nome_novo.strip():
            erro = "Informe o nome do cliente."
        elif not cliente_novo and cliente_existente is None:
            erro = "Selecione o cliente (ou ative 'Cliente novo')."
        elif not st.session_state.get("m_itens"):
            erro = "Adicione ao menos um produto."
        if erro:
            st.error(erro)
            return

        with st.spinner("Registrando venda…"):
            _salvar_venda({
                "canal_id": canais[canal],
                "feira_id": feiras.get(feira) if feira else None,
                "forma_id": formas[forma],
                "status_id": status[status_nome],
                "data": data,
                "cliente_novo": cliente_novo,
                "cliente_existente": cliente_existente,
                "nome_novo": nome_novo,
                "tel_novo": tel_novo,
            })


def tela_vendas_mobile() -> None:
    flash = st.session_state.pop("m_flash", None)
    if flash:
        st.success(flash)
    try:
        cad = cadastros()
    except Exception as err:
        st.error(f"Não foi possível carregar os cadastros: {err}")
        return

    _form_item()
    _render_carrinho()
    if st.session_state.get("m_itens"):
        _render_dados_venda(cad)
