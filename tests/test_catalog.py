from decimal import Decimal

import pytest

from emporio_agent.catalog import max_installments


@pytest.mark.parametrize(
    "total, esperado",
    [
        ("49.90", 1),  # nenhuma parcela >= R$ 50
        ("100", 2),
        ("159.90", 3),
        ("199.90", 3),  # 4x = 49,97 < R$ 80
        ("349.90", 4),  # 4x = 87,48 | 5x = 69,98 < R$ 80
        ("599.90", 6),
        ("749", 7),  # 7x = 107 >= R$ 100
        ("1127", 11),  # 12x = 93,92 < R$ 100
        ("2199", 12),
    ],
)
def test_parcelamento_segue_as_regras_3_1(total, esperado):
    assert max_installments(Decimal(total)) == esperado


def test_preco_do_takamine_gd20_com_pix_e_parcelas(make_box):
    result = make_box().call("buscar_produtos", {"consulta": "Takamine GD20"})
    assert result["total_encontrado"] == 1
    p = result["produtos"][0]
    assert p["preco_atual_fmt"] == "R$ 2.199,00"
    assert p["preco_no_pix_fmt"] == "R$ 2.089,05"
    assert p["parcelamento"] == "até 12x de R$ 183,25 sem juros no cartão de crédito"
    assert p["pode_comprar"] is True
    assert "promocao_ativa" not in p  # a promoção de 12% está inativa


def test_nome_com_espaco_ou_erro_de_digitacao_ainda_acha(make_box):
    box = make_box()
    assert box.call("buscar_produtos", {"consulta": "takamine gd 20"})["produtos"][0]["id"] == 95
    typo = box.call("buscar_produtos", {"consulta": "Takamne GD20"})
    assert typo["produtos"][0]["id"] == 95 and "aproximada" in typo["correspondencia"]


def test_violoes_ate_1000_so_traz_violoes_disponiveis_dentro_do_teto(make_box, store):
    result = make_box().call("buscar_produtos", {"categoria": "violões", "preco_max": 1000, "limite": 10})
    assert result["total_encontrado"] == 12
    for card in result["produtos"]:
        product = store.products[card["id"]]
        assert product.category_name == "Violões"
        assert product.purchasable
        value = Decimal(card["preco_atual_fmt"].replace("R$ ", "").replace(".", "").replace(",", "."))
        assert value <= 1000
    ids = {c["id"] for c in result["produtos"]}
    assert 96 not in ids and 113 not in ids  # esgotado e descontinuado ficam de fora


def test_promocao_ativa_mostra_original_percentual_e_nao_acumula_com_pix(make_box):
    card = make_box().call("buscar_produtos", {"consulta": "Ohana CK-20"})["produtos"][0]
    promo = card["promocao_ativa"]
    assert promo["preco_original_fmt"] == "R$ 549,00"
    assert promo["desconto_percent"] == 20
    assert promo["preco_promocional_fmt"] == "R$ 439,20"
    assert "preco_no_pix_fmt" not in card
    assert "NÃO se aplica" in card["desconto_pix"]
    assert card["parcelamento"].startswith("até 5x de R$ 87,84")  # parcelamento sobre o preço promocional


def test_promocao_vencida_nao_vira_desconto(make_box):
    card = make_box().call("buscar_produtos", {"consulta": "Tagima Woodstock"})["produtos"][0]
    assert "promocao_ativa" not in card  # "Black Friday 30%" está inativa
    assert card["preco_atual_fmt"] == "R$ 899,00"


def test_produto_esgotado_vem_com_alternativas_da_mesma_faixa(make_box):
    result = make_box().call("buscar_produtos", {"consulta": "Giannini GF-3D"})
    assert result["produtos"] == []
    assert result["indisponiveis_encontrados"][0]["disponibilidade"].startswith("esgotado")
    alternativas = result["alternativas_sugeridas"]["produtos"]
    assert alternativas and all(a["pode_comprar"] for a in alternativas)
    assert {a["id"] for a in alternativas} <= {86, 91, 107, 87}  # violões de aço de preço próximo


def test_descontinuado_e_em_breve_nao_sao_vendaveis(make_box):
    box = make_box()
    descontinuado = box.call("buscar_produtos", {"consulta": "Shelby SN-7C", "incluir_indisponiveis": True})
    assert descontinuado["produtos"][0]["disponibilidade"].startswith("descontinuado")
    em_breve = box.call("buscar_produtos", {"consulta": "Kala KA-ABP-CTG", "incluir_indisponiveis": True})
    assert em_breve["produtos"][0]["disponibilidade"].startswith("em breve")
    assert em_breve["produtos"][0]["pode_comprar"] is False


def test_categoria_sem_produtos_nao_afirma_que_a_loja_nao_vende(make_box):
    result = make_box().call("buscar_produtos", {"categoria": "flauta"})
    assert result["total_encontrado"] == 0
    assert "NÃO afirme" in result["observacoes"][0]


def test_item_com_descricao_inconsistente_nao_expoe_descricao_nem_specs(make_box):
    card = make_box().call("buscar_produtos", {"consulta": "Music Man Bass 1X"})["produtos"][0]
    assert "descricao" not in card and "especificacoes" not in card
    assert "aviso_dados" in card and card["preco_atual_fmt"] == "R$ 8.732,00"


def test_filtros_de_corda_e_captacao(make_box):
    result = make_box().call(
        "buscar_produtos", {"categoria": "violão", "tipo_cordas": "aço", "eletroacustico": True, "preco_max": 1500}
    )
    assert {c["id"] for c in result["produtos"]} == {107, 103}


def test_sem_filtros_devolve_resumo_do_catalogo(make_box):
    result = make_box().call("buscar_produtos", {})
    assert "catalogo_resumo" in result
    assert {r["categoria"] for r in result["catalogo_resumo"]} >= {"Violões", "Ukuleles"}


def test_argumentos_em_formatos_variados_sao_aceitos(make_box):
    result = make_box().call("buscar_produtos", {"categoria": "ukulele", "preco_max": "R$ 500,00", "limite": 3.0})
    assert result["produtos"] and len(result["produtos"]) <= 3
