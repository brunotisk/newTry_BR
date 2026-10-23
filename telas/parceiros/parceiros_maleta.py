"""Parceiros > Maleta: montagem, conferência de saída, ajustes, recebimento e histórico de envios.

Ponto de entrada: secao_maleta(parceiro)."""
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import uuid

import streamlit as st

from db import buscar_todos, supabase

from telas.parceiros.parceiros_comum import (
    STATUS_ATIVOS,
    TAM_FOTO,
    _flash,
    _fmt_data,
    _fmt_moeda,
    _fmt_qtd,
    _fmt_qtd_ou_traco,
    _fragmento,
    _html_passos,
    _iguais,
    _menu,
    _mostrar_flash,
    _norm,
    _panorama_parceiros,
    _parse_data_segura,
)


# ----------------------------------------------------------------------
# Fotos dos produtos (coluna produtos.foto_url; bucket público do Supabase)
# ----------------------------------------------------------------------
# As fotos podem ser URLs públicas completas ou caminhos relativos dentro do bucket.
# URLs antigas de outros buckets do Supabase são redirecionadas para o bucket atual,
# mantendo o mesmo caminho do arquivo. URLs da Shopify continuam com miniatura reduzida.
# As imagens são renderizadas como <img> comuns do navegador.
BUCKET_FOTOS = "fotos_produtos"

LARGURA_MINIATURA = 160   # largura solicitada à CDN da Shopify

# Fotos no bucket do Supabase costumam estar em tamanho original (pesadas para uma
# miniatura de 56 px). Com plano que inclui "Image Transformations" (Pro), troque
# para True: o navegador passa a baixar uma versão reduzida de cada foto.
TRANSFORMAR_NO_SUPABASE = False

# ----------------------------------------------------------------------
# Fotos da maleta (conferência de saída): bucket do Supabase Storage + tabela
# parcerias_envio_arquivos (migracao_fotos_maleta.sql cria os dois).
# ----------------------------------------------------------------------
BUCKET_MALETAS = "fotos_maletas"

LADO_MAX_FOTO_MALETA = 1600   # px: a foto é reduzida antes de enviar (celular gera 4000+ px)

QUALIDADE_JPEG_MALETA = 82

TIPOS_FOTO_MALETA = ["jpg", "jpeg", "png", "webp"]

# PDFs de cada movimento da maleta (criação e ajustes): bucket PÚBLICO do Supabase Storage +
# tabela parcerias_envio_pdfs (migracao_pdfs_maleta.sql cria a tabela, a coluna e as políticas).
BUCKET_PDFS = "pdf_maletas"

EXIGIR_FOTO_SAIDA = False     # True: só confirma a saída da maleta com pelo menos 1 foto

# PDF da maleta (lista de itens para conferir/entregar): exige o pacote `reportlab`.
PDF_FOTO_PX = 240             # lado da miniatura embutida (arquivo leve)

PDF_FOTO_MM = 18              # tamanho da foto na página

def _url_miniatura_base(url: str) -> str:
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

@lru_cache(maxsize=4096)
def _url_miniatura(url: str) -> str:
    """URL final da miniatura. O resultado é memorizado por URL: sem isso cada foto
    era recalculada (inclusive via cliente do Storage) a cada interação."""
    final = _url_miniatura_base(url)
    marcador = "/storage/v1/object/public/"
    if TRANSFORMAR_NO_SUPABASE and marcador in final:
        partes = urlparse(final)
        query = dict(parse_qsl(partes.query))
        query.update(
            width=str(LARGURA_MINIATURA), height=str(LARGURA_MINIATURA),
            resize="cover", quality="70",
        )
        final = urlunparse(partes._replace(
            path=partes.path.replace(marcador, "/storage/v1/render/image/public/", 1),
            query=urlencode(query),
        ))
    return final

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
    return '<table class="tab-fotos">' + cabecalho + "".join(corpo) + "</table>"

TIPOS_MOVIMENTO = {"adicao": "➕ Adição", "remocao": "➖ Remoção"}

ETAPAS_ENVIADO = ["📦 Maleta", "✏️ Ajustar maleta", "📥 Receber e fechar ciclo"]

OPCOES_AJUSTE = ["1. Remover itens da maleta", "2. Adicionar itens à maleta"]

# Escolha "descrição do site x descrição padrão": um valor único, compartilhado por todos os
# campos de seleção de produtos (cada tela tem o seu interruptor, todos sincronizados).
CHAVE_USAR_SITE = "parceiros_usar_descricao_site"

def _celula_foto(url) -> str:
    url = str(url or "").strip()
    if url:
        return f'<img src="{html.escape(_url_miniatura(url), quote=True)}" loading="lazy" alt="">'
    return '<div class="sem-foto">sem foto</div>'

