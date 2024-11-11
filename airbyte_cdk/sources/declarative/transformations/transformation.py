#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from airbyte_cdk.sources.types import Config, StreamSlice, StreamState


@dataclass
class RecordTransformation:  # noqa: PLW1641  # missing __hash__ method
    """Implementations of this class define transformations that can be applied to records of a stream."""

    @abstractmethod
    def transform(
        self,
        record: dict[str, Any],
        config: Config | None = None,
        stream_state: StreamState | None = None,
        stream_slice: StreamSlice | None = None,
    ) -> None:
        """Transform a record by adding, deleting, or mutating fields directly from the record reference passed in argument.

        :param record: The input record to be transformed
        :param config: The user-provided configuration as specified by the source's spec
        :param stream_state: The stream state
        :param stream_slice: The stream slice
        :return: The transformed record
        """

    def __eq__(self, other: object) -> bool:
        return other.__dict__ == self.__dict__
