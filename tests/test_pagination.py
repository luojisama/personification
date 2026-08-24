from __future__ import annotations

import pytest

from ..core.pagination import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PaginationError,
    SortFieldNotAllowed,
    build_page,
    normalize_pagination,
    resolve_sort,
)


def test_pagination_defaults_and_offset() -> None:
    params = normalize_pagination()

    assert params.page == DEFAULT_PAGE == 1
    assert params.page_size == DEFAULT_PAGE_SIZE == 20
    assert params.offset == 0
    assert normalize_pagination(page="3", page_size="25").offset == 50


def test_page_size_is_capped_at_one_hundred() -> None:
    params = normalize_pagination(page=2, page_size=999)

    assert params.page == 2
    assert params.page_size == MAX_PAGE_SIZE == 100
    assert params.offset == 100


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"page": 0}, "invalid_page"),
        ({"page": "nope"}, "invalid_page"),
        ({"page_size": 0}, "invalid_page_size"),
        ({"page_size": True}, "invalid_page_size"),
    ],
)
def test_invalid_pagination_values_have_stable_codes(
    kwargs: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(PaginationError) as caught:
        normalize_pagination(**kwargs)

    assert caught.value.code == code


def test_build_page_uses_the_shared_shape() -> None:
    result = build_page(
        [{"id": 21}, {"id": 22}],
        total=42,
        page=2,
        page_size=20,
    )

    assert result.to_dict() == {
        "items": [{"id": 21}, {"id": 22}],
        "page": 2,
        "page_size": 20,
        "total": 42,
        "total_pages": 3,
    }


def test_empty_page_has_zero_total_pages() -> None:
    result = build_page([], total=0)

    assert result.total_pages == 0
    assert result.to_dict()["items"] == []


def test_sort_selection_returns_only_server_owned_value() -> None:
    selection = resolve_sort(
        "updated_at",
        allowed={
            "updated_at": "profiles.updated_at",
            "qq": "profiles.user_id",
        },
        default="qq",
        direction="DESC",
    )

    assert selection.name == "updated_at"
    assert selection.value == "profiles.updated_at"
    assert selection.direction == "desc"


def test_sort_selection_rejects_unlisted_client_input() -> None:
    with pytest.raises(SortFieldNotAllowed) as caught:
        resolve_sort(
            "updated_at DESC; DROP TABLE profiles",
            allowed={"updated_at": "profiles.updated_at"},
            default="updated_at",
        )

    assert caught.value.code == "sort_field_not_allowed"
    assert caught.value.allowed == ("updated_at",)


def test_sort_direction_is_allowlisted() -> None:
    with pytest.raises(PaginationError) as caught:
        resolve_sort(
            None,
            allowed={"updated_at": "profiles.updated_at"},
            default="updated_at",
            direction="desc nulls last",
        )

    assert caught.value.code == "sort_direction_not_allowed"
