"""The Ethiopian calendar (ዓ.ም.), for the accountant's export (Phase 15c).

12 months of 30 days, then Pagume (5 days, 6 in the year before a Gregorian
leap year). The year starts on Meskerem 1 = September 11 (September 12 before
a Gregorian leap year). Converted through the Julian day number, with the
usual Amete Mihret epoch.
"""
from datetime import date

EPOCH = 1723856  # the Julian day number before Meskerem 1, year 1
_JDN_OF_ORDINAL = 1721425  # date.toordinal() + this = the Julian day number

MONTHS_AM = ("መስከረም", "ጥቅምት", "ኅዳር", "ታኅሣሥ", "ጥር", "የካቲት", "መጋቢት", "ሚያዝያ", "ግንቦት",
             "ሰኔ", "ሐምሌ", "ነሐሴ", "ጳጉሜ")
MONTHS_EN = ("Meskerem", "Tikimt", "Hidar", "Tahsas", "Tir", "Yekatit", "Megabit", "Miyazya", "Ginbot",
             "Sene", "Hamle", "Nehase", "Pagume")


def to_ethiopian(day: date) -> tuple[int, int, int]:
    """(year, month 1–13, day) of a Gregorian date."""
    jdn = day.toordinal() + _JDN_OF_ORDINAL
    r = (jdn - EPOCH) % 1461
    n = r % 365 + 365 * (r // 1460)
    year = 4 * ((jdn - EPOCH) // 1461) + r // 365 - r // 1460
    return year, n // 30 + 1, n % 30 + 1


def from_ethiopian(year: int, month: int, day: int) -> date:
    """The Gregorian date of an Ethiopian one."""
    jdn = EPOCH + 365 + 365 * (year - 1) + year // 4 + 30 * month + day - 31
    return date.fromordinal(jdn - _JDN_OF_ORDINAL)


def ethiopian_text(day: date) -> str:
    """"መስከረም 3, 2019" — how it's written in Ethiopia."""
    year, month, number = to_ethiopian(day)
    return f"{MONTHS_AM[month - 1]} {number}, {year}"


def month_range(year: int, month: int) -> tuple[date, date]:
    """[first day, first day of the next) of an Ethiopian month, in Gregorian
    dates. Nehase (12) includes Pagume, so the year is 12 months for the export."""
    start = from_ethiopian(year, month, 1)
    end = from_ethiopian(year + 1, 1, 1) if month >= 12 else from_ethiopian(year, month + 1, 1)
    return start, end
