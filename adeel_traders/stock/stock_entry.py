"""Stock Entry rules (registered in hooks.py doc_events)."""

import frappe
from frappe import _

from adeel_traders.setup import data


def validate_adjustment_reason(doc, method=None):
	"""SRS 3.3: damaged / tester / expired / lost adjustments must state a reason.

	Enforced on the server; the form only shows the field as mandatory for convenience.
	"""
	if (
		doc.stock_entry_type in data.ADJUSTMENT_TYPES_NEEDING_REASON
		and not (doc.get("at_adjustment_reason") or "").strip()
	):
		frappe.throw(
			_("Please enter the reason for this {0} stock entry.").format(_(doc.stock_entry_type)),
			title=_("Reason required"),
		)
