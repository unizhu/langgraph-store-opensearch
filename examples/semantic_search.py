"""Semantic search example aligning with docs/CODE_EXAMPLES.md."""

from langchain_openai import OpenAIEmbeddings

from langgraph_opensearch_store import OpenSearchStore, Settings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
settings = Settings(index_prefix="semantic_mem")
store = OpenSearchStore.from_settings(settings=settings, embeddings=embeddings)
store.setup()

ns = ("memories", "user_456")
store.put(ns, "1", {"text": "I love pizza"})
store.put(ns, "2", {"text": "I am a plumber"})

print(store.search(ns, query="I'm hungry", limit=1))
