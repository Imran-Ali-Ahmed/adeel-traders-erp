"""Products & stock API for the Next.js staff app (SRS 3.1-3.4).

Rules:
- Every read goes through frappe.get_list / doc.check_permission, so ERPNext roles and
  user permissions apply. Data a user may not read is returned as None, never leaked.
- Cost and margin are only returned to users with the "Can View Cost" role (counter staff must not see cost).
- Writes create real ERPNext documents (Item, Item Price, Stock Entry); all stock and
  accounting logic stays in ERPNext.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from adeel_traders import permissions
from adeel_traders.setup import data

PIECE = "Piece"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _can_view_cost() -> bool:
	return permissions.can("cost.view")


def _margin(price: float | None, cost: float | None) -> float | None:
	if not price or cost is None:
		return None
	return round((price - cost) / price * 100, 1)


def _company() -> str:
	return frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")


def _selling_prices(filters: dict, fields: list[str]) -> list:
	"""Rows of the 3 channel SELLING price lists only.

	Standard ERPNext lets only Master Managers read Item Price, because that doctype also
	holds BUYING prices. Selling prices are public (customers see them), so anyone who may
	read the item may see them here; buying price lists are never queried. Editing still
	requires Item Price write permission (see set_price).
	"""
	filters = {**filters, "price_list": ("in", data.PRICE_LISTS)}
	return frappe.get_all("Item Price", filters=filters, fields=fields, limit_page_length=0)


def _prices_by_item(item_codes: list[str]) -> dict:
	"""{item_code: {price_list: rate}} for the 3 channel price lists, per Piece."""
	if not item_codes:
		return {}
	rows = _selling_prices(
		{"item_code": ("in", item_codes), "uom": PIECE}, ["item_code", "price_list", "price_list_rate"]
	)
	out: dict = {}
	for row in rows:
		out.setdefault(row.item_code, {})[row.price_list] = flt(row.price_list_rate)
	return out


def _not_for_sale_warehouses() -> set[str]:
	"""Full names of the warehouses whose stock can't be sold (damaged, to-check, testers)."""
	abbr = frappe.get_cached_value("Company", _company(), "abbr")
	return {f"{name} - {abbr}" for name in data.NOT_FOR_SALE_WAREHOUSES}


def _stock_by_item(item_codes: list[str]) -> dict | None:
	"""{item_code: {"qty", "value", "reserved", "not_for_sale"}} summed over warehouses.

	qty / value / reserved only count warehouses that can sell; stock sitting in the
	damaged / to-check / tester warehouses is reported separately as not_for_sale."""
	if not item_codes:
		return {}
	if not frappe.has_permission("Bin", "read"):
		return None
	rows = frappe.get_list(
		"Bin",
		filters={"item_code": ("in", item_codes)},
		fields=["item_code", "warehouse", "actual_qty", "stock_value", "reserved_stock"],
		limit_page_length=0,
	)
	blocked = _not_for_sale_warehouses()
	out: dict = {}
	for row in rows:
		s = out.setdefault(
			row.item_code,
			{"qty": 0.0, "value": 0.0, "reserved": 0.0, "not_for_sale": 0.0, "not_for_sale_value": 0.0},
		)
		if row.warehouse in blocked:
			s["not_for_sale"] += flt(row.actual_qty)
			s["not_for_sale_value"] += flt(row.stock_value)
			continue
		s["qty"] += flt(row.actual_qty)
		s["value"] += flt(row.stock_value)
		s["reserved"] += flt(row.reserved_stock)
	return out


