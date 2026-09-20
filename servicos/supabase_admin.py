"""
Único ponto de criação do client Supabase com service_role key.

Usado por todo módulo em `servicos/` que precisa gravar dados ignorando RLS
(estoque, importação de compras, importação de vendas). Nunca use este
client em código que roda direto na sessão do usuário — para isso existe
`db.py` (client anônimo, respeitando RLS).

Antes, cada módulo (estoque_ajuste.py, supabase_import.py, vendas_import.py)
definia seu próprio `get_client()` idêntico. Agora existe só este.

Requer as variáveis de ambiente:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()  # lê o arquivo .env na raiz do projeto, se existir


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)
