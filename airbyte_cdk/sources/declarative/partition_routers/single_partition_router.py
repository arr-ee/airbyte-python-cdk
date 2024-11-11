#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#
from __future__ import annotations

from dataclasses import InitVar, dataclass
from typing import TYPE_CHECKING, Any

from airbyte_cdk.sources.declarative.partition_routers.partition_router import PartitionRouter
from airbyte_cdk.sources.types import StreamSlice, StreamState


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass
class SinglePartitionRouter(PartitionRouter):
    """Partition router returning only a stream slice"""

    parameters: InitVar[Mapping[str, Any]]

    def get_request_params(
        self,
        stream_state: StreamState | None = None,  # noqa: ARG002  (unused)
        stream_slice: StreamSlice | None = None,  # noqa: ARG002  (unused)
        next_page_token: Mapping[str, Any] | None = None,  # noqa: ARG002  (unused)
    ) -> Mapping[str, Any]:
        return {}

    def get_request_headers(
        self,
        stream_state: StreamState | None = None,  # noqa: ARG002  (unused)
        stream_slice: StreamSlice | None = None,  # noqa: ARG002  (unused)
        next_page_token: Mapping[str, Any] | None = None,  # noqa: ARG002  (unused)
    ) -> Mapping[str, Any]:
        return {}

    def get_request_body_data(
        self,
        stream_state: StreamState | None = None,  # noqa: ARG002  (unused)
        stream_slice: StreamSlice | None = None,  # noqa: ARG002  (unused)
        next_page_token: Mapping[str, Any] | None = None,  # noqa: ARG002  (unused)
    ) -> Mapping[str, Any]:
        return {}

    def get_request_body_json(
        self,
        stream_state: StreamState | None = None,  # noqa: ARG002  (unused)
        stream_slice: StreamSlice | None = None,  # noqa: ARG002  (unused)
        next_page_token: Mapping[str, Any] | None = None,  # noqa: ARG002  (unused)
    ) -> Mapping[str, Any]:
        return {}

    def stream_slices(self) -> Iterable[StreamSlice]:
        yield StreamSlice(partition={}, cursor_slice={})

    def set_initial_state(self, stream_state: StreamState) -> None:
        """SinglePartitionRouter doesn't have parent streams"""
        pass

    def get_stream_state(self) -> Mapping[str, StreamState] | None:
        """SinglePartitionRouter doesn't have parent streams"""
        pass
