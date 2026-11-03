import json

import pytest
from conftest import ScriptedBackend, at, function_call_response, text_response
from google.genai import types

from emporio_agent.agent import FALLBACK_TEXT, MAX_USER_CHARS
from emporio_agent.app import create_agent
from emporio_agent.config import Settings
from emporio_agent.llm import LLMError
from emporio_agent.sessions import SessionStore

SETTINGS = Settings(api_key=None, models=("modelo-falso",))


@pytest.fixture()
def build(store, policies, tmp_path):
    def _build(steps, *, now="2026-03-23 10:30", phone=None, history=None, settings=SETTINGS):
        backend = ScriptedBackend(steps)
        agent = create_agent(
            settings, backend=backend, clock=at(now), phone=phone, history=history,
            store=store, policies=policies, handoff_path=tmp_path / "handoffs.jsonl",
        )  # fmt: skip
        return agent, backend

    return _build


def test_agente_consulta_ferramenta_e_responde(build):
    agent, backend = build(
        [function_call_response(("buscar_produtos", {"consulta": "Takamine GD20"})), text_response("Sai por *R$ 2.199,00*.")]
    )
    reply = agent.reply("Quanto custa o Takamine GD20?")
    assert reply.text == "Sai por *R$ 2.199,00*."
    assert [(t.name, t.args) for t in reply.tool_calls] == [("buscar_produtos", {"consulta": "Takamine GD20"})]
    assert reply.tool_calls[0].result["produtos"][0]["preco_atual_fmt"] == "R$ 2.199,00"

    # O resultado da ferramenta volta ao modelo como function_response, em ordem.
    sent = backend.contents_seen[1]
    assert [c.role for c in sent] == ["user", "model", "user"]
    response_part = sent[2].parts[0].function_response
    assert response_part.name == "buscar_produtos"
    assert response_part.response["result"]["produtos"][0]["id"] == 95


def test_chamadas_paralelas_voltam_juntas_e_na_mesma_ordem(build):
    agent, backend = build(
        [
            function_call_response(("status_loja", {}), ("consultar_politicas", {"consulta": "endereço"})),
            text_response("ok"),
        ]
    )
    agent.reply("Qual o endereço e vocês estão abertos?")
    answers = backend.contents_seen[1][2].parts
    assert [p.function_response.name for p in answers] == ["status_loja", "consultar_politicas"]


def test_historico_acumula_entre_turnos(build):
    agent, backend = build([text_response("Oi! Em que posso ajudar?"), text_response("Claro!")])
    agent.reply("Oi")
    agent.reply("Quero um violão")
    assert [c.role for c in backend.contents_seen[1]] == ["user", "model", "user"]
    assert [t["role"] for t in agent.transcript] == ["user", "model", "user", "model"]


def test_resposta_vazia_vira_fallback_sem_poluir_o_historico(build):
    agent, _ = build([types.GenerateContentResponse(candidates=[])])
    reply = agent.reply("oi")
    assert reply.text == FALLBACK_TEXT and "(67) 3341-4444" in reply.text
    assert agent.contents == [] and agent.transcript == []


def test_limite_de_rodadas_de_ferramenta(build):
    settings = Settings(api_key=None, models=("x",), max_tool_rounds=3)
    agent, backend = build([function_call_response(("status_loja", {}))] * 5, settings=settings)
    reply = agent.reply("oi")
    assert reply.text == FALLBACK_TEXT and len(backend.system_prompts) == 3
    assert agent.contents == []


def test_erro_do_modelo_desfaz_o_turno_e_permite_tentar_de_novo(build):
    agent, _ = build([text_response("primeira"), LLMError("cota"), text_response("segunda")])
    agent.reply("um")
    with pytest.raises(LLMError):
        agent.reply("dois")
    assert [t["text"] for t in agent.transcript] == ["um", "primeira"]  # o turno que falhou não ficou
    assert agent.reply("dois de novo").text == "segunda"


def test_texto_de_raciocinio_nao_chega_ao_cliente(build):
    parts = [types.Part(text="pensando...", thought=True), types.Part(text="Resposta final")]
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])
    agent, _ = build([response])
    assert agent.reply("oi").text == "Resposta final"