def _bundle_availability(bundle_codes: list[str]) -> dict:
	"""{bundle_item_code: sets that can be packed now}.

	A set can only be packed from parts in the SAME warehouse, so we count sets per
	warehouse (the scarcest part decides) and add them up. Only Product Bundles count.
	"""
	if not bundle_codes:
		return {}
	bundles = frappe.get_all(
		"Product Bundle Item",
		filters={"parent": ("in", bundle_codes), "parenttype": "Product Bundle"},
		fields=["parent", "item_code", "qty"],
	)
	if not bundles:
		return {}
	parts = {b.item_code for b in bundles}
	blocked = _not_for_sale_warehouses()
	stock: dict = {}
	for row in frappe.get_list(
		"Bin",
		filters={"item_code": ("in", list(parts))},
		fields=["item_code", "warehouse", "actual_qty", "reserved_stock"],
		limit_page_length=0,
	):
		if row.warehouse in blocked:
			continue  # damaged / testers can't be packed into a set to sell
		stock[(row.item_code, row.warehouse)] = flt(row.actual_qty) - flt(row.reserved_stock)
	warehouses = {w for _, w in stock}

	out: dict = {}
	for code in {b.parent for b in bundles}:
		lines = [b for b in bundles if b.parent == code]
		total = 0
		for warehouse in warehouses:
			total += min(
				int(stock.get((b.item_code, warehouse), 0) // flt(b.qty)) if flt(b.qty) else 0 for b in lines
			)
		out[code] = max(total, 0)
	return out


def _locations_with_stock(item_codes: list[str]) -> int:
	blocked = _not_for_sale_warehouses()
	return len(
		{
			row.warehouse
			for row in frappe.get_list(
				"Bin",
				filters={"item_code": ("in", item_codes), "actual_qty": (">", 0)},
				fields=["warehouse"],
				limit_page_length=0,
			)
			if row.warehouse not in blocked
		}
	)


def _stock_by_location(item_codes: list[str], show_cost: bool) -> list[dict]:
	"""Quantity (and value at cost, for cost viewers) per location, biggest value first."""
	if not item_codes:
		return []
	blocked = _not_for_sale_warehouses()
	totals: dict = {}
	for row in frappe.get_list(
		"Bin",
		filters={"item_code": ("in", item_codes)},
		fields=["warehouse", "actual_qty", "stock_value"],
		limit_page_length=0,
	):
		t = totals.setdefault(row.warehouse, {"qty": 0.0, "value": 0.0})
		t["qty"] += flt(row.actual_qty)
		t["value"] += flt(row.stock_value)
	rows = [
		{
			"warehouse": wh,
			"for_sale": wh not in blocked,
			"qty": t["qty"],
			"value": round(t["value"], 2) if show_cost else None,
		}
		for wh, t in totals.items()
		if t["qty"]
	]
	return sorted(rows, key=lambda r: (not r["for_sale"], -(r["value"] or r["qty"])))


def _bundle_detail(item_code: str) -> dict | None:
	"""Parts of a Product Bundle and how many sets can be packed (None if not a bundle)."""
	parts = frappe.get_all(
		"Product Bundle Item",
		filters={"parent": item_code, "parenttype": "Product Bundle"},
		fields=["item_code", "qty"],
		order_by="idx asc",
	)
	if not parts:
		return None
	names = {
		i.name: i.item_name
		for i in frappe.get_all(
			"Item", filters={"name": ("in", [p.item_code for p in parts])}, fields=["name", "item_name"]
		)
	}
	stock = _stock_by_item([p.item_code for p in parts]) or {}
	rows = []
	for p in parts:
		in_stock = flt(stock.get(p.item_code, {}).get("qty", 0))
		rows.append(
			{
				"item_code": p.item_code,
				"item_name": names.get(p.item_code, p.item_code),
				"qty_per_set": flt(p.qty),
				"in_stock": in_stock,
				"enough_for": int(in_stock // flt(p.qty)) if flt(p.qty) else 0,
			}
		)
	limiting = min(rows, key=lambda r: r["enough_for"])
	for r in rows:
		r["limiting"] = r is limiting
	sets_available = _bundle_availability([item_code]).get(item_code, 0)

	# Why the number is what it is, so the screen can explain a zero.
	blocked_by = None
	if sets_available == 0:
		if limiting["enough_for"] == 0:
			blocked_by = {
				"type": "part_out_of_stock",
				"item_code": limiting["item_code"],
				"item_name": limiting["item_name"],
				"in_stock": limiting["in_stock"],
			}
		else:
			blocked_by = {"type": "parts_in_different_locations"}
	return {"sets_available": sets_available, "blocked_by": blocked_by, "parts": rows}


def _bundle_costs(bundle_codes: list[str]) -> dict:
	"""{bundle: cost of one set}: the sum of its parts' costs. Missing if any part has no cost."""
	if not bundle_codes:
		return {}
	lines = frappe.get_all(
		"Product Bundle Item",
		filters={"parent": ("in", bundle_codes), "parenttype": "Product Bundle"},
		fields=["parent", "item_code", "qty"],
	)
	part_codes = list({line.item_code for line in lines})
	stock = _stock_by_item(part_codes) or {}
	valuation = {
		i.name: flt(i.valuation_rate)
		for i in frappe.get_all(
			"Item", filters={"name": ("in", part_codes)}, fields=["name", "valuation_rate"]
		)
	}
	part_cost = {code: _cost(stock.get(code), valuation.get(code, 0)) for code in part_codes}
	out: dict = {}
	for code in bundle_codes:
		mine = [line for line in lines if line.parent == code]
		if not mine or any(part_cost.get(line.item_code) is None for line in mine):
			out[code] = None
		else:
			out[code] = round(sum(flt(line.qty) * part_cost[line.item_code] for line in mine), 2)
	return out


def _cost(stock: dict | None, fallback_rate: float) -> float | None:
	"""Average cost per Piece = stock value / qty (ERPNext valuation); item valuation rate if no stock."""
	if stock and stock["qty"] > 0:
		return round(stock["value"] / stock["qty"], 2)
	return flt(fallback_rate) or None


def _channel_prices(prices: dict, cost: float | None, show_cost: bool) -> list[dict]:
	return [
		{
			"price_list": pl,
			"rate": prices.get(pl),
			"margin": _margin(prices.get(pl), cost) if show_cost else None,
		}
		for pl in data.PRICE_LISTS
	]


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


RESTOCK_PREVIEW = 3  # items shown on the list page's "Needs restocking" card
STATUSES = ("all", "in_stock", "low_stock", "out_of_stock", "expiring", "restock")
SORTS = {
	"name": (lambda r: (r["item_name"] or "").lower(), False),
	"stock_low": (lambda r: r["_qty"], False),
	"stock_high": (lambda r: r["_qty"], True),
	"newest": (lambda r: r["_created"], True),
}


def _reorder_levels(item_codes: list[str]) -> dict:
	"""{item_code: total reorder level over all warehouses} from the item's Reorder table."""
	levels: dict = {}
	for row in frappe.get_all(
		"Item Reorder",
		filters={"parent": ("in", item_codes), "parenttype": "Item"},
		fields=["parent", "warehouse_reorder_level"],
	):
		levels[row.parent] = levels.get(row.parent, 0) + flt(row.warehouse_reorder_level)
	return levels


def _expiring_items(item_codes: list[str]) -> set:
	"""Items with a batch in stock that expires within EXPIRY_WARNING_DAYS (or has expired)."""
	limit = frappe.utils.add_days(frappe.utils.nowdate(), data.EXPIRY_WARNING_DAYS)
	return set(
		frappe.get_all(
			"Batch",
			filters={"item": ("in", item_codes), "expiry_date": ("<=", limit), "batch_qty": (">", 0)},
			pluck="item",
		)
	)


def _main_warehouse() -> str:
	abbr = frappe.get_cached_value("Company", _company(), "abbr")
	return f"{data.DEFAULT_WAREHOUSE} - {abbr}"


def _main_reorder(item) -> dict:
	"""Reorder level and quantity the edit screen shows: the Main Warehouse row."""
	main = _main_warehouse()
	for row in item.reorder_levels:
		if row.warehouse == main:
			return {"level": flt(row.warehouse_reorder_level), "qty": flt(row.warehouse_reorder_qty)}
	return {"level": 0, "qty": 0}


def _status(item, qty: float, reorder_level: float) -> str:
	if not item.is_stock_item:
		return "bundle"
	if qty <= 0:
		return "out_of_stock"
	if reorder_level and qty <= reorder_level:
		return "low_stock"
	return "in_stock"


@frappe.whitelist(methods=["GET"])
def list_products(
	search: str | None = None,
	item_group: str | None = None,
	status: str = "all",
	sort: str = "name",
	include_disabled: bool = False,
	brand: str | None = None,
	start: int = 0,
	page_length: int = 25,
) -> dict:
	"""Sellable products (variants and normal items, not variant templates).

	Every matching product is classified first (stock status is calculated here, on the
	server, from stock levels, reorder levels and batch expiry dates), then the result is
	counted per status, sorted and paged. Returns the page, the counts for the filter
	tabs and the summary for the KPI tiles.
	"""
	frappe.has_permission("Item", "read", throw=True)
	if status not in STATUSES:
		frappe.throw(_("Unknown status filter {0}").format(status))
	if sort not in SORTS:
		frappe.throw(_("Unknown sort {0}").format(sort))
	page_length = min(max(cint(page_length), 1), 200)

	filters: dict = {"has_variants": 0}
	if not include_disabled:
		filters["disabled"] = 0
	if item_group:
		lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"]) or (None, None)
		if lft is None:
			frappe.throw(_("Item Group {0} not found").format(item_group))
		filters["item_group"] = (
			"in",
			frappe.get_all("Item Group", {"lft": (">=", lft), "rgt": ("<=", rgt)}, pluck="name"),
		)

	if brand:
		filters["brand"] = brand

	or_filters = None
	search = (search or "").strip()
	if search:
		barcode_parent = frappe.db.get_value("Item Barcode", {"barcode": search}, "parent")
		if barcode_parent:
			filters["name"] = barcode_parent  # exact barcode scan
		else:
			like = f"%{search}%"
			or_filters = {"name": ("like", like), "item_name": ("like", like), "brand": ("like", like)}

	items = frappe.get_list(
		"Item",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"item_name",
			"item_group",
			"brand",
			"stock_uom",
			"image",
			"disabled",
			"variant_of",
			"has_batch_no",
			"valuation_rate",
			"is_stock_item",
			"creation",
		],
		limit_page_length=0,
	)
	codes = [i.name for i in items]
	stock = _stock_by_item(codes)  # None if the user may not read stock
	can_see_stock = stock is not None
	reorder = _reorder_levels(codes) if codes else {}
	expiring = _expiring_items(codes) if codes and can_see_stock else set()
	show_cost = _can_view_cost()

	classified = []
	for item in items:
		s = (stock or {}).get(
			item.name,
			{"qty": 0.0, "value": 0.0, "reserved": 0.0, "not_for_sale": 0.0, "not_for_sale_value": 0.0},
		)
		classified.append(
			{
				"item": item,
				"stock": s if can_see_stock else None,
				"status": _status(item, s["qty"], reorder.get(item.name, 0)) if can_see_stock else None,
				"expiring": item.name in expiring,
				"item_name": item.item_name,
				"_qty": s["qty"],
				"_created": item.creation,
			}
		)

	stocked = [c for c in classified if c["item"].is_stock_item]
	counts = {
		"all": len(classified),
		"in_stock": sum(1 for c in classified if c["status"] == "in_stock"),
		"low_stock": sum(1 for c in classified if c["status"] == "low_stock"),
		"out_of_stock": sum(1 for c in classified if c["status"] == "out_of_stock"),
		"expiring": len(expiring),
		"restock": 0,  # filled in below, once the restock list is known
	}
	summary = {
		"products": len(classified),
		"units": sum(c["_qty"] for c in stocked) if can_see_stock else None,
		"stock_value": round(sum(c["stock"]["value"] for c in stocked), 2)
		if can_see_stock and show_cost
		else None,
		# damaged / to-check / tester stock: still owned and still on the books, but not sellable
		"not_for_sale_units": sum(c["stock"]["not_for_sale"] for c in stocked) if can_see_stock else None,
		"not_for_sale_value": round(sum(c["stock"]["not_for_sale_value"] for c in stocked), 2)
		if can_see_stock and show_cost
		else None,
		"categories": len({c["item"].item_group for c in classified}),
		"locations": _locations_with_stock(codes) if can_see_stock and codes else None,
		"by_location": _stock_by_location([c["item"].name for c in stocked], show_cost)
		if can_see_stock
		else None,
	}

	# "Needs restocking" = a stock item that HAS a reorder level and is at or below it. Items with
	# no stock and no reorder level are only "out of stock": nothing says we want them in stock.
	restock = sorted(
		(
			c
			for c in stocked
			if can_see_stock and reorder.get(c["item"].name) and c["_qty"] <= reorder[c["item"].name]
		),
		key=lambda c: c["_qty"] - reorder[c["item"].name],  # biggest shortfall first
	)
	counts["restock"] = len(restock)
	restock_summary = {
		"count": len(restock),
		"items": [
			{
				"item_code": c["item"].name,
				"item_name": c["item"].item_name,
				"on_hand": c["_qty"],
				"reorder_level": reorder[c["item"].name],
			}
			for c in restock[:RESTOCK_PREVIEW]
		],
		"out_without_level": sum(
			1
			for c in stocked
			if can_see_stock and c["status"] == "out_of_stock" and not reorder.get(c["item"].name)
		),
	}

	if status == "restock":
		selected = restock
	elif status == "expiring":
		selected = [c for c in classified if c["expiring"]]
	elif status != "all":
		selected = [c for c in classified if c["status"] == status]
	else:
		selected = classified
	key, reverse = SORTS[sort]
	selected.sort(key=key, reverse=reverse)
	page = selected[cint(start) : cint(start) + page_length]

	page_codes = [c["item"].name for c in page]
	prices = _prices_by_item(page_codes)
	barcodes = (
		{
			row.parent: row.barcode
			for row in frappe.get_all(
				"Item Barcode",
				filters={"parent": ("in", page_codes)},
				fields=["parent", "barcode"],
				order_by="idx asc",
			)
		}
		if page_codes
		else {}
	)
	bundles = (
		_bundle_availability([c["item"].name for c in page if not c["item"].is_stock_item])
		if can_see_stock
		else {}
	)

	bundle_costs = (
		_bundle_costs([c["item"].name for c in page if not c["item"].is_stock_item]) if show_cost else {}
	)

	rows = []
	for c in page:
		item, item_stock = c["item"], c["stock"]
		if not show_cost:
			cost = None
		elif not item.is_stock_item:
			cost = bundle_costs.get(item.name)
		else:
			cost = _cost(item_stock, item.valuation_rate)
		rows.append(
			{
				"item_code": item.name,
				"item_name": item.item_name,
				"item_group": item.item_group,
				"brand": item.brand,
				"stock_uom": item.stock_uom,
				"image": item.image,
				"disabled": item.disabled,
				"variant_of": item.variant_of,
				"has_batch_no": item.has_batch_no,
				"barcode": barcodes.get(item.name),
				"cost": cost,
				"prices": _channel_prices(prices.get(item.name, {}), cost, show_cost),
				"stock_qty": item_stock["qty"] if item_stock is not None else None,
				"not_for_sale_qty": item_stock["not_for_sale"] if item_stock is not None else None,
				"reserved_qty": item_stock["reserved"] if item_stock is not None else None,
				"is_stock_item": item.is_stock_item,
				"bundle_available": bundles.get(item.name),
				"reorder_level": reorder.get(item.name) or None,
				"status": c["status"],
				"expiring": c["expiring"],
			}
		)
	return {
		"items": rows,
		"total": len(selected),
		"counts": counts,
		"summary": summary,
		"restock": restock_summary,
		"can_view_cost": show_cost,
	}


@frappe.whitelist(methods=["GET"])
def get_product(item_code: str) -> dict:
	item = frappe.get_doc("Item", item_code)
	item.check_permission("read")
	show_cost = _can_view_cost()

	warehouses = None
	if frappe.has_permission("Bin", "read"):
		warehouses = frappe.get_list(
			"Bin",
			filters={"item_code": item_code},
			fields=["warehouse", "actual_qty", "reserved_stock", "projected_qty", "stock_value"],
			order_by="warehouse asc",
			limit_page_length=0,
		)
	blocked = _not_for_sale_warehouses()
	totals = None
	if warehouses is not None:
		selling = [w for w in warehouses if w.warehouse not in blocked]
		totals = {
			"qty": sum(flt(w.actual_qty) for w in selling),
			"value": sum(flt(w.stock_value) for w in selling),
		}
	if not show_cost:
		cost = None
	elif not item.is_stock_item:
		cost = _bundle_costs([item_code]).get(item_code)
	else:
		cost = _cost(totals, item.valuation_rate)
	reorder_level = _reorder_levels([item_code]).get(item_code, 0)

	all_prices = _selling_prices({"item_code": item_code}, ["price_list", "uom", "price_list_rate"])
	piece_prices = {p.price_list: flt(p.price_list_rate) for p in all_prices if p.uom == PIECE}
	conversion = {u.uom: flt(u.conversion_factor) for u in item.uoms}
	unit_prices = [
		{
			"price_list": p.price_list,
			"uom": p.uom,
			"rate": flt(p.price_list_rate),
			"per_piece": round(flt(p.price_list_rate) / conversion[p.uom], 2)
			if conversion.get(p.uom)
			else None,
			# how much cheaper per piece than buying the same number of pieces one by one
			"discount": round(
				(piece_prices[p.price_list] * conversion[p.uom] - flt(p.price_list_rate))
				/ (piece_prices[p.price_list] * conversion[p.uom])
				* 100,
				1,
			)
			if piece_prices.get(p.price_list) and conversion.get(p.uom)
			else None,
			# margin of the price per piece against the cost per piece
			"margin": _margin(flt(p.price_list_rate) / conversion[p.uom], cost)
			if show_cost and conversion.get(p.uom)
			else None,
		}
		for p in all_prices
		if p.uom != PIECE
	]

	batches = []
	if item.has_batch_no and warehouses is not None:
		from erpnext.stock.doctype.batch.batch import get_batch_qty

		expiry = {
			b.name: b.expiry_date
			for b in frappe.get_list(
				"Batch", filters={"item": item_code}, fields=["name", "expiry_date"], limit_page_length=0
			)
		}
		for row in get_batch_qty(item_code=item_code) or []:
			if flt(row.get("qty")) > 0:
				batches.append(
					{
						"batch_no": row.get("batch_no"),
						"warehouse": row.get("warehouse"),
						"qty": flt(row.get("qty")),
						"expiry_date": expiry.get(row.get("batch_no")),
					}
				)
		batches.sort(key=lambda b: (b["expiry_date"] is None, b["expiry_date"] or ""))

	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"item_group": item.item_group,
		"brand": item.brand,
		"description": item.description,
		"image": item.image,
		"stock_uom": item.stock_uom,
		"disabled": item.disabled,
		"variant_of": item.variant_of,
		"attributes": [{"attribute": a.attribute, "value": a.attribute_value} for a in item.attributes],
		"has_batch_no": item.has_batch_no,
		"is_stock_item": item.is_stock_item,
		"status": _status(item, totals["qty"] if totals else 0, reorder_level)
		if totals is not None
		else None,
		"reorder_level": reorder_level or None,
		"reorder_main": _main_reorder(item),
		# value of the sellable stock at cost (damaged and testers are left out)
		"stock_value": round(totals["value"], 2) if show_cost and totals is not None else None,
		# everything physically there = sellable + not for sale
		"physical_qty": sum(flt(w.actual_qty) for w in warehouses) if warehouses is not None else None,
		"not_for_sale_qty": sum(flt(w.actual_qty) for w in warehouses if w.warehouse in blocked)
		if warehouses is not None
		else None,
		"not_for_sale_value": round(sum(flt(w.stock_value) for w in warehouses if w.warehouse in blocked), 2)
		if warehouses is not None and show_cost
		else None,
		"bundle": _bundle_detail(item.name) if not item.is_stock_item else None,
		"has_expiry_date": item.has_expiry_date,
		"shelf_life_in_days": item.shelf_life_in_days,
		"barcodes": [{"barcode": b.barcode, "type": b.barcode_type} for b in item.barcodes],
		"uoms": [{"uom": u.uom, "conversion_factor": flt(u.conversion_factor)} for u in item.uoms],
		"cost": cost,
		"prices": _channel_prices(piece_prices, cost, show_cost),
		"unit_prices": unit_prices,
		"warehouses": [
			{
				"warehouse": w.warehouse,
				"for_sale": w.warehouse not in blocked,
				"qty": flt(w.actual_qty),
				"reserved": flt(w.reserved_stock),
				"value": round(flt(w.stock_value), 2) if show_cost else None,
				"projected": flt(w.projected_qty),
			}
			for w in (warehouses or [])
		]
		if warehouses is not None
		else None,
		"batches": batches,
		"can": {
			"view_cost": show_cost,
			"edit_prices": bool(frappe.has_permission("Item Price", "write")),
			# Only stock items can be moved; a gift set's stock lives in its parts.
			"move_stock": bool(item.is_stock_item and frappe.has_permission("Stock Entry", "create")),
			"generate_barcode": not item.barcodes and permissions.can("barcode.generate_print"),
			"edit_product": bool(item.has_permission("write")),
			"add_shade": bool(item.variant_of and frappe.has_permission("Item", "create")),
			"add_variant": bool(item.variant_of and frappe.has_permission("Item", "create")),
		},
	}


