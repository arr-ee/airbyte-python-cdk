#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#
from __future__ import annotations

import copy
import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from unittest import mock
from unittest.mock import MagicMock, patch

import orjson
import pytest
import requests

from unit_tests.connector_builder.utils import create_configured_catalog

from airbyte_cdk import connector_builder
from airbyte_cdk.connector_builder.connector_builder_handler import (
    DEFAULT_MAXIMUM_NUMBER_OF_PAGES_PER_SLICE,
    DEFAULT_MAXIMUM_NUMBER_OF_SLICES,
    DEFAULT_MAXIMUM_RECORDS,
    TestReadLimits,
    create_source,
    get_limits,
    resolve_manifest,
)
from airbyte_cdk.connector_builder.main import (
    handle_connector_builder_request,
    handle_request,
    read_stream,
)
from airbyte_cdk.connector_builder.models import (
    LogMessage,
    StreamRead,
    StreamReadPages,
    StreamReadSlices,
)
from airbyte_cdk.models import (
    AirbyteLogMessage,
    AirbyteMessage,
    AirbyteMessageSerializer,
    AirbyteRecordMessage,
    AirbyteStateMessage,
    AirbyteStream,
    AirbyteStreamState,
    ConfiguredAirbyteCatalog,
    ConfiguredAirbyteCatalogSerializer,
    ConfiguredAirbyteStream,
    ConnectorSpecification,
    DestinationSyncMode,
    Level,
    StreamDescriptor,
    SyncMode,
    Type,
)
from airbyte_cdk.models import Type as MessageType
from airbyte_cdk.sources.declarative.declarative_stream import DeclarativeStream
from airbyte_cdk.sources.declarative.manifest_declarative_source import ManifestDeclarativeSource
from airbyte_cdk.sources.declarative.retrievers import SimpleRetrieverTestReadDecorator
from airbyte_cdk.sources.declarative.retrievers.simple_retriever import SimpleRetriever
from airbyte_cdk.utils.airbyte_secrets_utils import filter_secrets, update_secrets


if TYPE_CHECKING:
    from airbyte_cdk.sources.streams.core import Stream


_stream_name = "stream_with_custom_requester"
_stream_primary_key = "id"
_stream_url_base = "https://api.sendgrid.com"
_stream_options = {
    "name": _stream_name,
    "primary_key": _stream_primary_key,
    "url_base": _stream_url_base,
}
_page_size = 2

_A_STATE = [
    AirbyteStateMessage(
        type="STREAM",
        stream=AirbyteStreamState(
            stream_descriptor=StreamDescriptor(name=_stream_name), stream_state={"key": "value"}
        ),
    )
]

_A_PER_PARTITION_STATE = [
    AirbyteStateMessage(
        type="STREAM",
        stream=AirbyteStreamState(
            stream_descriptor=StreamDescriptor(name=_stream_name),
            stream_state={
                "states": [
                    {
                        "partition": {"key": "value"},
                        "cursor": {"item_id": 0},
                    },
                ],
                "parent_state": {},
            },
        ),
    )
]

MANIFEST: dict[str, str | dict] = {
    "version": "0.30.3",
    "definitions": {
        "retriever": {
            "paginator": {
                "type": "DefaultPaginator",
                "page_size": _page_size,
                "page_size_option": {"inject_into": "request_parameter", "field_name": "page_size"},
                "page_token_option": {"inject_into": "path", "type": "RequestPath"},
                "pagination_strategy": {
                    "type": "CursorPagination",
                    "cursor_value": "{{ response._metadata.next }}",
                    "page_size": _page_size,
                },
            },
            "partition_router": {
                "type": "ListPartitionRouter",
                "values": ["0", "1", "2", "3", "4", "5", "6", "7"],
                "cursor_field": "item_id",
            },
            "" "requester": {
                "path": "/v3/marketing/lists",
                "authenticator": {
                    "type": "BearerAuthenticator",
                    "api_token": "{{ config.apikey }}",
                },
                "request_parameters": {"a_param": "10"},
            },
            "record_selector": {"extractor": {"field_path": ["result"]}},
        },
    },
    "streams": [
        {
            "type": "DeclarativeStream",
            "$parameters": _stream_options,
            "retriever": "#/definitions/retriever",
        },
    ],
    "check": {"type": "CheckStream", "stream_names": ["lists"]},
    "spec": {
        "connection_specification": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": [],
            "properties": {},
            "additionalProperties": True,
        },
        "type": "Spec",
    },
}

