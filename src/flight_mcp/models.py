from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .channels import PROVIDERS, Provider


def today_china() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class RouteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    origin: str = Field(min_length=1, max_length=60, description="出发城市/机场，如首尔、仁川、ICN")
    destination: str = Field(min_length=1, max_length=60, description="到达城市/机场，如成都、天府、TFU")
    dates: list[date] = Field(min_length=1, max_length=7, description="出发当地日期，YYYY-MM-DD；单程")

    @field_validator("dates")
    @classmethod
    def valid_dates(cls, values: list[date]) -> list[date]:
        today = today_china()
        if any(d < today or d > today + timedelta(days=365) for d in values):
            raise ValueError("出发日期需在今天至未来 365 天内；请填写具体年份")
        return sorted(set(values))


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    routes: list[RouteQuery] = Field(min_length=1, max_length=8)
    providers: list[Provider] = Field(default_factory=lambda: list(PROVIDERS), min_length=1, max_length=5)
    notify: bool = True
    direct_only: bool = True
    exclude_stopovers: bool = True
    cabin: Literal["any", "economy", "business", "first"] = "economy"
    max_price: float | None = Field(default=None, gt=0)
    departure_after: str | None = None
    departure_before: str | None = None
    sort_by: Literal["price", "departure", "duration"] = "price"
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("providers")
    @classmethod
    def unique_providers(cls, values: list[Provider]) -> list[Provider]:
        return list(dict.fromkeys(values))

    @field_validator("departure_after", "departure_before")
    @classmethod
    def valid_clock(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("起飞时段请使用 HH:MM，例如 08:00")
        return value

    @model_validator(mode="after")
    def batch_limit(self) -> "SearchRequest":
        if sum(len(route.dates) for route in self.routes) > 16:
            raise ValueError("每次最多查询 16 个航线与日期组合，请拆分查询")
        if self.max_price is not None and not math.isfinite(self.max_price):
            raise ValueError("价格上限必须为有限正数")
        return self
