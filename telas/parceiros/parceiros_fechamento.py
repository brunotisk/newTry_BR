"""Parceiros > Fechamento: ciclo de comissão, conciliação bancária e histórico de ciclos.

Ponto de entrada: secao_fechamento(parceiro)."""
from datetime import date, datetime, timedelta
import hashlib
import io

import pandas as pd
import streamlit as st

from db import buscar_todos, supabase

from telas.parceiros.parceiros_comum import (
    _flash,
    _fmt_moeda,
    _fragmento,
    _menu,
    _mostrar_flash,
    _panorama_parceiros,
    _parse_data_segura,
)


CHUNK_IN = 400  # tamanho dos blocos em consultas .in_() (URLs enormes falham no PostgREST)

STATUS_RETIRADA = "Retirada - Parceiros"

@st.cache_data(ttl=60, show_spinner=False)
def _buscar_fechamentos(parceiro_id) -> list:
    return buscar_todos(
        lambda: supabase.table("parcerias_fechamentos")
        .select(
            "id, numero_ciclo, data_inicio, data_fim, percentual_comissao, "
            "valor_total_vendas, valor_total_comissao, status"
        )
        .eq("parceiro_id", parceiro_id)
        .order("numero_ciclo", desc=True)
    )

@st.cache_data(ttl=30, show_spinner=False)
def _vendas_candidatas(canal_id, data_ini: str, data_fim: str) -> list:
    """Vendas do canal no período que ainda não entraram em nenhum fechamento.
    Só consulta os vínculos das vendas do período (antes lia os de TODOS os ciclos,
    com o limite padrão de 1000 linhas, o que truncaria o resultado)."""
    vendas = buscar_todos(
        lambda: supabase.table("vendas")
        .select(
            "id, data_venda, cliente, valor_final, "
            "comissao_percentual, comissao_valor, "
            "produtos(descricao), status_venda(nome)"
        )
        .eq("canal_venda_id", canal_id)
        .gte("data_venda", data_ini)
        .lte("data_venda", data_fim)
        .order("data_venda")
        .order("id")
    )
    ids = [v["id"] for v in vendas]
    incluidos = set()
    for i in range(0, len(ids), CHUNK_IN):
        bloco_ids = ids[i:i + CHUNK_IN]
        incluidos.update(
            r["venda_id"]
            for r in buscar_todos(
                lambda: supabase.table("parcerias_fechamento_vendas")
                .select("venda_id")
                .in_("venda_id", bloco_ids)
                .order("id")
            )
        )
    return [v for v in vendas if v["id"] not in incluidos]

@st.cache_data(ttl=600, show_spinner=False)
def _retiradas_por_fechamento(ids_fechamentos: tuple, canal_id) -> dict:
    """{fechamento_id: valor das retiradas de peças}. Três consultas no total, em vez de
    uma por bloco de ids: vínculos, id do status e só as vendas de retirada do canal."""
    if not ids_fechamentos:
        return {}
    vinculos = buscar_todos(
        lambda: supabase.table("parcerias_fechamento_vendas")
        .select("fechamento_id, venda_id")
        .in_("fechamento_id", list(ids_fechamentos))
        .order("id")
    )
    retirada_por_venda = {}
    status = (
        supabase.table("status_venda").select("id").eq("nome", STATUS_RETIRADA).execute().data or []
    )
    if status and canal_id:
        retiradas = buscar_todos(
            lambda: supabase.table("vendas")
            .select("id, valor_final")
            .eq("canal_venda_id", canal_id)
            .eq("status_id", status[0]["id"])
            .order("id")
        )
        retirada_por_venda = {v["id"]: float(v.get("valor_final") or 0) for v in retiradas}
    total = {fid: 0.0 for fid in ids_fechamentos}
    for v in vinculos:
        total[v["fechamento_id"]] = total.get(v["fechamento_id"], 0.0) + retirada_por_venda.get(v["venda_id"], 0.0)
    return total

@st.cache_data(ttl=600, show_spinner=False)
def _detalhes_ciclo(fechamento_id) -> tuple:
    """(vínculos, vendas) de um ciclo já fechado: não muda, então guarda por mais tempo."""
    vinculos = buscar_todos(
        lambda: supabase.table("parcerias_fechamento_vendas")
        .select("venda_id, valor_venda, percentual_comissao, valor_comissao")
        .eq("fechamento_id", fechamento_id)
        .order("id")
    )
    ids_vendas = [v["venda_id"] for v in vinculos]
    vendas = []
    for i in range(0, len(ids_vendas), CHUNK_IN):
        bloco_ids = ids_vendas[i:i + CHUNK_IN]
        vendas.extend(
            buscar_todos(
                lambda: supabase.table("vendas")
                .select(
                    "id, data_venda, cliente, valor_final, quantidade, "
                    "produtos(descricao, codigo_interno), status_venda(nome)"
                )
                .in_("id", bloco_ids)
                .order("id")
            )
        )
    vendas.sort(key=lambda v: (str(v.get("data_venda") or ""), v["id"]))
    return vinculos, vendas

def _invalidar_cache_fechamento() -> None:
    _buscar_fechamentos.clear()
    _vendas_candidatas.clear()
    _retiradas_por_fechamento.clear()
    _detalhes_ciclo.clear()
    _panorama_parceiros.clear()