@frappe.whitelist(methods=["GET"])
def form_options() -> dict:
	"""Values for the staff app's dropdowns, filtered by the user's permissions.

	Each list is returned only when the role may read it, and left empty otherwise, so a role that
	can add products but has no stock access (set on the Roles page) still gets a working form."""
	company = _company()

	def readable(doctype: str) -> bool:
		return bool(frappe.has_permission(doctype, "read"))

	def warehouses(fetch):
		if not readable("Warehouse"):
			return []
		return fetch(
			"Warehouse",
			filters={"company": company, "is_group": 0, "disabled": 0},
			pluck="name",
			order_by="name asc",
			limit_page_length=0,
		)

	item_groups = (
		[
			g
			for g in frappe.get_list(
				"Item Group", filters={"is_group": 0}, pluck="name", order_by="name asc", limit_page_length=0
			)
			# ERPNext's own default categories stay hidden until a product uses one
			if g not in data.ERPNEXT_FACTORY_ITEM_GROUPS or frappe.db.exists("Item", {"item_group": g})
		]
		if readable("Item Group")
		else []
	)
	return {
		# where stock may leave from: own branch only (see permissions.py)
		"warehouses": warehouses(frappe.get_list),
		# where stock may go to: any branch
		"all_warehouses": warehouses(frappe.get_all),
		"item_groups": item_groups,
		"uoms": [u for u in data.UOMS if frappe.db.exists("UOM", u)],
		"brands": frappe.get_list("Brand", pluck="name", order_by="name asc", limit_page_length=0)
		if readable("Brand")
		else [],
		"price_lists": data.PRICE_LISTS,
		"adjustment_types": list(data.STOCK_ENTRY_TYPES),
	}


