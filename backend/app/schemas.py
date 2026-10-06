"""Small data models used by the API and claim packet."""

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field


class Price(BaseModel):
    amount: float | None = None
    source: str = ""
    url: str = ""
    retrieved_at: str = ""
    converted: bool = False
    condition_assumed: str = ""


class Book(BaseModel):
    id: str
    shelf: str = ""
    position: int = 0
    frame_ref: str = ""
    status: Literal["identified", "unidentified", "needs_appraisal"] = "unidentified"
    title: str = ""
    author: str = ""
    publisher: str = ""
    edition: str = ""
    isbn: str = ""
    spine_height_cm: float | None = None
    spine_thickness_cm: float | None = None
    id_confidence: float = 0.0
    replacement_cost: Price = Field(default_factory=Price)
    used_value: Price = Field(default_factory=Price)


class Dimensions(BaseModel):
    w: float | None = None
    h: float | None = None
    d: float | None = None


class ClaimItem(BaseModel):
    id: str
    category: str
    description: str = ""
    material: str = ""
    brand_model: str = ""
    frame_ref: str = ""
    dimensions_cm: Dimensions = Field(default_factory=Dimensions)
    status: Literal["priced", "range", "needs_appraisal"] = "needs_appraisal"
    replacement_cost: dict = Field(
        default_factory=lambda: {
            "low": None,
            "high": None,
            "source": "",
            "url": "",
            "retrieved_at": "",
        }
    )
    confidence: float = 0.0


class SweepStart(BaseModel):
    appraisal_threshold: float = Field(default=2000, gt=0, allow_inf_nan=False)
    country: str = ""
    currency: str = ""
    device: str = "browser camera"
    country_code: str = Field(default="", pattern=r"^([A-Z]{2})?$")


class Room(BaseModel):
    length_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    floor_area_m2: float | None = None
    wall_area_m2: float | None = None
    shelved_wall_area_m2: float | None = None
    floor_area_ft2: float | None = None
    wall_area_ft2: float | None = None
    shelved_wall_area_ft2: float | None = None
    scale_method: str = ""
    confidence: float = 0.0


def empty_packet(
    sweep_id: str, country: str = "", currency: str = "", device: str = ""
) -> dict:
    return {
        "sweep": {
            "id": sweep_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "device": device,
            "duration_s": 0,
            "country": country,
            "currency": currency,
        },
        "room": Room().model_dump(),
        "books": [],
        "items": [],
        "totals": {},
        "review_queue": [],
    }
