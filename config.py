"""
Configuração do LLM.

Lê as variáveis do arquivo .env e devolve o modelo de linguagem escolhido
(Gemini ou OpenAI). Centralizar isso aqui permite trocar de provedor sem
mexer no resto do código.
"""

import os

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

load_dotenv()

DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash",
    "openai": "gpt-4o-mini",
}

# Temperatura baixa: queremos respostas consistentes e que sigam o formato ReAct.
DEFAULT_TEMPERATURE = 0.1


class ConfigError(Exception):
    """Erro de configuração (provedor inválido, chave ausente etc.)."""


def get_memory_window() -> int:
    """Retorna quantas trocas de mensagens a memória deve guardar."""
    raw_value = os.getenv("MEMORY_WINDOW", "10")
    try:
        window = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"MEMORY_WINDOW deve ser um número inteiro, recebido: {raw_value!r}") from exc
    if window < 1:
        raise ConfigError("MEMORY_WINDOW deve ser pelo menos 1.")
    return window


def get_llm(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    """
    Cria o LLM a partir das variáveis de ambiente.

    Args:
        provider: "gemini" ou "openai". Se omitido, usa LLM_PROVIDER do .env.
        model: nome do modelo. Se omitido, usa LLM_MODEL ou o padrão do provedor.

    Raises:
        ConfigError: se o provedor for desconhecido ou a chave de API estiver ausente.
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()
    if provider not in DEFAULT_MODELS:
        raise ConfigError(f"Provedor '{provider}' não suportado. Use 'gemini' ou 'openai'.")

    model = model or os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider]

    if provider == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key or api_key == "sua-chave-aqui":
            raise ConfigError("Defina GOOGLE_API_KEY no arquivo .env (veja .env.example).")
        # Import tardio: só exige o pacote do provedor que está sendo usado.
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, temperature=DEFAULT_TEMPERATURE, google_api_key=api_key)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ConfigError("Defina OPENAI_API_KEY no arquivo .env (veja .env.example).")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, temperature=DEFAULT_TEMPERATURE, api_key=api_key)
