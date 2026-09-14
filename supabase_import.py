"""
Importa uma NF-e (já parseada por nfe_parser.py) para o Supabase:
  1. upsert do fornecedor
  2. insert da compra (cabeçalho) - idempotente via chave_acesso
  3. upsert de produtos novos (atualizando ultima_compra e nr_compras)
  4. insert dos itens da compra
  5. atualiza saldo de estoque + grava movimento no ledger

Requer variáveis de ambiente:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY   (service_role key - só use isso no backend, nunca no client)

Instalar: pip install supabase
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from nfe_parser import parse_nfe_file, NotaFiscal

from supabase import create_client, Client

load_dotenv()  # lê o arquivo .env na raiz do projeto, se existir


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


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


def inserir_item_e_atualizar_estoque(sb: Client, compra_id: int, produto_id: int, item):
    item_resp = sb.table("compras_itens").insert({
        "compra_id": compra_id,
        "produto_id": produto_id,
        "numero_item": item.numero_item,
        "quantidade": float(item.quantidade),
        "valor_unitario": float(item.valor_unitario),
        "valor_desconto": float(item.valor_desconto),
        "valor_total": float(item.valor_total),
    }).execute()
    compra_item_id = item_resp.data[0]["id"]

    # saldo atual do produto (0 se ainda não existir linha de estoque)
    estoque_resp = sb.table("estoque").select("quantidade_atual").eq("produto_id", produto_id).execute()
    saldo_anterior = estoque_resp.data[0]["quantidade_atual"] if estoque_resp.data else 0
    novo_saldo = float(saldo_anterior) + float(item.quantidade)

    sb.table("estoque").upsert({
        "produto_id": produto_id,
        "quantidade_atual": novo_saldo,
    }, on_conflict="produto_id").execute()

    sb.table("estoque_movimentos").insert({
        "produto_id": produto_id,
        "compra_item_id": compra_item_id,
        "tipo": "entrada",
        "quantidade": float(item.quantidade),
        "saldo_apos": novo_saldo,
    }).execute()


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
        inserir_item_e_atualizar_estoque(sb, compra_id, produto_id, item)

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