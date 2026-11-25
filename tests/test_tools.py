"""Testes das ferramentas. As APIs externas são simuladas (mock) para os testes rodarem offline."""

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from tools.budget import calculate_trip_budget
from tools.fares import make_fares_tool
from tools.reference_costs import make_reference_costs_tool
from tools.currency import _parse_conversion_input, convert_currency
from tools.http_client import ExternalServiceError
from tools.input_cleaning import clean_tool_input
from tools.weather import _parse_weather_input, get_weather_forecast

# ---------- previsão do tempo ----------

FAKE_GEOCODING = {
    "results": [{"name": "Lisboa", "admin1": "Lisboa", "country": "Portugal", "latitude": 38.7, "longitude": -9.1}]
}
FAKE_FORECAST = {
    "daily": {
        "time": ["2026-12-01", "2026-12-02"],
        "weather_code": [0, 63],
        "temperature_2m_max": [17.2, 15.0],
        "temperature_2m_min": [9.1, 10.3],
        "precipitation_probability_max": [5, 80],
    }
}
FAKE_HISTORICAL = {
    "daily": {
        "time": ["d1", "d2", "d3", "d4"],
        "temperature_2m_max": [30.0, 32.0, 28.0, 30.0],
        "temperature_2m_min": [20.0, 22.0, 18.0, 20.0],
        "precipitation_sum": [0.0, 12.5, 3.0, 0.2],
    }
}


def fake_weather_api(url, params=None):
    if "geocoding" in url:
        return FAKE_GEOCODING
    return FAKE_HISTORICAL if "archive" in url else FAKE_FORECAST


TODAY = date(2026, 11, 24)


def test_parse_weather_input_formats():
    assert _parse_weather_input("Lisboa", TODAY) == ("Lisboa", None, TODAY, date(2026, 11, 30))
    assert _parse_weather_input("Buenos Aires | 3", TODAY)[3] == date(2026, 11, 26)
    assert _parse_weather_input("Paris | 99", TODAY)[3] == date(2026, 12, 9)  # limite de 16 dias
    assert _parse_weather_input("Firenze, IT | 2027-07-10 | 2027-07-15", TODAY) == (
        "Firenze", "IT", date(2027, 7, 10), date(2027, 7, 15)
    )


@pytest.mark.parametrize(
    "text, message",
    [
        ("Lisboa | 2026-01-01 | 2026-01-05", "já passou"),
        ("Lisboa | 2027-01-10 | 2027-01-05", "anterior à data inicial"),
        ("Lisboa | 10/01/2027", "AAAA-MM-DD"),
        ("Lisboa | 2027-01-01 | 2027-03-01", "período máximo"),
    ],
)
def test_parse_weather_input_invalid_dates(text, message):
    with pytest.raises(ValueError, match=message):
        _parse_weather_input(text, TODAY)


def test_weather_forecast_for_near_dates():
    with patch("tools.weather.get_json", side_effect=fake_weather_api) as api:
        result = get_weather_forecast("Lisboa | 2")
    assert result.startswith("PREVISÃO DO TEMPO para Lisboa, Lisboa, Portugal")
    assert "céu limpo" in result and "chuva moderada" in result and "80%" in result
    assert "forecast" in api.call_args.args[0]


def test_weather_uses_historical_climate_for_distant_dates():
    start = date.today() + timedelta(days=60)
    end = start + timedelta(days=3)
    with patch("tools.weather.get_json", side_effect=fake_weather_api) as api:
        result = get_weather_forecast(f"Lisboa, PT | {start} | {end}")
    assert result.startswith("CLIMA TÍPICO")
    assert "Temperatura máxima média: 30.0°C" in result
    assert "Dias com chuva: 2 de 4" in result  # 12.5 mm e 3.0 mm
    archive_params = api.call_args.kwargs.get("params") or api.call_args.args[1]
    assert archive_params["start_date"].startswith(str(start.year - 1))
    geocoding_params = api.call_args_list[0].args[1]
    assert geocoding_params["countryCode"] == "PT"


def test_weather_city_not_found():
    with patch("tools.weather.get_json", return_value={"results": []}):
        assert "não encontrada" in get_weather_forecast("Cidadeinexistente")


def test_weather_network_error_returns_message():
    with patch("tools.weather.get_json", side_effect=ExternalServiceError("timeout")):
        assert get_weather_forecast("Lisboa").startswith("Erro ao consultar")


def test_weather_empty_input():
    assert "informe o nome" in get_weather_forecast("   ")


# ---------- conversor de moedas ----------

FAKE_QUOTES = {"USD-BRL": 5.0, "EUR-BRL": 6.0}


