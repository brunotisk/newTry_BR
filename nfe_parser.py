"""
Parser de NF-e (modelo 55, padrão SEFAZ / nfeProc).
Extrai cabeçalho da compra, dados do fornecedor e itens.

Uso:
    from nfe_parser import parse_nfe_file
    nota = parse_nfe_file("caminho/para/nota.xml")
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime

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


if __name__ == "__main__":
    import sys
    nota = parse_nfe_file(sys.argv[1])
    print(f"Chave de acesso : {nota.chave_acesso}")
    print(f"NF nº {nota.numero_nf} série {nota.serie} - {nota.data_emissao}")
    print(f"Fornecedor      : {nota.fornecedor_razao_social} ({nota.fornecedor_cnpj})")
    print(f"Valor produtos  : R$ {nota.valor_produtos}")
    print(f"Valor desconto  : R$ {nota.valor_desconto}")
    print(f"Valor total NF  : R$ {nota.valor_total}")
    print(f"Itens           : {len(nota.itens)}")
    print("-" * 70)
    for item in nota.itens[:5]:
        print(f"  [{item.numero_item:>2}] {item.codigo_interno} | {item.descricao[:45]:<45} | "
              f"qtd={item.quantidade} | unit=R${item.valor_unitario} | total=R${item.valor_total}")
    if len(nota.itens) > 5:
        print(f"  ... e mais {len(nota.itens) - 5} itens")
