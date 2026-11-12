"""Change history (Phase 1, P1.5; SRS 3.13 "record of every change").

Nothing is recorded here. Frappe already keeps:
  - a Version for every save of a tracked record (old -> new for each field and table row),
  - Comments (our own "Info" notes, e.g. a person's branches changed),
  - Deleted Document for anything deleted, and the Activity Log for logins.
This module only reads them and puts them in plain words for the staff app, with our rules:
  - you see the history of a record only if you may read the record,
  - cost fields are left out for people who may not see cost,
  - the global log (every area) needs "audit.view" (owner and accountant).

Contract used by web/apps/staff/lib/api.ts:
	get_history(doctype, name, start, page_length) -> {events, has_more}
	audit_log(user, area, from_date, to_date, start, page_length) -> {events, has_more, areas, people}
	event = {at, user, user_name, kind, summary, changes[{field, from, to}], doctype, name, title,
	         link{doctype, name} (the record that has a page in the app), area}
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, strip_html

from adeel_traders import permissions

# Areas of the app, and the ERPNext records behind each. The audit log filters by these.
AREAS: dict[str, tuple[str, ...]] = {
	"Products": ("Item", "Product Bundle"),
	"Prices": ("Item Price", "Pricing Rule"),
	"Catalogue": ("Item Group", "Brand", "UOM"),
	"Stock": ("Stock Entry", "Stock Reconciliation", "Stock Count", "Warehouse"),
	"People": ("User",),
	"Roles": ("Role", "Role Profile"),
	"Company": ("Company", "Branch", "Address", "Letter Head"),
	"Settings": (
		"Mode of Payment",
		"Email Template",
		"Stock Settings",
		"Selling Settings",
		"System Settings",
	),
}
DOCTYPE_AREA = {d: area for area, doctypes in AREAS.items() for d in doctypes}
# What each record is called in a sentence: "deleted the address Adeel Traders-Office".
TYPE_WORD = {
	"Item": "product",
	"Item Price": "price",
	"Product Bundle": "gift set",
	"Pricing Rule": "pricing rule",
	"Item Group": "category",
	"Brand": "brand",
	"UOM": "unit",
	"Stock Entry": "stock entry",
	"Stock Reconciliation": "stock count posting",
	"Stock Count": "stock count",
	"Warehouse": "stock location",
	"User": "person",
	"Role": "role",
	"Role Profile": "role profile",
	"Company": "company",
	"Branch": "branch",
	"Address": "address",
	"Letter Head": "letterhead",
	"Mode of Payment": "payment method",
	"Email Template": "email template",
	"Stock Settings": "stock settings",
	"Selling Settings": "selling settings",
	"System Settings": "system settings",
}
# Field names that read badly in a sentence ("changed What this role is for").
LABEL_WORDS = {"at_description": "the description"}
LOGINS = "Logins"

# Fields that show what something cost us. Only cost viewers see them.
COST_FIELDS = {
	"valuation_rate",
	"last_purchase_rate",
	"incoming_rate",
	"basic_rate",
	"basic_amount",
	"amount_difference",
	"valuation_rate_difference",
	"total_incoming_value",
	"total_outgoing_value",
	"value_difference",
	"stock_value_difference",
	"difference_value",
}
# Bookkeeping fields nobody needs to read about.
SKIP_FIELDS = {
	"modified",
	"modified_by",
	"idx",
	"lft",
	"rgt",
	"old_parent",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
	"naming_series",
}
COMMENT_TYPES = ("Info", "Comment", "Workflow", "Attachment", "Attachment Removed", "Edit")
PAGE_MAX = 100


def _names(users: set[str]) -> dict[str, str]:
	if not users:
		return {}
	return dict(
		frappe.get_all(
			"User", filters={"name": ["in", list(users)]}, fields=["name", "full_name"], as_list=True
		)
	)


def _value(df, value) -> str:
	if value in (None, ""):
		return "blank"
	if df and df.fieldtype == "Check":
		return "Yes" if cint(value) else "No"
	if df and df.fieldtype == "Currency":
		# Frappe stores the formatted amount ("₨ 450.00"); older or raw values are numbers
		if isinstance(value, int | float):
			return "Rs " + f"{value:,.2f}".removesuffix(".00")
		text = re.sub(r"^(₨|PKR|Rs\.?)\s*", "", cstr(value).strip())
		return "Rs " + text.removesuffix(".00")
	if df and df.fieldtype in ("Date", "Datetime"):
		return cstr(value)[:16]
	text = strip_html(cstr(value)).strip()
	return text if len(text) <= 80 else text[:77] + "..."


def _label(meta, fieldname: str) -> str:
	if fieldname in LABEL_WORDS:
		return LABEL_WORDS[fieldname]
	df = meta.get_field(fieldname)
	if fieldname == "docstatus":
		return "Status"
	return _(df.label) if df and df.label else fieldname.replace("_", " ").capitalize()


def _row_title(row: dict) -> str:
	"""A short name for a table row: the first useful value in it."""
	for key in (
		"role_profile",
		"role",
		"barcode",
		"uom",
		"item_code",
		"for_value",
		"attribute_value",
		"warehouse",
		"price_list",
	):
		if row.get(key):
			return cstr(row[key])
	return ""


def _visible(meta, fieldname: str, see_cost: bool) -> bool:
	if fieldname in SKIP_FIELDS or (fieldname in COST_FIELDS and not see_cost):
		return False
	df = meta.get_field(fieldname)
	if df and (df.fieldtype in ("Password", "Code", "HTML Editor") or df.fieldname.startswith("_")):
		return False
	return bool(df) or fieldname in ("docstatus", "name", "workflow_state")


DOCSTATUS = {0: "Draft", 1: "Submitted", 2: "Cancelled"}


def _blank(value) -> bool:
	"""None, "", 0 and "0.0" all mean "nothing set"; a change between them is not a change."""
	return (
		value in (None, "")
		or (isinstance(value, int | float) and not value)
		or cstr(value).strip() in ("0", "0.0")
	)


def _version_event(v: dict, see_cost: bool) -> dict | None:
	try:
		data = json.loads(v.data or "{}")
	except ValueError:
		return None
	meta = frappe.get_meta(v.ref_doctype)
	changes = []
	kind = "changed"
	for fieldname, old, new in data.get("changed") or []:
		if not _visible(meta, fieldname, see_cost) or (_blank(old) and _blank(new)):
			continue
		if fieldname == "docstatus":
			kind = {1: "submitted", 2: "cancelled"}.get(cint(new), kind)
			old, new = DOCSTATUS.get(cint(old), old), DOCSTATUS.get(cint(new), new)
			changes.append({"field": "Status", "from": old, "to": new})
			continue
		df = meta.get_field(fieldname)
		changes.append({"field": _label(meta, fieldname), "from": _value(df, old), "to": _value(df, new)})
	for key, verb in (("added", "Added"), ("removed", "Removed")):
		for table, row in data.get(key) or []:
			title = _row_title(row or {})
			changes.append(
				{
					"field": _label(meta, table),
					"from": "" if verb == "Added" else title,
					"to": title if verb == "Added" else "",
					"action": verb.lower(),
				}
			)
	for table, _idx, _row, fields in data.get("row_changed") or []:
		child = frappe.get_meta(meta.get_field(table).options) if meta.get_field(table) else meta
		for fieldname, old, new in fields:
			if not _visible(child, fieldname, see_cost) or (_blank(old) and _blank(new)):
				continue
			df = child.get_field(fieldname)
			changes.append(
				{
					"field": f"{_label(meta, table)} · {_label(child, fieldname)}",
					"from": _value(df, old),
					"to": _value(df, new),
				}
			)
	if not changes:
		return None
	return {
		"at": cstr(v.creation),
		"user": v.owner,
		"kind": kind,
		"summary": _summary(kind, changes),
		"changes": changes,
		"doctype": v.ref_doctype,
		"name": v.docname,
	}


def _summary(kind: str, changes: list[dict]) -> str:
	if kind in ("submitted", "cancelled", "created", "deleted"):
		return kind.capitalize()
	first = changes[0]
	if first.get("action") == "added":
		text = f"Added {first['to'] or 'a row'} to {first['field']}"
	elif first.get("action") == "removed":
		text = f"Removed {first['from'] or 'a row'} from {first['field']}"
	else:
		text = f"Changed {first['field']}"
	return text + (f" and {len(changes) - 1} more" if len(changes) > 1 else "")


def _comment_event(c: dict) -> dict:
	text = strip_html(re.sub(r"<br\s*/?>", " · ", cstr(c.content))).strip()
	kind = "note" if c.comment_type in ("Info", "Comment") else c.comment_type.lower().replace(" ", "_")
	return {
		"at": cstr(c.creation),
		"user": c.owner,
		"kind": kind,
		"summary": text if c.comment_type != "Attachment" else f"Attached {text}",
		"changes": [],
		"doctype": c.reference_doctype,
		"name": c.reference_name,
	}


def _price_lists_hidden(see_cost: bool) -> set[str]:
	return set() if see_cost else set(frappe.get_all("Price List", filters={"buying": 1}, pluck="name"))


def _related(doctype: str, name: str, see_cost: bool) -> list[tuple[str, str]]:
	"""Records whose history belongs on this page too: a product's prices."""
	if doctype != "Item":
		return []
	hidden = _price_lists_hidden(see_cost)
	return [
		("Item Price", p.name)
		for p in frappe.get_all("Item Price", filters={"item_code": name}, fields=["name", "price_list"])
		if p.price_list not in hidden
	]