# ---------------------------------------------------------------------------
# writes (POST only; Frappe checks the CSRF token and each document's permissions)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def set_price(item_code: str, price_list: str, rate: float, uom: str = PIECE) -> dict:
	if price_list not in data.PRICE_LISTS:
		frappe.throw(_("Unknown price list {0}").format(price_list))
	if flt(rate) < 0:
		frappe.throw(_("Price cannot be negative"))
	item = frappe.get_doc("Item", item_code)
	item.check_permission("read")
	if uom != item.stock_uom and uom not in {u.uom for u in item.uoms}:
		frappe.throw(_("{0} is not a unit of this product. Add the unit first.").format(uom))

	name = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": price_list, "uom": uom})
	if name:
		doc = frappe.get_doc("Item Price", name)
		doc.price_list_rate = flt(rate)
		doc.save()
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item_code,
				"price_list": price_list,
				"uom": uom,
				"price_list_rate": flt(rate),
			}
		).insert()
	return {"price_list": price_list, "uom": uom, "rate": flt(doc.price_list_rate)}


@frappe.whitelist(methods=["POST"])
def create_product(
	item_code: str,
	item_name: str,
	item_group: str,
	stock_uom: str = PIECE,
	has_batch_no: bool = False,
	shelf_life_in_days: int = 0,
	prices: dict | None = None,
	barcode: str | None = None,
	brand: str | None = None,
	description: str | None = None,
) -> dict:
	"""Create a simple (non-variant) product with its channel prices.

	barcode: the code already printed on the product (scanned or typed). Left empty, an internal
	EAN-13 barcode is generated so every product can be scanned from day one."""
	barcode = (barcode or "").strip()
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code.strip(),
			"item_name": item_name.strip(),
			"item_group": item_group,
			"brand": brand or None,
			"description": (description or "").strip() or None,
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"has_batch_no": int(bool(has_batch_no)),
			"create_new_batch": int(bool(has_batch_no)),
			"has_expiry_date": int(bool(has_batch_no) and cint(shelf_life_in_days) > 0),
			"shelf_life_in_days": cint(shelf_life_in_days) or None,
			"barcodes": [{"barcode": barcode, "barcode_type": _barcode_type(barcode), "uom": stock_uom}]
			if barcode
			else [],
		}
	).insert()  # permission + all standard Item validations (duplicate barcode, EAN check digit)
	if not barcode:
		from adeel_traders.stock.barcode import add_internal_barcode

		item.flags.ignore_permissions = True  # the user just created this product
		add_internal_barcode(item)
	for price_list, rate in (prices or {}).items():
		if rate not in (None, ""):
			set_price(item.name, price_list, flt(rate))
	return {"item_code": item.name}


