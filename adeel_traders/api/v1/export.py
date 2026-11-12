"""Export any list to Excel (Phase 1, P1.9; SRS 6 "export lists and reports").

One rule for every list: the file holds exactly what the screen shows. Each export calls the
same list function as the screen (same filters, same permission checks, cost left out for people
who may not see cost), page by page, and turns the rows into a sheet with Frappe's own
xlsxutils. Every export is written to Frappe's Access Log (who exported what, with which filters).

Who may export: the "export.lists" capability (the "Can export lists" switch on a role).

Contract used by web/apps/staff/lib/api.ts (a file download):
	export_list(what, search, status, sort, brand, item_group, category, branch, from_date, to_date, report)
	what = products | people | stock-counts | report
"""

import json

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from adeel_traders import permissions
from adeel_traders.api.v1 import people, products, reports, stock_count

EXPORT_MAX = 10_000  # rows; larger lists should be filtered first
STATUS_WORDS = {
	"in_stock": "In stock",
	"low_stock": "Low stock",
	"out_of_stock": "Out of stock",
	None: "",
}


def _products(f: dict) -> tuple[str, list[str], list[list]]:
	rows, start = [], 0
	while True:
		page = products.list_products(
			search=f.get("search"),
			item_group=f.get("item_group") or f.get("category"),
			status=f.get("status") or "all",
			sort=f.get("sort") or "name",
			brand=f.get("brand"),
			start=start,
			page_length=200,
		)
		rows += page["items"]
		start += 200
		if start >= page["total"] or start >= EXPORT_MAX:
			break
	cost = page["can_view_cost"]
	header = [
		"Code",
		"Product",
		"Category",
		"Brand",
		"Barcode",
		"Unit",
		"Available to sell",
		"Not for sale",
		"Reorder level",
		"Status",
	]
	if cost:
		header.append("Cost (Rs)")
	price_lists = [p["price_list"] for p in rows[0]["prices"]] if rows else []
	for pl in price_lists:
		header.append(f"{pl} price (Rs)")
		if cost:
			header.append(f"{pl} margin %")
	out = []
	for r in rows:
		line = [
			r["item_code"],
			r["item_name"],
			r["item_group"],
			r["brand"] or "",
			r["barcode"] or "",
			r["stock_uom"],
			r["stock_qty"],
			r["not_for_sale_qty"],
			r["reorder_level"],
			"Gift set" if not r["is_stock_item"] else STATUS_WORDS.get(r["status"], r["status"]),
		]
		if cost:
			line.append(r["cost"])
		for p in r["prices"]:
			line.append(p["rate"])
			if cost:
				line.append(p["margin"])
		out.append(line)
	return "Products", header, out


def _people(_f: dict) -> tuple[str, list[str], list[list]]:
	data = people.list_people()  # needs users.manage
	header = ["Name", "Email", "Role", "Works at", "Login", "Last login", "Temporary password"]
	out = [
		[
			p["name"],
			p["email"],
			", ".join(r["label"] for r in p["roles"]),
			", ".join(p["branches"]) or "All branches",
			"Enabled" if p["enabled"] else "Disabled",
			str(p["last_login"] or ""),
			"Yes" if p["must_change_password"] else "No",
		]
		for p in data["people"]
	]
	return "People", header, out


def _stock_counts(_f: dict) -> tuple[str, list[str], list[list]]:
	rows, start = [], 0
	while True:
		page = stock_count.list_counts(start=start, page_length=100)
		rows += page["counts"]
		start += 100
		if len(page["counts"]) < 100 or start >= EXPORT_MAX:
			break
	header = ["Count", "Location", "Date", "Status", "Counted", "Difference", "Counted by"]
	out = [
		[
			c["name"],
			c["warehouse"].rsplit(" - ", 1)[0],
			getdate(c["posting_date"]),
			c["status"],
			c["counted"],
			c["difference"],  # empty for counters while a count is open (blind count)
			c["by"],
		]
		for c in rows
	]
	return "Stock counts", header, out


def _report(f: dict) -> tuple[str, list[str], list[list]]:
	spec, columns, rows = reports.report_rows(f.get("report") or "", f)
	return spec.title, [c.label for c in columns], [[r[c.key] for c in columns] for r in rows[:EXPORT_MAX]]


EXPORTS = {
	"products": _products,
	"people": _people,
	"stock-counts": _stock_counts,
	"report": _report,
}


@frappe.whitelist(methods=["GET"])
def export_list(
	what: str,
	search: str | None = None,
	status: str | None = None,
	sort: str | None = None,
	brand: str | None = None,
	item_group: str | None = None,
	category: str | None = None,
	branch: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	report: str | None = None,
	hide_zero: int | None = None,
) -> None:
	permissions.require("export.lists")
	if what not in EXPORTS:
		frappe.throw(_("Unknown list {0}").format(what))
	filters = {
		k: v
		for k, v in {
			"search": search,
			"status": status,
			"sort": sort,
			"brand": brand,
			"item_group": item_group,
			"category": category,
			"branch": branch,
			"from_date": from_date,
			"to_date": to_date,
			"report": report,
			"hide_zero": hide_zero,
		}.items()
		if v
	}
	title, header, rows = EXPORTS[what](filters)

	from frappe.core.doctype.access_log.access_log import make_access_log
	from frappe.utils.xlsxutils import build_xlsx_response

	make_access_log(report_name=title, filters=json.dumps(filters), file_type="Excel", method="export_list")
	build_xlsx_response([header, *rows], f"{title} {nowdate()}")
