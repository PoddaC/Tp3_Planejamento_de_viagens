"""
Acesso à base de destinos (data/destinos.json).

Usada pelos formulários da interface (País -> Estado -> Cidade -> moeda e
código do país). Centralizar a leitura aqui evita que cada parte do código
interprete o JSON de um jeito.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "destinos.json"

# Níveis de qualidade aceitos: chave interna -> rótulo exibido.
QUALITY_LEVELS = {"economico": "Econômico", "conforto": "Conforto", "luxo": "Luxo"}


@dataclass(frozen=True)
class City:
    """Cidade exibida ao usuário e o nome usado para buscar o clima na API."""

    display_name: str
    search_name: str


@lru_cache(maxsize=1)
def _load_data() -> dict:
    """Lê o JSON uma única vez (cache)."""
    with DATA_FILE.open(encoding="utf-8") as file:
        return json.load(file)["paises"]


def list_countries() -> list[str]:
    """Países disponíveis, em ordem alfabética."""
    return sorted(_load_data(), key=str.casefold)


def get_country(country: str) -> dict:
    """Dados de um país (ignora maiúsculas/minúsculas e espaços nas pontas)."""
    data = _load_data()
    for name, info in data.items():
        if name.casefold() == country.strip().casefold():
            return info
    raise KeyError(f"país '{country}' não está na base. Disponíveis: {', '.join(list_countries())}")


def is_known_country(country: str) -> bool:
    """True se o país está na base (o usuário também pode digitar países que não estão)."""
    try:
        get_country(country)
        return True
    except KeyError:
        return False


def list_states(country: str) -> list[str]:
    """Estados/regiões de um país."""
    return sorted(get_country(country)["estados"], key=str.casefold)


def list_cities(country: str, state: str) -> list[City]:
    """Cidades de um estado. Cada item do JSON pode ser texto ou {nome, busca}."""
    cities = []
    for item in get_country(country)["estados"][state]:
        if isinstance(item, str):
            cities.append(City(display_name=item, search_name=item))
        else:
            cities.append(City(display_name=item["nome"], search_name=item.get("busca", item["nome"])))
    return cities


def resolve_city(country: str, city_name: str) -> City:
    """
    Converte o nome digitado em City. Se a cidade estiver na base, usa o nome de
    busca cadastrado (ex.: 'Florença' -> 'Firenze'); senão, usa o próprio texto.
    """
    if is_known_country(country):
        for state in list_states(country):
            for city in list_cities(country, state):
                if city.display_name.casefold() == city_name.strip().casefold():
                    return city
    return City(display_name=city_name.strip(), search_name=city_name.strip())


def country_currency(country: str) -> str:
    """Código da moeda local (ex.: 'EUR')."""
    return get_country(country)["moeda"]


def country_code(country: str) -> str:
    """Código ISO de 2 letras (ex.: 'PT'), usado para filtrar a busca de cidades."""
    return get_country(country)["codigo"]


def normalize_quality(quality: str) -> str:
    """Converte 'Econômico', 'economico', 'ECONOMICO'... para a chave interna."""
    text = quality.strip().casefold().replace("ô", "o")
    for key in QUALITY_LEVELS:
        if text == key:
            return key
    raise KeyError(f"qualidade '{quality}' inválida. Use: {', '.join(QUALITY_LEVELS)}")
