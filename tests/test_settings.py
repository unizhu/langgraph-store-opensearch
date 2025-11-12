from langgraph_opensearch_store.config import Settings


def test_hosts_are_normalized():
    settings = Settings(hosts="localhost:9200, https://remote:443")  # type: ignore[arg-type]
    assert settings.hosts == ["https://localhost:9200", "https://remote:443"]


def test_namespace_hash_is_stable():
    settings = Settings(hosts="http://localhost:9200")
    idx1 = settings.namespace_to_index(("prefs", "u1"))
    idx2 = settings.namespace_to_index(("prefs", "u1"))
    assert idx1 == idx2


def test_ignore_ssl_flag_turns_off_verification():
    settings = Settings(hosts="http://localhost:9200", ignore_ssl_certs=True)
    assert settings.verify_certs is False