OAUTH_MANIFEST: dict[str, str | dict] = {
    "version": "0.30.3",
    "definitions": {
        "retriever": {
            "paginator": {
                "type": "DefaultPaginator",
                "page_size": _page_size,
                "page_size_option": {"inject_into": "request_parameter", "field_name": "page_size"},
                "page_token_option": {"inject_into": "path", "type": "RequestPath"},
                "pagination_strategy": {
                    "type": "CursorPagination",
                    "cursor_value": "{{ response.next }}",
                    "page_size": _page_size,
                },
            },
            "partition_router": {
                "type": "ListPartitionRouter",
                "values": ["0", "1", "2", "3", "4", "5", "6", "7"],
                "cursor_field": "item_id",
            },
            "" "requester": {
                "path": "/v3/marketing/lists",
                "authenticator": {"type": "OAuthAuthenticator", "api_token": "{{ config.apikey }}"},
                "request_parameters": {"a_param": "10"},
            },
            "record_selector": {"extractor": {"field_path": ["result"]}},
        },
    },
    "streams": [
        {
            "type": "DeclarativeStream",
            "$parameters": _stream_options,
            "retriever": "#/definitions/retriever",
        },
    ],
    "check": {"type": "CheckStream", "stream_names": ["lists"]},
    "spec": {
        "connection_specification": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": [],
            "properties": {},
            "additionalProperties": True,
        },
        "type": "Spec",
    },
}

RESOLVE_MANIFEST_CONFIG = {
    "__injected_declarative_manifest": MANIFEST,
    "__command": "resolve_manifest",
}

TEST_READ_CONFIG = {
    "__injected_declarative_manifest": MANIFEST,
    "__command": "test_read",
    "__test_read_config": {"max_pages_per_slice": 2, "max_slices": 5, "max_records": 10},
}

DUMMY_CATALOG = {
    "streams": [
        {
            "stream": {
                "name": "dummy_stream",
                "json_schema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {},
                },
                "supported_sync_modes": ["full_refresh"],
                "source_defined_cursor": False,
            },
            "sync_mode": "full_refresh",
            "destination_sync_mode": "overwrite",
        }
    ]
}

CONFIGURED_CATALOG = {
    "streams": [
        {
            "stream": {
                "name": _stream_name,
                "json_schema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {},
                },
                "supported_sync_modes": ["full_refresh"],
                "source_defined_cursor": False,
            },
            "sync_mode": "full_refresh",
            "destination_sync_mode": "overwrite",
        }
    ]
}

MOCK_RESPONSE = {
    "result": [
        {"id": 1, "name": "Nora Moon", "position": "director"},
        {"id": 2, "name": "Hae Sung Jung", "position": "cinematographer"},
        {"id": 3, "name": "Arthur Zenneranski", "position": "composer"},
    ]
}


@pytest.fixture
def valid_resolve_manifest_config_file(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(RESOLVE_MANIFEST_CONFIG))
    return config_file


@pytest.fixture
def valid_read_config_file(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(TEST_READ_CONFIG))
    return config_file


@pytest.fixture
def dummy_catalog(tmp_path: Path) -> Path:
    config_file = tmp_path / "catalog.json"
    config_file.write_text(json.dumps(DUMMY_CATALOG))
    return config_file


@pytest.fixture
def configured_catalog(tmp_path: Path) -> Path:
    config_file = tmp_path / "catalog.json"
    config_file.write_text(json.dumps(CONFIGURED_CATALOG))
    return config_file


@pytest.fixture
def invalid_config_file(tmp_path: Path) -> Path:
    invalid_config = copy.deepcopy(RESOLVE_MANIFEST_CONFIG)
    invalid_config["__command"] = "bad_command"
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(invalid_config))
    return config_file


def _mocked_send(
    self,
    request: requests.Request,
    **kwargs: Any,
) -> requests.Response:
    """Mocks the outbound send operation to provide faster and more reliable responses compared to actual API requests"""
    response = requests.Response()
    response.request = request
    response.status_code = 200
    response.headers = {"header": "value"}
    response_body = MOCK_RESPONSE
    response._content = json.dumps(  # noqa: SLF001  (private member accessed)
        response_body,
    ).encode("utf-8")
    return response


def test_handle_resolve_manifest(
    valid_resolve_manifest_config_file: Path,
    dummy_catalog: Path,
) -> None:
    with mock.patch.object(
        connector_builder.main,
        "handle_connector_builder_request",
        return_value=AirbyteMessage(type=MessageType.RECORD),
    ) as patched_handle:
        handle_request(
            [
                "read",
                "--config",
                str(valid_resolve_manifest_config_file),
                "--catalog",
                str(dummy_catalog),
            ]
        )
        assert patched_handle.call_count == 1


def test_handle_test_read(
    valid_read_config_file: Path,
    configured_catalog: Path,
) -> None:
    with mock.patch.object(
        connector_builder.main,
        "handle_connector_builder_request",
        return_value=AirbyteMessage(type=MessageType.RECORD),
    ) as patch:
        handle_request(
            ["read", "--config", str(valid_read_config_file), "--catalog", str(configured_catalog)]
        )
        assert patch.call_count == 1


