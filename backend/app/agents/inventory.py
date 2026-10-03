"""Inventory management from the Mini App (Phase 10b; docs/inventory-management.md).

The rules, in one place:
- Stock lives on the variant (one color + one size), never below 0.
- A product's color × size grid is saved by matching each row to an
  existing variant (by its id, or else by color + size): matched rows are
  updated, new ones added. Nothing is removed unless asked.
- A variant or product that was ever ordered can't be deleted (its order
  lines point to it): it's taken off sale instead (stock 0).
- Stock changes from the Mini App use the database's adjust_stock, so two
  staff members changing the same variant at once can't lose a change.
- Every change is picked up by the channel sync (Phase 8d): posts update
  themselves.
- Staff manage stock and sizes too, but never prices (design: "Staff price
  locked"): with prices_locked, a row may not set or change a price.
"""
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.services.supabase_service import DatabaseError, NotFoundError, OutOfStockError, SupabaseService

logger = logging.getLogger(__name__)

LOW_STOCK_AT = 2  # "low stock" in the dashboard: this many left, or fewer


class InventoryError(Exception):
    """A change was refused. `message` is safe to show in the Mini App."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message, self.status_code = message, status_code


@dataclass
class GridRow:
    """One cell of the color × size grid."""
    color: str | None
    size: str | None
    stock: int
    price: Decimal | None = None  # None: the product's base price
    id: UUID | None = None  # an existing variant


@dataclass
class GridResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    kept_off_sale: list[str] = field(default_factory=list)  # ordered before: stock set to 0 instead


def _key(color: str | None, size: str | None) -> tuple[str, str]:
    return ((color or "").strip().lower(), (size or "").strip().lower())


def _label(row: dict[str, Any]) -> str:
    return " ".join(part for part in (row.get("color"), row.get("size")) if part) or "variant"


def _same_price(new: Decimal | None, current: Any) -> bool:
    if new is None or current is None:
        return new is None and current is None
    return Decimal(str(new)) == Decimal(str(current))


def effective_price(variant: dict[str, Any], product: dict[str, Any]) -> Decimal | None:
    """What a customer pays: the variant's own price, else the product's."""
    price = variant.get("price_override")
    if price is None:
        price = product.get("base_price")
    if price is None:
        return None
    price = Decimal(str(price))
    return price.quantize(Decimal(1)) if price == price.to_integral_value() else price  # 2000.0 -> 2000


def summarize(product: dict[str, Any]) -> dict[str, Any]:
    """A product row (with product_variants) as the Mini App's list shows it."""
    variants = product.get("product_variants") or []
    prices = [p for p in (effective_price(v, product) for v in variants) if p is not None]
    if not prices and product.get("base_price") is not None:
        prices = [effective_price({}, product)]  # no colors or sizes yet: the product's own price
    total = sum(int(v.get("stock_quantity") or 0) for v in variants)
    return {
        "id": product["id"], "code": product.get("code"), "name": product["name"],
        "brand": product.get("brand"), "category": product.get("category"),
        "base_price": product.get("base_price"), "photo_url": product.get("photo_url"),
        "description": product.get("description"), "search_keywords": product.get("search_keywords"),
        "condition": product.get("condition"), "warranty_months": product.get("warranty_months"),
        "total_stock": total, "variant_count": len(variants), "on_sale": total > 0,
        "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None,
        "low_stock": any(int(v.get("stock_quantity") or 0) <= LOW_STOCK_AT for v in variants),
        "variants": sorted(
            ({"id": v["id"], "color": v.get("color"), "size": v.get("size"),
              "stock": int(v.get("stock_quantity") or 0), "price_override": v.get("price_override"),
              "price": effective_price(v, product)} for v in variants),
            key=lambda v: (v["color"] or "", v["size"] or "")),
    }


class Inventory:
    def __init__(self, db: SupabaseService):
        self.db = db

    async def product(self, store_id: UUID, product_id: UUID) -> dict[str, Any]:
        row = await self.db.get_product_with_variants(store_id, product_id)
        if row is None:
            raise InventoryError("Product not found.", 404)
        return row

    async def save_grid(self, store_id: UUID, product_id: UUID, rows: list[GridRow],
                        remove: list[UUID] | None = None, *, prices_locked: bool = False) -> GridResult:
        """Save a product's color × size grid (see the module notes)."""
        product = await self.product(store_id, product_id)
        existing = {UUID(v["id"]): v for v in product.get("product_variants") or []}
        by_key = {_key(v.get("color"), v.get("size")): vid for vid, v in existing.items()}
        seen: set[tuple[str, str]] = set()
        result = GridResult()
        for row in rows:
            key = _key(row.color, row.size)
            if key in seen:
                raise InventoryError(f"{row.color or ''} {row.size or ''} is in the grid twice.".strip())
            seen.add(key)
            target = row.id if row.id in existing else by_key.get(key)
            if row.id is not None and row.id not in existing:
                raise InventoryError("A variant in the grid doesn't belong to this product.", 404)
            if prices_locked:
                current = existing[target].get("price_override") if target is not None else None
                if _same_price(row.price, current) is False:
                    raise InventoryError("Only the owner can change prices.", 403)
            if target is None:
                await self.db.add_variant(store_id, product_id, row.color, row.size, row.stock, row.price)
                result.added += 1
            else:
                await self.db.update_variant(store_id, target, {
                    "color": row.color, "size": row.size, "stock_quantity": row.stock,
                    "price_override": str(row.price) if row.price is not None else None,
                })
                result.updated += 1
        for variant_id in remove or []:
            variant = existing.get(variant_id)
            if variant is None:
                raise InventoryError("A variant to remove doesn't belong to this product.", 404)
            try:
                await self.db.delete_variant(store_id, variant_id)
                result.removed += 1
            except NotFoundError:
                raise InventoryError("Variant not found.", 404)
            except DatabaseError:  # ordered before: its order lines point to it
                await self.db.update_variant(store_id, variant_id, {"stock_quantity": 0})
                result.kept_off_sale.append(_label(variant))
        return result

    async def change_stock(self, store_id: UUID, variant_id: UUID, *, change: int | None = None,
                           set_to: int | None = None) -> int:
        """Add or remove stock (`change`, e.g. +5 or -1), or set the exact count
        (`set_to`, after counting). Returns the new stock. Never below 0."""
        if (change is None) == (set_to is None):
            raise InventoryError("Give either a change or a new stock number.")
        try:
            if set_to is not None:
                if set_to < 0:
                    raise InventoryError("Stock can't be below 0.")
                row = await self.db.update_variant(store_id, variant_id, {"stock_quantity": set_to})
                return int(row["stock_quantity"])
            return await self.db.update_stock(store_id, variant_id, change)
        except NotFoundError:
            raise InventoryError("Variant not found.", 404)
        except OutOfStockError:
            raise InventoryError("There isn't that much in stock: stock can't go below 0.", 409)

    async def take_off_sale(self, store_id: UUID, product_id: UUID) -> int:
        await self.product(store_id, product_id)
        return await self.db.take_off_sale(store_id, product_id)

    async def delete(self, store_id: UUID, product_id: UUID) -> None:
        """Delete a product that was never ordered; refused otherwise."""
        await self.product(store_id, product_id)
        try:
            await self.db.delete_product(store_id, product_id)
        except NotFoundError:
            raise InventoryError("Product not found.", 404)
        except DatabaseError:
            raise InventoryError("This product has orders, so it can't be deleted. "
                                 "Take it off sale instead.", 409)
