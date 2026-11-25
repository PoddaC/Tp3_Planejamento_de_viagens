"""
Ferramenta 1: previsão do tempo / clima do período da viagem.

Usa as APIs gratuitas da Open-Meteo (sem chave):
1. Geocoding: converte o nome da cidade em coordenadas.
2. Se a viagem começa dentro de 16 dias -> previsão do tempo (forecast).
3. Se começa depois disso -> não existe previsão confiável, então mostramos o
   clima do MESMO período no ano anterior (API histórica) como referência.
"""

from datetime import date, timedelta

from langchain_core.tools import Tool

from tools.http_client import ExternalServiceError, get_json
from tools.input_cleaning import clean_tool_input

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"

DEFAULT_FORECAST_DAYS = 7
FORECAST_HORIZON_DAYS = 16  # limite da previsão da Open-Meteo
MAX_TRIP_DAYS = 31

# Tradução resumida dos códigos de tempo da OMM usados pela Open-Meteo.
WEATHER_CODE_DESCRIPTIONS = {
    0: "céu limpo", 1: "predominantemente limpo", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina com geada",
    51: "garoa fraca", 53: "garoa moderada", 55: "garoa forte",
    61: "chuva fraca", 63: "chuva moderada", 65: "chuva forte",
    71: "neve fraca", 73: "neve moderada", 75: "neve forte",
    80: "pancadas de chuva fracas", 81: "pancadas de chuva moderadas", 82: "pancadas de chuva fortes",
    95: "trovoadas", 96: "trovoadas com granizo", 99: "trovoadas fortes com granizo",
}
RAINY_DAY_THRESHOLD_MM = 1.0  # dia com pelo menos 1 mm de chuva conta como "dia chuvoso"


def _parse_city(text: str) -> tuple[str, str | None]:
    """'Firenze, IT' -> ('Firenze', 'IT'). O código do país (2 letras) é opcional."""
    if "," in text:
        city, suffix = (part.strip() for part in text.rsplit(",", 1))
        if len(suffix) == 2 and suffix.isalpha():
            return city, suffix.upper()
    return text.strip(), None


def _parse_weather_input(raw_input: str, today: date | None = None) -> tuple[str, str | None, date, date]:
    """
    Interpreta a entrada da ferramenta. Formatos aceitos:
        "Cidade"                                 -> próximos 7 dias
        "Cidade | 5"                             -> próximos 5 dias
        "Cidade, CC | 2026-12-10 | 2026-12-15"   -> período específico (CC = país, opcional)

    Returns:
        (cidade, código do país ou None, data inicial, data final)
    """
    today = today or date.today()
    parts = [part.strip() for part in clean_tool_input(raw_input).split("|")]
    city, country = _parse_city(parts[0])
    if not city:
        raise ValueError("informe o nome de uma cidade")

    if len(parts) == 1:
        return city, country, today, today + timedelta(days=DEFAULT_FORECAST_DAYS - 1)

    if len(parts) == 2 and parts[1].isdigit():
        days = max(1, min(int(parts[1]), FORECAST_HORIZON_DAYS))
        return city, country, today, today + timedelta(days=days - 1)

    try:
        start = date.fromisoformat(parts[1])
        end = date.fromisoformat(parts[2]) if len(parts) > 2 and parts[2] else start
    except ValueError as exc:
        raise ValueError("datas devem estar no formato AAAA-MM-DD") from exc

    if end < start:
        raise ValueError("a data final é anterior à data inicial")
    if start < today:
        raise ValueError("a data inicial já passou; informe datas futuras")
    if (end - start).days + 1 > MAX_TRIP_DAYS:
        raise ValueError(f"o período máximo é de {MAX_TRIP_DAYS} dias")
    return city, country, start, end


def find_city(city: str, country: str | None) -> dict:
    """Busca as coordenadas da cidade (filtrando pelo país, se informado)."""
    params = {"name": city, "count": 1, "language": "pt", "format": "json"}
    if country:
        params["countryCode"] = country
    results = get_json(GEOCODING_URL, params).get("results") or []
    if not results:
        where = f" ({country})" if country else ""
        raise ValueError(f"cidade '{city}'{where} não encontrada")
    return results[0]


def _describe(code: int | None) -> str:
    return WEATHER_CODE_DESCRIPTIONS.get(code, f"código {code}")


