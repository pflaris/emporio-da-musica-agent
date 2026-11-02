"""Montagem das peças (dados + políticas + ferramentas + modelo) em um Agent pronto para uso."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import config
from .agent import Agent, Backend
from .config import Settings
from .data_store import DataStore, load_store
from .llm import GeminiBackend
from .policies import PolicyIndex, load_policies
from .tools import ToolContext, Toolbox


def create_agent(
    settings: Settings,
    *,
    phone: str | None = None,
    history: list[dict] | None = None,
    clock: Callable[[], datetime] = config.now,
    handoff_path: Path | None = None,
    backend: Backend | None = None,
    store: DataStore | None = None,
    policies: PolicyIndex | None = None,
) -> Agent:
    """`phone` simula o número de WhatsApp de quem escreve: se estiver no cadastro, o cliente é
    identificado e só enxerga os próprios pedidos."""
    store = store or load_store()
    policies = policies or load_policies()
    customer = store.customer_by_phone(phone) if phone else None

    ctx_kwargs = {"handoff_path": handoff_path} if handoff_path else {}
    toolbox = Toolbox(ToolContext(store, policies, customer=customer, clock=clock, **ctx_kwargs))
    backend = backend or GeminiBackend(settings, toolbox.specs)
    return Agent(toolbox=toolbox, backend=backend, settings=settings, customer=customer, history=history, clock=clock)