@frappe.whitelist(methods=["POST"])
def update_product(
	item_code: str,
	item_name: str,
	item_group: str,
	reorder_level: float = 0,
	reorder_qty: float = 0,
	disabled: bool = False,
	brand: str | None = None,
	description: str | None = None,
) -> dict:
	"""Edit the basics of a product. Item permissions and validations apply (it is a normal save)."""
	if not item_name.strip():
		frappe.throw(_("Product name is required"))
	if flt(reorder_level) < 0 or flt(reorder_qty) < 0:
		frappe.throw(_("Reorder numbers cannot be negative"))

	item = frappe.get_doc("Item", item_code)
	item.item_name = item_name.strip()
	item.item_group = item_group
	item.brand = brand or None
	item.description = (description or "").strip() or None
	item.disabled = int(bool(disabled))

	main = _main_warehouse()
	row = next((r for r in item.reorder_levels if r.warehouse == main), None)
	if flt(reorder_level) > 0:
		if row is None:
			row = item.append("reorder_levels", {"warehouse": main, "material_request_type": "Purchase"})
		row.warehouse_reorder_level = flt(reorder_level)
		row.warehouse_reorder_qty = flt(reorder_qty)
	elif row is not None:
		item.remove(row)
	item.save()
	return {"item_code": item.name}


