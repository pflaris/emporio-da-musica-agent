import inspect

import pytest
from conftest import text_response
from google.genai import errors

from emporio_agent.config import Settings
from emporio_agent.llm import GeminiBackend, LLMError, MissingApiKeyError
from emporio_agent.tools import TOOL_SPECS, ToolContext, Toolbox


def make_backend(models=("m1", "m2"), retries=3):
    sleeps: list[float] = []
    backend = GeminiBackend(
        Settings(api_key="chave-falsa", models=models, api_retries=retries), TOOL_SPECS, sleep=sleeps.append
    )
    return backend, sleeps


def stub(backend, behaviours):
    """Troca generate_content por uma sequência de resultados/erros e registra os modelos usados."""
    used: list[str] = []
    queue = list(behaviours)

    def fake(model, contents, config):
        used.append(model)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    backend._client.models.generate_content = fake
    return used


def api_error(code, status="ERRO"):
    return errors.APIError(code, {"error": {"code": code, "message": "falhou", "status": status}})


def test_sem_chave_o_erro_explica_como_resolver():
    with pytest.raises(MissingApiKeyError, match="GEMINI_API_KEY"):
        GeminiBackend(Settings(api_key=None, models=("m",)), TOOL_SPECS)


def test_declaracoes_e_configuracao_enviadas_ao_gemini():
    backend, _ = make_backend()
    declarations = backend._tools[0].function_declarations
    assert [d.name for d in declarations] == [s["name"] for s in TOOL_SPECS]
    config = backend._config("sistema")
    assert config.automatic_function_calling.disable is True  # o laço é nosso
    assert config.system_instruction == "sistema"


def test_repete_em_429_com_espera_crescente_e_depois_funciona():
    backend, sleeps = make_backend()
    ok = text_response("oi")
    used = stub(backend, [api_error(429), api_error(503), ok])
    assert backend.generate([], "s") is ok
    assert used == ["m1", "m1", "m1"] and sleeps == [2.0, 4.0]


def test_modelo_inexistente_passa_para_o_proximo_da_lista():
    backend, _ = make_backend()
    ok = text_response("oi")
    used = stub(backend, [api_error(404, "NOT_FOUND"), ok])
    assert backend.generate([], "s") is ok
    assert used == ["m1", "m2"] and backend.model == "m2"  # e continua nele


def test_cota_esgotada_depois_das_tentativas_tambem_cai_para_o_proximo():
    backend, _ = make_backend(retries=2)
    ok = text_response("oi")
    used = stub(backend, [api_error(429), api_error(429), ok])
    assert backend.generate([], "s") is ok
    assert used == ["m1", "m1", "m2"]


def test_sem_modelo_de_reserva_o_erro_e_amigavel():
    backend, _ = make_backend(models=("so-um",), retries=1)
    stub(backend, [api_error(429)])
    with pytest.raises(LLMError, match="cota gratuita"):
        backend.generate([], "s")


def test_erro_de_requisicao_nao_e_repetido(store, policies):
    backend, sleeps = make_backend()
    used = stub(backend, [api_error(400, "INVALID_ARGUMENT")])
    with pytest.raises(LLMError, match="HTTP 400"):
        backend.generate([], "s")
    assert used == ["m1"] and sleeps == []


# ---- coerência entre o que o modelo enxerga e o que o código aceita --------------------


def test_cada_ferramenta_declarada_tem_handler_com_os_mesmos_parametros(store, policies):
    box = Toolbox(ToolContext(store, policies))
    assert {s["name"] for s in TOOL_SPECS} == set(box._handlers)
    for spec in TOOL_SPECS:
        schema = spec["parameters"]
        assert schema["type"] == "object" and spec["description"].strip()
        properties = set(schema.get("properties", {}))
        assert set(schema.get("required", [])) <= properties
        handler_params = set(inspect.signature(box._handlers[spec["name"]]).parameters)
        assert properties <= handler_params, f"{spec['name']}: parâmetros declarados sem handler"
        for name, prop in schema.get("properties", {}).items():
            assert prop["type"] in {"string", "number", "integer", "boolean"} and prop.get("description", "x"), name
