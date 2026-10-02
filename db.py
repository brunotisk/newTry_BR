import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

BD = os.getenv("BD", "").strip().upper()

if BD == "HML":
    SUPABASE_URL = os.getenv("SUPABASE_URL_HOMOLOG")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY_HOMOLOG")

elif BD == "PRD":
    SUPABASE_URL = os.getenv("SUPABASE_URL_PRD")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY_PRD")

else:
    raise RuntimeError(
        "BD inválido. Use BD=HML ou BD=PRD no arquivo .env."
    )

if not SUPABASE_URL:
    raise RuntimeError(
        f"URL do Supabase não configurada para o ambiente {BD}."
    )

if not SUPABASE_KEY:
    raise RuntimeError(
        f"Chave do Supabase não configurada para o ambiente {BD}."
    )

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# O Supabase (PostgREST) devolve no máximo 1000 linhas por requisição, mesmo
# com .limit() maior. Para ler tudo, é preciso paginar com .range().
TAMANHO_PAGINA = 1000


def buscar_todos(montar_query, tamanho_pagina: int = TAMANHO_PAGINA) -> list:
    """Busca TODAS as linhas de uma query, paginando com range().

    `montar_query` é uma função sem argumentos que devolve a query SEM
    .execute() (a cada página uma query nova é montada). A query precisa ter
    .order() por uma coluna estável/única, senão a paginação pode repetir ou
    pular linhas. `tamanho_pagina` não pode passar do "Max rows" configurado
    na API do Supabase (padrão 1000).
    """
    todos: list = []
    inicio = 0
    while True:
        lote = (
            montar_query()
            .range(inicio, inicio + tamanho_pagina - 1)
            .execute()
            .data
            or []
        )
        todos.extend(lote)
        if len(lote) < tamanho_pagina:
            break
        inicio += tamanho_pagina
    return todos