def _format_forecast(place: str, daily: dict) -> str:
    """Formata a previsão dia a dia."""
    lines = [f"PREVISÃO DO TEMPO para {place}:"]
    for index, day in enumerate(daily["time"]):
        rain = daily["precipitation_probability_max"][index]
        rain_text = f"{rain}%" if rain is not None else "sem dado"
        lines.append(
            f"- {day}: {_describe(daily['weather_code'][index])}, mín {daily['temperature_2m_min'][index]}°C, "
            f"máx {daily['temperature_2m_max'][index]}°C, chance de chuva {rain_text}"
        )
    return "\n".join(lines)


def _format_historical(place: str, daily: dict, start: date, end: date) -> str:
    """Resume o clima do mesmo período no ano anterior (médias e dias de chuva)."""
    max_temps = [value for value in daily["temperature_2m_max"] if value is not None]
    min_temps = [value for value in daily["temperature_2m_min"] if value is not None]
    rain = [value or 0 for value in daily["precipitation_sum"]]
    if not max_temps or not min_temps:
        raise KeyError("sem dados históricos")
    rainy_days = sum(1 for value in rain if value >= RAINY_DAY_THRESHOLD_MM)
    return (
        f"CLIMA TÍPICO para {place} entre {start:%d/%m} e {end:%d/%m} "
        f"(referência: mesmo período de {start.year - 1}; a viagem está além dos {FORECAST_HORIZON_DAYS} dias "
        "de previsão, então isto NÃO é previsão):\n"
        f"- Temperatura máxima média: {sum(max_temps) / len(max_temps):.1f}°C "
        f"(maior: {max(max_temps):.1f}°C)\n"
        f"- Temperatura mínima média: {sum(min_temps) / len(min_temps):.1f}°C "
        f"(menor: {min(min_temps):.1f}°C)\n"
        f"- Dias com chuva: {rainy_days} de {len(rain)} | chuva acumulada: {sum(rain):.0f} mm"
    )


def _shift_one_year_back(day: date) -> date:
    """Mesmo dia no ano anterior (29/02 vira 28/02)."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def get_weather_forecast(raw_input: str) -> str:
    """
    Função executada pela tool. Sempre devolve texto (inclusive em caso de erro),
    para que o agente possa ler a observação e decidir o próximo passo.
    """
    today = date.today()
    try:
        city, country, start, end = _parse_weather_input(raw_input, today)
        location = find_city(city, country)
        place = ", ".join(p for p in (location.get("name"), location.get("admin1"), location.get("country")) if p)
        coordinates = {"latitude": location["latitude"], "longitude": location["longitude"], "timezone": "auto"}

        horizon_end = today + timedelta(days=FORECAST_HORIZON_DAYS - 1)
        if start <= horizon_end:
            forecast_end = min(end, horizon_end)
            data = get_json(FORECAST_URL, {
                **coordinates,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "start_date": start.isoformat(),
                "end_date": forecast_end.isoformat(),
            })
            result = _format_forecast(place, data["daily"])
            if forecast_end < end:
                result += f"\n(Previsão disponível só até {forecast_end:%d/%m}; os dias seguintes ainda não têm previsão.)"
            return result

        data = get_json(HISTORICAL_URL, {
            **coordinates,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "start_date": _shift_one_year_back(start).isoformat(),
            "end_date": _shift_one_year_back(end).isoformat(),
        })
        return _format_historical(place, data["daily"], start, end)

    except ValueError as exc:
        return f"Erro: {exc}."
    except ExternalServiceError as exc:
        return f"Erro ao consultar o clima: {exc}. Tente novamente mais tarde."
    except (KeyError, TypeError, ZeroDivisionError):
        return "Erro: a API de clima devolveu dados em formato inesperado."


weather_tool = Tool(
    name="previsao_do_tempo",
    func=get_weather_forecast,
    description=(
        "Informa o clima de uma cidade. Se o período começa nos próximos 16 dias, devolve a PREVISÃO dia a dia "
        "(condição, mín/máx, chance de chuva). Se for mais distante, devolve o CLIMA TÍPICO do mesmo período no "
        "ano anterior (médias e dias de chuva). "
        "Entrada: 'Cidade' (próximos 7 dias), 'Cidade | N' (próximos N dias) ou "
        "'Cidade, CC | AAAA-MM-DD | AAAA-MM-DD' (CC = código do país com 2 letras, opcional). "
        "Exemplos: 'Lisboa', 'Gramado, BR | 2027-07-10 | 2027-07-15'."
    ),
)
