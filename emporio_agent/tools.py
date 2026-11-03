"""Ferramentas que o LLM pode chamar (function calling).

Este módulo não depende do SDK do Gemini: `TOOL_SPECS` é JSON Schema puro e `Toolbox.call`
recebe/devolve dicionários. O adaptador para o Gemini fica em `llm.py`.

Princípio: o LLM decide *quando* consultar e *como conversar*; os números, as regras e as
verificações de segurança são feitos aqui, em código determinístico.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import catalog, config, orders, store_info
from .data_store import Customer, DataStore
from .policies import PolicyIndex

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Declarações (o que o modelo enxerga)
# --------------------------------------------------------------------------------------

TOOL_SPECS: list[dict] = [
    {
        "name": "buscar_produtos",
        "description": (
            "Consulta o catálogo em tempo real: preço, promoção ativa, estoque e especificações. "
            "USE SEMPRE que o cliente falar de produtos, preços, disponibilidade, parcelamento de um item "
            "ou pedir recomendação; nunca responda isso de memória. Para um modelo específico, passe o "
            "nome em `consulta` (ex.: 'Takamine GD20'). Para listas, use `categoria` e filtros. Sem nenhum "
            "filtro, devolve só um resumo do catálogo. Para 'mais caro', 'mais barato', 'melhor' ou qualquer "
            "superlativo, use `ordenar_por` com `limite` 1 a 3. `total_encontrado` é o total que bateu com o "
            "filtro, mesmo que só parte seja devolvida. Os valores já vêm formatados (campos *_fmt): copie-os, "
            "não recalcule."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string", "description": "Marca, modelo ou palavras-chave (ex.: 'Takamine GD20', 'ukulele soprano'). Vazio se só filtrar."},
                "categoria": {"type": "string", "description": "Tipo de instrumento: violão, guitarra, baixo, bateria, teclado, ukulele, sopro..."},
                "preco_max": {"type": "number", "description": "Preço máximo em reais que o cliente quer pagar (considera o preço atual, com promoção)."},
                "preco_min": {"type": "number", "description": "Preço mínimo em reais."},
                "tipo_cordas": {"type": "string", "enum": ["nylon", "aco"], "description": "Filtra violões/ukuleles por tipo de corda."},
                "eletroacustico": {"type": "boolean", "description": "true = só com captação/eletrônica; false = só sem."},
                "incluir_indisponiveis": {"type": "boolean", "description": "true para listar também itens esgotados/descontinuados."},
                "ordenar_por": {
                    "type": "string",
                    "enum": ["preco_asc", "preco_desc"],
                    "description": "Ordena pelo preço atual ANTES de cortar no limite. preco_desc = mais caro primeiro; preco_asc = mais barato primeiro. Obrigatório para 'mais caro', 'mais barato' e qualquer superlativo.",
                },
                "limite": {"type": "integer", "description": "Máximo de itens a retornar (1 a 10). Padrão 6."},
            },
        },
    },
    {
        "name": "consultar_pedido",
        "description": (
            "Consulta status, itens, valor, código de rastreamento e previsão de entrega de um pedido. "
            "Só retorna dados de um cliente verificado: se a sessão não identifica o cliente, passe em "
            "`verificacao` o e-mail cadastrado OU o telefone cadastrado (com DDD) que o cliente informou. "
            "Se o cliente deu e-mail E telefone na conversa, passe os DOIS em `verificacao` (precisam ser do mesmo cadastro). Nunca revele dados de pedido sem um resultado positivo desta ferramenta."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero_pedido": {"type": "integer", "description": "Número do pedido informado pelo cliente."},
                "verificacao": {"type": "string", "description": "E-mail ou telefone cadastrado, informado pelo cliente. Vazio se a sessão já identifica o cliente."},
            },
            "required": ["numero_pedido"],
        },
    },
    {
        "name": "listar_pedidos",
        "description": (
            "Lista os pedidos do cliente (número, data, status, valor e itens). Use quando o cliente pedir "
            "'meus pedidos' ou não souber o número do pedido. Se a sessão não identifica o cliente, passe em "
            "`verificacao` o e-mail OU o telefone cadastrado (com DDD). Não peça o número do pedido para isto. Se o cliente deu e-mail E telefone na conversa, passe os DOIS em `verificacao` (precisam ser do mesmo cadastro)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "verificacao": {"type": "string", "description": "E-mail ou telefone cadastrado, informado pelo cliente. Vazio se a sessão já identifica o cliente."},
            },
        },
    },
    {
        "name": "avaliar_devolucao",
        "description": (
            "Aplica as regras de devolução/troca/defeito a um pedido específico (prazos em dias corridos) e "
            "informa se está dentro do prazo, condições, reembolso e próximos passos. Use quando o cliente "
            "quiser devolver, trocar ou reclamar de defeito de um pedido. Antes, peça o número do pedido e o "
            "motivo. Também exige verificação como em `consultar_pedido`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "numero_pedido": {"type": "integer", "description": "Número do pedido."},
                "tipo": {
                    "type": "string",
                    "enum": ["arrependimento", "defeito", "preferencia"],
                    "description": "arrependimento = desistiu da compra; defeito = problema de fabricação; preferencia = trocar por outra cor/modelo.",
                },
                "verificacao": {"type": "string", "description": "E-mail ou telefone cadastrado (vazio se a sessão já identifica o cliente)."},
            },
            "required": ["numero_pedido", "tipo"],
        },
    },
    {
        "name": "calcular_frete",
        "description": (
            "Regra de frete e prazo de entrega para uma cidade e valor de pedido (grátis acima de R$ 500 em "
            "Campo Grande, taxa fixa abaixo; Correios/Jadlog fora). Use quando perguntarem de frete, entrega ou "
            "prazo. Se a cidade não for informada e o cliente estiver identificado, usa a cidade do cadastro."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cidade": {"type": "string", "description": "Cidade de entrega."},
                "valor_pedido": {"type": "number", "description": "Valor da compra em reais (use o preço atual do item), se conhecido."},
            },
        },
    },
    {
        "name": "consultar_politicas",
        "description": (
            "Busca no manual de políticas da loja: pagamento e parcelamento, trocas/devoluções, garantia, "
            "frete e prazos, promoções, atendimento, privacidade, endereço e contato. Use para regras e "
            "informações institucionais. Responda com base nos trechos retornados, com suas palavras, sem "
            "inventar nada que não esteja neles."
        ),
        "parameters": {
            "type": "object",
            "properties": {"consulta": {"type": "string", "description": "A dúvida do cliente em poucas palavras (ex.: 'prazo para devolver', 'formas de pagamento')."}},
            "required": ["consulta"],
        },
    },
    {
        "name": "status_loja",
        "description": (
            "Informa se a loja está aberta agora, o horário de hoje e quando abre de novo (considera feriados "
            "nacionais e de MS). Use para 'está aberto?', 'que horas abre?' e para dizer quando o atendimento "
            "humano volta."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "encaminhar_atendente",
        "description": (
            "Registra o caso para a equipe humana: reclamação, devolução/troca a processar, dúvida que você não "
            "consegue resolver ou pedido do cliente por atendente. Retorna o protocolo e o prazo de retorno. "
            "Chame depois de entender o problema e, se possível, de ter um contato do cliente. Você NÃO "
            "cancela, altera nem reembolsa pedidos: isso é sempre feito pela equipe."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["reclamacao", "devolucao_troca", "duvida_complexa", "outro"]},
                "resumo": {"type": "string", "description": "Resumo claro do caso, com número do pedido se houver."},
                "contato": {"type": "string", "description": "Telefone ou e-mail para retorno, se o cliente informou."},
            },
            "required": ["tipo", "resumo"],
        },
    },
]


# --------------------------------------------------------------------------------------
# Contexto e execução
# --------------------------------------------------------------------------------------


@dataclass
class ToolContext:
    store: DataStore
    policies: PolicyIndex
    # Cliente identificado pela própria sessão (ex.: número de WhatsApp conhecido). None = anônimo.
    customer: Customer | None = None
    clock: Callable[[], datetime] = config.now
    handoff_path: Path = field(default_factory=lambda: config.RUNTIME_DIR / "atendimentos_encaminhados.jsonl")


def _to_int(value) -> int:
    if isinstance(value, bool):
        raise ValueError("número inválido")
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    if not match:
        raise ValueError(f"número inválido: {value!r}")
    return int(match.group())


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    cleaned = re.sub(r"[^\d,.\-]", "", str(value))
    if "," in cleaned:  # 1.299,90 -> 1299.90
        cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned) if cleaned else None


def _to_bool(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "sim", "1", "yes"}


_DENIAL = {
    "precisa_verificacao": (
        "Para sua segurança, preciso confirmar que o pedido é seu. Peça ao cliente o e-mail cadastrado ou o "
        "telefone cadastrado (com DDD) e chame a ferramenta de novo com `verificacao`."
    ),
    "nao_confere": (
        "Não foi possível localizar um pedido com esses dados. Peça para o cliente conferir o número do pedido e "
        "o e-mail/telefone cadastrado. NÃO diga se o pedido existe ou de quem é."
    ),
    "email_compartilhado": (
        "Para sua segurança, preciso também do telefone cadastrado (com DDD). Peça ao cliente e chame a "
        "ferramenta de novo com `verificacao` contendo o e-mail E o telefone informados na conversa. "
        "NÃO explique o motivo além de 'segurança'."
    ),
}


class Toolbox:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self._handlers: dict[str, Callable[..., dict]] = {
            "buscar_produtos": self.buscar_produtos,
            "consultar_pedido": self.consultar_pedido,
            "listar_pedidos": self.listar_pedidos,
            "avaliar_devolucao": self.avaliar_devolucao,
            "calcular_frete": self.calcular_frete,
            "consultar_politicas": self.consultar_politicas,
            "status_loja": self.status_loja,
            "encaminhar_atendente": self.encaminhar_atendente,
        }

    @property
    def specs(self) -> list[dict]:
        return TOOL_SPECS

    def call(self, name: str, args: dict | None) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return {"erro": f"Ferramenta desconhecida: {name}"}
        try:
            return handler(**(args or {}))
        except (TypeError, ValueError) as exc:
            log.warning("Argumentos inválidos em %s: %s", name, exc)
            return {"erro": f"Argumentos inválidos: {exc}. Reformule a chamada."}
        except Exception:  # noqa: BLE001 - qualquer falha vira resposta amigável para o modelo
            log.exception("Falha na ferramenta %s", name)
            return {"erro": "Falha interna ao consultar o sistema. Peça desculpas e ofereça encaminhar para a equipe."}

    # ---- catálogo ----------------------------------------------------------------------

    def buscar_produtos(
        self,
        consulta: str = "",
        categoria: str = "",
        preco_max=None,
        preco_min=None,
        tipo_cordas: str = "",
        eletroacustico=None,
        incluir_indisponiveis=False,
        limite=6,
        ordenar_por: str = "",
    ) -> dict:
        return catalog.search_products(
            self.ctx.store,
            query=consulta or "",
            category=categoria or "",
            price_max=_to_float(preco_max),
            price_min=_to_float(preco_min),
            strings=tipo_cordas or "",
            electric=_to_bool(eletroacustico),
            include_unavailable=bool(_to_bool(incluir_indisponiveis)),
            limit=_to_int(limite) if limite else 6,
            sort_by=ordenar_por or "",
        )

    # ---- pedidos -----------------------------------------------------------------------

    def _authorize(self, numero_pedido, verificacao):
        order, owner, error = orders.authorize_order(
            self.ctx.store, _to_int(numero_pedido), self.ctx.customer, verificacao or ""
        )
        if error:
            return None, None, {"encontrado": False, "mensagem": _DENIAL[error]}
        return order, owner, None

    def consultar_pedido(self, numero_pedido, verificacao: str = "") -> dict:
        order, owner, denial = self._authorize(numero_pedido, verificacao)
        if denial:
            return denial
        return orders.order_card(self.ctx.store, order, owner, self.ctx.clock().date())

    def listar_pedidos(self, verificacao: str = "") -> dict:
        customer, error = orders.resolve_customer(self.ctx.store, self.ctx.customer, verificacao or "")
        if error:
            return {"encontrado": False, "mensagem": _DENIAL[error]}
        return orders.list_orders(self.ctx.store, customer)

    def avaliar_devolucao(self, numero_pedido, tipo: str, verificacao: str = "") -> dict:
        kind = orders.normalize_return_type(tipo)
        if kind is None:
            return {"erro": "Tipo inválido. Use: arrependimento, defeito ou preferencia."}
        order, _owner, denial = self._authorize(numero_pedido, verificacao)
        if denial:
            return denial
        return orders.evaluate_return(order, kind, self.ctx.clock().date())

    # ---- loja --------------------------------------------------------------------------

    def calcular_frete(self, cidade: str = "", valor_pedido=None) -> dict:
        city = (cidade or "").strip()
        note = None
        if not city and self.ctx.customer:
            city = self.ctx.customer.city
            note = "Cidade tirada do cadastro do cliente; confirme se a entrega é nesse endereço."
        result = store_info.shipping_quote(city, _to_float(valor_pedido))
        if note and "erro" not in result:
            result["observacao_cidade"] = note
        return result

    def consultar_politicas(self, consulta: str) -> dict:
        hits = self.ctx.policies.search(consulta or "", top_k=3)
        if not hits:
            return {
                "trechos": [],
                "observacao": "Nada encontrado sobre isso nas políticas. Não invente: diga que vai confirmar com a equipe e ofereça encaminhar.",
            }
        return {
            "trechos": [{"secao": s.label, "texto": s.text} for s, _score in hits],
            "observacao": (
                "Fonte interna da loja: explique com suas palavras como política da loja; não cite 'manual' nem "
                "procedimentos internos (ex.: 'consultar a gerência')."
            ),
        }

    def status_loja(self) -> dict:
        return store_info.store_status(self.ctx.clock())

    # ---- encaminhamento ----------------------------------------------------------------

    def encaminhar_atendente(self, resumo: str, tipo: str = "outro", contato: str = "") -> dict:
        if not (resumo or "").strip():
            return {"erro": "Resumo vazio. Descreva o caso para a equipe."}
        now = self.ctx.clock()
        path = self.ctx.handoff_path
        path.parent.mkdir(parents=True, exist_ok=True)
        sequence = (sum(1 for _ in path.open(encoding="utf-8")) if path.exists() else 0) + 1
        protocol = f"EMP-{now:%Y%m%d}-{sequence:04d}"
        record = {
            "protocolo": protocol,
            "criado_em": now.isoformat(timespec="seconds"),
            "tipo": tipo,
            "resumo": resumo.strip(),
            "contato": (contato or "").strip() or None,
            "cliente_id_da_sessao": self.ctx.customer.id if self.ctx.customer else None,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        status = store_info.store_status(now)
        return {
            "protocolo": protocol,
            "prazo_de_retorno": (
                "até 24 horas úteis" if tipo == "reclamacao" else "a equipe retorna no próximo horário de atendimento"
            ),
            "loja_aberta_agora": status["aberta_agora"],
            "proxima_abertura": status["proxima_abertura"],
            "observacao": "Este protocolo é só do nosso registro de atendimento; não é um número de pedido.",
        }
