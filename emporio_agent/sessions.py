"""Persistência simples do histórico de conversa (um JSON por sessão).

Guardamos só o texto das trocas (cliente/assistente). Os resultados de ferramentas não são salvos:
são dados "vivos" (estoque, status de pedido) e devem ser reconsultados, não reaproveitados de uma
conversa antiga. Os arquivos ficam em `runtime/` (fora do git) por conterem dados de clientes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .config import RUNTIME_DIR

Turn = dict  # {"role": "user" | "model", "text": str}


class SessionStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or (RUNTIME_DIR / "sessions")

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "default"
        return self.directory / f"{safe}.json"

    def load(self, session_id: str, max_turns: int = 30) -> list[Turn]:
        path = self._path(session_id)
        if not path.exists():
            return []
        try:
            turns = json.loads(path.read_text(encoding="utf-8")).get("turns", [])
        except (json.JSONDecodeError, OSError):
            return []
        turns = [t for t in turns if t.get("role") in {"user", "model"} and t.get("text")][-max_turns:]
        # O modelo exige que o histórico comece por uma fala do usuário.
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        return turns

    def save(self, session_id: str, turns: list[Turn]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "updated_at": datetime.now().isoformat(timespec="seconds"), "turns": turns}
        self._path(session_id).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)
