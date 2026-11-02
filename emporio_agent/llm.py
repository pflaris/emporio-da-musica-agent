"""Adaptador para o Gemini (SDK google-genai): único módulo que cria o cliente e chama a API.

Decisões:
- Function calling *manual* (automatic_function_calling desligado): o laço fica em `agent.py`, o que
  permite registrar cada chamada de ferramenta (auditoria/exemplos), limitar rodadas e tratar erros.
- Schemas das ferramentas em JSON Schema explícito (`parameters_json_schema`), com descrições por
  parâmetro e enums, em vez de inferir a partir de assinaturas Python.
- Repete automaticamente em 429/5xx (cota por minuto do plano gratuito) e, se o modelo principal não
  existir ou estiver sem cota para a chave, passa para o próximo da lista de reserva.
"""

from __future__ import annotations

import logging
import time

from google import genai
from google.genai import errors, types

from .config import Settings

log = logging.getLogger(__name__)

_RETRYABLE = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Falha ao falar com o modelo (já com mensagem amigável para exibir)."""


class MissingApiKeyError(LLMError):
    pass


class GeminiBackend:
    def __init__(self, settings: Settings, tool_specs: list[dict], *, sleep=time.sleep) -> None:
        if not settings.api_key:
            raise MissingApiKeyError(
                "Defina GEMINI_API_KEY (copie .env.example para .env). Chave gratuita: https://aistudio.google.com/apikey"
            )
        self.settings = settings
        self.models = list(settings.models)
        self._sleep = sleep
        # Sem timeout, uma conexão presa deixa a CLI parada para sempre (timeout em ms).
        self._client = genai.Client(api_key=settings.api_key, http_options=types.HttpOptions(timeout=25_000))
        self._tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=spec["name"],
                        description=spec["description"],
                        parameters_json_schema=spec["parameters"],
                    )
                    for spec in tool_specs
                ]
            )
        ]

    @property
    def model(self) -> str:
        return self.models[0]

    def _config(self, system_instruction: str) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=self._tools,
            temperature=self.settings.temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def generate(self, contents: list[types.Content], system_instruction: str) -> types.GenerateContentResponse:
        config = self._config(system_instruction)
        while True:
            model = self.models[0]
            try:
                return self._call_with_retry(model, contents, config)
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                if code in {404, 429, 403, 500, 502, 503, 504} and len(self.models) > 1:
                    log.warning("Modelo %s indisponível (HTTP %s); usando %s.", model, code, self.models[1])
                    self.models.pop(0)
                    continue
                raise LLMError(self._friendly(exc)) from exc

    def _call_with_retry(self, model: str, contents, config):
        attempts = max(self.settings.api_retries, 1)
        for attempt in range(attempts):
            try:
                return self._client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                if code in _RETRYABLE and attempt < attempts - 1:
                    delay = 2.0 * (2**attempt)
                    log.warning("Gemini HTTP %s; nova tentativa em %.0fs (%d/%d).", code, delay, attempt + 1, attempts)
                    self._sleep(delay)
                    continue
                raise

    @staticmethod
    def _friendly(exc: errors.APIError) -> str:
        code = getattr(exc, "code", None)
        if code == 429:
            return "Limite de uso do Gemini atingido (cota gratuita). Aguarde um minuto e tente de novo."
        if code in {401, 403}:
            return "A chave do Gemini foi recusada. Confira GEMINI_API_KEY."
        if code == 404:
            return "O modelo configurado não está disponível para esta chave. Ajuste GEMINI_MODEL."
        return f"Erro ao consultar o Gemini (HTTP {code}): {getattr(exc, 'message', exc)}"
