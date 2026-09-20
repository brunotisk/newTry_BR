"""
Funções para gerenciar as listas de apoio usadas na tela de vendas:
canais de venda e status de venda. As duas tabelas têm o mesmo formato
(id, nome, ativo), então essas funções recebem o nome da tabela.

Usam o client anônimo (db.supabase, mesma sessão autenticada do usuário),
no mesmo padrão já usado por telas/categorias.py.
"""
from db import supabase


def listar(tabela: str, apenas_ativos: bool = True) -> list[dict]:
    query = supabase.table(tabela).select("id, nome, ativo").order("nome")
    if apenas_ativos:
        query = query.eq("ativo", True)
    resp = query.execute()
    return resp.data or []


def criar(tabela: str, nome: str) -> dict:
    resp = supabase.table(tabela).insert({"nome": nome.strip()}).execute()
    return resp.data[0]


def atualizar(tabela: str, item_id: int, nome: str, ativo: bool) -> dict:
    resp = (
        supabase.table(tabela)
        .update({"nome": nome.strip(), "ativo": ativo})
        .eq("id", item_id)
        .execute()
    )
    return resp.data[0]