def _html_tabela(cabecalhos: list, linhas: list, colunas_num=()) -> str:
    """Tabela HTML somente leitura. Cada linha é uma lista de células: texto
    (escapado) ou ("foto", url). `colunas_num`: índices alinhados à direita."""
    ths = "".join(
        f'<th class="num">{html.escape(h)}</th>' if i in colunas_num else f"<th>{html.escape(h)}</th>"
        for i, h in enumerate(cabecalhos)
    )
    trs = []
    for linha in linhas:
        tds = []
        for i, c in enumerate(linha):
            if isinstance(c, tuple) and c and c[0] == "foto":
                tds.append(f"<td>{_celula_foto(c[1])}</td>")
            else:
                cls = ' class="num"' if i in colunas_num else ""
                texto = html.escape(str(c)).replace("$", "&#36;")
                tds.append(f"<td{cls}>{texto}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return '<table class="tab-fotos"><tr>' + ths + "</tr>" + "".join(trs) + "</table>"

# ---------------------------------------------------------------------------
# Acesso a dados
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def _buscar_envios(parceiro_id) -> list:
    return (
        supabase.table("parcerias_envios")
        .select(
            "id, numero_envio, data_envio, status, observacao, enviado_em, fechado_em, "
            "obs_conferencia_saida, obs_fechamento, total_itens, total_pecas, "
            "total_vendido, total_devolvido, total_divergencia, valor_total"
        )
        .eq("parceiro_id", parceiro_id)
        .order("numero_envio", desc=True)
        .execute()
        .data
        or []
    )

@st.cache_data(ttl=60, show_spinner=False)
def _buscar_itens_dos_envios(ids_envios: tuple) -> dict:
    """Devolve {envio_id: [itens]} em uma única consulta."""
    if not ids_envios:
        return {}
    itens = buscar_todos(
        lambda: supabase.table("parcerias_envio_itens")
        .select(
            "id, envio_id, produto_id, quantidade, valor_unitario, conferido_saida, "
            "qtd_vendida, qtd_devolvida, qtd_divergencia, "
            "produtos(codigo_interno, descricao, foto_url"
            ", produto_descricao_site)"
        )
        .in_("envio_id", list(ids_envios))
        .order("id")
    )
    por_envio: dict = {}
    for item in itens:
        por_envio.setdefault(item["envio_id"], []).append(item)
    return por_envio

@st.cache_data(ttl=60, show_spinner=False)
def _buscar_movimentos(ids_envios: tuple) -> dict:
    """Devolve {envio_id: [movimentos]} (adições/remoções durante o ciclo)."""
    if not ids_envios:
        return {}
    movs = buscar_todos(
        lambda: supabase.table("parcerias_envio_movimentos")
        .select(
            "id, envio_id, produto_id, tipo, quantidade, motivo, criado_em, "
            f"{'pdf_id, ' if _tem_pdfs_maleta() else ''}"
            "produtos(codigo_interno, descricao)"
        )
        .in_("envio_id", list(ids_envios))
        .order("id")
    )
    por_envio: dict = {}
    for m in movs:
        por_envio.setdefault(m["envio_id"], []).append(m)
    return por_envio

@st.cache_data(ttl=300, show_spinner=False)
def _tem_pdfs_maleta() -> bool:
    """True se a tabela parcerias_envio_pdfs e a coluna movimentos.pdf_id existem
    (migracao_pdfs_maleta.sql). Sem elas a tela funciona como antes, só não guarda PDFs."""
    try:
        supabase.table("parcerias_envio_pdfs").select("id").limit(1).execute()
        supabase.table("parcerias_envio_movimentos").select("pdf_id").limit(1).execute()
        return True
    except Exception:
        return False

@st.cache_data(ttl=60, show_spinner=False)
def _buscar_pdfs_maleta(ids_envios: tuple) -> dict:
    """{envio_id: [PDFs guardados]} (criação e ajustes), do mais antigo ao mais novo."""
    if not ids_envios or not _tem_pdfs_maleta():
        return {}
    linhas = buscar_todos(
        lambda: supabase.table("parcerias_envio_pdfs")
        .select(
            "id, envio_id, evento, motivo, total_itens, total_pecas, "
            "caminho_padrao, caminho_site, criado_em"
        )
        .in_("envio_id", list(ids_envios))
        .order("id")
    )
    por_envio: dict = {}
    for l in linhas:
        por_envio.setdefault(l["envio_id"], []).append(l)
    return por_envio

@st.cache_data(ttl=30, show_spinner=False)
def _vendido_por_produto(canal_id, desde) -> dict:
    """{produto_id: quantidade vendida} no canal do parceiro desde `desde`
    (inclusive). Inclui todas as vendas do canal, também as 'Retirada - Parceiros'."""
    desde = str(desde or "")[:10]
    if not canal_id or not desde:
        return {}
    vendas = buscar_todos(
        lambda: supabase.table("vendas")
        .select("produto_id, quantidade")
        .eq("canal_venda_id", canal_id)
        .gte("data_venda", desde)
        .order("id")
    )
    total: dict = {}
    for v in vendas:
        pid = v["produto_id"]
        total[pid] = total.get(pid, 0.0) + float(v.get("quantidade") or 0)
    return total

SEM_CATEGORIA = "(sem categoria)"

SEM_BANHO = "(sem banho)"

def _toggle_descricao_site(chave_local: str) -> bool:
    """Interruptor 'descrição do site' (produto_descricao_site) x descrição padrão (descricao).

    Cada lista de produtos chama este helper com a sua própria key; o valor fica em
    CHAVE_USAR_SITE e vale para todas. Devolve True quando deve usar a descrição do site."""
    interruptor = getattr(st, "toggle", st.checkbox)

    def _sincronizar():
        st.session_state[CHAVE_USAR_SITE] = bool(st.session_state.get(chave_local))

    st.session_state[chave_local] = bool(st.session_state.get(CHAVE_USAR_SITE, False))
    usar = interruptor(
        "Descrição do site",
        key=chave_local,
        on_change=_sincronizar,
        help="Ligado: mostra produto_descricao_site (produtos sem esse texto continuam com a "
             "descrição padrão). Desligado: mostra a descrição padrão (descricao).",
    )
    return bool(usar)

@st.cache_data(ttl=300, show_spinner=False)
def _buscar_categorias() -> dict:
    """{categoria_id: {'categoria', 'banho'}} de categorias_produtos (cadastro pequeno).
    Inclui as inativas: um produto antigo ainda pode apontar para elas."""
    try:
        linhas = buscar_todos(
            lambda: supabase.table("categorias_produtos")
            .select("id, categoria, banho")
            .order("id")
        )
    except Exception:
        return {}
    return {
        l["id"]: {"categoria": (l.get("categoria") or "").strip(), "banho": (l.get("banho") or "").strip()}
        for l in linhas
    }

@st.cache_data(ttl=30, show_spinner=False)
def _carregar_produtos_disponiveis() -> dict:
    """Produtos com saldo em estoque, descontando o que está reservado em envios
    ATIVOS (de qualquer parceiro):
      - envio Aberto (em montagem): a quantidade inteira;
      - envio Enviado: o que ainda está com o parceiro (enviado - vendido), porque
        o que já foi vendido já saiu do saldo do estoque pelo fluxo de vendas.

    Retorna {produto_id: {codigo, descricao, foto_url, categoria, banho, saldo, comprometido,
    disponivel, preco, busca_cod, busca_desc}}.
    """
    linhas_estoque = buscar_todos(
        lambda: supabase.table("estoque")
        .select(
            "quantidade_atual, estoque_preco_venda_sugerida, "
            "produtos(id, codigo_interno, descricao, foto_url, categoria_id"
            ", produto_descricao_site)"
        )
        .gt("quantidade_atual", 0)
        .order("produto_id")
    )

    ativos = (
        supabase.table("parcerias_envios")
        .select("id, status, data_envio, parceiros(canal_id)")
        .in_("status", list(STATUS_ATIVOS))
        .execute()
        .data
        or []
    )
    comprometido: dict = {}
    if ativos:
        ids_ativos = [e["id"] for e in ativos]
        itens_ativos = buscar_todos(
            lambda: supabase.table("parcerias_envio_itens")
            .select("envio_id, produto_id, quantidade")
            .in_("envio_id", ids_ativos)
            .order("id")
        )
        por_envio: dict = {}
        for it in itens_ativos:
            por_envio.setdefault(it["envio_id"], []).append(it)

        for e in ativos:
            vendidos = {}
            if e.get("status") == "Enviado":
                canal = (e.get("parceiros") or {}).get("canal_id")
                vendidos = _vendido_por_produto(canal, e.get("data_envio"))
            for it in por_envio.get(e["id"], []):
                pid = it["produto_id"]
                qtd = float(it.get("quantidade") or 0)
                com_parceiro = max(qtd - vendidos.get(pid, 0.0), 0.0) if vendidos else qtd
                comprometido[pid] = comprometido.get(pid, 0.0) + com_parceiro

    categorias = _buscar_categorias()
    produtos: dict = {}
    for linha in linhas_estoque:
        produto = linha.get("produtos")
        if not produto:
            continue
        cat = categorias.get(produto.get("categoria_id"), {})
        saldo = float(linha.get("quantidade_atual") or 0)
        reservado = comprometido.get(produto["id"], 0.0)
        produtos[produto["id"]] = {
            "codigo": produto.get("codigo_interno") or "-",
            "descricao": produto.get("descricao") or "-",
            "foto_url": produto.get("foto_url"),
            "categoria": cat.get("categoria", ""),
            "banho": cat.get("banho", ""),
            "busca_cod": _norm(produto.get("codigo_interno")),
            "busca_desc": _norm(produto.get("descricao")),
            "produto_descricao_site": (produto.get("produto_descricao_site") or "").strip(),
            "busca_site": _norm(produto.get("produto_descricao_site")),
            "saldo": saldo,
            "comprometido": reservado,
            "disponivel": max(saldo - reservado, 0.0),
            "preco": float(linha.get("estoque_preco_venda_sugerida") or 0),
        }
    return produtos

def _registrar_movimentos(envio_id, movimentos: list, motivo: str = "", pdf_id=None) -> None:
    """movimentos: lista de (produto_id, 'adicao'|'remocao', quantidade). `pdf_id` liga os
    movimentos ao PDF guardado para esse ajuste (parcerias_envio_pdfs)."""
    if not movimentos:
        return
    linhas = [
        {
            "envio_id": envio_id,
            "produto_id": pid,
            "tipo": tipo,
            "quantidade": qtd,
            "motivo": (motivo or "").strip() or None,
        }
        for pid, tipo, qtd in movimentos
    ]
    if pdf_id is not None:
        for linha in linhas:
            linha["pdf_id"] = pdf_id
    supabase.table("parcerias_envio_movimentos").insert(linhas).execute()

def _invalidar_cache_envios() -> None:
    """Descarta as leituras em cache que dependem dos envios. Chamada após qualquer
    gravação (e também quando uma gravação falha, que pode ter sido parcial)."""
    _buscar_envios.clear()
    _buscar_itens_dos_envios.clear()
    _buscar_movimentos.clear()
    _buscar_pdfs_maleta.clear()
    _buscar_arquivos.clear()
    _carregar_produtos_disponiveis.clear()
    _panorama_parceiros.clear()

def _atualizar_vendas_fechamento() -> None:
    """Dentro do fechamento: recarrega as vendas do sistema e refaz os campos."""
    _vendido_por_produto.clear()
    _resetar_editores_envio()

def _resetar_editores_envio() -> None:
    """Força os campos de edição a recomeçarem do zero (troca a key deles)."""
    _invalidar_cache_envios()
    st.session_state["parceiros_envio_reset"] = (
        st.session_state.get("parceiros_envio_reset", 0) + 1
    )

# ---------------------------------------------------------------------------
# Novo envio
# ---------------------------------------------------------------------------

def _render_novo_envio(parceiro: dict, envios: list, itens_por_envio: dict) -> None:
    proximo = (envios[0]["numero_envio"] + 1) if envios else 1
    ultimo = envios[0] if envios else None

    devolvidos = []
    if ultimo and ultimo.get("status") == "Fechado":
        devolvidos = [
            it for it in itens_por_envio.get(ultimo["id"], [])
            if float(it.get("qtd_devolvida") or 0) > 0
        ]

    st.info(
        f"Nenhuma maleta em andamento. O próximo será o **envio #{proximo}**. "
        "Gere-o para montar a maleta com os produtos que ficarão com o parceiro."
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

    copiar = False
    if devolvidos:
        pecas = sum(float(it.get("qtd_devolvida") or 0) for it in devolvidos)
        copiar = st.checkbox(
            f"Montar o novo envio com os {len(devolvidos)} produto(s) devolvidos no "
            f"envio #{ultimo['numero_envio']} ({_fmt_qtd(pecas)} peça(s))",
            value=True,
            key=f"parceiros_envio_copiar_{ultimo['id']}",
            help="Você ainda poderá ajustar quantidades e adicionar produtos antes de enviar.",
        )

    if st.button("▶️ Gerar novo envio", key="parceiros_envio_iniciar", type="primary"):
        try:
            criado = supabase.table("parcerias_envios").insert({
                "parceiro_id": parceiro["id"],
                "numero_envio": proximo,
                "data_envio": data_envio.isoformat(),
                "observacao": observacao.strip() or None,
                "status": "Aberto",
            }).execute().data
            novo_id = criado[0]["id"]

            sem_saldo = []
            if copiar and devolvidos:
                disp = _carregar_produtos_disponiveis()
                registros = []
                for it in devolvidos:
                    devolvida = float(it.get("qtd_devolvida") or 0)
                    p = disp.get(it["produto_id"])
                    qtd = min(devolvida, p["disponivel"]) if p else 0.0
                    if qtd > 0:
                        registros.append({
                            "envio_id": novo_id,
                            "produto_id": it["produto_id"],
                            "quantidade": qtd,
                            "valor_unitario": p["preco"],
                        })
                    if qtd < devolvida:
                        sem_saldo.append((it.get("produtos") or {}).get("codigo_interno") or it["produto_id"])
                if registros:
                    supabase.table("parcerias_envio_itens").insert(registros).execute()

            _resetar_editores_envio()
            if sem_saldo:
                _flash(
                    "warning",
                    f"Envio #{proximo} gerado, mas estes produtos não puderam voltar "
                    f"(total ou parcialmente) por falta de saldo disponível: "
                    f"{', '.join(map(str, sem_saldo))}.",
                )
            else:
                _flash("success", f"Envio #{proximo} gerado. Monte a maleta e faça a conferência de saída.")
            st.rerun()
        except Exception as e:
            _invalidar_cache_envios()
            st.error(f"Não foi possível gerar o envio: {e}")

# ---------------------------------------------------------------------------
# Edição dos itens e adição de produtos (montagem e ajustes durante o ciclo)
# ---------------------------------------------------------------------------

def _classificar_alteracoes(itens: list, linhas_editadas: list, disp: dict, minimos: dict = None):
    """Compara a edição com o que está gravado.

    `minimos`: {produto_id: quantidade já vendida}. A quantidade não pode ficar
    abaixo disso e o item não pode ser removido enquanto houver venda.

    Retorna (ids_para_remover, [(id, nova_qtd)], erros).
    """
    minimos = minimos or {}
    por_id = {it["id"]: it for it in itens}
    remover, atualizar, erros = [], [], []
    for linha in linhas_editadas:
        item = por_id.get(linha["id"])
        if not item:
            continue
        pid = item["produto_id"]
        minimo = float(minimos.get(pid, 0.0))
        codigo = (item.get("produtos") or {}).get("codigo_interno") or pid
        if linha.get("Remover"):
            if minimo > 0:
                erros.append(
                    f"{codigo}: não dá para remover, já houve venda de {_fmt_qtd(minimo)}. "
                    "Reduza a quantidade até o vendido."
                )
            else:
                remover.append(item["id"])
            continue
        atual = float(item.get("quantidade") or 0)
        nova = float(linha.get("Qtd") or 0)
        if _iguais(nova, atual):
            continue
        if minimo > 0 and nova < minimo:
            erros.append(f"{codigo}: quantidade menor que o já vendido ({_fmt_qtd(minimo)}).")
            continue
        if nova <= 0:
            erros.append(f"{codigo}: a quantidade deve ser maior que zero (ou marque Remover).")
            continue
        maximo = atual + disp.get(pid, {}).get("disponivel", 0.0)
        if nova > maximo:
            erros.append(
                f"{codigo}: quantidade {_fmt_qtd(nova)} maior que o disponível "
                f"({_fmt_qtd(maximo)})."
            )
            continue
        atualizar.append((item["id"], nova))
    return remover, atualizar, erros

def _secao_itens_envio(
    envio: dict, itens: list, disp: dict, modo: str,
    vendidos: dict = None, liberado: bool = True, motivo: str = "",
) -> bool:
    """Lista editável dos produtos do envio (quantidade e remoção).

    modo 'Aberto': edita livremente. modo 'Enviado': cada mudança também é gravada
    como movimentação. `liberado=False` bloqueia o salvamento (conferência pendente).
    Retorna True se há alterações ainda não salvas.
    """
    envio_id = envio["id"]
    reset = st.session_state.get("parceiros_envio_reset", 0)
    vendidos = vendidos or {}

    st.markdown("**Produtos da maleta**" if modo == "Enviado" else "**Produtos selecionados para este envio**")
    if not itens:
        st.caption("Nenhum produto selecionado ainda. Use a seção abaixo para adicionar.")
        return False

    usar_site = _toggle_descricao_site(f"parceiros_tg_itens_{envio_id}")
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
            vendido = min(float(vendidos.get(it["produto_id"], 0.0)), float(it.get("quantidade") or 0))
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
            c_prod.write(_descricao_pdf(prod, usar_site))
            if vendido > 0:
                c_prod.caption(f"já vendido: {_fmt_qtd(vendido)}")
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

    minimos = vendidos if modo == "Enviado" else {}
    remover, atualizar, erros = _classificar_alteracoes(itens, linhas_editadas, disp, minimos)
    pendentes = bool(remover or atualizar or erros)

    for erro in erros:
        st.error(erro)
    if modo == "Enviado" and (remover or atualizar) and not liberado:
        st.info("Marque a conferência física acima para poder salvar as alterações.")

    if st.button(
        "💾 Salvar alterações",
        key="parceiros_envio_salvar",
        disabled=not (remover or atualizar) or bool(erros) or not liberado,
    ):
        # A lista na tela usa dados em cache (até 30 s); ao gravar, confere de novo
        # com o estoque e as reservas atuais.
        _carregar_produtos_disponiveis.clear()
        remover, atualizar, erros_agora = _classificar_alteracoes(
            itens, linhas_editadas, _carregar_produtos_disponiveis(), minimos
        )
        try:
            if erros_agora:
                raise ValueError(" | ".join(erros_agora))
            por_id = {it["id"]: it for it in itens}
            movimentos = []
            if remover:
                for item_id in remover:
                    movimentos.append(
                        (por_id[item_id]["produto_id"], "remocao", float(por_id[item_id]["quantidade"] or 0))
                    )
                supabase.table("parcerias_envio_itens").delete().in_("id", remover).execute()
            for item_id, nova_qtd in atualizar:
                atual = float(por_id[item_id]["quantidade"] or 0)
                supabase.table("parcerias_envio_itens").update(
                    {"quantidade": nova_qtd}
                ).eq("id", item_id).execute()
                movimentos.append((
                    por_id[item_id]["produto_id"],
                    "adicao" if nova_qtd > atual else "remocao",
                    abs(nova_qtd - atual),
                ))
            if modo == "Enviado":
                _registrar_movimentos(envio_id, movimentos, motivo)
            _resetar_editores_envio()
            _flash(
                "success",
                "Alterações salvas e registradas nas movimentações."
                if modo == "Enviado" else "Alterações salvas.",
            )
            st.rerun()
        except Exception as e:
            _invalidar_cache_envios()
            st.error(f"Não foi possível salvar as alterações: {e}")

    return pendentes

def _busca_desc(produto: dict, usar_site: bool) -> str:
    """Texto (sem acentos) em que o filtro 'Produto' procura: a descrição padrão e, com o
    interruptor ligado, também a descrição do site."""
    base = produto.get("busca_desc") or ""
    return f"{base} {produto.get('busca_site') or ''}" if usar_site else base

def _cb_limpar_filtros(chaves: tuple) -> None:
    for chave in chaves:
        st.session_state[chave] = [] if chave.endswith(("_cat", "_banho")) else ""

@_fragmento
def _lista_candidatos(
    envio: dict, itens: list, disp: dict, modo: str,
    liberado: bool = True, motivo: str = "",
) -> None:
    """Lista paginada (com fotos) para escolher novos produtos para a maleta."""
    envio_id = envio["id"]
    reset = st.session_state.get("parceiros_envio_reset", 0)

    usar_site = _toggle_descricao_site(f"parceiros_tg_cand_{envio_id}")

    ja_no_envio = {it["produto_id"] for it in itens}
    disponiveis = [
        (pid, p) for pid, p in disp.items()
        if pid not in ja_no_envio and p["disponivel"] > 0
    ]

    # --- filtros: código, produto, categoria e banho (todos combinam entre si) ---
    prefixo = f"parceiros_filtro_{envio_id}"
    k_cod, k_prod, k_cat, k_banho = (f"{prefixo}_{n}" for n in ("cod", "prod", "cat", "banho"))
    opcoes_cat = sorted({p.get("categoria") or SEM_CATEGORIA for _, p in disponiveis}, key=_norm)
    opcoes_banho = sorted({p.get("banho") or SEM_BANHO for _, p in disponiveis}, key=_norm)
    for chave, opcoes in ((k_cat, opcoes_cat), (k_banho, opcoes_banho)):
        if chave in st.session_state:  # descarta o que deixou de existir (ex.: acabou o saldo)
            st.session_state[chave] = [v for v in st.session_state[chave] if v in opcoes]

    mostrar_categorias = opcoes_cat != [SEM_CATEGORIA] or opcoes_banho != [SEM_BANHO]
    c_cod, c_prod, c_cat, c_banho = st.columns([1.1, 1.7, 1.4, 1.4])
    filtro_cod = c_cod.text_input(
        "Código", key=k_cod, placeholder="13465 ou vários: 13465, 13470",
        help="Aceita um ou mais códigos separados por vírgula, ponto e vírgula ou espaço.",
    )
    filtro_prod = c_prod.text_input(
        "Produto", key=k_prod, placeholder="ex.: brinco coração",
        help="Todas as palavras precisam aparecer na descrição. Acentos não importam.",
    )
    cats_sel = banhos_sel = []
    if mostrar_categorias:
        cats_sel = c_cat.multiselect("Categoria", opcoes_cat, key=k_cat, placeholder="Todas")
        banhos_sel = c_banho.multiselect("Banho", opcoes_banho, key=k_banho, placeholder="Todos")

    tokens_cod = [t for t in re.split(r"[\s,;]+", _norm(filtro_cod)) if t]
    palavras = [w for w in _norm(filtro_prod).split() if w]
    filtros_ativos = bool(tokens_cod or palavras or cats_sel or banhos_sel)

    candidatos = [
        (pid, p) for pid, p in disponiveis
        if (not tokens_cod or any(t in p["busca_cod"] for t in tokens_cod))
        and all(w in _busca_desc(p, usar_site) for w in palavras)
        and (not cats_sel or (p.get("categoria") or SEM_CATEGORIA) in cats_sel)
        and (not banhos_sel or (p.get("banho") or SEM_BANHO) in banhos_sel)
    ]

    if filtros_ativos:
        col_n, col_limpar = st.columns([4, 1])
        col_n.caption(f"{len(candidatos)} de {len(disponiveis)} produto(s) com os filtros aplicados.")
        col_limpar.button(
            "Limpar filtros", key=f"{prefixo}_limpar",
            on_click=_cb_limpar_filtros, args=((k_cod, k_prod, k_cat, k_banho),),
        )

    if not candidatos:
        st.info("Nenhum produto disponível para adicionar com estes filtros.")
        return

    # Seleção guardada em session_state: sobrevive à troca de página e de busca.
    base = f"parceiros_cand_{envio_id}_{reset}"
    sel = st.session_state.setdefault(f"{base}_sel", {})
    for pid in list(sel):  # descarta o que deixou de estar disponível
        if pid in ja_no_envio or pid not in disp or disp[pid]["disponivel"] <= 0:
            sel.pop(pid)

    POR_PAGINA = 20
    n_paginas = max(1, (len(candidatos) + POR_PAGINA - 1) // POR_PAGINA)
    sufixo_filtro = hashlib.md5(
        "|".join([filtro_cod, filtro_prod, ",".join(sorted(cats_sel)), ",".join(sorted(banhos_sel))]).encode("utf-8")
    ).hexdigest()[:8]  # muda ao mudar qualquer filtro: a paginação volta para a página 1
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
            c_prod.write(_descricao_pdf(p, usar_site))
            detalhe = " · ".join(x for x in (p.get("categoria"), p.get("banho")) if x)
            if detalhe:
                c_prod.caption(detalhe)
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
    if modo == "Ajuste":
        # Ajuste da maleta com o parceiro: nada é gravado aqui. A seleção vai para a prévia
        # "maleta como ficaria" (fora deste fragmento), que só é atualizada por um rerun completo.
        st.caption(
            f"{len(marcados)} produto(s) marcado(s) para entrar na maleta. Veja a prévia abaixo; "
            "a alteração só é gravada ao salvar."
        )
        assinatura = tuple(sorted((str(k), float(v)) for k, v in sel.items()))
        k_prev = f"{base}_sel_prev"
        if st.session_state.get(k_prev, ()) != assinatura:
            st.session_state[k_prev] = assinatura
            st.rerun()
        return

    if modo == "Enviado" and marcados and not liberado:
        st.info("Marque a conferência física acima para poder adicionar à maleta.")

    if st.button(
        f"➕ Adicionar selecionados ({len(marcados)})",
        key="parceiros_envio_adicionar",
        disabled=not marcados or not liberado,
    ):
        _carregar_produtos_disponiveis.clear()
        disp_agora = _carregar_produtos_disponiveis()
        erros_add = []
        novos = []
        for linha in marcados:
            qtd = float(linha.get("Qtd") or 0)
            maximo = float(disp_agora.get(linha["produto_id"], {}).get("disponivel", 0.0))
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
                if modo == "Enviado":
                    _registrar_movimentos(
                        envio_id,
                        [(n["produto_id"], "adicao", n["quantidade"]) for n in novos],
                        motivo,
                    )
                _resetar_editores_envio()
                _flash("success", f"{len(novos)} produto(s) adicionado(s) ao envio.")
                st.rerun()
            except Exception as e:
                _invalidar_cache_envios()
                st.error(f"Não foi possível adicionar os produtos: {e}")

# ---------------------------------------------------------------------------
# Etapa 1: montagem da maleta + conferência de saída
# ---------------------------------------------------------------------------


def _secao_adicionar_produtos(
    envio: dict, itens: list, disp: dict, modo: str,
    liberado: bool = True, motivo: str = "",
) -> None:
    """Seleção de novos produtos. A lista só é montada com o interruptor ligado: antes ela
    era desenhada mesmo recolhida (centenas de elementos a cada interação)."""
    interruptor = getattr(st, "toggle", st.checkbox)
    aberto = interruptor(
        "➕ Adicionar produtos ao envio",
        value=not itens,
        key=f"parceiros_add_aberto_{envio['id']}",
    )
    if aberto:
        _lista_candidatos(envio, itens, disp, modo, liberado, motivo)

@st.cache_data(ttl=60, show_spinner=False)
def _buscar_arquivos(ids_envios: tuple) -> dict:
    """{envio_id: [arquivos]} (fotos da maleta), mais antigas primeiro."""
    if not ids_envios:
        return {}
    linhas = buscar_todos(
        lambda: supabase.table("parcerias_envio_arquivos")
        .select("id, envio_id, etapa, caminho_storage, nome_arquivo, mime_type, tamanho_bytes, criado_em")
        .in_("envio_id", list(ids_envios))
        .order("id")
    )
    por_envio: dict = {}
    for a in linhas:
        por_envio.setdefault(a["envio_id"], []).append(a)
    return por_envio

@lru_cache(maxsize=2048)
def _url_arquivo(caminho: str) -> str:
    """URL pública da foto no bucket (calculada uma vez por arquivo)."""
    return str(supabase.storage.from_(BUCKET_MALETAS).get_public_url(caminho))

def _preparar_imagem(conteudo: bytes, nome: str):
    """Corrige a rotação do celular, reduz para LADO_MAX_FOTO_MALETA px e converte para JPEG.
    Uma foto de 6 MB vira ~300 KB: menos espaço no Storage e a tela abre mais rápido.
    Sem a biblioteca Pillow, envia o arquivo original. Arquivo que não é imagem gera erro.
    Retorna (bytes, mime, extensão)."""
    extensao = "." + nome.rsplit(".", 1)[-1].lower() if "." in (nome or "") else ".jpg"
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
    except ImportError:
        return conteudo, "image/png" if extensao == ".png" else "image/jpeg", extensao
    try:
        imagem = ImageOps.exif_transpose(Image.open(BytesIO(conteudo)))
        if imagem.mode in ("RGBA", "LA", "P"):
            rgba = imagem.convert("RGBA")
            fundo = Image.new("RGB", rgba.size, (255, 255, 255))
            fundo.paste(rgba, mask=rgba.split()[3])
            imagem = fundo
        else:
            imagem = imagem.convert("RGB")
        imagem.thumbnail((LADO_MAX_FOTO_MALETA, LADO_MAX_FOTO_MALETA))
        saida = BytesIO()
        imagem.save(saida, format="JPEG", quality=QUALIDADE_JPEG_MALETA, optimize=True)
        return saida.getvalue(), "image/jpeg", ".jpg"
    except Exception as e:
        raise ValueError(f"não é uma imagem válida ({e.__class__.__name__})")

def _salvar_fotos_maleta(envio_id, etapa: str, arquivos: list):
    """Envia as fotos ao Storage e registra cada uma em parcerias_envio_arquivos.
    Retorna (quantidade_salva, lista_de_erros)."""
    salvas, erros = 0, []
    bucket = supabase.storage.from_(BUCKET_MALETAS)
    for f in arquivos:
        caminho = None
        try:
            conteudo, mime, extensao = _preparar_imagem(f.getvalue(), f.name)
            caminho = f"envio_{envio_id}/{etapa}/{uuid.uuid4().hex}{extensao}"
            bucket.upload(caminho, conteudo, {"content-type": mime})
            supabase.table("parcerias_envio_arquivos").insert({
                "envio_id": envio_id,
                "etapa": etapa,
                "caminho_storage": caminho,
                "nome_arquivo": f.name,
                "mime_type": mime,
                "tamanho_bytes": len(conteudo),
            }).execute()
            salvas += 1
        except Exception as e:
            erros.append(f"{f.name}: {e}")
            if caminho:  # não deixa arquivo órfão no Storage se o registro falhou
                try:
                    bucket.remove([caminho])
                except Exception:
                    pass
    return salvas, erros

def _remover_foto_maleta(arquivo: dict) -> None:
    try:
        supabase.storage.from_(BUCKET_MALETAS).remove([arquivo["caminho_storage"]])
        supabase.table("parcerias_envio_arquivos").delete().eq("id", arquivo["id"]).execute()
        _buscar_arquivos.clear()
        _flash("success", "Foto removida.")
    except Exception as e:
        _buscar_arquivos.clear()
        _flash("warning", f"Não foi possível remover a foto: {e}")

def _grade_fotos(arquivos: list, editavel: bool = False) -> None:
    """Miniaturas em grade; clicar na imagem abre em tela cheia."""
    por_linha = 4
    for ini in range(0, len(arquivos), por_linha):
        colunas = st.columns(por_linha)
        for coluna, a in zip(colunas, arquivos[ini:ini + por_linha]):
            with coluna:
                st.image(
                    _url_arquivo(a["caminho_storage"]),
                    caption=f"{'Saída' if a.get('etapa') == 'saida' else 'Retorno'} · {_fmt_data(a.get('criado_em'))}",
                    use_container_width=True,
                )
                if editavel and st.button("🗑 Remover", key=f"parceiros_foto_rm_{a['id']}"):
                    _remover_foto_maleta(a)
                    st.rerun()

def _secao_fotos_maleta(envio_id, etapa: str = "saida") -> int:
    """Área de fotos da conferência: fotos já salvas + envio de novas. Retorna quantas existem."""
    st.markdown("**📷 Fotos da maleta**" + ("" if EXIGIR_FOTO_SAIDA else " (opcional)"))
    try:
        todas = _buscar_arquivos((envio_id,)).get(envio_id, [])
    except Exception as e:
        st.warning(
            f"Não foi possível acessar as fotos da maleta ({e}). Execute o script "
            "migracao_fotos_maleta.sql no Supabase para habilitar este recurso."
        )
        return 0
    existentes = [a for a in todas if a.get("etapa") == etapa]
    if existentes:
        _grade_fotos(existentes, editavel=True)

    contador = f"parceiros_fotos_reset_{envio_id}_{etapa}"
    novos = st.file_uploader(
        "Adicionar fotos da maleta",
        type=TIPOS_FOTO_MALETA,
        accept_multiple_files=True,
        key=f"parceiros_fotos_up_{envio_id}_{etapa}_{st.session_state.get(contador, 0)}",
        help="Tire uma foto da maleta aberta, com as peças à vista, antes de enviar. "
             "No celular, a opção de câmera aparece ao tocar aqui.",
    )
    if st.button(
        f"📤 Salvar {len(novos)} foto(s)" if novos else "📤 Salvar fotos",
        key=f"parceiros_fotos_salvar_{envio_id}_{etapa}",
        disabled=not novos,
    ):
        salvas, erros = _salvar_fotos_maleta(envio_id, etapa, novos)
        _buscar_arquivos.clear()
        st.session_state[contador] = st.session_state.get(contador, 0) + 1  # esvazia o campo de upload
        if erros:
            _flash("warning", f"{salvas} foto(s) salva(s). Falharam: " + " | ".join(erros))
        else:
            _flash("success", f"{salvas} foto(s) da maleta salva(s).")
        st.rerun()
    return len(existentes)

def _mostrar_fotos_maleta(envio_id) -> None:
    """Fotos da maleta somente para consulta (maleta enviada e histórico). Se a tabela de
    fotos ainda não existe no banco, simplesmente não mostra nada."""
    try:
        arquivos = _buscar_arquivos((envio_id,)).get(envio_id, [])
    except Exception:
        return
    if arquivos:
        st.markdown("**📷 Fotos da maleta**")
        _grade_fotos(arquivos, editavel=False)

# ---------------------------------------------------------------------------
# PDF da maleta (conferência de saída e maleta com o parceiro)
# ---------------------------------------------------------------------------

_CACHE_FOTOS_PDF: dict = {}   # url -> (JPEG, largura, altura). Só guarda sucessos.

MOTIVO_SEM_CADASTRO = "sem foto cadastrada"

def _foto_para_pdf(url_foto):
    """Baixa a foto do produto e a reduz para o PDF. Devolve (bytes, larg, alt) ou None
    (sem foto, link quebrado, tempo esgotado...). Fica em memória: o 2º PDF não rebaixa."""
    if not url_foto:
        return None
    url = _url_miniatura(str(url_foto))
    if not url:
        return None
    if url in _CACHE_FOTOS_PDF:
        return _CACHE_FOTOS_PDF[url]
    try:
        from io import BytesIO
        import requests
        from PIL import Image, ImageOps

        resposta = requests.get(url, timeout=(3, 10))
        resposta.raise_for_status()
        if len(resposta.content) > 10_000_000:
            return None
        imagem = ImageOps.exif_transpose(Image.open(BytesIO(resposta.content)))
        if imagem.mode in ("RGBA", "LA", "P"):
            rgba = imagem.convert("RGBA")
            fundo = Image.new("RGB", rgba.size, (255, 255, 255))
            fundo.paste(rgba, mask=rgba.split()[3])
            imagem = fundo
        else:
            imagem = imagem.convert("RGB")
        imagem.thumbnail((PDF_FOTO_PX, PDF_FOTO_PX))
        saida = BytesIO()
        imagem.save(saida, format="JPEG", quality=80)
        resultado = (saida.getvalue(), imagem.size[0], imagem.size[1])
    except Exception:
        return None
    if len(_CACHE_FOTOS_PDF) > 2000:
        _CACHE_FOTOS_PDF.clear()
    _CACHE_FOTOS_PDF[url] = resultado
    return resultado

def _texto_pdf(valor, markup: bool = True) -> str:
    """Texto seguro para as fontes padrão do PDF (cobrem o português) e, com markup=True,
    para o XML dos Paragraph (escapa & < >)."""
    texto = str(valor if valor is not None else "").encode("cp1252", "replace").decode("cp1252")
    return html.escape(texto, quote=False) if markup else texto

def _gerar_pdf_maleta(titulo: str, subtitulo: str, resumo: str, linhas: list, com_conferencia: bool = False):
    """PDF A4 com Foto | Código | Produto | Qtd (e uma caixinha para marcar, na conferência).

    linhas: [{'codigo', 'descricao', 'qtd', 'foto_url'}]. As fotos são baixadas em paralelo.
    Retorna (pdf_em_bytes, sem_foto), onde sem_foto lista os produtos que saíram sem foto
    ({'codigo', 'descricao', 'motivo'}). Levanta ImportError se o reportlab não estiver instalado."""
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Flowable, Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    urls = sorted({l["foto_url"] for l in linhas if l.get("foto_url")})
    with ThreadPoolExecutor(max_workers=8) as pool:
        fotos = dict(zip(urls, pool.map(_foto_para_pdf, urls)))

    base = getSampleStyleSheet()["Normal"]
    cinza = colors.HexColor("#555555")
    est_titulo = ParagraphStyle("titulo", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=21)
    est_sub = ParagraphStyle("sub", parent=base, fontSize=9.5, leading=12.5, textColor=cinza)
    est_cel = ParagraphStyle("cel", parent=base, fontSize=9.5, leading=12)
    est_cab = ParagraphStyle("cab", parent=est_cel, fontName="Helvetica-Bold", textColor=colors.white)
    est_cab_c = ParagraphStyle("cabc", parent=est_cab, alignment=1)
    est_cod = ParagraphStyle("cod", parent=est_cel, fontName="Helvetica-Bold", fontSize=10.5)
    est_qtd = ParagraphStyle("qtd", parent=est_cel, fontName="Helvetica-Bold", fontSize=13, leading=15, alignment=1)
    est_sem = ParagraphStyle("sem", parent=est_cel, fontSize=7, textColor=colors.HexColor("#888888"), alignment=1)

    class Caixa(Flowable):
        """Quadradinho vazio para marcar o item conferido."""
        def __init__(self, lado=5.5 * mm):
            super().__init__()
            self.lado, self.width, self.height, self.hAlign = lado, lado, lado, "CENTER"

        def draw(self):
            self.canv.setLineWidth(0.9)
            self.canv.rect(0, 0, self.lado, self.lado)

    caixa_mm = PDF_FOTO_MM * mm
    cabecalho = [Paragraph("Foto", est_cab), Paragraph("Código", est_cab), Paragraph("Produto", est_cab),
                 Paragraph("Qtd", est_cab_c)]
    larguras = [24, 28, 110, 18]
    if com_conferencia:
        cabecalho.append(Paragraph("Conf.", est_cab_c))
        larguras = [24, 28, 94, 18, 16]

    dados, total = [cabecalho], 0.0
    for linha in linhas:
        foto = fotos.get(linha.get("foto_url"))
        if foto:
            conteudo, larg, alt = foto
            escala = min(caixa_mm / larg, caixa_mm / alt)
            celula_foto = RLImage(BytesIO(conteudo), width=larg * escala, height=alt * escala)
        else:
            celula_foto = Paragraph("sem foto", est_sem)
        qtd = float(linha.get("qtd") or 0)
        total += qtd
        registro = [
            celula_foto,
            Paragraph(_texto_pdf(linha.get("codigo") or "-"), est_cod),
            Paragraph(_texto_pdf(linha.get("descricao") or "-"), est_cel),
            Paragraph(f"{qtd:g}", est_qtd),
        ]
        if com_conferencia:
            registro.append(Caixa())
        dados.append(registro)

    rodape_total = ["", "", Paragraph("<b>Total de peças</b>", ParagraphStyle("tt", parent=est_cel, alignment=2)),
                    Paragraph(f"{total:g}", est_qtd)]
    if com_conferencia:
        rodape_total.append("")
    dados.append(rodape_total)

    ultima = len(dados) - 1
    # Altura fixa nas linhas de item (com ou sem foto): tabela uniforme. None = automática.
    alturas = [None] + [caixa_mm + 8] * (ultima - 1) + [None]
    tabela = Table(dados, colWidths=[l * mm for l in larguras], rowHeights=alturas, repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F3E4E")),
        ("ROWBACKGROUNDS", (0, 1), (-1, ultima - 1), [colors.white, colors.HexColor("#F4F5F7")]),
        ("GRID", (0, 0), (-1, ultima - 1), 0.4, colors.HexColor("#C9CDD2")),
        ("LINEABOVE", (0, ultima), (-1, ultima), 1.2, colors.HexColor("#2F3E4E")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, ultima - 1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ] + (
        # o total nunca fica sozinho numa página: acompanha a última linha de item
        [("NOSPLIT", (0, ultima - 1), (-1, ultima))] if ultima >= 2 else []
    )))

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(cinza)
        canvas.drawString(15 * mm, 9 * mm, _texto_pdf(titulo, markup=False))
        canvas.drawRightString(195 * mm, 9 * mm, f"Página {doc.page}")
        canvas.restoreState()

    sem_foto = []
    for linha in linhas:
        url = linha.get("foto_url")
        if not url:
            motivo = MOTIVO_SEM_CADASTRO
        elif not fotos.get(url):
            motivo = "foto cadastrada, mas não foi possível carregá-la"
        else:
            continue
        sem_foto.append({"codigo": linha.get("codigo"), "descricao": linha.get("descricao"), "motivo": motivo})

    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=18 * mm, title=_texto_pdf(titulo, markup=False),
    )
    documento.build(
        [Paragraph(_texto_pdf(titulo), est_titulo), Paragraph(_texto_pdf(subtitulo), est_sub),
         Paragraph(_texto_pdf(resumo), est_sub), Spacer(1, 6 * mm), tabela],
        onFirstPage=rodape, onLaterPages=rodape,
    )
    return buffer.getvalue(), sem_foto

def _assinatura_itens(itens: list) -> str:
    """Muda quando o conteúdo da maleta muda: o PDF guardado deixa de valer."""
    base = "|".join(f"{i['id']}:{i['produto_id']}:{i.get('quantidade')}" for i in itens)
    return hashlib.md5(base.encode("utf-8")).hexdigest()

def _descricao_pdf(produto: dict, usar_site: bool) -> str:
    """Descrição que vai ao PDF: a do site (se pedida e preenchida) ou a padrão."""
    if usar_site:
        texto = (produto.get("produto_descricao_site") or "").strip()
        if texto:
            return texto
    return produto.get("descricao") or "-"

def _resumir_lista(rotulos: list, limite: int = 8) -> str:
    if len(rotulos) <= limite:
        return ", ".join(rotulos)
    return ", ".join(rotulos[:limite]) + f" e mais {len(rotulos) - limite}"

def _rotulo_produto(produto: dict) -> str:
    nome = (produto.get("descricao") or "").strip()
    nome = nome if len(nome) <= 28 else nome[:27] + "…"
    return f"{produto.get('codigo_interno') or '-'} ({nome})" if nome else str(produto.get("codigo_interno") or "-")

def _conteudo_pdf_maleta(parceiro_nome: str, envio: dict, itens: list, variante: str) -> None:
    """Conteúdo do popup: avisos, escolha da descrição, geração e download do PDF.
    O PDF é gerado ao abrir (e de novo se a opção de descrição mudar) e fica guardado
    enquanto a maleta não mudar."""
    envio_id = envio["id"]
    base = f"parceiros_pdfdlg_{envio_id}_{variante}"
    produtos = [i.get("produtos") or {} for i in itens]
    pecas = sum(float(i.get("quantidade") or 0) for i in itens)
    st.markdown(
        f"**Envio #{envio['numero_envio']}** · {parceiro_nome or '-'} · "
        f"{len(itens)} produto(s) · {_fmt_qtd(pecas)} peça(s)"
    )

    # --- descrição: padrão ou do site ---
    interruptor = getattr(st, "toggle", st.checkbox)
    usar_site = bool(interruptor(
        "Usar a descrição do site no PDF",
        value=bool(st.session_state.get(CHAVE_USAR_SITE, False)),
        key=f"{base}_site",
        help="Ligado: usa a descrição do site (produto_descricao_site); produtos sem esse texto "
             "saem com a descrição padrão. Desligado: usa a descrição padrão (descricao).",
    ))
    if usar_site:
        sem_site = [_rotulo_produto(p) for p in produtos if not (p.get("produto_descricao_site") or "").strip()]
        if sem_site:
            st.info(
                f"{len(sem_site)} produto(s) sem descrição do site; no PDF eles usam a descrição "
                f"padrão: {_resumir_lista(sem_site)}."
            )

    # --- aviso de produtos sem foto (antes de gerar) ---
    sem_cadastro = [_rotulo_produto(p) for p in produtos if not p.get("foto_url")]
    if sem_cadastro:
        st.warning(
            f"⚠️ {len(sem_cadastro)} produto(s) sem foto cadastrada: "
            f"{_resumir_lista(sem_cadastro)}. No PDF eles saem com “sem foto”."
        )

    # --- gera (ou reaproveita) o PDF ---
    assinatura = _assinatura_itens(itens) + (":site" if usar_site else ":padrao")
    chave = f"{base}_pdf"
    pronto = st.session_state.get(chave)
    if pronto is None or pronto.get("assinatura") != assinatura:
        agora = datetime.now()
        if variante == "conferencia":
            situacao = "Conferência de saída"
        elif variante == "ajuste":
            situacao = "Maleta com o parceiro após os ajustes (prévia, ainda não salva)"
        else:
            situacao = f"Maleta com o parceiro desde {_fmt_data(envio.get('data_envio'))}"
        try:
            with st.spinner("Gerando o PDF (carregando as fotos)..."):
                dados, sem_foto = _gerar_pdf_maleta(
                    titulo=f"Maleta - Envio #{envio['numero_envio']}",
                    subtitulo=f"Parceiro: {parceiro_nome or '-'}   |   {situacao}   |   "
                              f"Emitido em {agora:%d/%m/%Y %H:%M}",
                    resumo=f"{len(itens)} produto(s) · {_fmt_qtd(pecas)} peça(s)",
                    linhas=[
                        {
                            "codigo": p.get("codigo_interno"),
                            "descricao": _descricao_pdf(p, usar_site),
                            "qtd": i.get("quantidade"),
                            "foto_url": p.get("foto_url"),
                        }
                        for i, p in zip(itens, produtos)
                    ],
                    com_conferencia=(variante == "conferencia"),
                )
        except ImportError:
            st.error("Para gerar o PDF, instale a biblioteca: pip install reportlab")
            return
        except Exception as e:
            st.error(f"Não foi possível gerar o PDF: {e}")
            return
        nome_slug = re.sub(r"[^a-z0-9]+", "_", _norm(parceiro_nome)).strip("_") or "parceiro"
        pronto = {
            "assinatura": assinatura,
            "bytes": dados,
            "sem_foto": sem_foto,
            "nome": (
                f"{ {'conferencia': 'conferencia_saida', 'ajuste': 'maleta_ajustada'}.get(variante, 'maleta') }"
                f"_envio_{envio['numero_envio']}_{nome_slug}_{agora:%Y%m%d}.pdf"
            ),
            "hora": agora.strftime("%H:%M"),
        }
        st.session_state[chave] = pronto

    # --- resultado: fotos que, apesar de cadastradas, não puderam ser carregadas ---
    falhas = [x for x in pronto["sem_foto"] if x["motivo"] != MOTIVO_SEM_CADASTRO]
    if falhas:
        st.warning(
            f"⚠️ {len(falhas)} produto(s) têm foto cadastrada, mas ela não pôde ser carregada "
            f"para o PDF: {_resumir_lista([str(x['codigo']) for x in falhas])}. Eles saem com “sem foto”."
        )
    elif not sem_cadastro:
        st.success("Todas as fotos foram incluídas no PDF.")

    argumentos = dict(
        label="⬇️ Baixar PDF", data=pronto["bytes"], file_name=pronto["nome"],
        mime="application/pdf", key=f"{chave}_baixar", type="primary", use_container_width=True,
    )
    try:
        st.download_button(on_click="ignore", **argumentos)  # sem rerun ao baixar
    except Exception:  # Streamlit mais antigo: não aceita on_click="ignore"
        st.download_button(**argumentos)
    col_info, col_refazer = st.columns([3, 1], vertical_alignment="center")
    col_info.caption(f"PDF gerado às {pronto['hora']} · {pronto['nome']}")
    col_refazer.button(
        "Gerar de novo", key=f"{chave}_refazer",
        on_click=lambda: st.session_state.pop(chave, None),
    )

def _criar_decorador_dialogo(titulo: str):
    """st.dialog (Streamlit >= 1.37) ou st.experimental_dialog; None se não houver popup."""
    for nome in ("dialog", "experimental_dialog"):
        decorador = getattr(st, nome, None)
        if decorador is not None:
            try:
                return decorador(titulo, width="large")
            except TypeError:
                return decorador(titulo)
    return None

_decorador_dialogo = _criar_decorador_dialogo("📄 PDF da maleta")

_dialogo_pdf = _decorador_dialogo(_conteudo_pdf_maleta) if _decorador_dialogo else None

def _botao_pdf_maleta(parceiro_nome: str, envio: dict, itens: list, variante: str) -> None:
    """Botão que abre o popup do PDF. Chamar FORA de blocos @st.fragment (o popup não abre
    de dentro de outro bloco independente). variante: 'conferencia' ou 'maleta'."""
    if not itens:
        return
    chave = f"parceiros_pdf_{envio['id']}_{variante}"
    if _dialogo_pdf is not None:
        if st.button(
            "📄 PDF da maleta", key=f"{chave}_abrir",
            help="Abre uma janela para escolher a descrição, ver avisos e baixar o PDF.",
        ):
            _dialogo_pdf(parceiro_nome, envio, itens, variante)
        return
    # Streamlit sem popup: o mesmo conteúdo em um quadro na própria página.
    aberto = f"{chave}_inline"
    if st.session_state.get(aberto):
        with st.container(border=True):
            _conteudo_pdf_maleta(parceiro_nome, envio, itens, variante)
            st.button("Fechar", key=f"{chave}_fechar", on_click=lambda: st.session_state.pop(aberto, None))
    else:
        st.button(
            "📄 PDF da maleta", key=f"{chave}_abrir",
            on_click=lambda: st.session_state.__setitem__(aberto, True),
        )

def _iniciar_confirmacao(chave: str) -> None:
    st.session_state[chave] = True

def _cancelar_confirmacao(chave: str) -> None:
    st.session_state.pop(chave, None)

def _cb_marcar_todos(chave_todos: str, chaves_itens: list) -> None:
    valor = bool(st.session_state.get(chave_todos))
    for chave in chaves_itens:
        st.session_state[chave] = valor

@_fragmento
def _render_conferencia_saida(
    envio: dict, itens: list, total_pecas: float, total_valor: float, chave_conf: str,
    parceiro_nome: str = "",
) -> None:
    envio_id = envio["id"]
    st.markdown("##### 📋 Conferência de saída")
    st.caption(
        "Confira, peça a peça, o que está indo na maleta. O envio só pode ser "
        "confirmado quando todos os itens estiverem marcados."
    )

    usar_site = _toggle_descricao_site(f"parceiros_tg_conf_{envio_id}")
    chaves = [f"parceiros_conf_saida_{envio_id}_{it['id']}" for it in itens]
    chave_todos = f"parceiros_conf_saida_todos_{envio_id}"
    st.checkbox(
        "Marcar todos como conferidos",
        key=chave_todos,
        on_change=_cb_marcar_todos,
        args=(chave_todos, chaves),
    )

    cols = [1.0, 1.0, 1.4, 3.4, 1.0, 1.4]
    _legenda_fotos([(it.get("produtos") or {}).get("foto_url") for it in itens])
    with st.container(border=True):
        for col, titulo in zip(
            st.columns(cols, vertical_alignment="center"),
            ["Conferido", "Foto", "Código", "Produto", "Qtd", "Subtotal"],
        ):
            col.markdown(f"**{titulo}**")
        for it, chave in zip(itens, chaves):
            prod = it.get("produtos") or {}
            c_ok, c_foto, c_cod, c_prod, c_qtd, c_sub = st.columns(cols, vertical_alignment="center")
            c_ok.checkbox("Conferido", key=chave, label_visibility="collapsed")
            with c_foto:
                _mostrar_foto(prod.get("foto_url"))
            c_cod.write(prod.get("codigo_interno") or "-")
            c_prod.write(_descricao_pdf(prod, usar_site))
            c_qtd.write(_fmt_qtd(it.get("quantidade")))
            c_sub.write(_fmt_moeda(float(it.get("quantidade") or 0) * float(it.get("valor_unitario") or 0)))

    n_ok = sum(1 for chave in chaves if st.session_state.get(chave))
    st.caption(
        f"{n_ok} de {len(itens)} item(ns) conferido(s) · {_fmt_qtd(total_pecas)} peça(s) · "
        f"{_fmt_moeda(total_valor)}"
    )

    n_fotos = _secao_fotos_maleta(envio_id, "saida")

    col_data, col_obs = st.columns([1, 3])
    with col_data:
        data_saida = st.date_input(
            "Data de saída da maleta",
            value=_parse_data_segura(envio.get("data_envio")) or date.today(),
            key=f"parceiros_conf_saida_data_{envio_id}",
            format="DD/MM/YYYY",
            help="As vendas do canal do parceiro passam a contar a partir desta data.",
        )
    with col_obs:
        obs = st.text_input(
            "Observações da conferência (opcional)",
            key=f"parceiros_conf_saida_obs_{envio_id}",
        )

    col_ok, col_voltar, _ = st.columns([1.6, 1, 3])
    with col_ok:
        confirmar = st.button(
            "✅ Confirmar envio da maleta",
            type="primary",
            key="parceiros_conf_saida_ok",
            disabled=n_ok < len(itens) or (EXIGIR_FOTO_SAIDA and n_fotos == 0),
        )
    if EXIGIR_FOTO_SAIDA and n_fotos == 0:
        st.caption("Anexe pelo menos uma foto da maleta para confirmar o envio.")
    with col_voltar:
        if st.button("Voltar", key="parceiros_conf_saida_voltar"):
            _cancelar_confirmacao(chave_conf)
            st.rerun()  # fragmento: precisa de rerun completo para sair da conferência

    if confirmar:
        try:
            supabase.table("parcerias_envio_itens").update(
                {"conferido_saida": True}
            ).in_("id", [it["id"] for it in itens]).execute()
            supabase.table("parcerias_envios").update({
                "status": "Enviado",
                "data_envio": data_saida.isoformat(),
                "enviado_em": datetime.now().isoformat(),
                "obs_conferencia_saida": obs.strip() or None,
                "total_itens": len(itens),
                "total_pecas": total_pecas,
                "valor_total": total_valor,
            }).eq("id", envio_id).eq("status", "Aberto").execute()
            with st.spinner("Gerando e guardando os PDFs da maleta..."):
                _pdf_id, aviso = _guardar_pdfs_evento(
                    parceiro_nome, {**envio, "data_envio": data_saida.isoformat()}, itens, "criacao",
                )
            st.session_state.pop(chave_conf, None)
            _resetar_editores_envio()
            msg = f"Envio #{envio['numero_envio']} conferido e enviado. A maleta está com o parceiro."
            _flash("warning", f"{msg} Atenção: {aviso}") if aviso else _flash("success", msg)
            st.rerun()
        except Exception as e:
            _invalidar_cache_envios()
            st.error(f"Não foi possível confirmar o envio: {e}")

def _render_envio_aberto(parceiro: dict, envio: dict, itens: list) -> None:
    envio_id = envio["id"]
    chave_conf = f"parceiros_envio_conferindo_{envio_id}"

    total_pecas = sum(float(i.get("quantidade") or 0) for i in itens)
    total_valor = sum(
        float(i.get("quantidade") or 0) * float(i.get("valor_unitario") or 0) for i in itens
    )

    # Etapa 2 (conferência de saída) tem tela própria: as tabelas da montagem (itens e lista
    # de adicionar produtos) saem de cena e só voltam ao clicar em "Voltar".
    if st.session_state.get(chave_conf) and itens:
        st.markdown(f"##### Envio #{envio['numero_envio']} — conferência de saída")
        _botao_pdf_maleta(parceiro.get("nome") or "", envio, itens, "conferencia")
        _render_conferencia_saida(
            envio, itens, total_pecas, total_valor, chave_conf, parceiro.get("nome") or "",
        )
        return

    st.markdown(
        f"##### Envio #{envio['numero_envio']} — montagem da maleta "
        f"({_fmt_data(envio.get('data_envio'))})"
    )
    if envio.get("observacao"):
        st.caption(envio["observacao"])

    try:
        disp = _carregar_produtos_disponiveis()
    except Exception as e:
        st.error(f"Não foi possível carregar os produtos em estoque: {e}")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Produtos no envio", len(itens))
    c2.metric("Peças", _fmt_qtd(total_pecas))
    c3.metric("Valor (preço sugerido)", _fmt_moeda(total_valor))

    pendentes = _secao_itens_envio(envio, itens, disp, modo="Aberto")
    _secao_adicionar_produtos(envio, itens, disp, modo="Aberto")

    st.divider()
    if pendentes:
        st.warning("Há alterações não salvas na lista acima. Salve-as antes de conferir e enviar.")
    st.button(
        "📦 Conferir e enviar a maleta",
        type="primary",
        disabled=not itens or pendentes,
        key="parceiros_envio_conferir",
        on_click=_iniciar_confirmacao,
        args=(chave_conf,),
    )

# ---------------------------------------------------------------------------
# Etapa 2: maleta com o parceiro (ajustes) e Etapa 3: fechamento do ciclo
# ---------------------------------------------------------------------------

def _fmt_data_hora(valor, converter_utc: bool = True) -> str:
    """dd/mm/aaaa hh:mm. Horários gravados pelo banco (now()) vêm em UTC e são levados para
    o horário de Brasília; `enviado_em` foi gravado pelo app com a hora local, sem conversão."""
    texto = str(valor or "").strip()
    if not texto:
        return "-"
    if len(texto) <= 10:
        return _fmt_data(valor)
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return _fmt_data(valor)
    if converter_utc and dt.tzinfo is not None:
        dt = dt.astimezone(timezone(timedelta(hours=-3)))
    return dt.strftime("%d/%m/%Y %H:%M")

def _guardar_pdfs_evento(parceiro_nome: str, envio: dict, itens: list, evento: str, motivo=None):
    """Gera os dois PDFs da maleta (descrição padrão e descrição do site), guarda no bucket
    público `pdf_maletas` e registra em parcerias_envio_pdfs.

    evento: 'criacao' (saída da maleta) ou 'ajuste' (adição/remoção). Nunca levanta exceção:
    a gravação da maleta não pode falhar por causa do PDF. Retorna (pdf_id, aviso): pdf_id é
    None se nada foi guardado e `aviso` explica o motivo."""
    if not _tem_pdfs_maleta():
        return None, (
            "os PDFs não foram guardados (execute migracao_pdfs_maleta.sql no Supabase para habilitar)."
        )
    bucket = supabase.storage.from_(BUCKET_PDFS)
    caminhos: dict = {}
    try:
        agora = datetime.now()
        stamp = agora.strftime("%Y%m%d_%H%M%S")
        produtos = [i.get("produtos") or {} for i in itens]
        pecas = sum(float(i.get("quantidade") or 0) for i in itens)
        rotulo = (
            f"Criação da maleta (saída em {_fmt_data(envio.get('data_envio'))})"
            if evento == "criacao" else "Ajuste da maleta (adição/remoção de itens)"
        )
        variantes = [("padrao", False), ("site", True)]
        for nome_var, usar_site in variantes:
            dados, _sem_foto = _gerar_pdf_maleta(
                titulo=f"Maleta - Envio #{envio['numero_envio']}",
                subtitulo=(
                    f"Parceiro: {parceiro_nome or '-'}   |   {rotulo}   |   "
                    f"Descrição: {'do site' if usar_site else 'padrão'}   |   "
                    f"Emitido em {agora:%d/%m/%Y %H:%M}"
                ),
                resumo=f"{len(itens)} produto(s) · {_fmt_qtd(pecas)} peça(s)",
                linhas=[
                    {
                        "codigo": p.get("codigo_interno"),
                        "descricao": _descricao_pdf(p, usar_site),
                        "qtd": i.get("quantidade"),
                        "foto_url": p.get("foto_url"),
                    }
                    for i, p in zip(itens, produtos)
                ],
            )
            caminho = f"envio_{envio['id']}/{stamp}_{evento}_{nome_var}.pdf"
            bucket.upload(caminho, dados, {"content-type": "application/pdf"})
            caminhos[nome_var] = caminho
        resposta = supabase.table("parcerias_envio_pdfs").insert({
            "envio_id": envio["id"],
            "evento": evento,
            "motivo": (motivo or "").strip() or None,
            "total_itens": len(itens),
            "total_pecas": pecas,
            "caminho_padrao": caminhos.get("padrao"),
            "caminho_site": caminhos.get("site"),
        }).execute()
        _buscar_pdfs_maleta.clear()
        return resposta.data[0]["id"], None
    except ImportError:
        aviso = "os PDFs não foram gerados: instale a biblioteca com pip install reportlab."
    except Exception as e:
        aviso = f"não foi possível guardar os PDFs da maleta ({e})."
    if caminhos:  # não deixa arquivo órfão no Storage se o registro falhou
        try:
            bucket.remove(list(caminhos.values()))
        except Exception:
            pass
    return None, aviso

def _url_pdf_maleta(caminho: str) -> str:
    """URL pública do PDF com ?download=: o navegador baixa o arquivo em vez de só abri-lo."""
    try:
        url = str(supabase.storage.from_(BUCKET_PDFS).get_public_url(caminho)).rstrip("?")
    except Exception:
        return ""
    nome = caminho.rsplit("/", 1)[-1]
    return f"{url}{'&' if '?' in url else '?'}{urlencode({'download': nome})}"

def _eventos_do_ciclo(envio: dict, movimentos: list, pdfs: list) -> list:
    """Linha do tempo da maleta: a criação (saída) e cada ajuste (adições/remoções salvas de
    uma vez). Os movimentos de um mesmo ajuste são agrupados pelo PDF (pdf_id); movimentos
    antigos, sem PDF, são agrupados pelo horário de gravação."""
    eventos = []
    pdf_criacao = next((p for p in pdfs if p.get("evento") == "criacao"), None)
    if envio.get("enviado_em") or envio.get("status") in ("Enviado", "Fechado"):
        if pdf_criacao and pdf_criacao.get("total_itens") is not None:
            resumo = f"{pdf_criacao['total_itens']} produto(s) · {_fmt_qtd(pdf_criacao.get('total_pecas'))} peça(s)"
        elif envio.get("status") == "Enviado" and envio.get("total_itens") is not None:
            # enquanto a maleta está com o parceiro, os totais do envio ainda são os da saída
            resumo = f"{envio['total_itens']} produto(s) · {_fmt_qtd(envio.get('total_pecas'))} peça(s)"
        else:
            resumo = ""
        eventos.append({
            "titulo": "🆕 Criação da maleta",
            "quando": _fmt_data_hora(envio.get("enviado_em") or envio.get("data_envio"), converter_utc=False),
            "resumo": resumo,
            "pdf": pdf_criacao,
            "movs": [],
        })

    pdfs_por_id = {p["id"]: p for p in pdfs}
    grupos: dict = {}
    for m in movimentos:
        if m.get("pdf_id") in pdfs_por_id:
            chave = ("pdf", m["pdf_id"])
        else:
            chave = ("ts", str(m.get("criado_em"))[:19])
        grupos.setdefault(chave, []).append(m)
    for p in pdfs:
        if p.get("evento") == "ajuste":
            grupos.setdefault(("pdf", p["id"]), [])

    ajustes = []
    for chave, movs in grupos.items():
        pdf = pdfs_por_id.get(chave[1]) if chave[0] == "pdf" else None
        instante = (pdf or {}).get("criado_em") or (movs[0].get("criado_em") if movs else None)
        motivo = (pdf or {}).get("motivo") or next((m.get("motivo") for m in movs if m.get("motivo")), None)
        partes = []
        if motivo:
            partes.append(f"Motivo: {motivo}")
        if pdf and pdf.get("total_itens") is not None:
            partes.append(f"a maleta ficou com {pdf['total_itens']} produto(s) · {_fmt_qtd(pdf.get('total_pecas'))} peça(s)")
        ajustes.append({
            "ordem": str(instante or "")[:19],
            "quando": _fmt_data_hora(instante),
            "resumo": " · ".join(partes),
            "pdf": pdf,
            "movs": movs,
        })
    ajustes.sort(key=lambda a: a["ordem"])
    for n, a in enumerate(ajustes, start=1):
        a["titulo"] = f"✏️ Ajuste #{n}"
    return eventos + ajustes

def _botao_baixar_pdf(coluna, rotulo: str, caminho) -> None:
    url = _url_pdf_maleta(caminho) if caminho else ""
    if not url:
        return
    botao = getattr(coluna, "link_button", None)
    if botao is not None:
        botao(rotulo, url, use_container_width=True)
    else:
        coluna.markdown(f"[{rotulo}]({url})")

def _render_movimentos_ciclo(envio: dict, movimentos: list, pdfs: list, ocultar_vazio: bool = False) -> None:
    """'Movimentações durante o ciclo': criação da maleta e cada ajuste, com os botões para
    baixar os PDFs guardados (descrição padrão e descrição do site)."""
    eventos = _eventos_do_ciclo(envio, movimentos, pdfs)
    if not eventos and ocultar_vazio:
        return
    st.markdown("**Movimentações durante o ciclo**")
    if not eventos:
        st.caption("Nenhuma movimentação registrada.")
        return
    if not _tem_pdfs_maleta():
        st.caption(
            "Para guardar e baixar o PDF de cada movimentação, execute migracao_pdfs_maleta.sql no Supabase."
        )
    elif len(eventos) == 1 and not eventos[0]["movs"]:
        st.caption("Nenhuma adição ou remoção desde a saída da maleta.")

    for ev in eventos:
        with st.container(border=True):
            c_info, c_padrao, c_site = st.columns([5, 1.5, 1.7], vertical_alignment="center")
            c_info.markdown(f"**{ev['titulo']}** · {ev['quando']}")
            if ev["resumo"]:
                c_info.caption(ev["resumo"].replace("$", "\\$"))
            pdf = ev["pdf"]
            if pdf and (pdf.get("caminho_padrao") or pdf.get("caminho_site")):
                _botao_baixar_pdf(c_padrao, "⬇️ PDF padrão", pdf.get("caminho_padrao"))
                _botao_baixar_pdf(c_site, "⬇️ PDF do site", pdf.get("caminho_site"))
            else:
                c_padrao.caption("sem PDF")
            if ev["movs"]:
                st.markdown(
                    _html_tabela(
                        ["Tipo", "Código", "Produto", "Qtd"],
                        [
                            [
                                TIPOS_MOVIMENTO.get(m.get("tipo"), m.get("tipo") or "-"),
                                (m.get("produtos") or {}).get("codigo_interno") or "-",
                                (m.get("produtos") or {}).get("descricao") or "-",
                                _fmt_qtd(m.get("quantidade")),
                            ]
                            for m in ev["movs"]
                        ],
                        colunas_num={3},
                    ),
                    unsafe_allow_html=True,
                )


def _cb_vendido_alterado(k_v: str, k_r: str, k_auto: str, enviado: float) -> None:
    """Enquanto o 'recebido' não foi mexido à mão, ele acompanha enviado - vendido."""
    if st.session_state.get(k_auto, True):
        st.session_state[k_r] = max(enviado - float(st.session_state.get(k_v) or 0), 0.0)

def _cb_recebido_alterado(k_auto: str) -> None:
    st.session_state[k_auto] = False

@_fragmento
def _render_fechamento_envio(parceiro: dict, envio: dict, itens: list) -> None:
    envio_id = envio["id"]
    reset = st.session_state.get("parceiros_envio_reset", 0)
    try:
        vendidos = _vendido_por_produto(parceiro.get("canal_id"), envio.get("data_envio"))
    except Exception as e:
        st.error(f"Não foi possível carregar as vendas do sistema: {e}")
        vendidos = {}

    st.markdown("##### 📥 Receber a maleta e fechar o ciclo")
    st.caption(
        "Confira o que o parceiro informou como vendido e o que chegou fisicamente. "
        f"'Sistema' mostra as vendas do canal desde {_fmt_data(envio.get('data_envio'))}; "
        "o vendido confirmado já vem preenchido com esse valor, ajuste se a mensagem do "
        "parceiro for diferente. O recebido começa como enviado − vendido."
    )
    if not itens:
        st.info("Este envio não tem produtos.")
        return

    st.button(
        "🔄 Atualizar vendas do sistema",
        key="parceiros_fech_atualizar",
        on_click=_atualizar_vendas_fechamento,
        help="Recarrega as vendas do canal e refaz os campos abaixo com os valores atuais.",
    )
    _legenda_fotos([(it.get("produtos") or {}).get("foto_url") for it in itens])
    cols = [1.0, 1.3, 2.7, 0.9, 1.0, 1.2, 1.2, 1.7]
    linhas = []
    with st.container(border=True):
        for col, titulo in zip(
            st.columns(cols, vertical_alignment="center"),
            ["Foto", "Código", "Produto", "Enviado", "Sistema", "Vendido", "Recebido", "Situação"],
        ):
            col.markdown(f"**{titulo}**")

        for it in itens:
            prod = it.get("produtos") or {}
            enviado = float(it.get("quantidade") or 0)
            sistema = float(vendidos.get(it["produto_id"], 0.0))
            k_v = f"parceiros_fech_v_{envio_id}_{reset}_{it['id']}"
            k_r = f"parceiros_fech_r_{envio_id}_{reset}_{it['id']}"
            k_a = f"parceiros_fech_a_{envio_id}_{reset}_{it['id']}"
            if k_v not in st.session_state:
                st.session_state[k_v] = min(sistema, enviado)
            if k_r not in st.session_state:
                st.session_state[k_r] = max(enviado - min(sistema, enviado), 0.0)
            if k_a not in st.session_state:
                st.session_state[k_a] = True

            c_foto, c_cod, c_prod, c_env, c_sis, c_v, c_r, c_sit = st.columns(cols, vertical_alignment="center")
            with c_foto:
                _mostrar_foto(prod.get("foto_url"))
            c_cod.write(prod.get("codigo_interno") or "-")
            c_prod.write(prod.get("descricao") or "-")
            c_env.write(_fmt_qtd(enviado))
            c_sis.write(_fmt_qtd(sistema))
            vendido = c_v.number_input(
                "Vendido", min_value=0.0, max_value=enviado, step=1.0, format="%g",
                key=k_v, label_visibility="collapsed",
                on_change=_cb_vendido_alterado, args=(k_v, k_r, k_a, enviado),
            )
            recebido = c_r.number_input(
                "Recebido", min_value=0.0, max_value=enviado, step=1.0, format="%g",
                key=k_r, label_visibility="collapsed",
                on_change=_cb_recebido_alterado, args=(k_a,),
            )
            diferenca = round(enviado - vendido - recebido, 6)
            if diferenca == 0:
                c_sit.write("✅ ok")
            elif diferenca > 0:
                c_sit.write(f"⚠️ falta {_fmt_qtd(diferenca)}")
            else:
                c_sit.write(f"⚠️ sobra {_fmt_qtd(-diferenca)}")
            linhas.append({
                "item": it, "enviado": enviado, "sistema": sistema,
                "vendido": float(vendido), "recebido": float(recebido), "diferenca": diferenca,
            })

    total_env = sum(l["enviado"] for l in linhas)
    total_ven = sum(l["vendido"] for l in linhas)
    total_rec = sum(l["recebido"] for l in linhas)
    divergentes = [l for l in linhas if l["diferenca"] != 0]
    fora_sistema = [l for l in linhas if not _iguais(l["vendido"], min(l["sistema"], l["enviado"]))]
    total_div = sum(abs(l["diferenca"]) for l in divergentes)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Enviado", _fmt_qtd(total_env))
    m2.metric("Vendido", _fmt_qtd(total_ven))
    m3.metric("Recebido", _fmt_qtd(total_rec))
    m4.metric("Divergência", _fmt_qtd(total_div))

    if divergentes:
        st.warning(
            f"{len(divergentes)} produto(s) com divergência (enviado ≠ vendido + recebido). "
            "Falta = peça que não voltou nem foi vendida. Sobra = voltou/vendeu mais do que foi enviado."
        )
    if fora_sistema:
        st.info(
            f"{len(fora_sistema)} produto(s) com vendido diferente das vendas registradas no sistema. "
            "Se o parceiro vendeu mais, registre a venda na tela de vendas para o estoque e o "
            "fechamento de comissão ficarem corretos."
        )

    precisa_obs = bool(divergentes or fora_sistema)
    obs = st.text_area(
        "Observação do fechamento" + (" (obrigatória: há divergências)" if precisa_obs else " (opcional)"),
        key=f"parceiros_fech_obs_{envio_id}_{reset}",
        height=80,
    )
    conferi = st.checkbox(
        "Conferi fisicamente a maleta e as quantidades acima",
        key=f"parceiros_fech_conf_{envio_id}_{reset}",
    )

    if st.button(
        f"✅ Fechar ciclo do envio #{envio['numero_envio']}",
        type="primary",
        key="parceiros_fech_fechar",
        disabled=not conferi or (precisa_obs and not obs.strip()),
    ):
        try:
            for l in linhas:
                supabase.table("parcerias_envio_itens").update({
                    "qtd_vendida": l["vendido"],
                    "qtd_devolvida": l["recebido"],
                    "qtd_divergencia": l["diferenca"],
                }).eq("id", l["item"]["id"]).execute()
            valor_total = sum(
                l["enviado"] * float(l["item"].get("valor_unitario") or 0) for l in linhas
            )
            supabase.table("parcerias_envios").update({
                "status": "Fechado",
                "fechado_em": datetime.now().isoformat(),
                "obs_fechamento": obs.strip() or None,
                "total_itens": len(linhas),
                "total_pecas": total_env,
                "total_vendido": total_ven,
                "total_devolvido": total_rec,
                "total_divergencia": total_div,
                "valor_total": valor_total,
            }).eq("id", envio_id).eq("status", "Enviado").execute()
            _resetar_editores_envio()
            _flash(
                "success",
                f"Ciclo do envio #{envio['numero_envio']} fechado. "
                "Para a próxima maleta, gere um novo envio abaixo.",
            )
            st.rerun()
        except Exception as e:
            _invalidar_cache_envios()
            st.error(f"Não foi possível fechar o ciclo: {e}")

def _tabela_maleta_atual(itens: list, vendidos: dict, usar_site: bool) -> str:
    """Tabela somente leitura da maleta que está com o parceiro."""
    linhas = []
    for i in itens:
        prod = i.get("produtos") or {}
        enviado = float(i.get("quantidade") or 0)
        vendido = min(float(vendidos.get(i["produto_id"], 0.0)), enviado)
        linhas.append([
            ("foto", prod.get("foto_url")),
            prod.get("codigo_interno") or "-",
            _descricao_pdf(prod, usar_site),
            _fmt_qtd(enviado),
            _fmt_qtd(vendido),
            _fmt_qtd(enviado - vendido),
            _fmt_moeda(i.get("valor_unitario")),
        ])
    return _html_tabela(
        ["Foto", "Código", "Produto", "Enviado", "Vendido (sistema)", "Na maleta", "Valor unit."],
        linhas,
        colunas_num={3, 4, 5, 6},
    )

def _ui_remover_itens(itens: list, vendidos: dict, base: str, rem: dict) -> None:
    """Opção 1: lista da maleta com 'Remover' e quantidade. Nada é gravado aqui: as escolhas
    ficam em `rem` ({item_id: {'remover', 'qtd'}}) e vão para a prévia. Guardar em `rem`
    (e não só nos widgets) mantém a escolha ao alternar entre as opções 1 e 2."""
    if not itens:
        st.info("A maleta está vazia: não há itens para remover.")
        return
    usar_site = _toggle_descricao_site(f"{base}_tg_rem")
    st.caption(
        "Marque os produtos que saem da maleta. Para tirar só algumas peças, reduza a quantidade. "
        "Produtos com venda registrada não podem ser removidos nem ficar abaixo do vendido."
    )
    _legenda_fotos([(it.get("produtos") or {}).get("foto_url") for it in itens])
    cols = [0.9, 1.0, 1.4, 3.2, 1.3, 1.3, 1.3]
    with st.container(border=True):
        for col, titulo in zip(
            st.columns(cols, vertical_alignment="center"),
            ["Remover", "Foto", "Código", "Produto", "Qtd", "Valor unit.", "Subtotal"],
        ):
            col.markdown(f"**{titulo}**")

        for it in itens:
            prod = it.get("produtos") or {}
            atual = float(it.get("quantidade") or 0)
            valor_unit = float(it.get("valor_unitario") or 0)
            vendido = min(float(vendidos.get(it["produto_id"], 0.0)), atual)
            estado = rem.get(it["id"], {})
            k_chk, k_qtd = f"{base}_rmchk_{it['id']}", f"{base}_rmqtd_{it['id']}"
            if k_chk not in st.session_state:  # widgets fora da tela perdem o estado
                st.session_state[k_chk] = bool(estado.get("remover")) and vendido <= 0
            if k_qtd not in st.session_state:
                st.session_state[k_qtd] = float(estado.get("qtd", atual))
            st.session_state[k_qtd] = min(max(float(st.session_state[k_qtd]), vendido), atual)

            c_rm, c_foto, c_cod, c_prod, c_qtd, c_val, c_sub = st.columns(cols, vertical_alignment="center")
            marcado = c_rm.checkbox(
                "Remover", key=k_chk, label_visibility="collapsed", disabled=vendido > 0,
            )
            with c_foto:
                _mostrar_foto(prod.get("foto_url"))
            c_cod.write(prod.get("codigo_interno") or "-")
            c_prod.write(_descricao_pdf(prod, usar_site))
            if vendido > 0:
                c_prod.caption(f"já vendido: {_fmt_qtd(vendido)}")
            qtd = c_qtd.number_input(
                "Qtd", min_value=vendido, max_value=atual, step=1.0, format="%g",
                key=k_qtd, label_visibility="collapsed", disabled=bool(marcado),
            )
            c_val.write(_fmt_moeda(valor_unit))
            c_sub.write(_fmt_moeda(0.0 if marcado else qtd * valor_unit))

            if marcado or not _iguais(qtd, atual):
                rem[it["id"]] = {"remover": bool(marcado), "qtd": float(qtd)}
            else:
                rem.pop(it["id"], None)

def _render_ajuste_maleta(parceiro: dict, envio: dict, itens: list, vendidos: dict) -> None:
    """Etapa 'Ajustar maleta' (maleta com o parceiro).

    Itens atuais recolhidos -> opção 1 (remover) ou 2 (adicionar) -> prévia da maleta como
    ficaria (com PDF) -> salvar. Sem conferência física: as alterações só dependem do botão
    de salvar e ficam registradas nas movimentações.

    Não é um @st.fragment: o botão do PDF abre um popup e não funciona dentro de fragmento."""
    envio_id = envio["id"]
    reset = st.session_state.get("parceiros_envio_reset", 0)
    base = f"parceiros_adj_{envio_id}_{reset}"
    try:
        disp = _carregar_produtos_disponiveis()
    except Exception as e:
        st.error(f"Não foi possível carregar os produtos em estoque: {e}")
        return

    st.caption(
        "Escolha uma das opções para tirar ou colocar produtos na maleta. As alterações aparecem "
        "na prévia abaixo e só são gravadas ao clicar em salvar; cada uma fica registrada nas movimentações."
    )

    # --- itens atuais: recolhidos ---
    pecas_atuais = sum(float(i.get("quantidade") or 0) for i in itens)
    valor_atual = sum(float(i.get("quantidade") or 0) * float(i.get("valor_unitario") or 0) for i in itens)
    usar_site = bool(st.session_state.get(CHAVE_USAR_SITE, False))
    with st.expander(
        f"📦 Itens da maleta atual ({len(itens)} produto(s) · {_fmt_qtd(pecas_atuais)} peça(s))",
        expanded=False,
    ):
        if itens:
            _legenda_fotos([(i.get("produtos") or {}).get("foto_url") for i in itens])
            st.markdown(_tabela_maleta_atual(itens, vendidos, usar_site), unsafe_allow_html=True)
        else:
            st.caption("A maleta está vazia.")

    # --- as duas opções ---
    opcao = _menu("Opção", OPCOES_AJUSTE, key=f"parceiros_ajuste_op_{envio_id}")
    rem = st.session_state.setdefault(f"{base}_rem", {})
    if opcao == OPCOES_AJUSTE[0]:
        _ui_remover_itens(itens, vendidos, base, rem)
    else:
        # fragmento: filtros e páginas não refazem a tela; a seleção muda -> rerun completo
        _lista_candidatos(envio, itens, disp, "Ajuste")

    # --- o que mudou (remoções e reduções + adições) ---
    linhas_editadas = [
        {
            "id": it["id"],
            "Remover": rem.get(it["id"], {}).get("remover", False),
            "Qtd": rem.get(it["id"], {}).get("qtd", float(it.get("quantidade") or 0)),
        }
        for it in itens
    ]
    minimos = {
        it["produto_id"]: min(float(vendidos.get(it["produto_id"], 0.0)), float(it.get("quantidade") or 0))
        for it in itens
    }
    remover, atualizar, erros = _classificar_alteracoes(itens, linhas_editadas, disp, minimos)

    sel = st.session_state.get(f"parceiros_cand_{envio_id}_{reset}_sel", {})
    novos = []
    for pid, qtd in sel.items():
        p = disp.get(pid)
        if not p or p["disponivel"] <= 0:
            continue
        if qtd <= 0 or qtd > p["disponivel"]:
            erros.append(
                f"{p['codigo']}: quantidade {_fmt_qtd(qtd)} maior que o disponível ({_fmt_qtd(p['disponivel'])})."
            )
            continue
        novos.append((pid, float(qtd), p))

    st.divider()
    st.markdown("##### 🧾 Maleta como ficaria")
    for erro in erros:
        st.error(erro)
    if erros:
        return
    if not (remover or atualizar or novos):
        st.caption(
            "Nenhuma alteração ainda. Marque itens para remover (opção 1) ou escolha produtos "
            "para adicionar (opção 2)."
        )
        return

    por_remover, nova_qtd = set(remover), dict(atualizar)
    projetados, linhas_prev = [], []
    for it in itens:
        if it["id"] in por_remover:
            continue
        atual = float(it.get("quantidade") or 0)
        qtd = nova_qtd.get(it["id"], atual)
        projetados.append({**it, "quantidade": qtd})
        prod = it.get("produtos") or {}
        linhas_prev.append([
            ("foto", prod.get("foto_url")),
            prod.get("codigo_interno") or "-",
            _descricao_pdf(prod, usar_site),
            _fmt_qtd(qtd),
            f"✏️ {_fmt_qtd(atual)} → {_fmt_qtd(qtd)}" if it["id"] in nova_qtd else "—",
        ])
    for pid, qtd, p in novos:
        prod = {
            "codigo_interno": p["codigo"],
            "descricao": p["descricao"],
            "foto_url": p.get("foto_url"),
            "produto_descricao_site": p.get("produto_descricao_site") or "",
        }
        projetados.append({
            "id": f"novo_{pid}", "produto_id": pid, "quantidade": qtd,
            "valor_unitario": p["preco"], "produtos": prod,
        })
        linhas_prev.append([
            ("foto", prod["foto_url"]), prod["codigo_interno"],
            _descricao_pdf(prod, usar_site), _fmt_qtd(qtd), "➕ novo",
        ])

    pecas_novas = sum(float(i.get("quantidade") or 0) for i in projetados)
    valor_novo = sum(float(i.get("quantidade") or 0) * float(i.get("valor_unitario") or 0) for i in projetados)

    def _delta(novo: float, antigo: float, formato) -> str:
        dif = novo - antigo
        return None if _iguais(dif, 0.0) else f"{'+' if dif > 0 else '-'}{formato(abs(dif))}"

    m1, m2, m3 = st.columns(3)
    m1.metric("Produtos", len(projetados), delta=_delta(len(projetados), len(itens), _fmt_qtd))
    m2.metric("Peças", _fmt_qtd(pecas_novas), delta=_delta(pecas_novas, pecas_atuais, _fmt_qtd))
    m3.metric("Valor (preço sugerido)", _fmt_moeda(valor_novo), delta=_delta(valor_novo, valor_atual, _fmt_moeda))

    if linhas_prev:
        _legenda_fotos([l[0][1] for l in linhas_prev])
        st.markdown(
            _html_tabela(["Foto", "Código", "Produto", "Qtd", "Alteração"], linhas_prev, colunas_num={3}),
            unsafe_allow_html=True,
        )
    else:
        st.info("A maleta ficaria vazia.")
    removidos = [_rotulo_produto(it.get("produtos") or {}) for it in itens if it["id"] in por_remover]
    if removidos:
        st.caption(f"➖ Removidos: {_resumir_lista(removidos)}")

    motivo = st.text_input("Motivo (opcional)", key=f"parceiros_ajuste_motivo_{envio_id}_{reset}")
    col_pdf, col_salvar, _ = st.columns([1.2, 2, 2])
    with col_pdf:
        _botao_pdf_maleta(parceiro.get("nome") or "", envio, projetados, "ajuste")
    with col_salvar:
        salvar = st.button(
            "💾 Salvar alterações na maleta", type="primary", key="parceiros_ajuste_salvar",
        )
    st.caption("O PDF mostra a maleta como ficaria. Salve para gravar a alteração e atualizar o estoque reservado.")

    if not salvar:
        return

    # A tela usa dados em cache (até 30 s); ao gravar, confere de novo com o estoque atual.
    _carregar_produtos_disponiveis.clear()
    disp_agora = _carregar_produtos_disponiveis()
    remover_a, atualizar_a, erros_a = _classificar_alteracoes(itens, linhas_editadas, disp_agora, minimos)
    for pid, qtd, p in novos:
        maximo = float(disp_agora.get(pid, {}).get("disponivel", 0.0))
        if qtd > maximo:
            erros_a.append(f"{p['codigo']}: quantidade {_fmt_qtd(qtd)} maior que o disponível ({_fmt_qtd(maximo)}).")
    try:
        if erros_a:
            raise ValueError(" | ".join(erros_a))
        por_id = {it["id"]: it for it in itens}
        movimentos = []
        if remover_a:
            for item_id in remover_a:
                movimentos.append(
                    (por_id[item_id]["produto_id"], "remocao", float(por_id[item_id]["quantidade"] or 0))
                )
            supabase.table("parcerias_envio_itens").delete().in_("id", remover_a).execute()
        for item_id, nova in atualizar_a:
            atual = float(por_id[item_id]["quantidade"] or 0)
            supabase.table("parcerias_envio_itens").update({"quantidade": nova}).eq("id", item_id).execute()
            movimentos.append((
                por_id[item_id]["produto_id"], "adicao" if nova > atual else "remocao", abs(nova - atual),
            ))
        if novos:
            supabase.table("parcerias_envio_itens").insert([
                {
                    "envio_id": envio_id,
                    "produto_id": pid,
                    "quantidade": qtd,
                    "valor_unitario": float(disp_agora[pid]["preco"]),
                }
                for pid, qtd, _p in novos
            ]).execute()
            movimentos += [(pid, "adicao", qtd) for pid, qtd, _p in novos]
        with st.spinner("Gerando e guardando os PDFs da maleta..."):
            pdf_id, aviso = _guardar_pdfs_evento(
                parceiro.get("nome") or "", envio, projetados, "ajuste", motivo,
            )
        _registrar_movimentos(envio_id, movimentos, motivo, pdf_id)
        _resetar_editores_envio()
        msg = "Alterações salvas e registradas nas movimentações. A maleta foi atualizada."
        _flash("warning", f"{msg} Atenção: {aviso}") if aviso else _flash("success", msg)
        st.rerun()
    except Exception as e:
        _invalidar_cache_envios()
        st.error(f"Não foi possível salvar as alterações: {e}")

def _render_envio_enviado(parceiro: dict, envio: dict, itens: list, movimentos: list, pdfs: list = None) -> None:
    envio_id = envio["id"]

    st.markdown(
        f"##### 📦 Envio #{envio['numero_envio']} — maleta com o parceiro desde "
        f"{_fmt_data(envio.get('data_envio'))}"
    )
    if envio.get("observacao"):
        st.caption(envio["observacao"])
    if envio.get("obs_conferencia_saida"):
        st.caption(f"Conferência de saída: {envio['obs_conferencia_saida']}")

    canal_id = parceiro.get("canal_id")
    if not canal_id:
        st.warning("Este parceiro não possui canal de venda vinculado: não é possível comparar com as vendas do sistema.")
    try:
        vendidos = _vendido_por_produto(canal_id, envio.get("data_envio"))
    except Exception as e:
        st.error(f"Não foi possível carregar os dados da maleta: {e}")
        return

    total_env = sum(float(i.get("quantidade") or 0) for i in itens)
    total_ven = sum(
        min(float(vendidos.get(i["produto_id"], 0.0)), float(i.get("quantidade") or 0)) for i in itens
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Produtos", len(itens))
    c2.metric("Peças enviadas", _fmt_qtd(total_env))
    c3.metric("Vendidas (sistema)", _fmt_qtd(total_ven))
    c4.metric("Ainda na maleta", _fmt_qtd(total_env - total_ven))

    etapa = _menu("Etapa", ETAPAS_ENVIADO, key=f"parceiros_etapa_{envio_id}")

    if etapa == ETAPAS_ENVIADO[0]:
        _botao_pdf_maleta(parceiro.get("nome") or "", envio, itens, "maleta")
        if itens:
            usar_site = _toggle_descricao_site(f"parceiros_tg_maleta_{envio_id}")
            _legenda_fotos([(i.get("produtos") or {}).get("foto_url") for i in itens])
            st.markdown(_tabela_maleta_atual(itens, vendidos, usar_site), unsafe_allow_html=True)
        else:
            st.info("A maleta está vazia. Use 'Ajustar maleta' para adicionar produtos.")
        _render_movimentos_ciclo(envio, movimentos, pdfs or [])
        _mostrar_fotos_maleta(envio_id)

    elif etapa == ETAPAS_ENVIADO[1]:
        _render_ajuste_maleta(parceiro, envio, itens, vendidos)

    else:
        _render_fechamento_envio(parceiro, envio, itens)

# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------

@_fragmento
def _render_historico_envios(parceiro: dict, envios: list, itens_por_envio: dict, movimentos_por_envio: dict) -> None:
    st.markdown("##### Histórico de envios")
    if not envios:
        st.caption("Nenhum envio registrado ainda para este parceiro.")
        return

    linhas = []
    for e in envios:
        itens = itens_por_envio.get(e["id"], [])
        fechado = e.get("status") == "Fechado" and e.get("total_pecas") is not None
        if fechado:
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
            "Vendidas": _fmt_qtd_ou_traco(e.get("total_vendido")) if fechado else "-",
            "Devolvidas": _fmt_qtd_ou_traco(e.get("total_devolvido")) if fechado else "-",
            "Divergência": _fmt_qtd_ou_traco(e.get("total_divergencia")) if fechado else "-",
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
        st.caption("Clique em um envio para ver os produtos, as movimentações e a conferência.")
        return

    envio = envios[selecionadas[0]]
    if envio["id"] in itens_por_envio:
        itens = itens_por_envio[envio["id"]]
    else:  # envios antigos não são carregados de antemão: só o que o usuário abrir
        itens = _buscar_itens_dos_envios((envio["id"],)).get(envio["id"], [])
    st.markdown(
        f"**Envio #{envio['numero_envio']}** — {_fmt_data(envio.get('data_envio'))} "
        f"({envio.get('status')})"
    )
    for rotulo, campo in (
        ("Observação", "observacao"),
        ("Conferência de saída", "obs_conferencia_saida"),
        ("Fechamento", "obs_fechamento"),
    ):
        if envio.get(campo):
            st.caption(f"{rotulo}: {envio[campo]}")
    if not itens:
        st.info("Este envio não possui produtos.")
        return

    _legenda_fotos([(i.get("produtos") or {}).get("foto_url") for i in itens])
    if envio.get("status") == "Fechado":
        linhas_det = []
        for i in itens:
            prod = i.get("produtos") or {}
            conferido = i.get("qtd_devolvida") is not None
            dif = float(i.get("qtd_divergencia") or 0)
            if not conferido:
                situacao = "sem conferência"
            elif dif == 0:
                situacao = "✅ ok"
            elif dif > 0:
                situacao = f"⚠️ falta {_fmt_qtd(dif)}"
            else:
                situacao = f"⚠️ sobra {_fmt_qtd(-dif)}"
            linhas_det.append([
                ("foto", prod.get("foto_url")),
                prod.get("codigo_interno") or "-",
                prod.get("descricao") or "-",
                _fmt_qtd(i.get("quantidade")),
                _fmt_qtd_ou_traco(i.get("qtd_vendida")),
                _fmt_qtd_ou_traco(i.get("qtd_devolvida")),
                situacao,
            ])
        st.markdown(
            _html_tabela(
                ["Foto", "Código", "Produto", "Enviado", "Vendido", "Devolvido", "Situação"],
                linhas_det,
                colunas_num={3, 4, 5},
            ),
            unsafe_allow_html=True,
        )
    else:
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

    if envio["id"] in movimentos_por_envio:
        movimentos = movimentos_por_envio[envio["id"]]
    else:
        movimentos = _buscar_movimentos((envio["id"],)).get(envio["id"], [])
    pdfs = _buscar_pdfs_maleta((envio["id"],)).get(envio["id"], [])
    _render_movimentos_ciclo(envio, movimentos, pdfs, ocultar_vazio=True)
    _mostrar_fotos_maleta(envio["id"])

def _secao_parceiro_produtos_enviados(parceiro: dict) -> None:
    """Aba 'Produtos Enviados': ciclo da maleta do parceiro.

    montagem -> conferência de saída -> maleta com o parceiro (ajustes) ->
    recebimento e fechamento do ciclo -> novo envio. O histórico de envios fica na aba
    'Históricos'."""
    _mostrar_flash()
    try:
        envios = _buscar_envios(parceiro["id"])
        ativo = next((e for e in envios if e.get("status") in STATUS_ATIVOS), None)
        # Só carrega os itens do que a tela precisa: o envio ativo ou, sem ativo, o
        # último fechado (para sugerir os devolvidos). O histórico completo cresce
        # sem parar; os detalhes de envios antigos são buscados sob demanda.
        if ativo is not None:
            ids_itens = (ativo["id"],)
        else:
            ids_itens = (envios[0]["id"],) if envios else ()
        itens_por_envio = _buscar_itens_dos_envios(ids_itens)
        enviado = ativo is not None and ativo.get("status") == "Enviado"
        movimentos_por_envio = _buscar_movimentos((ativo["id"],)) if enviado else {}
        pdfs_por_envio = _buscar_pdfs_maleta((ativo["id"],)) if enviado else {}
    except Exception as e:
        st.error(f"Não foi possível carregar os envios: {e}")
        st.caption(
            "Verifique se o script SQL de envios (migracao_ciclo_maleta.sql) foi "
            "executado por completo, incluindo as colunas e a tabela de movimentações."
        )
        return

    if ativo is None:
        passo = -1
    elif ativo.get("status") == "Aberto":
        conferindo = bool(
            st.session_state.get(f"parceiros_envio_conferindo_{ativo['id']}")
            and itens_por_envio.get(ativo["id"])
        )
        passo = 1 if conferindo else 0
    else:
        etapa = st.session_state.get(f"parceiros_etapa_{ativo['id']}")
        passo = 3 if etapa == ETAPAS_ENVIADO[2] else 2
    st.markdown(
        _html_passos(["Montagem", "Conferência de saída", "Maleta com o parceiro", "Recebimento e fechamento"], passo),
        unsafe_allow_html=True,
    )

    if ativo is None:
        _render_novo_envio(parceiro, envios, itens_por_envio)
    elif ativo.get("status") == "Aberto":
        _render_envio_aberto(parceiro, ativo, itens_por_envio.get(ativo["id"], []))
    else:
        _render_envio_enviado(
            parceiro, ativo, itens_por_envio.get(ativo["id"], []),
            movimentos_por_envio.get(ativo["id"], []),
            pdfs_por_envio.get(ativo["id"], []),
        )

def _secao_historico_envios(parceiro: dict) -> None:
    """Histórico das maletas (envios) do parceiro, com o detalhe de cada uma ao clicar."""
    try:
        envios = _buscar_envios(parceiro["id"])
        ativo = next((e for e in envios if e.get("status") in STATUS_ATIVOS), None)
        # Só o envio ativo precisa dos itens de antemão (para o resumo da linha); os
        # envios antigos são buscados quando o usuário clica neles.
        itens_por_envio = _buscar_itens_dos_envios((ativo["id"],)) if ativo else {}
    except Exception as e:
        st.error(f"Não foi possível carregar os envios: {e}")
        return
    _render_historico_envios(parceiro, envios, itens_por_envio, {})

MALETA_SUBSECOES = ["atual", "historico"]
MALETA_ROTULOS = {"atual": "📦 Maleta atual", "historico": "🗂️ Histórico de envios"}


def secao_maleta(parceiro: dict) -> None:
    """Grupo 'Maleta' do parceiro: ciclo físico da maleta e histórico de envios."""
    sub = _menu("Maleta", MALETA_SUBSECOES, key="parceiros_maleta_sub", formatar=MALETA_ROTULOS.get)
    if sub == "atual":
        _secao_parceiro_produtos_enviados(parceiro)
    else:
        _mostrar_flash()
        _secao_historico_envios(parceiro)
