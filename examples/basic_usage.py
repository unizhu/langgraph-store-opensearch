"""Minimal synchronous usage example."""

from langchain_openai import OpenAIEmbeddings

from langgraph_opensearch_store import OpenSearchStore, Settings

settings = Settings()
store = OpenSearchStore.from_settings(settings=settings, embeddings=OpenAIEmbeddings(model="text-embedding-3-small"))
store.setup()

namespace = ("prefs", "user_123")
store.put(namespace, "coding_style", {"text": "I enjoy typed Python"})
print(store.get(namespace, "coding_style"))
