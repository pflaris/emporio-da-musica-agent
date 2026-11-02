from decimal import Decimal

from emporio_agent.data_quality import build_report
from emporio_agent.text_utils import apply_discount, brl, tokenize


def test_volumes_carregados(store):
    assert len(store.products) == 65
    assert len(store.orders) == 20
    assert len(store.customers) == 50
    assert len(store.categories) == 9


def test_itens_de_pedido_agrupados(store):
    assert [i.product_id for i in store.order(15).items] == [82, 106]
    assert store.order(3).items[0].quantity == 2


def test_so_promocoes_ativas_valem(store):
    assert {p.id for p in store.promotions if p.is_active} == {12, 14, 19, 24}
    assert store.active_promotion(121).discount_percent == 20  # Ohana CK-20
    assert store.active_promotion(95) is None  # Takamine GD20: promoção 4 está inativa
    assert store.active_promotion(91) is None  # "Black Friday 30%" venceu


def test_descricao_inconsistente_marcada(store):
    unreliable = {p.id for p in store.products.values() if not p.description_reliable}
    assert unreliable == set(range(135, 145))
    assert store.products[95].description_reliable
    assert store.products[93].description_reliable  # Martin D-28 cita Martin: coerente


def test_status_e_estoque(store):
    assert store.products[96].stock == 0 and not store.products[96].purchasable
    assert store.products[113].status == "discontinued"
    assert store.products[130].status == "coming_soon" and not store.products[130].purchasable


def test_cliente_por_telefone_com_formatos_diferentes(store):
    assert store.customer_by_phone("(67) 99789-0123").id == 14
    assert store.customer_by_phone("67997890123").id == 14
    assert store.customer_by_phone("+55 67 99789-0123").id == 14
    assert store.customer_by_phone("123") is None


def test_relatorio_de_qualidade_acha_os_problemas_conhecidos(store):
    codes = {issue.code for issue in build_report(store)}
    assert {"descricao_inconsistente", "total_pedido_diverge", "contato_duplicado", "categoria_sem_produtos"} <= codes


def test_formatacao_de_dinheiro():
    assert brl(1299) == "R$ 1.299,00"
    assert brl(Decimal("159.9")) == "R$ 159,90"
    assert brl(1234567.5) == "R$ 1.234.567,50"
    assert apply_discount(2199, 5) == Decimal("2089.05")  # arredondamento comercial


def test_tokenizacao_unifica_plural_e_acento():
    assert tokenize("Violões") == tokenize("violão")
    assert "ate" not in tokenize("até mil")  # stopword
