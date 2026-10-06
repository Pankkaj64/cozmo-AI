"""Metric geometry from recorded references, never from a model's numeric guess."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SQFT_PER_M2 = 10.7639104167


class Point(BaseModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_ref: str
    start: Point
    end: Point
    length_cm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    reference: str = Field(min_length=3, max_length=300)
    same_plane: Literal[True]
    front_on: Literal[True]


class RoomInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length_m: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    width_m: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    height_m: float = Field(gt=0, le=30, allow_inf_nan=False)
    # A non-self-intersecting floor-plan polygon in metres, in boundary order.
    polygon_m: list[tuple[float, float]] = Field(default_factory=list, max_length=30)
    shelving: list[tuple[float, float]] = Field(default_factory=list, max_length=50)
    source: str = Field(min_length=3, max_length=500)
    frame_ref: str
    method: Literal["known_dimensions", "lidar", "reference_geometry"] = (
        "known_dimensions"
    )

    @model_validator(mode="after")
    def geometry(self):
        if self.polygon_m:
            if len(self.polygon_m) < 3 or len(set(self.polygon_m)) != len(
                self.polygon_m
            ):
                raise ValueError(
                    "Polygon needs at least three distinct boundary points"
                )
            if any(
                not math.isfinite(v) or abs(v) > 100 for p in self.polygon_m for v in p
            ):
                raise ValueError("Invalid polygon coordinate")

            def cross(a, b, c):
                return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

            edges = list(zip(self.polygon_m, self.polygon_m[1:] + self.polygon_m[:1]))
            for i, (a, b) in enumerate(edges):
                for j, (c, d) in enumerate(edges):
                    if j <= i + 1 or (i == 0 and j == len(edges) - 1):
                        continue
                    if (
                        cross(a, b, c) * cross(a, b, d) <= 0
                        and cross(c, d, a) * cross(c, d, b) <= 0
                    ):
                        raise ValueError("Polygon boundaries intersect")
        elif self.length_m is None or self.width_m is None:
            raise ValueError("Supply length and width, or a floor-plan polygon")
        if any(
            not math.isfinite(v) or v <= 0 or v > 100
            for pair in self.shelving
            for v in pair
        ):
            raise ValueError("Shelving widths and heights must be positive metres")
        return self


def room_geometry(value: RoomInput) -> dict:
    if value.polygon_m:
        points = value.polygon_m
        edges = list(zip(points, points[1:] + points[:1]))
        area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) / 2
        perimeter = sum(math.dist(a, b) for a, b in edges)
        length = max(p[0] for p in points) - min(p[0] for p in points)
        width = max(p[1] for p in points) - min(p[1] for p in points)
        shape = "polygon; length and width describe its bounding rectangle"
    else:
        length, width = value.length_m, value.width_m
        area, perimeter = length * width, 2 * (length + width)
        shape = "rectangle"
    if area <= 0:
        raise ValueError("Floor area must be positive")
    wall = perimeter * value.height_m
    shelved = sum(w * h for w, h in value.shelving)
    if shelved > wall:
        raise ValueError("Shelving coverage exceeds total wall area")
    return {
        "length_m": length,
        "width_m": width,
        "height_m": value.height_m,
        "floor_area_m2": round(area, 4),
        "wall_area_m2": round(wall, 4),
        "shelved_wall_area_m2": round(shelved, 4),
        "floor_area_ft2": round(area * SQFT_PER_M2, 4),
        "wall_area_ft2": round(wall * SQFT_PER_M2, 4),
        "shelved_wall_area_ft2": round(shelved * SQFT_PER_M2, 4),
        "scale_method": value.method,
        "source": value.source,
        "frame_ref": value.frame_ref,
        "shape": shape,
        "polygon_m": value.polygon_m,
        "shelving": value.shelving,
        "confidence": 0.7,
        "assumptions": "Vertical walls; gross wall area includes doors/windows. Supplied reference dimensions require verification.",
    }


def measure_spine(
    box: list[float], calibration: Calibration, width: int, height: int
) -> dict:
    if len(box) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in box):
        raise ValueError("Spine bounds must be normalized image coordinates")
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Spine bounds must have positive width and height")
    pixels = math.hypot(
        (calibration.end.x - calibration.start.x) * width,
        (calibration.end.y - calibration.start.y) * height,
    )
    if pixels < 20:
        raise ValueError("Use a reference at least 20 pixels long")
    scale = calibration.length_cm / pixels
    sides = sorted([(x2 - x1) * width * scale, (y2 - y1) * height * scale])
    return {
        "spine_height_cm": round(sides[1], 2),
        "spine_thickness_cm": round(sides[0], 2),
        "measurement": {
            "method": "same-plane front-on reference",
            "cm_per_pixel": scale,
            "calibration": calibration.model_dump(),
            "bbox": box,
            "assumptions": "Long spine edge is height; perspective and rotated spines need review.",
        },
    }
