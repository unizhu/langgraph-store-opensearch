from langgraph_opensearch_store.config import OpenSearchStoreConfig, Settings, SettingsBuilder
from langgraph_opensearch_store.store import OpenSearchStore


def test_settings_builder_conn_string():
    builder = SettingsBuilder().from_conn_string("https://user:pass@example.com:9443/?search_mode=text&ttl_minutes=60")
    settings = builder.build()
    assert settings.hosts == ["https://example.com:9443"]
    assert settings.username == "user"
    assert settings.search_mode == "text"
    assert settings.ttl_minutes_default == 60


def test_store_from_conn_string():
    store = OpenSearchStore.from_conn_string("https://admin:secret@localhost:9200/?verify_certs=false")
    assert isinstance(store.settings, Settings)
    assert store.settings.verify_certs is False


def test_store_config_dataclass():
    conf = OpenSearchStoreConfig(hosts=["http://localhost:9200"], search_mode="vector", ttl_minutes_default=30)
    settings = conf.to_settings()
    assert settings.search_mode == "vector"
    assert settings.ttl_minutes_default == 30
