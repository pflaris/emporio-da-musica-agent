"""Catálogo: busca de produtos, preço/promoção, PIX, parcelamento e alternativas.

Toda conta (desconto, parcela) é feita aqui, em código, e devolvida já formatada: o LLM só
precisa copiar os valores. Isso evita o erro mais comum de agentes de atendimento, que é errar
aritmética de preço.
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal

from .data_store import DataStore, Product
from .text_utils import apply_discount, brl, money, normalize, tokenize

PIX_DISCOUNT_PERCENT = 5

# --------------------------------------------------------------------------------------
# Categorias (o cliente diz "violão"; o CSV tem a categoria "Violões")
# --------------------------------------------------------------------------------------

_CATEGORY_WORDS: dict[int, str] = {
    1: "guitarra guitarras",
    2: "baixo baixos contrabaixo",
    3: "bateria baterias percussao",
    4: "teclado teclados piano pianos sintetizador sintetizadores arranjador controlador",
    5: "violao violoes",
    6: "sopro madeira madeiras flauta clarinete saxofone sax oboe",
    7: "sopro metal metais trompete trombone tuba trompa",
    8: "orquestral orquestrais violino viola violoncelo cello contrabaixo",
    9: "ukulele ukuleles ukelele",
}
_CATEGORY_TOKENS = {cid: frozenset(tokenize(words)) for cid, words in _CATEGORY_WORDS.items()}


def detect_categories(text: str) -> list[int]:
    tokens = set(tokenize(text))
    return [cid for cid, words in _CATEGORY_TOKENS.items() if words & tokens]


# --------------------------------------------------------------------------------------
# Preço, promoção, PIX e parcelamento
# --------------------------------------------------------------------------------------


def current_price(store: DataStore, product: Product):
    """(preço que o cliente paga hoje, promoção ativa ou None)."""
    promo = store.active_promotion(product.id)
    if promo:
        return apply_discount(product.price, promo.discount_percent), promo
    return product.price, None


def max_installments(total: Decimal) -> int:
    """Maior nº de parcelas permitido pela seção 3.1 do manual (sempre sem juros).

    Até 3x: parcela >= R$ 50 | 4x a 6x: >= R$ 80 | 7x a 12x: >= R$ 100.
    """
    for n in range(12, 1, -1):
        minimum = 50 if n <= 3 else 80 if n <= 6 else 100
        if total / n >= minimum:
            return n
    return 1


def pricing(store: DataStore, product: Product) -> dict:
    price_now, promo = current_price(store, product)
    n = max_installments(price_now)
    info: dict = {
        "preco_atual_fmt": brl(price_now),
        "parcelamento": (
            f"até {n}x de {brl(price_now / n)} sem juros no cartão de crédito"
            if n > 1
            else "valor baixo demais para parcelar; pagamento à vista"
        ),
    }
    if promo:
        info["promocao_ativa"] = {
            "nome": promo.name,
            "desconto_percent": promo.discount_percent,
            "preco_original_fmt": brl(product.price),
            "preco_promocional_fmt": brl(price_now),
            "economia_fmt": brl(product.price - price_now),
        }
        info["desconto_pix"] = (
            "NÃO se aplica: promoções não são cumulativas com o desconto de 5% do PIX"
        )
    else:
        info["preco_tabela_fmt"] = brl(product.price)
        info["preco_no_pix_fmt"] = brl(apply_discount(product.price, PIX_DISCOUNT_PERCENT))
        info["desconto_pix"] = "5% de desconto sobre o preço de tabela no PIX"
    return info


def availability(product: Product) -> dict:
    if product.status == "discontinued":
        return {"disponibilidade": "descontinuado (não faz mais parte do catálogo)", "pode_comprar": False}
    if product.status == "coming_soon":
        return {"disponibilidade": "em breve (ainda não disponível para compra)", "pode_comprar": False}
    if product.stock <= 0:
        return {"disponibilidade": "esgotado no momento (temporariamente indisponível)", "pode_comprar": False}
    info = {"disponibilidade": f"disponível ({product.stock} em estoque)", "pode_comprar": True}
    if product.stock <= 3:
        info["ultimas_unidades"] = True
    return info


_SPEC_LABELS = {
    "top": "tampo", "back_sides": "fundo e laterais", "neck": "braço", "strings": "cordas",
    "scale": "escala", "electronics": "eletrônica", "color": "cor", "body": "corpo",
    "shells": "cascos", "pieces": "peças", "hardware": "ferragens", "keys": "teclas",
    "type": "tipo", "polyphony": "polifonia",
}  # fmt: skip
_SPEC_VALUES = {"steel": "aço", "nylon": "nylon", "yes": "sim", "no": "não"}


def format_specs(product: Product) -> str:
    parts = []
    for key, value in product.specs.items():
        label = _SPEC_LABELS.get(key, key)
        parts.append(f"{label}: {_SPEC_VALUES.get(value.lower(), value)}")
    return "; ".join(parts)


def product_card(store: DataStore, product: Product) -> dict:
    card: dict = {
        "id": product.id,
        "nome": product.name,
        "categoria": product.category_name,
        **availability(product),
        **pricing(store, product),
    }
    if product.description_reliable:
        card["descricao"] = product.description
        card["especificacoes"] = format_specs(product)
    else:
        card["aviso_dados"] = (
            "A descrição cadastrada deste item contradiz o nome. Informe apenas nome, categoria, "
            "preço e disponibilidade; para detalhes técnicos diga que vai confirmar com a equipe."
        )
    return card


# --------------------------------------------------------------------------------------
# Alternativas
# --------------------------------------------------------------------------------------


def alternatives_for(store: DataStore, product: Product, limit: int = 3) -> list[Product]:
    """Itens disponíveis da mesma categoria, do mesmo tipo de corda e preço mais próximo."""
    target, _ = current_price(store, product)
    pool = [p for p in store.products_in_category(product.category_id) if p.purchasable and p.id != product.id]

    def rank(candidate: Product):
        same_strings = candidate.specs.get("strings") == product.specs.get("strings")
        price, _ = current_price(store, candidate)
        return (0 if same_strings else 1, abs(price - target))

    return sorted(pool, key=rank)[:limit]


# --------------------------------------------------------------------------------------
# Busca
# --------------------------------------------------------------------------------------


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text))


def _normalize_strings(value: str) -> str:
    token = normalize(value or "")
    if token in {"aco", "steel", "metal", "folk"}:
        return "steel"
    if token in {"nylon", "nailon", "naylon", "nilon", "classico"}:
        return "nylon"
    return ""


def _haystack(product: Product) -> set[str]:
    text = [product.name, product.category_name]
    if product.description_reliable:
        text += [product.description, " ".join(product.specs.values())]
    if product.specs.get("strings") == "steel":
        text.append("aço")
    if product.specs.get("electronics") == "yes":
        text.append("eletroacústico captação")
    return set(tokenize(" ".join(text)))


def _overview(store: DataStore) -> dict:
    rows = []
    for category in store.categories.values():
        sellable = [p for p in store.products_in_category(category.id) if p.purchasable]
        if not sellable:
            continue
        prices = sorted(current_price(store, p)[0] for p in sellable)
        rows.append(
            {
                "categoria": category.name,
                "produtos_a_venda": len(sellable),
                "faixa_de_preco": f"de {brl(prices[0])} a {brl(prices[-1])}",
            }
        )
    return {
        "catalogo_resumo": rows,
        "observacoes": ["Nenhum filtro informado: mostrando só o resumo. Pergunte o tipo de instrumento e a faixa de preço."],
    }


def search_products(
    store: DataStore,
    *,
    query: str = "",
    category: str = "",
    price_max=None,
    price_min=None,
    strings: str = "",
    electric: bool | None = None,
    include_unavailable: bool = False,
    limit: int = 6,
) -> dict:
    notes: list[str] = []
    limit = max(1, min(int(limit or 6), 10))
    query = (query or "").strip()

    category_ids = detect_categories(category) if category else []
    if category and not category_ids:
        notes.append(f"Categoria '{category}' não reconhecida; filtro de categoria ignorado.")
    strings_value = _normalize_strings(strings)
    if strings and not strings_value:
        notes.append(f"Tipo de corda '{strings}' não reconhecido; use nylon ou aço.")

    nothing_asked = not (query or category_ids or price_max is not None or price_min is not None or strings_value or electric is not None)
    if nothing_asked:
        return _overview(store)

    pool = [p for p in store.products.values() if not category_ids or p.category_id in category_ids]
    if category_ids and not pool:
        return {
            "total_encontrado": 0,
            "produtos": [],
            "observacoes": [
                "Não há produtos cadastrados nessa categoria no sistema agora. NÃO afirme que a loja "
                "não vende esse tipo de instrumento; diga que não encontrou itens no sistema neste "
                "momento e ofereça confirmar com a equipe."
            ],
        }

    def passes(product: Product) -> bool:
        price, _ = current_price(store, product)
        if price_max is not None and price > money(price_max):
            return False
        if price_min is not None and price < money(price_min):
            return False
        if strings_value and product.specs.get("strings") != strings_value:
            return False
        if electric is not None and product.specs.get("electronics") != ("yes" if electric else "no"):
            return False
        return True

    pool = [p for p in pool if passes(p)]

    approximate = False
    if query:
        q_tokens = tokenize(query)
        q_compact = _compact(query)
        scored: list[tuple[Product, float]] = []
        for p in pool:
            hay = _haystack(p)
            name_tokens = set(tokenize(p.name))
            contiguous = len(q_compact) >= 3 and q_compact in _compact(p.name)  # "gd 20" ~ "GD20"
            if contiguous or (q_tokens and all(t in hay for t in q_tokens)):
                name_cov = (sum(t in name_tokens for t in q_tokens) / len(q_tokens)) if q_tokens else 1.0
                scored.append((p, 1.0 if contiguous else name_cov))
        if not scored and q_tokens:
            approximate = True
            for p in pool:
                hay = _haystack(p)
                hits = sum(
                    max((difflib.SequenceMatcher(None, t, h).ratio() for h in hay), default=0.0) >= 0.8
                    for t in q_tokens
                )
                if hits / len(q_tokens) >= 0.66:
                    scored.append((p, hits / len(q_tokens)))
        scored.sort(key=lambda pair: (-pair[1], current_price(store, pair[0])[0]))
        matched = [p for p, _ in scored]
    else:
        matched = sorted(pool, key=lambda p: current_price(store, p)[0])

    available = [p for p in matched if p.purchasable]
    shown = matched if include_unavailable else available

    result: dict = {
        "total_encontrado": len(shown),
        "mostrando": min(len(shown), limit),
        "correspondencia": "aproximada (nome parecido; confirme com o cliente)" if approximate else "exata",
        "produtos": [product_card(store, p) for p in shown[:limit]],
    }

    if query and not include_unavailable:
        hidden = [p for p in matched if not p.purchasable]
        if hidden:
            result["indisponiveis_encontrados"] = [
                {"id": p.id, "nome": p.name, "preco_tabela_fmt": brl(p.price), **availability(p)}
                for p in hidden[:3]
            ]
    if query and matched and not available:
        alternatives = alternatives_for(store, matched[0])
        result["alternativas_sugeridas"] = {
            "para": matched[0].name,
            "produtos": [product_card(store, p) for p in alternatives],
        }
    if len(shown) > limit:
        notes.append(f"Há mais {len(shown) - limit} resultado(s); mostre poucos e pergunte se o cliente quer ver mais.")
    if not shown and not result.get("alternativas_sugeridas"):
        notes.append("Nenhum produto encontrado com esses critérios. Sugira ajustar a faixa de preço ou o tipo.")
    if notes:
        result["observacoes"] = notes
    return result
