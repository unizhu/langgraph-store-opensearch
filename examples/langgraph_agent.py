"""Sketch of wiring the store into a LangGraph workflow."""

import uuid
from typing import Any, Dict, Mapping

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import MessagesState, StateGraph, START
from langgraph.graph.state import CompiledStateGraph

from langgraph_opensearch_store import OpenSearchStore, Settings

settings = Settings(index_prefix="agent_mem")
store = OpenSearchStore.from_settings(
    settings=settings,
    embeddings=OpenAIEmbeddings(model="text-embedding-3-small"),
)
store.setup()


def call_model(state: MessagesState, config: RunnableConfig, *, store=store) -> Dict[str, Any]:
    configurable = config.get("configurable") or {}
    if not isinstance(configurable, Mapping):
        configurable = {}
    user_id = str(configurable.get("user_id", "default"))
    namespace = ("memories", user_id)
    last_entry = state["messages"][-1]
    last_message = str(last_entry.content)
    memories = store.search(namespace, query=last_message, limit=2)
    context = "\n".join(
        str(m.value.get("text")) for m in memories if isinstance(m.value, dict)
    ).strip()

    if "remember" in last_message.lower():
        store.put(namespace, str(uuid.uuid4()), {"text": last_message})

    response = ChatOpenAI(model="gpt-4o-mini").invoke(
        [
            {"role": "system", "content": f"You know: {context}" if context else "No prior info."},
            *state["messages"],
        ]
    )
    return {"messages": response}


def build_graph() -> CompiledStateGraph:
    builder = StateGraph(MessagesState)
    builder.add_node(call_model)
    builder.add_edge(START, "call_model")
    return builder.compile(store=store)


if __name__ == "__main__":
    graph = build_graph()
    cfg = RunnableConfig(configurable={"thread_id": "t1", "user_id": "alice"})
    graph.invoke({"messages": [{"role": "user", "content": "Remember that I love pizza"}]}, cfg)
    result = graph.invoke({"messages": [{"role": "user", "content": "What do I like?"}]}, cfg)
    print(result["messages"][-1].content)
