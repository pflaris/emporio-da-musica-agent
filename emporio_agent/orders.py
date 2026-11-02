"""Pedidos: verificação de identidade, consulta de status e regras de troca/devolução.

Privacidade (o manual cita a LGPD): dados de pedido só saem se o solicitante for verificado.
- Sessão identificada (ex.: número do WhatsApp conhecido): só libera pedidos desse cliente.
- Sessão anônima: exige e-mail cadastrado OU telefone cadastrado (9 dígitos finais).
- Pedido inexistente e dados que não conferem dão a MESMA resposta (não dá para "descobrir"
  quais números de pedido existem).
"""

from __future__ import annotations

import re
from datetime import date

from .data_store import Customer, DataStore, Order
from .text_utils import brl

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

STATUS_PT = {
    "pending": "pendente (aguardando confirmação)",
    "confirmed": "confirmado (em preparação para envio)",
    "shipped": "enviado (a caminho do cliente)",
    "delivered": "entregue",
    "cancelled": "cancelado",
}


def fmt_date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def payment_label(code: str) -> str:
    match = re.fullmatch(r"credit_(\d+)x", code)
    if match:
        return f"cartão de crédito em {int(match.group(1))}x"
    return {"pix": "PIX", "boleto": "boleto bancário", "debit": "cartão de débito"}.get(code, code)


# --------------------------------------------------------------------------------------
# Verificação
# --------------------------------------------------------------------------------------


def verify_customer(store: DataStore, customer: Customer, verification: str) -> tuple[bool, str]:
    text = (verification or "").strip().lower()
    emails = _EMAIL_RE.findall(text)
    for email in emails:
        if email == customer.email:
            # E-mail repetido em mais de um cadastro não identifica ninguém sozinho.
            if len(store.customers_with_email(email)) > 1:
                return False, "email_compartilhado"
            return True, "email"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 9 and digits[-9:] == customer.phone_digits[-9:]:
        return True, "telefone"
    return False, "nao_confere"


def authorize_order(
    store: DataStore,
    order_id: int,
    session_customer: Customer | None,
    verification: str,
) -> tuple[Order | None, Customer | None, str | None]:
    """Devolve (pedido, dono, erro). `erro` é um código para a camada de ferramentas."""
    order = store.order(order_id)
    owner = store.customers.get(order.customer_id) if order else None

    if session_customer is not None:
        if order is not None and order.customer_id == session_customer.id:
            return order, owner, None
        return None, None, "nao_confere"

    if not (verification or "").strip():
        return None, None, "precisa_verificacao"  # igual exista o pedido ou não
    if order is None or owner is None:
        return None, None, "nao_confere"
    ok, reason = verify_customer(store, owner, verification)
    if not ok:
        return None, None, reason
    return order, owner, None


# --------------------------------------------------------------------------------------
# Status do pedido
# --------------------------------------------------------------------------------------

_GUIDANCE = {
    "pending": "Pedido ainda pendente (aguardando confirmação). Ainda não existe código de rastreamento.",
    "confirmed": (
        "Pedido confirmado e em preparação. O código de rastreamento é enviado por e-mail e WhatsApp "
        "assim que o pedido for despachado; por enquanto não há código."
    ),
    "shipped": "Pedido a caminho. O cliente pode acompanhar pelo código no site dos Correios ou da Jadlog.",
    "delivered": "Pedido entregue. O sistema guarda a previsão de entrega, mas não a data efetiva de recebimento.",
    "cancelled": "Pedido cancelado. Explique o motivo registrado, se houver, e ofereça ajuda para refazer a compra.",
}


def order_card(store: DataStore, order: Order, owner: Customer | None, today: date) -> dict:
    items = []
    for item in order.items:
        product = store.products.get(item.product_id)
        items.append({"produto": product.name if product else "produto não identificado", "quantidade": item.quantity})

    card: dict = {
        "encontrado": True,
        "numero_pedido": order.id,
        "cliente_primeiro_nome": owner.first_name if owner else None,
        "status": STATUS_PT.get(order.status, order.status),
        "data_do_pedido": fmt_date(order.order_date),
        "valor_total_fmt": brl(order.total),
        "forma_de_pagamento": payment_label(order.payment_method),
        "itens": items,
        "orientacao": _GUIDANCE.get(order.status, ""),
    }
    if order.tracking_code:
        card["codigo_rastreamento"] = order.tracking_code
    if order.estimated_delivery:
        card["previsao_de_entrega"] = fmt_date(order.estimated_delivery)
        if order.status == "shipped" and order.estimated_delivery < today:
            card["alerta"] = (
                "A previsão de entrega já passou. Peça desculpas, sugira conferir o rastreio e "
                "ofereça encaminhar para a equipe verificar com a transportadora."
            )
    if order.notes:
        card["observacao_do_pedido"] = order.notes
    return card


# --------------------------------------------------------------------------------------
# Troca / devolução
# --------------------------------------------------------------------------------------

RETURN_TYPES = {
    "arrependimento": ("arrepend", "desist", "devolv", "devoluc"),
    "defeito": ("defeit", "quebr", "problema", "estrag", "garantia"),
    "preferencia": ("prefer", "troca", "cor", "modelo", "tamanho"),
}


