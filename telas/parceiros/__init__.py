"""Pacote de Parceiros.

    parceiros_principal.py   escolha do parceiro e navegação (Fechamento / Maleta)
    parceiros_fechamento.py  ciclo de comissão, conciliação bancária e histórico de ciclos
    parceiros_maleta.py      ciclo físico da maleta (envios, conferência, ajustes, PDFs)
    parceiros_comum.py       utilitários compartilhados

Uso: from telas.parceiros import secao_parceiros
"""
from telas.parceiros.parceiros_principal import secao_parceiros

__all__ = ["secao_parceiros"]
