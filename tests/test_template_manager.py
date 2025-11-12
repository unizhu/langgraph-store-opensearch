from unittest.mock import MagicMock

from langgraph_opensearch_store.config import Settings
from langgraph_opensearch_store.schema import TemplateManager


def test_template_manager_apply_creates_indices():
    client = MagicMock()
    client.indices = MagicMock()
    client.indices.exists.return_value = False

    settings = Settings(hosts="http://localhost:9200")
    manager = TemplateManager(client, settings)
    manager.apply()

    assert client.indices.put_index_template.called
    created_indices = [call.kwargs["index"] for call in client.indices.create.call_args_list]
    assert settings.data_index_bootstrap in created_indices
    assert settings.namespace_index_name in created_indices
    client.indices.put_alias.assert_called_with(
        index=settings.data_index_bootstrap,
        name=settings.data_index_alias,
        ignore=[404],
    )
