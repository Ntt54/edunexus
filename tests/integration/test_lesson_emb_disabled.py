"""Embeddings désactivés : TutorService/Retriever hors REST.

100 % offline (httpx.MockTransport injecté, aucun démon Ollama). Couvre :
- ``is_embedding_disabled`` True quand config.tutor_embedding_model="disabled" ;
- ``retriever.retrieve()`` → [] SANS appel ``client.embed``
  (transport mocké qui LÈVE sur /api/embed) ;
- ``_ingestion_skip_mode()`` True (RAG off ⇒ skip d'ingestion) ;
- ``set_embedding_model("")`` ne lève pas (sentinelle vide acceptée).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.ollama_tutor.client import OllamaClient
from src.ollama_tutor.config import Config
from src.ollama_tutor.tutor.service import TutorService
from src.ollama_tutor.tutor.store import LibraryStore


def _embed_raises_transport() -> httpx.MockTransport:
    """Transport dont ``/api/embed`` LÈVE — prouve l'absence d'appel embed."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/api/embed"):
            raise AssertionError(
                "client.embed appelé alors que les embeddings sont désactivés"
            )
        return httpx.Response(200, json={"models": []}, request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def svc(tmp_path: Path) -> TutorService:
    config = Config(config_dir=tmp_path)
    config.tutor_embedding_model = "disabled"
    store = LibraryStore(tmp_path)
    client = OllamaClient(transport=_embed_raises_transport())
    return TutorService(store, client, config)


def test_service_is_embedding_disabled(svc: TutorService) -> None:
    assert svc.is_embedding_disabled is True


@pytest.mark.asyncio
async def test_retriever_retrieve_returns_empty_no_embed_call(
    svc: TutorService, tmp_path: Path
) -> None:
    subj = svc.store.create_subject("Informatique")
    results = await svc.retriever.retrieve(subj.id, "une question quelconque", k=3)
    assert results == []


def test_ingestion_skip_mode_true_when_disabled(svc: TutorService) -> None:
    assert svc._ingestion_skip_mode() is True


def test_set_embedding_model_empty_does_not_raise(svc: TutorService) -> None:
    svc.set_embedding_model("")
    assert svc.is_embedding_disabled is True


def test_set_embedding_model_none_canonicalizes(svc: TutorService) -> None:
    svc.set_embedding_model("none")
    assert svc.model == "disabled"
    assert svc.is_embedding_disabled is True