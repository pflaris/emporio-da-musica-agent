from datetime import date

from conftest import ScriptedBackend, function_call_response, text_response

from emporio_agent.config import Settings
from emporio_agent.gerar_exemplos import SCENARIOS, render_markdown, run_scenario

SETTINGS = Settings(api_key=None, models=("modelo-falso",))


def test_cenarios_cobrem_o_que_o_desafio_pede():
    assert 3 <= len(SCENARIOS) <= 5
    assert len({s.slug for s in SCENARIOS}) == len(SCENARIOS)
    first_turns = " ".join(t for s in SCENARIOS for t in s.turns)
    for expected in ("violões disponíveis custando até R$1000", "Takamine GD20", "endereço da loja", "me arrependi da minha compra"):
        assert expected in first_turns


def test_renderiza_markdown_com_ferramentas_e_data_simulada(store, policies, tmp_path):
    scenario = next(s for s in SCENARIOS if s.slug.startswith("03"))
    steps = [
        text_response("Oi, Letícia! Qual é o número do pedido?"),
        function_call_response(("avaliar_devolucao", {"numero_pedido": 7, "tipo": "arrependimento"})),
        text_response("Dá sim! Você ainda tem 4 dias."),
        function_call_response(("encaminhar_atendente", {"tipo": "devolucao_troca", "resumo": "Devolução do pedido 7"})),
        text_response("Pronto! Protocolo EMP-20260220-0001."),
    ]
    backend = ScriptedBackend(steps)
    agent, exchanges = run_scenario(
        scenario, settings=SETTINGS, store=store, policies=policies, handoff_dir=tmp_path, backend=backend, pause=0, sleep=lambda s: None
    )
    markdown = render_markdown(scenario, "03", agent, exchanges, date(2026, 11, 2))

    assert markdown.startswith("# Conversa 03 — Arrependimento de compra")
    assert "sexta-feira, 20/02/2026, 10:30" in markdown
    assert "identificado pelo telefone (Letícia)" in markdown
    assert "**Cliente:** É o pedido 7." in markdown
    assert "`avaliar_devolucao`" in markdown and "sim_dentro_do_prazo" in markdown  # resultado real da ferramenta
    assert "Protocolo EMP-20260220-0001" in markdown
