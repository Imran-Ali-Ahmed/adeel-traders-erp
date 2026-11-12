"""Report kit (Phase 1, P1.10): ERPNext's own reports, served to the staff app with our standard
filters (dates, branch, category, brand) and our rules (cost columns only for cost viewers).

Nothing is recalculated here: ERPNext's report code (frappe.desk.query_report) runs the report and
checks the report permission. This module only
  - maps our filters to the report's filters (a branch becomes its stock locations),
  - keeps the columns the screen shows, in plain words, and hides cost columns when needed,
  - sorts and pages the rows for the screen.
The same rows feed the Excel export (P1.9), so a download always matches the screen.

Contract used by web/apps/staff/lib/api.ts:
	run_report(report, from_date, to_date, branch, category, brand, search, sort, start, page_length)
		-> {key, title, columns[{key,label,type}], rows, total, totals, filters}
"""

from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, getdate, today

from adeel_traders import permissions
from adeel_traders.setup import data

PAGE_LENGTH_MAX = 500


@dataclass(frozen=True)
class Column:
	key: str  # fieldname in the ERPNext report row
	label: str
	type: str = "text"  # text | qty | money | date
	cost: bool = False  # hidden unless the user may see cost
	detail: bool = False  # period movements: shown to managers and the owner, not to shop staff


@dataclass(frozen=True)
class Report:
	title: str
	erpnext_report: str
	description: str
	columns: tuple[Column, ...]
	filters: frozenset[str] = field(default_factory=frozenset)  # dates, branch, category, brand
	search: tuple[str, ...] = ()  # columns the search box looks in


REPORTS: dict[str, Report] = {
	"stock-balance": Report(
		title="Stock balance",
		erpnext_report="Stock Balance",
		description="Stock in, out and left per product and location for a period.",
		columns=(
			Column("item_name", "Product"),
			Column("item_code", "Code"),
			Column("item_group", "Category"),
			Column("brand", "Brand"),
			Column("warehouse", "Location"),
			Column("opening_qty", "Opening", "qty", detail=True),
			Column("in_qty", "In", "qty", detail=True),
			Column("out_qty", "Out", "qty", detail=True),
			Column("bal_qty", "Balance", "qty"),
			Column("bal_val", "Value", "money", cost=True),
			Column("note", "Note"),  # "Not for sale" for damaged, returns to check and testers
		),
		filters=frozenset({"dates", "branch", "category", "brand"}),
		search=("item_name", "item_code"),
	),
}


def _report(key: str) -> Report:
	if key not in REPORTS:
		frappe.throw(_("Unknown report {0}").format(key))
	return REPORTS[key]


def _columns(report: Report) -> list[Column]:
	see_cost = permissions.can("cost.view")
	# the people who see system quantities (managers) also see how stock moved in the period
	see_detail = permissions.can("stock.count.post")
	return [c for c in report.columns if (see_cost or not c.cost) and (see_detail or not c.detail)]


def _company() -> str:
	return (
		frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name", limit=1)[0]
	)


def _not_for_sale_warehouses() -> set[str]:
	"""Damaged, Returns - To Check and Testers: never counted as stock you can sell (DESIGN.md)."""
	abbr = frappe.get_cached_value("Company", _company(), "abbr")
	return {f"{name} - {abbr}" for name in data.NOT_FOR_SALE_WAREHOUSES}


def _branch_warehouses(branch: str) -> list[str]:
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Unknown branch {0}").format(branch))
	return frappe.get_all("Warehouse", filters={"branch": branch, "is_group": 0}, pluck="name") or [
		"__none__"
	]


def _erpnext_filters(report: Report, f: dict) -> dict:
	out = {
		"company": _company(),
		"from_date": str(getdate(f.get("from_date") or get_first_day(today()))),
		"to_date": str(getdate(f.get("to_date") or today())),
	}
	if out["from_date"] > out["to_date"]:
		frappe.throw(_("The start date is after the end date."))
	if "branch" in report.filters and f.get("branch"):
		out["warehouse"] = _branch_warehouses(f["branch"])
	if "category" in report.filters and f.get("category"):
		out["item_group"] = f["category"]
	return out


