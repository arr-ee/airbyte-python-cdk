#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from airbyte_cdk.connector import BaseConnector, DefaultConnectorMixin, TConfig
from airbyte_cdk.models import (
    AirbyteCatalog,
    AirbyteMessage,
    AirbyteStateMessage,
    AirbyteStateMessageSerializer,
    ConfiguredAirbyteCatalog,
    ConfiguredAirbyteCatalogSerializer,
)


if TYPE_CHECKING:
    import logging


TState = TypeVar("TState")
TCatalog = TypeVar("TCatalog")


class ExperimentalClassWarning(DeprecationWarning):
    pass


class BaseSource(BaseConnector[TConfig], ABC, Generic[TConfig, TState, TCatalog]):
    @abstractmethod
    def read_state(self, state_path: str) -> TState: ...

    @abstractmethod
    def read_catalog(self, catalog_path: str) -> TCatalog: ...

    @abstractmethod
    def read(
        self,
        logger: logging.Logger,
        config: TConfig,
        catalog: TCatalog,
        state: TState | None = None,
    ) -> Iterable[AirbyteMessage]:
        """Returns a generator of the AirbyteMessages generated by reading the source with the given configuration, catalog, and state."""

    @abstractmethod
    def discover(self, logger: logging.Logger, config: TConfig) -> AirbyteCatalog:
        """Returns an AirbyteCatalog representing the available streams and fields in this integration. For example, given valid credentials to a
        Postgres database, returns an Airbyte catalog where each postgres table is a stream, and each table column is a field.
        """


class Source(
    DefaultConnectorMixin,
    BaseSource[Mapping[str, Any], list[AirbyteStateMessage], ConfiguredAirbyteCatalog],
    ABC,
):
    # can be overridden to change an input state.
    @classmethod
    def read_state(cls, state_path: str) -> list[AirbyteStateMessage]:
        """Retrieves the input state of a sync by reading from the specified JSON file. Incoming state can be deserialized into either
        a JSON object for legacy state input or as a list of AirbyteStateMessages for the per-stream state format. Regardless of the
        incoming input type, it will always be transformed and output as a list of AirbyteStateMessage(s).
        :param state_path: The filepath to where the stream states are located
        :return: The complete stream state based on the connector's previous sync
        """
        parsed_state_messages = []
        if state_path:
            state_obj = BaseConnector._read_json_file(state_path)  # noqa: SLF001  (private member)
            if state_obj:
                for state in state_obj:  # type: ignore  # `isinstance(state_obj, List)` ensures that this is a list
                    parsed_message = AirbyteStateMessageSerializer.load(state)
                    if (
                        not parsed_message.stream
                        and not parsed_message.data
                        and not parsed_message.global_
                    ):
                        raise ValueError(
                            "AirbyteStateMessage should contain either a stream, global, or state field"
                        )
                    parsed_state_messages.append(parsed_message)
        return parsed_state_messages

    # can be overridden to change an input catalog
    @classmethod
    def read_catalog(cls, catalog_path: str) -> ConfiguredAirbyteCatalog:
        return ConfiguredAirbyteCatalogSerializer.load(cls._read_json_file(catalog_path))

    @property
    def name(self) -> str:
        """Source name"""
        return self.__class__.__name__