def test_resolve_manifest(
    valid_resolve_manifest_config_file: Path,
) -> None:
    config = copy.deepcopy(RESOLVE_MANIFEST_CONFIG)
    command = "resolve_manifest"
    config["__command"] = command
    source = ManifestDeclarativeSource(MANIFEST)
    limits = TestReadLimits()
    resolved_manifest = handle_connector_builder_request(
        source, command, config, create_configured_catalog("dummy_stream"), _A_STATE, limits
    )

    expected_resolved_manifest = {
        "type": "DeclarativeSource",
        "version": "0.30.3",
        "definitions": {
            "retriever": {
                "paginator": {
                    "type": "DefaultPaginator",
                    "page_size": _page_size,
                    "page_size_option": {
                        "inject_into": "request_parameter",
                        "field_name": "page_size",
                    },
                    "page_token_option": {"inject_into": "path", "type": "RequestPath"},
                    "pagination_strategy": {
                        "type": "CursorPagination",
                        "cursor_value": "{{ response._metadata.next }}",
                        "page_size": _page_size,
                    },
                },
                "partition_router": {
                    "type": "ListPartitionRouter",
                    "values": ["0", "1", "2", "3", "4", "5", "6", "7"],
                    "cursor_field": "item_id",
                },
                "requester": {
                    "path": "/v3/marketing/lists",
                    "authenticator": {
                        "type": "BearerAuthenticator",
                        "api_token": "{{ config.apikey }}",
                    },
                    "request_parameters": {"a_param": "10"},
                },
                "record_selector": {"extractor": {"field_path": ["result"]}},
            },
        },
        "streams": [
            {
                "type": "DeclarativeStream",
                "retriever": {
                    "type": "SimpleRetriever",
                    "paginator": {
                        "type": "DefaultPaginator",
                        "page_size": _page_size,
                        "page_size_option": {
                            "type": "RequestOption",
                            "inject_into": "request_parameter",
                            "field_name": "page_size",
                            "name": _stream_name,
                            "primary_key": _stream_primary_key,
                            "url_base": _stream_url_base,
                            "$parameters": _stream_options,
                        },
                        "page_token_option": {
                            "type": "RequestPath",
                            "inject_into": "path",
                            "name": _stream_name,
                            "primary_key": _stream_primary_key,
                            "url_base": _stream_url_base,
                            "$parameters": _stream_options,
                        },
                        "pagination_strategy": {
                            "type": "CursorPagination",
                            "cursor_value": "{{ response._metadata.next }}",
                            "name": _stream_name,
                            "primary_key": _stream_primary_key,
                            "url_base": _stream_url_base,
                            "$parameters": _stream_options,
                            "page_size": _page_size,
                        },
                        "name": _stream_name,
                        "primary_key": _stream_primary_key,
                        "url_base": _stream_url_base,
                        "$parameters": _stream_options,
                    },
                    "requester": {
                        "type": "HttpRequester",
                        "path": "/v3/marketing/lists",
                        "authenticator": {
                            "type": "BearerAuthenticator",
                            "api_token": "{{ config.apikey }}",
                            "name": _stream_name,
                            "primary_key": _stream_primary_key,
                            "url_base": _stream_url_base,
                            "$parameters": _stream_options,
                        },
                        "request_parameters": {"a_param": "10"},
                        "name": _stream_name,
                        "primary_key": _stream_primary_key,
                        "url_base": _stream_url_base,
                        "$parameters": _stream_options,
                    },
                    "partition_router": {
                        "type": "ListPartitionRouter",
                        "values": ["0", "1", "2", "3", "4", "5", "6", "7"],
                        "cursor_field": "item_id",
                        "name": _stream_name,
                        "primary_key": _stream_primary_key,
                        "url_base": _stream_url_base,
                        "$parameters": _stream_options,
                    },
                    "record_selector": {
                        "type": "RecordSelector",
                        "extractor": {
                            "type": "DpathExtractor",
                            "field_path": ["result"],
                            "name": _stream_name,
                            "primary_key": _stream_primary_key,
                            "url_base": _stream_url_base,
                            "$parameters": _stream_options,
                        },
                        "name": _stream_name,
                        "primary_key": _stream_primary_key,
                        "url_base": _stream_url_base,
                        "$parameters": _stream_options,
                    },
                    "name": _stream_name,
                    "primary_key": _stream_primary_key,
                    "url_base": _stream_url_base,
                    "$parameters": _stream_options,
                },
                "name": _stream_name,
                "primary_key": _stream_primary_key,
                "url_base": _stream_url_base,
                "$parameters": _stream_options,
            },
        ],
        "check": {"type": "CheckStream", "stream_names": ["lists"]},
        "spec": {
            "connection_specification": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": [],
                "properties": {},
                "additionalProperties": True,
            },
            "type": "Spec",
        },
    }
    assert resolved_manifest
    assert resolved_manifest.record
    assert resolved_manifest.record.data["manifest"] == expected_resolved_manifest
    assert resolved_manifest.record.stream == "resolve_manifest"


