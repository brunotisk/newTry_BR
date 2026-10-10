"""Tela de Parceiros: fechamento de ciclos e envio de produtos.

Arquivo independente de vendas.py (sem import circular): os helpers usados aqui
(_fmt_moeda, _parse_data_segura, _container_com_key) foram replicados abaixo.
Ponto de entrada: secao_parceiros().
"""
import hashlib
import html
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pandas as pd
import streamlit as st

from db import supabase, buscar_todos

# ----------------------------------------------------------------------
# Fotos dos produtos (coluna produtos.foto_url; bucket público do Supabase)
# ----------------------------------------------------------------------
# As fotos podem ser URLs públicas completas ou caminhos relativos dentro do bucket.
# URLs antigas de outros buckets do Supabase são redirecionadas para o bucket atual,
# mantendo o mesmo caminho do arquivo. URLs da Shopify continuam com miniatura reduzida.
# As imagens são renderizadas como <img> comuns do navegador.
BUCKET_FOTOS = "fotos_produtos"
LARGURA_MINIATURA = 160   # largura solicitada à CDN da Shopify
TAM_FOTO = 56             # px de exibição

_CSS_TABELA_FOTOS = (
    "<style>"
    ".tab-fotos{width:100%;border-collapse:collapse;font-size:0.9rem}"
    ".tab-fotos th{text-align:left;padding:6px 8px;border-bottom:1px solid rgba(128,128,128,.45);font-weight:600}"
    ".tab-fotos td{padding:6px 8px;border-bottom:1px solid rgba(128,128,128,.2);vertical-align:middle}"
    ".tab-fotos .num{text-align:right;white-space:nowrap}"
    f".tab-fotos img{{width:{TAM_FOTO}px;height:{TAM_FOTO}px;object-fit:cover;border-radius:6px;display:block}}"
    f".tab-fotos .sem-foto{{width:{TAM_FOTO}px;height:{TAM_FOTO}px;border-radius:6px;display:flex;"
    "align-items:center;justify-content:center;font-size:0.7rem;opacity:.6;"
    "border:1px dashed rgba(128,128,128,.6)}"
    "</style>"
)


def _url_miniatura(url: str) -> str:
    """Resolve fotos para o bucket público fotos_produtos quando possível.

    - URL já apontando para fotos_produtos: mantém como está.
    - URL de outro bucket público do Supabase: usa o mesmo caminho no bucket atual.
    - Caminho relativo: gera a URL pública do bucket atual.
    - URL da Shopify: pede uma miniatura reduzida.
    - Outras URLs completas: mantém como estão.
    """
    texto = str(url or "").strip()
    if not texto:
        return ""

    partes = urlparse(texto)
    marcador_storage = "/storage/v1/object/public/"

    if marcador_storage in partes.path:
        prefixo, restante = partes.path.split(marcador_storage, 1)
        bucket_atual, separador, caminho = restante.partition("/")
        if separador and caminho and bucket_atual != BUCKET_FOTOS:
            # A foto já está no Storage; aponta para o bucket novo usando o mesmo objeto.
            try:
                return str(supabase.storage.from_(BUCKET_FOTOS).get_public_url(caminho))
            except Exception:
                return texto
        return texto

    if partes.scheme in ("http", "https") and partes.netloc:
        if "cdn.shopify.com" not in partes.netloc:
            return texto
        query = dict(parse_qsl(partes.query))
        query["width"] = str(LARGURA_MINIATURA)
        return urlunparse(partes._replace(query=urlencode(query)))

    # Valor sem domínio: considera que é o caminho do objeto no bucket público.
    try:
        return str(supabase.storage.from_(BUCKET_FOTOS).get_public_url(texto.lstrip("/")))
    except Exception:
        return texto


def _mostrar_foto(url) -> None:
    """Foto de uma linha (st.image = <img> do navegador); 'sem foto' se não houver URL."""
    url = str(url).strip() if url else ""
    if url:
        st.image(_url_miniatura(url), width=TAM_FOTO)
    else:
        st.caption("sem foto")


def _legenda_fotos(urls: list) -> None:
    """Ajuda a distinguir 'produto sem foto cadastrada' de 'foto que não carregou'."""
    com_foto = sum(1 for u in urls if u and str(u).strip())
    st.caption(f"📷 {com_foto} de {len(urls)} produtos listados têm foto cadastrada.")


def _html_tabela_produtos(linhas: list) -> str:
    """Tabela HTML (somente leitura) com miniatura. `linhas`: dicts com foto, codigo,
    produto, qtd, valor_unit, subtotal (textos já formatados, exceto foto)."""
    corpo = []
    for l in linhas:
        url = str(l.get("foto") or "").strip()
        foto = (
            f'<img src="{html.escape(_url_miniatura(url), quote=True)}" loading="lazy" alt="">'
            if url else '<div class="sem-foto">sem foto</div>'
        )
        corpo.append(
            f"<tr><td>{foto}</td>"
            f"<td>{html.escape(str(l['codigo']))}</td>"
            f"<td>{html.escape(str(l['produto']))}</td>"
            f"<td class=\"num\">{html.escape(str(l['qtd']))}</td>"
            f"<td class=\"num\">{html.escape(str(l['valor_unit']))}</td>"
            f"<td class=\"num\">{html.escape(str(l['subtotal']))}</td></tr>"
        )
    cabecalho = (
        "<tr><th>Foto</th><th>Código</th><th>Produto</th>"
        '<th class="num">Qtd</th><th class="num">Valor unit.</th><th class="num">Subtotal</th></tr>'
    )
    return _CSS_TABELA_FOTOS + '\n<table class="tab-fotos">' + cabecalho + "".join(corpo) + "</table>"



