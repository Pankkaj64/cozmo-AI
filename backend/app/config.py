"""All configuration in one place: paths, models, detector classes, API endpoints, env settings.

Environment variables are read at access time (so `.env`, `model_choices.env` and shell
overrides behave the same everywhere and tests can patch `os.environ`). Every variable
read here is documented in `backend/.env.example`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---- paths ----------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
DATA_DIR = ROOT_DIR / "data"
FRAME_DIR = DATA_DIR / "frames"  # every analysed camera frame (evidence)
PACKET_DIR = DATA_DIR / "packets"  # internal sweep state and checkpoints
CLAIM_DIR = DATA_DIR / "claims"  # deliverables: claim_packet.json + report.html per sweep
RUNTIME_DIR = BACKEND_DIR / ".runtime"  # downloaded model weights and tool caches
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

# `.env` wins over the committed model profile; real environment variables win over both.
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR / "model_choices.env")

# ---- local models (defaults; override in model_choices.env or .env) --------------------
DEFAULT_DETECTOR_MODEL = "yolo26s.pt"  # book boxes + COCO objects
DEFAULT_VALIDATOR_DETECTOR_MODEL = "yolo11s.pt"  # independent second count
DEFAULT_ROOM_DETECTOR_MODEL = "library-room-yoloe26s-v2.pt"  # open-vocabulary room categories
DEFAULT_BOOK_READER_MODEL = "gemma3:latest"  # reads the visible title of a crop (Ollama)
DEFAULT_CROP_VERIFIER_MODEL = "qwen3-vl:2b"  # blind crop category check (Ollama)
DEFAULT_CONVERSATION_MODEL = "qwen2.5:3b"  # spoken conversation (Ollama)
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OCR_ENGINE = "paddle"  # or "easyocr"

# Open-vocabulary prompts for the room detector. Structural classes and people are
# detected only so they can be ignored.
ROOM_CLASSES = [
    "book", "bookshelf", "cabinet", "chair", "sofa", "table", "coffee machine", "lamp",
    "framed painting", "portrait", "rug", "television", "monitor", "laptop", "speaker", "vase",
    "clock", "mirror", "plant pot", "curtain", "air conditioner", "fan", "printer", "sculpture",
    "cushion", "bottle", "cup", "person", "cell phone", "door", "window", "wall", "floor", "paper bag",
]  # fmt: skip
IGNORED_CLASSES = {"person", "door", "window", "wall", "floor", "paper bag"}

# ---- external APIs (the only network calls the backend makes) ----------------------------
# Each endpoint can be overridden with the environment variable of the same name.
OPEN_LIBRARY_SEARCH_URL = os.getenv(
    "OPEN_LIBRARY_SEARCH_URL", "https://openlibrary.org/search.json"
)
OPEN_LIBRARY_BOOKS_URL = os.getenv("OPEN_LIBRARY_BOOKS_URL", "https://openlibrary.org/api/books")
OPEN_LIBRARY_BASE_URL = os.getenv("OPEN_LIBRARY_BASE_URL", "https://openlibrary.org/")
GOOGLE_BOOKS_VOLUMES_URL = os.getenv(
    "GOOGLE_BOOKS_URL", "https://www.googleapis.com/books/v1/volumes"
)
EBAY_API_HOSTS = {
    "production": os.getenv("EBAY_API_URL", "https://api.ebay.com"),
    "sandbox": os.getenv("EBAY_SANDBOX_API_URL", "https://api.sandbox.ebay.com"),
}
EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
FRANKFURTER_URL = os.getenv("FX_PRIMARY_URL", "https://api.frankfurter.app/latest")  # ECB rates
OPEN_ER_API_URL = os.getenv("FX_FALLBACK_URL", "https://open.er-api.com/v6/latest")  # fallback FX
HTTP_USER_AGENT = "LibraryContentsClaimAgent/0.3 (local research prototype)"


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _number(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


class Settings:
    """Typed accessors over environment variables. Read lazily, never cached."""

    # --- local inference ---------------------------------------------------------------
    @property
    def ollama_url(self) -> str:
        return os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL)

    @property
    def detector_model(self) -> str:
        return os.getenv("DETECTOR_MODEL", DEFAULT_DETECTOR_MODEL)

    @property
    def validator_detector_model(self) -> str:
        return os.getenv("VALIDATOR_DETECTOR_MODEL", DEFAULT_VALIDATOR_DETECTOR_MODEL)

    @property
    def room_detector_model(self) -> str:
        return os.getenv("ROOM_DETECTOR_MODEL", DEFAULT_ROOM_DETECTOR_MODEL)

    @property
    def book_reader_model(self) -> str:
        return os.getenv("BOOK_READER_MODEL", DEFAULT_BOOK_READER_MODEL)

    @property
    def crop_verifier_model(self) -> str:
        return os.getenv("CROP_VERIFIER_MODEL", DEFAULT_CROP_VERIFIER_MODEL)

    @property
    def crop_verifier_timeout_s(self) -> float:
        return _number("CROP_VERIFIER_TIMEOUT_S", "45")

    @property
    def crop_verification_frame_budget_s(self) -> float:
        return _number("CROP_VERIFICATION_FRAME_BUDGET_S", "90")

    @property
    def conversation_model(self) -> str:
        return os.getenv("CONVERSATION_MODEL", DEFAULT_CONVERSATION_MODEL)

    @property
    def ocr_engine(self) -> str:
        """'paddle' (PaddleOCR, default) or 'easyocr'."""
        return os.getenv("OCR_ENGINE", DEFAULT_OCR_ENGINE).strip().lower()

    @property
    def ocr_lang(self) -> str:
        return os.getenv("OCR_LANG", "en")

    # --- pricing and catalogue sources -------------------------------------------------
    @property
    def enable_catalogue_lookup(self) -> bool:
        return _flag("ENABLE_CATALOGUE_LOOKUP")

    @property
    def enable_live_research(self) -> bool:
        return _flag("ENABLE_LIVE_RESEARCH")

    @property
    def enable_google_books(self) -> bool:
        return _flag("ENABLE_GOOGLE_BOOKS", "true")

    @property
    def google_books_api_key(self) -> str:
        return os.getenv("GOOGLE_BOOKS_API_KEY", "")

    @property
    def ebay_access_token(self) -> str:
        return os.getenv("EBAY_ACCESS_TOKEN", "")

    @property
    def ebay_client_id(self) -> str:
        return os.getenv("EBAY_CLIENT_ID", "")

    @property
    def ebay_client_secret(self) -> str:
        return os.getenv("EBAY_CLIENT_SECRET", "")

    @property
    def ebay_marketplace_id(self) -> str:
        return os.getenv("EBAY_MARKETPLACE_ID", "")

    @property
    def ebay_api_url(self) -> str:
        return EBAY_API_HOSTS.get(os.getenv("EBAY_ENV", "production"), EBAY_API_HOSTS["production"])

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_access_token or (self.ebay_client_id and self.ebay_client_secret))

    @property
    def pricing_min_match(self) -> float:
        return _number("PRICING_MIN_MATCH", "0.8")

    @property
    def pricing_allow_ebook_proxy(self) -> bool:
        return _flag("PRICING_ALLOW_EBOOK_PROXY")

    @property
    def pricing_research_timeout_s(self) -> float:
        return _number("PRICING_RESEARCH_TIMEOUT_S", "180")

    @property
    def enable_fx_conversion(self) -> bool:
        return _flag("ENABLE_FX_CONVERSION", "true")

    @property
    def any_price_source_configured(self) -> bool:
        return self.ebay_configured or self.enable_google_books

    # --- transport ----------------------------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


settings = Settings()