def test_resolve_manifest_error_returns_error_response() -> None:
    class MockManifestDeclarativeSource:
        @property
        def resolved_manifest(self) -> os.NoReturn:
            raise ValueError

    source = MockManifestDeclarativeSource()
    response = resolve_manifest(source)
    assert "Error resolving manifest" in response.trace.error.message


def test_read() -> None:
    config = TEST_READ_CONFIG
    source = ManifestDeclarativeSource(MANIFEST)

    real_record = AirbyteRecordMessage(
        data={"id": "1234", "key": "value"}, emitted_at=1, stream=_stream_name
    )
    stream_read = StreamRead(
        logs=[{"message": "here be a log message"}],
        slices=[
            StreamReadSlices(
                pages=[StreamReadPages(records=[real_record], request=None, response=None)],
                slice_descriptor=None,
                state=None,
            )
        ],
        auxiliary_requests=[],
        test_read_limit_reached=False,
        inferred_schema=None,
        inferred_datetime_formats=None,
        latest_config_update={},
    )

    expected_airbyte_message = AirbyteMessage(
        type=MessageType.RECORD,
        record=AirbyteRecordMessage(
            stream=_stream_name,
            data={
                "logs": [{"message": "here be a log message"}],
                "slices": [
                    {
                        "pages": [{"records": [real_record], "request": None, "response": None}],
                        "slice_descriptor": None,
                        "state": None,
                    }
                ],
                "test_read_limit_reached": False,
                "auxiliary_requests": [],
                "inferred_schema": None,
                "inferred_datetime_formats": None,
                "latest_config_update": {},
            },
            emitted_at=1,
        ),
    )
    limits = TestReadLimits()
    with patch(
        "airbyte_cdk.connector_builder.message_grouper.MessageGrouper.get_message_groups",
        return_value=stream_read,
    ) as get_message_groups_mock:
        output_record: AirbyteMessage = handle_connector_builder_request(
            source=source,
            command="test_read",
            config=config,
            catalog=ConfiguredAirbyteCatalogSerializer.load(CONFIGURED_CATALOG),
            state=_A_STATE,
            limits=limits,
        )
        #         source: DeclarativeSource,
        # config: Mapping[str, Any],
        # configured_catalog: ConfiguredAirbyteCatalog,
        # state: list[AirbyteStateMessage],
        # record_limit: int | None = None,

        get_message_groups_mock.assert_called_with(
            source=source,
            config=config,
            configured_catalog=ConfiguredAirbyteCatalogSerializer.load(CONFIGURED_CATALOG),
            state=_A_STATE,
            record_limit=limits.max_records,
        )
        assert output_record.record
        output_record.record.emitted_at = 1
        assert (
            orjson.dumps(AirbyteMessageSerializer.dump(output_record)).decode()
            == orjson.dumps(AirbyteMessageSerializer.dump(expected_airbyte_message)).decode()
        )


def test_config_update() -> None:
    manifest = copy.deepcopy(MANIFEST)
    manifest["definitions"]["retriever"]["requester"]["authenticator"] = {
        "type": "OAuthAuthenticator",
        "token_refresh_endpoint": "https://oauth.endpoint.com/tokens/bearer",
        "client_id": "{{ config['credentials']['client_id'] }}",
        "client_secret": "{{ config['credentials']['client_secret'] }}",
        "refresh_token": "{{ config['credentials']['refresh_token'] }}",
        "refresh_token_updater": {},
    }
    config = copy.deepcopy(TEST_READ_CONFIG)
    config["__injected_declarative_manifest"] = manifest
    config["credentials"] = {
        "client_id": "a client id",
        "client_secret": "a client secret",
        "refresh_token": "a refresh token",
    }
    source = ManifestDeclarativeSource(manifest)

    refresh_request_response = {
        "access_token": "an updated access token",
        "refresh_token": "an updated refresh token",
        "expires_in": 3600,
    }
    with patch(
        "airbyte_cdk.sources.streams.http.requests_native_auth.SingleUseRefreshTokenOauth2Authenticator._get_refresh_access_token_response",
        return_value=refresh_request_response,
    ):
        output = handle_connector_builder_request(
            source=source,
            command="test_read",
            config=config,
            catalog=ConfiguredAirbyteCatalogSerializer.load(CONFIGURED_CATALOG),
            state=_A_PER_PARTITION_STATE,
            limits=TestReadLimits(),
        )
        assert output.record.data["latest_config_update"]


