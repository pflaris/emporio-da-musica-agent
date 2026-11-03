"""Laço do agente: mensagem do cliente -> (modelo <-> ferramentas)* -> resposta final."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from google.genai import types

from . import config, store_info
from .config import Settings
from .data_store import Customer
from .llm import LLMError
from .prompts import build_system_prompt
from .text_utils import polish_reply
from .tools import Toolbox

log = logging.getLogger(__name__)

MAX_USER_CHARS = 2000  # mensagens enormes só gastam cota (e facilitam injeção de prompt)

FALLBACK_TEXT = (
    "Desculpa, tive um probleminha para montar a resposta agora. Pode repetir sua pergunta? "
    f"Se preferir, fale com a equipe pelo {config.STORE_PHONE}."
)


class Backend(Protocol):
    model: str

    def generate(self, contents: list[types.Content], system_instruction: str) -> types.GenerateContentResponse: ...


@dataclass
class ToolTrace:
    name: str
    args: dict
    result: dict


@dataclass
class AgentReply:
    text: str
    tool_calls: list[ToolTrace] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        *,
        toolbox: Toolbox,
        backend: Backend,
        settings: Settings,
        customer: Customer | None = None,
        history: list[dict] | None = None,
        clock: Callable[[], datetime] = config.now,
    ) -> None:
        self.toolbox = toolbox
        self.backend = backend
        self.settings = settings
        self.customer = customer
        self.clock = clock
        self.transcript: list[dict] = list(history or [])
        self.contents: list[types.Content] = [
            types.Content(role=turn["role"], parts=[types.Part(text=turn["text"])]) for turn in self.transcript
        ]

    def reset(self) -> None:
        self.transcript.clear()
        self.contents.clear()

    def _system_prompt(self) -> str:
        now = self.clock()
        return build_system_prompt(now, store_info.store_status(now), self.customer)

    @staticmethod
    def _content_of(response: types.GenerateContentResponse) -> types.Content | None:
        candidates = getattr(response, "candidates", None) or []
        return candidates[0].content if candidates else None

    def reply(self, user_text: str) -> AgentReply:
        user_text = (user_text or "").strip()[:MAX_USER_CHARS]
        if not user_text:
            return AgentReply("Pode me contar o que você precisa? Estou por aqui. 🎶")

        checkpoint = len(self.contents)
        self.contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        traces: list[ToolTrace] = []
        final_text = ""

        try:
            for _ in range(self.settings.max_tool_rounds):
                response = self.backend.generate(self.contents, self._system_prompt())
                content = self._content_of(response)
                if content is None or not content.parts:
                    log.warning("Resposta vazia do modelo (possível bloqueio de segurança).")
                    break
                # Guardamos o conteúdo do modelo exatamente como veio (preserva assinaturas de raciocínio).
                self.contents.append(content)

                calls = [part.function_call for part in content.parts if part.function_call]
                if not calls:
                    final_text = "".join(part.text for part in content.parts if part.text and not part.thought).strip()
                    break

                answers = []
                for call in calls:
                    args = dict(call.args or {})
                    result = self.toolbox.call(call.name, args)
                    traces.append(ToolTrace(call.name, args, result))
                    answers.append(types.Part.from_function_response(name=call.name, response={"result": result}))
                self.contents.append(types.Content(role="user", parts=answers))
            else:
                log.warning("Limite de %d rodadas de ferramenta atingido.", self.settings.max_tool_rounds)
        except LLMError:
            del self.contents[checkpoint:]  # não deixa meia-conversa no histórico
            raise

        if not final_text:
            del self.contents[checkpoint:]
            return AgentReply(FALLBACK_TEXT, traces)

        final_text = polish_reply(final_text)
        self.transcript.append({"role": "user", "text": user_text})
        self.transcript.append({"role": "model", "text": final_text})
        return AgentReply(final_text, traces)
