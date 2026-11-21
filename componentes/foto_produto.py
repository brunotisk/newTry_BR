"""Campo de foto usado no cadastro/edição de produtos e de kits.

A foto vai para o bucket público `fotos_produtos` e a URL fica em `produtos.foto_url`
(a mesma regra do Admin > Auditoria de Fotos: JPEG, maior lado até 1200 px, arquivo
<codigo_interno>.jpg). Kit é um produto comum, então usa o mesmo campo.
"""
from __future__ import annotations

import streamlit as st

from servicos.busca_fotos_produtos import enviar_foto_do_produto

TIPOS_FOTO = ["jpg", "jpeg", "png", "webp"]


def campo_foto(key: str, foto_atual: str | None = None, rotulo: str = "Foto (opcional)"):
    """Mostra a foto atual (se houver) e o seletor de arquivo. Devolve o arquivo escolhido ou None."""
    url = str(foto_atual or "").strip()
    if url.startswith("http"):
        c_img, c_up = st.columns([1, 4], vertical_alignment="center")
        c_img.image(url, width=96)
        alvo = c_up
        rotulo = "Trocar foto (opcional)"
    else:
        alvo = st
    arquivo = alvo.file_uploader(
        rotulo,
        type=TIPOS_FOTO,
        key=key,
        help="A foto é convertida para JPEG, reduzida para até 1200 px e substitui a atual.",
    )
    if arquivo is not None:
        st.image(arquivo, caption="Nova foto (será gravada ao salvar)", width=160)
    return arquivo


def salvar_foto(supabase, produto_id: int, codigo: str, arquivo) -> str | None:
    """Envia a foto escolhida e grava produtos.foto_url.

    Devolve None se deu certo (ou se não havia foto) e o texto do erro se falhou, para a
    tela avisar sem desfazer o cadastro já feito.
    """
    if arquivo is None:
        return None
    try:
        enviar_foto_do_produto(supabase, produto_id, str(codigo or ""), arquivo.getvalue())
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None
