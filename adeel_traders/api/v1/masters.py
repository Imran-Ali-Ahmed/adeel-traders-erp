"""Settings lists (Phase 1, P1.12): units, payment methods, and the numbering of documents.

One small kit for every "simple list" in Settings (the Catalogue pattern): list with "used by N",
add, rename (Frappe's rename, every link follows), switch off, and delete only when nothing uses
it. Catalogue (categories, brands) uses the same rename and delete helpers. Later phases add their
lists here (expense types in Phase 7, leave types in Phase 11) with one line each.

Numbering is shown, not changed: document numbers are decided in Phase 2 (D6) before the first
real document.

Contract used by web/apps/staff/lib/api.ts:
	list_masters(kind)                      -> {kind, title, items[{name, enabled, used, type}], types, can_edit}
	                                           units add: base, and per unit standard, whole_number, core,
	                                           base_unit, can_delete
	add_master(kind, name, type, whole_number, pieces) -> {name}   (whole_number and pieces: units only)
	set_standard_size(name, pieces)         -> {name, standard}    (units: pieces in one unit; empty clears)
	rename_master(kind, old_name, new_name) -> {name}
	set_master_enabled(kind, name, enabled) -> {name, enabled}
	delete_master(kind, name)               -> {deleted}
	numbering()                             -> {documents[{doctype, label, series[{series, current, next}]}]}
"""

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import cint, flt

from adeel_traders import permissions
from adeel_traders.setup import data
from adeel_traders.setup.core_setup import ensure_unit_category


@dataclass(frozen=True)
class Master:
	doctype: str
	title: str
	capability: str
	# (doctype, field) pairs that point to an entry: "used by N" and "delete only when unused"
	usage: tuple[tuple[str, str], ...]
	types: tuple[str, ...] = ()  # e.g. Cash / Bank for payment methods


MASTERS: dict[str, Master] = {
	"units": Master(
		"UOM",
		"Units",
		"units.manage",
		(("Item", "stock_uom"), ("UOM Conversion Detail", "uom"), ("Item Price", "uom")),
	),
	"payment-methods": Master(
		"Mode of Payment",
		"Payment methods",
		"payments.manage",
		(
			("Payment Entry", "mode_of_payment"),
			("Sales Invoice Payment", "mode_of_payment"),
			("POS Payment Method", "mode_of_payment"),
		),
		("Cash", "Bank", "General", "Phone"),
	),
}


def _master(kind: str) -> Master:
	if kind not in MASTERS:
		frappe.throw(_("Unknown list {0}").format(kind))
	return MASTERS[kind]


def base_unit() -> str:
	"""The unit all stock is counted in (Frappe's Stock Settings > Default UOM): Piece."""
	return frappe.db.get_single_value("Stock Settings", "stock_uom") or "Piece"


def _protect(kind: str, name: str, action: str) -> None:
	"""Entries the app or ERPNext rely on by name cannot be changed in these ways: the base unit and
	the units our code names (units), and Cash (payment methods)."""
	if kind == "payment-methods":
		if name in data.PROTECTED_PAYMENT_METHODS:
			frappe.throw(
				_("{0} is used by ERPNext itself (the till and payments), so it cannot be {1}.").format(
					name, action
				)
			)
		return
	if name == base_unit():
		frappe.throw(
			_("{0} is the base unit: all stock is counted in it, so it cannot be {1}.").format(name, action)
		)
	if name in data.UOMS and action != "switched off":
		frappe.throw(_("{0} is used by the app itself and cannot be {1}.").format(name, action))


def used_by(doctype: str, name: str, usage) -> int:
	"""How many records point to this entry."""
	return sum(frappe.db.count(dt, {field: name}) for dt, field in usage if frappe.db.exists("DocType", dt))


# Shared with catalogue.py ---------------------------------------------------


def rename_record(doctype: str, old_name: str, new_name: str) -> str:
	"""Frappe's rename: every record that points to it follows."""
	new_name = (new_name or "").strip()
	if not new_name:
		frappe.throw(_("Enter the new name"))
	if new_name == old_name:
		return old_name
	if frappe.db.exists(doctype, new_name):
		frappe.throw(_("{0} already exists").format(new_name))
	frappe.get_doc(doctype, old_name).check_permission("write")
	return frappe.rename_doc(doctype, old_name, new_name)


