"""Categories (Item Group) and brands (Brand) for the staff app's Settings, Catalogue page.

Thin wrappers over the standard DocTypes: Frappe's own permissions and validations apply, and
renames go through frappe.rename_doc so every product keeps its link.
"""

import frappe
from frappe import _

from adeel_traders.api.v1.masters import delete_if_unused, rename_record
from adeel_traders.setup import data

ROOT_GROUP = "All Item Groups"


@frappe.whitelist(methods=["GET"])
def get_catalogue() -> dict:
	frappe.has_permission("Item Group", "read", throw=True)
	groups = frappe.get_list(
		"Item Group",
		fields=["name", "parent_item_group", "is_group", "lft"],
		order_by="lft asc",
		limit_page_length=0,
	)
	counts = {
		row.item_group: row.count
		for row in frappe.get_all(
			"Item", fields=["item_group", {"COUNT": "*", "as": "count"}], group_by="item_group"
		)
	}
	brand_counts = {
		row.brand: row.count
		for row in frappe.get_all(
			"Item",
			filters={"brand": ("is", "set")},
			fields=["brand", {"COUNT": "*", "as": "count"}],
			group_by="brand",
		)
	}
	return {
		"categories": [
			{
				"name": g.name,
				"parent": g.parent_item_group,
				"is_group": g.is_group,
				"products": counts.get(g.name, 0),
			}
			for g in groups
			if g.name != ROOT_GROUP
			and not (g.name in data.ERPNEXT_FACTORY_ITEM_GROUPS and not counts.get(g.name))
		],
		"brands": [
			{"name": b, "products": brand_counts.get(b, 0)}
			for b in frappe.get_list("Brand", pluck="name", order_by="name asc", limit_page_length=0)
		],
		"can_edit": bool(
			frappe.has_permission("Item Group", "create") and frappe.has_permission("Brand", "create")
		),
	}


@frappe.whitelist(methods=["POST"])
def add_category(name: str, parent: str = ROOT_GROUP) -> dict:
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Enter a category name"))
	if not frappe.db.get_value("Item Group", parent, "is_group"):
		frappe.throw(_("{0} cannot hold other categories").format(parent))
	doc = frappe.get_doc(
		{"doctype": "Item Group", "item_group_name": name, "parent_item_group": parent, "is_group": 0}
	).insert()
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def add_brand(name: str) -> dict:
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Enter a brand name"))
	doc = frappe.get_doc({"doctype": "Brand", "brand": name}).insert()
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def rename(doctype: str, old_name: str, new_name: str) -> dict:
	"""Rename a category or brand; products keep their link (standard rename)."""
	if doctype not in ("Item Group", "Brand"):
		frappe.throw(_("Only categories and brands can be renamed here"))
	if old_name == ROOT_GROUP:
		frappe.throw(_("The top category cannot be renamed"))
	return {"name": rename_record(doctype, old_name, new_name)}  # shared with Settings lists


@frappe.whitelist(methods=["POST"])
def delete_brand(name: str) -> dict:
	"""Only a brand no product uses can be deleted."""
	frappe.get_doc("Brand", name).check_permission("delete")
	delete_if_unused("Brand", name, (("Item", "brand"),), "product(s)")  # shared with Settings lists
	return {"deleted": name}
