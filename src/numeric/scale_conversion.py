"""Decimal-safe magnitude scale and unit conversion contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Union

from src.evidence.schemas import CanonicalDecimal, Scale
from src.understanding.requested_scale_unit_parser import RequestedScale
from src.understanding.schemas import SchemaValidationError


class ScaleConversionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"


class ScaleConversionFailureCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    SOURCE_SCALE_REQUIRED = "SOURCE_SCALE_REQUIRED"
    UNSUPPORTED_SCALE_CONVERSION = "UNSUPPORTED_SCALE_CONVERSION"
    UNSUPPORTED_UNIT_CONVERSION = "UNSUPPORTED_UNIT_CONVERSION"


_MAGNITUDE_FACTORS = {
    Scale.RAW: Decimal("1"),
    Scale.THOUSAND: Decimal("1000"),
    Scale.MILLION: Decimal("1000000"),
    Scale.BILLION: Decimal("1000000000"),
}

RequestedOutputScale = Union[Scale, RequestedScale]


@dataclass(frozen=True)
class ScaleConversionResult:
    status: ScaleConversionStatus
    canonical_value: Optional[CanonicalDecimal]
    output_scale: Optional[Scale]
    output_unit: Optional[str]
    failure_code: Optional[ScaleConversionFailureCode]
    failure_message: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScaleConversionStatus):
            raise SchemaValidationError("status must be a ScaleConversionStatus")
        if self.canonical_value is not None and not isinstance(
            self.canonical_value, CanonicalDecimal
        ):
            raise SchemaValidationError(
                "canonical_value must be a CanonicalDecimal or null"
            )
        if self.output_scale is not None and not isinstance(self.output_scale, Scale):
            raise SchemaValidationError("output_scale must be a Scale or null")
        if self.output_unit is not None and not isinstance(self.output_unit, str):
            raise SchemaValidationError("output_unit must be a string or null")
        if self.failure_code is not None and not isinstance(
            self.failure_code, ScaleConversionFailureCode
        ):
            raise SchemaValidationError(
                "failure_code must be a ScaleConversionFailureCode or null"
            )
        if self.failure_message is not None and (
            not isinstance(self.failure_message, str) or not self.failure_message
        ):
            raise SchemaValidationError(
                "failure_message must be a non-empty string or null"
            )
        if self.status is ScaleConversionStatus.SUCCESS:
            if self.canonical_value is None:
                raise SchemaValidationError("SUCCESS requires canonical_value")
            if self.failure_code is not None or self.failure_message is not None:
                raise SchemaValidationError("SUCCESS cannot contain failure details")
        elif (
            self.canonical_value is not None
            or self.output_scale is not None
            or self.output_unit is not None
            or self.failure_code is None
            or self.failure_message is None
        ):
            raise SchemaValidationError(
                "REJECTED requires only failure_code and failure_message"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "canonical_value": self.canonical_value,
            "output_scale": (
                None if self.output_scale is None else self.output_scale.value
            ),
            "output_unit": self.output_unit,
            "failure_code": (
                None if self.failure_code is None else self.failure_code.value
            ),
            "failure_message": self.failure_message,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ScaleConversionResult":
        if not isinstance(value, Mapping):
            raise SchemaValidationError("ScaleConversionResult must be an object")
        expected = {
            "status",
            "canonical_value",
            "output_scale",
            "output_unit",
            "failure_code",
            "failure_message",
        }
        if set(value) != expected:
            raise SchemaValidationError(
                "ScaleConversionResult must contain its exact schema"
            )
        try:
            status = ScaleConversionStatus(value["status"])
            output_scale = (
                None
                if value["output_scale"] is None
                else Scale(value["output_scale"])
            )
            failure_code = (
                None
                if value["failure_code"] is None
                else ScaleConversionFailureCode(value["failure_code"])
            )
        except (TypeError, ValueError) as error:
            raise SchemaValidationError(
                "ScaleConversionResult contains an invalid enum"
            ) from error
        canonical_value = value["canonical_value"]
        if canonical_value is not None:
            canonical_value = CanonicalDecimal(canonical_value)
        return cls(
            status=status,
            canonical_value=canonical_value,
            output_scale=output_scale,
            output_unit=value["output_unit"],
            failure_code=failure_code,
            failure_message=value["failure_message"],
        )


def _rejected(
    code: ScaleConversionFailureCode, message: str
) -> ScaleConversionResult:
    return ScaleConversionResult(
        status=ScaleConversionStatus.REJECTED,
        canonical_value=None,
        output_scale=None,
        output_unit=None,
        failure_code=code,
        failure_message=message,
    )


def _normalized_requested_scale(
    requested_scale: Optional[RequestedOutputScale],
) -> Optional[Scale]:
    if requested_scale is None:
        return None
    if isinstance(requested_scale, Scale):
        return requested_scale
    if isinstance(requested_scale, RequestedScale):
        return Scale(requested_scale.value)
    raise TypeError("requested_output_scale must be a Scale, RequestedScale, or null")


def _serialize_decimal(value: Decimal) -> CanonicalDecimal:
    if value.is_zero():
        return CanonicalDecimal("0")
    serialized = format(value, "f")
    if "." in serialized:
        serialized = serialized.rstrip("0").rstrip(".")
    return CanonicalDecimal(serialized)


def convert_canonical_scale(
    canonical_value: CanonicalDecimal,
    *,
    source_scale: Optional[Scale],
    source_unit: Optional[str] = None,
    requested_output_scale: Optional[RequestedOutputScale] = None,
    requested_output_unit: Optional[str] = None,
) -> ScaleConversionResult:
    """Convert scale metadata exactly, without mutating evidence or bindings."""
    if not isinstance(canonical_value, CanonicalDecimal):
        return _rejected(
            ScaleConversionFailureCode.INVALID_INPUT,
            "canonical_value must be a CanonicalDecimal",
        )
    if source_scale is not None and not isinstance(source_scale, Scale):
        return _rejected(
            ScaleConversionFailureCode.INVALID_INPUT,
            "source_scale must be a Scale or null",
        )
    if source_unit is not None and not isinstance(source_unit, str):
        return _rejected(
            ScaleConversionFailureCode.INVALID_INPUT,
            "source_unit must be a string or null",
        )
    if requested_output_unit is not None and not isinstance(
        requested_output_unit, str
    ):
        return _rejected(
            ScaleConversionFailureCode.INVALID_INPUT,
            "requested_output_unit must be a string or null",
        )
    try:
        output_scale = _normalized_requested_scale(requested_output_scale)
    except (TypeError, ValueError) as error:
        return _rejected(ScaleConversionFailureCode.INVALID_INPUT, str(error))

    if requested_output_unit is not None and requested_output_unit != source_unit:
        return _rejected(
            ScaleConversionFailureCode.UNSUPPORTED_UNIT_CONVERSION,
            "requested unit must equal the source unit; FX conversion is unsupported",
        )

    output_unit = source_unit
    if output_scale is None:
        return ScaleConversionResult(
            status=ScaleConversionStatus.SUCCESS,
            canonical_value=canonical_value,
            output_scale=source_scale,
            output_unit=output_unit,
            failure_code=None,
            failure_message=None,
        )
    if source_scale is None:
        return _rejected(
            ScaleConversionFailureCode.SOURCE_SCALE_REQUIRED,
            "source_scale is required when output scale conversion is requested",
        )
    if source_scale is Scale.PERCENT or output_scale is Scale.PERCENT:
        if source_scale is output_scale is Scale.PERCENT:
            return ScaleConversionResult(
                status=ScaleConversionStatus.SUCCESS,
                canonical_value=canonical_value,
                output_scale=Scale.PERCENT,
                output_unit=output_unit,
                failure_code=None,
                failure_message=None,
            )
        return _rejected(
            ScaleConversionFailureCode.UNSUPPORTED_SCALE_CONVERSION,
            "PERCENT cannot be converted to or from a magnitude scale",
        )
    if source_scale not in _MAGNITUDE_FACTORS or output_scale not in _MAGNITUDE_FACTORS:
        return _rejected(
            ScaleConversionFailureCode.UNSUPPORTED_SCALE_CONVERSION,
            "only RAW, THOUSAND, MILLION, and BILLION magnitude scales are supported",
        )
    if source_scale is output_scale:
        converted = canonical_value
    else:
        source_factor = _MAGNITUDE_FACTORS[source_scale]
        output_factor = _MAGNITUDE_FACTORS[output_scale]
        decimal_value = Decimal(canonical_value)
        digit_count = len(decimal_value.as_tuple().digits)
        with localcontext() as context:
            context.prec = max(28, digit_count + 20)
            converted = _serialize_decimal(
                decimal_value * source_factor / output_factor
            )
    return ScaleConversionResult(
        status=ScaleConversionStatus.SUCCESS,
        canonical_value=converted,
        output_scale=output_scale,
        output_unit=output_unit,
        failure_code=None,
        failure_message=None,
    )
