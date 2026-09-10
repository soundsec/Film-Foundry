"""Internal contracts for film-side spatial fields.

The contracts are intentionally small and internal.  They describe where a
field acts, how its global work grid maps to the frame, and whether it may own
persistent state.  They do not make every scalar image interchangeable: stage
and quantity remain explicit so exposure, development-rate, and density fields
cannot silently cross pipeline boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


SPATIAL_FIELD_CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class SpatialFieldPlan:
    """Static semantic and resource declaration for one spatial effect."""

    key: str
    stage: str
    quantity: str
    field_kind: str
    requires_global_source: bool
    requires_tile_halo: bool
    random_field_policy: str = "none"
    persistent_state: bool = False
    disabled_identity_guarantee: bool = True

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("spatial field plan key must not be empty")
        if self.stage not in {
            "pre_latent_exposure",
            "development_formation",
            "final_medium_component_density",
        }:
            raise ValueError(f"unsupported spatial field stage: {self.stage}")
        if self.quantity not in {
            "exposure_addition",
            "development_rate_multiplier",
            "optical_density_delta",
        }:
            raise ValueError(f"unsupported spatial field quantity: {self.quantity}")
        if self.field_kind not in {
            "global_scalar",
            "global_layer",
            "tile_local",
            "coordinate_random",
        }:
            raise ValueError(f"unsupported spatial field kind: {self.field_kind}")
        if self.random_field_policy not in {
            "none",
            "fixed_seed_global",
            "coordinate_counter",
        }:
            raise ValueError(
                f"unsupported spatial random policy: {self.random_field_policy}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": SPATIAL_FIELD_CONTRACT_VERSION,
            "key": self.key,
            "stage": self.stage,
            "quantity": self.quantity,
            "field_kind": self.field_kind,
            "requires_global_source": self.requires_global_source,
            "requires_tile_halo": self.requires_tile_halo,
            "random_field_policy": self.random_field_policy,
            "persistent_state": self.persistent_state,
            "disabled_identity_guarantee": self.disabled_identity_guarantee,
        }


@dataclass(frozen=True, slots=True)
class GlobalFieldGrid:
    """One frame-global work grid with an explicit pixel-center mapping."""

    full_shape: tuple[int, int]
    work_shape: tuple[int, int]
    pixel_center_convention: str = "opencv_half_pixel"

    def __post_init__(self) -> None:
        if any(int(value) <= 0 for value in (*self.full_shape, *self.work_shape)):
            raise ValueError("spatial field grid dimensions must be positive")
        if self.pixel_center_convention != "opencv_half_pixel":
            raise ValueError(
                f"unsupported pixel-center convention: {self.pixel_center_convention}"
            )

    @property
    def reduced(self) -> bool:
        return self.work_shape != self.full_shape

    @property
    def scale_y(self) -> float:
        return self.work_shape[0] / self.full_shape[0]

    @property
    def scale_x(self) -> float:
        return self.work_shape[1] / self.full_shape[1]


@dataclass(frozen=True, slots=True)
class GlobalRateMultiplierField:
    """Lazy frame-global scalar multiplier held on one reduced work grid.

    Row evaluation follows the half-pixel center convention explicitly, so a
    full request and any partition of row requests produce the same float32
    values. The field never estimates statistics inside a material tile.
    """

    work_field: np.ndarray
    grid: GlobalFieldGrid

    def __post_init__(self) -> None:
        field = np.asarray(self.work_field, dtype=np.float32)
        if field.ndim != 2 or field.shape != self.grid.work_shape:
            raise ValueError(
                "global rate work field must match the declared work grid"
            )
        minimum = float(np.min(field))
        maximum = float(np.max(field))
        if (
            not np.isfinite(minimum)
            or not np.isfinite(maximum)
            or minimum < 0.0
        ):
            raise ValueError("global rate multiplier must be finite and non-negative")
        field.setflags(write=False)
        object.__setattr__(self, "work_field", field)

    @property
    def shape(self) -> tuple[int, int]:
        return self.grid.full_shape

    @staticmethod
    def _axis_coordinates(output_indices: np.ndarray, source_size: int, target_size: int):
        source = (output_indices.astype(np.float32) + 0.5) * (
            float(source_size) / float(target_size)
        ) - 0.5
        lower = np.floor(source).astype(np.int32)
        weight = source - lower.astype(np.float32)
        upper = lower + 1
        np.clip(lower, 0, source_size - 1, out=lower)
        np.clip(upper, 0, source_size - 1, out=upper)
        return lower, upper, weight.astype(np.float32, copy=False)

    def slice_rows(self, start: int, stop: int) -> np.ndarray:
        full_h, full_w = self.grid.full_shape
        work_h, work_w = self.grid.work_shape
        start, stop = int(start), int(stop)
        if start < 0 or stop < start or stop > full_h:
            raise ValueError("global rate row slice lies outside the frame")
        if self.grid.work_shape == self.grid.full_shape:
            return np.asarray(self.work_field[start:stop], dtype=np.float32)

        x0, x1, wx = self._axis_coordinates(
            np.arange(full_w, dtype=np.int32), work_w, full_w
        )
        y0, y1, wy = self._axis_coordinates(
            np.arange(start, stop, dtype=np.int32), work_h, full_h
        )
        one_minus_wx = 1.0 - wx
        output = np.empty((stop - start, full_w), dtype=np.float32)
        top = np.empty(full_w, dtype=np.float32)
        bottom = np.empty(full_w, dtype=np.float32)
        for local_row, (lower_y, upper_y, row_weight) in enumerate(
            zip(y0, y1, wy)
        ):
            np.multiply(self.work_field[lower_y, x0], one_minus_wx, out=top)
            top += self.work_field[lower_y, x1] * wx
            np.multiply(self.work_field[upper_y, x0], one_minus_wx, out=bottom)
            bottom += self.work_field[upper_y, x1] * wx
            np.multiply(top, 1.0 - row_weight, out=output[local_row])
            output[local_row] += bottom * row_weight
        return output


@dataclass(frozen=True, slots=True)
class GlobalLayerRateMultiplierField:
    """Lazy frame-global material-layer rate multipliers on one work grid.

    Each layer remains a scalar optical/chemical field.  They share an exact
    pixel-center mapping but are reconstructed only for the material rows that
    are currently consumed.  This is intentionally distinct from an RGB image:
    the final axis addresses emulsion/material layers.
    """

    layer_fields: tuple[GlobalRateMultiplierField, ...]

    def __post_init__(self) -> None:
        fields = tuple(self.layer_fields)
        if len(fields) not in {1, 3}:
            raise ValueError("global layer rate field requires one or three layers")
        if not all(isinstance(field, GlobalRateMultiplierField) for field in fields):
            raise ValueError("global layer rate fields must use scalar global fields")
        grid = fields[0].grid
        if any(field.grid != grid for field in fields[1:]):
            raise ValueError("global layer rate fields must share one work grid")
        object.__setattr__(self, "layer_fields", fields)

    @property
    def grid(self) -> GlobalFieldGrid:
        return self.layer_fields[0].grid

    @property
    def shape(self) -> tuple[int, int, int]:
        return (*self.grid.full_shape, len(self.layer_fields))

    def slice_rows(self, start: int, stop: int) -> np.ndarray:
        start, stop = int(start), int(stop)
        full_h, full_w = self.grid.full_shape
        if start < 0 or stop < start or stop > full_h:
            raise ValueError("global layer rate row slice lies outside the frame")
        rows = np.empty(
            (stop - start, full_w, len(self.layer_fields)),
            dtype=np.float32,
        )
        for layer, field in enumerate(self.layer_fields):
            rows[..., layer] = field.slice_rows(start, stop)
        return rows


@dataclass(frozen=True, slots=True)
class StepDevelopmentRateField:
    """Compose general unevenness with a first-development-only multiplier."""

    full_shape: tuple[int, int]
    first_step_label: str
    common_rate: np.ndarray | None = None
    first_step_rate: np.ndarray | GlobalRateMultiplierField | None = None
    step_rate_fields: tuple[
        tuple[
            str,
            str,
            np.ndarray | GlobalRateMultiplierField | GlobalLayerRateMultiplierField,
        ],
        ...,
    ] = ()

    def __post_init__(self) -> None:
        shape = (int(self.full_shape[0]), int(self.full_shape[1]))
        if any(value <= 0 for value in shape):
            raise ValueError("development rate field shape must be positive")
        if not str(self.first_step_label).strip():
            raise ValueError("first development step label must not be empty")
        for name, field in (
            ("common_rate", self.common_rate),
            ("first_step_rate", self.first_step_rate),
        ):
            if field is None:
                continue
            field_shape = getattr(field, "shape", None)
            if field_shape not in {shape, (*shape, 1), (*shape, 3)}:
                raise ValueError(f"{name} must match full development frame {shape}")
            if isinstance(field, np.ndarray):
                values = np.asarray(field, dtype=np.float32)
                minimum = float(np.min(values))
                maximum = float(np.max(values))
                if (
                    not np.isfinite(minimum)
                    or not np.isfinite(maximum)
                    or minimum < 0.0
                ):
                    raise ValueError(f"{name} must be finite and non-negative")
        entries = tuple(self.step_rate_fields)
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            if len(entry) != 3:
                raise ValueError("step rate entries must contain label, action, and field")
            label, action, field = entry
            key = (str(label), str(action))
            if not key[0] or not key[1]:
                raise ValueError("step rate label and action must not be empty")
            if key in seen:
                raise ValueError(f"duplicate step rate declaration for {key}")
            seen.add(key)
            field_shape = getattr(field, "shape", None)
            if field_shape not in {shape, (*shape, 1), (*shape, 3)}:
                raise ValueError(
                    f"step rate field {key} must match full development frame {shape}"
                )
            if isinstance(field, np.ndarray):
                values = np.asarray(field, dtype=np.float32)
                minimum = float(np.min(values))
                maximum = float(np.max(values))
                if (
                    not np.isfinite(minimum)
                    or not np.isfinite(maximum)
                    or minimum < 0.0
                ):
                    raise ValueError(f"step rate field {key} must be finite and non-negative")
        object.__setattr__(self, "full_shape", shape)
        object.__setattr__(self, "step_rate_fields", entries)

    @property
    def shape(self) -> tuple[int, int]:
        return self.full_shape

    @staticmethod
    def _rows(field, start: int, stop: int) -> np.ndarray | None:
        if field is None:
            return None
        if isinstance(field, (GlobalRateMultiplierField, GlobalLayerRateMultiplierField)):
            return field.slice_rows(start, stop)
        return np.asarray(field[start:stop], dtype=np.float32)

    @staticmethod
    def _multiply_rates(left: np.ndarray | None, right: np.ndarray | None):
        if left is None:
            return right
        if right is None:
            return left
        if left.ndim == 2 and right.ndim == 3:
            left = left[..., None]
        elif left.ndim == 3 and right.ndim == 2:
            right = right[..., None]
        return np.multiply(left, right, dtype=np.float32)

    def slice_rows(self, start: int, stop: int) -> "StepDevelopmentRateField":
        start, stop = int(start), int(stop)
        return StepDevelopmentRateField(
            full_shape=(stop - start, self.full_shape[1]),
            first_step_label=self.first_step_label,
            common_rate=self._rows(self.common_rate, start, stop),
            first_step_rate=self._rows(self.first_step_rate, start, stop),
            step_rate_fields=tuple(
                (label, action, self._rows(field, start, stop))
                for label, action, field in self.step_rate_fields
            ),
        )

    def rate_for_step(self, label: str, action: str) -> np.ndarray | None:
        common = self._rows(self.common_rate, 0, self.full_shape[0])
        result = common
        if str(label) == self.first_step_label:
            first = self._rows(self.first_step_rate, 0, self.full_shape[0])
            result = self._multiply_rates(result, first)
        for entry_label, entry_action, field in self.step_rate_fields:
            if str(label) == entry_label and str(action) == entry_action:
                rows = self._rows(field, 0, self.full_shape[0])
                result = self._multiply_rates(result, rows)
        return result


@dataclass(frozen=True, slots=True)
class LayerExposureAdditionField:
    """Lazy scalar spatial field with explicit material-layer coupling.

    One HxW return field is retained. Layer expansion happens only while the
    current material frame/tile is consumed, so a three-layer material does not
    require three persistent full-resolution arrays.
    """

    scalar_field: np.ndarray
    layer_weights: tuple[float, ...]
    strength: float = 1.0

    def __post_init__(self) -> None:
        field = np.asarray(self.scalar_field, dtype=np.float32)
        weights = np.asarray(self.layer_weights, dtype=np.float32)
        strength = float(self.strength)
        if field.ndim != 2 or field.size == 0:
            raise ValueError("layer exposure scalar field must be a non-empty HxW array")
        field_minimum = float(np.min(field))
        field_maximum = float(np.max(field))
        if (
            not np.isfinite(field_minimum)
            or not np.isfinite(field_maximum)
            or field_minimum < 0.0
        ):
            raise ValueError("layer exposure scalar field must be finite and non-negative")
        if weights.ndim != 1 or weights.size not in {1, 3}:
            raise ValueError("layer exposure weights must contain one or three values")
        if not np.all(np.isfinite(weights)) or np.any(weights < 0.0):
            raise ValueError("layer exposure weights must be finite and non-negative")
        if not np.isfinite(strength) or strength < 0.0:
            raise ValueError("layer exposure strength must be finite and non-negative")
        maximum = float(weights.max(initial=0.0))
        normalized = (
            np.zeros_like(weights, dtype=np.float32)
            if maximum <= 1e-8
            else weights / maximum
        )
        field.setflags(write=False)
        object.__setattr__(self, "scalar_field", field)
        object.__setattr__(
            self,
            "layer_weights",
            tuple(float(value) for value in normalized),
        )
        object.__setattr__(self, "strength", strength)

    @property
    def shape(self) -> tuple[int, int, int]:
        return (*self.scalar_field.shape, len(self.layer_weights))

    def slice_rows(self, start: int, stop: int) -> "LayerExposureAdditionField":
        return LayerExposureAdditionField(
            self.scalar_field[int(start) : int(stop)],
            self.layer_weights,
            self.strength,
        )

    def add_scaled_to(
        self,
        target: np.ndarray,
        material_scale: np.ndarray,
    ) -> None:
        """Add to an HxWxL target using one reusable scalar tile buffer."""
        target = np.asarray(target, dtype=np.float32)
        expected_shape = self.shape
        if target.shape != expected_shape:
            raise ValueError(
                f"layer exposure target must have shape {expected_shape}, got {target.shape}"
            )
        scales = np.asarray(material_scale, dtype=np.float32).reshape(-1)
        if scales.shape != (expected_shape[-1],):
            raise ValueError(
                "material layer scale must match the layer exposure field count"
            )
        delta = np.empty_like(self.scalar_field, dtype=np.float32)
        for layer, weight in enumerate(self.layer_weights):
            np.multiply(self.scalar_field, weight, out=delta)
            np.multiply(delta, self.strength, out=delta)
            np.multiply(delta, scales[layer], out=delta)
            np.add(target[..., layer], delta, out=target[..., layer])


@dataclass(frozen=True, slots=True)
class GlobalLayerExposureAdditionField:
    """Layer-specific exposure additions retained on one bounded work grid.

    Each layer owns a scalar work-grid field. Full-resolution rows are only
    reconstructed while the corresponding material rows are consumed. This
    preserves source colour/layer differences without retaining another
    full-frame HxWxL exposure image.
    """

    layer_fields: tuple[GlobalRateMultiplierField, ...]
    strength: float = 1.0

    def __post_init__(self) -> None:
        fields = tuple(self.layer_fields)
        if len(fields) not in {1, 3}:
            raise ValueError("global layer exposure requires one or three fields")
        if not all(isinstance(field, GlobalRateMultiplierField) for field in fields):
            raise ValueError("global layer exposure fields must use one global grid")
        grid = fields[0].grid
        if any(field.grid != grid for field in fields[1:]):
            raise ValueError("global layer exposure fields must share one grid")
        strength = float(self.strength)
        if not np.isfinite(strength) or strength < 0.0:
            raise ValueError("global layer exposure strength must be finite and non-negative")
        object.__setattr__(self, "layer_fields", fields)
        object.__setattr__(self, "strength", strength)

    @property
    def grid(self) -> GlobalFieldGrid:
        return self.layer_fields[0].grid

    @property
    def shape(self) -> tuple[int, int, int]:
        return (*self.grid.full_shape, len(self.layer_fields))

    @property
    def work_shape(self) -> tuple[int, int, int]:
        return (*self.grid.work_shape, len(self.layer_fields))

    def slice_rows(self, start: int, stop: int) -> "GlobalLayerExposureAdditionSlice":
        return GlobalLayerExposureAdditionSlice(self, int(start), int(stop))

    def _add_rows_to(
        self,
        target: np.ndarray,
        material_scale: np.ndarray,
        start: int,
        stop: int,
    ) -> None:
        target = np.asarray(target, dtype=np.float32)
        full_h, full_w = self.grid.full_shape
        start, stop = int(start), int(stop)
        expected_shape = (stop - start, full_w, len(self.layer_fields))
        if start < 0 or stop < start or stop > full_h:
            raise ValueError("global layer exposure row slice lies outside the frame")
        if target.shape != expected_shape:
            raise ValueError(
                f"global layer exposure target must have shape {expected_shape}, got {target.shape}"
            )
        scales = np.asarray(material_scale, dtype=np.float32).reshape(-1)
        if scales.shape != (len(self.layer_fields),):
            raise ValueError("material layer scale must match global exposure layers")
        for layer, field in enumerate(self.layer_fields):
            rows = field.slice_rows(start, stop)
            delta = np.multiply(
                rows,
                np.float32(self.strength * float(scales[layer])),
                dtype=np.float32,
            )
            np.add(target[..., layer], delta, out=target[..., layer])

    def add_scaled_to(self, target: np.ndarray, material_scale: np.ndarray) -> None:
        self._add_rows_to(target, material_scale, 0, self.grid.full_shape[0])

    def resample_to_full_shape(
        self,
        full_shape: tuple[int, int],
    ) -> "GlobalLayerExposureAdditionField":
        """Resample the bounded declaration onto another complete frame grid.

        This is used by reduced provisional kinetics. It never expands through
        the original full frame before reaching the requested work grid.
        """
        target_h, target_w = (int(full_shape[0]), int(full_shape[1]))
        if target_h <= 0 or target_w <= 0:
            raise ValueError("global layer exposure target grid must be positive")
        if (target_h, target_w) == self.grid.full_shape:
            return self
        target_grid = GlobalFieldGrid(
            full_shape=(target_h, target_w),
            work_shape=(target_h, target_w),
        )
        interpolation = (
            cv2.INTER_AREA
            if target_h <= self.grid.work_shape[0]
            and target_w <= self.grid.work_shape[1]
            else cv2.INTER_LINEAR
        )
        target_fields = tuple(
            GlobalRateMultiplierField(
                cv2.resize(
                    field.work_field,
                    (target_w, target_h),
                    interpolation=interpolation,
                ).astype(np.float32, copy=False),
                target_grid,
            )
            for field in self.layer_fields
        )
        return GlobalLayerExposureAdditionField(target_fields, self.strength)


@dataclass(frozen=True, slots=True)
class GlobalLayerExposureAdditionSlice:
    """Cheap row view over a global layer exposure declaration."""

    parent: GlobalLayerExposureAdditionField
    start: int
    stop: int

    def __post_init__(self) -> None:
        start, stop = int(self.start), int(self.stop)
        if start < 0 or stop < start or stop > self.parent.grid.full_shape[0]:
            raise ValueError("global layer exposure slice lies outside the frame")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "stop", stop)

    @property
    def shape(self) -> tuple[int, int, int]:
        return (
            self.stop - self.start,
            self.parent.grid.full_shape[1],
            len(self.parent.layer_fields),
        )

    def slice_rows(self, start: int, stop: int) -> "GlobalLayerExposureAdditionSlice":
        start, stop = int(start), int(stop)
        if start < 0 or stop < start or stop > self.stop - self.start:
            raise ValueError("nested global layer exposure slice lies outside the view")
        return GlobalLayerExposureAdditionSlice(
            self.parent,
            self.start + start,
            self.start + stop,
        )

    def add_scaled_to(self, target: np.ndarray, material_scale: np.ndarray) -> None:
        self.parent._add_rows_to(
            target,
            material_scale,
            self.start,
            self.stop,
        )


@dataclass(frozen=True, slots=True)
class EdgeExposureAdditionField:
    """Lazy material-layer exposure entering from declared frame edges.

    Edge order is top, right, bottom, left. No scene pixels or image gradients
    are read: the scalar field is a pure function of frame geometry.
    """

    full_shape: tuple[int, int]
    layer_weights: tuple[float, ...]
    edge_weights: tuple[float, float, float, float]
    strength: float
    depth_scale: float
    row_chunk: int = 256

    def __post_init__(self) -> None:
        height, width = (int(self.full_shape[0]), int(self.full_shape[1]))
        layer_weights = np.asarray(self.layer_weights, dtype=np.float32)
        edge_weights = np.asarray(self.edge_weights, dtype=np.float32)
        if height <= 0 or width <= 0:
            raise ValueError("edge exposure frame dimensions must be positive")
        if layer_weights.ndim != 1 or layer_weights.size not in {1, 3}:
            raise ValueError("edge exposure layer weights must contain one or three values")
        if not np.all(np.isfinite(layer_weights)) or np.any(layer_weights < 0.0):
            raise ValueError("edge exposure layer weights must be finite and non-negative")
        if edge_weights.shape != (4,) or not np.all(np.isfinite(edge_weights)):
            raise ValueError("edge exposure weights must contain four finite values")
        if np.any(edge_weights < 0.0) or np.any(edge_weights > 1.0):
            raise ValueError("edge exposure weights must be between zero and one")
        if not np.isfinite(self.strength) or float(self.strength) < 0.0:
            raise ValueError("edge exposure strength must be finite and non-negative")
        if not np.isfinite(self.depth_scale) or float(self.depth_scale) <= 0.0:
            raise ValueError("edge exposure depth scale must be finite and positive")
        maximum = float(layer_weights.max(initial=0.0))
        normalized = (
            np.zeros_like(layer_weights)
            if maximum <= 1e-8
            else layer_weights / maximum
        )
        object.__setattr__(self, "full_shape", (height, width))
        object.__setattr__(self, "layer_weights", tuple(float(v) for v in normalized))
        object.__setattr__(self, "edge_weights", tuple(float(v) for v in edge_weights))
        object.__setattr__(self, "strength", float(self.strength))
        object.__setattr__(self, "depth_scale", float(self.depth_scale))
        object.__setattr__(self, "row_chunk", max(1, int(self.row_chunk)))

    @property
    def shape(self) -> tuple[int, int, int]:
        return (*self.full_shape, len(self.layer_weights))

    def _scalar_rows(self, start: int, stop: int) -> np.ndarray:
        height, width = self.full_shape
        if start < 0 or stop < start or stop > height:
            raise ValueError("edge exposure row slice lies outside the frame")
        depth_px = max(1.0, float(self.depth_scale) * float(min(height, width)))
        field = np.zeros((stop - start, width), dtype=np.float32)
        top, right, bottom, left = self.edge_weights
        if top > 0.0 or bottom > 0.0:
            rows = np.arange(start, stop, dtype=np.float32)[:, None]
            if top > 0.0:
                np.maximum(field, top * np.exp(-rows / depth_px), out=field)
            if bottom > 0.0:
                np.maximum(
                    field,
                    bottom * np.exp(-(float(height - 1) - rows) / depth_px),
                    out=field,
                )
        if left > 0.0 or right > 0.0:
            columns = np.arange(width, dtype=np.float32)[None, :]
            if left > 0.0:
                np.maximum(field, left * np.exp(-columns / depth_px), out=field)
            if right > 0.0:
                np.maximum(
                    field,
                    right * np.exp(-(float(width - 1) - columns) / depth_px),
                    out=field,
                )
        return field

    def slice_rows(self, start: int, stop: int) -> LayerExposureAdditionField:
        return LayerExposureAdditionField(
            self._scalar_rows(int(start), int(stop)),
            self.layer_weights,
            self.strength,
        )

    def add_scaled_to(self, target: np.ndarray, material_scale: np.ndarray) -> None:
        target = np.asarray(target, dtype=np.float32)
        if target.shape != self.shape:
            raise ValueError(
                f"edge exposure target must have shape {self.shape}, got {target.shape}"
            )
        scales = np.asarray(material_scale, dtype=np.float32).reshape(-1)
        if scales.shape != (self.shape[-1],):
            raise ValueError("material layer scale must match edge exposure layers")
        for start in range(0, self.full_shape[0], self.row_chunk):
            stop = min(start + self.row_chunk, self.full_shape[0])
            # Reuse the scalar-field implementation so full-frame and external
            # material tiling execute the same float32 operation order.
            self.slice_rows(start, stop).add_scaled_to(
                target[start:stop],
                scales,
            )


@dataclass(frozen=True, slots=True)
class CompositeLayerExposureAdditionField:
    """Sequential sum of compatible lazy material-layer exposure fields."""

    fields: tuple[object, ...]

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("composite layer exposure requires at least one field")
        shapes = tuple(getattr(field, "shape", None) for field in self.fields)
        if any(shape != shapes[0] for shape in shapes):
            raise ValueError("composite layer exposure fields must share one shape")

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.fields[0].shape

    def slice_rows(self, start: int, stop: int) -> "CompositeLayerExposureAdditionField":
        return CompositeLayerExposureAdditionField(
            tuple(field.slice_rows(start, stop) for field in self.fields)
        )

    def add_scaled_to(self, target: np.ndarray, material_scale: np.ndarray) -> None:
        for field in self.fields:
            field.add_scaled_to(target, material_scale)


LAZY_LAYER_EXPOSURE_FIELD_TYPES = (
    LayerExposureAdditionField,
    GlobalLayerExposureAdditionField,
    GlobalLayerExposureAdditionSlice,
    EdgeExposureAdditionField,
    CompositeLayerExposureAdditionField,
)


def combine_layer_exposure_addition_fields(*fields):
    active = tuple(field for field in fields if field is not None)
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    return CompositeLayerExposureAdditionField(active)


def resample_layer_exposure_addition(field, grid: GlobalFieldGrid):
    """Project any declared layer-exposure addition onto a global work grid."""

    if field is None or grid.work_shape == grid.full_shape:
        return field
    work_h, work_w = grid.work_shape
    if isinstance(field, LayerExposureAdditionField):
        scalar = cv2.resize(
            field.scalar_field,
            (work_w, work_h),
            interpolation=cv2.INTER_AREA,
        ).astype(np.float32)
        return LayerExposureAdditionField(scalar, field.layer_weights, field.strength)
    if isinstance(field, EdgeExposureAdditionField):
        return EdgeExposureAdditionField(
            full_shape=grid.work_shape,
            layer_weights=field.layer_weights,
            edge_weights=field.edge_weights,
            strength=field.strength,
            depth_scale=field.depth_scale,
            row_chunk=min(field.row_chunk, work_h),
        )
    if isinstance(field, GlobalLayerExposureAdditionField):
        return field.resample_to_full_shape(grid.work_shape)
    if isinstance(field, GlobalLayerExposureAdditionSlice):
        if field.shape[:2] != grid.full_shape:
            raise ValueError(
                "layer exposure slice must match the declared full work grid"
            )
        dense = np.zeros(field.shape, dtype=np.float32)
        field.add_scaled_to(dense, np.ones(field.shape[-1], dtype=np.float32))
        return cv2.resize(
            dense,
            (work_w, work_h),
            interpolation=cv2.INTER_AREA,
        ).astype(np.float32)
    if isinstance(field, CompositeLayerExposureAdditionField):
        return combine_layer_exposure_addition_fields(
            *(resample_layer_exposure_addition(item, grid) for item in field.fields)
        )
    values = np.asarray(field, dtype=np.float32)
    if values.ndim != 3 or values.shape[:2] != grid.full_shape:
        raise ValueError("layer exposure addition must match the declared full work grid")
    return cv2.resize(
        values,
        (work_w, work_h),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float32)


def global_field_grid(
    image_shape: tuple[int, ...],
    work_long_edge: int | None,
) -> GlobalFieldGrid:
    """Resolve one frame-global work grid without changing aspect ratio."""
    if len(image_shape) < 2:
        raise ValueError("image_shape must contain height and width")
    height, width = int(image_shape[0]), int(image_shape[1])
    if height <= 0 or width <= 0:
        raise ValueError("image dimensions must be positive")
    if work_long_edge is None:
        work_shape = (height, width)
    else:
        edge = int(work_long_edge)
        if edge <= 0:
            raise ValueError("work_long_edge must be positive or None")
        scale = min(1.0, float(edge) / float(max(height, width)))
        work_shape = (
            max(1, int(round(height * scale))),
            max(1, int(round(width * scale))),
        )
    return GlobalFieldGrid(full_shape=(height, width), work_shape=work_shape)


def resample_global_scalar_field(
    field: np.ndarray,
    grid: GlobalFieldGrid,
    *,
    to_work_grid: bool,
) -> np.ndarray:
    """Resample a frame-global scalar field using one fixed coordinate map.

    Downsampling uses area interpolation; upsampling uses linear interpolation.
    This preserves the legacy warm-halation numerical path while making its
    global-grid ownership explicit.
    """
    values = np.asarray(field, dtype=np.float32)
    expected = grid.full_shape if to_work_grid else grid.work_shape
    target = grid.work_shape if to_work_grid else grid.full_shape
    if values.ndim != 2 or values.shape != expected:
        raise ValueError(
            f"global scalar field shape {values.shape} does not match {expected}"
        )
    if expected == target:
        return values
    interpolation = cv2.INTER_AREA if to_work_grid else cv2.INTER_LINEAR
    return cv2.resize(
        values,
        (target[1], target[0]),
        interpolation=interpolation,
    )
