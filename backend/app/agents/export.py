"""A month of sales for the accountant (Phase 15, D72/D73): an Excel file.

Owners pick a month in the Mini App (Orders → Export); the shop's bot sends
the file to their private chat (files opened inside Telegram's Mini App
often don't download). Three sheets:

    Summary   money received, per channel and payment method, discounts, unpaid
    Sales     one row per item of every paid order: what, how many, listed and
              final price, discount, payment method, who sold or confirmed it
    Orders    one row per order, paid or not: customer, delivery/pickup, stage

Months are Addis Ababa months, by the day the order was placed. Cancelled
orders are listed in Orders but count nowhere else.
"""
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.agents.shop_types import labels
from app.agents.tools import order_number
from app.models.schemas import Store

ADDIS = timezone(timedelta(hours=3), "EAT")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONEY = "#,##0.00"
DATE = "yyyy-mm-dd hh:mm"

STAGES = {"pending": "Waiting for payment", "confirmed": "Paid", "out_for_delivery": "On the way",
          "delivered": "Delivered", "cancelled": "Cancelled"}
CHANNELS = {"telegram": "Telegram", "in_shop": "In the shop"}
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class ExportError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class Export:
    filename: str
    data: bytes
    orders: int
    revenue: Decimal


def month_bounds(month: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """"2026-09" → [Sep 1, Oct 1) in Addis Ababa. Not a month in the future."""
    match = _MONTH.match(month or "")
    if not match or not 1 <= int(match[2]) <= 12 or int(match[1]) < 2020:
        raise ExportError("Choose a month like 2026-09.")
    year, number = int(match[1]), int(match[2])
    start = datetime(year, number, 1, tzinfo=ADDIS)
    end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=ADDIS)
    if start > (now or datetime.now(timezone.utc)):
        raise ExportError("That month hasn't started yet.")
    return start, end


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _local(value: str) -> datetime:
    """A database time as Addis local time, without a zone (Excel has none)."""
    return datetime.fromisoformat(value).astimezone(ADDIS).replace(tzinfo=None)


def _paid_amount(order: dict[str, Any]) -> Decimal:
    payments = order.get("payments") or []
    return sum((_money(p.get("amount")) for p in payments), Decimal(0)) if payments \
        else _money(order.get("total_price"))


def _method(order: dict[str, Any]) -> str:
    payments = order.get("payments") or []
    return order.get("payment_method") or next((p["method"] for p in payments if p.get("method")), "") or ""


def _who(order: dict[str, Any]) -> str:
    payments = order.get("payments") or []
    return order.get("sold_by_name") or next(
        (p["confirmed_by_name"] for p in payments if p.get("confirmed_by_name")), "") or ""


def _sheet(book: Workbook, title: str, header: list[str], rows: list[list[Any]],
           money: tuple[int, ...] = (), dates: tuple[int, ...] = ()) -> None:
    sheet = book.create_sheet(title)
    sheet.append(header)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    for column in range(1, len(header) + 1):
        letter = get_column_letter(column)
        for cell in sheet[letter][1:]:
            if column in money:
                cell.number_format = MONEY
            elif column in dates:
                cell.number_format = DATE
        widest = max([len(str(header[column - 1]))] + [len(str(r[column - 1] or "")) for r in rows])
        sheet.column_dimensions[letter].width = min(max(widest + 2, 10), 45)
    sheet.freeze_panes = "A2"


def build_export(store: Store, orders: list[dict[str, Any]], start: datetime) -> Export:
    """The workbook for `orders` (from orders_for_export), as bytes."""
    option1, option2 = labels(store)
    sales: list[list[Any]] = []
    order_rows: list[list[Any]] = []
    revenue = discounts = Decimal(0)
    by_channel: dict[str, Decimal] = {}
    by_method: dict[str, tuple[int, Decimal]] = {}
    paid_count = unpaid_count = cancelled_count = 0

    for order in orders:
        number = order_number(UUID(order["id"]))
        when = _local(order["created_at"])
        channel = CHANNELS.get(order.get("channel") or "telegram", "Telegram")
        status, paid = order.get("status") or "pending", order.get("payment_status") == "paid"
        items = order.get("order_items") or []
        order_rows.append([
            when, number, channel, order.get("contact_name") or "", order.get("contact_phone") or "",
            (order.get("fulfillment_method") or "").capitalize(), STAGES.get(status, status),
            "Paid" if paid else ("Refunded" if order.get("payment_status") == "refunded" else "Not paid"),
            _money(order.get("total_price")), _method(order) if paid else "", sum(i["quantity"] for i in items),
        ])
        if status == "cancelled":
            cancelled_count += 1
            continue
        if not paid:
            unpaid_count += 1
            continue
        paid_count += 1
        amount = _paid_amount(order)
        revenue += amount
        by_channel[channel] = by_channel.get(channel, Decimal(0)) + amount
        method = _method(order) or "Not recorded"
        count, total = by_method.get(method, (0, Decimal(0)))
        by_method[method] = (count + 1, total + amount)
        for item in items:
            variant = item.get("product_variants") or {}
            product = variant.get("products") or {}
            price, quantity = _money(item.get("price")), int(item["quantity"])
            listed = _money(item["list_price"]) if item.get("list_price") is not None else price
            off = max(listed - price, Decimal(0)) * quantity
            discounts += off
            sales.append([when, number, channel, product.get("name") or "", product.get("code") or "",
                          variant.get("color") or "", variant.get("size") or "", quantity, listed, price,
                          off, price * quantity, _method(order), _who(order)])

    book = Workbook()
    summary = book.active
    summary.title = "Summary"
    month_name = start.strftime("%B %Y")
    summary_rows: list[list[Any]] = [
        [store.name, month_name],
        [],
        ["Money received (paid orders and sales)", revenue],
        ["Paid orders and sales", paid_count],
        *[[f"  {channel}", total] for channel, total in sorted(by_channel.items())],
        ["Discounts given in the shop", discounts],
        ["Orders not paid", unpaid_count],
        ["Cancelled orders", cancelled_count],
        [],
        ["By payment method", "Payments", "Amount"],
        *[[method, count, total] for method, (count, total) in sorted(by_method.items())],
        [],
        [f"Orders placed {month_name}, Addis Ababa time. Amounts in ETB."],
    ]
    for row in summary_rows:
        summary.append(row)
    summary["A1"].font = Font(bold=True, size=14)
    for row in summary.iter_rows():
        for cell in row:
            if isinstance(cell.value, Decimal):
                cell.number_format = MONEY
            if cell.value in ("By payment method", "Payments", "Amount"):
                cell.font = Font(bold=True)
    summary.column_dimensions["A"].width = 40
    summary.column_dimensions["B"].width = 16
    summary.column_dimensions["C"].width = 16

    _sheet(book, "Sales",
           ["Date", "Order", "Where", "Product", "Code", option1.en, option2.en, "Quantity",
            "Listed price", "Price", "Discount", "Total", "Payment method", "Sold / confirmed by"],
           sales, money=(9, 10, 11, 12), dates=(1,))
    _sheet(book, "Orders",
           ["Date", "Order", "Where", "Customer", "Phone", "Delivery / pickup", "Stage", "Payment",
            "Total", "Payment method", "Items"],
           order_rows, money=(9,), dates=(1,))

    buffer = io.BytesIO()
    book.save(buffer)
    shop = re.sub(r"[^A-Za-z0-9]+", "-", store.name).strip("-") or "shop"
    return Export(f"{shop}-{start:%Y-%m}.xlsx", buffer.getvalue(), len(orders), revenue)
