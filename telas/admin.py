import os
import subprocess
from pathlib import Path
import re
import streamlit as st
from datetime import datetime, timedelta
from db import supabase
from auth import usuario_e_admin
from servicos.compras_import import excluir_compra
from servicos.vendas_import import excluir_vendas_em_massa
from servicos.log_automacao import FLUXOS, ETAPAS


def _fmt_moeda(valor) -> str:
    try:
        v = float(valor or 0)
        return (
            f"R$ {v:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def _fmt_data(data_iso) -> str:
    if not data_iso:
        return "-"
    try:
        s = str(data_iso)[:10]
        ano, mes, dia = s.split("-")
        return f"{dia}/{mes}/{ano}"
    except Exception:
        return str(data_iso)


def _secao_exclusao_compras():
    st.subheader("🗑️ Exclusão de Compras (XML)")
    st.caption(
        "Esta rotina estorna as quantidades dos itens comprados no estoque (com registro no ledger de movimentações) "
        "e exclui a compra, liberando a chave de acesso para eventual reimportação."
    )

    try:
        resp_compras = (
            supabase.table("compras")
            .select(
                "id, numero_nf, data_emissao, valor_total, chave_acesso, "
                "compra_origem, fornecedor_id, fornecedores(razao_social, nome_fantasia)"
            )
            # No módulo de exclusão, a ordenação padrão é pelo ID mais recente.
            .order("id", desc=True)
            .execute()
        )
        compras = resp_compras.data or []
    except Exception as e:
        st.error(f"Erro ao carregar lista de compras: {e}")
        return

    if not compras:
        st.info("Nenhuma compra encontrada no banco de dados.")
        return

    def _rotulo_compra(c: dict) -> str:
        nf = c.get("numero_nf") or "Sem NF"
        forn = (c.get("fornecedores") or {}).get("nome_fantasia") or (c.get("fornecedores") or {}).get("razao_social") or "Fornecedor não identificado"
        dt = _fmt_data(c.get("data_emissao"))
        vt = _fmt_moeda(c.get("valor_total"))
        return f"NF {nf} | {forn} | {dt} | {vt} (ID #{c['id']})"

    # Por padrão, nenhuma compra fica selecionada.
    # O usuário pode localizar uma NF pelo filtro e então escolher a compra no dropdown.
    col_dropdown, col_filtro_nf = st.columns([3.2, 1.3])

    with col_filtro_nf:
        filtro_nf = st.text_input(
            "Filtrar por NF",
            value="",
            placeholder="Digite a NF...",
            key="admin_filtro_nf_excluir",
        ).strip()

    compras_filtradas = compras
    if filtro_nf:
        termo_nf = filtro_nf.casefold()
        compras_filtradas = [
            c for c in compras
            if termo_nf in str(c.get("numero_nf") or "").casefold()
        ]

    opcoes = {c["id"]: c for c in compras_filtradas}
    opcoes_dropdown = [None] + list(opcoes.keys())

    with col_dropdown:
        compra_id_selecionado = st.selectbox(
            "Selecione a compra que deseja excluir",
            options=opcoes_dropdown,
            index=0,
            format_func=lambda cid: (
                "Selecione uma compra..."
                if cid is None
                else _rotulo_compra(opcoes[cid])
            ),
            key="admin_select_compra_excluir",
        )

    if compra_id_selecionado is None:
        if filtro_nf and not compras_filtradas:
            st.info(f"Nenhuma compra encontrada para a NF **{filtro_nf}**.")
        else:
            st.caption(
                "Nenhuma compra selecionada. Use o filtro de NF ou escolha uma compra no dropdown."
            )
        return

    compra = opcoes[compra_id_selecionado]
    forn_info = compra.get("fornecedores") or {}
    nome_fornecedor = forn_info.get("nome_fantasia") or forn_info.get("razao_social") or "-"

    # Card com resumo da compra selecionada
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Número NF:** {compra.get('numero_nf') or '-'}")
        c2.markdown(f"**Data Emissão:** {_fmt_data(compra.get('data_emissao'))}")
        c3.markdown(f"**Valor Total:** {_fmt_moeda(compra.get('valor_total'))}")
        c4.markdown(f"**Origem:** {compra.get('compra_origem') or 'Importação XML'}")

        st.caption(f"**Fornecedor:** {nome_fornecedor} | **Chave:** `{compra.get('chave_acesso') or '-'}`")

    # Carrega itens da compra e consulta estoque atual de cada um
    try:
        resp_itens = (
            supabase.table("compras_itens")
            .select(
                "id, produto_id, quantidade, valor_unitario, valor_total, "
                "produtos(codigo_interno, descricao)"
            )
            .eq("compra_id", compra_id_selecionado)
            .execute()
        )
        itens = resp_itens.data or []
    except Exception as e:
        st.error(f"Erro ao consultar itens da compra: {e}")
        return

    if not itens:
        # Compras órfãs/incompletas podem existir quando uma importação foi
        # interrompida depois da criação do cabeçalho e antes da gravação dos itens.
        # Nesse caso não há estoque a estornar, mas o cabeçalho deve continuar
        # podendo ser excluído pela rotina administrativa.
        st.warning(
            "Esta compra não possui itens vinculados. Nenhum estoque será estornado; "
            "a exclusão removerá apenas o cabeçalho (e eventuais anexos/movimentos vinculados)."
        )
        produto_ids = []
    else:
        # Consulta saldos em estoque dos produtos afetados
        produto_ids = [it["produto_id"] for it in itens]
    saldos_atuais = {}
    if produto_ids:
        try:
            resp_est = (
                supabase.table("estoque")
                .select("produto_id, quantidade_atual")
                .in_("produto_id", produto_ids)
                .execute()
            )
            for row in (resp_est.data or []):
                saldos_atuais[row["produto_id"]] = float(row.get("quantidade_atual") or 0)
        except Exception as e:
            st.error(f"Erro ao consultar saldos atuais de estoque: {e}")

    # Monta lista para a tabela de impacto
    dados_tabela = []
    tem_negativo = False

    for it in itens:
        prod = it.get("produtos") or {}
        pid = it["produto_id"]
        qtd_comprada = float(it.get("quantidade") or 0)
        saldo_atual = saldos_atuais.get(pid, 0.0)
        saldo_pos = saldo_atual - qtd_comprada

        if saldo_pos < 0:
            tem_negativo = True
            status = "🔴 Ficará negativo"
        elif saldo_pos == 0:
            status = "🟡 Ficará zerado"
        else:
            status = "🟢 Positivo"

        dados_tabela.append({
            "Código": prod.get("codigo_interno") or "-",
            "Descrição": prod.get("descricao") or "-",
            "Qtd na NF (Estorno)": f"-{qtd_comprada:g}",
            "Saldo Atual": f"{saldo_atual:g}",
            "Saldo Projetado": f"{saldo_pos:g}",
            "Status Pós-Exclusão": status,
        })

    st.markdown("#### Prévia de Impacto no Estoque")

    if not itens:
        st.info("Sem itens vinculados: não haverá alteração de saldo de estoque.")
    elif tem_negativo:
        st.warning(
            "⚠️ **Atenção:** Um ou mais produtos ficarão com **saldo de estoque negativo**. "
            "Isso significa que já houve saídas ou vendas registradas para esses itens após a importação desta compra."
        )

    st.dataframe(dados_tabela, use_container_width=True, hide_index=True)

    st.divider()

    # Confirmação e ação de exclusão
    st.markdown("#### Confirmar Exclusão Definitiva")
    if itens:
        st.write(
            "Ao confirmar, o sistema irá:  \n"
            "1. Gravar movimentos de ajuste no ledger estornando a quantidade de cada produto.  \n"
            "2. Recalcular os dados de última compra e número de compras dos produtos afetados.  \n"
            "3. Excluir anexos vinculados e remover o registro da compra e de seus itens."
        )
    else:
        st.write(
            "Ao confirmar, o sistema irá remover os anexos vinculados, eventuais "
            "movimentos vinculados e o cabeçalho da compra. Como não há itens, "
            "nenhum ajuste de estoque será realizado."
        )

    nf_esperada = str(compra.get("numero_nf") or "").strip()
    col_conf, col_btn = st.columns([1.5, 1])

    with col_conf:
        confirmacao_texto = st.text_input(
            f"Digite o número da NF ({nf_esperada}) para habilitar o botão de exclusão:",
            key=f"admin_confirm_nf_{compra_id_selecionado}",
        )

    pode_excluir = confirmacao_texto.strip() == nf_esperada

    with col_btn:
        st.write("")  # Espaçamento vertical
        st.write("")
        if st.button(
            "🗑️ Excluir Compra e Estornar Estoque" if itens else "🗑️ Excluir Compra sem Itens",
            type="primary",
            disabled=not pode_excluir,
            use_container_width=True,
            key=f"btn_admin_excluir_{compra_id_selecionado}",
        ):
            with st.spinner("Excluindo compra e ajustando estoque..."):
                try:
                    resultado = excluir_compra(supabase, compra_id_selecionado)
                    st.success(
                        f"✅ Compra #{resultado['compra_id']} (NF {resultado['numero_nf']}) excluída com sucesso! "
                        f"{resultado['itens_estornados']} itens estornados no estoque e ledger."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao excluir compra: {e}")


def _secao_exclusao_vendas_massa():
    st.subheader("🗑️ Exclusão de Vendas em Massa")
    st.caption(
        "Exclui várias vendas de uma vez — por período ou todas — devolvendo "
        "a quantidade de cada uma ao estoque do produto correspondente e "
        "registrando o estorno no ledger. É a mesma rotina usada na exclusão "
        "individual de uma venda, repetida para cada registro selecionado; "
        "cada venda excluída gera sua própria linha no Log de Automações."
    )

    todas = st.toggle(
        "Selecionar todas as vendas do sistema (ignora o período abaixo)",
        value=False,
        key="admin_massa_todas",
    )

    data_ini = data_fim = None
    if not todas:
        col_di, col_df = st.columns(2)
        with col_di:
            data_ini = st.date_input("Data inicial", value=None, key="admin_massa_data_ini")
        with col_df:
            data_fim = st.date_input("Data final", value=None, key="admin_massa_data_fim")

        if not data_ini or not data_fim:
            st.caption(
                "Selecione a data inicial e final, ou marque "
                "\"Selecionar todas as vendas do sistema\" acima."
            )
            return
        if data_ini > data_fim:
            st.error("A data inicial não pode ser depois da data final.")
            return

    try:
        query = (
            supabase.table("vendas")
            .select(
                "id, produto_id, quantidade, data_venda, valor_final, cliente, "
                "produtos(codigo_interno, descricao)"
            )
            .order("data_venda", desc=True)
        )
        if not todas:
            query = query.gte("data_venda", data_ini.isoformat()).lte(
                "data_venda", data_fim.isoformat()
            )
        vendas = query.execute().data or []
    except Exception as e:
        st.error(f"Erro ao consultar vendas: {e}")
        return

    if not vendas:
        st.info("Nenhuma venda encontrada para os critérios selecionados.")
        return

    total_vendas = len(vendas)
    valor_total = sum(float(v.get("valor_final") or 0) for v in vendas)
    produtos_distintos = len({v["produto_id"] for v in vendas})

    col_k1, col_k2, col_k3 = st.columns(3)
    with col_k1:
        with st.container(border=True):
            st.caption("Vendas selecionadas")
            st.title(f"{total_vendas}")
    with col_k2:
        with st.container(border=True):
            st.caption("Valor total")
            st.title(_fmt_moeda(valor_total))
    with col_k3:
        with st.container(border=True):
            st.caption("Produtos distintos afetados")
            st.title(f"{produtos_distintos}")

    rotulo_periodo = (
        "TODAS AS VENDAS DO SISTEMA" if todas
        else f"{_fmt_data(data_ini.isoformat())} até {_fmt_data(data_fim.isoformat())}"
    )
    st.warning(
        f"⚠️ Você está prestes a excluir **{total_vendas} venda(s)** ({rotulo_periodo}). "
        "O estoque de cada produto será estornado e esta ação não pode ser desfeita."
    )

    with st.expander(f"Ver as {total_vendas} venda(s) que serão excluídas"):
        dados_tabela = [
            {
                "ID": v["id"],
                "Data": _fmt_data(v.get("data_venda")),
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Qtd": v.get("quantidade"),
                "Valor": _fmt_moeda(v.get("valor_final")),
                "Cliente": v.get("cliente") or "-",
            }
            for v in vendas
        ]
        st.dataframe(dados_tabela, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Confirmar Exclusão em Massa")

    confirmacao = st.text_input(
        f"Digite {total_vendas} (a quantidade de vendas acima) para habilitar o botão de exclusão:",
        key="admin_massa_confirmacao",
    )
    pode_excluir = confirmacao.strip() == str(total_vendas)

    if st.button(
        f"🗑️ Excluir {total_vendas} Venda(s) e Estornar Estoque",
        type="primary",
        disabled=not pode_excluir,
        use_container_width=True,
        key="btn_admin_excluir_vendas_massa",
    ):
        with st.spinner(f"Excluindo {total_vendas} venda(s) e ajustando estoque..."):
            resultado = excluir_vendas_em_massa(supabase, vendas)

        if resultado["falhas"]:
            st.warning(
                f"⚠️ {resultado['sucesso']} de {resultado['total']} venda(s) excluída(s) com "
                f"sucesso. {len(resultado['falhas'])} falharam — veja o detalhe abaixo. As que "
                "falharam continuam no sistema e podem ser tentadas novamente."
            )
            st.dataframe(resultado["falhas"], use_container_width=True, hide_index=True)
        else:
            st.success(
                f"✅ {resultado['sucesso']} venda(s) excluída(s) com sucesso! "
                "Estoque estornado para cada produto afetado."
            )
            st.caption("Recarregue a aba (ou troque de filtro) para atualizar a lista.")


def _secao_ajuste_comissao():
    st.subheader("💰 Ajuste de Comissão")
    st.caption(
        "Aplica nas vendas de um canal o percentual de comissão cadastrado no "
        "parceiro vinculado a esse canal. A comissão é calculada sobre o "
        "**valor final** da venda e grava `comissao_percentual` e `comissao_valor`."
    )

    # --- Canais que possuem parceiro cadastrado ---
    try:
        canais = (
            supabase.table("canais_venda")
            .select("id, nome, ativo")
            .order("nome")
            .execute()
            .data
            or []
        )
        parceiros = (
            supabase.table("parceiros")
            .select("id, canal_id, nome, percentual_comissao, ativo")
            .execute()
            .data
            or []
        )
    except Exception as e:
        st.error(f"Erro ao carregar canais e parceiros: {e}")
        return

    parceiro_por_canal = {p["canal_id"]: p for p in parceiros}
    canais_map = {c["id"]: c for c in canais}

    def _rotulo_canal(cid) -> str:
        c = canais_map[cid]
        p = parceiro_por_canal.get(cid)
        if not p:
            return f"{c['nome']} — sem parceiro cadastrado"
        return f"{c['nome']} — {p['nome']} ({float(p['percentual_comissao'] or 0):g}%)"

    canal_id = st.selectbox(
        "Canal de venda",
        options=[None] + [c["id"] for c in canais],
        format_func=lambda cid: "Selecione um canal..." if cid is None else _rotulo_canal(cid),
        key="admin_comissao_canal",
    )
    if canal_id is None:
        st.caption("Selecione um canal para continuar.")
        return

    parceiro = parceiro_por_canal.get(canal_id)
    if not parceiro:
        st.warning(
            "Este canal não possui parceiro cadastrado, então não há comissão "
            "para aplicar. Cadastre o parceiro e tente novamente."
        )
        return
    if not parceiro.get("ativo"):
        st.warning(f"⚠️ O parceiro **{parceiro['nome']}** está marcado como inativo.")

    pct = float(parceiro.get("percentual_comissao") or 0)
    with st.container(border=True):
        c1, c2 = st.columns(2)
        c1.markdown(f"**Parceiro:** {parceiro['nome']}")
        c2.markdown(f"**Comissão cadastrada:** {pct:g}%")

    # --- Escopo da seleção ---
    modo = st.radio(
        "Quais vendas ajustar?",
        options=["Período", "Todas as vendas do canal", "Venda específica (ID)"],
        horizontal=True,
        key="admin_comissao_modo",
    )

    data_ini = data_fim = None
    ids_venda: list[int] = []

    if modo == "Período":
        col_di, col_df = st.columns(2)
        with col_di:
            data_ini = st.date_input("Data inicial", value=None, key="admin_comissao_data_ini")
        with col_df:
            data_fim = st.date_input("Data final", value=None, key="admin_comissao_data_fim")
        if not data_ini or not data_fim:
            st.caption("Selecione a data inicial e a final.")
            return
        if data_ini > data_fim:
            st.error("A data inicial não pode ser depois da data final.")
            return

    elif modo == "Venda específica (ID)":
        texto_ids = st.text_input(
            "ID da venda (para várias, separe por vírgula)",
            placeholder="Ex.: 123 ou 123, 124, 130",
            key="admin_comissao_ids",
        ).strip()
        if not texto_ids:
            st.caption("Informe ao menos um ID de venda.")
            return
        partes = [p.strip() for p in re.split(r"[,\s;]+", texto_ids) if p.strip()]
        invalidos = [p for p in partes if not p.isdigit()]
        if invalidos:
            st.error(f"ID(s) inválido(s): {', '.join(invalidos)}. Use apenas números.")
            return
        ids_venda = sorted({int(p) for p in partes})

    # --- Consulta das vendas (sempre restritas ao canal escolhido) ---
    try:
        query = (
            supabase.table("vendas")
            .select(
                "id, data_venda, valor_final, cliente, comissao_percentual, "
                "comissao_valor, produtos(descricao)"
            )
            .eq("canal_venda_id", canal_id)
            .order("data_venda", desc=True)
        )
        if modo == "Período":
            query = query.gte("data_venda", data_ini.isoformat()).lte(
                "data_venda", data_fim.isoformat()
            )
        elif modo == "Venda específica (ID)":
            query = query.in_("id", ids_venda)
        vendas = query.execute().data or []
    except Exception as e:
        st.error(f"Erro ao consultar vendas: {e}")
        return

    if modo == "Venda específica (ID)":
        encontrados = {v["id"] for v in vendas}
        faltantes = [i for i in ids_venda if i not in encontrados]
        if faltantes:
            st.warning(
                "Venda(s) não encontrada(s) neste canal: "
                + ", ".join(f"#{i}" for i in faltantes)
            )

    if not vendas:
        st.info("Nenhuma venda encontrada para os critérios selecionados.")
        return

    # --- Vendas já incluídas em fechamento de parceria ficam de fora ---
    fechadas: set[int] = set()
    try:
        ids_todas = [v["id"] for v in vendas]
        for i in range(0, len(ids_todas), 200):
            lote = ids_todas[i:i + 200]
            resp = (
                supabase.table("parcerias_fechamento_vendas")
                .select("venda_id")
                .in_("venda_id", lote)
                .execute()
                .data
                or []
            )
            fechadas.update(r["venda_id"] for r in resp)
    except Exception as e:
        st.error(f"Erro ao verificar fechamentos de parceria: {e}")
        return

    elegiveis = [v for v in vendas if v["id"] not in fechadas]
    if fechadas:
        st.warning(
            f"{len(fechadas)} venda(s) já fazem parte de um fechamento de parceria e "
            "**não serão alteradas**, para não divergir do valor já fechado: "
            + ", ".join(f"#{i}" for i in sorted(fechadas)[:20])
            + ("..." if len(fechadas) > 20 else "")
        )
    if not elegiveis:
        st.info("Nenhuma venda elegível para ajuste.")
        return

    def _nova_comissao(v: dict) -> float:
        return round(float(v.get("valor_final") or 0) * pct / 100, 2)

    total_vendas = len(elegiveis)
    base_total = sum(float(v.get("valor_final") or 0) for v in elegiveis)
    comissao_atual = sum(float(v.get("comissao_valor") or 0) for v in elegiveis)
    comissao_nova = sum(_nova_comissao(v) for v in elegiveis)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        with st.container(border=True):
            st.caption("Vendas a ajustar")
            st.title(f"{total_vendas}")
    with k2:
        with st.container(border=True):
            st.caption("Valor vendido")
            st.title(_fmt_moeda(base_total))
    with k3:
        with st.container(border=True):
            st.caption("Comissão atual")
            st.title(_fmt_moeda(comissao_atual))
    with k4:
        with st.container(border=True):
            st.caption(f"Comissão nova ({pct:g}%)")
            st.title(_fmt_moeda(comissao_nova))

    with st.expander(f"Ver as {total_vendas} venda(s) que serão ajustadas"):
        st.dataframe(
            [
                {
                    "ID": v["id"],
                    "Data": _fmt_data(v.get("data_venda")),
                    "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                    "Cliente": v.get("cliente") or "-",
                    "Valor final": _fmt_moeda(v.get("valor_final")),
                    "% atual": (
                        "-" if v.get("comissao_percentual") is None
                        else f"{float(v['comissao_percentual']):g}%"
                    ),
                    "Comissão atual": _fmt_moeda(v.get("comissao_valor")),
                    "% nova": f"{pct:g}%",
                    "Comissão nova": _fmt_moeda(_nova_comissao(v)),
                }
                for v in elegiveis
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    if st.button(
        f"✅ Aplicar {pct:g}% em {total_vendas} venda(s)",
        type="primary",
        use_container_width=True,
        key="btn_admin_aplicar_comissao",
    ):
        ok, falhas = 0, []
        with st.spinner(f"Atualizando {total_vendas} venda(s)..."):
            for v in elegiveis:
                try:
                    supabase.table("vendas").update(
                        {
                            "comissao_percentual": pct,
                            "comissao_valor": _nova_comissao(v),
                        }
                    ).eq("id", v["id"]).execute()
                    ok += 1
                except Exception as e:
                    falhas.append({"ID": v["id"], "Erro": str(e)})

        if falhas:
            st.warning(
                f"⚠️ {ok} de {total_vendas} venda(s) atualizada(s). "
                f"{len(falhas)} falharam — veja abaixo."
            )
            st.dataframe(falhas, use_container_width=True, hide_index=True)
        else:
            st.success(
                f"✅ Comissão de {pct:g}% aplicada em {ok} venda(s) do canal "
                f"**{canais_map[canal_id]['nome']}**."
            )


_ICONE_ETAPA = {
    "cadastro_produto": "📇",
    "ajusta_estoque": "📦",
    "ajusta_movimentacao": "🔄",
}

_ORDEM_ETAPA = {"cadastro_produto": 0, "ajusta_estoque": 1, "ajusta_movimentacao": 2}

_PERIODOS = {
    "Últimas 24 horas": 1,
    "Últimos 7 dias": 7,
    "Últimos 30 dias": 30,
    "Tudo": None,
}


def _fmt_hora(valor_iso) -> str:
    if not valor_iso:
        return "-"
    try:
        dt = datetime.fromisoformat(str(valor_iso).replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(valor_iso)


def _rotulo_referencia(grupo: dict) -> str:
    if grupo.get("compra_id"):
        return f"Compra #{grupo['compra_id']}"
    if grupo.get("venda_id"):
        return f"Venda #{grupo['venda_id']}"
    return f"Operação {str(grupo['operacao_id'])[:8]}"


def _secao_log_automacoes():
    st.subheader("📋 Log de Automações")
    st.caption(
        "Histórico das inserções e ajustes que o sistema faz automaticamente: "
        "cadastro de produto (só em importação de compra), ajuste de saldo de "
        "estoque e gravação do movimento no ledger (`estoque_movimentos`)."
    )

    col_f1, col_f2, col_f3 = st.columns([2, 2, 1.3])

    with col_f1:
        opcoes_fluxo = ["Todos"] + list(FLUXOS.keys())
        fluxo_selecionado = st.selectbox(
            "Fluxo",
            options=opcoes_fluxo,
            format_func=lambda f: "Todos os fluxos" if f == "Todos" else FLUXOS.get(f, f),
            key="admin_log_filtro_fluxo",
        )

    with col_f2:
        periodo_selecionado = st.selectbox(
            "Período",
            options=list(_PERIODOS.keys()),
            index=1,
            key="admin_log_filtro_periodo",
        )

    with col_f3:
        st.write("")
        apenas_falhas = st.toggle("Somente falhas", value=False, key="admin_log_filtro_falhas")

    try:
        query = (
            supabase.table("logs_automacao")
            .select("*")
            .order("criado_em", desc=True)
            .limit(1000)
        )

        if fluxo_selecionado != "Todos":
            query = query.eq("fluxo", fluxo_selecionado)

        dias = _PERIODOS[periodo_selecionado]
        if dias is not None:
            cutoff = (datetime.utcnow() - timedelta(days=dias)).isoformat()
            query = query.gte("criado_em", cutoff)

        if apenas_falhas:
            query = query.eq("sucesso", False)

        registros_base = query.execute().data or []
    except Exception as e:
        st.error(
            "Erro ao carregar o log de automações. Verifique se a tabela "
            f"`logs_automacao` já foi criada no banco. Detalhe: {e}"
        )
        return

    if not registros_base:
        st.info("Nenhum registro encontrado para os filtros selecionados.")
        return

    # Filtro de usuário: as opções vêm do próprio resultado já carregado
    # (não dá pra oferecer antes de saber quem aparece no período/fluxo).
    usuarios_distintos = sorted({r.get("usuario") for r in registros_base if r.get("usuario")})
    if usuarios_distintos:
        usuario_selecionado = st.selectbox(
            "Usuário",
            options=["Todos"] + usuarios_distintos,
            key="admin_log_filtro_usuario",
        )
        registros = (
            registros_base if usuario_selecionado == "Todos"
            else [r for r in registros_base if r.get("usuario") == usuario_selecionado]
        )
    else:
        registros = registros_base

    if not registros:
        st.info("Nenhum registro encontrado para os filtros selecionados.")
        return

    total = len(registros)
    total_falhas = sum(1 for r in registros if not r.get("sucesso"))

    col_k1, col_k2 = st.columns(2)
    with col_k1:
        with st.container(border=True):
            st.caption("Etapas registradas no período")
            st.title(f"{total}")
    with col_k2:
        with st.container(border=True):
            st.caption("Etapas com falha")
            st.title(f"{total_falhas}")

    st.markdown("---")

    aba_resumo, aba_detalhe = st.tabs(["📊 Log Resumo", "📄 Log Detalhe"])

    grupos = _agrupar_registros(registros)

    with aba_resumo:
        _renderizar_log_resumo(grupos)

    with aba_detalhe:
        _renderizar_log_detalhe(grupos)


_COLUNAS_RESUMO_CONDICAO = {
    "Compras": lambda r: r.get("compra_id") is not None,
    "Vendas": lambda r: r.get("venda_id") is not None,
    "Produto": lambda r: r["etapa"] == "cadastro_produto",
    "Mov. Estoque": lambda r: r["etapa"] == "ajusta_movimentacao",
    "Saldo Estoque": lambda r: r["etapa"] == "ajusta_estoque",
}


def _farol_coluna(etapas: list[dict], condicao) -> str:
    """🟢 todas as ocorrências dessa área tiveram sucesso, 🔴 pelo menos uma
    falhou, '—' quando essa área nem se aplica a esta execução (nenhum
    registro correspondente)."""
    filtrados = [r for r in etapas if condicao(r)]
    if not filtrados:
        return "—"
    return "🔴" if any(not r.get("sucesso") for r in filtrados) else "🟢"


def _agrupar_registros(registros: list[dict]) -> list[dict]:
    """Agrupa as etapas de uma mesma execução: por compra_id, por venda_id,
    ou por operacao_id quando o registro não pertence a nenhuma compra/venda
    específica. Cada grupo representa UMA atividade individual — uma
    importação de XML, uma venda (manual ou importada), uma edição, uma
    exclusão — na ordem em que ocorreu."""
    grupos: dict = {}
    for r in registros:
        if r.get("compra_id"):
            chave = ("compra", r["compra_id"])
        elif r.get("venda_id"):
            chave = ("venda", r["venda_id"])
        else:
            chave = ("op", r["operacao_id"])

        if chave not in grupos:
            grupos[chave] = {
                "fluxo": r["fluxo"],
                "compra_id": r.get("compra_id"),
                "venda_id": r.get("venda_id"),
                "operacao_id": r.get("operacao_id"),
                "etapas": [],
                "criado_em_max": r["criado_em"],
            }
        grupos[chave]["etapas"].append(r)
        if r["criado_em"] > grupos[chave]["criado_em_max"]:
            grupos[chave]["criado_em_max"] = r["criado_em"]

    return sorted(grupos.values(), key=lambda g: g["criado_em_max"], reverse=True)


def _renderizar_log_resumo(grupos: list[dict]):
    st.caption(
        "Uma linha por execução — cada importação de XML, cada venda "
        "(importada ou manual), cada edição/exclusão gera sua própria linha. "
        "Cada coluna mostra o farol daquela área: 🟢 tudo certo, 🔴 houve "
        "falha, — a área não se aplica a essa atividade."
    )

    tabela = []
    for grupo in grupos:
        etapas = grupo["etapas"]
        usuarios = sorted({e.get("usuario") for e in etapas if e.get("usuario")})
        sucesso_geral = all(e.get("sucesso") for e in etapas)

        tabela.append({
            "Status": "🟢" if sucesso_geral else "🔴",
            "Atividade": FLUXOS.get(grupo["fluxo"], grupo["fluxo"]),
            "Referência": _rotulo_referencia(grupo),
            **{
                coluna: _farol_coluna(etapas, condicao)
                for coluna, condicao in _COLUNAS_RESUMO_CONDICAO.items()
            },
            "Usuário": ", ".join(usuarios) if usuarios else "—",
            "Quando": _fmt_hora(grupo["criado_em_max"]),
        })

    st.dataframe(tabela, use_container_width=True, hide_index=True)


def _renderizar_log_detalhe(grupos: list[dict]):
    for grupo in grupos:
        etapas = sorted(
            grupo["etapas"], key=lambda e: _ORDEM_ETAPA.get(e["etapa"], 99)
        )
        sucesso_geral = all(e.get("sucesso") for e in etapas)
        icone_geral = "✅" if sucesso_geral else "❌"
        rotulo_fluxo = FLUXOS.get(grupo["fluxo"], grupo["fluxo"])
        rotulo_ref = _rotulo_referencia(grupo)

        usuarios_grupo = sorted({e.get("usuario") for e in etapas if e.get("usuario")})
        rotulo_usuario = f" — 👤 {usuarios_grupo[0]}" if len(usuarios_grupo) == 1 else (
            f" — 👤 {len(usuarios_grupo)} usuários" if len(usuarios_grupo) > 1 else ""
        )

        titulo = (
            f"{icone_geral} {rotulo_fluxo} — {rotulo_ref} — "
            f"{_fmt_hora(grupo['criado_em_max'])} ({len(etapas)} etapa(s)){rotulo_usuario}"
        )

        with st.expander(titulo):
            for e in etapas:
                icone_etapa = _ICONE_ETAPA.get(e["etapa"], "•")
                icone_status = "✅" if e.get("sucesso") else "❌"
                rotulo_etapa = ETAPAS.get(e["etapa"], e["etapa"])

                partes = [f"{icone_status} {icone_etapa} **{rotulo_etapa}**"]
                if e.get("produto_id"):
                    partes.append(f"produto #{e['produto_id']}")
                if e.get("quantidade") is not None:
                    partes.append(f"qtd {e['quantidade']:g}")
                if e.get("usuario"):
                    partes.append(f"👤 {e['usuario']}")
                partes.append(_fmt_hora(e["criado_em"]))

                st.write(" · ".join(partes))
                if e.get("mensagem"):
                    st.caption(e["mensagem"])


def _localizar_docs() -> Path:
    candidatos = [
        Path(__file__).resolve().parent / "docs",
        Path(__file__).resolve().parent.parent / "docs",
    ]
    for caminho in candidatos:
        if caminho.exists():
            return caminho
    return candidatos[0]


def _ler_versao() -> str:
    arquivo = _localizar_docs() / "version.py"
    try:
        conteudo = arquivo.read_text(encoding="utf-8")
        match = re.search(r'APP_VERSION\s*=\s*["\\\']([^"\\\']+)["\\\']', conteudo)
        return match.group(1) if match else "Não informada"
    except Exception:
        return "Não informada"


def _ler_changelog() -> str:
    try:
        return (_localizar_docs() / "changelog.md").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _informacoes_git() -> tuple[str, str]:
    try:
        raiz = Path(__file__).resolve().parent
        h = subprocess.run(
            ["git", "-C", str(raiz), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        d = subprocess.run(
            ["git", "-C", str(raiz), "log", "-1", "--format=%cI"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        data = "-"
        if d:
            data = datetime.fromisoformat(d.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
        return h or "-", data
    except Exception:
        return "-", "-"


def _secao_info_sistema():
    st.subheader("ℹ️ Informações do Sistema")

    nome_aplicacao = "Sistema ERP Light - BR"
    ambiente = os.getenv("APP_ENV", "").strip().upper()
    if not ambiente:
        ambiente = "DEV" if os.getenv("SKIP_AUTH", "").strip().lower() == "true" else "NÃO INFORMADO"

    versao = _ler_versao()
    commit, ultima_atualizacao = _informacoes_git()
    changelog = _ler_changelog()

    col1, col2, col3 = st.columns(3)

    with col1:
        with st.container(border=True):
            st.caption("Aplicação")
            st.markdown(f"### {nome_aplicacao}")
            st.caption("Versão")
            st.markdown(f"### `v{versao}`")

    with col2:
        with st.container(border=True):
            st.caption("Ambiente")
            icone = {"PROD": "🟢", "HOMOLOG": "🟡", "DEV": "🔵"}.get(ambiente, "⚪")
            st.markdown(f"### {icone} {ambiente}")
            st.caption("Última atualização")
            st.markdown(f"### {ultima_atualizacao}")

    with col3:
        with st.container(border=True):
            st.caption("Informações Administrativas")
            st.markdown(
                "Área destinada a módulos operacionais e de manutenção avançada do sistema."
            )
            st.caption(
                "Acesso restrito ao administrador (`bruno.ishikawa@gmail.com`) "
                "ou ambiente de desenvolvimento (`SKIP_AUTH=True`)."
            )

    st.caption(f"Commit: `{commit}`")
    st.caption("A data de atualização corresponde ao último commit disponível no Git.")

    if changelog:
        with st.expander("📋 Histórico de versões"):
            st.markdown(changelog)




def _csv_download(dados: list[dict], nome: str) -> None:
    """Exibe botão de download CSV sem depender de pandas."""
    import csv
    import io

    if not dados:
        st.info("Não há registros para exportar.")
        return
    buffer = io.StringIO()
    colunas = list(dict.fromkeys(chave for linha in dados for chave in linha.keys()))
    writer = csv.DictWriter(buffer, fieldnames=colunas, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(dados)
    st.download_button(
        f"⬇️ Baixar {nome} (CSV)",
        data=buffer.getvalue().encode("utf-8-sig"),
        file_name=nome,
        mime="text/csv",
        use_container_width=True,
        key=f"download_{nome.replace('.', '_')}",
    )


def _carregar_todos_produtos_fotos() -> list[dict]:
    """Busca todos os produtos, contornando o limite padrão de paginação do Supabase."""
    todos = []
    inicio = 0
    tamanho = 1000
    while True:
        lote = (
            supabase.table("produtos")
            .select("id, codigo_interno, descricao, foto_url, produto_descricao_site")
            .order("codigo_interno")
            .range(inicio, inicio + tamanho - 1)
            .execute()
            .data
            or []
        )
        todos.extend(lote)
        if len(lote) < tamanho:
            break
        inicio += tamanho
    return todos


def _secao_auditoria_fotos():
    """Audita fotos dos produtos contra o catálogo público e gerencia o Storage."""
    st.subheader("🖼️ Auditoria de Fotos dos Produtos")
    st.caption(
        "Compara `produtos.codigo_interno` com o SKU das variantes no site, "
        "verifica as URLs atuais e permite copiar fotos para o Storage público do Supabase."
    )
    st.info(
        "A auditoria atualiza `produtos.produto_descricao_site` com o título encontrado "
        "no catálogo público para cada SKU correspondente. As fotos só são enviadas ao "
        "Storage e `produtos.foto_url` só é atualizado quando você executar uma ação de envio."
    )

    try:
        # Serviço de auditoria separado da camada de interface.
        from servicos.busca_fotos_produtos import (
            LOJA_PADRAO, BUCKET, ACAO_COPIAR, ACAO_MIGRAR,
            baixar_catalogo, indexar_por_sku, auditar,
            testar_urls, processar_lote, enviar_foto_do_produto,
            gravar_descricao_site, gravar_descricoes_site_lote, diagnosticar_ambiente,
        )
    except Exception as e:
        st.error(
            "Não foi possível carregar `servicos/busca_fotos_produtos.py`. Confirme que o arquivo "
            f"está no projeto e que requests e Pillow estão instalados. Detalhe: {e}"
        )
        return

    loja = st.text_input(
        "URL da loja",
        value=LOJA_PADRAO,
        key="admin_fotos_url_loja",
        help="Endereço público da loja Shopify.",
    ).strip().rstrip("/")
    bucket = st.text_input(
        "Bucket do Supabase Storage",
        value=BUCKET,
        key="admin_fotos_bucket_v2",
    ).strip() or BUCKET

    with st.expander("🩺 Diagnóstico do ambiente (rode em DEV e em PROD e compare)"):
        st.caption(
            "Testa, no ambiente em que o app está rodando agora: projeto Supabase, colunas, permissão "
            "de UPDATE em produtos, função em lote, envio/leitura/remoção no bucket e acesso à loja."
        )
        if st.button("Rodar diagnóstico", key="admin_fotos_diagnostico"):
            with st.spinner("Verificando banco, Storage e rede..."):
                st.session_state["admin_fotos_diag"] = diagnosticar_ambiente(supabase, bucket, loja)
        diagnostico = st.session_state.get("admin_fotos_diag")
        if diagnostico:
            st.dataframe(diagnostico, use_container_width=True, hide_index=True)
            if any(l["status"].startswith("❌") for l in diagnostico):
                st.error("Há verificações com falha neste ambiente — é aí que o envio de fotos para.")
            else:
                st.success("Todas as verificações passaram neste ambiente.")

    col_auditar, col_limpar = st.columns([1, 1])
    with col_auditar:
        executar_auditoria = st.button(
            "🔎 Auditar fotos agora",
            type="primary",
            use_container_width=True,
            key="admin_fotos_auditar",
        )
    with col_limpar:
        limpar_resultado = st.button(
            "Limpar resultado",
            use_container_width=True,
            key="admin_fotos_limpar",
        )

    col_cache, col_urls = st.columns(2)
    with col_cache:
        atualizar_catalogo = st.checkbox(
            "Baixar catálogo novamente (ignorar cache)",
            value=False,
            key="admin_fotos_atualizar_catalogo",
            help="Por padrão, reutiliza por 30 minutos o catálogo baixado nesta sessão, reduzindo chamadas à loja e o risco de HTTP 429.",
        )
    with col_urls:
        testar_todas_urls = st.checkbox(
            "Testar acessibilidade de todas as URLs (mais lento)",
            value=False,
            key="admin_fotos_testar_urls",
            help="Faz requisições HTTP para cada URL de foto. Deixe desmarcado para uma auditoria mais rápida; você pode testar as URLs quando precisar.",
        )

    if limpar_resultado:
        for chave_estado in ("admin_fotos_auditoria", "admin_fotos_urls_testadas"):
            st.session_state.pop(chave_estado, None)
        st.rerun()

    if executar_auditoria:
        if not loja.startswith(("https://", "http://")):
            st.error("Informe uma URL válida começando com http:// ou https://.")
            return
        try:
            with st.spinner("Consultando produtos do banco e catálogo do site..."):
                produtos_db = _carregar_todos_produtos_fotos()
                progresso = st.progress(0, text="Preparando catálogo público...")
                def _progresso(pagina, total):
                    progresso.progress(min(0.85, pagina / 100), text=f"Catálogo: página {pagina} — {total} produtos")
                avisos_catalogo = []
                cache_catalogo = st.session_state.get("admin_fotos_catalogo_cache")
                loja_cache = st.session_state.get("admin_fotos_catalogo_cache_loja")
                cache_em = st.session_state.get("admin_fotos_catalogo_cache_em")
                cache_valido = (
                    bool(cache_catalogo)
                    and loja_cache == loja
                    and isinstance(cache_em, datetime)
                    and datetime.now() - cache_em < timedelta(minutes=30)
                )
                if cache_valido and not atualizar_catalogo:
                    catalogo = cache_catalogo
                    progresso.progress(0.85, text=f"Reutilizando catálogo em cache ({len(catalogo)} produtos)")
                else:
                    catalogo = baixar_catalogo(loja, progresso=_progresso, avisos=avisos_catalogo)
                    if catalogo:
                        st.session_state["admin_fotos_catalogo_cache"] = catalogo
                        st.session_state["admin_fotos_catalogo_cache_loja"] = loja
                        st.session_state["admin_fotos_catalogo_cache_em"] = datetime.now()
                for aviso_catalogo in avisos_catalogo:
                    st.warning(aviso_catalogo)
                if not catalogo:
                    st.error(
                        "Não foi possível obter nenhuma página do catálogo. "
                        "A auditoria foi interrompida para evitar resultados incorretos. "
                        "Tente novamente mais tarde."
                    )
                    return
                indice_site = indexar_por_sku(catalogo, loja)
                linhas = auditar(produtos_db, indice_site, bucket=bucket)
                # Persiste o título do produto no site para os SKUs que tiveram correspondência.
                # Um erro de gravação é mostrado sem descartar os resultados da auditoria.
                atualizadas_descricao = 0
                falhas_descricao = []
                produtos_por_id = {p.get("id"): p for p in produtos_db}
                descricoes_pendentes = []
                linhas_por_id = {}
                for linha in linhas:
                    descricao_site = str(linha.get("produto_no_site") or "").strip()
                    if not descricao_site or linha.get("situacao_site") == "nao_encontrado":
                        continue
                    produto_id = linha.get("produto_id")
                    descricao_anterior = str((produtos_por_id.get(produto_id) or {}).get("produto_descricao_site") or "").strip()
                    if descricao_site == descricao_anterior:
                        linha["produto_descricao_site"] = descricao_anterior
                        continue
                    descricoes_pendentes.append({"id": produto_id, "descricao": descricao_site})
                    linhas_por_id[produto_id] = linha
                if descricoes_pendentes:
                    try:
                        # Caminho rápido: atualiza todas as descrições em uma única chamada ao banco.
                        atualizadas_descricao = gravar_descricoes_site_lote(supabase, descricoes_pendentes)
                        for item in descricoes_pendentes:
                            linhas_por_id[item["id"]]["produto_descricao_site"] = item["descricao"]
                    except Exception:
                        # Compatibilidade com bancos nos quais a função SQL ainda não foi criada.
                        # Funciona, mas é mais lento; o SQL do pacote habilita a gravação em lote.
                        for item in descricoes_pendentes:
                            try:
                                gravar_descricao_site(supabase, item["id"], item["descricao"])
                                linhas_por_id[item["id"]]["produto_descricao_site"] = item["descricao"]
                                atualizadas_descricao += 1
                            except Exception as erro_descricao:
                                falhas_descricao.append({
                                    "codigo_interno": linhas_por_id[item["id"]].get("codigo_interno"),
                                    "erro": f"{type(erro_descricao).__name__}: {erro_descricao}",
                                })
                status_urls = {}
                if testar_todas_urls:
                    progresso.progress(0.86, text="Verificando acessibilidade das URLs (etapa mais demorada)...")
                    urls = sorted({
                        str(url).strip()
                        for linha in linhas
                        for url in (linha.get("foto_site"), linha.get("foto_atual_url"))
                        if str(url or "").strip().startswith(("https://", "http://"))
                    })
                    status_urls = testar_urls(urls) if urls else {}
                else:
                    progresso.progress(0.98, text="Pulando teste individual das URLs para concluir mais rápido...")
                for linha in linhas:
                    url_site = str(linha.get("foto_site") or "").strip()
                    url_atual = str(linha.get("foto_atual_url") or "").strip()
                    if testar_todas_urls:
                        linha["url_site_testada"] = status_urls.get(url_site, "sem_url") if url_site else "sem_url"
                        linha["url_banco_testada"] = status_urls.get(url_atual, "sem_url") if url_atual else "sem_url"
                    else:
                        linha["url_site_testada"] = "não testada" if url_site else "sem_url"
                        linha["url_banco_testada"] = "não testada" if url_atual else "sem_url"
                    if testar_todas_urls and linha.get("acao") == "ok" and url_atual and status_urls.get(url_atual) == "quebrada":
                        linha["acao"] = "verificar_url_storage"
                st.session_state["admin_fotos_auditoria"] = linhas
                st.session_state["admin_fotos_urls_testadas"] = status_urls
                st.success(
                    f"Auditoria concluída: {len(produtos_db)} produtos cadastrados auditados; "
                    f"{atualizadas_descricao} descrição(ões) do site atualizada(s) no banco."
                )
                if not testar_todas_urls:
                    st.caption("Para acelerar a auditoria, o teste HTTP individual das fotos foi pulado. Marque a opção acima se precisar validar as URLs.")
                if falhas_descricao:
                    st.warning(
                        "Algumas descrições não foram gravadas em `produto_descricao_site`. "
                        "Confirme se a coluna foi criada no banco e veja os detalhes abaixo."
                    )
                    st.dataframe(falhas_descricao, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Falha ao executar a auditoria: {type(e).__name__}: {e}")
            return

    linhas = st.session_state.get("admin_fotos_auditoria")
    if linhas is None:
        st.caption("Clique em **Auditar fotos agora** para consultar o banco e o catálogo do site.")
        return

    total = len(linhas)
    sem_foto = sum(1 for r in linhas if r.get("foto_atual_tipo") == "sem_foto")
    storage_ok = sum(1 for r in linhas if r.get("foto_atual_tipo") == "storage" and r.get("url_banco_testada") == "ok")
    url_quebrada = sum(1 for r in linhas if "quebrada" in (r.get("url_banco_testada"), r.get("url_site_testada")))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Produtos auditados", total)
    k2.metric("Sem foto no banco", sem_foto)
    k3.metric("Fotos no Storage acessíveis", storage_ok)
    k4.metric("URLs quebradas", url_quebrada)

    st.markdown("#### Resultado da auditoria")
    filtro = st.multiselect(
        "Filtrar por ação",
        options=sorted({str(r.get("acao") or "") for r in linhas}),
        default=sorted({str(r.get("acao") or "") for r in linhas}),
        key="admin_fotos_filtro_acao",
    )
    exibidas = [r for r in linhas if str(r.get("acao") or "") in filtro]
    colunas_visiveis = [
        "codigo_interno", "descricao", "produto_no_site", "produto_descricao_site",
        "situacao_site", "foto_atual_tipo", "url_banco_testada", "url_site_testada", "acao",
        "pagina_site", "foto_site", "foto_atual_url", "origem_copia",
    ]
    st.dataframe(
        [{c: r.get(c, "") for c in colunas_visiveis} for r in exibidas],
        use_container_width=True,
        hide_index=True,
        column_config={
            "pagina_site": st.column_config.LinkColumn("Página no site"),
            "foto_site": st.column_config.LinkColumn("Foto no site"),
            "foto_atual_url": st.column_config.LinkColumn("Foto atual no banco"),
            "origem_copia": st.column_config.LinkColumn("Origem da cópia"),
        },
    )
    _csv_download(linhas, "auditoria_fotos_produtos.csv")

    st.divider()
    st.markdown("#### Enviar fotos para o Storage")
    st.caption(
        "Escolha o alcance do envio. Só entram produtos cadastrados no banco e que tenham "
        "uma foto correspondente encontrada no catálogo da loja."
    )
    elegiveis = [
        r for r in linhas
        if r.get("acao") in (ACAO_COPIAR, ACAO_MIGRAR)
        and r.get("origem_copia")
    ]
    sem_foto_elegiveis = [
        r for r in elegiveis
        if r.get("foto_atual_tipo") == "sem_foto" or r.get("acao") == ACAO_COPIAR
    ]

    if not elegiveis:
        st.success("Não há fotos encontradas no site para os produtos cadastrados que precisam ser enviadas.")
    else:
        modo_envio = st.radio(
            "O que você deseja enviar?",
            options=["Sem foto cadastrada", "Todos encontrados no site", "Escolher código"],
            format_func=lambda opcao: {
                "Sem foto cadastrada": f"Somente produtos sem foto cadastrada ({len(sem_foto_elegiveis)})",
                "Todos encontrados no site": f"Todos os produtos com foto encontrada no site ({len(elegiveis)})",
                "Escolher código": "Escolher um produto pelo código",
            }[opcao],
            index=0,
            key="admin_fotos_modo_envio",
            help=(
                "Enviar todos pode substituir as fotos atuais dos produtos selecionados. "
                "A opção de código limita o envio a um único produto."
            ),
        )
        por_codigo = {str(r.get("codigo_interno")): r for r in elegiveis}
        selecionados = []
        if modo_envio == "Sem foto cadastrada":
            selecionados = [str(r.get("codigo_interno")) for r in sem_foto_elegiveis]
            st.caption("Serão enviados apenas produtos que ainda não têm foto cadastrada no banco.")
        elif modo_envio == "Todos encontrados no site":
            selecionados = list(por_codigo)
            st.warning(
                "Esta opção pode substituir fotos que já estão cadastradas, usando a imagem atual do site. "
                "Confira a lista antes de confirmar."
            )
        else:
            codigos = list(por_codigo)
            codigo_escolhido = st.selectbox(
                "Código do produto",
                options=codigos,
                index=None,
                placeholder="Digite ou selecione um código...",
                format_func=lambda codigo: (
                    f"{codigo} — {por_codigo[codigo].get('descricao') or por_codigo[codigo].get('produto_no_site') or ''}"
                ),
                key="admin_fotos_codigo_envio_unico",
            )
            if codigo_escolhido:
                selecionados = [codigo_escolhido]

        if selecionados:
            itens_envio = [por_codigo[c] for c in selecionados]
            st.caption(f"Produtos selecionados para envio: **{len(itens_envio)}**")
            with st.expander("Conferir produtos selecionados", expanded=(modo_envio != "Todos encontrados no site")):
                st.dataframe(
                    [
                        {
                            "Código": item.get("codigo_interno"),
                            "Produto cadastrado": item.get("descricao"),
                            "Descrição no site": item.get("produto_no_site"),
                            "Situação atual": item.get("foto_atual_tipo"),
                        }
                        for item in itens_envio
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

        confirmar = st.checkbox(
            "Confirmo o envio das fotos selecionadas e a atualização de produtos.foto_url.",
            key="admin_fotos_confirmar_copia_v2",
        )
        if st.button(
            f"☁️ Enviar {len(selecionados)} foto(s) para o bucket {bucket}",
            type="primary",
            disabled=not selecionados or not confirmar,
            use_container_width=True,
            key="admin_fotos_enviar_lote_v2",
        ):
            itens = [por_codigo[c] for c in selecionados]
            barra = st.progress(0, text="Enviando fotos...")
            def _progresso_lote(n, total_lote, codigo):
                barra.progress(n / max(total_lote, 1), text=f"Processando {n}/{total_lote}: {codigo}")
            with st.spinner("Baixando, normalizando e enviando as imagens..."):
                resultados = processar_lote(supabase, itens, progresso=_progresso_lote, bucket=bucket)
            # O resultado fica em session_state e é mostrado logo abaixo, depois do rerun
            # (mensagens exibidas antes do st.rerun() desaparecem na hora).
            st.session_state["admin_fotos_resultado_lote"] = resultados
            st.rerun()

    resultado_lote = st.session_state.get("admin_fotos_resultado_lote")
    if resultado_lote:
        ok_lote = sum(1 for r in resultado_lote if r.get("ok"))
        falhas_lote = [r for r in resultado_lote if not r.get("ok")]
        if falhas_lote:
            st.error(
                f"Último envio: {ok_lote} de {len(resultado_lote)} foto(s) gravada(s); "
                f"{len(falhas_lote)} falharam. O erro de cada código está na coluna `erro` abaixo."
            )
            st.dataframe(falhas_lote, use_container_width=True, hide_index=True)
        else:
            st.success(f"Último envio: {ok_lote} de {len(resultado_lote)} foto(s) enviada(s) e gravada(s) em `produtos.foto_url`.")
        with st.expander("Último resultado do envio automático"):
            st.dataframe(resultado_lote, use_container_width=True, hide_index=True)
            _csv_download(resultado_lote, "resultado_envio_fotos.csv")

    st.divider()
    st.markdown("#### Enviar uma foto manualmente")
    produtos_db = None
    try:
        produtos_db = _carregar_todos_produtos_fotos()
    except Exception as e:
        st.error(f"Não foi possível carregar os produtos para envio manual: {e}")
        return

    opcoes_produto = {
        p["id"]: p for p in produtos_db
    }
    opcoes_ids = list(opcoes_produto)
    if not opcoes_ids:
        st.info("Não há produtos cadastrados.")
        return

    produto_id = st.selectbox(
        "Produto que receberá a foto",
        options=opcoes_ids,
        format_func=lambda pid: (
            f"{opcoes_produto[pid].get('codigo_interno')} — {opcoes_produto[pid].get('descricao')}"
        ),
        key="admin_fotos_produto_manual",
    )
    arquivo_foto = st.file_uploader(
        "Selecione a foto do produto",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"],
        key="admin_fotos_arquivo_manual",
        help="A imagem será convertida para JPEG, limitada a 1200 px e enviada ao bucket público.",
    )
    confirmar_manual = st.checkbox(
        "Confirmo que quero substituir a foto_url deste produto pela nova foto.",
        key="admin_fotos_confirmar_manual",
    )
    if st.button(
        "⬆️ Enviar foto manual ao Storage",
        type="primary",
        disabled=arquivo_foto is None or not confirmar_manual,
        use_container_width=True,
        key="admin_fotos_enviar_manual",
    ):
        produto = opcoes_produto[produto_id]
        try:
            with st.spinner("Normalizando e enviando a foto..."):
                nova_url = enviar_foto_do_produto(
                    supabase,
                    produto_id,
                    str(produto.get("codigo_interno") or ""),
                    arquivo_foto.getvalue(),
                    bucket=bucket,
                )
            st.success(f"Foto enviada e `produtos.foto_url` atualizado para {produto.get('codigo_interno')}.")
            st.markdown(f"[Abrir foto no Storage]({nova_url})")
            st.session_state.pop("admin_fotos_auditoria", None)
        except Exception as e:
            st.error(f"Falha ao enviar a foto: {type(e).__name__}: {e}")



def tela_admin():
    if not usuario_e_admin():
        st.error("⛔ Acesso não autorizado. Esta área é restrita a administradores.")
        st.stop()

    st.header("⚙️ Painel de Administração")

    aba_compras, aba_vendas, aba_comissao, aba_log, aba_fotos, aba_info = st.tabs([
        "🗑️ Exclusão de Compras (XML)",
        "🗑️ Exclusão de Vendas em Massa",
        "💰 Ajuste Comissão",
        "📋 Log de Automações",
        "🖼️ Auditoria de Fotos",
        "ℹ️ Informações do Sistema",
    ])

    with aba_compras:
        _secao_exclusao_compras()

    with aba_vendas:
        _secao_exclusao_vendas_massa()

    with aba_comissao:
        _secao_ajuste_comissao()

    with aba_log:
        _secao_log_automacoes()

    with aba_fotos:
        _secao_auditoria_fotos()

    with aba_info:
        _secao_info_sistema()
