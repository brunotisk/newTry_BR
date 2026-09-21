"""
Componente para armazenar arquivos relacionados a compras no Supabase Storage.

Bucket: compras
Tabela: compras_arquivos

Não interpreta XML e não cria/edita compras.
"""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any


BUCKET_COMPRAS = "compras"
TABELA_ARQUIVOS = "compras_arquivos"


def _obter_supabase():
    """Obtém o mesmo cliente Supabase usado pelo projeto em db.py."""
    from db import supabase

    return supabase


def _sanitizar_nome(nome: str) -> str:
    nome = PurePosixPath(str(nome or "arquivo")).name
    nome = re.sub(r"[^A-Za-z0-9._-]+", "_", nome)
    return nome.strip("._") or "arquivo"


def _ler_bytes(arquivo: Any) -> bytes:
    if isinstance(arquivo, bytes):
        return arquivo
    if hasattr(arquivo, "getvalue"):
        return arquivo.getvalue()
    if hasattr(arquivo, "read"):
        return arquivo.read()
    raise TypeError("arquivo deve ser bytes, UploadedFile ou objeto com read().")


def _mime_type(nome_arquivo: str, mime_type: str | None = None) -> str:
    if mime_type:
        return mime_type
    tipo, _ = mimetypes.guess_type(nome_arquivo)
    return tipo or "application/octet-stream"


def _tipo_arquivo(nome_arquivo: str, tipo_arquivo: str | None = None) -> str:
    if tipo_arquivo:
        return tipo_arquivo.upper().strip()

    extensao = PurePosixPath(nome_arquivo).suffix.lower()
    if extensao == ".xml":
        return "XML"
    if extensao == ".pdf":
        return "PDF"
    return "OUTRO"


def nome_arquivo_compra(numero_nf: Any, tipo_arquivo: str) -> str:
    """Gera o nome padronizado do arquivo usando o número da NF."""
    tipo = str(tipo_arquivo or "").upper().strip()
    if tipo not in {"XML", "PDF"}:
        raise ValueError("tipo_arquivo deve ser XML ou PDF.")

    numero = str(numero_nf or "").strip()
    if not numero:
        raise ValueError("O número da NF é obrigatório para nomear o arquivo.")

    return _sanitizar_nome(f"{numero}.{tipo.lower()}")


def _caminho_storage(compra_id: int, nome_arquivo: str) -> str:
    agora = datetime.now()
    return (
        f"{agora:%Y}/{agora:%m}/{int(compra_id)}/"
        f"{_sanitizar_nome(nome_arquivo)}"
    )


def upload_arquivo_compra(
    compra_id: int,
    arquivo: Any,
    nome_arquivo: str | None = None,
    tipo_arquivo: str | None = None,
    mime_type: str | None = None,
    supabase=None,
    sobrescrever: bool = False,
) -> dict:
    """
    Envia XML/PDF ao bucket compras e cria o vínculo em compras_arquivos.

    Retorna o registro criado no banco.
    """
    if not compra_id:
        raise ValueError("compra_id é obrigatório.")

    nome = _sanitizar_nome(
        nome_arquivo or getattr(arquivo, "name", None) or "arquivo"
    )

    if PurePosixPath(nome).suffix.lower() not in {".xml", ".pdf"}:
        raise ValueError("Nesta versão são aceitos apenas arquivos XML e PDF.")

    conteudo = _ler_bytes(arquivo)
    if not conteudo:
        raise ValueError("O arquivo está vazio.")

    tipo = _tipo_arquivo(nome, tipo_arquivo)
    mime = _mime_type(nome, mime_type)
    caminho = _caminho_storage(compra_id, nome)
    client = supabase or _obter_supabase()

    existentes = (
        client.table(TABELA_ARQUIVOS)
        .select("id, caminho_storage, nome_arquivo")
        .eq("compra_id", int(compra_id))
        .eq("nome_arquivo", nome)
        .execute()
    )

    if existentes.data and not sobrescrever:
        raise ValueError(
            f"O arquivo '{nome}' já está relacionado à compra {compra_id}."
        )

    if existentes.data and sobrescrever:
        antigo = existentes.data[0]
        caminho = antigo["caminho_storage"]
        client.storage.from_(BUCKET_COMPRAS).remove([caminho])
        client.table(TABELA_ARQUIVOS).delete().eq("id", antigo["id"]).execute()

    client.storage.from_(BUCKET_COMPRAS).upload(
        caminho,
        conteudo,
        {"content-type": mime, "upsert": False},
    )

    registro = {
        "compra_id": int(compra_id),
        "tipo_arquivo": tipo,
        "nome_arquivo": nome,
        "caminho_storage": caminho,
        "mime_type": mime,
        "tamanho_bytes": len(conteudo),
    }

    try:
        resposta = client.table(TABELA_ARQUIVOS).insert(registro).execute()
    except Exception:
        try:
            client.storage.from_(BUCKET_COMPRAS).remove([caminho])
        except Exception:
            pass
        raise

    if not resposta.data:
        raise RuntimeError("Arquivo enviado, mas o registro não foi retornado.")

    return resposta.data[0]


def listar_arquivos_compra(compra_id: int, supabase=None) -> list[dict]:
    """Lista os arquivos vinculados à compra."""
    if not compra_id:
        return []

    client = supabase or _obter_supabase()
    resposta = (
        client.table(TABELA_ARQUIVOS)
        .select(
            "id, compra_id, tipo_arquivo, nome_arquivo, "
            "caminho_storage, mime_type, tamanho_bytes, criado_em"
        )
        .eq("compra_id", int(compra_id))
        .order("criado_em", desc=True)
        .execute()
    )
    return resposta.data or []


def gerar_url_arquivo_compra(
    arquivo: dict | int,
    supabase=None,
    validade_segundos: int = 300,
) -> str:
    """Gera uma URL assinada temporária para um arquivo privado."""
    client = supabase or _obter_supabase()

    if isinstance(arquivo, int):
        resposta = (
            client.table(TABELA_ARQUIVOS)
            .select("caminho_storage")
            .eq("id", arquivo)
            .single()
            .execute()
        )
        registro = resposta.data or {}
    else:
        registro = arquivo

    caminho = registro.get("caminho_storage")
    if not caminho:
        raise ValueError("O arquivo não possui caminho_storage.")

    resposta = client.storage.from_(BUCKET_COMPRAS).create_signed_url(
        caminho, int(validade_segundos)
    )

    if isinstance(resposta, dict):
        return resposta.get("signedURL") or resposta.get("signedUrl") or ""

    return (
        getattr(resposta, "signedURL", None)
        or getattr(resposta, "signedUrl", "")
    )


def excluir_arquivo_compra(arquivo: dict | int, supabase=None) -> bool:
    """Exclui o arquivo do Storage e o registro em compras_arquivos."""
    client = supabase or _obter_supabase()

    if isinstance(arquivo, int):
        resposta = (
            client.table(TABELA_ARQUIVOS)
            .select("id, caminho_storage")
            .eq("id", arquivo)
            .single()
            .execute()
        )
        registro = resposta.data or {}
    else:
        registro = arquivo

    registro_id = registro.get("id")
    caminho = registro.get("caminho_storage")

    if not registro_id or not caminho:
        raise ValueError("É necessário informar id e caminho_storage.")

    client.storage.from_(BUCKET_COMPRAS).remove([caminho])
    client.table(TABELA_ARQUIVOS).delete().eq("id", registro_id).execute()
    return True
