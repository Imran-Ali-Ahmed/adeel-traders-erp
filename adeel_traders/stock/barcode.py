"""Internal EAN-13 barcodes for items that have none (SRS 3.4).

Format: "20" + 10-digit counter + EAN-13 check digit. GS1 reserves prefixes 20-29 for
in-store use, so generated codes never clash with real manufacturer barcodes.
ERPNext's Item validation additionally rejects duplicates and wrong EAN-13 check digits.
"""

import frappe
from frappe import _
from frappe.model.naming import getseries

from adeel_traders import permissions
from adeel_traders.setup import data

MAX_ATTEMPTS = 20


def ean13_check_digit(first_12_digits: str) -> str:
	"""Return the EAN-13 check digit for a 12-digit string."""
	if len(first_12_digits) != 12 or not first_12_digits.isdigit():
		raise ValueError("EAN-13 needs exactly 12 digits before the check digit")
	total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first_12_digits))
	return str((10 - total % 10) % 10)


def next_internal_ean13() -> str:
	"""Next unused internal EAN-13. The counter row is locked, so parallel calls never collide."""
	for _attempt in range(MAX_ATTEMPTS):
		body = data.BARCODE_PREFIX + getseries(data.BARCODE_SERIES_KEY, 10)
		code = body + ean13_check_digit(body)
		if not frappe.db.exists("Item Barcode", {"barcode": code}):
			return code
	frappe.throw(_("Could not find a free internal barcode. Please contact the administrator."))


def add_internal_barcode(item) -> str:
	"""Add an internal EAN-13 barcode to an item document (permissions are checked by the caller)."""
	code = next_internal_ean13()
	item.append("barcodes", {"barcode": code, "barcode_type": "EAN-13", "uom": item.stock_uom})
	item.save()  # standard validation: duplicate check + EAN-13 check digit
	return code


@frappe.whitelist()
def generate_item_barcode(item_code: str) -> str:
	"""Add an internal EAN-13 barcode to an item that has no barcode yet."""
	permissions.require("barcode.generate_print")  # product editors and store staff
	item = frappe.get_doc("Item", item_code)
	item.check_permission("read")
	item.flags.ignore_permissions = True  # store staff may add a barcode but not edit the product

	if item.barcodes:
		frappe.throw(
			_("Item {0} already has a barcode. Existing barcodes are never replaced.").format(
				frappe.bold(item_code)
			)
		)
	return add_internal_barcode(item)