@patch("traceback.TracebackException.from_exception")
def test_read_returns_error_response(
    mock_from_exception: Any,  # noqa: ANN401  (any-type)
) -> None:
    class MockDeclarativeStream:
        @property
        def primary_key(self) -> list[list]:
            return [[]]

        @property
        def cursor_field(self) -> list:
            return []

    class MockManifestDeclarativeSource:
        def streams(self, config) -> list[MockDeclarativeStream]:
            return [MockDeclarativeStream()]

        def read(self, logger, config, catalog, state) -> os.NoReturn:
            raise ValueError("error_message")

        def spec(self, logger: logging.Logger) -> ConnectorSpecification:
            connector_specification = mock.Mock()
            connector_specification.connectionSpecification = {}
            return connector_specification

        @property
        def check_config_against_spec(self) -> Literal[False]:
            return False

    stack_trace = "a stack trace"
    mock_from_exception.return_value = stack_trace

    source = MockManifestDeclarativeSource()
    limits = TestReadLimits()
    response = read_stream(
        source,
        TEST_READ_CONFIG,
        ConfiguredAirbyteCatalogSerializer.load(CONFIGURED_CATALOG),
        _A_STATE,
        limits,
    )

    expected_stream_read = StreamRead(
        logs=[LogMessage("error_message", "ERROR", "error_message", "a stack trace")],
        slices=[],
        test_read_limit_reached=False,
        auxiliary_requests=[],
        inferred_schema=None,
        inferred_datetime_formats={},
        latest_config_update=None,
    )

    expected_message = AirbyteMessage(
        type=MessageType.RECORD,
        record=AirbyteRecordMessage(
            stream=_stream_name, data=dataclasses.asdict(expected_stream_read), emitted_at=1
        ),
    )
    response.record.emitted_at = 1
    assert response == expected_message


def test_handle_429_response() -> None:
    response = _create_429_page_response(
        {"result": [{"error": "too many requests"}], "_metadata": {"next": "next"}}
    )

    # Add backoff strategy to avoid default endless backoff loop
    TEST_READ_CONFIG["__injected_declarative_manifest"]["definitions"]["retriever"]["requester"][
        "error_handler"
    ] = {"backoff_strategies": [{"type": "ConstantBackoffStrategy", "backoff_time_in_seconds": 5}]}

    config = TEST_READ_CONFIG
    limits = TestReadLimits()
    source = create_source(config, limits)

    with patch("requests.Session.send", return_value=response) as mock_send:
        response: AirbyteMessage = handle_connector_builder_request(
            source=source,
            command="test_read",
            config=config,
            catalog=ConfiguredAirbyteCatalogSerializer.load(CONFIGURED_CATALOG),
            state=_A_PER_PARTITION_STATE,
            limits=limits,
        )

        mock_send.assert_called_once()


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("check", id="test_check_command_error"),
        pytest.param("spec", id="test_spec_command_error"),
        pytest.param("discover", id="test_discover_command_error"),
        pytest.param(None, id="test_command_is_none_error"),
        pytest.param("", id="test_command_is_empty_error"),
    ],
)
def test_invalid_protocol_command(
    command: Literal["check", "spec", "discover", ""] | None,
    valid_resolve_manifest_config_file: Path,
) -> None:
    config = copy.deepcopy(RESOLVE_MANIFEST_CONFIG)
    config["__command"] = "resolve_manifest"
    with pytest.raises(SystemExit):
        handle_request(
            [command, "--config", str(valid_resolve_manifest_config_file), "--catalog", ""]
        )


def test_missing_command(valid_resolve_manifest_config_file: Path) -> None:
    with pytest.raises(SystemExit):
        handle_request(["--config", str(valid_resolve_manifest_config_file), "--catalog", ""])


def test_missing_catalog(valid_resolve_manifest_config_file: Path) -> None:
    with pytest.raises(SystemExit):
        handle_request(["read", "--config", str(valid_resolve_manifest_config_file)])


def test_missing_config(valid_resolve_manifest_config_file: Path) -> None:
    with pytest.raises(SystemExit):
        handle_request(["read", "--catalog", str(valid_resolve_manifest_config_file)])


def test_invalid_config_command(
    invalid_config_file: Path,
    dummy_catalog: Path,
) -> None:
    with pytest.raises(ValueError):
        handle_request(
            ["read", "--config", str(invalid_config_file), "--catalog", str(dummy_catalog)]
        )


@pytest.fixture
def manifest_declarative_source() -> mock.Mock:
    return mock.Mock(spec=ManifestDeclarativeSource, autospec=True)


def create_mock_retriever(name: str, url_base: str, path: Path) -> mock.Mock:
    http_stream = mock.Mock(spec=SimpleRetriever, autospec=True)
    http_stream.name = name
    http_stream.requester = MagicMock()
    http_stream.requester.get_url_base.return_value = url_base
    http_stream.requester.get_path.return_value = path
    http_stream._paginator_path.return_value = None
    return http_stream


def create_mock_declarative_stream(http_stream) -> mock.Mock:
    declarative_stream = mock.Mock(spec=DeclarativeStream, autospec=True)
    declarative_stream.retriever = http_stream
    return declarative_stream