def delete_if_unused(doctype: str, name: str, usage, word: str = "record(s)") -> None:
	used = used_by(doctype, name, usage)
	if used:
		frappe.throw(_("{0} is used by {1} {2}; switch it off instead").format(name, used, word))
	try:
		frappe.delete_doc(doctype, name, ignore_permissions=True)  # caller checked the capability
	except frappe.LinkExistsError:  # Frappe found a document we do not count above
		frappe.throw(
			_("{0} is used in documents, so it cannot be deleted. Switch it off instead.").format(name)
		)


# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def list_masters(kind: str) -> dict:
	m = _master(kind)
	frappe.has_permission(m.doctype, "read", throw=True)
	fields = (
		["name", "enabled"]
		+ (["type"] if m.types else [])
		+ (["must_be_whole_number"] if kind == "units" else [])
	)
	rows = frappe.get_all(m.doctype, fields=fields, order_by="enabled desc, name asc", limit_page_length=0)
	if kind == "units":
		# ERPNext's 229 sample units (Acre, Ampere...) stay out of sight, but a unit that is ours, common,
		# used by something or given a size is always listed, switched off or not: it can be switched on again.
		sizes = _standard_sizes()
		keep = set(data.UOMS) | set(data.COMMON_UOMS) | set(sizes)
		rows = [r for r in rows if r.enabled or r.name in keep or used_by(m.doctype, r.name, m.usage)]
	items = []
	for r in rows:
		item = {
			"name": r.name,
			"enabled": bool(r.enabled),
			"used": used_by(m.doctype, r.name, m.usage),
			"type": r.get("type"),
		}
		if kind == "payment-methods":
			protected = r.name in data.PROTECTED_PAYMENT_METHODS
			item.update(core=protected, can_delete=not protected and not item["used"])
		if kind == "units":
			item.update(
				whole_number=bool(r.must_be_whole_number),
				core=r.name in data.UOMS,
				base_unit=r.name == base_unit(),
				standard=_standard_sizes().get(r.name),
				can_delete=r.name not in data.UOMS and r.name != base_unit() and not item["used"],
			)
		items.append(item)
	out = {
		"kind": kind,
		"title": m.title,
		"items": items,
		"types": list(m.types),
		"can_edit": permissions.can(m.capability),
	}
	if kind == "units":
		out["base"] = base_unit()
	return out


def _standard_sizes() -> dict[str, float]:
	"""{unit: pieces} from Frappe's UOM Conversion Factor rows that point to the base unit."""
	return {
		r.from_uom: flt(r.value)
		for r in frappe.get_all(
			"UOM Conversion Factor", filters={"to_uom": base_unit()}, fields=["from_uom", "value"]
		)
		if r.from_uom != base_unit()
	}


@frappe.whitelist(methods=["POST"])
def add_master(
	kind: str, name: str, type: str | None = None, whole_number: int | bool = 1, pieces: float | None = None
) -> dict:
	m = _master(kind)
	permissions.require(m.capability)
	name = (name or "").strip()
	if not 1 <= len(name) <= 60:
		frappe.throw(_("Enter a name of up to 60 characters."))
	if m.types and type not in m.types:
		frappe.throw(_("Choose the kind: {0}").format(", ".join(m.types)))
	existing = frappe.db.sql(
		f"select name, enabled from `tab{m.doctype}` where lower(name)=%s", name.lower(), as_dict=True
	)
	if existing:
		if existing[0].enabled:
			frappe.throw(_("{0} already exists").format(existing[0].name))
		# a switched-off entry (e.g. one of ERPNext's sample units) is simply switched back on
		frappe.get_doc(m.doctype, existing[0].name).update({"enabled": 1}).save(ignore_permissions=True)
		return {"name": existing[0].name}
	values = {"doctype": m.doctype, "enabled": 1}
	if m.doctype == "UOM":
		values.update({"uom_name": name, "must_be_whole_number": cint(whole_number)})
	else:
		values.update({"mode_of_payment": name, "type": type})
	doc = frappe.get_doc(values).insert(ignore_permissions=True)  # capability checked above
	if kind == "units" and pieces not in (None, ""):
		set_standard_size(doc.name, pieces)
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def rename_master(kind: str, old_name: str, new_name: str) -> dict:
	m = _master(kind)
	permissions.require(m.capability)
	_protect(kind, old_name, "renamed")
	return {"name": rename_record(m.doctype, old_name, new_name)}