def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _parse_data_segura(valor):
    """Converte 'YYYY-MM-DD...' (ou vazio/None/inválido) em date, sem lançar exceção."""
    texto = str(valor or "")[:10]
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def _container_com_key(key: str, **opcoes):
    try:
        return st.container(key=key, **opcoes)
    except TypeError:  # Streamlit antigo, sem `key` no container
        return st.container(**opcoes)


def _render_cabecalho_parceiro(parceiro: dict) -> None:
    canal_nome = (parceiro.get("canais_venda") or {}).get("nome") or "-"
    telefone = parceiro.get("telefone") or "-"
    email = parceiro.get("email") or "-"
    comissao = parceiro.get("percentual_comissao")
    comissao_fmt = f"{float(comissao):g}%" if comissao is not None else "-"
    observacao = (parceiro.get("observacao") or "").strip()

    observacao_html = (
        f'<div style="margin-top:0.5rem; font-size:0.85rem; opacity:0.75;">'
        f'{html.escape(observacao)}</div>'
        if observacao else ""
    )

    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.35); border-radius:0.5rem;
                     padding:1rem 1.2rem; margin-bottom:1rem;">
            <div style="font-size:1.1rem; font-weight:700; margin-bottom:0.5rem;">
                {html.escape(parceiro.get('nome') or '')}
            </div>
            <div style="display:flex; gap:2rem; flex-wrap:wrap; font-size:0.9rem;">
                <div><span style="opacity:0.7;">Canal:</span> {html.escape(canal_nome)}</div>
                <div><span style="opacity:0.7;">Comissão padrão:</span> {comissao_fmt}</div>
                <div><span style="opacity:0.7;">Telefone:</span> {html.escape(telefone)}</div>
                <div><span style="opacity:0.7;">E-mail:</span> {html.escape(email)}</div>
            </div>
            {observacao_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _secao_parceiro_vendas_canal(parceiro: dict) -> None:
    """Lista as vendas feitas através do canal deste parceiro, em um período
    selecionável. Puramente de leitura — não depende de nenhuma regra de
    fechamento."""
    canal_id = parceiro.get("canal_id")
    if not canal_id:
        st.warning("Este parceiro não possui um canal de venda vinculado.")
        return

    col_ini, col_fim = st.columns(2)
    with col_ini:
        data_ini = st.date_input(
            "De", value=date.today().replace(day=1), key="parceiros_produtos_data_ini"
        )
    with col_fim:
        data_fim = st.date_input("Até", value=date.today(), key="parceiros_produtos_data_fim")

    if data_ini > data_fim:
        st.error("A data inicial não pode ser depois da data final.")
        return

    try:
        vendas = buscar_todos(
            lambda: supabase.table("vendas")
            .select(
                "id, data_venda, cliente, quantidade, valor_final, "
                "comissao_percentual, comissao_valor, "
                "produtos(codigo_interno, descricao), status_venda(nome)"
            )
            .eq("canal_venda_id", canal_id)
            .gte("data_venda", data_ini.isoformat())
            .lte("data_venda", data_fim.isoformat())
            .order("data_venda", desc=True)
            .order("id", desc=True)
        )
    except Exception as e:
        st.error(f"Não foi possível carregar os produtos enviados: {e}")
        return

    if not vendas:
        st.info("Nenhuma venda deste canal no período selecionado.")
        return

    linhas = []
    total_valor = 0.0
    total_comissao = 0.0
    for v in vendas:
        produto = v.get("produtos") or {}
        valor_final = float(v.get("valor_final") or 0)
        comissao_valor = float(v.get("comissao_valor") or 0)
        total_valor += valor_final
        total_comissao += comissao_valor
        linhas.append({
            "Data": v.get("data_venda"),
            "Cliente": v.get("cliente") or "-",
            "Produto": produto.get("descricao") or "-",
            "Código": produto.get("codigo_interno") or "-",
            "Qtd": v.get("quantidade"),
            "Valor": valor_final,
            "Comissão %": v.get("comissao_percentual"),
            "Comissão R$": comissao_valor,
            "Status": (v.get("status_venda") or {}).get("nome") or "-",
        })

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Vendas no período", len(linhas))
    col_m2.metric("Valor total", _fmt_moeda(total_valor))
    col_m3.metric("Comissão total", _fmt_moeda(total_comissao))

    st.dataframe(linhas, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Produtos enviados (envios de produtos ao parceiro)
# ---------------------------------------------------------------------------

def _fmt_data(valor) -> str:
    d = _parse_data_segura(valor)
    return d.strftime("%d/%m/%Y") if d else "-"


def _fmt_qtd(valor) -> str:
    return f"{float(valor or 0):g}"


def _buscar_envios(parceiro_id) -> list:
    return (
        supabase.table("parcerias_envios")
        .select(
            "id, numero_envio, data_envio, status, observacao, fechado_em, "
            "total_itens, total_pecas, valor_total"
        )
        .eq("parceiro_id", parceiro_id)
        .order("numero_envio", desc=True)
        .execute()
        .data
        or []
    )


def _buscar_itens_dos_envios(ids_envios: list) -> dict:
    """Devolve {envio_id: [itens]} em uma única consulta."""
    if not ids_envios:
        return {}
    itens = buscar_todos(
        lambda: supabase.table("parcerias_envio_itens")
        .select(
            "id, envio_id, produto_id, quantidade, valor_unitario, "
            "produtos(codigo_interno, descricao, foto_url)"
        )
        .in_("envio_id", ids_envios)
        .order("id")
    )
    por_envio: dict = {}
    for item in itens:
        por_envio.setdefault(item["envio_id"], []).append(item)
    return por_envio


def _carregar_produtos_disponiveis() -> dict:
    """Produtos com saldo em estoque, já descontando o que está reservado em
    QUALQUER envio ainda aberto (de qualquer parceiro).

    Retorna {produto_id: {codigo, descricao, saldo, comprometido, disponivel, preco}}.
    """
    linhas_estoque = buscar_todos(
        lambda: supabase.table("estoque")
        .select(
            "quantidade_atual, estoque_preco_venda_sugerida, "
            "produtos(id, codigo_interno, descricao, foto_url)"
        )
        .gt("quantidade_atual", 0)
        .order("produto_id")
    )

    abertos = (
        supabase.table("parcerias_envios")
        .select("id")
        .eq("status", "Aberto")
        .execute()
        .data
        or []
    )
    comprometido: dict = {}
    ids_abertos = [e["id"] for e in abertos]
    if ids_abertos:
        itens_abertos = buscar_todos(
            lambda: supabase.table("parcerias_envio_itens")
            .select("produto_id, quantidade")
            .in_("envio_id", ids_abertos)
            .order("id")
        )
        for it in itens_abertos:
            pid = it["produto_id"]
            comprometido[pid] = comprometido.get(pid, 0.0) + float(it.get("quantidade") or 0)

    produtos: dict = {}
    for linha in linhas_estoque:
        produto = linha.get("produtos")
        if not produto:
            continue
        saldo = float(linha.get("quantidade_atual") or 0)
        reservado = comprometido.get(produto["id"], 0.0)
        produtos[produto["id"]] = {
            "codigo": produto.get("codigo_interno") or "-",
            "descricao": produto.get("descricao") or "-",
            "foto_url": produto.get("foto_url"),
            "saldo": saldo,
            "comprometido": reservado,
            "disponivel": max(saldo - reservado, 0.0),
            "preco": float(linha.get("estoque_preco_venda_sugerida") or 0),
        }
    return produtos


def _resetar_editores_envio() -> None:
    """Força os data_editor a recomeçarem do zero (troca a key deles)."""
    st.session_state["parceiros_envio_reset"] = (
        st.session_state.get("parceiros_envio_reset", 0) + 1
    )


def _render_novo_envio(parceiro: dict, envios: list) -> None:
    proximo = (envios[0]["numero_envio"] + 1) if envios else 1
    st.info(
        f"Nenhum envio em aberto. O próximo será o **envio #{proximo}**. "
        "Inicie-o para selecionar os produtos que ficarão com o parceiro."
    )

    col_data, col_obs = st.columns([1, 3])
    with col_data:
        data_envio = st.date_input(
            "Data do envio",
            value=date.today(),
            key="parceiros_envio_data_novo",
            format="DD/MM/YYYY",
        )
    with col_obs:
        observacao = st.text_input(
            "Observação (opcional)", key="parceiros_envio_obs_novo"
        )

    if st.button("▶️ Iniciar novo envio", key="parceiros_envio_iniciar", type="primary"):
        try:
            supabase.table("parcerias_envios").insert({
                "parceiro_id": parceiro["id"],
                "numero_envio": proximo,
                "data_envio": data_envio.isoformat(),
                "observacao": observacao.strip() or None,
                "status": "Aberto",
            }).execute()
            _resetar_editores_envio()
            st.success(f"Envio #{proximo} iniciado.")
            st.rerun()
        except Exception as e:
            st.error(f"Não foi possível iniciar o envio: {e}")


def _classificar_alteracoes(itens: list, linhas_editadas: list, disp: dict):
    """Compara a tabela editada com o que está gravado.

    Retorna (ids_para_remover, [(id, nova_qtd)], erros).
    """
    por_id = {it["id"]: it for it in itens}
    remover, atualizar, erros = [], [], []
    for linha in linhas_editadas:
        item = por_id.get(linha["id"])
        if not item:
            continue
        codigo = (item.get("produtos") or {}).get("codigo_interno") or item["produto_id"]
        if linha.get("Remover"):
            remover.append(item["id"])
            continue
        atual = float(item.get("quantidade") or 0)
        nova = float(linha.get("Qtd") or 0)
        if nova == atual:
            continue
        if nova <= 0:
            erros.append(f"{codigo}: a quantidade deve ser maior que zero (ou marque Remover).")
            continue
        maximo = atual + disp.get(item["produto_id"], {}).get("disponivel", 0.0)
        if nova > maximo:
            erros.append(
                f"{codigo}: quantidade {_fmt_qtd(nova)} maior que o disponível "
                f"({_fmt_qtd(maximo)})."
            )
            continue
        atualizar.append((item["id"], nova))
    return remover, atualizar, erros


def _iniciar_confirmacao_fechamento(chave: str) -> None:
    st.session_state[chave] = True


def _cancelar_confirmacao_fechamento(chave: str) -> None:
    st.session_state.pop(chave, None)


def _render_envio_aberto(parceiro: dict, envio: dict, itens: list) -> None:
    envio_id = envio["id"]
    reset = st.session_state.get("parceiros_envio_reset", 0)

    st.markdown(
        f"##### Envio #{envio['numero_envio']} em aberto — "
        f"{_fmt_data(envio.get('data_envio'))}"
    )
    if envio.get("observacao"):
        st.caption(envio["observacao"])

    try:
        disp = _carregar_produtos_disponiveis()
    except Exception as e:
        st.error(f"Não foi possível carregar os produtos em estoque: {e}")
        return

    total_pecas = sum(float(i.get("quantidade") or 0) for i in itens)
    total_valor = sum(
        float(i.get("quantidade") or 0) * float(i.get("valor_unitario") or 0) for i in itens
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Produtos no envio", len(itens))
    c2.metric("Peças", _fmt_qtd(total_pecas))
    c3.metric("Valor (preço sugerido)", _fmt_moeda(total_valor))

    # ------------------------------------------------------------------
    # 1) Produtos já selecionados para o envio (editar qtd / remover)
    # ------------------------------------------------------------------
    st.markdown("**Produtos selecionados para este envio**")
    pendentes = False
    if not itens:
        st.caption("Nenhum produto selecionado ainda. Use a seção abaixo para adicionar.")
    else:
        _legenda_fotos([(it.get("produtos") or {}).get("foto_url") for it in itens])
        cols_itens = [0.9, 1.0, 1.4, 3.2, 1.3, 1.3, 1.3]
        linhas_editadas = []
        with st.container(border=True):
            for col, titulo in zip(
                st.columns(cols_itens, vertical_alignment="center"),
                ["Remover", "Foto", "Código", "Produto", "Qtd", "Valor unit.", "Subtotal"],
            ):
                col.markdown(f"**{titulo}**")

            for it in itens:
                prod = it.get("produtos") or {}
                valor_unit = float(it.get("valor_unitario") or 0)
                c_rm, c_foto, c_cod, c_prod, c_qtd, c_val, c_sub = st.columns(
                    cols_itens, vertical_alignment="center"
                )
                remover_flag = c_rm.checkbox(
                    "Remover",
                    key=f"parceiros_rm_{envio_id}_{reset}_{it['id']}",
                    label_visibility="collapsed",
                )
                with c_foto:
                    _mostrar_foto(prod.get("foto_url"))
                c_cod.write(prod.get("codigo_interno") or "-")
                c_prod.write(prod.get("descricao") or "-")
                qtd = c_qtd.number_input(
                    "Qtd",
                    min_value=0.0,
                    step=1.0,
                    value=float(it.get("quantidade") or 0),
                    format="%g",
                    key=f"parceiros_qtd_{envio_id}_{reset}_{it['id']}",
                    label_visibility="collapsed",
                )
                c_val.write(_fmt_moeda(valor_unit))
                c_sub.write(_fmt_moeda(qtd * valor_unit))
                linhas_editadas.append({"id": it["id"], "Remover": remover_flag, "Qtd": qtd})

        remover, atualizar, erros = _classificar_alteracoes(itens, linhas_editadas, disp)
        pendentes = bool(remover or atualizar or erros)

        if erros:
            for erro in erros:
                st.error(erro)

        if st.button(
            "💾 Salvar alterações",
            key="parceiros_envio_salvar",
            disabled=not (remover or atualizar) or bool(erros),
        ):
            try:
                if remover:
                    supabase.table("parcerias_envio_itens").delete().in_("id", remover).execute()
                for item_id, nova_qtd in atualizar:
                    supabase.table("parcerias_envio_itens").update(
                        {"quantidade": nova_qtd}
                    ).eq("id", item_id).execute()
                _resetar_editores_envio()
                st.success("Alterações salvas.")
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível salvar as alterações: {e}")

    # ------------------------------------------------------------------
    # 2) Seleção de novos produtos
    # ------------------------------------------------------------------
    ja_no_envio = {it["produto_id"] for it in itens}
    with st.expander("➕ Adicionar produtos ao envio", expanded=not itens):
        filtro = st.text_input(
            "Buscar produto (código ou descrição)",
            key=f"parceiros_envio_busca_{envio_id}",
        ).strip().lower()

        candidatos = [
            (pid, p) for pid, p in disp.items()
            if pid not in ja_no_envio
            and p["disponivel"] > 0
            and (not filtro or filtro in f"{p['codigo']} {p['descricao']}".lower())
        ]

        if not candidatos:
            st.info("Nenhum produto disponível para adicionar com este filtro.")
        else:
            # Seleção guardada em session_state: sobrevive à troca de página e de busca.
            base = f"parceiros_cand_{envio_id}_{reset}"
            sel = st.session_state.setdefault(f"{base}_sel", {})
            for pid in list(sel):  # descarta o que deixou de estar disponível
                if pid in ja_no_envio or pid not in disp or disp[pid]["disponivel"] <= 0:
                    sel.pop(pid)

            POR_PAGINA = 20
            n_paginas = max(1, (len(candidatos) + POR_PAGINA - 1) // POR_PAGINA)
            sufixo_filtro = hashlib.md5(filtro.encode("utf-8")).hexdigest()[:8]
            if n_paginas > 1:
                pagina = st.number_input(
                    f"Página (de {n_paginas}) — {len(candidatos)} produtos",
                    min_value=1,
                    max_value=n_paginas,
                    value=1,
                    step=1,
                    key=f"{base}_pag_{sufixo_filtro}",
                )
            else:
                pagina = 1
            visiveis = candidatos[(pagina - 1) * POR_PAGINA: pagina * POR_PAGINA]

            _legenda_fotos([p.get("foto_url") for _, p in visiveis])
            cols_cand = [0.9, 1.0, 1.3, 3.0, 0.9, 1.0, 1.3, 1.3]
            with st.container(border=True):
                for col, titulo in zip(
                    st.columns(cols_cand, vertical_alignment="center"),
                    ["Selecionar", "Foto", "Código", "Produto", "Saldo", "Disp.", "Qtd", "Valor unit."],
                ):
                    col.markdown(f"**{titulo}**")

                for pid, p in visiveis:
                    c_sel, c_foto, c_cod, c_prod, c_saldo, c_disp, c_qtd, c_val = st.columns(
                        cols_cand, vertical_alignment="center"
                    )
                    maximo = float(p["disponivel"])
                    minimo = min(1.0, maximo)
                    k_chk, k_qtd = f"{base}_chk_{pid}", f"{base}_qtd_{pid}"
                    if k_chk not in st.session_state:  # widgets fora da tela perdem o estado
                        st.session_state[k_chk] = pid in sel
                    if k_qtd not in st.session_state:
                        st.session_state[k_qtd] = float(sel.get(pid, 1.0))
                    st.session_state[k_qtd] = min(max(float(st.session_state[k_qtd]), minimo), maximo)

                    marcado = c_sel.checkbox("Selecionar", key=k_chk, label_visibility="collapsed")
                    with c_foto:
                        _mostrar_foto(p.get("foto_url"))
                    c_cod.write(p["codigo"])
                    c_prod.write(p["descricao"])
                    c_saldo.write(_fmt_qtd(p["saldo"]))
                    c_disp.write(_fmt_qtd(p["disponivel"]))
                    qtd = c_qtd.number_input(
                        "Qtd",
                        min_value=minimo,
                        max_value=maximo,
                        step=1.0,
                        format="%g",
                        key=k_qtd,
                        label_visibility="collapsed",
                    )
                    c_val.write(_fmt_moeda(p["preco"]))

                    if marcado:
                        sel[pid] = float(qtd)
                    else:
                        sel.pop(pid, None)

            marcados = [
                {
                    "produto_id": pid,
                    "Código": disp[pid]["codigo"],
                    "Qtd": qtd_sel,
                    "Disponível": disp[pid]["disponivel"],
                    "Valor unit.": disp[pid]["preco"],
                }
                for pid, qtd_sel in sel.items()
            ]
            if len(marcados) > sum(1 for pid, _ in visiveis if pid in sel):
                st.caption("A seleção inclui produtos de outras páginas ou buscas.")

            if st.button(
                f"➕ Adicionar selecionados ({len(marcados)})",
                key="parceiros_envio_adicionar",
                disabled=not marcados,
            ):
                erros_add = []
                novos = []
                for linha in marcados:
                    qtd = float(linha.get("Qtd") or 0)
                    maximo = float(linha.get("Disponível") or 0)
                    if qtd <= 0:
                        erros_add.append(f"{linha['Código']}: informe uma quantidade maior que zero.")
                    elif qtd > maximo:
                        erros_add.append(
                            f"{linha['Código']}: quantidade {_fmt_qtd(qtd)} maior que o "
                            f"disponível ({_fmt_qtd(maximo)})."
                        )
                    else:
                        novos.append({
                            "envio_id": envio_id,
                            "produto_id": linha["produto_id"],
                            "quantidade": qtd,
                            "valor_unitario": float(linha.get("Valor unit.") or 0),
                        })
                if erros_add:
                    for erro in erros_add:
                        st.error(erro)
                else:
                    try:
                        supabase.table("parcerias_envio_itens").insert(novos).execute()
                        _resetar_editores_envio()
                        st.success(f"{len(novos)} produto(s) adicionado(s) ao envio.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Não foi possível adicionar os produtos: {e}")

    # ------------------------------------------------------------------
    # 3) Fechar envio
    # ------------------------------------------------------------------
    st.divider()
    chave_confirmar = f"parceiros_envio_confirmar_{envio_id}"

    if pendentes:
        st.warning("Há alterações não salvas na tabela acima. Salve-as antes de fechar o envio.")

    if st.session_state.get(chave_confirmar) and not pendentes:
        st.warning(
            f"Fechar o **envio #{envio['numero_envio']}** com {len(itens)} produto(s) e "
            f"{_fmt_qtd(total_pecas)} peça(s)? Depois de fechado, ele não poderá mais ser editado."
        )
        col_ok, col_cancel, _ = st.columns([1, 1, 3])
        with col_ok:
            confirmou = st.button(
                "Confirmar fechamento", type="primary", key="parceiros_envio_confirmar_ok"
            )
        with col_cancel:
            st.button(
                "Cancelar",
                key="parceiros_envio_confirmar_cancelar",
                on_click=_cancelar_confirmacao_fechamento,
                args=(chave_confirmar,),
            )

        if confirmou:
            try:
                supabase.table("parcerias_envios").update({
                    "status": "Fechado",
                    "fechado_em": datetime.now().isoformat(),
                    "total_itens": len(itens),
                    "total_pecas": total_pecas,
                    "valor_total": total_valor,
                }).eq("id", envio_id).eq("status", "Aberto").execute()
                st.session_state.pop(chave_confirmar, None)
                _resetar_editores_envio()
                st.success(f"Envio #{envio['numero_envio']} fechado.")
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível fechar o envio: {e}")
    else:
        st.button(
            f"✅ Fechar envio #{envio['numero_envio']}",
            type="primary",
            disabled=not itens or pendentes,
            key="parceiros_envio_fechar",
            on_click=_iniciar_confirmacao_fechamento,
            args=(chave_confirmar,),
        )


def _render_historico_envios(parceiro: dict, envios: list, itens_por_envio: dict) -> None:
    st.markdown("##### Histórico de envios")
    if not envios:
        st.caption("Nenhum envio registrado ainda para este parceiro.")
        return

    linhas = []
    for e in envios:
        itens = itens_por_envio.get(e["id"], [])
        if e.get("status") == "Fechado" and e.get("total_pecas") is not None:
            n_itens = e.get("total_itens") or len(itens)
            pecas = float(e.get("total_pecas") or 0)
            valor = float(e.get("valor_total") or 0)
        else:
            n_itens = len(itens)
            pecas = sum(float(i.get("quantidade") or 0) for i in itens)
            valor = sum(
                float(i.get("quantidade") or 0) * float(i.get("valor_unitario") or 0)
                for i in itens
            )
        linhas.append({
            "Envio": e["numero_envio"],
            "Data": _fmt_data(e.get("data_envio")),
            "Status": e.get("status"),
            "Produtos": n_itens,
            "Peças": _fmt_qtd(pecas),
            "Valor": _fmt_moeda(valor),
            "Fechado em": _fmt_data(e.get("fechado_em")),
        })

    selecao = st.dataframe(
        linhas,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"parceiros_historico_envios_{parceiro['id']}",
    )

    selecionadas = selecao.selection.rows
    if not selecionadas:
        st.caption("Clique em um envio para ver os produtos enviados.")
        return

    envio = envios[selecionadas[0]]
    itens = itens_por_envio.get(envio["id"], [])
    st.markdown(
        f"**Envio #{envio['numero_envio']}** — {_fmt_data(envio.get('data_envio'))} "
        f"({envio.get('status')})"
    )
    if envio.get("observacao"):
        st.caption(envio["observacao"])
    if not itens:
        st.info("Este envio não possui produtos.")
        return

    _legenda_fotos([(i.get("produtos") or {}).get("foto_url") for i in itens])
    st.markdown(
        _html_tabela_produtos([
            {
                "foto": (i.get("produtos") or {}).get("foto_url"),
                "codigo": (i.get("produtos") or {}).get("codigo_interno") or "-",
                "produto": (i.get("produtos") or {}).get("descricao") or "-",
                "qtd": _fmt_qtd(i.get("quantidade")),
                "valor_unit": _fmt_moeda(i.get("valor_unitario")),
                "subtotal": _fmt_moeda(
                    float(i.get("quantidade") or 0) * float(i.get("valor_unitario") or 0)
                ),
            }
            for i in itens
        ]),
        unsafe_allow_html=True,
    )


def _secao_parceiro_produtos_enviados(parceiro: dict) -> None:
    """Aba 'Produtos Enviados': seleção dos produtos que ficam com o parceiro,
    fechamento do envio e histórico. As vendas do canal continuam disponíveis
    no expander ao final."""
    try:
        envios = _buscar_envios(parceiro["id"])
        itens_por_envio = _buscar_itens_dos_envios([e["id"] for e in envios])
    except Exception as e:
        st.error(f"Não foi possível carregar os envios: {e}")
        st.caption(
            "Verifique se as tabelas `parcerias_envios` e `parcerias_envio_itens` "
            "foram criadas no banco (script SQL de envios)."
        )
        return

    envio_aberto = next((e for e in envios if e.get("status") == "Aberto"), None)

    if envio_aberto is None:
        _render_novo_envio(parceiro, envios)
    else:
        _render_envio_aberto(parceiro, envio_aberto, itens_por_envio.get(envio_aberto["id"], []))

    st.divider()
    _render_historico_envios(parceiro, envios, itens_por_envio)

    st.divider()
    with st.expander("📈 Vendas do canal deste parceiro (por período)"):
        _secao_parceiro_vendas_canal(parceiro)


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
    parceiro_id = parceiro["id"]
    canal_id = parceiro.get("canal_id")

    try:
        fechamentos = (
            supabase.table("parcerias_fechamentos")
            .select(
                "id, numero_ciclo, data_inicio, data_fim, percentual_comissao, "
                "valor_total_vendas, valor_total_comissao, status"
            )
            .eq("parceiro_id", parceiro_id)
            .order("numero_ciclo", desc=True)
            .execute()
            .data
            or []
        )
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
                supabase.table("parcerias_fechamentos").insert({
                    "parceiro_id": parceiro_id,
                    "numero_ciclo": proximo_numero,
                    "data_inicio": data_inicio_sugerida.isoformat(),
                    "percentual_comissao": parceiro.get("percentual_comissao") or 0,
                    "status": "Aberto",
                }).execute()
                st.success(f"Ciclo #{proximo_numero} iniciado.")
                st.rerun()
            except Exception as e:
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
                st.success(
                    f"Início do ciclo #{ciclo_aberto['numero_ciclo']} alterado para "
                    f"{novo_inicio_ciclo.strftime('%d/%m/%Y')}."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível alterar o início do ciclo: {e}")
                return

        if data_inicio_ciclo and data_fim_ciclo < data_inicio_ciclo:
            st.error("A data final não pode ser antes do início do ciclo.")
            return

        try:
            ids_ja_incluidos = {
                v["venda_id"]
                for v in (
                    supabase.table("parcerias_fechamento_vendas")
                    .select("venda_id")
                    .in_(
                        "fechamento_id",
                        [f["id"] for f in fechamentos],
                    )
                    .execute()
                    .data
                    or []
                )
            }
            vendas_periodo = (
                supabase.table("vendas")
                .select(
                    "id, data_venda, cliente, valor_final, "
                    "comissao_percentual, comissao_valor, "
                    "produtos(descricao), status_venda(nome)"
                )
                .eq("canal_venda_id", canal_id)
                .gte("data_venda", data_inicio_ciclo.isoformat())
                .lte("data_venda", data_fim_ciclo.isoformat())
                .order("data_venda")
                .execute()
                .data
                or []
            )
            candidatas = [v for v in vendas_periodo if v["id"] not in ids_ja_incluidos]
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
        observacoes_ajustes = []
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

                st.success(
                    f"Ciclo #{ciclo_aberto['numero_ciclo']} fechado com "
                    f"{len(registros)} venda(s)."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível fechar o ciclo: {e}")

    st.divider()
    st.markdown("##### Histórico de ciclos")
    if not fechamentos:
        st.caption("Nenhum ciclo de fechamento registrado ainda para este parceiro.")
        return

    # Calcula o valor final de cada ciclo histórico usando a mesma regra
    # da visualização do fechamento: comissão menos retiradas de peças.
    valor_final_por_fechamento = {}
    try:
        ids_fechamentos = [f["id"] for f in fechamentos if f.get("id") is not None]
        vinculos_historico = []
        if ids_fechamentos:
            vinculos_historico = (
                supabase.table("parcerias_fechamento_vendas")
                .select("fechamento_id, venda_id, valor_comissao")
                .in_("fechamento_id", ids_fechamentos)
                .execute()
                .data
                or []
            )

        ids_vendas_historico = [v["venda_id"] for v in vinculos_historico if v.get("venda_id") is not None]
        status_por_venda = {}
        if ids_vendas_historico:
            vendas_historico = (
                supabase.table("vendas")
                .select("id, status_venda(nome), valor_final")
                .in_("id", ids_vendas_historico)
                .execute()
                .data
                or []
            )
            status_por_venda = {v["id"]: v for v in vendas_historico}

        for fechamento in fechamentos:
            fechamento_id = fechamento.get("id")
            comissao = float(fechamento.get("valor_total_comissao") or 0)
            retirada = 0.0
            for vinculo in vinculos_historico:
                if vinculo.get("fechamento_id") != fechamento_id:
                    continue
                venda = status_por_venda.get(vinculo.get("venda_id"), {})
                if (venda.get("status_venda") or {}).get("nome") == "Retirada - Parceiros":
                    retirada += float(venda.get("valor_final") or 0)
            valor_final_por_fechamento[fechamento_id] = comissao - retirada
    except Exception:
        # Se não for possível carregar os vínculos históricos, mantém a
        # comissão como fallback para não impedir a visualização do histórico.
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
            vinculos = (
                supabase.table("parcerias_fechamento_vendas")
                .select("venda_id, valor_venda, percentual_comissao, valor_comissao")
                .eq("fechamento_id", ciclo_visualizado["id"])
                .execute()
                .data
                or []
            )
            ids_vendas = [v["venda_id"] for v in vinculos]

            vendas_fechamento = []
            if ids_vendas:
                vendas_fechamento = (
                    supabase.table("vendas")
                    .select(
                        "id, data_venda, cliente, valor_final, quantidade, "
                        "produtos(descricao, codigo_interno), status_venda(nome)"
                    )
                    .in_("id", ids_vendas)
                    .order("data_venda")
                    .execute()
                    .data
                    or []
                )
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


def _limpar_estado_parceiro() -> None:
    """Remove do session_state tudo que pertence ao parceiro atualmente selecionado."""
    prefixos = ("parceiros_", "historico_ciclos_", "retirada_auto_obs_")
    for chave in list(st.session_state.keys()):
        if isinstance(chave, str) and chave.startswith(prefixos):
            del st.session_state[chave]


def _desmarcar_parceiro() -> None:
    _limpar_estado_parceiro()
    st.session_state.pop("parceiros_selecionado", None)


def secao_parceiros():
    try:
        resp = (
            supabase.table("parceiros")
            .select(
                "id, nome, canal_id, telefone, email, percentual_comissao, "
                "observacao, ativo, canais_venda(nome)"
            )
            .eq("ativo", True)
            .order("nome")
            .execute()
        )
        parceiros = resp.data or []
    except Exception as e:
        st.markdown("### 🤝 Parceiros")
        st.error(f"Não foi possível carregar os parceiros: {e}")
        return

    if not parceiros:
        st.markdown("### 🤝 Parceiros")
        st.info(
            "Nenhum parceiro cadastrado ainda. Cadastre um parceiro na aba "
            "**Cadastros Auxiliares** para começar."
        )
        return

    nome_selecionado = st.session_state.get("parceiros_selecionado")
    parceiro = next((p for p in parceiros if p["nome"] == nome_selecionado), None)

    # Parceiro salvo não existe mais (ou nada selecionado): mostra o dropdown.
    if parceiro is None:
        if nome_selecionado:
            _limpar_estado_parceiro()

        st.markdown("### 🤝 Parceiros")
        escolhido = st.selectbox(
            "Selecione um parceiro",
            options=[p["nome"] for p in parceiros],
            index=None,
            placeholder="Escolha um parceiro...",
            key="parceiros_selecao_dropdown",
        )
        if escolhido:
            # Primeira seleção: guarda o parceiro e recarrega sem o dropdown.
            st.session_state["parceiros_selecionado"] = escolhido
            st.rerun()

        st.caption("Selecione um parceiro acima para ver o fechamento e os produtos enviados.")
        return

    # Parceiro selecionado: título com nome, comissão e botão para desmarcar.
    comissao = parceiro.get("percentual_comissao")
    comissao_fmt = f"{float(comissao):g}%" if comissao is not None else "-"
    titulo = f"Parceiros: {parceiro['nome']} (Comissão: {comissao_fmt})"

    st.markdown(
        """
        <style>
        .st-key-parceiros_desmarcar button {
            min-height: 2rem;
            height: 2rem;
            width: 2rem;
            min-width: 2rem;
            padding: 0;
            border-radius: 50%;
            border: 1px solid rgba(220, 53, 69, 0.55);
            color: #dc3545;
            background: transparent;
            font-size: 1rem;
            font-weight: 700;
            line-height: 1;
        }
        .st-key-parceiros_desmarcar button:hover {
            background: #dc3545;
            border-color: #dc3545;
            color: #ffffff;
        }
        .st-key-parceiros_desmarcar button p { margin: 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Título em uma linha só: as colunas desta linha se ajustam ao conteúdo
    # (largura automática), então o ✕ fica colado ao texto, qualquer que seja
    # o tamanho do nome do parceiro.
    st.markdown(
        """
        <style>
        .st-key-parceiros_titulo [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 0.75rem !important;
        }
        .st-key-parceiros_titulo [data-testid="stColumn"] {
            width: auto !important;
            flex: 0 0 auto !important;
            min-width: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Larguras proporcionais só como reserva, caso o CSS acima não se aplique.
    largura_titulo = max(len(titulo) * 1.5, 30)
    espaco = max(100 - largura_titulo - 4, 1)
    with _container_com_key("parceiros_titulo"):
        try:
            col_titulo, col_x, _ = st.columns(
                [largura_titulo, 4, espaco], vertical_alignment="center", gap="small"
            )
        except TypeError:  # Streamlit mais antigo, sem vertical_alignment
            col_titulo, col_x, _ = st.columns([largura_titulo, 4, espaco], gap="small")

        with col_titulo:
            st.markdown(
                "<div style='font-size:1.65rem; font-weight:700; line-height:1.3; "
                f"white-space:nowrap;'>🤝 {html.escape(titulo)}</div>",
                unsafe_allow_html=True,
            )
        with col_x:
            st.button(
                "✕",
                key="parceiros_desmarcar",
                help="Desmarcar parceiro",
                on_click=_desmarcar_parceiro,
            )

    sub_fechamento, sub_produtos = st.tabs(["📑 Fechamento", "📦 Produtos Enviados"])

    with sub_fechamento:
        _secao_parceiro_fechamento(parceiro)

    with sub_produtos:
        _secao_parceiro_produtos_enviados(parceiro)
