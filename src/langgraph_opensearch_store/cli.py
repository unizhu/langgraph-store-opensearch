"""Operational CLI for LangGraph OpenSearch Store."""

from __future__ import annotations

import json
from typing import Any

import click

from .store import OpenSearchStore


@click.group()
@click.option("--conn", "conn_str", envvar="OPENSEARCH_CONN", help="Connection string.")
@click.option("--hosts", default=None, envvar="OPENSEARCH_HOSTS")
@click.option("--auth-mode", default=None, envvar="OPENSEARCH_AUTH_MODE")
@click.option("--username", default=None, envvar="OPENSEARCH_USERNAME")
@click.option("--password", default=None, envvar="OPENSEARCH_PASSWORD")
@click.pass_context
def cli(ctx: click.Context, conn_str: str | None, **kwargs: Any) -> None:
    params = {k: v for k, v in kwargs.items() if v is not None}
    if conn_str:
        store = OpenSearchStore.from_conn_string(conn_str, **params)
    else:
        store = OpenSearchStore.from_params(**params)
    store.setup()
    ctx.obj = store


@cli.command()
@click.pass_obj
def health(store: OpenSearchStore) -> None:
    click.echo(json.dumps(store.get_health(), indent=2))


@cli.command()
@click.pass_obj
def stats(store: OpenSearchStore) -> None:
    click.echo(json.dumps(store.get_stats(), indent=2))


@cli.command(name="ttl-sweep")
@click.option("--batch-size", default=1000, type=int)
@click.pass_obj
def ttl_sweep(store: OpenSearchStore, batch_size: int) -> None:
    result = store.ttl_manager.run_once(batch_size=batch_size)
    click.echo(json.dumps(result, indent=2))


def main() -> None:  # pragma: no cover - CLI entrypoint
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
