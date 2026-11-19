"""Data contracts: what the API returns and what the pipeline writes."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RatesResponse(BaseModel):
    """Body of `GET /v1/{start}..{end}` (time series endpoint)."""

    model_config = ConfigDict(extra="ignore")

    amount: float
    base: str = Field(min_length=3, max_length=3)
    start_date: date
    end_date: date
    rates: dict[date, dict[str, float]]

    @field_validator("rates")
    @classmethod
    def rates_are_positive(
        cls, value: dict[date, dict[str, float]]
    ) -> dict[date, dict[str, float]]:
        for day, by_currency in value.items():
            for currency, rate in by_currency.items():
                if rate <= 0:
                    raise ValueError(f"non-positive rate {rate} for {currency} on {day}")
        return value


class FxRate(BaseModel):
    """One row of the target table. Primary key: (rate_date, base_currency, quote_currency)."""

    model_config = ConfigDict(frozen=True)

    rate_date: date
    base_currency: str
    quote_currency: str
    rate: float = Field(gt=0)
    source: str = "ecb/frankfurter"
    ingested_at: datetime

    @property
    def key(self) -> tuple[date, str, str]:
        return (self.rate_date, self.base_currency, self.quote_currency)
