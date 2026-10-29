"""Pagination: query parameters, response envelope and a helper for single-entity selects."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class PageParams:
    page: int
    size: int


def _page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageParams:
    return PageParams(page=page, size=size)


# Use as an endpoint parameter:  params: Pagination
Pagination = Annotated[PageParams, Depends(_page_params)]


def paginate(db: Session, stmt: Select, params: PageParams) -> tuple[list, int]:
    """Run a single-entity select (``select(Model)...``) for one page. The count ignores ORDER BY."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.offset((params.page - 1) * params.size).limit(params.size)).all()
    return list(rows), total or 0
