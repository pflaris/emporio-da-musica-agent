import pytest


def ids(hits):
    return [section.id for section, _ in hits]


def test_manual_dividido_em_secoes(policies):
    section_ids = {s.id for s in policies.sections}
    assert {"0", "1", "1.2", "2", "3", "3.1", "4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3", "6.2", "7.1", "7.3", "8.1", "9"} <= section_ids


def test_lista_numerada_de_7_2_nao_vira_titulo(policies):
    section = policies.get("7.2")
    assert "1. Saudação" in section.text and "5. Fechamento" in section.text
    assert not any(s.title.startswith(("Saudação", "Entendimento", "Consulta", "Resposta", "Fechamento")) for s in policies.sections)


def test_cabecalho_e_rodape_removidos(policies):
    joined = "\n".join(s.text for s in policies.sections[1:])
    assert "Manual de Políticas e Procedimentos" not in joined
    assert "Página 2" not in joined


def test_contradicoes_do_manual_resolvidas(policies):
    everything = "\n".join(s.text for s in policies.sections)
    assert "3321-4500" not in everything  # número conflitante foi unificado
    assert "(67) 3341-4444" in everything
    assert "empóriodamusica" not in everything  # domínio com acento é inválido
    assert "contato@emporiodamusica.com.br" in everything
    assert "Nota de interpretação" in policies.get("3.1").text
    assert policies.applied_fixes  # os ajustes ficam registrados


@pytest.mark.parametrize(
    "pergunta, esperado",
    [
        ("Me arrependi da minha compra, posso devolver meu pedido?", "4.1"),
        ("Qual o endereço da loja?", "0"),
        ("Vocês abrem sábado? que horas?", "2"),
        ("dá pra parcelar em 12x?", "3.1"),
        ("meu violão chegou com defeito", "4.2"),
        ("como rastreio meu pedido", "5.3"),
        ("quero fazer uma reclamação", "7.3"),
        ("vocês vendem cordas de violão?", "1"),
        ("tem desconto no pix?", "6.2"),
    ],
)
def test_busca_acha_a_secao_certa(policies, pergunta, esperado):
    assert esperado in ids(policies.search(pergunta, top_k=3))


def test_primeiro_resultado_para_devolucao_e_endereco(policies):
    assert ids(policies.search("posso devolver?"))[0] == "4.1"
    assert ids(policies.search("onde fica a loja? endereço"))[0] == "0"


def test_pergunta_fora_do_escopo_nao_retorna_nada(policies):
    assert policies.search("receita de bolo de cenoura") == []
    assert policies.search("") == []
