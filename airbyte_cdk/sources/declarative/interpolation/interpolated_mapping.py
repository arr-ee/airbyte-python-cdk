#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from typing import TYPE_CHECKING, Any

from airbyte_cdk.sources.declarative.interpolation.jinja import JinjaInterpolation
from airbyte_cdk.sources.types import Config


@dataclass
class InterpolatedMapping:
    """Wrapper around a Mapping[str, str] where both the keys and values are to be interpolated.

    Attributes:
        mapping (Mapping[str, str]): to be evaluated
    """

    mapping: Mapping[str, str]
    parameters: InitVar[Mapping[str, Any]]

    def __post_init__(self, parameters: Mapping[str, Any] | None) -> None:
        self._interpolation = JinjaInterpolation()
        self._parameters = parameters

    def eval(self, config: Config, **additional_parameters: Any) -> dict[str, Any]:  # noqa: ANN401  (any-type)
        """Wrapper around a Mapping[str, str] that allows for both keys and values to be interpolated.

        :param config: The user-provided configuration as specified by the source's spec
        :param additional_parameters: Optional parameters used for interpolation
        :return: The interpolated mapping
        """
        valid_key_types = additional_parameters.pop("valid_key_types", (str,))
        valid_value_types = additional_parameters.pop("valid_value_types", None)
        return {
            self._interpolation.eval(
                name,
                config,
                valid_types=valid_key_types,
                parameters=self._parameters,
                **additional_parameters,
            ): self._eval(value, config, valid_types=valid_value_types, **additional_parameters)
            for name, value in self.mapping.items()
        }

    def _eval(self, value: str, config: Config, **kwargs: Any) -> Any:  # noqa: ANN401  (any-type)
        # The values in self._mapping can be of Any type
        # We only want to interpolate them if they are strings
        if isinstance(value, str):
            return self._interpolation.eval(value, config, parameters=self._parameters, **kwargs)
        return value
