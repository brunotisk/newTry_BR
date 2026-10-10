"""Fotos dos produtos: auditoria contra o catálogo do site e envio ao Storage do Supabase.

Junta, num módulo reutilizável (sem Streamlit), o que faziam validar_fotos_lolia.py
e gerar_sql_fotos.py, e acrescenta a cópia das fotos para o Storage:

  1. baixar_catalogo / indexar_por_sku  -> catálogo público (Shopify) indexado por SKU
  2. auditar                            -> cruza produtos.codigo_interno com o site e com
                                           a foto_url gravada hoje
  3. processar_lote / enviar_para_storage -> baixa a foto, normaliza (JPEG até 1200 px),
                                           sobe para o bucket e grava produtos.foto_url

A chave de ligação com o site é sempre produtos.codigo_interno (== SKU da variante),
ignorando zeros à esquerda (0010575 == 10575).
"""
from __future__ import annotations

import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from PIL import Image

LOJA_PADRAO = "https://loliaacessorios.com"
BUCKET = "fotos_produtos"

POR_PAGINA = 250
MAX_PAGINAS = 100
LADO_MAX = 1200            # px: maior lado da foto gravada no Storage
TAMANHO_MAX_DOWNLOAD = 25 * 1024 * 1024
UA = {"User-Agent": "Mozilla/5.0 (auditoria de fotos do catálogo)"}

# Estado da foto_url gravada no banco
SEM_FOTO = "sem_foto"      # foto_url vazia
STORAGE = "storage"        # já aponta para o bucket do Supabase
EXTERNA = "externa"        # aponta para outro endereço (ex.: CDN da Shopify)

# Ação sugerida pela auditoria
ACAO_OK = "ok"
ACAO_COPIAR = "copiar_do_site"            # sem foto no banco; o site tem
ACAO_MIGRAR = "migrar_para_storage"       # foto externa; passar para o Storage
ACAO_MANUAL = "enviar_manual"             # sem foto no banco e no site: subir à mão
ACOES_AUTOMATICAS = (ACAO_COPIAR, ACAO_MIGRAR)


# ----------------------------------------------------------------------
# Chaves e URLs
# ----------------------------------------------------------------------
def chave(codigo) -> str:
    """Normaliza código/SKU: só dígitos sem zeros à esquerda; senão texto em maiúsculas."""
    texto = str(codigo or "").strip()
    if texto.isdigit():
        return texto.lstrip("0") or "0"
    return texto.upper()


def caminho_foto(codigo) -> str:
    """Nome do arquivo no bucket: <codigo_interno>.jpg (caracteres inseguros viram '_')."""
    seguro = re.sub(r"[^A-Za-z0-9._-]", "_", str(codigo).strip())
    return f"{seguro}.jpg"


def url_com_largura(url: str, largura: int) -> str:
    """Na CDN da Shopify, pede a imagem já reduzida (?width=...). Outras URLs ficam iguais."""
    partes = urlparse(url)
    if "cdn.shopify.com" not in partes.netloc:
        return url
    query = dict(parse_qsl(partes.query))
    query["width"] = str(largura)
    return urlunparse(partes._replace(query=urlencode(query)))


def tipo_foto_atual(foto_url, bucket: str = BUCKET) -> str:
    url = str(foto_url or "").strip()
    if not url:
        return SEM_FOTO
    if f"/storage/v1/object/public/{bucket}/" in url:
        return STORAGE
    return EXTERNA


# ----------------------------------------------------------------------
# Catálogo do site
# ----------------------------------------------------------------------
def baixar_catalogo(loja: str = LOJA_PADRAO, progresso=None, avisos: list | None = None) -> list:
    """Lê /products.json página a página. Se houver HTTP 429, encerra e retorna o que já baixou.

    `progresso(pagina, total_produtos)` atualiza o andamento; `avisos`, quando fornecido,
    recebe uma mensagem se a loja limitar as requisições (HTTP 429).
    """
    loja = loja.rstrip("/")
    produtos = []
    for pagina in range(1, MAX_PAGINAS + 1):
        url = f"{loja}/products.json?limit={POR_PAGINA}&page={pagina}"
        resp = requests.get(url, headers=UA, timeout=30)
        if resp.status_code == 429:
            mensagem = (
                f"A loja limitou as consultas (HTTP 429) na página {pagina}. "
                f"A busca foi encerrada e a auditoria usará os {len(produtos)} produtos "
                "já baixados. Portanto, os resultados podem estar incompletos."
            )
            if avisos is not None:
                avisos.append(mensagem)
            break
        if resp.status_code != 200:
            raise RuntimeError(
                f"A loja respondeu HTTP {resp.status_code} em {url}. "
                "A lista pública de produtos pode estar desativada."
            )
        lote = resp.json().get("products") or []
        if not lote:
            break
        produtos.extend(lote)
        if progresso:
            progresso(pagina, len(produtos))
        time.sleep(0.3)
    return produtos


