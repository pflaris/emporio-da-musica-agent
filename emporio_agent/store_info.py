"""Informações operacionais da loja: aberta agora? e regras de frete por cidade/valor."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache

from .text_utils import brl, money, normalize

DAY_NAMES = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# Seção 2 do manual. Domingo e feriados: fechado.
OPENING_HOURS: dict[int, tuple[time, time] | None] = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(13)),
    6: None,
}

FREE_SHIPPING_ABOVE = Decimal("500.00")
LOCAL_SHIPPING_FEE = Decimal("35.00")


@lru_cache(maxsize=8)
def _holidays(year: int):
    try:
        import holidays

        try:
            return holidays.country_holidays("BR", subdiv="MS", years=year, language="pt_BR")
        except Exception:  # idioma indisponível nesta versão
            return holidays.country_holidays("BR", subdiv="MS", years=year)
    except Exception:  # biblioteca ausente: segue sem calendário de feriados
        return {}


def holiday_name(day: date) -> str | None:
    return _holidays(day.year).get(day)


def _fmt_day(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()]}, {day:%d/%m/%Y}"


def _fmt_hours(hours: tuple[time, time] | None) -> str:
    return "fechado" if hours is None else f"{hours[0]:%H:%M} às {hours[1]:%H:%M}"


def store_status(now: datetime) -> dict:
    today = now.date()
    holiday = holiday_name(today)
    hours = None if holiday else OPENING_HOURS[today.weekday()]
    is_open = bool(hours and hours[0] <= now.time().replace(second=0, microsecond=0) < hours[1])

    if is_open:
        reason = None
    elif holiday:
        reason = f"feriado ({holiday})"
    elif hours is None:
        reason = "domingo"
    elif now.time() < hours[0]:
        reason = "ainda não abriu hoje"
    else:
        reason = "já encerrou o expediente de hoje"

    next_open = None
    for offset in range(0, 15):
        day = today + timedelta(days=offset)
        day_hours = None if holiday_name(day) else OPENING_HOURS[day.weekday()]
        if day_hours and datetime.combine(day, day_hours[0], tzinfo=now.tzinfo) > now:
            next_open = f"{_fmt_day(day)}, às {day_hours[0]:%H:%M}"
            break

    return {
        "agora": f"{_fmt_day(today)}, {now:%H:%M}",
        "aberta_agora": is_open,
        "motivo_se_fechada": reason,
        "horario_de_hoje": _fmt_hours(hours) if not holiday else f"fechado (feriado: {holiday})",
        "proxima_abertura": None if is_open else next_open,
        "horarios_da_loja": {
            "segunda a sexta": "09:00 às 18:00",
            "sábado": "09:00 às 13:00",
            "domingo e feriados": "fechado",
        },
        "avisos": [
            "Considera feriados nacionais e estaduais de MS. Feriados municipais de Campo Grande e "
            "horários estendidos de datas especiais (Black Friday, Natal) não constam no sistema.",
        ],
    }


# --------------------------------------------------------------------------------------
# Frete
# --------------------------------------------------------------------------------------

# Suposição documentada no README: só "Campo Grande" é tratada como região metropolitana,
# porque o manual não lista quais municípios a compõem.
_LOCAL_CITIES = {"campo grande"}

_LARGE_ITEMS_NOTE = (
    "Instrumentos de grande porte (baterias acústicas, pianos digitais, contrabaixos) podem exigir "
    "frete especial, com cotação individual feita pela equipe."
)


def shipping_quote(city: str, order_value: Decimal | float | None) -> dict:
    city_clean = (city or "").strip()
    if not city_clean:
        return {"erro": "Cidade não informada. Pergunte a cidade de entrega ao cliente."}

    if normalize(city_clean) in _LOCAL_CITIES:
        result: dict = {
            "cidade": city_clean,
            "regiao": "Campo Grande (entrega local por motoboy próprio)",
            "prazo": "1 a 3 dias úteis",
            "regra": f"Frete grátis para pedidos acima de {brl(FREE_SHIPPING_ABOVE)}; abaixo disso, taxa fixa de {brl(LOCAL_SHIPPING_FEE)}.",
            "aviso_entrega": "O cliente recebe uma ligação antes da entrega.",
            "observacoes": [_LARGE_ITEMS_NOTE],
        }
        if order_value is not None:
            value = money(order_value)
            free = value > FREE_SHIPPING_ABOVE
            result["valor_do_pedido_fmt"] = brl(value)
            result["frete_fmt"] = "grátis" if free else brl(LOCAL_SHIPPING_FEE)
        return result

    return {
        "cidade": city_clean,
        "regiao": "fora de Campo Grande (envio por transportadora)",
        "modalidades": [
            {"servico": "PAC (Correios)", "prazo": "5 a 12 dias úteis"},
            {"servico": "SEDEX (Correios)", "prazo": "2 a 5 dias úteis"},
            {"servico": "Jadlog (.package)", "prazo": "3 a 8 dias úteis"},
        ],
        "extras": "Todas incluem rastreamento e seguro contra extravio e danos.",
        "valor_do_frete": (
            "Calculado automaticamente pelo CEP de destino, peso e dimensões do produto; não é possível "
            "estimar o valor aqui. Informe que o valor aparece ao informar o CEP no fechamento do pedido."
        ),
        "observacoes": [
            _LARGE_ITEMS_NOTE,
            "Se a cidade for vizinha de Campo Grande e o cliente perguntar sobre entrega local, "
            "diga que a equipe confirma (o sistema só trata Campo Grande como entrega local).",
            "Em caso de avaria, o cliente deve recusar o recebimento e avisar a loja imediatamente.",
        ],
    }
