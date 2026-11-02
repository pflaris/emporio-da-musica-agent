"""Configuração central: caminhos, variáveis de ambiente e "relógio" do agente."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
# Tudo que é gerado em tempo de execução (sessões, atendimentos encaminhados) fica aqui
# e está no .gitignore, porque contém dados de clientes.
RUNTIME_DIR = ROOT / "runtime"

# A loja fica em Campo Grande/MS (UTC-4). Todos os horários do agente usam este fuso.
TZ = ZoneInfo("America/Campo_Grande")

STORE_NAME = "Empório da Música"
# Telefone/WhatsApp oficial (ver o ajuste documentado em policies.TEXT_FIXES).
STORE_PHONE = "(67) 3341-4444"

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-2.5-flash")

load_dotenv(ROOT / ".env")


def now() -> datetime:
    """Data/hora atual no fuso da loja.

    `EMPORIO_NOW` (ex.: "2026-03-23 10:30") permite simular outro momento. Isso é útil porque
    os CSVs são um retrato fixo de fev/mar de 2026: sem isso, todo pedido "recente" já estaria
    fora do prazo de devolução e os exemplos não seriam reproduzíveis.
    """
    override = os.getenv("EMPORIO_NOW", "").strip()
    if override:
        parsed = datetime.fromisoformat(override)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
    return datetime.now(TZ)


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    models: tuple[str, ...]
    temperature: float = 0.3
    # Limite de rodadas de ferramenta por mensagem do cliente (evita laços infinitos).
    max_tool_rounds: int = 6
    # Quantos turnos de texto são reaproveitados ao retomar uma sessão salva.
    max_history_turns: int = 30
    api_retries: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
        primary = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
        fallbacks_env = os.getenv("GEMINI_FALLBACK_MODELS", "")
        fallbacks = (
            tuple(m.strip() for m in fallbacks_env.split(",") if m.strip())
            if fallbacks_env
            else DEFAULT_FALLBACK_MODELS
        )
        models = (primary,) + tuple(m for m in fallbacks if m != primary)
        return cls(api_key=api_key, models=models)
