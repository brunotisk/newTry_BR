"""
Registra ajustes manuais de estoque (perda, quebra, contagem física, etc.)
que não vêm de uma compra nem de uma venda.

Usa o client com service_role key (mesmo padrão de vendas_import.py e
supabase_import.py) para poder gravar em `estoque` e `estoque_movimentos`.

Requer as mesmas variáveis de ambiente:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def registrar_ajuste(
    sb: Client,
    produto_id: int,
    quantidade: float,
    motivo: str,
    data_movimento: str,
) -> int:
    """Registra um ajuste manual de estoque.

    `quantidade` é um valor com sinal: positivo para adicionar ao saldo
    (ex.: encontrou itens a mais na contagem), negativo para retirar
    (ex.: perda, quebra, extravio).
    """
    estoque_resp = sb.table("estoque").select("quantidade_atual").eq("produto_id", produto_id).execute()
    saldo_anterior = estoque_resp.data[0]["quantidade_atual"] if estoque_resp.data else 0
    novo_saldo = float(saldo_anterior) + float(quantidade)

    sb.table("estoque").upsert({
        "produto_id": produto_id,
        "quantidade_atual": novo_saldo,
    }, on_conflict="produto_id").execute()

    resp = sb.table("estoque_movimentos").insert({
        "produto_id": produto_id,
        "tipo": "ajuste",
        "quantidade": float(quantidade),
        "saldo_apos": novo_saldo,
        "data_movimento": data_movimento,
        "motivo": motivo,
    }).execute()

    return resp.data[0]["id"]
