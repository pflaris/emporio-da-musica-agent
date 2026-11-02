from __future__ import annotations

from datetime import datetime

import pytest
from google.genai import types

from emporio_agent.config import TZ
from emporio_agent.data_store import load_store
from emporio_agent.policies import load_policies
from emporio_agent.tools import ToolContext, Toolbox


@pytest.fixture(scope="session")
def store():
    return load_store()


@pytest.fixture(scope="session")
def policies():
    return load_policies()


def at(value: str):
    """Relógio fixo no fuso de Campo Grande: at('2026-03-23 10:30')."""
    moment = datetime.fromisoformat(value).replace(tzinfo=TZ)
    return lambda: moment


@pytest.fixture()
def make_box(store, policies, tmp_path):
    def _make(now: str = "2026-03-23 10:30", customer=None) -> Toolbox:
        return Toolbox(
            ToolContext(store, policies, customer=customer, clock=at(now), handoff_path=tmp_path / "handoffs.jsonl")
        )

    return _make


# ---- helpers para simular o Gemini sem rede -------------------------------------------


def function_call_response(*calls: tuple[str, dict]) -> types.GenerateContentResponse:
    parts = [types.Part.from_function_call(name=name, args=args) for name, args in calls]
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


def text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


class ScriptedBackend:
    """Devolve respostas pré-definidas e guarda o que o agente enviou."""

    model = "modelo-falso"

    def __init__(self, steps):
        self.steps = list(steps)
        self.system_prompts: list[str] = []
        self.contents_seen: list[list[types.Content]] = []

    def generate(self, contents, system_instruction):
        self.system_prompts.append(system_instruction)
        self.contents_seen.append(list(contents))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step
