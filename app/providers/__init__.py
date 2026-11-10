from __future__ import annotations

from functools import lru_cache

from ..config import Settings, get_settings
from .embed_hash import HashEmbedder
from .llm_local import LocalExtractiveLLM, OpenAICompatLLM
from .rerank import HeuristicReranker


@lru_cache
def get_embedder(dim: int = 512):
    settings = get_settings()
    if settings.embed_provider == "openai" and settings.openai_api_key and settings.openai_base_url:
        from .embed_openai import OpenAICompatEmbedder

        return OpenAICompatEmbedder(settings.openai_base_url, settings.openai_api_key, settings.openai_model, dim)
    return HashEmbedder(dim)


@lru_cache
def get_llm():
    settings = get_settings()
    if settings.llm_provider in ("openai", "deepseek") and settings.openai_api_key:
        from .llm_deepseek import DeepSeekLLM, ResilientLLM

        primary = DeepSeekLLM(
            settings.openai_base_url,
            settings.openai_api_key,
            model_fast=settings.openai_model or "deepseek-flash",
            model_deep=settings.openai_model_deep or "deepseek-v4-pro",
            timeout=settings.llm_timeout,
            max_tokens_simple=settings.max_tokens_simple,
            max_tokens_complex=settings.max_tokens_complex,
        )
        return ResilientLLM(primary, LocalExtractiveLLM())
    return LocalExtractiveLLM()


@lru_cache
def get_reranker():
    return HeuristicReranker()


def describe_providers(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    embedder = get_embedder(settings.embed_dim)
    llm = get_llm()
    return {
        "embedding": getattr(embedder, "name", "unknown"),
        "llm": getattr(llm, "name", "unknown"),
        "rerank": getattr(get_reranker(), "name", "unknown"),
    }