@frappe.whitelist(methods=["GET"])
def variant_attributes(item_code: str) -> list[dict]:
	"""The attributes a new variant needs (from the product's template), with existing values."""
	template = _template_of(item_code)
	out = []
	for row in template.attributes:
		attribute = frappe.get_cached_doc("Item Attribute", row.attribute)
		out.append(
			{
				"attribute": row.attribute,
				"values": [
					{"value": v.attribute_value, "abbr": v.abbr} for v in attribute.item_attribute_values
				],
			}
		)
	return out


@frappe.whitelist(methods=["POST"])
def add_variant(item_code: str, values: dict | str) -> dict:
	"""A new variant of the same template (shade, size, fragrance…), made by ERPNext's own
	variant code. `values` = {attribute: {"value": "18 Coral Pink", "abbr": "18"}}; a value that
	does not exist yet is added to the attribute (it needs a short code)."""
	from erpnext.controllers.item_variant import create_variant, get_variant

	values = frappe.parse_json(values) or {}
	template = _template_of(item_code)
	args = {}
	for row in template.attributes:
		given = values.get(row.attribute) or {}
		value = (given.get("value") or "").strip()
		abbr = (given.get("abbr") or "").strip()
		if not value:
			frappe.throw(_("Enter a {0}").format(row.attribute))
		attribute = frappe.get_doc("Item Attribute", row.attribute)
		known = {v.attribute_value for v in attribute.item_attribute_values}
		if value not in known:
			if not abbr:
				frappe.throw(_("Enter a short code for the new {0} {1}").format(row.attribute, value))
			if abbr in {v.abbr for v in attribute.item_attribute_values}:
				frappe.throw(_("Code {0} is already used by another {1}").format(abbr, row.attribute))
			attribute.append("item_attribute_values", {"attribute_value": value, "abbr": abbr})
			attribute.save()
		args[row.attribute] = value

	if get_variant(template.name, args):
		frappe.throw(_("{0} already has {1}").format(template.item_name, ", ".join(args.values())))
	variant = create_variant(template.name, args)
	variant.insert()
	return {"item_code": variant.name}


