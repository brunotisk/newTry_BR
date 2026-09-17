from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


_FRONTEND = Path(__file__).parent / "campo_cliente_frontend"
_campo_cliente = components.declare_component("campo_cliente", path=str(_FRONTEND))


def campo_cliente(label, value="", opcoes=None, placeholder="", key=None):
    """Campo de cliente com lista suspensa pesquisável e entrada livre."""
    resultado = _campo_cliente(
        label=label,
        value=value or "",
        opcoes=opcoes or [],
        placeholder=placeholder,
        key=key,
        default=value or "",
        height=72,
    )
    return value if resultado is None else resultado