def indexar_por_sku(produtos: list, loja: str = LOJA_PADRAO) -> dict:
    """{chave(sku): dados}. Se o SKU se repete, vale o primeiro."""
    loja = loja.rstrip("/")
    indice = {}
    for p in produtos:
        imagens = {i["id"]: i.get("src") for i in (p.get("images") or []) if i.get("id")}
        foto_produto = (p.get("image") or {}).get("src") or next(iter(imagens.values()), None)
        n_fotos = len(p.get("images") or []) or (1 if foto_produto else 0)
        for v in p.get("variants") or []:
            sku = chave(v.get("sku"))
            if not sku or sku in indice:
                continue
            foto = (
                (v.get("featured_image") or {}).get("src")
                or imagens.get(v.get("image_id"))
                or foto_produto
            )
            indice[sku] = {
                "produto": p.get("title") or "",
                "variante": v.get("title") or "",
                "sku_site": v.get("sku") or "",
                "disponivel": "sim" if v.get("available") else "não",
                "pagina": f"{loja}/products/{p['handle']}" if p.get("handle") else "",
                "n_fotos": n_fotos,
                "foto": foto or "",
            }
    return indice


# ----------------------------------------------------------------------
# Auditoria
# ----------------------------------------------------------------------
def auditar(produtos_db: list, indice_site: dict, bucket: str = BUCKET) -> list:
    """Uma linha por produto do sistema: situação no site, foto gravada hoje e ação sugerida."""
    linhas = []
    for p in produtos_db:
        codigo = p.get("codigo_interno") or ""
        site = indice_site.get(chave(codigo))
        foto_site = (site or {}).get("foto") or ""
        if not site:
            situacao_site = "nao_encontrado"
        elif foto_site:
            situacao_site = "com_foto"
        else:
            situacao_site = "sem_foto"

        foto_atual = str(p.get("foto_url") or "").strip()
        tipo = tipo_foto_atual(foto_atual, bucket)

        # origem da cópia: a foto do site tem preferência sobre a URL externa já gravada
        origem = foto_site or (foto_atual if tipo == EXTERNA else "")
        if tipo == STORAGE:
            acao = ACAO_OK
        elif origem:
            acao = ACAO_COPIAR if tipo == SEM_FOTO else ACAO_MIGRAR
        else:
            acao = ACAO_MANUAL

        linhas.append({
            "produto_id": p.get("id"),
            "codigo_interno": codigo,
            "descricao": p.get("descricao") or "",
            "situacao_site": situacao_site,
            "produto_no_site": (site or {}).get("produto", ""),
            "produto_descricao_site": p.get("produto_descricao_site") or "",
            "pagina_site": (site or {}).get("pagina", ""),
            "qtd_fotos_site": (site or {}).get("n_fotos", ""),
            "foto_site": foto_site,
            "foto_atual_tipo": tipo,
            "foto_atual_url": foto_atual,
            "url_banco_testada": "",
            "origem_copia": origem,
            "acao": acao,
        })
    return linhas


def site_sem_cadastro(indice_site: dict, produtos_db: list) -> list:
    """SKUs que existem no site mas não correspondem a nenhum produtos.codigo_interno."""
    conhecidos = {chave(p.get("codigo_interno")) for p in produtos_db}
    return [
        {"sku_site": d["sku_site"], "produto_no_site": d["produto"], "variante": d["variante"],
         "disponivel_no_site": d["disponivel"], "qtd_fotos_site": d["n_fotos"],
         "foto_site": d["foto"], "pagina_site": d["pagina"]}
        for sku, d in indice_site.items() if sku not in conhecidos
    ]


def testar_urls(urls: list, workers: int = 16, timeout: int = 8) -> dict:
    """{url: 'ok' | 'quebrada'}: confere se cada URL abre e devolve uma imagem."""
    def _um(url):
        try:
            r = requests.head(url, headers=UA, timeout=timeout, allow_redirects=True)
            if r.status_code >= 400:  # alguns servidores rejeitam HEAD
                r = requests.get(url, headers=UA, timeout=timeout, stream=True)
                r.close()
            tipo = r.headers.get("content-type", "")
            return url, "ok" if r.status_code < 400 and tipo.startswith("image") else "quebrada"
        except Exception:
            return url, "quebrada"

    unicas = sorted(set(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_um, unicas))


