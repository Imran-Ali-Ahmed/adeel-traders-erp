"""Company page (Phase 1, P1.4): business details, logo and branches.

Everything is stored in ERPNext's own records: Company (NTN, phone, email, website, logo), Address
(company and branch addresses), Branch, Cost Center and Warehouse. This module only adds the
owner's rules: a new branch is set up completely in one step, and a branch is never deleted
(sales and stock history point to it), only renamed.

Callers need "company.manage" (owner and accountant) to change anything; reading needs the same.

Contract used by web/apps/staff/lib/api.ts:
	get_company()                                   -> {details, address, logo, branches, shared_locations}
	update_company(company_name, tax_id, phone_no, email, website, address_line1, city)
	set_company_logo(file_url)                      -> {logo}
	add_branch(name, address_line1, city, phone)    -> branch
	update_branch(branch, new_name, address_line1, city, phone) -> branch
"""

import frappe
from frappe import _
from frappe.utils import validate_email_address

from adeel_traders import permissions
from adeel_traders.setup.core_setup import branch_cost_center

BRANCH_NAME_MAX = 40
COMPANY_NAME_MAX = 80
LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "svg"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


def _company() -> str:
	return (
		frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name", limit=1)[0]
	)


def _address_of(doctype: str, name: str) -> dict:
	"""The first address linked to a record, as plain values (empty values when none)."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": doctype, "link_name": name, "parenttype": "Address"},
		pluck="parent",
	)
	row = (
		frappe.db.get_value("Address", links[0], ["name", "address_line1", "city", "phone"], as_dict=True)
		if links
		else None
	)
	return {
		"address_line1": (row or {}).get("address_line1") or "",
		"city": (row or {}).get("city") or "",
		"phone": (row or {}).get("phone") or "",
		"_name": (row or {}).get("name"),
	}


def _save_address(
	doctype: str, name: str, title: str, line1: str, city: str, phone: str = "", company_address: bool = False
):
	"""Create or update the one address of a company or branch (Frappe's Address)."""
	line1, city, phone = (line1 or "").strip(), (city or "").strip(), (phone or "").strip()
	existing = _address_of(doctype, name)["_name"]
	if not (line1 or city):
		# The company keeps its phone on the Company record; a branch phone lives on its address.
		if phone and not company_address:
			frappe.throw(_("Enter the street address and city to save the branch phone."))
		if existing:
			_detach_address(existing, doctype, name)
		return
	if not (line1 and city):
		frappe.throw(_("Enter both the street address and the city."))
	if existing:
		doc = frappe.get_doc("Address", existing)
		doc.update({"address_line1": line1, "city": city, "phone": phone})
		doc.save(ignore_permissions=True)  # a saved doc keeps its change history (P1.5)
		return
	frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": title,
			"address_type": "Office" if company_address else "Shop",
			"address_line1": line1,
			"city": city,
			"country": frappe.db.get_value("Company", _company(), "country") or "Pakistan",
			"phone": phone,
			"is_your_company_address": int(company_address),
			"links": [{"link_doctype": doctype, "link_name": name}],
		}
	).insert(ignore_permissions=True)  # permission checked by the caller (company.manage)


def _detach_address(address: str, doctype: str, name: str) -> None:
	"""Address emptied on the page: unlink it from the company or branch. The Address record is
	kept, because invoices printed earlier may point to it."""
	doc = frappe.get_doc("Address", address)
	doc.links = [l for l in doc.links if not (l.link_doctype == doctype and l.link_name == name)]
	if doctype == "Company":
		doc.is_your_company_address = 0
	doc.disabled = int(not doc.links)
	doc.save(ignore_permissions=True)  # permission checked by the caller (company.manage)


def _branch(name: str) -> dict:
	warehouses = frappe.get_all(
		"Warehouse", filters={"branch": name, "disabled": 0, "is_group": 0}, pluck="name", order_by="name asc"
	)
	people = len(
		set(frappe.get_all("User Permission", filters={"allow": "Branch", "for_value": name}, pluck="user"))
	)
	address = _address_of("Branch", name)
	address.pop("_name")
	return {
		"name": name,
		"warehouses": warehouses,
		"cost_center": frappe.db.get_value("Branch", name, "at_cost_center"),
		# people limited to this branch (people working at all branches are not counted)
		"people": people,
		"address": address,
	}


@frappe.whitelist(methods=["GET"])
def get_company() -> dict:
	permissions.require("company.manage")
	company = _company()
	doc = frappe.get_doc("Company", company)
	address = _address_of("Company", company)
	address.pop("_name")
	return {
		"details": {
			"name": doc.company_name,
			"abbr": doc.abbr,
			"tax_id": doc.tax_id or "",
			"phone_no": doc.phone_no or "",
			"email": doc.email or "",
			"website": doc.website or "",
			"currency": doc.default_currency,
			"country": doc.country,
		},
		"address": address,
		"logo": doc.company_logo,
		"branches": [_branch(b) for b in frappe.get_all("Branch", pluck="name", order_by="name asc")],
		# locations that belong to no branch: anyone may use them
		"shared_locations": frappe.get_all(
			"Warehouse",
			filters={"company": company, "is_group": 0, "disabled": 0, "branch": ["is", "not set"]},
			pluck="name",
			order_by="name asc",
		),
	}


