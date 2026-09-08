# Contract — Registre de providers (P0-B)

## `tutor/providers/registry.py`

```python
class LLMClient(ABC):
    name: str
    @property
    def is_healthy(self) -> bool: ...
    async def chat(self, messages: list[Message], **opts) -> dict: ...
    async def ping(self) -> bool: ...

class ProviderRegistry:
    def register(self, name: str, client: LLMClient, primary: bool = False) -> None
    def register_variant(self, hint: str, client: LLMClient) -> None  # large|small|fast
    def get(self, name: str | None = None) -> LLMClient  # lève LLMConfigurationError si vide
    async def ping_all(self) -> dict[str, bool]
```

Adaptateurs fins (un par backend) : `OllamaAdapter(client.py existant)`,
`OpenAICompatAdapter`, `GGUFAdapter` (via `LlamaServerManager`), `MockLLMClient`
(réponses scriptées, usage tests uniquement).

## Embedding

```python
class EmbeddingProvider(ABC):
    dimension: int
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

`NoOpEmbeddingProvider` (skip), `FallbackEmbeddingProvider([(name, p), …])`,
fabrique `get_embedding_provider(mode: auto|eager|skip)`.
`dimension` auto-détectée au premier appel, persistée en config ; changement ⇒
ré-indexation (contrainte existante conservée).

## Règles

- `client.py` garde son contrat httpx (payload `/api/chat`, streaming NDJSON) ;
  toute modification ⇒ `./benchmark.sh` (NFR-004).
- Aucun appel réseau dans les tests : `MockTransport` / `MockLLMClient`.
- Démarrage paresseux : aucun processus ni connexion à la construction.
