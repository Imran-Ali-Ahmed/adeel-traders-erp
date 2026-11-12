"""Stock counts by barcode scanning (SRS 3.3), on top of our Stock Count DocType.

Store staff (Stock Counter / Stock User) create counts and scan; a Stock Manager submits, which
creates ERPNext's Stock Reconciliation for the differences. Permissions come from the DocType.
"""

import frappe
from frappe import _
from frappe.utils import flt

from adeel_traders import permissions


def _company() -> str:
	return frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")


def _summary(doc) -> dict:
	"""One count as the screen needs it.

	Blind count: while a count is still open, people who cannot post it (store staff) are not
	sent the system quantities or differences at all. This is enforced here, not in the screen."""
	can_cost = permissions.can("cost.view")
	can_post = bool(frappe.has_permission("Stock Count", "submit"))
	blind = doc.docstatus == 0 and not can_post
	missing = [] if blind else doc.unscanned()

	def system_view(value):
		return None if blind else flt(value)

	rows = [
		{
			"row": r.name,
			"item_code": r.item_code,
			"item_name": r.item_name,
			"batch_no": r.batch_no,
			"not_scanned": bool(r.not_scanned),
			"counted": flt(r.counted_qty),
			"system": system_view(r.system_qty),
			"difference": system_view(r.difference),
			"difference_value": flt(r.difference_value) if can_cost and not blind else None,
		}
		for r in doc.items
	]
	missing_value = sum(m["difference_value"] for m in missing)
	return {
		"name": doc.name,
		"warehouse": doc.warehouse,
		"posting_date": str(doc.posting_date),
		"status": {0: "Draft", 1: "Submitted", 2: "Cancelled"}[doc.docstatus],
		"stock_reconciliation": doc.stock_reconciliation,
		"unscanned_as_zero": bool(doc.unscanned_as_zero),
		"rows": rows,
		"unscanned": [
			{
				"item_code": m["item_code"],
				"item_name": m["item_name"],
				"system": m["system"],
				"difference": m["difference"],
				"difference_value": m["difference_value"] if can_cost else None,
			}
			for m in missing
		],
		"totals": {
			"counted": flt(doc.total_counted),
			"difference": None
			if blind
			else flt(doc.total_difference) + sum(m["difference"] for m in missing),
			"difference_value": (flt(doc.difference_value) + missing_value)
			if can_cost and not blind
			else None,
			"rows": len(doc.items),
			"rows_with_difference": None
			if blind
			else sum(1 for r in doc.items if flt(r.difference)) + len(missing),
		},
		"can": {
			"edit": doc.docstatus == 0 and bool(doc.has_permission("write")),
			"submit": doc.docstatus == 0 and can_post,
			"cancel": doc.docstatus == 1 and bool(frappe.has_permission("Stock Count", "cancel")),
			"email": bool(doc.has_permission("email")),  # P1.8: email the count sheet
		},
	}


@frappe.whitelist(methods=["GET"])
def list_counts(start: int = 0, page_length: int = 25) -> dict:
	can_post = bool(frappe.has_permission("Stock Count", "submit"))
	rows = frappe.get_list(
		"Stock Count",
		fields=[
			"name",
			"warehouse",
			"posting_date",
			"docstatus",
			"total_counted",
			"total_difference",
			"modified",
			"owner",
		],
		order_by="creation desc",
		limit_start=start,
		limit_page_length=min(int(page_length), 100),
	)
	return {
		"counts": [
			{
				"name": r.name,
				"warehouse": r.warehouse,
				"posting_date": str(r.posting_date),
				"status": {0: "Draft", 1: "Submitted", 2: "Cancelled"}[r.docstatus],
				"counted": flt(r.total_counted),
				# blind count: open counts show no difference to people who cannot post them
				"difference": None if r.docstatus == 0 and not can_post else flt(r.total_difference),
				"by": frappe.utils.get_fullname(r.owner),
			}
			for r in rows
		],
		"can_create": bool(frappe.has_permission("Stock Count", "create")),
		"warehouses": frappe.get_list(
			"Warehouse",
			filters={"company": _company(), "is_group": 0, "disabled": 0},
			pluck="name",
			order_by="name asc",
			limit_page_length=0,
		),
	}


