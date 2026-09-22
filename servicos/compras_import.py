"""
Módulo de compras: parsing de NF-e (XML) e importação da nota para o Supabase.

Fluxo (importar_nfe):
  1. parse (parse_nfe_file / parse_nfe_string) — extrai cabeçalho, fornecedor e itens
  2. upsert do fornecedor
  3. insert da compra (cabeçalho) - idempotente via chave_acesso
  4. upsert de produtos novos (atualizando ultima_compra e nr_compras)
  5. insert dos itens da compra
  6. atualiza saldo de estoque + grava movimento no ledger (via servicos.estoque)
  7. sincroniza em estoque o preço de venda sugerido (2x o valor_unitario da
     compra mais recente), o preço de custo da última compra, a data da
     última compra e o número de compras do produto

O parsing de XML (antes em nfe_parser.py, separado) foi trazido pra cá porque
só é usado por este pipeline de importação de compras.

Instalar: pip install supabase
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, date

from supabase import Client

from .estoque import registrar_movimento, registrar_ajuste
from .arquivos_compra import excluir_arquivo_compra
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
        "compra_origem": "Importação XML",
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


def _sincronizar_estoque_pos_compra(sb: Client, produto_id: int, valor_unitario: Decimal, data_movimento: str) -> None:
    """Após uma compra ser registrada, sincroniza em `estoque` os campos que
    dependem do histórico de compras do produto:

      - estoque_preco_venda_sugerida: 2x o valor_unitario, mas só é recalculado
        se esta compra for a mais recente já registrada para o produto (evita
        que a importação de uma NF antiga sobrescreva um preço calculado a
        partir de uma compra mais nova).
      - estoque_preco_ultima_compra: o valor_unitario "cru" (sem multiplicar)
        pago na compra, com a mesma regra de recência acima.
      - estoque_ultima_compra: sempre espelha produtos.ultima_compra (que já é
        a data mais recente entre todas as compras do produto).
      - estoque_num_compras: sempre espelha produtos.nr_compras (contagem total
        de compras já feita em upsert_produto).
    """

    prod_resp = (
        sb.table("produtos")
        .select("ultima_compra, nr_compras")
        .eq("id", produto_id)
        .execute()
    )

    ultima_compra_produto = None
    nr_compras_produto = None
    if prod_resp.data:
        prod_atual = prod_resp.data[0]
        if prod_atual.get("ultima_compra"):
            ultima_compra_produto = str(prod_atual["ultima_compra"])[:10]
        nr_compras_produto = prod_atual.get("nr_compras")

    dados_estoque = {
        "produto_id": produto_id,
        "estoque_ultima_compra": ultima_compra_produto,
        "estoque_num_compras": nr_compras_produto,
    }

    # Só atualiza o preço (sugerido e o de custo da última compra) se esta
    # compra for igual (ou, por segurança, posterior) à ultima_compra já
    # registrada no produto.
    if ultima_compra_produto is None or data_movimento >= ultima_compra_produto:
        dados_estoque["estoque_preco_venda_sugerida"] = float(valor_unitario) * 2
        dados_estoque["estoque_preco_ultima_compra"] = float(valor_unitario)

    sb.table("estoque").upsert(dados_estoque, on_conflict="produto_id").execute()


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

    # Sincroniza estoque_ultima_compra, estoque_num_compras e (quando aplicável)
    # estoque_preco_venda_sugerida / estoque_preco_ultima_compra com base no
    # histórico atualizado do produto.
    _sincronizar_estoque_pos_compra(sb, produto_id, item.valor_unitario, data_movimento)


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


def excluir_compra(sb: Client, compra_id: int) -> dict:
    """Exclui uma compra e seus itens, revertendo os impactos no estoque e ledger.

    Etapas executadas:
      1. Carrega dados da compra e de seus itens em compras_itens.
      2. Para cada item da compra:
         - Grava no ledger (estoque_movimentos) um ajuste negativo estornando
           a quantidade comprada, atualizando estoque.quantidade_atual.
         - Recalcula produtos.nr_compras, produtos.ultima_compra e
           estoque.estoque_preco_ultima_compra com base nas demais compras do produto.
      3. Desvincula o compra_id das movimentações originais em estoque_movimentos (para não violar FK).
      4. Remove arquivos anexados em compras_arquivos e no Storage.
      5. Exclui os registros de compras_itens.
      6. Exclui o registro de compras.
    """
    compra_resp = sb.table("compras").select("*").eq("id", compra_id).execute()
    if not compra_resp.data:
        raise ValueError(f"Compra #{compra_id} não encontrada.")
    compra = compra_resp.data[0]
    numero_nf = compra.get("numero_nf") or "-"

    itens_resp = (
        sb.table("compras_itens")
        .select("id, produto_id, quantidade, valor_unitario")
        .eq("compra_id", compra_id)
        .execute()
    )
    itens = itens_resp.data or []

    produtos_afetados = set()
    hoje = date.today().isoformat()

    # 1. Estorno de estoque para cada item
    for item in itens:
        produto_id = item["produto_id"]
        quantidade = float(item.get("quantidade") or 0)
        produtos_afetados.add(produto_id)

        if quantidade > 0:
            motivo_estorno = (
                f"Estorno da compra #{compra_id} (NF {numero_nf}) - Exclusão de XML"
            )
            registrar_ajuste(
                sb,
                produto_id=produto_id,
                quantidade=-quantidade,
                motivo=motivo_estorno,
                data_movimento=hoje,
            )

    # 2. Recalcula indicadores de compras dos produtos afetados
    for produto_id in produtos_afetados:
        outras_resp = (
            sb.table("compras_itens")
            .select("valor_unitario, compra_id, compras(data_emissao)")
            .eq("produto_id", produto_id)
            .neq("compra_id", compra_id)
            .execute()
        )
        outras = outras_resp.data or []

        if outras:
            nr_compras_novo = len(outras)
            outras_ord = sorted(
                outras,
                key=lambda x: str((x.get("compras") or {}).get("data_emissao") or ""),
                reverse=True,
            )
            mais_recente = outras_ord[0]
            data_ult = (
                str((mais_recente.get("compras") or {}).get("data_emissao") or "")[:10]
                or None
            )
            preco_ult = float(mais_recente.get("valor_unitario") or 0)

            sb.table("produtos").update({
                "nr_compras": nr_compras_novo,
                "ultima_compra": data_ult,
            }).eq("id", produto_id).execute()

            sb.table("estoque").update({
                "estoque_num_compras": nr_compras_novo,
                "estoque_ultima_compra": data_ult,
                "estoque_preco_ultima_compra": preco_ult,
            }).eq("produto_id", produto_id).execute()
        else:
            # Não restaram outras compras deste produto
            sb.table("produtos").update({
                "nr_compras": 0,
                "ultima_compra": None,
            }).eq("id", produto_id).execute()

            sb.table("estoque").update({
                "estoque_num_compras": 0,
                "estoque_ultima_compra": None,
                "estoque_preco_ultima_compra": None,
            }).eq("produto_id", produto_id).execute()

    # 3. Desvincula compra_id de estoque_movimentos para preservar histórico
    sb.table("estoque_movimentos").update({"compra_id": None}).eq(
        "compra_id", compra_id
    ).execute()

    # 4. Remove arquivos anexados da compra
    try:
        arqs_resp = (
            sb.table("compras_arquivos")
            .select("id, caminho_storage")
            .eq("compra_id", compra_id)
            .execute()
        )
        for arq in (arqs_resp.data or []):
            try:
                excluir_arquivo_compra(arq, supabase=sb)
            except Exception:
                sb.table("compras_arquivos").delete().eq("id", arq["id"]).execute()
    except Exception:
        pass

    # 5. Exclui itens da compra
    sb.table("compras_itens").delete().eq("compra_id", compra_id).execute()

    # 6. Exclui a compra
    sb.table("compras").delete().eq("id", compra_id).execute()

    return {
        "status": "sucesso",
        "compra_id": compra_id,
        "numero_nf": numero_nf,
        "itens_estornados": len(itens),
        "produtos_afetados": len(produtos_afetados),
    }


if __name__ == "__main__":
    import sys
    resultado = importar_nfe(sys.argv[1])
    print(resultado)

