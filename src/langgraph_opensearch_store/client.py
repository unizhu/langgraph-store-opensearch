"""Helpers for creating configured OpenSearch clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings

try:  # pragma: no cover - optional dependency is validated at runtime
    from opensearchpy import AWSV4SignerAuth, OpenSearch
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "opensearch-py must be installed to use langgraph-opensearch-store."
    ) from exc

try:  # pragma: no cover - optional dependency
    from botocore.credentials import Credentials
except ImportError:  # pragma: no cover
    Credentials = None  # type: ignore[assignment]


def create_client(settings: Settings) -> OpenSearch:
    """Instantiate an OpenSearch client based on the provided settings."""

    kwargs: dict[str, Any] = {
        "hosts": settings.host_urls(),
        "verify_certs": settings.verify_certs,
        "timeout": settings.timeout,
        "headers": settings.extra_headers or None,
        "retry_on_status": [429, 502, 503, 504],
        "max_retries": 3,
    }

    if settings.auth_mode == "basic":
        kwargs["http_auth"] = _basic_auth(settings)
    else:
        kwargs["http_auth"] = _sigv4_auth(settings)

    return OpenSearch(**{k: v for k, v in kwargs.items() if v is not None})


def _basic_auth(settings: Settings) -> tuple[str, str] | None:
    if settings.username and settings.password:
        return (settings.username, settings.password.get_secret_value())
    return None


def _sigv4_auth(settings: Settings) -> AWSV4SignerAuth:
    if settings.aws_region is None:
        msg = "aws_region is required when auth_mode='sigv4'"
        raise ValueError(msg)

    try:  # pragma: no cover - boto3 is optional
        from boto3 import client as boto3_client, session as boto3_session
    except ImportError as exc:  # pragma: no cover
        raise ImportError("boto3 is required for SigV4 authentication") from exc

    session_credentials = None
    if settings.aws_role_arn:
        sts = boto3_client("sts", region_name=settings.aws_region)
        if settings.aws_web_identity_token_file:
            token = Path(settings.aws_web_identity_token_file).read_text().strip()
            resp = sts.assume_role_with_web_identity(
                RoleArn=settings.aws_role_arn,
                RoleSessionName=settings.aws_session_name,
                WebIdentityToken=token,
            )
        else:
            resp = sts.assume_role(
                RoleArn=settings.aws_role_arn,
                RoleSessionName=settings.aws_session_name,
            )
        session_credentials = resp["Credentials"]
    else:
        boto_session = boto3_session.Session()
        credentials = boto_session.get_credentials()
        if credentials is None:
            msg = "No AWS credentials available for SigV4 authentication"
            raise RuntimeError(msg)
        session_credentials = credentials.get_frozen_credentials()

    if Credentials is None:
        msg = "botocore is required for SigV4 authentication"
        raise ImportError(msg)

    if isinstance(session_credentials, dict):
        access_key = session_credentials["AccessKeyId"]
        secret_key = session_credentials["SecretAccessKey"]
        token = session_credentials.get("SessionToken")
    else:
        access_key = session_credentials.access_key
        secret_key = session_credentials.secret_key
        token = session_credentials.token

    frozen = Credentials(
        access_key=access_key,
        secret_key=secret_key,
        token=token,
    )

    return AWSV4SignerAuth(frozen, settings.aws_region, settings.aws_service)
