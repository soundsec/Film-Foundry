"""Material extent and projection of reference-frame spatial parameters.

The physical path gives existing relative radii a declared reference length;
it does not claim that the historical parameters were measured in micrometres.
Projection happens once before formation. Existing work grids still span the
same material extent, and scan settings never participate in this projection.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import TYPE_CHECKING

from film_foundry.contracts.data_kernel import MATERIAL_GEOMETRY_MODEL_ID

if TYPE_CHECKING:
    from film_foundry.model.config import DarkroomConfig, DevelopRecipeConfig, FilmStockConfig


FRAME_LONG_EDGES_MM = {
    "half_frame": 24.0,
    "35mm": 36.0,
    "6x6": 56.0,
    "6x7": 69.0,
    "4x5": 120.0,
}

MATERIAL_RADIUS_FIELDS = (
    "emulsion_blur_radius",
    "emulsion_highlight_spread_radius",
    "halation_core_radius",
    "halation_outer_radius",
    "halation_exponential_radius",
    "halation_source_blur_radius",
    "halation_peak_radius",
    "halation_area_radius",
    "silver_grain_radius",
    "dir_diffusion_radius",
)


def _positive_length(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive length in mm")
    try:
        length = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive length in mm") from exc
    if not isfinite(length) or length <= 0.0:
        raise ValueError(f"{name} must be a finite positive length in mm")
    return length


@dataclass(frozen=True, slots=True)
class MaterialGeometry:
    """Frozen extent of the exposed frame, independent of working resolution."""

    frame_long_edge_mm: float
    reference_short_edge_mm: float
    full_shape: tuple[int, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_long_edge_mm", _positive_length(
            self.frame_long_edge_mm, "frame_long_edge_mm"))
        object.__setattr__(self, "reference_short_edge_mm", _positive_length(
            self.reference_short_edge_mm, "reference_short_edge_mm"))
        if len(self.full_shape) != 2 or any(
            isinstance(v, bool) or not isinstance(v, int) or v <= 0
            for v in self.full_shape
        ):
            raise ValueError("material geometry full_shape must contain two positive integers")
        object.__setattr__(self, "full_shape", tuple(self.full_shape))

    @property
    def frame_short_edge_mm(self) -> float:
        return self.frame_long_edge_mm * min(self.full_shape) / max(self.full_shape)

    @property
    def radius_factor(self) -> float:
        return self.reference_short_edge_mm / self.frame_short_edge_mm

    def radius_um(self, relative_radius: float) -> float:
        return float(relative_radius) * self.reference_short_edge_mm * 1000.0

    def radius_pixels(self, relative_radius: float, work_shape: tuple[int, int]) -> float:
        """Map length to a grid spanning the same short material edge.

        Short-edge projection matches the existing isotropic kernels. A rounded
        work-grid aspect ratio can differ by a pixel; it does not redefine the
        material extent. Sigma factors, sampling floors and support limits stay
        the responsibility of each existing component.
        """
        return float(relative_radius) * self.radius_factor * min(work_shape)

    def as_dict(self) -> dict[str, object]:
        return {
            "model": MATERIAL_GEOMETRY_MODEL_ID,
            "frame_long_edge_mm": self.frame_long_edge_mm,
            "reference_short_edge_mm": self.reference_short_edge_mm,
            "full_shape": list(self.full_shape),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "MaterialGeometry":
        if not isinstance(payload, dict) or payload.get("model") != MATERIAL_GEOMETRY_MODEL_ID:
            raise ValueError("unsupported material_geometry model")
        if set(payload) != {"model", "frame_long_edge_mm", "reference_short_edge_mm", "full_shape"}:
            raise ValueError("material_geometry fields are incomplete or unsupported")
        shape = payload["full_shape"]
        if not isinstance(shape, (list, tuple)):
            raise ValueError("material_geometry full_shape must be an array")
        return cls(payload["frame_long_edge_mm"], payload["reference_short_edge_mm"], tuple(shape))


def resolve_material_geometry(
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    image_shape: tuple[int, ...],
) -> MaterialGeometry | None:
    reference = film.spatial_reference_short_edge_mm
    if reference is None:
        if recipe.frame_long_edge_mm is not None:
            raise ValueError("develop.frame_long_edge_mm requires film.spatial_reference_short_edge_mm")
        return None
    reference = _positive_length(reference, "film.spatial_reference_short_edge_mm")
    long_edge = recipe.frame_long_edge_mm
    if long_edge is None:
        key = str(recipe.frame_size).strip().lower().replace("-", "_").replace(" ", "_")
        if key not in FRAME_LONG_EDGES_MM:
            raise ValueError("unknown physical frame_size; supply develop.frame_long_edge_mm")
        long_edge = FRAME_LONG_EDGES_MM[key]
    return MaterialGeometry(long_edge, reference, tuple(int(v) for v in image_shape[:2]))


def project_formation_config(config: DarkroomConfig, geometry: MaterialGeometry) -> DarkroomConfig:
    """Private projection into the existing relative-radius numerical kernels.

    Never save this projected config as a recipe. Grain scales are projected
    after their existing material/process preparation in density_grain.py.
    """
    gain = geometry.radius_factor
    film = replace(config.film, **{
        key: float(getattr(config.film, key)) * gain for key in MATERIAL_RADIUS_FIELDS
    })
    recipe = replace(
        config.chemistry,
        frame_size="35mm",  # no second empirical grain amplitude/radius factor
        development_adjacency_radius=float(config.chemistry.development_adjacency_radius) * gain,
    )
    return replace(config, film=film, chemistry=recipe)
