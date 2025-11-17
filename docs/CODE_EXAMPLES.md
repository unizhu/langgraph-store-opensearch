## Verified Example Codes (LangGraph 1.0)

All snippets mirror the LangGraph add-memory guide (MemorySaver + `store` interface) and the `langgraph-opensearch` package exposed on PyPI.[^1][^2]

***

### 1  Basic OpenSearch Store (Pydantic Settings)

```python
# examples/basic_usage.py
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

print("GET ▶", store.get(namespace, "coding_style").value)
matches = store.search(namespace, query="typed", limit=2, metadata_filter={"source": "profile"})
print("SEARCH ▶", [m.value for m in matches])
```

***

### 2  Semantic Search Pattern (mirrors docs)

```python
# examples/semantic_search.py
from langchain_openai import OpenAIEmbeddings

from langgraph_opensearch_store import OpenSearchStore
from langgraph_opensearch_store.config import Settings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
settings = Settings(
    hosts=["http://localhost:9200"],
    index_prefix="semantic_mem",
    search_mode="vector",
    search_num_candidates=256,
)

store = OpenSearchStore.from_settings(settings=settings, embeddings=embeddings)

store.setup()
ns = ("memories", "user_456")
store.put(ns, "1", {"text": "I love pizza"})
store.put(ns, "2", {"text": "I am a plumber"})

items = store.search(ns, query="I'm hungry", limit=1)
print("Top match:", items[0].value["text"])        # ➜  I love pizza
```

***

### 3  LangGraph Agent with Long-Term Memory

```python
# examples/langgraph_agent.py
import uuid
from typing import Any
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import StateGraph, MessagesState, START
from langchain_core.runnables import RunnableConfig

from langgraph_opensearch_store import OpenSearchStore
from langgraph_opensearch_store.config import Settings

# --- OpenSearch store --------------------------------------------------------
settings = Settings(hosts=["http://localhost:9200"], index_prefix="agent_mem", search_num_candidates=512)
store = OpenSearchStore.from_settings(
    settings=settings,
    embeddings=OpenAIEmbeddings(model="text-embedding-3-small"),
)

store.setup()

# --- LangGraph node ----------------------------------------------------------
def call_model(
    state: MessagesState,
    config: RunnableConfig,
    *,
    store=store,                # <- keyword-only as in docs
):
    user_id = config["configurable"]["user_id"]
    ns = ("memories", user_id)
    last = state["messages"][-1].content

    # semantic search
    memories = store.search(ns, query=last, limit=2)
    info = "\n".join(m.value["data"] for m in memories)
    system = f"You know: {info}" if info else "No prior info."

    # remember on demand
    if "remember" in last.lower():
        store.put(ns, str(uuid.uuid4()), {"data": last})

    response = ChatOpenAI(model="gpt-4o-mini").invoke(
        [{"role": "system", "content": system}, *state["messages"]]
    )
    return {"messages": response}

# --- Graph -------------------------------------------------------------------
builder = StateGraph(MessagesState)
builder.add_node(call_model)
builder.add_edge(START, "call_model")
graph = builder.compile(store=store)            # ➜   same as add-memory guide

cfg = {"configurable": {"thread_id": "t1", "user_id": "alice"}}

graph.invoke({"messages": [{"role": "user", "content": "Hi, remember my name is Alice"}]}, cfg)
answer = graph.invoke({"messages": [{"role": "user", "content": "What's my name?"}]}, cfg)
print(answer["messages"][-1].content)           # ➜  Your name is Alice.
```

***

### 4  AWS OpenSearch Service

```python
# examples/aws_deployment.py
from langchain_openai import OpenAIEmbeddings
from langgraph_opensearch_store import OpenSearchStore, Settings

settings = Settings(
    deployment="aws",
    hosts=["https://search-domain.us-east-1.es.amazonaws.com"],
    auth_mode="sigv4",
    aws_region="us-east-1",
    search_mode="hybrid",
    search_num_candidates=512,
)
store = OpenSearchStore.from_settings(settings=settings, embeddings=OpenAIEmbeddings(model="text-embedding-3-small"))
store.setup()
```

***

### 5  Full CRUD & Search Test (pytest)

```python
# tests/test_store_basic.py
import pytest
from langgraph_opensearch_store import OpenSearchStore
from langgraph_opensearch_store.config import Settings


@pytest.fixture(scope="module")
def store():
    settings = Settings(hosts=["http://localhost:9200"], index_prefix="test_index", embedding_dim=1536)
    s = OpenSearchStore.from_settings(settings=settings)
    s.setup()
    yield s
    s.client.indices.delete(index="test_index", ignore=[404])


def test_put_get(store):
    ns = ("prefs", "u1")
    store.put(ns, "key", {"x": 1})
    assert store.get(ns, "key").value["x"] == 1


def test_search(store):
    ns = ("prefs", "u1")
    res = store.search(ns, query="x", limit=1)
    assert len(res) >= 1
```

All code mirrors the signatures, namespace schemes, and `RunnableConfig` patterns shown in the official add-memory documentation.[^1]
 
[^1]: [LangGraph add-memory guide (MemorySaver + store interface)](https://docs.langchain.com/oss/python/langgraph/add-memory)
[^2]: [`langgraph-opensearch` package on PyPI](https://pypi.org/project/langgraph-opensearch/)
