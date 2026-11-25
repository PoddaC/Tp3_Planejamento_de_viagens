"""Função auxiliar para chamadas HTTP com tratamento de erros padronizado."""

import requests

REQUEST_TIMEOUT_SECONDS = 10


class ExternalServiceError(Exception):
    """Falha ao consultar um serviço externo (rede, timeout, resposta inválida)."""


def get_json(url: str, params: dict | None = None) -> dict:
    """
    Faz um GET e devolve o JSON da resposta.

    Raises:
        ExternalServiceError: para qualquer falha de rede, HTTP ou JSON inválido.
    """
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except requests.Timeout as exc:
        raise ExternalServiceError("o serviço demorou demais para responder") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        raise ExternalServiceError(f"o serviço respondeu com erro HTTP {status}") from exc
    except requests.RequestException as exc:
        raise ExternalServiceError("não foi possível conectar ao serviço") from exc
    except ValueError as exc:
        raise ExternalServiceError("o serviço devolveu uma resposta inválida") from exc