def _record(e: dict) -> None:
	"""The record in words, and the record that has a page in the app ("link")."""
	e.setdefault("title", e["name"])
	e["link"] = {"doctype": e["doctype"], "name": e["name"]}
	if e["doctype"] == "Item Price":
		price = e.pop("_data", None) or frappe.db.get_value(
			"Item Price", e["name"], ["item_code", "item_name", "price_list"], as_dict=True
		)
		if price:
			e["title"] = (
				f"{price.get('item_name') or price.get('item_code')} · {price.get('price_list')} price"
			)
			e["link"] = {"doctype": "Item", "name": price.get("item_code")}
	elif e["doctype"] == "User":
		e["title"] = frappe.db.get_value("User", e["name"], "full_name") or e["name"]
	e.pop("_data", None)
	# a price's title already says what it is ("Lipstick · Wholesale price")
	e["type_word"] = "" if e["doctype"] == "Item Price" else TYPE_WORD.get(e["doctype"], "")


def _group_failed_logins(events: list[dict]) -> list[dict]:
	"""Wrong-password tries by one person within 15 minutes become one line ("6 failed logins")."""
	events.sort(key=lambda e: e["at"], reverse=True)
	out: list[dict] = []
	open_group: dict[str, dict] = {}
	for e in events:
		if e["kind"] != "login_failed":
			e.pop("_ip", None)
			out.append(e)
			continue
		g = open_group.get(e["user"])
		if (
			g
			and (frappe.utils.get_datetime(g["_oldest"]) - frappe.utils.get_datetime(e["at"])).total_seconds()
			<= 900
		):
			g["_count"] += 1
			g["_oldest"] = e["at"]
			g["_ips"].add(e.pop("_ip", ""))
			continue
		e["_count"], e["_oldest"], e["_ips"] = 1, e["at"], {e.pop("_ip", "")}
		open_group[e["user"]] = e
		out.append(e)
	for e in out:
		if e["kind"] == "login_failed":
			n = e.pop("_count")
			e.pop("_oldest")
			ips = sorted(i for i in e.pop("_ips") if i)
			e["summary"] = "Failed login" if n == 1 else f"{n} failed logins"
			e["detail"] = ("From " + ", ".join(ips)) if ips else None
	return out