def report_rows(key: str, filters: dict) -> tuple[Report, list[Column], list[dict]]:
	"""All rows of a report (after our filters), with only the columns this user may see.
	Used by run_report (screen) and the Excel export, so both always agree."""
	report = _report(key)
	from frappe.desk.query_report import _run  # ERPNext's own runner; checks report permission

	result = _run(
		report_name=report.erpnext_report,
		filters=_erpnext_filters(report, filters),
		ignore_prepared_report=True,
	)
	raw = [r for r in result.get("result") or [] if isinstance(r, dict) and r.get("item_code")]

	codes = list({r["item_code"] for r in raw})
	brands = (
		dict(frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "brand"], as_list=True))
		if codes
		else {}
	)
	for r in raw:
		r["brand"] = brands.get(r["item_code"]) or ""

	blocked = _not_for_sale_warehouses()
	for r in raw:
		r["note"] = "Not for sale" if r.get("warehouse") in blocked else ""
	if cint(filters.get("hide_zero")):
		raw = [r for r in raw if flt(r.get("bal_qty"))]
	if "brand" in report.filters and filters.get("brand"):
		raw = [r for r in raw if r["brand"] == filters["brand"]]
	if text := (filters.get("search") or "").strip().lower():
		raw = [r for r in raw if any(text in str(r.get(c) or "").lower() for c in report.search)]

	columns = _columns(report)
	rows = [{c.key: r.get(c.key) for c in columns} for r in raw]

	sort = (filters.get("sort") or "").strip()
	desc = sort.startswith("-")
	sort_key = sort.lstrip("-")
	if sort_key in {c.key for c in columns}:
		numeric = next((c.type in ("qty", "money") for c in columns if c.key == sort_key), False)
		rows.sort(
			key=lambda r: (r[sort_key] or 0) if numeric else str(r[sort_key] or "").lower(),
			reverse=desc,
		)
	return report, columns, rows


def _options(report: Report) -> dict:
	"""Choices for the filter bar (only the filters this report has)."""
	out = {}
	if "branch" in report.filters:
		out["branches"] = frappe.get_all("Branch", pluck="name", order_by="name asc")
	if "category" in report.filters:
		out["categories"] = sorted(set(frappe.get_all("Item", filters={"disabled": 0}, pluck="item_group")))
	if "brand" in report.filters:
		out["brands"] = frappe.get_all("Brand", pluck="name", order_by="name asc")
	return out


@frappe.whitelist(methods=["GET"])
def run_report(
	report: str,
	from_date: str | None = None,
	to_date: str | None = None,
	branch: str | None = None,
	category: str | None = None,
	brand: str | None = None,
	search: str | None = None,
	sort: str | None = None,
	hide_zero: int | bool = 0,
	start: int = 0,
	page_length: int = 50,
) -> dict:
	filters = {
		"from_date": from_date,
		"to_date": to_date,
		"branch": branch,
		"category": category,
		"brand": brand,
		"search": search,
		"sort": sort,
		"hide_zero": hide_zero,
	}
	spec, columns, rows = report_rows(report, filters)
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), PAGE_LENGTH_MAX)
	# Totals are for stock you can sell; damaged, returns and testers are added up on their own.
	sellable = [r for r in rows if not r.get("note")]
	not_for_sale = [r for r in rows if r.get("note")]

	def add(of):
		return {
			c.key: round(sum(r[c.key] or 0 for r in of), 2) for c in columns if c.type in ("qty", "money")
		}

	totals, not_for_sale_totals = add(sellable), add(not_for_sale)
	applied = _erpnext_filters(spec, filters)
	return {
		"key": report,
		"title": spec.title,
		"description": spec.description,
		"columns": [{"key": c.key, "label": c.label, "type": c.type} for c in columns],
		"filters": sorted(spec.filters),
		"options": _options(spec),
		"applied": {"from_date": applied["from_date"], "to_date": applied["to_date"]},
		"rows": rows[start : start + page_length],
		"total": len(rows),
		"totals": totals,
		"not_for_sale_totals": not_for_sale_totals if not_for_sale else None,
	}
