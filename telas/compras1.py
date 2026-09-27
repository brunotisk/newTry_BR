import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
from db import supabase
from telas.importar_nf import tela_importar_nf
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao
from servicos.arquivos_compra import (
    upload_arquivo_compra,
    listar_arquivos_compra,
    gerar_url_arquivo_compra,
    baixar_arquivo_compra,
    excluir_arquivo_compra,
    nome_arquivo_compra,
)

# Compatibilidade: st.dialog é o nome estável (Streamlit >= 1.31); versões
# um pouco mais antigas ainda expõem a mesma coisa como st.experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog


def _fmt_numero(valor) -> str:
    """Formata um número no padrão BR (1.234,56), sem o prefixo 'R$ '."""
    return (
        f"{float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _fmt_moeda(valor) -> str:
    return f"R$ {_fmt_numero(valor)}"


def _parse_valor_input(valor, padrao=0.0):
    """Converte valor digitado em formato BR/US para float."""
    if valor is None:
        return float(padrao)
    texto = str(valor).strip()
    if not texto:
        return 0.0
    try:
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        return max(0.0, float(texto))
    except (TypeError, ValueError):
        return float(padrao)


def _fmt_valor_input(valor):
    return f"{float(valor or 0):.2f}".replace(".", ",")


def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 1 em vez de 1.000)."""
    return f"{float(valor or 0):g}"


def _badge_clipe_arquivos() -> str:
    """Indicador de 'NF possui arquivo anexado': um ícone de clipe discreto
    (SVG, sem fundo/caixa) ao lado do número da NF — mais limpo do que o
    emoji dentro de um selo colorido."""
    return (
        '<span title="Esta NF possui arquivos anexados." '
        'style="display:inline-flex;align-items:center;margin-left:6px;'
        'vertical-align:middle;color:#7C93C4;">'
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
        'stroke-linejoin="round">'
        '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66'
        'l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>'
        '</svg>'
        '</span>'
    )


def _badge_tipo_arquivo(tipo: str) -> str:
    """Selo colorido para o tipo do arquivo (PDF/XML) na lista de anexados."""
    tipo_norm = (tipo or "-").upper()
    cor_texto, cor_fundo = {
        "PDF": ("#f87171", "rgba(239,68,68,.14)"),
        "XML": ("#60a5fa", "rgba(59,130,246,.14)"),
    }.get(tipo_norm, ("#9ca3af", "rgba(156,163,175,.14)"))
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
        f'background:{cor_fundo};color:{cor_texto};font-size:12px;font-weight:700;'
        f'letter-spacing:.02em;">{tipo_norm}</span>'
    )


@_dialog("📦 Itens da compra", width="large")
def _dialog_itens_compra(compra: dict, produto_id: int | None = None):
    # Alarga o popup para acomodar a visualização detalhada
    st.markdown(
        """
        <style>
        div[data-testid="stDialog"] > div {
            max-width: 1180px !important;
            width: 94vw !important;
        }
        .btn-popover-desc div[data-testid="stPopover"] button {
            padding: 2px 4px !important;
            min-height: 26px !important;
            height: 26px !important;
            border: 1px solid rgba(255, 255, 255, 0.2) !important;
            background: transparent !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            border-radius: 4px !important;
        }
        .btn-popover-desc div[data-testid="stPopover"] button svg {
            display: none !important;
        }
        .btn-popover-desc div[data-testid="stPopover"] button p {
            margin: 0 !important;
            padding: 0 !important;
            font-size: 0.95rem !important;
            line-height: 1 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    compra_id = compra["id"]

    # Consulta os dados mais recentes da compra para ter pct_desconto_item atualizado
    try:
        compra_resp = (
            supabase.table("compras")
            .select("id, numero_nf, chave_acesso, pct_desconto_item")
            .eq("id", compra_id)
            .single()
            .execute()
        )
        compra_dados = compra_resp.data or compra
    except Exception:
        compra_dados = compra

    pct_desconto = float(compra_dados.get("pct_desconto_item") or 0.0)

    try:
        response = (
            supabase.table("compras_itens")
            .select(
                "id, numero_item, produto_id, quantidade, valor_unitario, valor_desconto, valor_total, valor_unit_ajustado,"
                " produtos(id, codigo_interno, descricao)"
            )
            .eq("compra_id", compra_id)
            .order("numero_item")
            .execute()
        )
        itens = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar itens da compra: {e}")
        return

    if not itens:
        st.info("Nenhum item encontrado para essa compra.")
        return

    if produto_id is not None:
        itens_relacionados = [
            item for item in itens
            if (item.get("produtos") or {}).get("id") == produto_id
        ]
        if itens_relacionados:
            itens = itens_relacionados
            st.caption("Item relacionado à movimentação de estoque")

    # Verifica se há valores ajustados preenchidos ou se a visualização está ativa
    itens_com_ajuste = any(float(item.get("valor_unit_ajustado") or 0) > 0 for item in itens)
    chave_vis = f"visualizar_ajuste_{compra_id}"
    exibir_ajustado = itens_com_ajuste or st.session_state.get(chave_vis, False)

    # Linha superior: Subtítulo à esquerda e botões de ação à direita
    col_sub, col_botoes = st.columns([1.3, 1.7], vertical_alignment="center")

    with col_sub:
        rotulo_sub = f"NF nº {compra_dados.get('numero_nf') or '-'} — Chave: {compra_dados.get('chave_acesso') or '-'}"
        if pct_desconto > 0:
            rotulo_sub += f" | **Desconto:** {pct_desconto:g}%"
        st.caption(rotulo_sub)

    with col_botoes:
        if pct_desconto > 0:
            # Regra: Vlr. Unit. Ajust. - (Vlr. Unit. Ajust. * pct_desconto_item) = Vlr. Unit.
            # Ou seja: Vlr. Unit. = Vlr. Unit. Ajust. * (1 - pct_desconto_item)
            # Logo:    Vlr. Unit. Ajust. = Vlr. Unit. / (1 - pct_desconto_item)
            fator_desc = (pct_desconto / 100.0) if pct_desconto > 1.0 else pct_desconto
            complemento = 1 - fator_desc
            rotulo_pct_fmt = f"{pct_desconto:g}%" if pct_desconto > 1.0 else f"{pct_desconto * 100:g}%"

            if exibir_ajustado:
                b1, b2 = st.columns([1, 1], gap="small", vertical_alignment="center")
                if b1.button(
                    "🔍 Visualizar preço desconto",
                    key=f"btn_vis_desc_{compra_id}",
                    help="Calcula o preço de lista: Vlr. Unit. Ajust. = Vlr. Unit. / (1 - pct_desconto_item)",
                    use_container_width=True,
                ):
                    with st.spinner("Atualizando valores de lista..."):
                        for it in itens:
                            v_unit = float(it.get("valor_unitario") or 0)
                            v_ajust = round(v_unit / complemento, 2) if complemento > 0 else v_unit
                            supabase.table("compras_itens").update(
                                {"valor_unit_ajustado": v_ajust}
                            ).eq("id", it["id"]).execute()
                            it["valor_unit_ajustado"] = v_ajust
                        st.session_state[chave_vis] = True
                        st.success(f"Valores de lista calculados com desconto de {rotulo_pct_fmt}!")
                        st.rerun()

                if b2.button(
                    "💾 Aplicar Valor Sugerido",
                    type="primary",
                    key=f"btn_aplicar_sugerido_{compra_id}",
                    help="Aplica no estoque o preço de venda sugerido (valor unitário ajustado) e ativa a flag",
                    use_container_width=True,
                ):
                    with st.spinner("Aplicando valores sugeridos ao estoque..."):
                        for it in itens:
                            v_ajust = float(it.get("valor_unit_ajustado") or 0)
                            if v_ajust <= 0:
                                v_unit = float(it.get("valor_unitario") or 0)
                                v_ajust = round(v_unit / complemento, 2) if complemento > 0 else v_unit

                            v_unit_compra = float(it.get("valor_unitario") or 0)

                            supabase.table("estoque").update({
                                "estoque_preco_venda_sugerida": v_ajust,
                                "estoque_preco_ultima_compra": v_unit_compra,
                                "estoque_flag_pct_desconto_item": True,
                            }).eq("produto_id", it["produto_id"]).execute()

                        st.success("Valores sugeridos aplicados com sucesso ao estoque!")
                        st.rerun()
            else:
                if st.button(
                    "🔍 Visualizar preço desconto",
                    key=f"btn_vis_desc_{compra_id}",
                    help="Calcula o preço de lista: Vlr. Unit. Ajust. = Vlr. Unit. / (1 - pct_desconto_item)",
                    use_container_width=True,
                ):
                    with st.spinner("Calculando valores de lista..."):
                        for it in itens:
                            v_unit = float(it.get("valor_unitario") or 0)
                            v_ajust = round(v_unit / complemento, 2) if complemento > 0 else v_unit
                            supabase.table("compras_itens").update(
                                {"valor_unit_ajustado": v_ajust}
                            ).eq("id", it["id"]).execute()
                            it["valor_unit_ajustado"] = v_ajust
                        st.session_state[chave_vis] = True
                        st.rerun()

    # Define as larguras de colunas da tabela
    if exibir_ajustado:
        col_pesos = [1.0, 3.2, 0.7, 1.3, 1.0, 1.1, 1.3]
    else:
        col_pesos = [1.0, 3.8, 0.8, 1.4, 1.2, 1.2]

    with st.container(border=True):
        colunas_cab = st.columns(col_pesos, vertical_alignment="center")
        colunas_cab[0].markdown("**Código**")
        colunas_cab[1].markdown("**Produto**")
        colunas_cab[2].markdown("**Qtd**")

        # Cabeçalho Vlr. Unit. com o lápis ao lado
        c_unit_header = colunas_cab[3]
        col_lbl, col_pop = c_unit_header.columns([0.65, 0.35], gap="small", vertical_alignment="center")
        col_lbl.markdown("**Vlr. Unit.**")
        with col_pop:
            st.markdown('<div class="btn-popover-desc">', unsafe_allow_html=True)
            with st.popover("✏️", help="Definir % desconto dos itens desta NF"):
                st.markdown("##### % Desconto dos Itens")
                st.caption("Define o percentual de desconto sobre o valor unitário dos itens desta compra.")
                novo_pct = st.number_input(
                    "Percentual (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=pct_desconto,
                    step=0.5,
                    format="%.2f",
                    key=f"input_pct_desc_{compra_id}",
                )
                if st.button(
                    "Adicionar desconto",
                    type="primary",
                    use_container_width=True,
                    key=f"salvar_pct_desc_{compra_id}",
                ):
                    supabase.table("compras").update(
                        {"pct_desconto_item": float(novo_pct)}
                    ).eq("id", compra_id).execute()
                    st.success("Percentual salvo!")
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

        colunas_cab[4].markdown("**Desconto**")
        colunas_cab[5].markdown("**Vlr. Total**")

        if exibir_ajustado:
            colunas_cab[6].markdown("**Vlr. Unit. Ajust.**")

        st.divider()

        for item in itens:
            produto = item.get("produtos") or {}
            colunas_linha = st.columns(col_pesos, vertical_alignment="center")
            colunas_linha[0].write(produto.get("codigo_interno") or "-")
            colunas_linha[1].write(produto.get("descricao") or "-")
            colunas_linha[2].write(_fmt_qtd(item.get("quantidade")))
            colunas_linha[3].write(_fmt_moeda(item.get("valor_unitario")))
            colunas_linha[4].write(_fmt_moeda(item.get("valor_desconto")))
            colunas_linha[5].write(_fmt_moeda(item.get("valor_total")))

            if exibir_ajustado:
                v_ajustado = item.get("valor_unit_ajustado")
                if v_ajustado is not None and float(v_ajustado) > 0:
                    colunas_linha[6].markdown(
                        f'<span style="color:#10b981; font-weight:600;">{_fmt_moeda(v_ajustado)}</span>',
                        unsafe_allow_html=True,
                    )
                else:
                    colunas_linha[6].write("-")


@_dialog("✏️ Desconto adicional")
def _dialog_editar_desconto(compra: dict):
    """Popup para preencher/editar o desconto adicional concedido numa compra
    (além do desconto já descrito na própria NF-e) e o motivo dele."""
    st.caption(
        f"NF nº {compra.get('numero_nf') or '-'} — "
        f"Chave: {compra.get('chave_acesso') or '-'}"
    )

    desconto_atual = float(compra.get("compras_desconto_adicional") or 0)
    motivo_atual = compra.get("compras_motivo_desconto") or ""

    with st.form(f"form_desconto_adicional_{compra['id']}", border=False):
        desconto_texto = st.text_input(
            "Desconto adicional (R$)",
            value=_fmt_valor_input(desconto_atual),
            help="Desconto concedido além do que já está descrito na nota fiscal.",
        )
        motivo_texto = st.text_area(
            "Motivo do desconto",
            value=motivo_atual,
            placeholder="Ex.: Avaria no transporte, negociação com o fornecedor...",
        )

        col_salvar, col_cancelar = st.columns(2)
        salvar = col_salvar.form_submit_button(
            "💾 Salvar", type="secondary", use_container_width=True
        )
        cancelar = col_cancelar.form_submit_button(
            "Cancelar", use_container_width=True
        )

    if cancelar:
        st.rerun()

    if not salvar:
        return

    desconto_valor = _parse_valor_input(desconto_texto, 0.0)
    motivo_valor = motivo_texto.strip()

    if desconto_valor > 0 and not motivo_valor:
        st.error("Informe o motivo do desconto adicional.")
        return

    try:
        supabase.table("compras").update({
            "compras_desconto_adicional": desconto_valor,
            "compras_motivo_desconto": motivo_valor or None,
        }).eq("id", compra["id"]).execute()
        st.success("Desconto adicional atualizado com sucesso!")
        st.rerun()
    except Exception as e:
        st.error(f"Erro ao salvar desconto adicional: {e}")


@_dialog("📎 Arquivos da compra", width="large")
def _dialog_arquivos_compra(compra: dict):
    """Gerencia XML/PDF vinculados a uma compra."""
    chave_wrap_upload = f"upload_arquivo_wrap_{compra['id']}"

    # CSS único, escopado só ao container deste botão (mesma técnica usada
    # no botão de preço de venda do estoque) — mais confiável do que torcer
    # por data-testid, que foi o que deixava o botão "voltando" pro vermelho
    # padrão do Streamlit antes.
    st.markdown(
        f"""
        <style>
        div[class*="st-key-{chave_wrap_upload}"] button[kind*="primary"] {{
            background-color: #2563EB !important;
            border-color: #2563EB !important;
            color: #FFFFFF !important;
        }}
        div[class*="st-key-{chave_wrap_upload}"] button[kind*="primary"]:hover {{
            background-color: #1D4ED8 !important;
            border-color: #1D4ED8 !important;
        }}
        div[class*="st-key-{chave_wrap_upload}"] button[kind*="primary"]:active {{
            background-color: #1E40AF !important;
            border-color: #1E40AF !important;
        }}
        div[class*="st-key-baixar_wrap_"] button {{
            color: #60a5fa !important;
            border-color: rgba(96,165,250,.35) !important;
        }}
        div[class*="st-key-baixar_wrap_"] button:hover {{
            background-color: rgba(59,130,246,.12) !important;
            border-color: #3b82f6 !important;
        }}
        div[class*="st-key-excluir_wrap_"] button {{
            color: #f87171 !important;
            border-color: rgba(248,113,113,.35) !important;
        }}
        div[class*="st-key-excluir_wrap_"] button:hover {{
            background-color: rgba(239,68,68,.12) !important;
            border-color: #ef4444 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        f"NF nº {compra.get('numero_nf') or '-'} — "
        f"Compra #{compra.get('id')}"
    )

    with st.container(border=True):
        st.markdown("**📤 Adicionar arquivo**")

        with st.container(key=chave_wrap_upload):
            with st.form(f"form_arquivo_compra_{compra['id']}", border=False):
                arquivo = st.file_uploader(
                    "Selecione o arquivo",
                    type=["xml", "pdf"],
                    key=f"upload_arquivo_compra_{compra['id']}",
                    help="Nesta versão são aceitos somente XML e PDF.",
                )

                col_tipo, col_botao = st.columns([1, 2], vertical_alignment="bottom")
                with col_tipo:
                    tipo = st.selectbox(
                        "Tipo do arquivo",
                        ["PDF", "XML"],
                        key=f"tipo_arquivo_compra_{compra['id']}",
                    )
                with col_botao:
                    enviar = st.form_submit_button(
                        "⬆️ Enviar arquivo",
                        type="primary",
                        use_container_width=True,
                    )

    if enviar:
        if arquivo is None:
            st.error("Selecione um arquivo.")
        else:
            extensao = arquivo.name.lower().rsplit(".", 1)[-1]
            if extensao != tipo.lower():
                st.error(
                    f"O tipo selecionado é {tipo}, mas o arquivo "
                    f"possui extensão .{extensao}."
                )
            else:
                try:
                    nome_padrao = nome_arquivo_compra(
                        compra.get("numero_nf"),
                        tipo,
                    )
                    registro = upload_arquivo_compra(
                        compra_id=compra["id"],
                        arquivo=arquivo,
                        nome_arquivo=nome_padrao,
                        tipo_arquivo=tipo,
                        mime_type=arquivo.type,
                    )
                    st.success(
                        f"Arquivo '{registro['nome_arquivo']}' enviado com sucesso."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao enviar arquivo: {e}")

    st.divider()

    try:
        arquivos = listar_arquivos_compra(compra["id"])
    except Exception as e:
        st.error(f"Erro ao carregar arquivos: {e}")
        return

    st.markdown(f"**📁 Arquivos na nuvem ({len(arquivos)})**")

    if arquivos:
        # Lista dos arquivos dentro de uma área visualmente separada.
        with st.container(border=True):
            for arquivo in arquivos:
                col_tipo, col_nome, col_data, col_acoes = st.columns(
                    [0.8, 3.2, 1.4, 1.8],
                    vertical_alignment="center",
                )

                tipo_arquivo = arquivo.get("tipo_arquivo") or "-"
                nome = arquivo.get("nome_arquivo") or "-"
                criado_em = arquivo.get("criado_em") or ""

                col_tipo.markdown(_badge_tipo_arquivo(tipo_arquivo), unsafe_allow_html=True)
                col_nome.write(nome)

                if criado_em:
                    try:
                        dt = datetime.fromisoformat(criado_em.replace("Z", "+00:00"))
                        # Supabase/Postgres grava o timestamp em UTC; exibe no horário de Brasília.
                        dt_br = dt.astimezone(ZoneInfo("America/Sao_Paulo"))
                        col_data.write(dt_br.strftime("%d/%m/%Y %H:%M"))
                    except (TypeError, ValueError):
                        col_data.write("-")
                else:
                    col_data.write("-")

                with col_acoes:
                    col_baixar, col_excluir = st.columns(2)

                    try:
                        conteudo_arquivo, nome_download, mime_download = baixar_arquivo_compra(
                            arquivo
                        )
                    except Exception:
                        conteudo_arquivo = None
                        nome_download = arquivo.get("nome_arquivo") or "arquivo"
                        mime_download = arquivo.get("mime_type") or "application/octet-stream"
                        col_baixar.error("Erro")

                    if conteudo_arquivo is not None:
                        with col_baixar.container(key=f"baixar_wrap_{arquivo['id']}"):
                            st.download_button(
                                "",
                                data=conteudo_arquivo,
                                file_name=nome_download,
                                mime=mime_download,
                                help="Baixar arquivo",
                                use_container_width=True,
                                key=f"baixar_arquivo_compra_{arquivo['id']}",
                                icon=":material/download:",
                            )

                    with col_excluir.container(key=f"excluir_wrap_{arquivo['id']}"):
                        if st.button(
                            "",
                            key=f"excluir_arquivo_compra_{arquivo['id']}",
                            help="Excluir arquivo da nuvem",
                            use_container_width=True,
                            icon=":material/delete:",
                        ):
                            try:
                                excluir_arquivo_compra(arquivo)
                                st.success("Arquivo excluído.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erro ao excluir arquivo: {e}")
    else:
        with st.container(border=True):
            st.info("Nenum arquivo anexado a esta compra.")


def _secao_listagem():
    # Abre automaticamente o detalhe solicitado pela tela de movimentações.
    compra_abrir_id = st.session_state.pop("compras_abrir_id", None)
    if compra_abrir_id:
        try:
            compra_resp = (
                supabase.table("compras")
                .select("id, numero_nf, data_emissao, valor_produtos, valor_desconto, valor_total, chave_acesso")
                .eq("id", int(compra_abrir_id))
                .single()
                .execute()
            )
            compra = compra_resp.data
            if compra:
                produto_id = st.session_state.pop("compras_abrir_produto_id", None)
                _dialog_itens_compra(compra, produto_id=produto_id)
        except Exception as e:
            st.error(f"Erro ao abrir a compra #{compra_abrir_id}: {e}")

    try:
        # 1. Consulta dos dados na tabela 'compras' (agora incluindo o id,
        #    necessário para buscar os itens e a contagem por compra)
        response = (
            supabase.table("compras")
            .select(
                "id, numero_nf, data_emissao, valor_produtos, valor_desconto,"
                " valor_total, chave_acesso, compras_desconto_adicional,"
                " compras_motivo_desconto, compra_origem, pct_desconto_item"
            )
            .order("data_emissao", desc=True)
            .limit(50000)
            .execute()
        )
        compras = response.data or []

        # 2. Identifica quais compras possuem arquivos anexados, e conta os
        #    itens por compra — ambas via IN(), buscadas em LOTES pequenos
        #    (em vez de um único IN() com todos os IDs de uma vez). Isso
        #    evita estourar o limite de URL/parâmetros da consulta quando há
        #    muitas compras, e reporta o erro de verdade na tela em vez de
        #    engolir silenciosamente (o que fazia a coluna "Itens" mostrar 0
        #    sem nenhuma pista do motivo real).
        def _buscar_em_lotes(tabela: str, coluna_filtro: str, valores: list, colunas_select: str, tamanho_lote: int = 200) -> list[dict]:
            linhas: list[dict] = []
            for inicio in range(0, len(valores), tamanho_lote):
                lote = valores[inicio:inicio + tamanho_lote]
                try:
                    resp = (
                        supabase.table(tabela)
                        .select(colunas_select)
                        .in_(coluna_filtro, lote)
                        .limit(50000)
                        .execute()
                    )
                    linhas.extend(resp.data or [])
                except Exception as err:
                    numero_lote = inicio // tamanho_lote + 1
                    st.warning(f"Erro ao consultar {tabela} (lote {numero_lote}): {err}")
            return linhas

        compras_com_arquivos: set[int] = set()
        compra_ids_arquivos = [c["id"] for c in compras]
        if compra_ids_arquivos:
            linhas_arquivos = _buscar_em_lotes(
                "compras_arquivos", "compra_id", compra_ids_arquivos, "compra_id"
            )
            compras_com_arquivos = {
                int(row["compra_id"])
                for row in linhas_arquivos
                if row.get("compra_id") is not None
            }

        # 3. Contagem de itens por compra
        qtd_itens_por_compra: dict[int, int] = {}
        compra_ids = [c["id"] for c in compras]
        if compra_ids:
            linhas_itens = _buscar_em_lotes(
                "compras_itens", "compra_id", compra_ids, "compra_id"
            )
            for row in linhas_itens:
                cid = row["compra_id"]
                qtd_itens_por_compra[cid] = qtd_itens_por_compra.get(cid, 0) + 1

        # 4. Cálculo dos Cards (KPIs)
        total_compras = len(compras)
        soma_valor_total = sum(
            float(item.get("valor_total") or 0) for item in compras
        )

        data_ultima_compra = "-"
        if compras and compras[0].get("data_emissao"):
            dt_ultima = datetime.fromisoformat(
                compras[0]["data_emissao"].replace("Z", "+00:00")
            )
            data_ultima_compra = dt_ultima.strftime("%d/%m/%y")

        valor_comprado_fmt = (
            f"R$ {soma_valor_total:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        # Ajuste CSS para igualar a altura exata dos cards
        st.markdown(
            """
            <style>
            div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"] > div[data-testid="stContainer"] {
                min-height: 125px;
                display: flex;
                flex-direction: column;
                justify-content: center;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        # 5. Exibição dos Cards no Topo (KPIs)
        col_kpi1, col_kpi2, col_kpi3 = st.columns(3)

        with col_kpi1:
            with st.container(border=True):
                st.caption("Total de Compras")
                st.title(f"{total_compras}")

        with col_kpi2:
            with st.container(border=True):
                st.caption("Valor Comprado")
                st.title(valor_comprado_fmt)

        with col_kpi3:
            with st.container(border=True):
                st.caption("Última Compra")
                st.title(data_ultima_compra)

        st.markdown("---")

        # 5. Filtros da listagem
        # Os filtros ficam abaixo dos KPIs e imediatamente antes da tabela,
        # conforme o layout solicitado.
        col_filtro_nf, col_filtro_pct = st.columns([2.2, 1.3], vertical_alignment="center")

        with col_filtro_nf:
            filtro_nf = st.text_input(
                "Filtrar por NF",
                value=st.session_state.get("compras_filtro_nf", ""),
                placeholder="Digite o número da NF...",
                key="compras_filtro_nf",
            ).strip()

        with col_filtro_pct:
            mostrar_pct = st.toggle(
                "Mostrar NFs com desconto %",
                value=st.session_state.get("compras_filtro_pct", False),
                key="compras_filtro_pct",
                help="Quando ativado, mostra somente NFs que possuem percentual de desconto nos itens.",
            )

        compras_filtradas = compras

        if filtro_nf:
            termo_nf = filtro_nf.casefold()
            compras_filtradas = [
                compra for compra in compras_filtradas
                if termo_nf in str(compra.get("numero_nf") or "").casefold()
            ]

        if mostrar_pct:
            compras_filtradas = [
                compra for compra in compras_filtradas
                if float(compra.get("pct_desconto_item") or 0) > 0
            ]

        if not compras_filtradas:
            if filtro_nf or mostrar_pct:
                st.info("Nenhuma NF encontrada com os filtros selecionados.")
            else:
                st.info("Nenhuma compra registrada.")
            return

        # 6. Paginação da listagem
        filtro_atual = len(compras_filtradas)
        if st.session_state.get("compras_total_anterior") != filtro_atual:
            st.session_state["compras_total_anterior"] = filtro_atual
            reset_paginacao("compras")

        itens_por_pagina = get_itens_por_pagina("compras", 50)
        total_compras_registros = len(compras_filtradas)
        total_paginas = max(1, (total_compras_registros + itens_por_pagina - 1) // itens_por_pagina)
        pagina_atual = min(int(st.session_state.get("pagina_atual_compras", 1)), total_paginas)
        st.session_state["pagina_atual_compras"] = max(1, pagina_atual)
        inicio = (st.session_state["pagina_atual_compras"] - 1) * itens_por_pagina
        compras_pagina = compras_filtradas[inicio:inicio + itens_por_pagina]

        # Paginação informativa no topo da tabela.
        render_paginacao(
            "compras",
            total_compras_registros,
            itens_por_pagina=itens_por_pagina,
            mostrar_contagem_superior=True,
            mostrar_contagem_inferior=False,
            permitir_seletor=True,
        )

        # 7. Tabela de Compras
        st.markdown(
            """
            <style>
            div[class*="st-key-ver_itens_compra_"] div[data-testid="stButton"],
            div[class*="st-key-editar_desconto_compra_"] div[data-testid="stButton"],
            div[class*="st-key-arquivos_compra_"] div[data-testid="stButton"] {
                width: 100% !important;
            }

            div[class*="st-key-ver_itens_compra_"] button,
            div[class*="st-key-editar_desconto_compra_"] button,
            div[class*="st-key-arquivos_compra_"] button {
                padding: 0.2rem 0 !important;
                min-height: 32px !important;
                height: 32px !important;
                min-width: 0 !important;
                width: 100% !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                box-sizing: border-box !important;
            }

            div[class*="st-key-ver_itens_compra_"] button p,
            div[class*="st-key-editar_desconto_compra_"] button p,
            div[class*="st-key-arquivos_compra_"] button p,
            div[class*="st-key-ver_itens_compra_"] button span,
            div[class*="st-key-editar_desconto_compra_"] button span,
            div[class*="st-key-arquivos_compra_"] button span {
                font-size: 0.95rem !important;
                line-height: 1 !important;
                margin: 0 !important;
                padding: 0 !important;
                overflow: visible !important;
                text-overflow: clip !important;
                white-space: nowrap !important;
                text-align: center !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        col_pesos = [1.3, 1.1, 1.3, 1.2, 1.3, 1.4, 0.6, 1.8]
        with st.container(border=True):
            c_nf, c_dt, c_prod, c_desc, c_tot, c_desc_adic, c_itens, c_acao = st.columns(
                col_pesos
            )

            c_nf.markdown("**Número NF**")
            c_dt.markdown("**Data Compra**")
            c_prod.markdown("**Valor Produto**")
            c_desc.markdown("**Desconto (-)**")
            c_tot.markdown("**Valor Total**")
            c_desc_adic.markdown("**Desconto Adicional**")
            c_itens.markdown("**Itens**")
            c_acao.markdown("**Ações**")

            st.divider()

            for item in compras_pagina:
                col_nf, col_dt, col_prod, col_desc, col_tot, col_desc_adic, col_itens, col_acao = (
                    st.columns(col_pesos)
                )

                desconto_adicional = float(item.get("compras_desconto_adicional") or 0)
                motivo_desconto = (item.get("compras_motivo_desconto") or "").strip()
                tem_desconto_adicional = desconto_adicional > 0
                chave_acesso = item.get("chave_acesso") or "-"

                # A chave de acesso (antes uma coluna própria) fica disponível
                # ao passar o mouse, via tooltip nativo do navegador. O
                # indicador de desconto adicional fica só na própria coluna
                # "Desconto Adicional" (badge laranja).
                rotulo_nf = item.get("numero_nf") or "-"
                tem_arquivos = item["id"] in compras_com_arquivos
                indicador_arquivos = _badge_clipe_arquivos() if tem_arquivos else ""
                tooltip_nf = f"Chave de acesso: {chave_acesso}"
                if tem_desconto_adicional:
                    tooltip_nf += f" | Desconto adicional: {motivo_desconto or 'motivo não informado'}"
                tooltip_nf_html = tooltip_nf.replace('"', "&quot;")
                col_nf.markdown(
                    f'<span title="{tooltip_nf_html}" style="cursor: help;">'
                    f'{rotulo_nf}{indicador_arquivos}</span>',
                    unsafe_allow_html=True,
                )

                if item.get("data_emissao"):
                    dt_item = datetime.fromisoformat(
                        item["data_emissao"].replace("Z", "+00:00")
                    )
                    col_dt.write(dt_item.strftime("%d/%m/%Y"))
                else:
                    col_dt.write("-")

                v_prod = float(item.get("valor_produtos") or 0)
                v_desc = float(item.get("valor_desconto") or 0)
                v_tot = float(item.get("valor_total") or 0)

                col_prod.write(_fmt_numero(v_prod))
                col_desc.write(_fmt_numero(v_desc))
                col_tot.write(_fmt_numero(v_tot))

                with col_desc_adic:
                    if tem_desconto_adicional:
                        motivo_tooltip = motivo_desconto or "Motivo não informado"
                        # Escapa aspas para não quebrar o atributo title do HTML.
                        motivo_html = motivo_tooltip.replace('"', "&quot;")
                        st.markdown(
                            f'<span title="{motivo_html}" '
                            'style="color:#ff8a3d; font-weight:600; cursor: help;">'
                            f'🏷️ {_fmt_numero(desconto_adicional)}</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.write("-")

                col_itens.write(str(qtd_itens_por_compra.get(item["id"], 0)))

                with col_acao:
                    col_ver, col_editar, col_arquivos = st.columns(
                        3, gap="small", vertical_alignment="center"
                    )
                    if col_ver.button(
                        "🔎",
                        key=f"ver_itens_compra_{item['id']}",
                        help="Ver itens da compra",
                        use_container_width=True,
                    ):
                        _dialog_itens_compra(item)
                    if col_editar.button(
                        "✏️",
                        key=f"editar_desconto_compra_{item['id']}",
                        help="Editar desconto adicional",
                        use_container_width=True,
                    ):
                        _dialog_editar_desconto(item)
                    if col_arquivos.button(
                        "📎",
                        key=f"arquivos_compra_{item['id']}",
                        help="Arquivos da compra",
                        use_container_width=True,
                    ):
                        _dialog_arquivos_compra(item)

        render_paginacao(
            "compras",
            total_compras_registros,
            itens_por_pagina=itens_por_pagina,
            mostrar_contagem_superior=False,
            mostrar_contagem_inferior=True,
            permitir_seletor=True,
        )

    except Exception as e:
        st.error(f"Erro ao carregar dados de compras: {e}")


def tela_compras():
    st.header("🛍️ Compras")

    aba_listagem, aba_importar = st.tabs(["Compras registradas", "Importar NF-e (XML)"])

    with aba_listagem:
        _secao_listagem()

    with aba_importar:
        tela_importar_nf()