"""
Módulo de compras: parsing de NF-e (XML) e importação da nota para o Supabase.

Fluxo (importar_nfe):
  1. parse (parse_nfe_file / parse_nfe_string) — extrai cabeçalho, fornecedor e itens
  2. upsert do fornecedor
  3. insert da compra (cabeçalho) - idempotente via chave_acesso
  4. upsert de produtos novos (atualizando ultima_compra e nr_compras)
  5. insert dos itens da compra
  6. atualiza saldo de estoque + grava movimento no ledger (via servicos.estoque)
  7. atualiza o preço de venda sugerido em estoque (2x o valor_unitario da
     compra mais recente do produto)

O parsing de XML (antes em nfe_parser.py, separado) foi trazido pra cá porque
só é usado por este pipeline de importação de compras.

Requer as variáveis de ambiente de servicos/supabase_admin.py:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Instalar: pip install supabase
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime

from supabase import Client

from .estoque import registrar_movimento
from .supabase_admin import get_client  # noqa: F401  (re-exportado por conveniência)

# ---------------------------------------------------------------------------
# Parsing de NF-e (modelo 55, padrão SEFAZ / nfeProc)
# ---------------------------------------------------------------------------

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


def _text(el, path, default=None):
    node = el.find(path, NS)
    return node.text if node is not None and node.text is not None else default


def _dec(el, path, default="0"):
    val = _text(el, path, default)
    return Decimal(val)


@dataclass
class ItemNota:
    numero_item: int
    codigo_interno: str      # cProd
    descricao: str           # xProd
    ncm: str
    unidade: str              # uCom
    quantidade: Decimal       # qCom
    valor_unitario: Decimal   # vUnCom
    valor_desconto: Decimal   # vDesc
    valor_total: Decimal      # vProd - vDesc


@dataclass
class NotaFiscal:
    chave_acesso: str
    numero_nf: str
    serie: str
    natureza_operacao: str
    data_emissao: datetime
    fornecedor_cnpj: str
    fornecedor_razao_social: str
    fornecedor_nome_fantasia: str
    fornecedor_uf: str
    fornecedor_municipio: str
    valor_produtos: Decimal
    valor_desconto: Decimal
    valor_total: Decimal
    itens: list[ItemNota] = field(default_factory=list)
    xml_original: str = ""


def parse_nfe_string(xml_content: str) -> NotaFiscal:
    root = ET.fromstring(xml_content)

    inf_nfe = root.find(".//nfe:infNFe", NS)
    ide = inf_nfe.find("nfe:ide", NS)
    emit = inf_nfe.find("nfe:emit", NS)
    ender_emit = emit.find("nfe:enderEmit", NS)
    total = inf_nfe.find("nfe:total/nfe:ICMSTot", NS)

    # chave de acesso: preferir o protocolo de autorização (chNFe), com fallback pro atributo Id
    ch_nfe = _text(root, ".//nfe:protNFe/nfe:infProt/nfe:chNFe")
    if not ch_nfe:
        raw_id = inf_nfe.get("Id", "")
        ch_nfe = raw_id.replace("NFe", "") if raw_id else None

    dh_emi_raw = _text(ide, "nfe:dhEmi")
    data_emissao = datetime.fromisoformat(dh_emi_raw) if dh_emi_raw else None

    itens = []
    for det in inf_nfe.findall("nfe:det", NS):
        prod = det.find("nfe:prod", NS)
        itens.append(ItemNota(
            numero_item=int(det.get("nItem")),
            codigo_interno=_text(prod, "nfe:cProd"),
            descricao=_text(prod, "nfe:xProd"),
            ncm=_text(prod, "nfe:NCM"),
            unidade=_text(prod, "nfe:uCom"),
            quantidade=_dec(prod, "nfe:qCom"),
            valor_unitario=_dec(prod, "nfe:vUnCom"),
            valor_desconto=_dec(prod, "nfe:vDesc"),
            valor_total=_dec(prod, "nfe:vProd") - _dec(prod, "nfe:vDesc"),
        ))

    return NotaFiscal(
        chave_acesso=ch_nfe,
        numero_nf=_text(ide, "nfe:nNF"),
        serie=_text(ide, "nfe:serie"),
        natureza_operacao=_text(ide, "nfe:natOp"),
        data_emissao=data_emissao,
        fornecedor_cnpj=_text(emit, "nfe:CNPJ"),
        fornecedor_razao_social=_text(emit, "nfe:xNome"),
        fornecedor_nome_fantasia=_text(emit, "nfe:xFant"),
        fornecedor_uf=_text(ender_emit, "nfe:UF"),
        fornecedor_municipio=_text(ender_emit, "nfe:xMun"),
        valor_produtos=_dec(total, "nfe:vProd"),
        valor_desconto=_dec(total, "nfe:vDesc"),
        valor_total=_dec(total, "nfe:vNF"),
        itens=itens,
        xml_original=xml_content,
    )


def parse_nfe_file(path: str) -> NotaFiscal:
    with open(path, "r", encoding="utf-8") as f:
        return parse_nfe_string(f.read())


# ---------------------------------------------------------------------------
# Importação da NF-e já parseada para o Supabase
# ---------------------------------------------------------------------------

def upsert_fornecedor(sb: Client, nota: NotaFiscal) -> int:
    resp = sb.table("fornecedores").upsert({
        "cnpj": nota.fornecedor_cnpj,
        "razao_social": nota.fornecedor_razao_social,
        "nome_fantasia": nota.fornecedor_nome_fantasia,
        "uf": nota.fornecedor_uf,
        "municipio": nota.fornecedor_municipio,
    }, on_conflict="cnpj").execute()
    return resp.data[0]["id"]


def nota_ja_importada(sb: Client, chave_acesso: str) -> bool:
    resp = sb.table("compras").select("id").eq("chave_acesso", chave_acesso).execute()
    return len(resp.data) > 0


def inserir_compra(sb: Client, nota: NotaFiscal, fornecedor_id: int) -> int:
    resp = sb.table("compras").insert({
        "chave_acesso": nota.chave_acesso,
        "numero_nf": nota.numero_nf,
        "serie": nota.serie,
        "natureza_operacao": nota.natureza_operacao,
        "fornecedor_id": fornecedor_id,
        "data_emissao": nota.data_emissao.isoformat(),
        "valor_produtos": float(nota.valor_produtos),
        "valor_desconto": float(nota.valor_desconto),
        "valor_total": float(nota.valor_total),
        "xml_original": nota.xml_original,
    }).execute()
    return resp.data[0]["id"]


def upsert_produto(sb: Client, item, data_emissao) -> int:
    """Cadastra o produto se ainda não existir (pelo codigo_interno = cProd).
    Se já existir, atualiza descrição, incrementa nr_compras e calcula a maior ultima_compra."""

    # Extrai a data do XML em formato ISO (YYYY-MM-DD)
    data_xml_str = data_emissao.isoformat()[:10] if hasattr(data_emissao, "isoformat") else str(data_emissao)[:10]

    # Consulta o registro atual do produto no banco
    prod_resp = (
        sb.table("produtos")
        .select("id, ultima_compra, nr_compras")
        .eq("codigo_interno", item.codigo_interno)
        .execute()
    )

    if prod_resp.data:
        prod_atual = prod_resp.data[0]

        # Incrementar quantidade de compras
        nr_compras_atual = prod_atual.get("nr_compras") or 0
        nr_compras_novo = nr_compras_atual + 1

        # Comparar datas e manter a maior (mais recente)
        ultima_compra_banco = prod_atual.get("ultima_compra")
        if ultima_compra_banco:
            str_banco = str(ultima_compra_banco)[:10]
            nova_ultima_compra = max(data_xml_str, str_banco)
        else:
            nova_ultima_compra = data_xml_str
    else:
        # Primeira compra registrada
        nr_compras_novo = 1
        nova_ultima_compra = data_xml_str

    resp = sb.table("produtos").upsert({
        "codigo_interno": item.codigo_interno,
        "descricao": item.descricao,
        "ncm": item.ncm,
        "unidade": item.unidade,
        "ultima_compra": nova_ultima_compra,
        "nr_compras": nr_compras_novo
    }, on_conflict="codigo_interno").execute()

    return resp.data[0]["id"]


def _atualizar_preco_venda_se_mais_recente(sb: Client, produto_id: int, valor_unitario: Decimal, data_movimento: str) -> None:
    """Recalcula estoque.Estoque_Preco_venda (2x o valor_unitario da compra) somente
    se esta compra for a mais recente já registrada para o produto (produtos.ultima_compra).
    Isso evita que a importação de uma NF antiga sobrescreva um preço já calculado
    a partir de uma compra mais nova."""

    prod_resp = (
        sb.table("produtos")
        .select("ultima_compra")
        .eq("id", produto_id)
        .execute()
    )

    ultima_compra_produto = None
    if prod_resp.data and prod_resp.data[0].get("ultima_compra"):
        ultima_compra_produto = str(prod_resp.data[0]["ultima_compra"])[:10]

    # Só atualiza o preço se esta compra for igual (ou, por segurança, posterior)
    # à ultima_compra já registrada no produto.
    if ultima_compra_produto is None or data_movimento >= ultima_compra_produto:
        novo_preco_venda = float(valor_unitario) * 2
        sb.table("estoque").upsert({
            "produto_id": produto_id,
            "Estoque_Preco_venda": novo_preco_venda,
        }, on_conflict="produto_id").execute()


def inserir_item_e_atualizar_estoque(sb: Client, compra_id: int, produto_id: int, item, data_emissao):
    sb.table("compras_itens").insert({
        "compra_id": compra_id,
        "produto_id": produto_id,
        "numero_item": item.numero_item,
        "quantidade": float(item.quantidade),
        "valor_unitario": float(item.valor_unitario),
        "valor_desconto": float(item.valor_desconto),
        "valor_total": float(item.valor_total),
    }).execute()

    data_movimento = data_emissao.isoformat()[:10] if hasattr(data_emissao, "isoformat") else str(data_emissao)[:10]

    # Antes, a atualização de saldo + inserção no ledger estava duplicada
    # aqui (era idêntica à de vendas_import.py e estoque_ajuste.py). Agora
    # é uma única implementação em servicos/estoque.py.
    registrar_movimento(
        sb,
        produto_id=produto_id,
        quantidade=float(item.quantidade),
        tipo="entrada",
        data_movimento=data_movimento,
        compra_id=compra_id,
    )

    # Atualiza o preço de venda sugerido (2x o valor_unitario da compra mais
    # recente) sempre que esta compra for a mais nova para o produto.
    _atualizar_preco_venda_se_mais_recente(sb, produto_id, item.valor_unitario, data_movimento)


def importar_nfe(caminho_xml: str) -> dict:
    """Roda o pipeline completo. Retorna um resumo do que foi feito."""
    nota = parse_nfe_file(caminho_xml)
    sb = get_client()

    if nota_ja_importada(sb, nota.chave_acesso):
        return {"status": "ja_importada", "chave_acesso": nota.chave_acesso}

    fornecedor_id = upsert_fornecedor(sb, nota)
    compra_id = inserir_compra(sb, nota, fornecedor_id)

    for item in nota.itens:
        # Passa a data da nota para calcular ultima_compra e nr_compras
        produto_id = upsert_produto(sb, item, nota.data_emissao)
        inserir_item_e_atualizar_estoque(sb, compra_id, produto_id, item, nota.data_emissao)

    return {
        "status": "importado",
        "chave_acesso": nota.chave_acesso,
        "compra_id": compra_id,
        "itens_processados": len(nota.itens),
        "valor_total": str(nota.valor_total),
    }


if __name__ == "__main__":
    import sys
    resultado = importar_nfe(sys.argv[1])
    print(resultado)