@_fragmento
def _secao_parceiro_fechamento(parceiro: dict) -> None:
    """Fluxo de fechamento do parceiro:

    - Sem ciclo aberto: botão para iniciar um novo ciclo. `data_inicio` é
      automática (dia seguinte ao `data_fim` do último ciclo fechado); no
      primeiro ciclo do parceiro, pede a data manualmente.
    - Com ciclo aberto: o usuário escolhe a data final, vê as vendas do
      canal do parceiro nesse período que ainda não entraram em nenhum
      fechamento, pode desmarcar alguma (ela fica disponível para o próximo
      ciclo) e fecha o ciclo. A comissão de cada venda vem do que já está
      gravado nela (`comissao_percentual`/`comissao_valor`), não é
      recalculada aqui.
    """
    _mostrar_flash()
    parceiro_id = parceiro["id"]
    canal_id = parceiro.get("canal_id")

    try:
        fechamentos = _buscar_fechamentos(parceiro_id)
    except Exception as e:
        st.error(f"Não foi possível carregar os fechamentos: {e}")
        return

    ciclo_aberto = next((f for f in fechamentos if f.get("status") == "Aberto"), None)
    ultimo_fechamento = fechamentos[0] if fechamentos else None

    if not canal_id:
        st.warning("Este parceiro não possui um canal de venda vinculado.")
        return

    if not ciclo_aberto:
        proximo_numero = (ultimo_fechamento["numero_ciclo"] + 1) if ultimo_fechamento else 1

        if ultimo_fechamento and ultimo_fechamento.get("data_fim"):
            data_inicio_sugerida = (
                _parse_data_segura(ultimo_fechamento["data_fim"]) + timedelta(days=1)
            )
            st.info(
                f"Próximo ciclo: **#{proximo_numero}**, a partir de "
                f"**{data_inicio_sugerida.strftime('%d/%m/%Y')}** "
                "(dia seguinte ao fim do último ciclo)."
            )
        else:
            data_inicio_sugerida = st.date_input(
                "Data de início do 1º ciclo",
                value=date.today().replace(day=1),
                key="parceiros_fechamento_data_inicio_manual",
            )

        if st.button("▶️ Iniciar novo ciclo", key="parceiros_iniciar_ciclo"):
            try:
                # A lista de parceiros fica em cache por até 30 s; o percentual que será
                # gravado no ciclo é sempre lido na hora.
                atual = (
                    supabase.table("parceiros").select("percentual_comissao")
                    .eq("id", parceiro_id).execute().data or [{}]
                )[0]
                supabase.table("parcerias_fechamentos").insert({
                    "parceiro_id": parceiro_id,
                    "numero_ciclo": proximo_numero,
                    "data_inicio": data_inicio_sugerida.isoformat(),
                    "percentual_comissao": atual.get("percentual_comissao") or 0,
                    "status": "Aberto",
                }).execute()
                _invalidar_cache_fechamento()
                _flash("success", f"Ciclo #{proximo_numero} iniciado.")
                st.rerun()
            except Exception as e:
                _invalidar_cache_fechamento()
                st.error(f"Não foi possível iniciar o ciclo: {e}")

    else:
        data_inicio_ciclo = _parse_data_segura(ciclo_aberto["data_inicio"])

        # Permite ajustar o início do ciclo enquanto ele estiver aberto.
        # Evita sobreposição com o ciclo finalizado anterior.
        anteriores = [
            f for f in fechamentos
            if f.get("id") != ciclo_aberto.get("id")
            and f.get("status") == "Finalizado"
            and f.get("data_fim")
        ]
        ultimo_anterior = max(
            anteriores,
            key=lambda f: f.get("numero_ciclo", 0),
            default=None,
        )
        data_min_inicio = None
        if ultimo_anterior:
            fim_anterior = _parse_data_segura(ultimo_anterior.get("data_fim"))
            if fim_anterior:
                data_min_inicio = fim_anterior + timedelta(days=1)

        if data_inicio_ciclo is None:
            data_inicio_ciclo = data_min_inicio or date.today()
        if data_min_inicio and data_inicio_ciclo < data_min_inicio:
            data_inicio_ciclo = data_min_inicio

        col_inicio, col_fim = st.columns(2)
        with col_inicio:
            novo_inicio_ciclo = st.date_input(
                "Início do ciclo",
                value=data_inicio_ciclo,
                min_value=data_min_inicio,
                key=f"parceiros_fechamento_data_inicio_{ciclo_aberto['id']}",
                help="A data inicial pode ser ajustada enquanto o ciclo estiver aberto.",
            )

        with col_fim:
            data_fim_ciclo = st.date_input(
                "Fechar com vendas até",
                value=max(date.today(), novo_inicio_ciclo),
                min_value=novo_inicio_ciclo,
                key=f"parceiros_fechamento_data_fim_{ciclo_aberto['id']}",
            )

        if novo_inicio_ciclo != data_inicio_ciclo:
            try:
                supabase.table("parcerias_fechamentos").update({
                    "data_inicio": novo_inicio_ciclo.isoformat(),
                }).eq("id", ciclo_aberto["id"]).execute()
                _invalidar_cache_fechamento()
                _flash(
                    "success",
                    f"Início do ciclo #{ciclo_aberto['numero_ciclo']} alterado para "
                    f"{novo_inicio_ciclo.strftime('%d/%m/%Y')}.",
                )
                st.rerun()
            except Exception as e:
                _invalidar_cache_fechamento()
                st.error(f"Não foi possível alterar o início do ciclo: {e}")
                return

        if data_inicio_ciclo and data_fim_ciclo < data_inicio_ciclo:
            st.error("A data final não pode ser antes do início do ciclo.")
            return

        try:
            candidatas = _vendas_candidatas(
                canal_id, data_inicio_ciclo.isoformat(), data_fim_ciclo.isoformat()
            )
        except Exception as e:
            st.error(f"Não foi possível carregar as vendas do período: {e}")
            return

        if not candidatas:
            st.info("Nenhuma venda pendente neste período para incluir no fechamento.")

        # A tabela-resumo fica visualmente acima da tabela de vendas.
        # Usamos um placeholder para preencher o resumo depois de calcular as
        # vendas selecionadas, mantendo a ordem visual desejada.
        resumo_container = st.container()

        linhas_editor = [
            {
                "Incluir": True,
                "id": v["id"],
                "Data": v.get("data_venda"),
                "Cliente": v.get("cliente") or "-",
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Valor": float(v.get("valor_final") or 0),
                "Comissão R$": float(v.get("comissao_valor") or 0),
            }
            for v in candidatas
        ]

        if candidatas:
            editado = st.data_editor(
                linhas_editor,
                key=f"parceiros_fechamento_editor_{ciclo_aberto['id']}",
                use_container_width=True,
                hide_index=True,
                disabled=["id", "Data", "Cliente", "Produto", "Valor", "Comissão R$"],
                column_order=["Incluir", "Data", "Cliente", "Produto", "Valor", "Comissão R$"],
                column_config={
                    "Incluir": st.column_config.CheckboxColumn("Incluir", width="small"),
                    "Data": st.column_config.TextColumn("Data", width="small"),
                    "Cliente": st.column_config.TextColumn("Cliente"),
                    "Produto": st.column_config.TextColumn("Produto"),
                    "Valor": st.column_config.NumberColumn("Valor", format="R$ %.2f", disabled=True),
                    "Comissão R$": st.column_config.NumberColumn("Comissão R$", format="R$ %.2f", disabled=True),
                },
            )
        else:
            editado = []

        selecionadas = [linha for linha in editado if linha["Incluir"]]
        valor_total = sum(float(l.get("Valor") or 0) for l in selecionadas)

        venda_por_id = {v["id"]: v for v in candidatas}
        comissao_total = sum(
            float(linha.get("Comissão R$") or 0)
            for linha in selecionadas
            if (venda_por_id.get(linha["id"], {}).get("status_venda") or {}).get("nome") != "Retirada - Parceiros"
        )

        retiradas_parceiros = [
            venda_por_id[linha["id"]]
            for linha in selecionadas
            if (venda_por_id.get(linha["id"], {}).get("status_venda") or {}).get("nome") == "Retirada - Parceiros"
        ]
        retirada_automatica = sum(float(v.get("valor_final") or 0) for v in retiradas_parceiros)

        # A seção de ajustes foi removida. A linha permanece no resumo para
        # deixar explícita a composição do fechamento, atualmente sem ajustes.
        ajustes_total = 0.0
        valor_final_fechamento = comissao_total - retirada_automatica

        obs_auto_key = f"retirada_auto_obs_{ciclo_aberto['id']}"
        if obs_auto_key not in st.session_state:
            st.session_state[obs_auto_key] = "Retirada - Parceiros"

        # Preenche o resumo antes da tabela de produtos/vendas.
        with resumo_container:
            st.markdown(
                f"##### Resumo do fechamento - de {data_inicio_ciclo.strftime('%d/%m/%Y')} até {data_fim_ciclo.strftime('%d/%m/%Y')}"
            )
            resumo_df = pd.DataFrame([
                {"Descrição": "Vendas no período", "Valor": _fmt_moeda(valor_total), "Observação": ""},
                {"Descrição": "Comissão Total (+)", "Valor": _fmt_moeda(comissao_total), "Observação": ""},
                {"Descrição": "Retirada Peças (-)", "Valor": _fmt_moeda(-retirada_automatica), "Observação": st.session_state.get(obs_auto_key, "")},
                {"Descrição": "Ajustes", "Valor": _fmt_moeda(ajustes_total), "Observação": ""},
                {"Descrição": "Valor Final", "Valor": _fmt_moeda(valor_final_fechamento), "Observação": ""},
            ])
            styled_resumo = resumo_df.style.apply(
                lambda row: [
                    "font-weight: 700; background-color: rgba(0, 123, 255, 0.16);"
                    if row["Descrição"] == "Valor Final" else ""
                    for _ in row
                ],
                axis=1,
            )
            st.dataframe(styled_resumo, use_container_width=True, hide_index=True)

        if candidatas:
            st.caption(
                "Desmarque alguma venda para deixá-la de fora deste ciclo — ela "
                "continua disponível para um fechamento futuro."
            )

        # Informações/KPIs do ciclo aberto ficam abaixo da tabela de vendas.
        st.info(
            f"Ciclo **#{ciclo_aberto['numero_ciclo']}** aberto desde "
            f"**{data_inicio_ciclo.strftime('%d/%m/%Y')}**."
        )

        # Os componentes visuais de retirada de peças e os KPIs
        # foram removidos. Os valores continuam sendo calculados acima
        # porque são utilizados no fechamento do ciclo.

        if st.button(
            f"✅ Fechar ciclo #{ciclo_aberto['numero_ciclo']}",
            type="primary",
            disabled=not selecionadas,
            key="parceiros_fechar_ciclo",
        ):
            try:
                venda_por_id = {v["id"]: v for v in candidatas}
                registros = []
                for linha in selecionadas:
                    venda = venda_por_id[linha["id"]]
                    eh_retirada_parceiro = (
                        (venda.get("status_venda") or {}).get("nome")
                        == "Retirada - Parceiros"
                    )
                    registros.append({
                        "fechamento_id": ciclo_aberto["id"],
                        "venda_id": venda["id"],
                        "valor_venda": float(venda.get("valor_final") or 0),
                        "percentual_comissao": 0
                        if eh_retirada_parceiro
                        else float(venda.get("comissao_percentual") or 0),
                        "valor_comissao": 0
                        if eh_retirada_parceiro
                        else float(venda.get("comissao_valor") or 0),
                    })
                if registros:
                    supabase.table("parcerias_fechamento_vendas").insert(registros).execute()

                supabase.table("parcerias_fechamentos").update({
                    "data_fim": data_fim_ciclo.isoformat(),
                    "valor_total_vendas": valor_total,
                    "valor_total_comissao": comissao_total,
                    "status": "Finalizado",
                    "fechado_em": datetime.now().isoformat(),
                }).eq("id", ciclo_aberto["id"]).execute()

                _invalidar_cache_fechamento()
                _flash(
                    "success",
                    f"Ciclo #{ciclo_aberto['numero_ciclo']} fechado com "
                    f"{len(registros)} venda(s).",
                )
                st.rerun()
            except Exception as e:
                _invalidar_cache_fechamento()
                st.error(f"Não foi possível fechar o ciclo: {e}")