def _rename_company(old: str, new: str) -> str:
	"""ERPNext's own rename: every record that points to the company follows. The short code
	(abbreviation, e.g. "AT" in "Cash - AT") stays the same."""
	new = (new or "").strip()
	if not new or new == old:
		return old
	if not 2 <= len(new) <= COMPANY_NAME_MAX:
		frappe.throw(_("A company name needs 2 to {0} characters.").format(COMPANY_NAME_MAX))
	if frappe.db.sql("select name from tabCompany where lower(name)=%s and name!=%s", (new.lower(), old)):
		frappe.throw(_("A company called {0} already exists.").format(new))
	frappe.rename_doc("Company", old, new, force=True)
	frappe.clear_cache()
	return new


@frappe.whitelist(methods=["POST"])
def update_company(
	company_name: str = "",
	tax_id: str = "",
	phone_no: str = "",
	email: str = "",
	website: str = "",
	address_line1: str = "",
	city: str = "",
) -> dict:
	"""Business details shown on invoices and receipts. A new company_name renames the company
	(every record follows). The phone is kept on the Company, so it needs no address."""
	permissions.require("company.manage")
	email = (email or "").strip()
	if email:
		validate_email_address(email, throw=True)
	company = _rename_company(_company(), company_name)
	doc = frappe.get_doc("Company", company)
	doc.tax_id = (tax_id or "").strip() or None
	doc.phone_no = (phone_no or "").strip() or None
	doc.email = email or None
	doc.website = (website or "").strip() or None
	doc.save(ignore_permissions=True)  # permission checked above
	_save_address("Company", company, company, address_line1, city, phone_no, company_address=True)
	return get_company()


@frappe.whitelist(methods=["POST"])
def set_company_logo(file_url: str | None = None) -> dict:
	"""Use an uploaded file as the logo (or clear it). The file is uploaded with Frappe's standard
	upload_file, attached to the Company."""
	permissions.require("company.manage")
	company = _company()
	if file_url:
		file = frappe.db.get_value(
			"File",
			{"file_url": file_url, "attached_to_doctype": "Company", "attached_to_name": company},
			["file_name", "file_size"],
			as_dict=True,
		)
		if not file:
			frappe.throw(_("Upload the logo first"))
		if (file.file_name or "").rsplit(".", 1)[-1].lower() not in LOGO_EXTENSIONS:
			frappe.throw(_("The logo must be a PNG, JPG, WebP or SVG image."))
		if (file.file_size or 0) > LOGO_MAX_BYTES:
			frappe.throw(_("The logo must be 2 MB or smaller."))
	doc = frappe.get_doc("Company", company)
	doc.company_logo = file_url or None
	doc.save(ignore_permissions=True)  # permission checked above; saved so the change is in the history
	# P1.7: Frappe puts this logo at the top of every email (when no email account sets its own)
	frappe.db.set_single_value("Website Settings", "app_logo", file_url or None)
	return {"logo": file_url or None}


def _check_branch_name(name: str, current: str | None = None) -> str:
	name = (name or "").strip()
	if not 2 <= len(name) <= BRANCH_NAME_MAX:
		frappe.throw(_("A branch name needs 2 to {0} characters.").format(BRANCH_NAME_MAX))
	clash = frappe.db.sql("select name from tabBranch where lower(name)=%s", name.lower())
	if clash and clash[0][0] != current:
		frappe.throw(_("A branch called {0} already exists.").format(name))
	return name


@frappe.whitelist(methods=["POST"])
def add_branch(name: str, address_line1: str = "", city: str = "", phone: str = "") -> dict:
	"""A new branch, set up completely: the Branch, its own stock location (warehouse) and its own
	cost center, so stock and accounts work for it at once."""
	permissions.require("company.manage")
	name = _check_branch_name(name)
	company = _company()
	abbr = frappe.get_cached_value("Company", company, "abbr")
	if frappe.db.exists("Warehouse", f"{name} - {abbr}"):
		frappe.throw(_("A stock location called {0} already exists.").format(name))

	branch = frappe.get_doc({"doctype": "Branch", "branch": name}).insert(ignore_permissions=True)
	branch.db_set("at_cost_center", branch_cost_center(company, name))
	parent = frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["is", "not set"]}, "name"
	)
	frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": name,
			"company": company,
			"parent_warehouse": parent,
			"branch": name,
		}
	).insert(ignore_permissions=True)
	_save_address("Branch", name, name, address_line1, city, phone)
	return _branch(name)


@frappe.whitelist(methods=["POST"])
def update_branch(
	branch: str,
	new_name: str | None = None,
	address_line1: str = "",
	city: str = "",
	phone: str = "",
) -> dict:
	"""Rename a branch (every link to it follows) or change its address. Branches are never
	deleted: sales and stock history point to them."""
	permissions.require("company.manage")
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Unknown branch {0}").format(branch))
	name = branch
	if new_name and new_name.strip() != branch:
		name = _check_branch_name(new_name, current=branch)
		frappe.rename_doc("Branch", branch, name, force=True)
	_save_address("Branch", name, name, address_line1, city, phone)
	return _branch(name)