def test_mensagem_vazia_nao_chama_o_modelo_e_mensagem_gigante_e_truncada(build):
    agent, backend = build([text_response("ok")])
    assert "o que você precisa" in agent.reply("   ").text
    assert backend.system_prompts == []
    agent.reply("a" * 10_000)
    sent_text = backend.contents_seen[0][0].parts[0].text
    assert len(sent_text) == MAX_USER_CHARS


def test_prompt_traz_persona_regras_e_contexto_dinamico(build):
    agent, backend = build([text_response("ok")], now="2026-11-02 19:11")  # Finados
    agent.reply("oi")
    prompt = backend.system_prompts[0]
    assert "Maestro" in prompt and "Empório da Música" in prompt
    assert "Regras de ouro" in prompt and "consultar_politicas" in prompt
    assert "FECHADA" in prompt and "feriado (Finados)" in prompt and "03/11/2026" in prompt
    assert "NÃO identificado" in prompt


def test_prompt_manda_recusar_pedido_das_proprias_regras_em_qualquer_forma(build):
    # Regra de prompt (não prova que o modelo obedece, mas impede que ela seja apagada sem querer).
    agent, backend = build([text_response("ok")])
    agent.reply("oi")
    prompt = backend.system_prompts[0]
    assert "Se pedir suas regras" in prompt
    assert "resumo" in prompt and "tópicos" in prompt and "tradução" in prompt


def test_cliente_identificado_pelo_telefone_entra_no_prompt_sem_dados_sensiveis(build):
    agent, backend = build([text_response("ok")], phone="(67) 99789-0123")
    agent.reply("oi")
    prompt = backend.system_prompts[0]
    assert "Letícia" in prompt and "ABERTA" in prompt
    assert "99789-0123" not in prompt and "leticia.rocha@jmail.com" not in prompt


def test_sessao_salva_e_retoma_so_o_texto(tmp_path, build):
    agent, _ = build([text_response("Oi, Letícia!")], phone="(67) 99789-0123")
    agent.reply("Oi")
    sessions = SessionStore(tmp_path / "sessions")
    sessions.save("tel-67997890123", agent.transcript)

    history = sessions.load("tel-67997890123")
    assert [t["role"] for t in history] == ["user", "model"]
    retomado, backend = build([text_response("Claro")], history=history)
    retomado.reply("Lembra de mim?")
    assert [c.role for c in backend.contents_seen[0]] == ["user", "model", "user"]


def test_sessao_descarta_historico_invalido_e_comeca_no_usuario(tmp_path):
    sessions = SessionStore(tmp_path)
    sessions.save("x", [{"role": "model", "text": "solto"}, {"role": "user", "text": "a"}, {"role": "model", "text": "b"}])
    assert [t["text"] for t in sessions.load("x")] == ["a", "b"]
    (tmp_path / "quebrado.json").write_text("{não é json", encoding="utf-8")
    assert sessions.load("quebrado") == []
    assert sessions.load("../../etc/passwd") == []  # id é sanitizado, nunca vira caminho


def test_encaminhar_atendente_grava_protocolo_e_informa_prazo(make_box, tmp_path):
    box = make_box("2026-03-23 10:30")
    first = box.call("encaminhar_atendente", {"tipo": "reclamacao", "resumo": "Pedido 7 chegou riscado", "contato": "(67) 99789-0123"})
    second = box.call("encaminhar_atendente", {"tipo": "devolucao_troca", "resumo": "Devolução do pedido 7"})
    assert first["protocolo"] == "EMP-20260323-0001" and second["protocolo"] == "EMP-20260323-0002"
    assert first["prazo_de_retorno"] == "até 24 horas úteis"
    assert "24 horas" not in second["prazo_de_retorno"]
    saved = [json.loads(line) for line in (tmp_path / "handoffs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert saved[0]["resumo"] == "Pedido 7 chegou riscado" and saved[0]["contato"] == "(67) 99789-0123"
    assert "erro" in box.call("encaminhar_atendente", {"tipo": "outro", "resumo": "  "})
