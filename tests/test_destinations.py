"""Testes da base de destinos (integridade do JSON e funções de acesso)."""

import pytest

import destinations


def test_every_country_has_required_data():
    for country in destinations.list_countries():
        info = destinations.get_country(country)
        assert len(info["codigo"]) == 2 and len(info["moeda"]) == 3
        assert "custos_usd" not in info  # custos agora são estimados pelo LLM
        for state in destinations.list_states(country):
            assert destinations.list_cities(country, state), f"{country}/{state} sem cidades"


def test_city_search_name():
    cities = destinations.list_cities("Itália", "Toscana")
    florence = next(city for city in cities if city.display_name == "Florença")
    assert florence.search_name == "Firenze"


def test_lookup_is_case_insensitive_and_quality_accepts_accent():
    assert destinations.country_currency("portugal") == "EUR"
    assert destinations.country_code("Japão") == "JP"
    assert destinations.normalize_quality("Econômico") == "economico"
    with pytest.raises(KeyError):
        destinations.get_country("Atlântida")


def test_is_known_country_allows_typed_countries():
    assert destinations.is_known_country("Brasil")
    assert not destinations.is_known_country("Peru")  # digitado pelo usuário: fora da base, mas permitido na interface


def test_resolve_city_uses_search_name_when_known():
    assert destinations.resolve_city("Itália", "florença").search_name == "Firenze"
    assert destinations.resolve_city("Peru", "Cusco").search_name == "Cusco"