def fake_currency_api(url, params=None):
    pair = url.rsplit("/", 1)[-1]
    if pair not in FAKE_QUOTES:
        raise ExternalServiceError("o serviço respondeu com erro HTTP 404")
    return {pair.replace("-", ""): {"bid": str(FAKE_QUOTES[pair])}}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("100 USD para BRL", (100.0, "USD", "BRL")),
        ("100,50 eur -> brl", (100.5, "EUR", "BRL")),
        ("1500 USD BRL", (1500.0, "USD", "BRL")),
    ],
)
def test_parse_conversion_input(text, expected):
    assert _parse_conversion_input(text) == expected


def test_convert_to_brl():
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        assert "500.00 BRL" in convert_currency("100 USD para BRL")


def test_convert_from_brl():
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        assert "20.00 USD" in convert_currency("100 BRL para USD")


def test_convert_between_foreign_currencies_uses_brl_bridge():
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        # 1 EUR = 6 BRL, 1 USD = 5 BRL -> 1 EUR = 1.2 USD
        assert "120.00 USD" in convert_currency("100 EUR para USD")


def test_convert_unknown_currency():
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        assert "não suportada" in convert_currency("100 XYZ para BRL")


def test_convert_bad_format():
    assert "fora do formato" in convert_currency("cem dólares")


# ---------- calculadora de orçamento ----------


def test_budget_full_calculation():
    payload = {
        "dias": 5,
        "pessoas": 2,
        "hospedagem_diaria": 400,  # 4 noites = 1600
        "alimentacao_diaria_pessoa": 100,  # 2 x 5 x 100 = 1000
        "transporte_total": 3000,
        "passeios_total": 400,
    }
    result = calculate_trip_budget(json.dumps(payload))
    # subtotal 6000 + 10% de reserva = 6600
    assert "Hospedagem (1 quarto(s) x 4 noites): 1,600.00 BRL" in result
    assert "Subtotal: 6,000.00 BRL" in result
    assert "TOTAL: 6,600.00 BRL" in result
    assert "Custo por pessoa: 3,300.00 BRL" in result
    assert "Custo médio por dia (grupo): 1,320.00 BRL" in result


def test_budget_accepts_markdown_wrapped_json():
    result = calculate_trip_budget('```json\n{"dias": 2, "pessoas": 1, "transporte_total": 100}\n```')
    assert "TOTAL: 110.00 BRL" in result


def test_budget_missing_required_field():
    assert "obrigatórios ausentes: pessoas" in calculate_trip_budget('{"dias": 3}')


def test_budget_invalid_json():
    assert "JSON inválido" in calculate_trip_budget("dias=3")


def test_budget_negative_value():
    assert "não pode ser negativo" in calculate_trip_budget('{"dias": 3, "pessoas": 1, "passeios_total": -5}')


def test_budget_with_daily_costs_rooms_and_summary():
    payload = {
        "destino": "Lisboa, Portugal",
        "qualidade": "Conforto",
        "periodo": "10/07 a 13/07",
        "dias": 4,
        "pessoas": 3,  # 3 pessoas -> 2 quartos automaticamente
        "hospedagem_diaria": 100,  # 2 quartos x 3 noites = 600
        "alimentacao_diaria_pessoa": 50,  # 3 x 4 x 50 = 600
        "transporte_diario_pessoa": 10,  # 3 x 4 x 10 = 120
        "passeios_diario_pessoa": 20,  # 3 x 4 x 20 = 240
        "reserva_percentual": 0,
    }
    result = calculate_trip_budget(json.dumps(payload))
    assert "Destino: Lisboa, Portugal" in result and "Padrão: Conforto" in result
    assert "3 pessoa(s) em 2 quarto(s)" in result
    assert "TOTAL: 1,560.00 BRL" in result
    assert "Custo por pessoa por dia: 130.00 BRL" in result


# ---------- estimativas feitas pelo LLM (custos e passagens) ----------


class FakeStructuredLLM:
    """Imita llm.with_structured_output(schema).invoke(prompt), devolvendo dados fixos e guardando os prompts."""

    def __init__(self, data: dict | None = None, error: Exception | None = None):
        self.data, self.error, self.prompts = data, error, []

    def with_structured_output(self, schema):
        fake = self

        class _Runnable:
            def invoke(self, prompt):
                fake.prompts.append(prompt)
                if fake.error:
                    raise fake.error
                return schema(**fake.data)

        return _Runnable()


COST_DATA = {
    "hospedagem_noite_usd": 150,
    "alimentacao_pessoa_dia_usd": 50,
    "transporte_local_pessoa_dia_usd": 20,
    "passeios_pessoa_dia_usd": 30,
    "alta_temporada": True,
    "justificativa": "Julho é verão e alta temporada em Lisboa.",
}
FARE_DATA = {
    "meio_de_transporte": "avião",
    "classe": "econômica",
    "preco_ida_volta_por_pessoa_brl": 4800,
    "alta_temporada": True,
    "justificativa": "Férias de julho encarecem os voos para a Europa.",
}


