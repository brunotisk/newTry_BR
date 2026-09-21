"""
Único ponto de criação do client Supabase com service_role key.

Usado por módulos em `servicos/` que precisam gravar dados
ignorando RLS.

O ambiente é definido pela variável BD no arquivo .env:

    BD=HML
    BD=PRD

As credenciais de cada ambiente ficam separadas no .env.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from supabase import create_client, Client


load_dotenv()


def get_client() -> Client:
    """Cria o client Supabase usando a service key do ambiente atual."""

    bd = os.getenv("BD", "").strip().upper()

    if bd == "HML":
        url = os.getenv("SUPABASE_URL_HOMOLOG")
        key = os.getenv("SUPABASE_SERVICE_KEY_HOMOLOG")

    elif bd == "PRD":
        url = os.getenv("SUPABASE_URL_PRD")
        key = os.getenv("SUPABASE_SERVICE_KEY_PRD")

    else:
        raise RuntimeError(
            "BD inválido. Configure no .env como BD=HML ou BD=PRD."
        )

    if not url:
        raise RuntimeError(
            f"SUPABASE_URL não configurada para o ambiente {bd}."
        )

    if not key:
        raise RuntimeError(
            f"SUPABASE_SERVICE_KEY não configurada para o ambiente {bd}."
        )

    return create_client(url, key)