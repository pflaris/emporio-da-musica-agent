"""Utilitários de texto (normalização/tokenização em PT-BR) e de dinheiro (Decimal + formato R$)."""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

# --------------------------------------------------------------------------------------
# Texto
# --------------------------------------------------------------------------------------

STOPWORDS = frozenset(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas por para pra com sem e ou que se
    eu voce voces meu minha seu sua ao aos ate esta esse essa isso ser tem ter qual quais quanto
    quando onde como ha mais muito ja so me te ele ela nao ai la aqui vcs
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize(text: str) -> str:
    """Minúsculas e sem acentos: 'Violões' -> 'violoes'."""
    return strip_accents(text).lower()


def stem(token: str) -> str:
    """Remove plurais simples para que 'violões', 'violão' e 'violao' coincidam.

    Não é um stemmer completo (seria exagero para um corpus pequeno); só unifica singular/plural,
    aplicado de forma idêntica a documentos e consultas.
    """
    if len(token) <= 3:
        return token
    for suffix, replacement in (("oes", "ao"), ("aes", "ao"), ("ais", "al")):
        if token.endswith(suffix):
            return token[: -len(suffix)] + replacement
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    tokens = _TOKEN_RE.findall(normalize(text))
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t) for t in tokens]


# --------------------------------------------------------------------------------------
# Dinheiro
# --------------------------------------------------------------------------------------

_CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Converte para Decimal com 2 casas (arredondamento comercial). Evita erros de float."""
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def brl(value) -> str:
    """Formata no padrão brasileiro: 1299 -> 'R$ 1.299,00'."""
    formatted = f"{money(value):,.2f}"  # 1,299.00
    return "R$ " + formatted.replace(",", "§").replace(".", ",").replace("§", ".")


def apply_discount(price, percent) -> Decimal:
    """Preço após desconto percentual, já arredondado."""
    return money(Decimal(str(price)) * (Decimal(100) - Decimal(str(percent))) / Decimal(100))
