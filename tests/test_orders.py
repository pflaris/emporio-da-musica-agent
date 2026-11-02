
def test_pedido_sem_verificacao_pede_confirmacao(make_box):
    result = make_box().call("consultar_pedido", {"numero_pedido": 8})
    assert result["encontrado"] is False and "e-mail" in result["mensagem"]


def test_nao_ha_como_descobrir_se_um_pedido_existe(make_box):
    box = make_box()
    sem_dados = box.call("consultar_pedido", {"numero_pedido": 8})
    inexistente_sem_dados = box.call("consultar_pedido", {"numero_pedido": 999})
    assert sem_dados == inexistente_sem_dados  # mesma resposta exista o pedido ou não

    errado = box.call("consultar_pedido", {"numero_pedido": 8, "verificacao": "foo@bar.com"})
    inexistente = box.call("consultar_pedido", {"numero_pedido": 999, "verificacao": "foo@bar.com"})
    assert errado == inexistente


def test_pedido_enviado_com_rastreio_e_alerta_de_atraso(make_box):
    result = make_box().call("consultar_pedido", {"numero_pedido": 8, "verificacao": "ANACAROL.ferreira@coldmail.com"})
    assert result["encontrado"] is True
    assert result["codigo_rastreamento"] == "BRJL5544332BR"
    assert result["previsao_de_entrega"] == "28/02/2026"
    assert "alerta" in result  # em 23/03 a previsão já passou
    assert result["itens"] == [{"produto": "Kala KA-C Concert Mogno", "quantidade": 1}]


def test_verificacao_por_telefone_aceita_formatos(make_box):
    box = make_box()
    for phone in ("(67) 99654-7821", "67996547821", "996547821"):
        assert box.call("consultar_pedido", {"numero_pedido": 8, "verificacao": phone})["encontrado"] is True
    assert box.call("consultar_pedido", {"numero_pedido": 8, "verificacao": "(67) 90000-0000"})["encontrado"] is False


def test_email_compartilhado_exige_telefone(make_box):
    box = make_box()
    # bruno.martins@jmail.com existe nos cadastros 11 e 43: o e-mail sozinho não basta
    por_email = box.call("consultar_pedido", {"numero_pedido": 10, "verificacao": "bruno.martins@jmail.com"})
    assert por_email["encontrado"] is False and "telefone" in por_email["mensagem"]
    por_telefone = box.call("consultar_pedido", {"numero_pedido": 10, "verificacao": "(67) 98543-2109"})
    assert por_telefone["encontrado"] is True


def test_sessao_identificada_so_enxerga_os_proprios_pedidos(make_box, store):
    leticia = store.customer_by_phone("(67) 99789-0123")  # cliente 14, dona do pedido 7
    box = make_box(customer=leticia)
    assert box.call("consultar_pedido", {"numero_pedido": 7})["encontrado"] is True
    outro = box.call("consultar_pedido", {"numero_pedido": 8, "verificacao": "anacarol.ferreira@coldmail.com"})
    assert outro["encontrado"] is False  # nem com os dados da dona: a sessão é de outra pessoa


def test_pedido_cancelado_mostra_motivo(make_box):
    result = make_box().call("consultar_pedido", {"numero_pedido": 18, "verificacao": "gustavo.nunes@inlook.com"})
    assert result["observacao_do_pedido"] == "Item fora de estoque no momento do pedido"
    assert "cancelado" in result["status"]


def test_pedido_confirmado_ainda_nao_tem_rastreio(make_box):
    result = make_box().call("consultar_pedido", {"numero_pedido": 9, "verificacao": "juliana.almeida@hayoo.com.br"})
    assert "codigo_rastreamento" not in result and "não há código" in result["orientacao"]


# ---- devolução / troca ----------------------------------------------------------------

LETICIA = {"numero_pedido": 7, "verificacao": "leticia.rocha@jmail.com"}  # entregue; previsão 17/02/2026


def devolver(make_box, now, tipo, **extra):
    return make_box(now).call("avaliar_devolucao", {**LETICIA, "tipo": tipo, **extra})


def test_arrependimento_dentro_de_7_dias(make_box):
    result = devolver(make_box, "2026-02-20 10:30", "arrependimento")
    assert result["elegivel"] == "sim_dentro_do_prazo"
    assert result["dias_desde_o_recebimento"] == 3 and result["dias_restantes"] == 4
    assert any("10 dias úteis" in c for c in result["condicoes"])
    assert any("Frete de devolução por conta da loja" in c for c in result["condicoes"])
    assert "aviso_sobre_data" in result  # a data real de recebimento não existe nos dados


def test_arrependimento_no_limite_exato_de_7_dias(make_box):
    assert devolver(make_box, "2026-02-24 10:30", "arrependimento")["dentro_do_prazo"] is True
    assert devolver(make_box, "2026-02-25 10:30", "arrependimento")["dentro_do_prazo"] is False


def test_arrependimento_fora_do_prazo_manda_perguntar_a_data_real(make_box):
    result = devolver(make_box, "2026-03-23 10:30", "arrependimento")
    assert result["elegivel"] == "fora_do_prazo_pela_data_de_referencia"
    assert "realmente" in result["proximo_passo"]


def test_defeito_ate_30_dias_da_compra_e_garantia_depois(make_box):
    assert devolver(make_box, "2026-03-05 10:30", "defeito")["elegivel"] == "sim_troca_por_defeito"  # 30 dias
    depois = devolver(make_box, "2026-03-06 10:30", "defeito")  # 31 dias
    assert depois["elegivel"] == "garantia_legal_com_fabricante"
    assert devolver(make_box, "2026-05-18 10:30", "defeito")["elegivel"] == "garantia_legal_com_fabricante"  # 90 dias
    assert devolver(make_box, "2026-05-19 10:30", "defeito")["elegivel"] == "garantia_legal_expirada"


def test_tipo_aceita_linguagem_natural(make_box):
    assert devolver(make_box, "2026-02-20 10:30", "quero desistir da compra")["tipo_de_solicitacao"] == "arrependimento"
    assert devolver(make_box, "2026-02-20 10:30", "veio com defeito")["tipo_de_solicitacao"] == "defeito"
    assert "erro" in devolver(make_box, "2026-02-20 10:30", "xyz")


def test_pedido_ainda_nao_entregue_ou_cancelado(make_box):
    enviado = make_box().call("avaliar_devolucao", {"numero_pedido": 8, "tipo": "arrependimento", "verificacao": "(67) 99654-7821"})
    assert enviado["elegivel"] == "nao_se_aplica_ainda"
    cancelado = make_box().call("avaliar_devolucao", {"numero_pedido": 18, "tipo": "arrependimento", "verificacao": "gustavo.nunes@inlook.com"})
    assert cancelado["elegivel"] is False


def test_devolucao_exige_verificacao(make_box):
    result = make_box().call("avaliar_devolucao", {"numero_pedido": 7, "tipo": "arrependimento"})
    assert result["encontrado"] is False