@pytest.mark.parametrize(
    "test_name, config, expected_max_records, expected_max_slices, expected_max_pages_per_slice",
    [
        (
            "test_no_test_read_config",
            {},
            DEFAULT_MAXIMUM_RECORDS,
            DEFAULT_MAXIMUM_NUMBER_OF_SLICES,
            DEFAULT_MAXIMUM_NUMBER_OF_PAGES_PER_SLICE,
        ),
        (
            "test_no_values_set",
            {"__test_read_config": {}},
            DEFAULT_MAXIMUM_RECORDS,
            DEFAULT_MAXIMUM_NUMBER_OF_SLICES,
            DEFAULT_MAXIMUM_NUMBER_OF_PAGES_PER_SLICE,
        ),
        (
            "test_values_are_set",
            {"__test_read_config": {"max_slices": 1, "max_pages_per_slice": 2, "max_records": 3}},
            3,
            1,
            2,
        ),
    ],
)
def test_get_limits(
    test_name: Literal["test_no_test_read_config", "test_no_values_set", "test_values_are_set"],
    config: dict[str, dict[Any, Any]] | dict[str, dict[str, int]],
    expected_max_records: Literal[100] | Literal[3],
    expected_max_slices: Literal[5] | Literal[1],
    expected_max_pages_per_slice: Literal[5] | Literal[2],
) -> None:
    limits = get_limits(config)
    assert limits.max_records == expected_max_records
    assert limits.max_pages_per_slice == expected_max_pages_per_slice
    assert limits.max_slices == expected_max_slices


def test_create_source() -> None:
    max_records = 3
    max_pages_per_slice = 2
    max_slices = 1
    limits = TestReadLimits(max_records, max_pages_per_slice, max_slices)

    config = {"__injected_declarative_manifest": MANIFEST}

    source = create_source(config, limits)

    assert isinstance(source, ManifestDeclarativeSource)
    assert source._constructor._limit_pages_fetched_per_slice == limits.max_pages_per_slice
    assert source._constructor._limit_slices_fetched == limits.max_slices
    assert source._constructor._disable_cache


def request_log_message(request: dict) -> AirbyteMessage:
    return AirbyteMessage(
        type=Type.LOG,
        log=AirbyteLogMessage(level=Level.INFO, message=f"request:{json.dumps(request)}"),
    )


def response_log_message(response: dict) -> AirbyteMessage:
    return AirbyteMessage(
        type=Type.LOG,
        log=AirbyteLogMessage(level=Level.INFO, message=f"response:{json.dumps(response)}"),
    )


def _create_request() -> requests.PreparedRequest:
    url = "https://example.com/api"
    headers = {"Content-Type": "application/json"}
    return requests.Request("POST", url, headers=headers, json={"key": "value"}).prepare()


def _create_response(
    body,
    request,
) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = bytes(json.dumps(body), "utf-8")  # noqa: SLF001  (private member)
    response.headers["Content-Type"] = "application/json"
    response.request = request
    return response


def _create_429_response(
    body,
    request,
) -> requests.Response:
    response = requests.Response()
    response.status_code = 429
    response._content = bytes(  # noqa: SLF001  (private member)
        json.dumps(body),
        encoding="utf-8",
    )
    response.headers["Content-Type"] = "application/json"
    response.request = request
    return response


def _create_page_response(
    response_body,
) -> requests.Response:
    request = _create_request()
    return _create_response(response_body, request)


def _create_429_page_response(
    response_body,
) -> requests.Response:
    request = _create_request()
    return _create_429_response(response_body, request)


@patch.object(
    requests.Session,
    "send",
    side_effect=(
        _create_page_response(
            {"result": [{"id": 0}, {"id": 1}], "_metadata": {"next": "next"}},
        ),
        _create_page_response(
            {"result": [{"id": 2}], "_metadata": {"next": "next"}},
        ),
    )
    * 10,
)
def test_read_source(
    mock_http_stream,
) -> None:
    """This test sort of acts as an integration test for the connector builder.

    Each slice has two pages
    The first page has two records
    The second page one record

    The response._metadata.next field in the first page tells the paginator to fetch the next page.
    """
    max_records = 100
    max_pages_per_slice = 2
    max_slices = 3
    limits = TestReadLimits(
        max_records=max_records,
        max_pages_per_slice=max_pages_per_slice,
        max_slices=max_slices,
    )

    catalog = ConfiguredAirbyteCatalog(
        streams=[
            ConfiguredAirbyteStream(
                stream=AirbyteStream(
                    name=_stream_name, json_schema={}, supported_sync_modes=[SyncMode.full_refresh]
                ),
                sync_mode=SyncMode.full_refresh,
                destination_sync_mode=DestinationSyncMode.append,
            )
        ]
    )

    config = {"__injected_declarative_manifest": MANIFEST}

    source = create_source(config, limits)

    output_data = read_stream(source, config, catalog, _A_PER_PARTITION_STATE, limits).record.data
    slices = output_data["slices"]

    assert len(slices) == max_slices
    for s in slices:
        pages = s["pages"]
        assert len(pages) == max_pages_per_slice

        first_page, second_page = pages[0], pages[1]
        assert len(first_page["records"]) == _page_size
        assert len(second_page["records"]) == 1

    streams: list[Stream] = source.streams(config)
    for s in streams:
        assert isinstance(s.retriever, SimpleRetrieverTestReadDecorator)