def _template_of(item_code: str):
	template_code = frappe.db.get_value("Item", item_code, "variant_of")
	if not template_code:
		frappe.throw(_("{0} is not a variant of a product with shades or sizes").format(item_code))
	template = frappe.get_doc("Item", template_code)
	template.check_permission("read")
	return template


@frappe.whitelist(methods=["POST"])
def add_unit(item_code: str, uom: str, pieces: float) -> dict:
	"""Add a selling unit such as Box = 12 Piece (a new row in the item's unit table)."""
	if uom not in data.UOMS:
		frappe.throw(_("Unknown unit {0}").format(uom))
	if flt(pieces) <= 0:
		frappe.throw(_("Pieces in the unit must be more than zero"))
	item = frappe.get_doc("Item", item_code)
	if uom == item.stock_uom or uom in {u.uom for u in item.uoms}:
		frappe.throw(_("{0} is already a unit of this product").format(uom))
	item.append("uoms", {"uom": uom, "conversion_factor": flt(pieces)})
	item.save()
	return {"uom": uom, "conversion_factor": flt(pieces)}


@frappe.whitelist(methods=["POST"])
def set_product_image(item_code: str, file_url: str | None = None) -> dict:
	"""Use an uploaded file as the product photo (or clear it). The file is uploaded with
	Frappe's standard upload_file, attached to the Item."""
	item = frappe.get_doc("Item", item_code)
	item.check_permission("write")
	if file_url:
		owned = frappe.db.exists(
			"File", {"file_url": file_url, "attached_to_doctype": "Item", "attached_to_name": item.name}
		)
		if not owned:
			frappe.throw(_("Upload the photo for this product first"))
	item.image = file_url or None
	item.save()
	return {"image": item.image}


def _clean_parts(parts) -> list[dict]:
	parts = frappe.parse_json(parts) or []
	clean, seen = [], set()
	for part in parts:
		code, qty = (part.get("item_code") or "").strip(), flt(part.get("qty"))
		if not code or qty <= 0:
			frappe.throw(_("Every part needs a product and a quantity above zero"))
		if code in seen:
			frappe.throw(_("{0} is listed twice").format(code))
		if not frappe.db.get_value("Item", code, "is_stock_item"):
			frappe.throw(_("{0} cannot be a part: only products with their own stock can").format(code))
		seen.add(code)
		clean.append({"item_code": code, "qty": qty})
	if len(clean) < 2:
		frappe.throw(_("A gift set needs at least two parts"))
	return clean


@frappe.whitelist(methods=["POST"])
def create_gift_set(
	item_code: str,
	item_name: str,
	parts: list | str,
	item_group: str | None = None,
	prices: dict | None = None,
	barcode: str | None = None,
) -> dict:
	"""A gift set = a non-stock product + ERPNext Product Bundle. Selling one takes each part
	out of stock (standard ERPNext behaviour)."""
	clean = _clean_parts(parts)
	group = item_group or data.GIFT_SET_GROUP
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code.strip(),
			"item_name": item_name.strip(),
			"item_group": group,
			"stock_uom": PIECE,
			"is_stock_item": 0,
			"barcodes": [
				{"barcode": barcode.strip(), "barcode_type": _barcode_type(barcode.strip()), "uom": PIECE}
			]
			if barcode and barcode.strip()
			else [],
		}
	).insert()
	frappe.get_doc({"doctype": "Product Bundle", "new_item_code": item.name, "items": clean}).insert()
	if not item.barcodes:
		from adeel_traders.stock.barcode import add_internal_barcode

		item.flags.ignore_permissions = True  # the user just created this gift set
		add_internal_barcode(item)
	for price_list, rate in (prices or {}).items():
		if rate not in (None, ""):
			set_price(item.name, price_list, flt(rate))
	return {"item_code": item.name}


@frappe.whitelist(methods=["POST"])
def update_gift_set_parts(item_code: str, parts: list | str) -> dict:
	clean = _clean_parts(parts)
	name = frappe.db.get_value("Product Bundle", {"new_item_code": item_code})
	if not name:
		frappe.throw(_("{0} is not a gift set").format(item_code))
	bundle = frappe.get_doc("Product Bundle", name)
	bundle.set("items", clean)
	bundle.save()  # permission + standard validation
	return {"item_code": item_code}