@frappe.whitelist(methods=["POST"])
def start_count(warehouse: str) -> dict:
	doc = frappe.get_doc({"doctype": "Stock Count", "warehouse": warehouse, "company": _company()}).insert()
	return _summary(doc)


@frappe.whitelist(methods=["GET"])
def get_count(name: str) -> dict:
	doc = frappe.get_doc("Stock Count", name)
	doc.check_permission("read")
	return _summary(doc)


def _draft(name: str):
	doc = frappe.get_doc("Stock Count", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("This count is already submitted"))
	return doc


@frappe.whitelist(methods=["POST"])
def scan(name: str, code: str, qty: float = 1, batch_no: str | None = None) -> dict:
	"""Add one scanned product (or `qty` of it). `code` is a barcode or a product code."""
	doc = _draft(name)
	code = (code or "").strip()
	item_code = frappe.db.get_value("Item Barcode", {"barcode": code}, "parent") or (
		code if frappe.db.exists("Item", code) else None
	)
	if not item_code:
		frappe.throw(_("No product has the barcode or code {0}").format(code))
	item = frappe.get_cached_value(
		"Item", item_code, ["is_stock_item", "has_batch_no", "item_name"], as_dict=True
	)
	if not item.is_stock_item:
		frappe.throw(_("{0} is a gift set; count its parts instead").format(item.item_name))
	if item.has_batch_no and not batch_no:
		from erpnext.stock.doctype.batch.batch import get_batch_qty

		batches = get_batch_qty(warehouse=doc.warehouse, item_code=item_code) or []
		return {
			"needs_batch": True,
			"item_code": item_code,
			"item_name": item.item_name,
			"batches": [{"batch_no": b.get("batch_no"), "qty": flt(b.get("qty"))} for b in batches],
		}
	row = next(
		(r for r in doc.items if r.item_code == item_code and (r.batch_no or None) == (batch_no or None)),
		None,
	)
	if row:
		row.counted_qty = flt(row.counted_qty) + flt(qty)
	else:
		doc.append(
			"items",
			{
				"item_code": item_code,
				"barcode": code if code != item_code else None,
				"batch_no": batch_no,
				"counted_qty": flt(qty),
			},
		)
	doc.save()
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def set_counted(name: str, row: str, counted: float) -> dict:
	doc = _draft(name)
	target = next((r for r in doc.items if r.name == row), None)
	if not target:
		frappe.throw(_("That row is not in this count"))
	if flt(counted) < 0:
		frappe.throw(_("Counted quantity cannot be negative"))
	target.counted_qty = flt(counted)
	doc.save()
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def remove_row(name: str, row: str) -> dict:
	doc = _draft(name)
	doc.items = [r for r in doc.items if r.name != row]
	doc.save()
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def submit_count(name: str) -> dict:
	doc = frappe.get_doc("Stock Count", name)
	if not doc.items:
		frappe.throw(_("Scan at least one product before submitting"))
	doc.submit()  # Stock Manager only (DocType permission); posts the differences via ERPNext
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def set_unscanned_as_zero(name: str, value: bool = True) -> dict:
	"""Full count of the location: stock that nobody scanned is treated as missing."""
	doc = _draft(name)
	doc.unscanned_as_zero = 1 if frappe.parse_json(value) else 0
	doc.save()
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def cancel_count(name: str) -> dict:
	"""Undo a posted count: ERPNext cancels its stock correction and the stock goes back."""
	doc = frappe.get_doc("Stock Count", name)
	if doc.docstatus != 1:
		frappe.throw(_("Only a posted count can be cancelled"))
	doc.cancel()  # Stock Manager only (DocType permission); cancels the Stock Reconciliation
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def delete_count(name: str) -> dict:
	doc = _draft(name)
	frappe.delete_doc("Stock Count", doc.name)
	return {"deleted": name}
