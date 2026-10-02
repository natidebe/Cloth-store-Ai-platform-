"""The store profile as lists (Phase 10b, migration 011), and the texts the bot sends.

The Mini App edits payment accounts, delivery areas with fees, and opening
hours per day as lists. The bot keeps sending the text columns it always
used (payment_instructions, delivery_info, opening_hours): they're written
from the lists here whenever the lists are saved.
"""
import re
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _one_line(value: str) -> str:
    return " ".join(value.split())


class PaymentAccount(BaseModel):
    name: str = Field(min_length=1, max_length=40)  # "Telebirr", "CBE", "Awash Bank"
    number: str = Field(min_length=1, max_length=60)  # "0911 000 000", "1000 1234 5678"
    holder: str | None = Field(default=None, max_length=60)  # optional: whose account

    @field_validator("name", "number", "holder")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return _one_line(value) if value else value


class DeliveryArea(BaseModel):
    area: str = Field(min_length=1, max_length=60)
    fee: Decimal = Field(ge=0, le=100_000)

    @field_validator("area")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _one_line(value)


class DayHours(BaseModel):
    open: bool = False
    start: str | None = Field(default=None, alias="from")
    end: str | None = Field(default=None, alias="to")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _times(self) -> "DayHours":
        if self.open:
            if not (self.start and TIME.match(self.start) and self.end and TIME.match(self.end)):
                raise ValueError("an open day needs from and to times like 08:30")
            if self.end <= self.start:
                raise ValueError("closing time must be after opening time")
        return self


class OpeningWeek(BaseModel):
    mon: DayHours = DayHours()
    tue: DayHours = DayHours()
    wed: DayHours = DayHours()
    thu: DayHours = DayHours()
    fri: DayHours = DayHours()
    sat: DayHours = DayHours()
    sun: DayHours = DayHours()

    def as_json(self) -> dict:
        return {day: getattr(self, day).model_dump(by_alias=True) for day in DAYS}


def _money(fee: Decimal) -> str:
    fee = Decimal(fee)
    return f"{fee:,.0f}" if fee == fee.to_integral_value() else f"{fee:,.2f}"


def payment_text(accounts: list[PaymentAccount]) -> str | None:
    """"Telebirr: 0911 000 000 (Selam Shoes)", one per line."""
    lines = [f"{a.name}: {a.number}" + (f" ({a.holder})" if a.holder else "") for a in accounts]
    return "\n".join(lines) or None


def delivery_text(areas: list[DeliveryArea]) -> str | None:
    """"Bole: 150 ETB", one per line."""
    return "\n".join(f"{a.area}: {_money(a.fee)} ETB" for a in areas) or None


def hours_text(week: OpeningWeek | None) -> str | None:
    """Days with the same hours grouped: "Mon–Sat 08:30–19:00, Sun closed"."""
    if week is None:
        return None
    groups: list[tuple[list[str], str]] = []
    for day in DAYS:
        hours = getattr(week, day)
        label = f"{hours.start}–{hours.end}" if hours.open else "closed"
        if groups and groups[-1][1] == label:
            groups[-1][0].append(day)
        else:
            groups.append(([day], label))
    if all(label == "closed" for _, label in groups):
        return None
    parts = []
    for days, label in groups:
        names = DAY_NAMES[days[0]] if len(days) == 1 else f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
        parts.append(f"{names} {label}")
    return ", ".join(parts)