def _matches(e: dict, text: str) -> bool:
	hay = [e.get("summary"), e.get("title"), e.get("name"), e.get("user_name"), e.get("type_word")]
	for c in e.get("changes") or []:
		hay += [c.get("field"), c.get("from"), c.get("to")]
	return any(text in cstr(h).lower() for h in hay if h)


def _finish(events: list[dict], start: int, page_length: int, search: str | None = None) -> dict:
	events = _group_failed_logins(events)
	names = _names({e["user"] for e in events})
	for e in events:
		_record(e)
		e["user_name"] = names.get(e["user"]) or e["user"]
		e["area"] = (
			LOGINS if e["kind"] in ("login", "logout", "login_failed") else DOCTYPE_AREA.get(e["doctype"], "")
		)
	if text := (search or "").strip().lower():
		events = [e for e in events if _matches(e, text)]
	return {"events": events[start : start + page_length], "has_more": len(events) > start + page_length}


@frappe.whitelist(methods=["GET"])
def get_history(doctype: str, name: str, start: int = 0, page_length: int = 20) -> dict:
	"""Everything that happened to one record (and its prices, for a product), newest first."""
	if doctype not in DOCTYPE_AREA:
		frappe.throw(_("No history is kept for {0}").format(doctype))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found").format(_(doctype), name), frappe.DoesNotExistError)
	if not frappe.has_permission(doctype, "read", name):
		frappe.throw(_("You do not have permission to see this history."), frappe.PermissionError)
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), PAGE_MAX)
	see_cost = permissions.can("cost.view")
	records = [(doctype, name), *_related(doctype, name, see_cost)]
	limit = start + page_length + 1

	events = []
	for dt, dn in records:
		for v in frappe.get_all(
			"Version",
			filters={"ref_doctype": dt, "docname": dn},
			fields=["owner", "creation", "data", "ref_doctype", "docname"],
			order_by="creation desc",
			limit=limit,
		):
			if event := _version_event(v, see_cost):
				events.append(event)
		for c in frappe.get_all(
			"Comment",
			filters={"reference_doctype": dt, "reference_name": dn, "comment_type": ["in", COMMENT_TYPES]},
			fields=["owner", "creation", "content", "comment_type", "reference_doctype", "reference_name"],
			order_by="creation desc",
			limit=limit,
		):
			events.append(_comment_event(c))
		created = frappe.db.get_value(dt, dn, ["owner", "creation"], as_dict=True)
		summary = "Created"
		if dt == "Item Price":
			price = frappe.db.get_value("Item Price", dn, ["price_list", "price_list_rate"], as_dict=True)
			summary = f"Set the {price.price_list} price: {_value(frappe.get_meta(dt).get_field('price_list_rate'), price.price_list_rate)}"
		events.append(
			{
				"at": cstr(created.creation),
				"user": created.owner,
				"kind": "created",
				"summary": summary,
				"changes": [],
				"doctype": dt,
				"name": dn,
			}
		)
	return _finish(events, start, page_length)


