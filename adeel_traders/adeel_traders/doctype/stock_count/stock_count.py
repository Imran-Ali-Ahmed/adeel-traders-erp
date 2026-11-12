# Copyright (c) 2026, Nesway and contributors
# For license information, please see license.txt
"""A stock count sheet (SRS 3.3).

Staff scan products into it; every counted row is kept (ERPNext's Stock Reconciliation drops
rows without a difference on each save, so it can't be the sheet itself). The system quantity
and the difference are refreshed on every save. When a Stock Manager submits the count, ERPNext's
own Stock Reconciliation is created and submitted for the rows that differ: that posts the
stock and the accounting. Cancelling the count cancels that reconciliation.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowtime

from adeel_traders.permissions import assert_can_act_on_warehouse


class StockCount(Document):
	def validate(self):
		assert_can_act_on_warehouse(self.warehouse)
		seen = set()
		for row in self.items:
			key = (row.item_code, row.batch_no or "")
			if key in seen:
				frappe.throw(_("Row {0}: {1} is counted twice").format(row.idx, row.item_code))
			seen.add(key)
			item = frappe.get_cached_value(
				"Item", row.item_code, ["is_stock_item", "has_batch_no"], as_dict=True
			)
			if not item or not item.is_stock_item:
				frappe.throw(
					_("Row {0}: {1} has no stock of its own (gift set?)").format(row.idx, row.item_code)
				)
			if item.has_batch_no and not row.batch_no:
				frappe.throw(_("Row {0}: choose the batch of {1}").format(row.idx, row.item_code))
			if flt(row.counted_qty) < 0:
				frappe.throw(_("Row {0}: counted quantity cannot be negative").format(row.idx))
		if self.docstatus == 0:
			self.refresh_system_quantities()
		self.total_counted = sum(flt(r.counted_qty) for r in self.items)
		self.total_difference = sum(flt(r.difference) for r in self.items)
		self.difference_value = sum(flt(r.difference_value) for r in self.items)

	def refresh_system_quantities(self):
		from erpnext.stock.doctype.batch.batch import get_batch_qty
		from erpnext.stock.utils import get_stock_balance

		for row in self.items:
			qty, rate = get_stock_balance(
				row.item_code, self.warehouse, self.posting_date, nowtime(), with_valuation_rate=True
			)
			if row.batch_no:
				qty = flt(
					get_batch_qty(batch_no=row.batch_no, warehouse=self.warehouse, item_code=row.item_code)
				)
			row.system_qty = flt(qty)
			row.valuation_rate = flt(rate)
			row.difference = flt(row.counted_qty) - row.system_qty
			row.difference_value = flt(row.difference * row.valuation_rate, 2)

	def unscanned(self) -> list[dict]:
		"""Preview for an open count: stock here that nobody scanned (see _missing_rows)."""
		if self.docstatus != 0:
			return []  # a posted count keeps the lines it posted (see before_submit)
		return self._missing_rows()

	def _missing_rows(self) -> list[dict]:
		"""Products with stock here that nobody scanned, when "not scanned = zero" is on.
		Batch-tracked products are left out: they must be scanned, batch by batch."""
		if not self.unscanned_as_zero:
			return []
		scanned = {r.item_code for r in self.items}
		bins = frappe.get_all(
			"Bin",
			filters={"warehouse": self.warehouse, "actual_qty": (">", 0)},
			fields=["item_code", "actual_qty", "stock_value"],
		)
		names = {
			i.name: i
			for i in frappe.get_all(
				"Item",
				filters={
					"name": ("in", [b.item_code for b in bins] or [""]),
					"is_stock_item": 1,
					"has_batch_no": 0,
				},
				fields=["name", "item_name"],
			)
		}
		return [
			{
				"item_code": b.item_code,
				"item_name": names[b.item_code].item_name,
				"system": flt(b.actual_qty),
				"rate": flt(b.stock_value) / flt(b.actual_qty) if flt(b.actual_qty) else 0,
				"difference": -flt(b.actual_qty),
				"difference_value": -flt(b.stock_value, 2),
			}
			for b in bins
			if b.item_code in names and b.item_code not in scanned
		]

	def before_submit(self):
		"""Freeze the "not scanned = zero" lines into the count, so the posted report keeps them
		even after the stock has been corrected."""
		for m in self._missing_rows():
			self.append(
				"items",
				{
					"item_code": m["item_code"],
					"item_name": m["item_name"],
					"counted_qty": 0,
					"system_qty": m["system"],
					"valuation_rate": m["rate"],
					"difference": m["difference"],
					"difference_value": m["difference_value"],
					"not_scanned": 1,
				},
			)
		self.total_counted = sum(flt(r.counted_qty) for r in self.items)
		self.total_difference = sum(flt(r.difference) for r in self.items)
		self.difference_value = sum(flt(r.difference_value) for r in self.items)

	def on_submit(self):
		changed = [r for r in self.items if flt(r.difference)]
		if not changed:
			return  # counted and found correct: nothing to post
		reco = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"purpose": "Stock Reconciliation",
				"company": self.company,
				"posting_date": self.posting_date,
				"set_posting_time": 1,
				"posting_time": nowtime(),
				"set_warehouse": self.warehouse,
				"items": [
					{
						"item_code": r.item_code,
						"warehouse": self.warehouse,
						"qty": flt(r.counted_qty),
						"valuation_rate": flt(r.valuation_rate) or None,
						"batch_no": r.batch_no or None,
						"use_serial_batch_fields": 1 if r.batch_no else 0,
					}
					for r in changed
				],
			}
		)
		reco.insert()
		reco.submit()
		self.db_set("stock_reconciliation", reco.name)

	def on_cancel(self):
		if self.stock_reconciliation:
			reco = frappe.get_doc("Stock Reconciliation", self.stock_reconciliation)
			if reco.docstatus == 1:
				reco.cancel()
