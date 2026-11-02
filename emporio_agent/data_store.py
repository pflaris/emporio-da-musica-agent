"""Carregamento, limpeza e indexação dos CSVs da loja.

Escolhas:
- `csv` da biblioteca padrão em vez de pandas: são ~190 linhas no total, então um DataFrame não
  traria ganho e deixaria a instalação e a inicialização mais pesadas.
- Valores monetários em `Decimal`, para não propagar erros de ponto flutuante para o cliente.
- Tudo é lido uma vez na inicialização e fica em memória (equivale a uma "consulta ao sistema").
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .config import DATA_DIR
from .text_utils import money, normalize, strip_accents

# --------------------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    description: str


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: Decimal
    category_id: int
    category_name: str
    description: str
    stock: int
    status: str  # active | discontinued | coming_soon
    specs: dict
    created_at: date | None
    # False quando o texto de descrição contradiz o nome do produto (ver `_description_reliable`).
    description_reliable: bool = True

    @property
    def purchasable(self) -> bool:
        return self.status == "active" and self.stock > 0


@dataclass(frozen=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str

    @property
    def phone_digits(self) -> str:
        return re.sub(r"\D", "", self.phone)

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    order_date: date
    status: str  # pending | confirmed | shipped | delivered | cancelled
    total: Decimal
    payment_method: str
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: tuple[OrderItem, ...]


# --------------------------------------------------------------------------------------
# Limpeza
# --------------------------------------------------------------------------------------

# Os produtos 135-144 têm nomes genéricos ("Bass 1X", "Kit 1 Studio", "Synth 1 Pro") e
# descrições que citam outra marca/modelo (ex.: "Music Man Bass 1X" descrito como Fender Jazz Bass).
# Não dá para saber qual dos dois está certo, então o agente só usa nome, preço e estoque deles.
_TEMPLATE_NAME = re.compile(r"\b(Bass \dX|Kit \d Studio|Synth \d Pro)\b")

_KNOWN_BRANDS = (
    "Fender", "Gibson", "Ibanez", "Yamaha", "Pearl", "Tama", "Roland", "Korg", "Nord",
    "Music Man", "PRS", "Martin", "Taylor", "Takamine", "Kala", "Ohana", "Kalani", "Tagima",
    "Giannini", "Rozini", "Shelby", "Crafter",
)  # fmt: skip


def _brands_in(text: str) -> set[str]:
    haystack = normalize(text)
    return {b for b in _KNOWN_BRANDS if normalize(b) in haystack}


def _description_reliable(name: str, description: str) -> bool:
    if _TEMPLATE_NAME.search(name):
        return False
    in_name, in_description = _brands_in(name), _brands_in(description)
    # Descrição que cita marca(s) e nenhuma delas é a do nome => contradição.
    return not (in_name and in_description and in_name.isdisjoint(in_description))


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _optional(value: str | None) -> str | None:
    cleaned = _clean(value)
    return cleaned or None


def _optional_date(value: str | None) -> date | None:
    cleaned = _optional(value)
    return date.fromisoformat(cleaned) if cleaned else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig tolera BOM (comum em CSVs exportados do Excel/Sheets).
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _parse_specs(raw: str | None) -> dict:
    try:
        parsed = json.loads(_clean(raw) or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v).strip() for k, v in parsed.items()} if isinstance(parsed, dict) else {}


# --------------------------------------------------------------------------------------
# Repositório em memória
# --------------------------------------------------------------------------------------


class DataStore:
    def __init__(
        self,
        categories: list[Category],
        products: list[Product],
        promotions: list[Promotion],
        customers: list[Customer],
        orders: list[Order],
    ) -> None:
        self.categories = {c.id: c for c in categories}
        self.products = {p.id: p for p in products}
        self.promotions = promotions
        self.customers = {c.id: c for c in customers}
        self.orders = {o.id: o for o in orders}

        # Se houver mais de uma promoção ativa para o mesmo produto, vale a de maior desconto
        # (o manual diz que promoções não são cumulativas).
        self._active_promotion: dict[int, Promotion] = {}
        for promo in promotions:
            if not promo.is_active:
                continue
            current = self._active_promotion.get(promo.product_id)
            if current is None or promo.discount_percent > current.discount_percent:
                self._active_promotion[promo.product_id] = promo

        self._customers_by_phone: dict[str, Customer] = {}
        for customer in customers:
            self._customers_by_phone.setdefault(customer.phone_digits, customer)

    # ---- carga --------------------------------------------------------------------------

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "DataStore":
        categories = [
            Category(int(r["category_id"]), _clean(r["name"]), _clean(r["description"]))
            for r in _read_csv(data_dir / "categories.csv")
        ]
        category_names = {c.id: c.name for c in categories}

        products = []
        for r in _read_csv(data_dir / "products.csv"):
            name, description = _clean(r["name"]), _clean(r["description"])
            category_id = int(r["category_id"])
            products.append(
                Product(
                    id=int(r["product_id"]),
                    name=name,
                    price=money(r["price_brl"]),
                    category_id=category_id,
                    category_name=category_names.get(category_id, "Sem categoria"),
                    description=description,
                    stock=int(r["stock_quantity"]),
                    status=_clean(r["status"]).lower(),
                    specs=_parse_specs(r.get("specs")),
                    created_at=_optional_date(r.get("created_at")),
                    description_reliable=_description_reliable(name, description),
                )
            )

        promotions = [
            Promotion(
                id=int(r["promotion_id"]),
                product_id=int(r["product_id"]),
                discount_percent=int(r["discount_percent"]),
                name=_clean(r["description"]),
                is_active=_clean(r["is_active"]) == "1",
            )
            for r in _read_csv(data_dir / "promotions.csv")
        ]

        customers = [
            Customer(
                int(r["customer_id"]),
                _clean(r["name"]),
                _clean(r["phone"]),
                _clean(r["email"]).lower(),
                _clean(r["city"]),
            )
            for r in _read_csv(data_dir / "customers.csv")
        ]

        items_by_order: dict[int, list[OrderItem]] = {}
        for r in _read_csv(data_dir / "order_items.csv"):
            items_by_order.setdefault(int(r["order_id"]), []).append(
                OrderItem(int(r["product_id"]), int(r["quantity"]))
            )

        orders = [
            Order(
                id=int(r["order_id"]),
                customer_id=int(r["customer_id"]),
                order_date=date.fromisoformat(_clean(r["order_date"])),
                status=_clean(r["status"]).lower(),
                total=money(r["total_brl"]),
                payment_method=_clean(r["payment_method"]).lower(),
                tracking_code=_optional(r.get("tracking_code")),
                estimated_delivery=_optional_date(r.get("estimated_delivery")),
                notes=_optional(r.get("notes")),
                items=tuple(items_by_order.get(int(r["order_id"]), [])),
            )
            for r in _read_csv(data_dir / "orders.csv")
        ]

        return cls(categories, products, promotions, customers, orders)

    # ---- consultas ----------------------------------------------------------------------

    def active_promotion(self, product_id: int) -> Promotion | None:
        return self._active_promotion.get(product_id)

    def products_in_category(self, category_id: int) -> list[Product]:
        return [p for p in self.products.values() if p.category_id == category_id]

    def customer_by_phone(self, phone: str) -> Customer | None:
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) < 8:
            return None
        if digits in self._customers_by_phone:
            return self._customers_by_phone[digits]
        # Aceita número sem DDD ou com DDI (55): compara pelo final.
        for known, customer in self._customers_by_phone.items():
            if known.endswith(digits) or digits.endswith(known):
                return customer
        return None

    def customers_with_email(self, email: str) -> list[Customer]:
        target = _clean(email).lower()
        return [c for c in self.customers.values() if c.email == target]

    def order(self, order_id: int) -> Order | None:
        return self.orders.get(order_id)


def load_store(data_dir: Path = DATA_DIR) -> DataStore:
    return DataStore.load(data_dir)


__all__ = [
    "Category", "Product", "Promotion", "Customer", "OrderItem", "Order", "DataStore", "load_store",
    "strip_accents",
]  # fmt: skip
