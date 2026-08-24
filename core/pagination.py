from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Generic, Mapping, TypeVar


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

T = TypeVar("T")
SortValueT = TypeVar("SortValueT")


class PaginationError(ValueError):
    """Stable validation error for shared list endpoints."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


class SortFieldNotAllowed(PaginationError):
    """Raised when a client sort key is not present in the server allowlist."""

    def __init__(self, field: str, allowed: tuple[str, ...]) -> None:
        super().__init__("sort_field_not_allowed", f"sort field is not allowed: {field}")
        self.field = field
        self.allowed = allowed


def _positive_int(value: Any, *, default: int, field: str) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise PaginationError(f"invalid_{field}", f"{field} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise PaginationError(
            f"invalid_{field}",
            f"{field} must be a positive integer",
        ) from exc
    if normalized < 1:
        raise PaginationError(f"invalid_{field}", f"{field} must be at least 1")
    return normalized


@dataclass(frozen=True)
class PaginationParams:
    page: int = DEFAULT_PAGE
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page < 1:
            raise PaginationError("invalid_page", "page must be at least 1")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or self.page_size < 1
            or self.page_size > MAX_PAGE_SIZE
        ):
            raise PaginationError(
                "invalid_page_size",
                f"page_size must be between 1 and {MAX_PAGE_SIZE}",
            )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @classmethod
    def from_values(
        cls,
        *,
        page: Any = None,
        page_size: Any = None,
    ) -> "PaginationParams":
        normalized_page = _positive_int(page, default=DEFAULT_PAGE, field="page")
        normalized_size = _positive_int(
            page_size,
            default=DEFAULT_PAGE_SIZE,
            field="page_size",
        )
        return cls(
            page=normalized_page,
            page_size=min(normalized_size, MAX_PAGE_SIZE),
        )


def normalize_pagination(
    *,
    page: Any = None,
    page_size: Any = None,
) -> PaginationParams:
    """Normalize HTTP-like page values into the shared 1/20/100 contract."""

    return PaginationParams.from_values(page=page, page_size=page_size)


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int

    def __post_init__(self) -> None:
        PaginationParams(page=self.page, page_size=self.page_size)
        if isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < 0:
            raise PaginationError("invalid_total", "total must be a non-negative integer")
        expected_pages = math.ceil(self.total / self.page_size) if self.total else 0
        if self.total_pages != expected_pages:
            raise PaginationError(
                "invalid_total_pages",
                "total_pages does not match total and page_size",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": list(self.items),
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
        }


def build_page(
    items: list[T],
    *,
    total: int,
    params: PaginationParams | None = None,
    page: Any = None,
    page_size: Any = None,
) -> Page[T]:
    """Build a serializable page without coupling callers to FastAPI or SQL."""

    if params is not None and (page is not None or page_size is not None):
        raise PaginationError(
            "conflicting_pagination",
            "pass params or page/page_size, not both",
        )
    normalized = params or normalize_pagination(page=page, page_size=page_size)
    if isinstance(total, bool):
        raise PaginationError("invalid_total", "total must be a non-negative integer")
    try:
        normalized_total = int(total)
    except (TypeError, ValueError) as exc:
        raise PaginationError(
            "invalid_total",
            "total must be a non-negative integer",
        ) from exc
    if normalized_total < 0:
        raise PaginationError("invalid_total", "total must be a non-negative integer")
    total_pages = math.ceil(normalized_total / normalized.page_size) if normalized_total else 0
    return Page(
        items=list(items),
        page=normalized.page,
        page_size=normalized.page_size,
        total=normalized_total,
        total_pages=total_pages,
    )


@dataclass(frozen=True)
class SortSelection(Generic[SortValueT]):
    name: str
    value: SortValueT
    direction: str


def resolve_sort(
    sort_by: Any,
    *,
    allowed: Mapping[str, SortValueT],
    default: str,
    direction: Any = "asc",
) -> SortSelection[SortValueT]:
    """Resolve a public sort name through a server-owned allowlist.

    The returned ``value`` comes from ``allowed``; the raw client value is never
    returned as an SQL identifier.
    """

    if not allowed:
        raise PaginationError("empty_sort_allowlist", "sort allowlist must not be empty")
    default_name = str(default or "").strip()
    if default_name not in allowed:
        raise PaginationError(
            "invalid_default_sort",
            "default sort field must exist in the allowlist",
        )
    requested = default_name if sort_by is None or sort_by == "" else str(sort_by).strip()
    if requested not in allowed:
        raise SortFieldNotAllowed(requested, tuple(allowed.keys()))
    normalized_direction = str(direction or "asc").strip().lower()
    if normalized_direction not in {"asc", "desc"}:
        raise PaginationError(
            "sort_direction_not_allowed",
            "sort direction must be asc or desc",
        )
    return SortSelection(
        name=requested,
        value=allowed[requested],
        direction=normalized_direction,
    )


__all__ = [
    "DEFAULT_PAGE",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Page",
    "PaginationError",
    "PaginationParams",
    "SortFieldNotAllowed",
    "SortSelection",
    "build_page",
    "normalize_pagination",
    "resolve_sort",
]