def test_costs_estimated_by_llm_and_converted_with_real_rate():
    llm = FakeStructuredLLM(COST_DATA)
    tool = make_reference_costs_tool(llm)
    with patch("tools.currency.get_json", side_effect=fake_currency_api):  # 1 USD = 5 BRL
        result = tool.func("Lisboa, Portugal | Conforto | 7 | BRL")
    assert "ALTA temporada" in result
    assert "Hospedagem (quarto duplo, por noite): 750.00 BRL" in result
    assert "Alimentação (por pessoa, por dia): 250.00 BRL" in result
    assert "Justificativa do modelo: Julho é verão" in result
    assert "Lisboa, Portugal" in llm.prompts[0] and "julho" in llm.prompts[0] and "hotel 3-4 estrelas" in llm.prompts[0]


def test_costs_are_cached_for_the_same_question():
    llm = FakeStructuredLLM(COST_DATA)
    tool = make_reference_costs_tool(llm)
    tool.func("Lisboa, Portugal | conforto | 7 | USD")
    tool.func("Lisboa, Portugal | conforto | 7 | USD")
    assert len(llm.prompts) == 1  # mesma pergunta -> mesmos números, sem nova chamada


def test_costs_keep_usd_when_exchange_api_fails():
    tool = make_reference_costs_tool(FakeStructuredLLM(COST_DATA))
    with patch("tools.currency.get_json", side_effect=ExternalServiceError("timeout")):
        result = tool.func("Lisboa, Portugal | luxo | 7 | BRL")
    assert "150.00 USD" in result and "mantidos em USD" in result


def test_costs_reject_absurd_values():
    tool = make_reference_costs_tool(FakeStructuredLLM({**COST_DATA, "hospedagem_noite_usd": -10}))
    assert "fora de uma faixa razoável" in tool.func("Lisboa, Portugal | conforto | 7 | USD")


def test_costs_llm_failure_returns_message():
    tool = make_reference_costs_tool(FakeStructuredLLM(error=RuntimeError("quota exceeded")))
    assert "não foi possível estimar os custos" in tool.func("Lisboa, Portugal | conforto | 7 | USD")


@pytest.mark.parametrize(
    "text, message",
    [
        ("Lisboa, Portugal | premium | 5", "qualidade 'premium' inválida"),
        ("Lisboa, Portugal | conforto | 13", "entre 1 e 12"),
        ("Lisboa, Portugal | conforto", "entrada incompleta"),
    ],
)
def test_costs_invalid_input(text, message):
    assert message in make_reference_costs_tool(FakeStructuredLLM(COST_DATA)).func(text)


def test_fares_estimated_by_llm():
    llm = FakeStructuredLLM(FARE_DATA)
    result = make_fares_tool(llm).func("São Paulo, Brasil | Lisboa, Portugal | 2 | 7 | conforto")
    assert "Meio: avião (econômica), alta temporada" in result
    assert "Por pessoa: 4,800.00 BRL" in result
    assert "Total para 2 pessoa(s): 9,600.00 BRL" in result
    assert "São Paulo, Brasil" in llm.prompts[0] and "Lisboa, Portugal" in llm.prompts[0]


def test_fares_llm_failure_returns_message():
    tool = make_fares_tool(FakeStructuredLLM(error=ConnectionError("sem internet")))
    assert "não foi possível estimar as passagens" in tool.func("São Paulo | Rio de Janeiro | 1 | 5 | economico")


@pytest.mark.parametrize(
    "text, message",
    [
        ("São Paulo | Lisboa", "entrada incompleta"),
        ("São Paulo | Lisboa | dois | 7 | conforto", "números inteiros"),
        ("São Paulo | Lisboa | 2 | 7 | premium", "inválida"),
        ("São Paulo | Lisboa | 0 | 7 | conforto", "pelo menos 1"),
    ],
)
def test_fares_invalid_input(text, message):
    assert message in make_fares_tool(FakeStructuredLLM(FARE_DATA)).func(text)


# ---------- limpeza das entradas ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Lisboa, Portugal | conforto | 7 | BRL Obervation:", "Lisboa, Portugal | conforto | 7 | BRL"),
        ("`100 USD para BRL`", "100 USD para BRL"),
        ("'Lisboa | 5'\nObservation: inventada", "Lisboa | 5"),
        ("Gramado | 3 Thought: agora sei", "Gramado | 3"),
    ],
)
def test_clean_tool_input(raw, expected):
    assert clean_tool_input(raw) == expected


def test_costs_convert_even_with_react_text_leaking_into_input():
    """Caso real do UC4: o Gemini mandou 'BRL Obervation:' e a ferramenta devolvia em USD."""
    tool = make_reference_costs_tool(FakeStructuredLLM(COST_DATA))
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        result = tool.func("Lisboa, Portugal | conforto | 7 | BRL Obervation:")
    assert "em BRL" in result and "750.00 BRL" in result
