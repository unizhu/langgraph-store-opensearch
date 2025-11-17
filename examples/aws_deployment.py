"""AWS OpenSearch Service helper example."""

from langchain_openai import OpenAIEmbeddings

from langgraph_opensearch_store import OpenSearchStore, Settings

settings = Settings(
    deployment="aws",
    hosts=["https://search-example.us-east-1.es.amazonaws.com"],
    auth_mode="sigv4",
    aws_region="us-east-1",
    search_mode="hybrid",
    search_num_candidates=512,
)
store = OpenSearchStore.from_settings(settings=settings, embeddings=OpenAIEmbeddings(model="text-embedding-3-small"))
store.setup()
