from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


_FRONTEND = Path(__file__).parent / "campo_mascarado_frontend"
_campo_mascarado = components.declare_component("campo_mascarado", path=str(_FRONTEND))


def campo_mascarado(label, value="", tipo="telefone", placeholder="", key=None):
    """Campo de texto com máscara aplicada enquanto o usuário digita."""
    resultado = _campo_mascarado(
        label=label,
        value=value or "",
        tipo=tipo,
        placeholder=placeholder,
        key=key,
        default=value or "",
        height=72,
    )
    return value if resultado is None else resultado