@patch.object(
    target=requests.Session,
    attribute="send",
    side_effect=(
        _create_page_response({"result": [{"id": 0}, {"id": 1}], "_metadata": {"next": "next"}}),
        _create_page_response({"result": [{"id": 2}], "_metadata": {"next": "next"}}),
    ),
)
def test_read_source_single_page_single_slice(
    mock_http_stream: Any,  # noqa: ANN401, ARG001  (any-type, unused-argument)
) -> None:
    max_records = 100
    max_pages_per_slice = 1
    max_slices = 1
    limits = TestReadLimits(
        max_records=max_records,
        max_pages_per_slice=max_pages_per_slice,
        max_slices=max_slices,
    )

    catalog = ConfiguredAirbyteCatalog(
        streams=[
            ConfiguredAirbyteStream(
                stream=AirbyteStream(
                    name=_stream_name, json_schema={}, supported_sync_modes=[SyncMode.full_refresh]
                ),
                sync_mode=SyncMode.full_refresh,
                destination_sync_mode=DestinationSyncMode.append,
            )
        ]
    )

    config = {"__injected_declarative_manifest": MANIFEST}

    source = create_source(config, limits)

    output_data = read_stream(
        source=source,
        config=config,
        configured_catalog=catalog,
        state=_A_PER_PARTITION_STATE,
        limits=limits,
    ).record.data
    slices = output_data["slices"]

    assert len(slices) == max_slices
    for s in slices:
        pages = s["pages"]
        assert len(pages) == max_pages_per_slice

        first_page = pages[0]
        assert len(first_page["records"]) == _page_size

    streams = source.streams(config)
    for s in streams:
        assert isinstance(s.retriever, SimpleRetrieverTestReadDecorator)


@pytest.mark.parametrize(
    "deployment_mode, url_base, expected_error",
    [
        pytest.param(
            "CLOUD",
            "https://airbyte.com/api/v1/characters",
            None,
            id="test_cloud_read_with_public_endpoint",
        ),
        pytest.param(
            "CLOUD",
            "https://10.0.27.27",
            "AirbyteTracedException",
            id="test_cloud_read_with_private_endpoint",
        ),
        pytest.param(
            "CLOUD",
            "https://localhost:80/api/v1/cast",
            "AirbyteTracedException",
            id="test_cloud_read_with_localhost",
        ),
        pytest.param(
            "CLOUD",
            "http://unsecured.protocol/api/v1",
            "InvalidSchema",
            id="test_cloud_read_with_unsecured_endpoint",
        ),
        pytest.param(
            "CLOUD",
            "https://domainwithoutextension",
            "Invalid URL",
            id="test_cloud_read_with_invalid_url_endpoint",
        ),
        pytest.param(
            "OSS", "https://airbyte.com/api/v1/", None, id="test_oss_read_with_public_endpoint"
        ),
        pytest.param(
            "OSS", "https://10.0.27.27/api/v1/", None, id="test_oss_read_with_private_endpoint"
        ),
    ],
)
@patch.object(target=requests.Session, attribute="send", new=_mocked_send)
def test_handle_read_external_requests(
    deployment_mode: str,
    url_base: str,
    expected_error: str,
) -> None:
    """This test acts like an integration test for the connector builder when it receives Test Read requests.

    The scenario being tested is whether requests should be denied if they are done on an unsecure channel or are made to internal
    endpoints when running on Cloud or OSS deployments
    """
    limits = TestReadLimits(max_records=100, max_pages_per_slice=1, max_slices=1)

    catalog = ConfiguredAirbyteCatalog(
        streams=[
            ConfiguredAirbyteStream(
                stream=AirbyteStream(
                    name=_stream_name, json_schema={}, supported_sync_modes=[SyncMode.full_refresh]
                ),
                sync_mode=SyncMode.full_refresh,
                destination_sync_mode=DestinationSyncMode.append,
            )
        ]
    )

    test_manifest = MANIFEST
    test_manifest["streams"][0]["$parameters"]["url_base"] = url_base
    config = {"__injected_declarative_manifest": test_manifest}

    source = create_source(config, limits)

    with mock.patch.dict(os.environ, {"DEPLOYMENT_MODE": deployment_mode}, clear=False):
        output_data = read_stream(
            source, config, catalog, _A_PER_PARTITION_STATE, limits
        ).record.data
        if expected_error:
            assert (
                len(output_data["logs"]) > 0
            ), "Expected at least one log message with the expected error"
            error_message = output_data["logs"][0]
            assert error_message["level"] == "ERROR"
            assert expected_error in error_message["stacktrace"]
        else:
            page_records = output_data["slices"][0]["pages"][0]
            assert len(page_records) == len(MOCK_RESPONSE["result"])


