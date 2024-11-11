# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
# ruff: noqa: A005  # Shadows built-in 'types' module

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, ValuesView
from typing import Any


# A FieldPointer designates a path to a field inside a mapping. For example, retrieving ["k1", "k1.2"] in the object {"k1" :{"k1.2":
# "hello"}] returns "hello"
FieldPointer = list[str]
Config = Mapping[str, Any]
ConnectionDefinition = Mapping[str, Any]
StreamState = Mapping[str, Any]


class Record(Mapping[str, Any]):  # noqa: PLW1641  # missing __hash__ method
    def __init__(
        self,
        data: Mapping[str, Any],
        associated_slice: StreamSlice | None,
    ) -> None:
        self._data = data
        self._associated_slice = associated_slice

    @property
    def data(self) -> Mapping[str, Any]:
        return self._data

    @property
    def associated_slice(self) -> StreamSlice | None:
        return self._associated_slice

    def __repr__(self) -> str:
        return repr(self._data)

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401  (any-type)
        return self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Any:  # noqa: ANN401  (any-type)
        return iter(self._data)

    def __contains__(self, item: object) -> bool:
        return item in self._data

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Record):
            # noinspection PyProtectedMember
            return self._data == other._data
        return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)


class StreamSlice(Mapping[str, Any]):  # noqa: PLW1641  # missing __hash__ method
    def __init__(
        self,
        *,
        partition: Mapping[str, Any],
        cursor_slice: Mapping[str, Any],
        extra_fields: Mapping[str, Any] | None = None,
    ) -> None:
        """:param partition: The partition keys representing a unique partition in the stream.
        :param cursor_slice: The incremental cursor slice keys, such as dates or pagination tokens.
        :param extra_fields: Additional fields that should not be part of the partition but passed along, such as metadata from the parent stream.
        """
        self._partition = partition
        self._cursor_slice = cursor_slice
        self._extra_fields = extra_fields or {}

        # Ensure that partition keys do not overlap with cursor slice keys
        if partition.keys() & cursor_slice.keys():
            raise ValueError("Keys for partition and incremental sync cursor should not overlap")

        self._stream_slice = dict(partition) | dict(cursor_slice)

    @property
    def partition(self) -> Mapping[str, Any]:
        """Returns the partition portion of the stream slice."""
        p = self._partition
        while isinstance(p, StreamSlice):
            p = p.partition
        return p

    @property
    def cursor_slice(self) -> Mapping[str, Any]:
        """Returns the cursor slice portion of the stream slice."""
        c = self._cursor_slice
        while isinstance(c, StreamSlice):
            c = c.cursor_slice
        return c

    @property
    def extra_fields(self) -> Mapping[str, Any]:
        """Returns the extra fields that are not part of the partition."""
        return self._extra_fields

    def __repr__(self) -> str:
        return repr(self._stream_slice)

    def __setitem__(self, key: str, value: Any) -> None:  # noqa: ANN401  (any-type)
        raise ValueError("StreamSlice is immutable")

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401  (any-type)
        return self._stream_slice[key]

    def __len__(self) -> int:
        return len(self._stream_slice)

    def __iter__(self) -> Iterator[str]:
        return iter(self._stream_slice)

    def __contains__(self, item: Any) -> bool:  # noqa: ANN401  (any-type)
        return item in self._stream_slice

    def keys(self) -> KeysView[str]:
        return self._stream_slice.keys()

    def items(self) -> ItemsView[str, Any]:
        return self._stream_slice.items()

    def values(self) -> ValuesView[Any]:
        return self._stream_slice.values()

    def get(self, key: str, default: Any = None) -> Any | None:  # noqa: ANN401  (any-type)
        return self._stream_slice.get(key, default)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return self._stream_slice == other
        if isinstance(other, StreamSlice):
            # noinspection PyProtectedMember
            return self._partition == other._partition and self._cursor_slice == other._cursor_slice
        return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __json_serializable__(self) -> Any:  # noqa: ANN401, PLW3201  (any-type, unexpected types)
        return self._stream_slice