@frappe.whitelist(methods=["POST"])
def set_master_enabled(kind: str, name: str, enabled: int | bool = 1) -> dict:
	m = _master(kind)
	permissions.require(m.capability)
	if not cint(enabled):
		_protect(kind, name, "switched off")
	doc = frappe.get_doc(m.doctype, name)
	doc.enabled = cint(enabled)
	doc.save(ignore_permissions=True)
	return {"name": name, "enabled": bool(doc.enabled)}


@frappe.whitelist(methods=["POST"])
def delete_master(kind: str, name: str) -> dict:
	m = _master(kind)
	permissions.require(m.capability)
	_protect(kind, name, "deleted")
	if kind == "units":
		# the unit's own standard size goes with it (the delete is refused, and this undone, when
		# the unit is used somewhere)
		frappe.db.delete("UOM Conversion Factor", {"from_uom": name, "to_uom": base_unit()})
	delete_if_unused(m.doctype, name, m.usage)
	return {"deleted": name}


@frappe.whitelist(methods=["POST"])
def set_standard_size(name: str, pieces: float | str | None = None) -> dict:
	"""How many Pieces one of this unit holds, for every product without a size of its own.
	Stored as Frappe's UOM Conversion Factor, which ERPNext reads when it converts a unit: a
	product's own size (on the product page) always wins. Empty clears it."""
	permissions.require("units.manage")
	base = base_unit()
	if name == base:
		frappe.throw(_("{0} is the base unit: one {0} is always one {0}.").format(base))
	unit = frappe.db.get_value("UOM", name, ["must_be_whole_number"], as_dict=True)
	if not unit:
		frappe.throw(_("Unknown unit {0}").format(name))
	row = frappe.db.get_value("UOM Conversion Factor", {"from_uom": name, "to_uom": base}, "name")
	if pieces in (None, ""):
		if row:
			frappe.delete_doc("UOM Conversion Factor", row, ignore_permissions=True)
		return {"name": name, "standard": None}
	value = flt(pieces)
	if value <= 0 or value > 1_000_000:
		frappe.throw(_("Enter a number of {0} above zero.").format(base))
	if unit.must_be_whole_number and value != int(value):
		frappe.throw(_("A {0} holds a whole number of {1}.").format(name, base))
	if row:
		frappe.db.set_value("UOM Conversion Factor", row, "value", value)
	else:
		frappe.get_doc(
			{
				"doctype": "UOM Conversion Factor",
				"category": ensure_unit_category(),
				"from_uom": name,
				"to_uom": base,
				"value": value,
			}
		).insert(ignore_permissions=True)  # capability checked above
	return {"name": name, "standard": value}


# Documents whose numbers people see on paper.
NUMBERED = (
	("Quotation", "Quotation"),
	("Sales Order", "Sales order"),
	("Sales Invoice", "Sales invoice"),
	("Delivery Note", "Delivery note"),
	("Purchase Order", "Purchase order"),
	("Purchase Receipt", "Goods received"),
	("Purchase Invoice", "Supplier bill"),
	("Payment Entry", "Payment"),
	("Stock Entry", "Stock entry"),
	("Stock Count", "Stock count"),
)


@frappe.whitelist(methods=["GET"])
def numbering() -> dict:
	"""Current numbering per document (view only until Phase 2 decides it, D6)."""
	permissions.require("numbering.view")
	from frappe.model.naming import NamingSeries

	documents = []
	for doctype, label in NUMBERED:
		field = frappe.get_meta(doctype).get_field("naming_series")
		if not field:
			continue
		series = []
		for option in (field.options or "").split("\n"):
			option = option.strip()
			if not option:
				continue
			ns = NamingSeries(option)
			try:
				preview = ns.get_preview()
				series.append(
					{
						"series": option,
						"current": ns.get_current_value(),
						"next": preview[0] if preview else None,
					}
				)
			except Exception:
				series.append({"series": option, "current": None, "next": None})
		documents.append({"doctype": doctype, "label": label, "series": series})
	return {"documents": documents}