@_fragmento
def _secao_historico_comissoes(parceiro: dict) -> None:
    """Histórico dos ciclos de comissão do parceiro. Clicar em um ciclo mostra o detalhe,
    somente para consulta (as vendas do ciclo, a comissão e a retirada de peças)."""
    parceiro_id = parceiro["id"]
    canal_id = parceiro.get("canal_id")
    try:
        fechamentos = _buscar_fechamentos(parceiro_id)
    except Exception as e:
        st.error(f"Não foi possível carregar os ciclos de comissão: {e}")
        return

    st.markdown("##### Histórico de ciclos")
    if not fechamentos:
        st.caption("Nenhum ciclo de fechamento registrado ainda para este parceiro.")
        return

    # Valor final de cada ciclo histórico = comissão - retiradas de peças (mesma regra do
    # fechamento). As retiradas vêm de 3 consultas guardadas em cache.
    valor_final_por_fechamento = {}
    try:
        ids_fechamentos = tuple(f["id"] for f in fechamentos if f.get("id") is not None)
        retiradas = _retiradas_por_fechamento(ids_fechamentos, canal_id)
        for fechamento in fechamentos:
            fechamento_id = fechamento.get("id")
            valor_final_por_fechamento[fechamento_id] = (
                float(fechamento.get("valor_total_comissao") or 0) - retiradas.get(fechamento_id, 0.0)
            )
    except Exception:
        # Sem os vínculos, mostra a comissão como alternativa para não esconder o histórico.
        for fechamento in fechamentos:
            valor_final_por_fechamento[fechamento.get("id")] = float(
                fechamento.get("valor_total_comissao") or 0
            )

    linhas_fechamento = [
        {
            "Ciclo": f["numero_ciclo"],
            "Início": f.get("data_inicio"),
            "Fim": f.get("data_fim") or "-",
            "Status": f.get("status"),
            "Comissão %": f.get("percentual_comissao"),
            "Valor vendas": _fmt_moeda(f.get("valor_total_vendas")),
            "Valor comissão": _fmt_moeda(f.get("valor_total_comissao")),
            "Valor final": _fmt_moeda(valor_final_por_fechamento.get(f.get("id"), 0)),
        }
        for f in fechamentos
    ]

    selecao_historico = st.dataframe(
        linhas_fechamento,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"historico_ciclos_{parceiro_id}",
    )

    # Ao clicar em um ciclo finalizado, abre a mesma visão do fechamento,
    # porém somente para consulta: sem campos editáveis e sem botão de fechar.
    linhas_selecionadas = selecao_historico.selection.rows
    if linhas_selecionadas:
        ciclo_visualizado = fechamentos[linhas_selecionadas[0]]

        try:
            vinculos, vendas_fechamento = _detalhes_ciclo(ciclo_visualizado["id"])
        except Exception as e:
            st.error(f"Não foi possível carregar os detalhes do ciclo: {e}")
            return

        vinculo_por_venda = {v["venda_id"]: v for v in vinculos}
        retirada_visualizacao = sum(
            float(v.get("valor_final") or 0)
            for v in vendas_fechamento
            if (v.get("status_venda") or {}).get("nome") == "Retirada - Parceiros"
        )
        comissao_visualizacao = sum(
            float(vinculo_por_venda.get(v["id"], {}).get("valor_comissao") or 0)
            for v in vendas_fechamento
            if (v.get("status_venda") or {}).get("nome") != "Retirada - Parceiros"
        )
        valor_vendas_visualizacao = sum(
            float(v.get("valor_final") or 0) for v in vendas_fechamento
        )
        ajustes_visualizacao = 0.0
        valor_final_visualizacao = comissao_visualizacao - retirada_visualizacao

        st.divider()
        inicio_visualizacao = _parse_data_segura(ciclo_visualizado.get("data_inicio"))
        fim_visualizacao = _parse_data_segura(ciclo_visualizado.get("data_fim"))
        inicio_txt = inicio_visualizacao.strftime("%d/%m/%Y") if inicio_visualizacao else str(ciclo_visualizado.get("data_inicio") or "-")
        fim_txt = fim_visualizacao.strftime("%d/%m/%Y") if fim_visualizacao else str(ciclo_visualizado.get("data_fim") or "-")

        st.markdown(
            f"##### Visualização do ciclo #{ciclo_visualizado['numero_ciclo']} - "
            f"de {inicio_txt} até {fim_txt}"
        )

        resumo_visualizacao = pd.DataFrame([
            {"Descrição": "Vendas no período", "Valor": _fmt_moeda(valor_vendas_visualizacao), "Observação": ""},
            {"Descrição": "Comissão Total (+)", "Valor": _fmt_moeda(comissao_visualizacao), "Observação": ""},
            {"Descrição": "Retirada Peças (-)", "Valor": _fmt_moeda(-retirada_visualizacao), "Observação": "Retirada - Parceiros" if retirada_visualizacao else ""},
            {"Descrição": "Ajustes", "Valor": _fmt_moeda(ajustes_visualizacao), "Observação": ""},
            {"Descrição": "Valor Final", "Valor": _fmt_moeda(valor_final_visualizacao), "Observação": ""},
        ])
        styled_visualizacao = resumo_visualizacao.style.apply(
            lambda row: [
                "font-weight: 700; background-color: rgba(0, 123, 255, 0.16);"
                if row["Descrição"] == "Valor Final" else ""
                for _ in row
            ],
            axis=1,
        )
        st.dataframe(styled_visualizacao, use_container_width=True, hide_index=True)

        linhas_vendas_visualizacao = [
            {
                "Data": v.get("data_venda"),
                "Cliente": v.get("cliente") or "-",
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Código": (v.get("produtos") or {}).get("codigo_interno") or "-",
                "Qtd": v.get("quantidade"),
                "Valor": float(v.get("valor_final") or 0),
                "Comissão R$": float(vinculo_por_venda.get(v["id"], {}).get("valor_comissao") or 0),
                "Status": (v.get("status_venda") or {}).get("nome") or "-",
            }
            for v in vendas_fechamento
        ]

        if linhas_vendas_visualizacao:
            st.dataframe(
                linhas_vendas_visualizacao,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Este ciclo não possui vendas vinculadas.")


CONC_TOL = 0.01          # tolerância de centavos


def _usuario_atual() -> str:
    u = st.session_state.get("user")
    return getattr(u, "email", None) or "desconhecido"


def _parse_valor(x) -> float:
    """Aceita 1234.56, '1.234,56', '-1.234,56'."""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _conc_contas():
    return (supabase.table("contas_bancarias").select("id, nome")
            .eq("ativo", True).order("nome").execute().data or [])


def _conc_formas() -> dict:
    rows = (supabase.table("formas_pagamento")
            .select("id, descricao, tipo_conciliacao, taxa_percentual, prazo_dias").execute().data or [])
    return {r["id"]: r for r in rows}


def _conc_vendas_pendentes(canal_id: int, formas: dict) -> pd.DataFrame:
    rows = buscar_todos(
        lambda: supabase.table("vendas")
        .select("id, produto_id, quantidade, valor_final, data_venda, cliente, cliente_id, "
                "forma_pagamento_id, status_venda(nome)")
        .eq("canal_venda_id", canal_id).eq("status_conciliacao", "Pendente")
        .order("data_venda").order("id")
    )
    # "Retirada - Parceiros" é peça que o parceiro levou e foi abatida da comissão: não há dinheiro a receber
    rows = [r for r in rows if (r.get("status_venda") or {}).get("nome") != STATUS_RETIRADA]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop(columns=["status_venda"])
    df["data_venda"] = pd.to_datetime(df["data_venda"]).dt.date
    df["valor_final"] = df["valor_final"].astype(float)
    f = lambda i, k, d: (formas.get(i) or {}).get(k, d)
    df["forma"] = df["forma_pagamento_id"].map(lambda i: f(i, "descricao", "-"))
    df["tipo"] = df["forma_pagamento_id"].map(lambda i: f(i, "tipo_conciliacao", "pix"))
    df["taxa"] = df["forma_pagamento_id"].map(lambda i: float(f(i, "taxa_percentual", 0)))
    df["prazo"] = df["forma_pagamento_id"].map(lambda i: int(f(i, "prazo_dias", 2)))
    df["liquido"] = (df["valor_final"] * (1 - df["taxa"] / 100)).round(2)
    return df


def _conc_lancamentos(conta_id: int, creditos: bool) -> pd.DataFrame:
    def _consulta():
        q = supabase.table("v_lancamentos_saldo").select("*").eq("conta_id", conta_id)
        q = q.gt("valor", 0) if creditos else q.lt("valor", 0)
        return q.order("data_lancamento", desc=True).order("id")

    rows = buscar_todos(_consulta)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["data_lancamento"] = pd.to_datetime(df["data_lancamento"]).dt.date
    df["valor"] = df["valor"].astype(float)
    if creditos:
        df["livre"] = (df["valor"] - df["alocado_vendas"].astype(float)).round(2)
        df = df[df["livre"] > CONC_TOL]
    else:
        df = df[~df["usado_pagamento"]]
    return df.reset_index(drop=True)


def _conc_sugestoes(vendas: pd.DataFrame, creditos: pd.DataFrame) -> list:
    """Retorna [{'credito': Series, 'vendas': DataFrame, 'regra': str}]."""
    sugestoes, usados_v, usados_c = [], set(), set()
    if vendas.empty or creditos.empty:
        return sugestoes

    # PIX: vendas do mesmo cliente e dia = 1 crédito
    pix = vendas[vendas["tipo"] == "pix"].copy()
    if not pix.empty:
        pix["chave"] = pix.apply(
            lambda r: str(r["cliente_id"]) if pd.notna(r["cliente_id"]) else str(r["cliente"] or ""), axis=1)
        for (d, _), g in pix.groupby(["data_venda", "chave"]):
            alvo = g["liquido"].sum()
            for _, c in creditos.iterrows():
                if c["id"] in usados_c:
                    continue
                if 0 <= (c["data_lancamento"] - d).days <= 5 and abs(c["livre"] - alvo) <= CONC_TOL:
                    sugestoes.append({"credito": c, "vendas": g, "regra": "PIX: valor exato"})
                    usados_c.add(c["id"])
                    usados_v.update(g["id"])
                    break

    # Cartão: crédito da adquirente = soma consecutiva de vendas líquidas de taxa
    cartao = vendas[vendas["tipo"] == "cartao"].sort_values("data_venda")
    for _, c in creditos.iterrows():
        if c["id"] in usados_c:
            continue
        cand = cartao[(~cartao["id"].isin(usados_v))
                      & (cartao["data_venda"] <= c["data_lancamento"])
                      & (cartao["data_venda"] >= c["data_lancamento"] - timedelta(days=35))]
        linhas = list(cand.itertuples(index=False))
        achou = None
        for i in range(len(linhas)):
            soma = 0.0
            for j in range(i, len(linhas)):
                soma += linhas[j].liquido
                tol = CONC_TOL + 0.02 * (j - i + 1)
                if abs(soma - c["livre"]) <= tol:
                    achou = [x.id for x in linhas[i:j + 1]]
                    break
                if soma > c["livre"] + tol:
                    break
            if achou:
                break
        if achou:
            sugestoes.append({"credito": c, "vendas": cartao[cartao["id"].isin(achou)],
                              "regra": "Cartão: soma líquida de taxa"})
            usados_c.add(c["id"])
            usados_v.update(achou)
    return sugestoes


def _conc_confirmar_baixa(vendas_sel: pd.DataFrame, lancamento_id, valor_credito: float):
    """Grava a ligação venda x lançamento. lancamento_id=None => baixa em caixa (dinheiro)."""
    total = float(vendas_sel["valor_final"].sum())
    caixa = lancamento_id is None
    linhas = []
    for _, v in vendas_sel.iterrows():
        alocado = v["valor_final"] if caixa else round(valor_credito * v["valor_final"] / total, 2)
        linhas.append({
            "venda_id": int(v["id"]),
            "lancamento_id": None if caixa else int(lancamento_id),
            "tipo_baixa": "caixa" if caixa else "banco",
            "valor_bruto_venda": float(v["valor_final"]),
            "valor_alocado": float(alocado),
            "taxa_valor": 0 if caixa else round(float(v["valor_final"]) - alocado, 2),
            "conciliado_por": _usuario_atual(),
        })
    supabase.table("conciliacao_vendas").insert(linhas).execute()
    supabase.table("vendas").update({"status_conciliacao": "Conciliada"}) \
        .in_("id", [int(i) for i in vendas_sel["id"]]).execute()


def _conc_aba_importar(contas):
    st.subheader("Importar extrato")
    if not contas:
        st.warning("Cadastre uma conta em `contas_bancarias` antes de importar.")
        return
    conta = st.selectbox("Conta", contas, format_func=lambda c: c["nome"], key="parceiros_conc_imp_conta")
    arq = st.file_uploader("Extrato (OFX ou CSV)", type=["ofx", "csv"])
    if not arq:
        return

    lanc = []
    if arq.name.lower().endswith(".ofx"):
        try:
            from ofxparse import OfxParser
        except ImportError:
            st.error("Instale o parser: `pip install ofxparse`")
            return
        ofx = OfxParser.parse(io.BytesIO(arq.read()))
        for acc in ([ofx.account] if getattr(ofx, "account", None) else []):
            for t in acc.statement.transactions:
                lanc.append({"conta_id": conta["id"], "data_lancamento": t.date.date().isoformat(),
                             "descricao": (t.memo or t.payee or "").strip(), "valor": float(t.amount),
                             "id_externo": str(t.id)})
    else:
        df = pd.read_csv(arq, sep=None, engine="python", dtype=str)
        cols = list(df.columns)
        c1, c2, c3 = st.columns(3)
        col_data = c1.selectbox("Coluna de data", cols)
        col_desc = c2.selectbox("Coluna de descrição", cols, index=min(1, len(cols) - 1))
        col_val = c3.selectbox("Coluna de valor", cols, index=min(2, len(cols) - 1))
        vistos = {}
        for _, r in df.iterrows():
            try:
                d = pd.to_datetime(r[col_data], dayfirst=True).date().isoformat()
                v = _parse_valor(r[col_val])
            except Exception:
                continue
            desc = str(r[col_desc] or "").strip()
            base = f"{d}|{desc}|{v:.2f}"
            vistos[base] = vistos.get(base, 0) + 1   # mesma linha repetida no dia continua única
            h = hashlib.sha1(f"{base}|{vistos[base]}".encode()).hexdigest()
            lanc.append({"conta_id": conta["id"], "data_lancamento": d, "descricao": desc,
                         "valor": v, "id_externo": h})

    if not lanc:
        st.warning("Nenhum lançamento lido.")
        return
    st.dataframe(pd.DataFrame(lanc).drop(columns=["conta_id", "id_externo"]).head(50), use_container_width=True)
    st.caption(f"{len(lanc)} lançamentos lidos (mostrando até 50).")
    if st.button("Importar", type="primary"):
        supabase.table("lancamentos_bancarios").upsert(
            lanc, on_conflict="conta_id,id_externo", ignore_duplicates=True).execute()
        st.success("Extrato importado. Lançamentos já existentes foram ignorados.")


def _conc_aba_recebimentos(parceiro, conta, formas):
    vendas = _conc_vendas_pendentes(parceiro["canal_id"], formas)
    creditos = _conc_lancamentos(conta["id"], creditos=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Vendas pendentes", 0 if vendas.empty else len(vendas))
    c2.metric("Valor a conciliar", _fmt_moeda(0 if vendas.empty else vendas["valor_final"].sum()))
    c3.metric("Créditos livres no extrato", _fmt_moeda(0 if creditos.empty else creditos["livre"].sum()))

    if vendas.empty:
        st.success("Todas as vendas deste parceiro estão conciliadas.")
        return

    # --- sugestões automáticas
    st.subheader("Sugestões automáticas")
    sugestoes = _conc_sugestoes(vendas, creditos)
    if not sugestoes:
        st.info("Nenhuma sugestão automática. Use o casamento manual abaixo.")
    for i, s in enumerate(sugestoes):
        c, g = s["credito"], s["vendas"]
        with st.container(border=True):
            st.markdown(f"**{s['regra']}** · crédito de **{_fmt_moeda(c['livre'])}** em "
                        f"{c['data_lancamento']:%d/%m/%Y} · _{c['descricao'] or '-'}_")
            st.dataframe(g[["id", "data_venda", "cliente", "forma", "valor_final", "liquido"]],
                         use_container_width=True, hide_index=True)
            if st.button("✅ Confirmar", key=f"parceiros_conc_sug_{i}_{c['id']}"):
                _conc_confirmar_baixa(g, c["id"], c["livre"])
                st.toast("Conciliado!", icon="✅")
                st.rerun()

    # --- manual
    st.subheader("Casamento manual")
    rotulo = lambda i: (lambda r: f"#{r.id} | {r.data_venda:%d/%m} | {r.cliente or '-'} | {r.forma} | "
                                  f"{_fmt_moeda(r.valor_final)}")(vendas.loc[i])
    sel_idx = st.multiselect("Vendas", vendas.index.tolist(), format_func=rotulo)
    if not sel_idx:
        return
    sel = vendas.loc[sel_idx]
    esperado = float(sel["liquido"].sum())
    bruto = float(sel["valor_final"].sum())

    opcoes = ["__caixa__"] + (creditos["id"].tolist() if not creditos.empty else [])
    def rot_cred(x):
        if x == "__caixa__":
            return "💵 Dinheiro em caixa (sem lançamento bancário)"
        r = creditos[creditos["id"] == x].iloc[0]
        return f"{r['data_lancamento']:%d/%m/%Y} | {_fmt_moeda(r['livre'])} | {r['descricao'] or '-'}"
    escolha = st.selectbox("Lançamento bancário", opcoes, format_func=rot_cred)

    if escolha == "__caixa__":
        st.caption(f"Total das vendas: {_fmt_moeda(bruto)}. Use para dinheiro recebido em mãos ou depositado em lote "
                   "que você não queira casar com o extrato.")
        if st.button("Baixar em caixa", type="primary"):
            _conc_confirmar_baixa(sel, None, bruto)
            st.rerun()
    else:
        livre = float(creditos[creditos["id"] == escolha].iloc[0]["livre"])
        valor = st.number_input("Valor do crédito a alocar", value=livre, min_value=0.0, step=0.01)
        dif = round(valor - esperado, 2)
        a, b, d = st.columns(3)
        a.metric("Esperado (líquido de taxa)", _fmt_moeda(esperado))
        b.metric("Alocado", _fmt_moeda(valor))
        d.metric("Diferença", _fmt_moeda(dif))
        if abs(dif) > max(CONC_TOL, 0.02 * len(sel)):
            st.warning("Diferença acima da tolerância. Confirme só se for tarifa ou taxa não cadastrada.")
        if st.button("Confirmar conciliação", type="primary"):
            _conc_confirmar_baixa(sel, escolha, valor)
            st.rerun()


def _conc_aba_comissoes(parceiro, conta):
    st.subheader("Pagamento de comissão ao parceiro")
    fechs = (supabase.table("parcerias_fechamentos")
             .select("id, numero_ciclo, data_inicio, data_fim, valor_total_vendas, valor_total_comissao, observacao")
             .eq("parceiro_id", parceiro["id"]).eq("status_pagamento", "A pagar")
             .eq("status", "Finalizado").order("numero_ciclo").execute().data or [])
    if not fechs:
        st.success("Nenhuma comissão a pagar em fechamentos encerrados.")
        return
    debitos = _conc_lancamentos(conta["id"], creditos=False)

    f = st.selectbox("Fechamento", fechs, format_func=lambda x: (
        f"Ciclo {x['numero_ciclo']} | {x['data_inicio']} a {x['data_fim']} | comissão {_fmt_moeda(x['valor_total_comissao'])}"))
    # mesma regra da tela de fechamento: valor final = comissão - retiradas de peças
    retirada = _retiradas_por_fechamento((f["id"],), parceiro.get("canal_id")).get(f["id"], 0.0)
    devida = round(float(f["valor_total_comissao"]) - retirada, 2)
    st.caption(f"Comissão {_fmt_moeda(f['valor_total_comissao'])} − retirada de peças {_fmt_moeda(retirada)} = **{_fmt_moeda(devida)}** a pagar")
    if devida <= 0:
        st.info("Valor final zero ou negativo: o parceiro não tem comissão a receber neste ciclo "
                "(se for negativo, é ele quem deve a diferença). Esse caso ainda não é conciliado aqui.")
        return
    if debitos.empty:
        st.info("Sem débitos livres no extrato desta conta.")
        return
    # ordena pelos débitos mais próximos do valor devido
    debitos = debitos.assign(dist=(debitos["valor"].abs() - devida).abs()).sort_values("dist")
    d = st.selectbox("Débito no extrato", debitos["id"].tolist(), format_func=lambda x: (
        lambda r: f"{r['data_lancamento']:%d/%m/%Y} | {_fmt_moeda(abs(r['valor']))} | {r['descricao'] or '-'}")(
        debitos[debitos["id"] == x].iloc[0]))
    pago = abs(float(debitos[debitos["id"] == d].iloc[0]["valor"]))
    dif = round(pago - devida, 2)
    a, b, c = st.columns(3)
    a.metric("Comissão devida", _fmt_moeda(devida))
    b.metric("Pago", _fmt_moeda(pago))
    c.metric("Diferença", _fmt_moeda(dif))

    motivo = ""
    if abs(dif) > CONC_TOL:
        motivo = st.text_input("Justificativa da diferença (obrigatória)")
    if st.button("Confirmar pagamento", type="primary", disabled=abs(dif) > CONC_TOL and not motivo.strip()):
        lanc = debitos[debitos["id"] == d].iloc[0]
        obs = (f["observacao"] + " | " if f.get("observacao") else "") + \
              (f"Dif. pagamento {_fmt_moeda(dif)}: {motivo.strip()}" if motivo.strip() else "")
        supabase.table("parcerias_fechamentos").update({
            "status_pagamento": "Pago", "lancamento_pagamento_id": int(d),
            "pago_em": lanc["data_lancamento"].isoformat(), "observacao": obs or None,
        }).eq("id", f["id"]).execute()
        st.rerun()


def _conc_aba_divergencias(parceiro, conta, formas):
    st.subheader("Divergências")
    hoje = date.today()

    # R01: venda sem crédito após o prazo da forma de pagamento
    vendas = _conc_vendas_pendentes(parceiro["canal_id"], formas)
    st.markdown("**R01 · Venda sem crédito após o prazo** (severidade alta)")
    if vendas.empty:
        st.success("Nada pendente.")
    else:
        atrasadas = vendas[vendas.apply(lambda r: r["data_venda"] + timedelta(days=int(r["prazo"])) < hoje, axis=1)]
        if atrasadas.empty:
            st.success("Nenhuma venda além do prazo.")
        else:
            atrasadas = atrasadas.assign(dias_atraso=atrasadas.apply(
                lambda r: (hoje - r["data_venda"]).days - int(r["prazo"]), axis=1))
            st.error(f"{len(atrasadas)} venda(s), {_fmt_moeda(atrasadas['valor_final'].sum())} em atraso.")
            st.dataframe(atrasadas[["id", "data_venda", "cliente", "forma", "valor_final", "dias_atraso"]],
                         use_container_width=True, hide_index=True)

    # R04: crédito sem venda
    st.markdown("**R04 · Crédito sem venda correspondente** (severidade média)")
    cred = _conc_lancamentos(conta["id"], creditos=True)
    if cred.empty:
        st.success("Todos os créditos estão alocados.")
    else:
        st.warning("Inclui qualquer entrada que não seja venda de parceria (outras receitas, aportes etc.).")
        st.dataframe(cred[["id", "data_lancamento", "descricao", "valor", "livre"]],
                     use_container_width=True, hide_index=True)

    # R06: totais e comissão dos fechamentos
    st.markdown("**R06 · Totais do fechamento x itens e comissão recalculada** (severidade alta)")
    conf = pd.DataFrame(supabase.table("v_conferencia_fechamentos").select("*")
                        .eq("parceiro_id", parceiro["id"]).execute().data or [])
    if conf.empty:
        st.info("Sem fechamentos.")
    else:
        for col in ["valor_total_vendas", "soma_vendas", "valor_total_comissao", "soma_comissao",
                    "comissao_recalculada"]:
            conf[col] = conf[col].astype(float)
        conf["dif_vendas"] = (conf["valor_total_vendas"] - conf["soma_vendas"]).round(2)
        conf["dif_comissao"] = (conf["valor_total_comissao"] - conf["soma_comissao"]).round(2)
        conf["dif_recalculo"] = (conf["soma_comissao"] - conf["comissao_recalculada"]).round(2)
        bad = conf[(conf[["dif_vendas", "dif_comissao", "dif_recalculo"]].abs() > CONC_TOL).any(axis=1)]
        if bad.empty:
            st.success("Fechamentos consistentes.")
        else:
            st.error(f"{len(bad)} fechamento(s) com diferença.")
            st.dataframe(bad[["fechamento_id", "numero_ciclo", "dif_vendas", "dif_comissao", "dif_recalculo"]],
                         use_container_width=True, hide_index=True)

    # R07: comissão fechada e não paga
    st.markdown("**R07 · Comissão fechada e não paga** (severidade média)")
    dias = st.number_input("Considerar atraso após (dias)", min_value=1, value=10, step=1)
    pend = pd.DataFrame(supabase.table("parcerias_fechamentos")
                        .select("id, numero_ciclo, fechado_em, valor_total_comissao")
                        .eq("parceiro_id", parceiro["id"]).eq("status_pagamento", "A pagar")
                        .eq("status", "Finalizado").execute().data or [])
    if pend.empty:
        st.success("Sem comissões pendentes.")
    else:
        pend["fechado_em"] = pd.to_datetime(pend["fechado_em"]).dt.date
        pend["dias"] = pend["fechado_em"].map(lambda d: (hoje - d).days)
        atras = pend[pend["dias"] > dias]
        if atras.empty:
            st.success("Nenhuma comissão em atraso.")
        else:
            st.warning(f"{len(atras)} comissão(ões) em atraso.")
            st.dataframe(atras, use_container_width=True, hide_index=True)

CONC_ABAS = ["importar", "recebimentos", "comissoes", "divergencias"]
CONC_ROTULOS = {
    "importar": "📥 Importar extrato",
    "recebimentos": "💰 Recebimentos",
    "comissoes": "🤝 Pagamento de comissão",
    "divergencias": "⚠️ Divergências",
}


def _secao_conciliacao(parceiro: dict) -> None:
    """Conciliação bancária do parceiro: vendas x créditos no extrato e comissão x débito.
    Usa menu (e não st.tabs) para só consultar o que estiver aberto."""
    if not parceiro.get("canal_id"):
        st.warning("Este parceiro não possui um canal de venda vinculado.")
        return
    try:
        contas = _conc_contas()
        conta = st.selectbox(
            "Conta bancária", contas, format_func=lambda c: c["nome"], key="parceiros_conc_conta",
        ) if contas else None
        aba = _menu("Conciliação", CONC_ABAS, key="parceiros_conc_aba", formatar=CONC_ROTULOS.get)

        if aba == "importar":
            _conc_aba_importar(contas)
        elif not conta:
            st.warning("Cadastre uma conta em `contas_bancarias` para conciliar.")
        elif aba == "recebimentos":
            _conc_aba_recebimentos(parceiro, conta, _conc_formas())
        elif aba == "comissoes":
            _conc_aba_comissoes(parceiro, conta)
        else:
            _conc_aba_divergencias(parceiro, conta, _conc_formas())
    except Exception as e:
        st.error(f"Erro na conciliação: {e}")


FECH_SUBSECOES = ["ciclo", "conciliacao", "historico"]
FECH_ROTULOS = {
    "ciclo": "📑 Ciclo atual",
    "conciliacao": "🧾 Conciliação bancária",
    "historico": "🗂️ Histórico de ciclos",
}


def secao_fechamento(parceiro: dict) -> None:
    """Grupo 'Fechamento' do parceiro: ciclo de comissão, conciliação bancária e histórico."""
    sub = _menu("Fechamento", FECH_SUBSECOES, key="parceiros_fech_sub", formatar=FECH_ROTULOS.get)
    if sub == "ciclo":
        _secao_parceiro_fechamento(parceiro)
    elif sub == "conciliacao":
        _mostrar_flash()
        _secao_conciliacao(parceiro)
    else:
        _mostrar_flash()
        _secao_historico_comissoes(parceiro)
