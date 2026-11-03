"""Interface de linha de comando: conversa com o Maestro no terminal.

    python -m emporio_agent                       # cliente anônimo
    python -m emporio_agent --telefone "(67) 99789-0123"   # simula o WhatsApp de um cliente cadastrado
    python -m emporio_agent --debug               # mostra as ferramentas que o agente chamou
    python -m emporio_agent --check-api           # testa chave e modelo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from google.genai import types

from . import config
from .app import create_agent
from .config import Settings
from .data_store import load_store
from .llm import GeminiBackend, LLMError, MissingApiKeyError
from .policies import load_policies
from .sessions import SessionStore
from .tools import TOOL_SPECS

HELP = "Comandos: /sair  /novo (recomeça a conversa)  /historico"


def _check_api(settings: Settings) -> int:
    try:
        backend = GeminiBackend(settings, TOOL_SPECS)
        response = backend.generate(
            [types.Content(role="user", parts=[types.Part(text="Responda apenas com a palavra: ok")])],
            "Você é um teste de conectividade.",
        )
        text = "".join(p.text or "" for p in response.candidates[0].content.parts if not p.thought).strip()
        print(f"API OK. Modelo em uso: {backend.model}. Resposta: {text!r}")
        return 0
    except LLMError as exc:
        print(f"[erro] {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # terminais Windows costumam não ser UTF-8
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(prog="python -m emporio_agent", description="Maestro, assistente da Empório da Música")
    parser.add_argument("--sessao", help="ID da sessão para salvar/retomar o histórico (padrão: 'cli' ou o telefone)")
    parser.add_argument("--telefone", help="Simula o número de WhatsApp do cliente (identifica-o pelo cadastro)")
    parser.add_argument("--novo", action="store_true", help="Descarta o histórico salvo desta sessão")
    parser.add_argument("--sem-historico", action="store_true", help="Não carrega nem salva histórico")
    parser.add_argument("--debug", action="store_true", help="Mostra chamadas de ferramenta e logs")
    parser.add_argument("--check-api", action="store_true", help="Testa a chave e o modelo e sai")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING, format="[%(levelname)s] %(message)s")
    # "HTTP Request: POST ..." (httpx/httpcore) só aparece com --debug.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.INFO if args.debug else logging.WARNING)
    settings = Settings.from_env()

    if args.check_api:
        return _check_api(settings)

    sessions = SessionStore()
    persist = not args.sem_historico

    store, policies = load_store(), load_policies()
    try:
        agent = create_agent(settings, phone=args.telefone, store=store, policies=policies)  # já identifica o cliente
    except MissingApiKeyError as exc:
        print(f"[erro] {exc}", file=sys.stderr)
        return 2

    if args.telefone and agent.customer is None:
        print("(Telefone não encontrado no cadastro: seguindo como cliente anônimo.)")

    session_id = args.sessao or (f"tel-{agent.customer.phone_digits}" if agent.customer else "cli")
    if persist and args.novo:
        sessions.delete(session_id)
    if persist:
        history = sessions.load(session_id, settings.max_history_turns)
        if history:
            agent = create_agent(settings, phone=args.telefone, history=history, store=store, policies=policies)
            print(f"(Retomando a conversa anterior: {len(history)} mensagens. Use /novo para recomeçar.)")

    print(f"=== {config.STORE_NAME} — atendimento ===")
    if agent.customer:
        print(f"(Sessão identificada como {agent.customer.first_name})")
    print(HELP + "\n")

    while True:
        try:
            text = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"/sair", "/exit", "/quit"}:
            break
        if text.lower() == "/novo":
            agent.reset()
            sessions.delete(session_id)
            print("Conversa reiniciada.\n")
            continue
        if text.lower() == "/historico":
            for turn in agent.transcript:
                who = "Você" if turn["role"] == "user" else "Maestro"
                print(f"{who}: {turn['text']}")
            print()
            continue

        try:
            reply = agent.reply(text)
        except LLMError as exc:
            print(f"\n[erro] {exc}\n")
            continue

        if args.debug:
            for call in reply.tool_calls:
                print(f"   ↳ {call.name}({json.dumps(call.args, ensure_ascii=False)})")
        print(f"\nMaestro: {reply.text}\n")
        if persist:
            sessions.save(session_id, agent.transcript)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
