"""Relatório de qualidade dos dados: o que encontramos de estranho e como o agente lida com isso.

Uso:  python -m emporio_agent.data_quality
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from .data_store import DataStore, load_store
from .text_utils import brl


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str
    handling: str


def build_report(store: DataStore) -> list[Issue]:
    issues: list[Issue] = []

    # 1) Descrições que contradizem o nome do produto
    unreliable = [p for p in store.products.values() if not p.description_reliable]
    if unreliable:
        ids = ", ".join(f"{p.id} ({p.name})" for p in sorted(unreliable, key=lambda p: p.id))
        issues.append(
            Issue(
                "descricao_inconsistente",
                f"{len(unreliable)} produtos com descrição/especificações que não batem com o nome: {ids}",
                "O agente informa só nome, categoria, preço e disponibilidade desses itens e "
                "evita detalhes técnicos (não há como saber se o nome ou a descrição está certo).",
            )
        )

    # 2) Total do pedido x soma dos itens (preços atuais do catálogo)
    mismatches = []
    for order in store.orders.values():
        expected = sum(
            (store.products[i.product_id].price * i.quantity for i in order.items if i.product_id in store.products),
            Decimal("0"),
        )
        if order.items and abs(expected - order.total) > Decimal("0.01"):
            mismatches.append(f"pedido {order.id}: total {brl(order.total)} x itens {brl(expected)}")
    if mismatches:
        issues.append(
            Issue(
                "total_pedido_diverge",
                "; ".join(mismatches),
                "O agente informa o total registrado no pedido (valor efetivamente cobrado); a "
                "diferença pode ser desconto/preço da época e não é explicada ao cliente.",
            )
        )

    # 3) Itens de pedido apontando para produto inexistente
    missing = {i.product_id for o in store.orders.values() for i in o.items if i.product_id not in store.products}
    if missing:
        issues.append(Issue("item_sem_produto", f"product_id inexistente: {sorted(missing)}", "Item aparece como 'produto não identificado'."))

    # 4) E-mails/telefones repetidos entre clientes (afeta a verificação de identidade)
    emails = Counter(c.email for c in store.customers.values())
    phones = Counter(c.phone_digits for c in store.customers.values())
    dup_emails = sorted(e for e, n in emails.items() if n > 1)
    dup_phones = sorted(p for p, n in phones.items() if n > 1)
    if dup_emails or dup_phones:
        issues.append(
            Issue(
                "contato_duplicado",
                f"e-mails repetidos: {dup_emails or '-'}; telefones repetidos: {dup_phones or '-'}",
                "E-mail compartilhado não vale como verificação de identidade: o agente pede o telefone cadastrado.",
            )
        )

    # 5) Categorias sem nenhum produto cadastrado
    empty = [c.name for c in store.categories.values() if not store.products_in_category(c.id)]
    if empty:
        issues.append(
            Issue(
                "categoria_sem_produtos",
                f"Categorias sem produtos no sistema: {', '.join(empty)} (o manual cita 'mais de 300 instrumentos'; o CSV tem {len(store.products)})",
                "O agente diz que não encontrou itens dessa categoria no sistema agora, sem afirmar que a loja não vende, e oferece confirmar com a equipe.",
            )
        )

    # 6) Produtos que não podem ser vendidos agora
    not_sellable = Counter(
        "esgotado" if (p.status == "active" and p.stock == 0) else p.status
        for p in store.products.values()
        if not p.purchasable
    )
    if not_sellable:
        issues.append(
            Issue(
                "produtos_indisponiveis",
                ", ".join(f"{k}: {v}" for k, v in sorted(not_sellable.items())),
                "Nunca confirma disponibilidade sem checar status+estoque; sugere alternativas da mesma categoria/faixa de preço.",
            )
        )

    # 7) Promoções
    active = [p for p in store.promotions if p.is_active]
    stale_ids = [p.id for p in store.promotions if not p.is_active]
    issues.append(
        Issue(
            "promocoes",
            f"{len(active)} ativas de {len(store.promotions)}; {len(stale_ids)} vencidas/inativas.",
            "Só is_active=1 vale. Se o cliente citar promoção vencida, o agente avisa com transparência e mostra o preço atual.",
        )
    )

    # 8) Consistência de rastreio x status
    odd = [
        f"pedido {o.id} ({o.status})"
        for o in store.orders.values()
        if (o.status in {"shipped", "delivered"} and not o.tracking_code)
        or (o.status in {"pending", "confirmed"} and o.tracking_code)
    ]
    if odd:
        issues.append(Issue("rastreio_x_status", ", ".join(odd), "Informa só o que está no sistema."))

    return issues


def main() -> None:
    store = load_store()
    report = build_report(store)
    print(f"Produtos: {len(store.products)} | Pedidos: {len(store.orders)} | Clientes: {len(store.customers)}\n")
    for issue in report:
        print(f"[{issue.code}]\n  achado:   {issue.detail}\n  decisão:  {issue.handling}\n")


if __name__ == "__main__":
    main()