def normalize_return_type(value: str) -> str | None:
    text = (value or "").lower()
    for kind, stems in RETURN_TYPES.items():
        if kind == text or any(stem in text for stem in stems):
            return kind
    return None


_NOT_COVERED = (
    "Não são cobertos: mau uso, quedas, umidade excessiva, modificações não autorizadas, desgaste "
    "natural (trastes, cordas, feltros, palhetas) e danos apenas estéticos."
)
_NOT_ELIGIBLE_ITEMS = (
    "Itens não elegíveis a troca: instrumentos personalizados/ajustes sob encomenda, produtos de "
    "liquidação com aviso de 'venda final' e boquilhas de sopro (higiene)."
)


def evaluate_return(order: Order, kind: str, today: date) -> dict:
    base: dict = {
        "numero_pedido": order.id,
        "status_do_pedido": STATUS_PT.get(order.status, order.status),
        "tipo_de_solicitacao": kind,
        "decisao_final": "A análise final é da equipe, após conferir o produto.",
    }

    if order.status == "cancelled":
        return {**base, "elegivel": False, "explicacao": "Este pedido já foi cancelado; não há o que devolver."}
    if order.status in {"pending", "confirmed"}:
        return {
            **base,
            "elegivel": "nao_se_aplica_ainda",
            "explicacao": (
                "O pedido ainda não foi enviado/entregue. Os prazos de devolução e troca contam a partir do "
                "recebimento. Para cancelar antes do envio, o manual não detalha o procedimento: encaminhe para a equipe."
            ),
        }
    if order.status == "shipped":
        return {
            **base,
            "elegivel": "nao_se_aplica_ainda",
            "explicacao": (
                "O pedido ainda está a caminho. Ao receber, o cliente terá 7 dias corridos para pedir devolução "
                "por arrependimento. Se o produto chegar avariado, deve recusar o recebimento e falar com a loja imediatamente."
            ),
        }

    # delivered: a data real de recebimento não existe nos dados; usamos a previsão de entrega.
    received = order.estimated_delivery or order.order_date
    days_received = max((today - received).days, 0)
    days_purchase = max((today - order.order_date).days, 0)
    base.update(
        {
            "data_de_referencia_do_recebimento": fmt_date(received),
            "dias_desde_o_recebimento": days_received,
            "aviso_sobre_data": (
                "A data efetiva de recebimento não está registrada; usando a previsão de entrega como referência. "
                "Se o cliente recebeu depois dela, o prazo conta da data real: pergunte quando recebeu."
            ),
        }
    )

    if kind in {"arrependimento", "preferencia"}:
        within = days_received <= 7
        extra = (
            {
                "regra": "Direito de arrependimento (compra online): até 7 dias corridos após o recebimento, sem precisar justificar.",
                "condicoes": [
                    "Produto na embalagem original, sem sinais de uso, com todos os acessórios e manuais.",
                    "Reembolso na mesma forma de pagamento original, em até 10 dias úteis.",
                    "Frete de devolução por conta da loja.",
                ],
            }
            if kind == "arrependimento"
            else {
                "regra": "Troca por preferência (cor, modelo, tamanho): até 7 dias, mediante disponibilidade do produto desejado.",
                "condicoes": [
                    "Produto em perfeito estado e na embalagem original.",
                    "Diferença de valor cobrada ou reembolsada conforme o caso.",
                ],
            }
        )
        return {
            **base,
            **extra,
            "prazo_em_dias": 7,
            "dentro_do_prazo": within,
            "dias_restantes": max(7 - days_received, 0) if within else 0,
            "elegivel": "sim_dentro_do_prazo" if within else "fora_do_prazo_pela_data_de_referencia",
            "proximo_passo": (
                "Oriente a separar o produto com tudo na embalagem original e ofereça encaminhar o pedido para a equipe iniciar o processo."
                if within
                else "Explique que, pela data de referência, o prazo de 7 dias já passou. Pergunte quando o cliente realmente "
                "recebeu; se foi dentro de 7 dias, encaminhe à equipe. Se for defeito, aplique a regra de defeito."
            ),
            "itens_nao_elegiveis": _NOT_ELIGIBLE_ITEMS,
        }

    # defeito
    if days_purchase <= 30:
        rule, status = "Troca por defeito de fabricação: até 30 dias corridos após a compra.", "sim_troca_por_defeito"
        step = "Encaminhe para a equipe agendar a troca; peça fotos/descrição do defeito."
    elif days_received <= 90:
        rule, status = (
            "Passou de 30 dias, mas está dentro da garantia legal de 90 dias (a partir do recebimento): o cliente "
            "deve acionar a garantia diretamente com o fabricante; a loja pode intermediar se o cliente pedir.",
            "garantia_legal_com_fabricante",
        )
        step = "Explique o caminho da garantia e ofereça intermediar com o fabricante via equipe."
    else:
        rule, status = (
            "Garantia legal de 90 dias expirada. Pode haver garantia do próprio fabricante (6 meses a 2 anos, "
            "conforme o certificado que acompanha o produto).",
            "garantia_legal_expirada",
        )
        step = "Sugira conferir o certificado de garantia do fabricante; a loja pode intermediar se solicitado."
    return {
        **base,
        "regra": rule,
        "dias_desde_a_compra": days_purchase,
        "elegivel": status,
        "nao_coberto": _NOT_COVERED,
        "proximo_passo": step,
    }