# What a stock movement means, in the words staff use.
_MOVEMENT_WORDS = {
	"Purchase Receipt": "Received",
	"Purchase Invoice": "Received",
	"Delivery Note": "Sold",
	"Sales Invoice": "Sold",
	"POS Invoice": "Sold",
	"Stock Reconciliation": "Counted",
}
_STOCK_ENTRY_WORDS = {
	"Material Transfer": "Moved",
	"Material Receipt": "Received",
	"Material Issue": "Written off",
	"Damaged": "Damaged",
	"Tester": "Tester",
	"Expired": "Expired",
	"Lost": "Lost",
}


@frappe.whitelist(methods=["GET"])
def get_stock_history(item_code: str, start: int = 0, page_length: int = 20) -> dict:
	"""Every stock movement of one product, newest first, from ERPNext's Stock Ledger."""
	frappe.get_doc("Item", item_code).check_permission("read")
	page_length = min(max(cint(page_length), 1), 100)
	show_cost = _can_view_cost()
	rows = frappe.get_list(
		"Stock Ledger Entry",
		filters={"item_code": item_code, "is_cancelled": 0},
		fields=[
			"posting_date",
			"posting_time",
			"voucher_type",
			"voucher_no",
			"warehouse",
			"actual_qty",
			"qty_after_transaction",
			"stock_value_difference",
		],
		order_by="posting_datetime desc, creation desc",
		limit_start=cint(start),
		limit_page_length=page_length + 1,
	)
	more = len(rows) > page_length
	rows = rows[:page_length]
	entry_types = {
		se.name: se.stock_entry_type
		for se in frappe.get_all(
			"Stock Entry",
			filters={"name": ("in", [r.voucher_no for r in rows if r.voucher_type == "Stock Entry"] or [""])},
			fields=["name", "stock_entry_type"],
		)
	}
	reco_purpose = {
		sr.name: sr.purpose
		for sr in frappe.get_all(
			"Stock Reconciliation",
			filters={
				"name": (
					"in",
					[r.voucher_no for r in rows if r.voucher_type == "Stock Reconciliation"] or [""],
				)
			},
			fields=["name", "purpose"],
		)
	}

	def what(r):
		if r.voucher_type == "Stock Entry":
			return _STOCK_ENTRY_WORDS.get(entry_types.get(r.voucher_no), "Stock entry")
		if r.voucher_type == "Stock Reconciliation" and reco_purpose.get(r.voucher_no) == "Opening Stock":
			return "Opening stock"
		return _MOVEMENT_WORDS.get(r.voucher_type, r.voucher_type)

	blocked = _not_for_sale_warehouses()
	return {
		"rows": [
			{
				"date": str(r.posting_date),
				"time": str(r.posting_time)[:5],
				"what": what(r),
				"document_type": r.voucher_type,
				"document": r.voucher_no,
				"location": r.warehouse,
				"for_sale": r.warehouse not in blocked,
				"qty": flt(r.actual_qty),
				"balance_here": flt(r.qty_after_transaction),
				"value": round(flt(r.stock_value_difference), 2) if show_cost else None,
			}
			for r in rows
		],
		"more": more,
	}


def _barcode_type(code: str) -> str:
	"""Let ERPNext check the digits of the common retail formats; anything else is stored as is."""
	if code.isdigit() and len(code) == 13:
		return "EAN-13"
	if code.isdigit() and len(code) == 12:
		return "UPC-A"
	return ""


def _stock_entry(entry_type: str, purpose: str, items: list[dict], reason: str | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": entry_type,
			"purpose": purpose,
			"company": _company(),
			"at_adjustment_reason": reason,
			"items": items,
		}
	)
	doc.insert()  # checks create permission + our reason rule + ERPNext stock validations
	doc.submit()  # checks submit permission; ERPNext picks batches by expiry (FEFO)
	return doc.name


@frappe.whitelist(methods=["POST"])
def transfer_stock(item_code: str, from_warehouse: str, to_warehouse: str, qty: float) -> dict:
	if flt(qty) <= 0:
		frappe.throw(_("Quantity must be more than zero"))
	if from_warehouse == to_warehouse:
		frappe.throw(_("Source and target warehouse must be different"))
	name = _stock_entry(
		"Material Transfer",
		"Material Transfer",
		[
			{
				"item_code": item_code,
				"qty": flt(qty),
				"s_warehouse": from_warehouse,
				"t_warehouse": to_warehouse,
			}
		],
	)
	return {"stock_entry": name}


@frappe.whitelist(methods=["POST"])
def adjust_stock(item_code: str, warehouse: str, qty: float, adjustment_type: str, reason: str) -> dict:
	"""Damaged / Tester: move to their own warehouse. Expired / Lost: write off (SRS 3.3)."""
	purpose = data.STOCK_ENTRY_TYPES.get(adjustment_type)
	if not purpose:
		frappe.throw(_("Unknown adjustment type {0}").format(adjustment_type))
	if flt(qty) <= 0:
		frappe.throw(_("Quantity must be more than zero"))

	row = {"item_code": item_code, "qty": flt(qty), "s_warehouse": warehouse}
	if purpose == "Material Transfer":
		abbr = frappe.get_cached_value("Company", _company(), "abbr")
		row["t_warehouse"] = f"{data.ADJUSTMENT_TARGET_WAREHOUSES[adjustment_type]} - {abbr}"
	name = _stock_entry(adjustment_type, purpose, [row], reason=reason)
	return {"stock_entry": name}
