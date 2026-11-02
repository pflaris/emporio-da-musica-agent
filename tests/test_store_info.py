from datetime import datetime

import pytest

from emporio_agent.config import TZ
from emporio_agent.store_info import shipping_quote, store_status


def status(value: str) -> dict:
    return store_status(datetime.fromisoformat(value).replace(tzinfo=TZ))


def test_aberta_em_dia_util_no_horario():
    s = status("2026-03-23 10:30")  # segunda
    assert s["aberta_agora"] is True and s["horario_de_hoje"] == "09:00 às 18:00"


@pytest.mark.parametrize("quando", ["2026-03-23 08:59", "2026-03-23 18:00", "2026-03-23 18:01"])
def test_fechada_fora_do_expediente(quando):
    assert status(quando)["aberta_agora"] is False


def test_sabado_so_ate_13h_e_proxima_abertura_na_segunda():
    assert status("2026-03-28 12:59")["aberta_agora"] is True
    depois = status("2026-03-28 14:00")
    assert depois["aberta_agora"] is False
    assert depois["proxima_abertura"] == "segunda-feira, 30/03/2026, às 09:00"


def test_domingo_fechado():
    s = status("2026-03-29 11:00")
    assert s["aberta_agora"] is False and s["motivo_se_fechada"] == "domingo"


def test_feriado_fecha_mesmo_em_dia_util():
    s = status("2026-11-02 11:00")  # segunda-feira, Finados
    assert s["aberta_agora"] is False and "feriado" in s["motivo_se_fechada"]
    assert s["proxima_abertura"] == "terça-feira, 03/11/2026, às 09:00"


def test_proxima_abertura_pula_feriado():
    # sexta 20/11/2026 é Consciência Negra: depois do expediente de quinta, só reabre no sábado
    s = status("2026-11-19 19:00")
    assert s["proxima_abertura"] == "sábado, 21/11/2026, às 09:00"


def test_frete_campo_grande_gratis_acima_de_500():
    assert shipping_quote("Campo Grande", 2199)["frete_fmt"] == "grátis"
    assert shipping_quote("campo grande", 500.01)["frete_fmt"] == "grátis"


def test_frete_campo_grande_taxa_fixa_ate_500():
    assert shipping_quote("Campo Grande", 500)["frete_fmt"] == "R$ 35,00"  # "acima de" R$ 500
    assert shipping_quote("Campo Grande", 349.9)["frete_fmt"] == "R$ 35,00"
    assert shipping_quote("Campo Grande", None)["prazo"] == "1 a 3 dias úteis"


def test_frete_outras_cidades_usa_transportadora_sem_inventar_valor():
    result = shipping_quote("Dourados", 1000)
    assert [m["servico"] for m in result["modalidades"]] == ["PAC (Correios)", "SEDEX (Correios)", "Jadlog (.package)"]
    assert "frete_fmt" not in result and "CEP" in result["valor_do_frete"]


def test_frete_sem_cidade_pede_a_cidade():
    assert "erro" in shipping_quote("", 100)


def test_frete_usa_cidade_do_cadastro_quando_sessao_identificada(make_box, store):
    ana = store.customer_by_phone("(67) 99654-7821")  # Campo Grande
    result = make_box(customer=ana).call("calcular_frete", {"valor_pedido": 300})
    assert result["frete_fmt"] == "R$ 35,00" and "observacao_cidade" in result