# ----------------------------------------------------------------------
# Imagem + Storage
# ----------------------------------------------------------------------
def preparar_jpeg(conteudo: bytes) -> bytes:
    """Abre qualquer formato comum, achata transparência em fundo branco e salva JPEG."""
    img = Image.open(io.BytesIO(conteudo))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fundo = Image.new("RGB", img.size, (255, 255, 255))
        fundo.paste(img, mask=img.split()[-1])
        img = fundo
    else:
        img = img.convert("RGB")
    img.thumbnail((LADO_MAX, LADO_MAX))
    saida = io.BytesIO()
    img.save(saida, format="JPEG", quality=85, optimize=True)
    return saida.getvalue()


def baixar_imagem(url: str) -> bytes:
    resp = requests.get(url_com_largura(url, LADO_MAX), headers=UA, timeout=30, stream=True)
    resp.raise_for_status()
    dados = bytearray()
    for bloco in resp.iter_content(64 * 1024):
        dados.extend(bloco)
        if len(dados) > TAMANHO_MAX_DOWNLOAD:
            raise ValueError("imagem maior que 25 MB")
    return bytes(dados)


def enviar_para_storage(supabase, codigo: str, jpeg: bytes, bucket: str = BUCKET) -> str:
    """Sobe <codigo>.jpg para o bucket (sobrescreve) e devolve a URL pública com ?v=<hora>,
    para o navegador não mostrar a versão antiga em cache."""
    path = caminho_foto(codigo)
    storage = supabase.storage.from_(bucket)
    storage.upload(path, jpeg, {"content-type": "image/jpeg", "upsert": "true", "cache-control": "3600"})
    url = str(storage.get_public_url(path)).rstrip("?")
    return f"{url}?v={int(time.time())}"


def gravar_foto_url(supabase, produto_id, url: str) -> None:
    supabase.table("produtos").update({"foto_url": url}).eq("id", produto_id).execute()


def gravar_descricao_site(supabase, produto_id, descricao: str) -> None:
    """Grava em produtos.produto_descricao_site o título correspondente do catálogo público."""
    supabase.table("produtos").update({"produto_descricao_site": descricao}).eq("id", produto_id).execute()


def gravar_descricoes_site_lote(supabase, itens: list[dict]) -> int:
    """Grava várias descrições com uma única chamada RPC do Supabase.

    Requer a função SQL atualizar_descricoes_site_lote(jsonb), incluída no pacote.
    Cada item contém `id` e `descricao`.
    """
    if not itens:
        return 0
    resposta = supabase.rpc(
        "atualizar_descricoes_site_lote", {"itens": itens}
    ).execute()
    try:
        return int(resposta.data or 0)
    except (TypeError, ValueError):
        return len(itens)


def enviar_foto_do_produto(supabase, produto_id, codigo: str, conteudo: bytes, bucket: str = BUCKET) -> str:
    """Normaliza a imagem, sobe ao Storage e grava produtos.foto_url. Devolve a nova URL."""
    url = enviar_para_storage(supabase, codigo, preparar_jpeg(conteudo), bucket)
    gravar_foto_url(supabase, produto_id, url)
    return url


def processar_lote(supabase, itens: list, progresso=None, workers: int = 4, bucket: str = BUCKET) -> list:
    """Copia as fotos de `itens` (linhas da auditoria) para o Storage e atualiza o banco.

    Baixa e reduz as imagens em paralelo; sobe e grava em sequência. Falha de um item não
    interrompe os demais. Devolve [{codigo_interno, produto_id, ok, url|erro}].
    """
    def _preparar(item):
        return preparar_jpeg(baixar_imagem(item["origem_copia"]))

    resultados = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futuros = [(item, pool.submit(_preparar, item)) for item in itens]
        for n, (item, futuro) in enumerate(futuros, 1):
            res = {"codigo_interno": item["codigo_interno"], "produto_id": item["produto_id"]}
            try:
                jpeg = futuro.result()
                url = enviar_para_storage(supabase, item["codigo_interno"], jpeg, bucket)
                gravar_foto_url(supabase, item["produto_id"], url)
                res.update(ok=True, url=url)
            except Exception as e:
                res.update(ok=False, erro=f"{type(e).__name__}: {e}")
            resultados.append(res)
            if progresso:
                progresso(n, len(itens), item["codigo_interno"])
    return resultados
