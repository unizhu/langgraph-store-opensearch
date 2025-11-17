"""Minimal synchronous usage example with search + metadata filters."""

from langchain_openai import OpenAIEmbeddings

from langgraph_opensearch_store import OpenSearchStore, Settings


settings = Settings(
    hosts=["http://localhost:9200"],
    search_mode="hybrid",
    search_num_candidates=400,
)
store = OpenSearchStore.from_settings(
    settings=settings,
    embeddings=OpenAIEmbeddings(model="text-embedding-3-small"),
)
store.setup()

namespace = ("prefs", "user_123")
store.put(namespace, "coding_style", {"text": "I enjoy typed Python", "source": "profile"})
store.put(namespace, "favorite_stack", {"text": "Async FastAPI", "source": "profile"})

print(store.get(namespace, "coding_style"))

matches = store.search(namespace, query="typed", limit=2, metadata_filter={"source": "profile"})
print([m.value for m in matches])