@pytest.mark.parametrize(
    "deployment_mode, token_url, expected_error",
    [
        pytest.param(
            "CLOUD",
            "https://airbyte.com/tokens/bearer",
            None,
            id="test_cloud_read_with_public_endpoint",
        ),
        pytest.param(
            "CLOUD",
            "https://10.0.27.27/tokens/bearer",
            "AirbyteTracedException",
            id="test_cloud_read_with_private_endpoint",
        ),
        pytest.param(
            "CLOUD",
            "http://unsecured.protocol/tokens/bearer",
            "InvalidSchema",
            id="test_cloud_read_with_unsecured_endpoint",
        ),
        pytest.param(
            "CLOUD",
            "https://domainwithoutextension",
            "Invalid URL",
            id="test_cloud_read_with_invalid_url_endpoint",
        ),
        pytest.param(
            "OSS",
            "https://airbyte.com/tokens/bearer",
            None,
            id="test_oss_read_with_public_endpoint",
        ),
        pytest.param(
            "OSS",
            "https://10.0.27.27/tokens/bearer",
            None,
            id="test_oss_read_with_private_endpoint",
        ),
    ],
)
@patch.object(
    target=requests.Session,
    attribute="send",
    new=_mocked_send,
)
def test_handle_read_external_oauth_request(
    deployment_mode: str,
    token_url: str,
    expected_error: str,
) -> None:
    """This test acts like an integration test for the connector builder when it receives Test Read requests.

    The scenario being tested is whether requests should be denied if they are done on an unsecure channel or are made to internal
    endpoints when running on Cloud or OSS deployments
    """
    limits = TestReadLimits(max_records=100, max_pages_per_slice=1, max_slices=1)

    catalog = ConfiguredAirbyteCatalog(
        streams=[
            ConfiguredAirbyteStream(
                stream=AirbyteStream(
                    name=_stream_name, json_schema={}, supported_sync_modes=[SyncMode.full_refresh]
                ),
                sync_mode=SyncMode.full_refresh,
                destination_sync_mode=DestinationSyncMode.append,
            )
        ]
    )

    oauth_authenticator_config: dict[str, str] = {
        "type": "OAuthAuthenticator",
        "token_refresh_endpoint": token_url,
        "client_id": "greta",
        "client_secret": "teo",
        "refresh_token": "john",
    }

    test_manifest = MANIFEST
    test_manifest["definitions"]["retriever"]["requester"]["authenticator"] = (
        oauth_authenticator_config
    )
    config = {"__injected_declarative_manifest": test_manifest}

    source = create_source(config, limits)

    with mock.patch.dict(os.environ, {"DEPLOYMENT_MODE": deployment_mode}, clear=False):
        output_data = read_stream(
            source, config, catalog, _A_PER_PARTITION_STATE, limits
        ).record.data
        if expected_error:
            assert (
                len(output_data["logs"]) > 0
            ), "Expected at least one log message with the expected error"
            error_message = output_data["logs"][0]
            assert error_message["level"] == "ERROR"
            assert expected_error in error_message["stacktrace"]


def test_read_stream_exception_with_secrets() -> None:
    # Define the test parameters
    config = {"__injected_declarative_manifest": "test_manifest", "api_key": "super_secret_key"}
    catalog = ConfiguredAirbyteCatalog(
        streams=[
            ConfiguredAirbyteStream(
                stream=AirbyteStream(
                    name=_stream_name, json_schema={}, supported_sync_modes=[SyncMode.full_refresh]
                ),
                sync_mode=SyncMode.full_refresh,
                destination_sync_mode=DestinationSyncMode.append,
            )
        ]
    )
    state = []
    limits = TestReadLimits()

    # Add the secret to be filtered
    update_secrets([config["api_key"]])

    # Mock the source
    mock_source = MagicMock()

    # Patch the handler to raise an exception
    with patch(
        "airbyte_cdk.connector_builder.message_grouper.MessageGrouper.get_message_groups"
    ) as mock_handler:
        mock_handler.side_effect = Exception("Test exception with secret key: super_secret_key")

        # Call the read_stream function and check for the correct error message
        response = read_stream(mock_source, config, catalog, state, limits)

        # Check if the error message contains the filtered secret
        filtered_message = filter_secrets("Test exception with secret key: super_secret_key")
        assert response.type == Type.TRACE
        assert filtered_message in response.trace.error.message
        assert "super_secret_key" not in response.trace.error.message
