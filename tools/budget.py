"""
Ferramenta 3: calculadora de orçamento de viagem.

Cálculo local (sem API). Recebe os dados da viagem em JSON e devolve um resumo
(destino, padrão, período, pessoas) e o total discriminado por categoria, com
custo médio por dia e por pessoa.
"""

import json
import math

from langchain_core.tools import Tool

REQUIRED_FIELDS = ("dias", "pessoas")

# Campos numéricos opcionais (padrão 0) e o que cada um representa.
COST_FIELDS = {
    "hospedagem_diaria": "valor de UM quarto por noite",
    "alimentacao_diaria_pessoa": "alimentação por pessoa por dia",
    "transporte_diario_pessoa": "transporte local por pessoa por dia",
    "passeios_diario_pessoa": "passeios e ingressos por pessoa por dia",
    "transporte_total": "passagens até o destino (total do grupo)",
    "passeios_total": "passeios já definidos (total do grupo)",
    "outros_total": "outros gastos (total do grupo)",
}
# Campos de texto opcionais, usados só para descrever a viagem no resumo.
TEXT_FIELDS = {"destino": "Destino", "qualidade": "Padrão", "periodo": "Período", "temporada": "Temporada"}

DEFAULT_CONTINGENCY_PERCENT = 10  # reserva para imprevistos
PEOPLE_PER_ROOM = 2  # usado para calcular os quartos quando não informados


def _parse_budget_input(raw_input: str) -> dict:
    """Converte a entrada JSON em dicionário e valida os campos."""
    text = raw_input.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]  # o LLM às vezes escreve ```json ... ```
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido ({exc.msg}). Exemplo: {{\"dias\": 5, \"pessoas\": 2, ...}}") from exc
    if not isinstance(data, dict):
        raise ValueError("a entrada deve ser um objeto JSON")

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"campos obrigatórios ausentes: {', '.join(missing)}")

    parsed: dict = {}
    for field in (*REQUIRED_FIELDS, *COST_FIELDS, "reserva_percentual", "quartos"):
        value = data.get(field, 0)
        try:
            parsed[field] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"o campo '{field}' deve ser numérico, recebido: {value!r}") from exc
        if parsed[field] < 0:
            raise ValueError(f"o campo '{field}' não pode ser negativo")

    if parsed["dias"] < 1 or parsed["pessoas"] < 1:
        raise ValueError("'dias' e 'pessoas' devem ser pelo menos 1")
    if "reserva_percentual" not in data:
        parsed["reserva_percentual"] = DEFAULT_CONTINGENCY_PERCENT
    if parsed["quartos"] < 1:
        parsed["quartos"] = math.ceil(parsed["pessoas"] / PEOPLE_PER_ROOM)

    parsed["moeda"] = str(data.get("moeda", "BRL")).upper()
    for field in TEXT_FIELDS:
        parsed[field] = str(data.get(field, "")).strip()
    return parsed


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def calculate_trip_budget(raw_input: str) -> str:
    """Função executada pela tool. Devolve o orçamento detalhado em texto."""
    try:
        data = _parse_budget_input(raw_input)
    except ValueError as exc:
        return f"Erro: {exc}."

    days, people, rooms, currency = data["dias"], data["pessoas"], data["quartos"], data["moeda"]
    nights = max(days - 1, 1)  # 5 dias de viagem = 4 noites
    person_days = people * days
    items = {
        f"Hospedagem ({rooms:g} quarto(s) x {nights:g} noites)": data["hospedagem_diaria"] * rooms * nights,
        f"Alimentação ({people:g} pessoas x {days:g} dias)": data["alimentacao_diaria_pessoa"] * person_days,
        f"Transporte local ({people:g} pessoas x {days:g} dias)": data["transporte_diario_pessoa"] * person_days,
        f"Passeios ({people:g} pessoas x {days:g} dias)": data["passeios_diario_pessoa"] * person_days,
        "Passagens / transporte até o destino": data["transporte_total"],
        "Passeios já definidos": data["passeios_total"],
        "Outros": data["outros_total"],
    }
    subtotal = sum(items.values())
    contingency = subtotal * data["reserva_percentual"] / 100
    total = subtotal + contingency

    lines = ["RESUMO DA VIAGEM:"]
    lines += [f"- {label}: {data[field]}" for field, label in TEXT_FIELDS.items() if data[field]]
    lines += [f"- Duração: {days:g} dias / {nights:g} noites", f"- Viajantes: {people:g} pessoa(s) em {rooms:g} quarto(s)"]
    lines.append("CUSTOS:")
    lines += [f"- {label}: {_money(value, currency)}" for label, value in items.items() if value > 0]
    lines += [
        f"- Subtotal: {_money(subtotal, currency)}",
        f"- Reserva para imprevistos ({data['reserva_percentual']:g}%): {_money(contingency, currency)}",
        f"TOTAL: {_money(total, currency)}",
        f"Custo médio por dia (grupo): {_money(total / days, currency)}",
        f"Custo por pessoa: {_money(total / people, currency)}",
        f"Custo por pessoa por dia: {_money(total / person_days, currency)}",
    ]
    return "\n".join(lines)


_fields_help = "; ".join(f"'{name}': {desc}" for name, desc in COST_FIELDS.items())
budget_tool = Tool(
    name="calculadora_orcamento",
    func=calculate_trip_budget,
    description=(
        "Calcula o orçamento total de uma viagem: detalhamento por categoria, reserva para imprevistos, "
        "custo médio por dia, por pessoa e por pessoa/dia. Entrada: um objeto JSON em uma única linha. "
        "Obrigatórios: 'dias' e 'pessoas'. Opcionais numéricos (padrão 0): "
        f"{_fields_help}; 'quartos' (padrão: 1 quarto a cada 2 pessoas); 'reserva_percentual' (padrão 10). "
        "Opcionais de texto (aparecem no resumo): 'destino', 'qualidade', 'periodo', 'temporada', 'moeda' (padrão BRL). "
        'Exemplo: {"destino": "Lisboa, Portugal", "qualidade": "Conforto", "periodo": "10/07 a 15/07", '
        '"dias": 6, "pessoas": 2, "hospedagem_diaria": 700, "alimentacao_diaria_pessoa": 260, '
        '"transporte_diario_pessoa": 110, "passeios_diario_pessoa": 190, "transporte_total": 7000, "moeda": "BRL"}'
    ),
)