@frappe.whitelist(methods=["GET"])
def audit_log(
	user: str | None = None,
	area: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	search: str | None = None,
	include_system: int | bool = 0,
	start: int = 0,
	page_length: int = 50,
) -> dict:
	"""Every change in the business, newest first (owner, manager and accountant).

	Changes made by Administrator (setup, scripts, developers) are left out unless include_system
	is on. Logins are shown under the area "Logins" (the app calls it Security), not in the full log."""
	permissions.require("audit.view")
	if area and area not in AREAS and area != LOGINS:
		frappe.throw(_("Unknown area {0}").format(area))
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), PAGE_MAX)
	limit = start + page_length + 1
	see_cost = permissions.can("cost.view")
	hidden_prices = _price_lists_hidden(see_cost)

	if from_date and to_date and getdate(from_date) > getdate(to_date):
		frappe.throw(_("The start date is after the end date."))
	low = f"{getdate(from_date)} 00:00:00" if from_date else None
	high = f"{getdate(to_date)} 23:59:59" if to_date else None

	def dated(filters: dict) -> dict:
		if low and high:
			filters["creation"] = ["between", [low, high]]
		elif low:
			filters["creation"] = [">=", low]
		elif high:
			filters["creation"] = ["<=", high]
		if user:
			filters["owner"] = user
		elif not cint(include_system):
			filters["owner"] = ["!=", "Administrator"]
		return filters

	events: list[dict] = []
	if area != LOGINS:
		doctypes = list(AREAS[area]) if area else list(DOCTYPE_AREA)
		# fetch extra rows, because some versions hold only hidden fields and are skipped
		for v in frappe.get_all(
			"Version",
			filters=dated({"ref_doctype": ["in", doctypes]}),
			fields=["owner", "creation", "data", "ref_doctype", "docname"],
			order_by="creation desc",
			limit=limit * 3,
		):
			if (
				v.ref_doctype == "Item Price"
				and frappe.db.get_value("Item Price", v.docname, "price_list") in hidden_prices
			):
				continue
			if event := _version_event(v, see_cost):
				events.append(event)
		for c in frappe.get_all(
			"Comment",
			filters=dated({"reference_doctype": ["in", doctypes], "comment_type": "Info"}),
			fields=["owner", "creation", "content", "comment_type", "reference_doctype", "reference_name"],
			order_by="creation desc",
			limit=limit,
		):
			events.append(_comment_event(c))
		for d in frappe.get_all(
			"Deleted Document",
			filters=dated({"deleted_doctype": ["in", doctypes]}),
			fields=["owner", "creation", "deleted_doctype", "deleted_name", "data"],
			order_by="creation desc",
			limit=limit,
		):
			try:
				deleted = json.loads(d.data or "{}")
			except ValueError:
				deleted = {}
			events.append(
				{
					"at": cstr(d.creation),
					"user": d.owner,
					"kind": "deleted",
					"summary": "Deleted",
					"changes": [],
					"doctype": d.deleted_doctype,
					"name": d.deleted_name,
					"title": deleted.get("title")
					or deleted.get("item_name")
					or deleted.get("full_name")
					or d.deleted_name,
					"_data": deleted if d.deleted_doctype == "Item Price" else None,
				}
			)
	if area == LOGINS:
		# logins and logouts live under "Logins" (Security), so they never bury the business changes
		login_filters = dated({"operation": ["in", ["Login", "Logout"]]})
		if "owner" in login_filters:
			login_filters["user"] = login_filters.pop("owner")
		for a in frappe.get_all(
			"Activity Log",
			filters=login_filters,
			fields=["user", "creation", "operation", "status", "ip_address"],
			order_by="creation desc",
			limit=limit * 5,  # failed tries are grouped, so read more rows than the page holds
		):
			failed = a.status == "Failed"
			events.append(
				{
					"at": cstr(a.creation),
					"user": a.user,
					"kind": "login_failed" if failed else a.operation.lower(),
					"summary": "Failed login"
					if failed
					else "Logged in"
					if a.operation == "Login"
					else "Logged out",
					"detail": None if failed else (f"From {a.ip_address}" if a.ip_address else None),
					"_ip": a.ip_address or "",
					"changes": [],
					"doctype": "User",
					"name": a.user,
				}
			)
	out = _finish(events, start, page_length, search)
	out["areas"] = [*AREAS, LOGINS]
	out["people"] = frappe.get_all(
		"User",
		filters={"user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)
	return out
