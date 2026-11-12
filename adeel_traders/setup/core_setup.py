"""Idempotent core setup for Adeel Traders (Phase 1).

Every function only CREATES records that are missing. Existing records and any later
changes made by an admin are never overwritten, so this is safe to run again.

Entry points (see setup/install.py and patches):
	run_setup(company=None)
"""

import frappe

from adeel_traders import permission_areas as pa
from adeel_traders.setup import data


def run_setup(company: str | None = None) -> None:
	"""Create Phase 1 masters and settings. Company-specific parts run only when a company exists."""
	ensure_uoms()
	ensure_default_unit_sizes_once()
	disable_sample_uoms_once()
	ensure_item_groups()
	ensure_sales_channels()
	ensure_accounting_dimension()
	ensure_branches()
	ensure_people_setup()
	ensure_change_tracking()
	ensure_report_roles()
	ensure_owner_extra_permissions()
	ensure_document_email_permissions()
	run_stock_setup()

	company = company or _single_company()
	if not company or not frappe.db.exists("Company", company):
		# Fresh install before the setup wizard: the wizard hook runs the rest later.
		return

	ensure_price_lists(company)
	ensure_customer_groups()
	ensure_warehouses(company)
	assign_warehouse_branches(company)
	assign_branch_cost_centers(company)
	ensure_default_warehouse(company)
	disable_unused_warehouses(company)
	apply_settings_once()


def _single_company() -> str | None:
	"""The company to set up when none is given.

	Patches run without a logged-in user, so the global default company is often empty there
	(this is why the default warehouse was never fixed on the existing site). Adeel Traders
	has exactly one company, so use it."""
	default = frappe.defaults.get_global_default("company")
	if default:
		return default
	companies = frappe.get_all("Company", pluck="name", limit=2)
	return companies[0] if len(companies) == 1 else None


def run_stock_setup() -> None:
	"""Phase 2 (products and stock). Company-independent; safe to run again."""
	ensure_item_attributes()
	ensure_stock_entry_types()
	ensure_stock_entry_reason_field()
	ensure_stock_counter_role()
	ensure_cost_viewer_role()
	ensure_role_flags()
	ensure_accounts_stock_read()
	apply_stock_settings_once()
	enable_batch_tracking_once()
	apply_stock_rules_once()
	apply_reorder_email_once()


def ensure_item_attributes() -> None:
	for attribute in data.ITEM_ATTRIBUTES:
		if not frappe.db.exists("Item Attribute", attribute):
			frappe.get_doc({"doctype": "Item Attribute", "attribute_name": attribute}).insert()


def ensure_stock_entry_types() -> None:
	for name, purpose in data.STOCK_ENTRY_TYPES.items():
		if not frappe.db.exists("Stock Entry Type", name):
			frappe.get_doc({"doctype": "Stock Entry Type", "name": name, "purpose": purpose}).insert()


def ensure_stock_entry_reason_field() -> None:
	"""Reason field on Stock Entry (SRS 3.3). Server-side rule: adeel_traders.stock.stock_entry."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	types = ", ".join(f"'{t}'" for t in data.ADJUSTMENT_TYPES_NEEDING_REASON)
	condition = f"eval:[{types}].includes(doc.stock_entry_type)"
	create_custom_fields(
		{
			"Stock Entry": [
				{
					"fieldname": "at_adjustment_reason",
					"label": "Adjustment Reason",
					"fieldtype": "Small Text",
					"insert_after": "stock_entry_type",
					"depends_on": condition,
					"mandatory_depends_on": condition,
					"no_copy": 1,
					"allow_on_submit": 0,
				}
			]
		}
	)


def ensure_stock_counter_role() -> None:
	"""Store staff count stock in our own Stock Count document (blind count, server-enforced).

	They get no access to ERPNext's Stock Reconciliation: it shows system quantities, which
	would defeat the blind count. A Stock Manager's submit of the count posts that document."""
	role = data.STOCK_COUNTER_ROLE
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert()
	remove_counter_reconciliation_permission()


def remove_counter_reconciliation_permission() -> None:
	"""Undo the early setup that let Stock Counter edit Stock Reconciliation (see above)."""
	frappe.db.delete(
		"Custom DocPerm",
		{"parent": "Stock Reconciliation", "role": data.STOCK_COUNTER_ROLE},
	)
	frappe.clear_cache(doctype="Stock Reconciliation")


def disable_unused_warehouses(company: str) -> None:
	"""Disable the warehouses ERPNext creates on its own and Adeel Traders does not use.

	A warehouse that holds stock is left alone, so nothing is ever hidden with goods in it."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	for name in data.ERPNEXT_FACTORY_WAREHOUSES:
		warehouse = f"{name} - {abbr}"
		if not frappe.db.exists("Warehouse", {"name": warehouse, "disabled": 0}):
			continue
		if frappe.db.exists("Bin", {"warehouse": warehouse, "actual_qty": ["!=", 0]}):
			continue
		if frappe.db.get_value("Company", company, "default_warehouse") == warehouse:
			continue
		frappe.db.set_value("Warehouse", warehouse, "disabled", 1)


def apply_stock_settings_once() -> None:
	flag = "adeel_traders_stock_settings_applied"
	if frappe.db.get_default(flag):
		return
	_apply_single("Stock Settings", data.STOCK_SETTINGS_PHASE_2)
	frappe.db.set_default(flag, "1")


def apply_stock_rules_once() -> None:
	flag = "adeel_traders_stock_rules_applied"
	if frappe.db.get_default(flag):
		return
	_apply_single("Stock Settings", data.STOCK_RULES_SETTINGS)
	frappe.db.set_default(flag, "1")


def apply_reorder_email_once() -> None:
	flag = "adeel_traders_reorder_email_applied"
	if frappe.db.get_default(flag):
		return
	_apply_single("Stock Settings", data.REORDER_EMAIL_SETTINGS)
	frappe.db.set_default(flag, "1")


def ensure_default_warehouse(company: str) -> None:
	"""v16 keeps the default warehouse on the Company.

	Replaces only an empty value or ERPNext's untouched factory default ("Stores"),
	never a warehouse someone chose on purpose."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	current = frappe.db.get_value("Company", company, "default_warehouse")
	factory = {f"{name} - {abbr}" for name in data.ERPNEXT_FACTORY_WAREHOUSES}
	if current and current not in factory:
		return
	name = f"{data.DEFAULT_WAREHOUSE} - {abbr}"
	if frappe.db.exists("Warehouse", name):
		frappe.db.set_value("Company", company, "default_warehouse", name)


def enable_batch_tracking_once() -> None:
	flag = "adeel_traders_batch_tracking_applied"
	if frappe.db.get_default(flag):
		return
	_apply_single("Stock Settings", data.BATCH_TRACKING_SETTINGS)
	frappe.db.set_default(flag, "1")


def ensure_uoms() -> None:
	for uom in data.UOMS:
		if not frappe.db.exists("UOM", uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": uom, "must_be_whole_number": 1}).insert()


def ensure_item_groups() -> None:
	root = "All Item Groups"
	for group, children in data.ITEM_GROUPS.items():
		_ensure_item_group(group, parent=root, is_group=bool(children))
		for child in children:
			_ensure_item_group(child, parent=group, is_group=False)


def _ensure_item_group(name: str, parent: str, is_group: bool) -> None:
	if frappe.db.exists("Item Group", name):
		return
	frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": name,
			"parent_item_group": parent,
			"is_group": int(is_group),
		}
	).insert()


def ensure_sales_channels() -> None:
	for channel, description in data.SALES_CHANNELS.items():
		if not frappe.db.exists("Sales Channel", channel):
			frappe.get_doc(
				{"doctype": "Sales Channel", "channel_name": channel, "description": description}
			).insert()


def ensure_accounting_dimension() -> None:
	"""Create the Sales Channel accounting dimension.

	No company default is set on purpose: a default would also tag expenses (rent, salaries)
	with a channel and distort profit by channel. Sales documents get their channel from
	dedicated logic in the sales phase (ADR-010).
	"""
	if frappe.db.exists("Accounting Dimension", {"document_type": data.ACCOUNTING_DIMENSION}):
		return
	# ERPNext adds the dimension field to all accounting doctypes in a background job.
	frappe.get_doc(
		{
			"doctype": "Accounting Dimension",
			"label": data.ACCOUNTING_DIMENSION,
			"document_type": data.ACCOUNTING_DIMENSION,
		}
	).insert()


def ensure_price_lists(company: str) -> None:
	currency = frappe.get_cached_value("Company", company, "default_currency")
	for price_list in data.PRICE_LISTS:
		if frappe.db.exists("Price List", price_list):
			continue
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": price_list,
				"currency": currency,
				"selling": 1,
				"buying": 0,
				"enabled": 1,
			}
		).insert()


def ensure_customer_groups() -> None:
	root = "All Customer Groups"
	for group, price_list in data.CUSTOMER_GROUPS.items():
		if frappe.db.exists("Customer Group", group):
			continue
		frappe.get_doc(
			{
				"doctype": "Customer Group",
				"customer_group_name": group,
				"parent_customer_group": root,
				"is_group": 0,
				"default_price_list": price_list,
			}
		).insert()


def ensure_warehouses(company: str) -> None:
	root = frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ("is", "not set")}
	)
	if not root:
		frappe.throw(f"No root warehouse found for company {company}")

	for warehouse_name in data.WAREHOUSES:
		if frappe.db.exists("Warehouse", {"warehouse_name": warehouse_name, "company": company}):
			continue
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": warehouse_name,
				"company": company,
				"parent_warehouse": root,
				"is_group": 0,
			}
		).insert()


def apply_settings_once() -> None:
	"""Apply Stock/Selling settings only the first time, so admin changes are kept later."""
	flag = "adeel_traders_core_settings_applied"
	if frappe.db.get_default(flag):
		return

	_apply_single("Stock Settings", data.STOCK_SETTINGS)
	_apply_single("Selling Settings", data.SELLING_SETTINGS)
	frappe.db.set_default(flag, "1")


def _apply_single(doctype: str, values: dict) -> None:
	settings = frappe.get_single(doctype)
	settings.update(values)
	settings.save()  # runs the standard validations of the settings doctype


def ensure_branches() -> None:
	"""Branches (D4) and the Branch link on Warehouse. Needs no company."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Warehouse": [
				{
					"fieldname": "branch",
					"label": "Branch",
					"fieldtype": "Link",
					"options": "Branch",
					"insert_after": "warehouse_name",
					"description": "Branch-limited staff can only act on stock of their own branch. "
					"Leave empty for shared locations (Damaged, Returns, Testers).",
				}
			]
		}
	)
	create_custom_fields(
		{
			"Branch": [
				{
					"fieldname": "at_cost_center",
					"label": "Cost center",
					"fieldtype": "Link",
					"options": "Cost Center",
					"insert_after": "branch",
					"description": "Sales and expenses of this branch are booked here.",
				}
			]
		}
	)
	for branch in data.BRANCHES:
		if not frappe.db.exists("Branch", branch):
			frappe.get_doc({"doctype": "Branch", "branch": branch}).insert()


def branch_cost_center(company: str, cost_center_name: str) -> str:
	"""The cost center with this name under the company, created when missing (leaf, under the
	company root)."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"{cost_center_name} - {abbr}"
	if not frappe.db.exists("Cost Center", name):
		root = frappe.db.get_value(
			"Cost Center",
			{"company": company, "is_group": 1, "parent_cost_center": ["is", "not set"]},
			"name",
		)
		frappe.get_doc(
			{
				"doctype": "Cost Center",
				"cost_center_name": cost_center_name,
				"company": company,
				"parent_cost_center": root,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
	return name


def assign_branch_cost_centers(company: str) -> None:
	"""Each branch books to its own cost center. A cost center someone chose is never replaced."""
	for branch, cost_center_name in data.BRANCH_COST_CENTER.items():
		if frappe.db.exists("Branch", branch) and not frappe.db.get_value("Branch", branch, "at_cost_center"):
			frappe.db.set_value(
				"Branch", branch, "at_cost_center", branch_cost_center(company, cost_center_name)
			)


def assign_warehouse_branches(company: str) -> None:
	"""Put the two main warehouses in their branch. A branch someone set is never replaced."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	for warehouse, branch in data.WAREHOUSE_BRANCH.items():
		name = f"{warehouse} - {abbr}"
		if frappe.db.exists("Warehouse", name) and not frappe.db.get_value("Warehouse", name, "branch"):
			frappe.db.set_value("Warehouse", name, "branch", branch)


def _switch_fields() -> list[dict]:
	"""One Check field on Role per switch (data.SWITCHES), in a chain after the section break."""
	fields, after = [], "at_section"
	for field_name, _role, title, *_rest in data.SWITCHES.values():
		fields.append({"fieldname": field_name, "label": title, "fieldtype": "Check", "insert_after": after})
		after = field_name
	return fields


def ensure_role_flags() -> None:
	"""Switches on the Role (Roles page, P1.2): see cost, export lists, plus a marker for the roles
	the owner creates and a short description. The two built-in cost/export roles carry the switches."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Role": [
				{
					"fieldname": "at_section",
					"fieldtype": "Section Break",
					"label": "Adeel Traders",
					"insert_after": "two_factor_auth",
				},
				*_switch_fields(),
				{
					"fieldname": "at_managed",
					"label": "Created on the Roles page",
					"fieldtype": "Check",
					"read_only": 1,
					"insert_after": _switch_fields()[-1]["fieldname"],
				},
				{
					"fieldname": "at_description",
					"label": "What this role is for",
					"fieldtype": "Small Text",
					"insert_after": "at_managed",
				},
			]
		}
	)
	for field_name, role, *_rest in data.SWITCHES.values():
		if frappe.db.exists("Role", role):
			frappe.db.set_value("Role", role, field_name, 1, update_modified=False)


def ensure_cost_viewer_role() -> None:
	"""Roles that switch a capability on, instead of a hard-coded list of roles.

	"Can View Cost" shows cost, margin and stock value. "Can Export Lists" allows Excel/PDF export
	(ERPNext's own export flag is on for every staff member, so it cannot tell roles apart).
	"Can See Change History" and "Can Decide Approvals" work the same way (data.SWITCHES)."""
	for _field, role, *_rest in data.SWITCHES.values():
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0}).insert()


def ensure_accounts_stock_read() -> None:
	"""Accountants see stock quantities (SRS 2.1 matrix) but cannot move stock: read-only on Bin."""
	from frappe.permissions import add_permission, update_permission_property

	for role in ("Accounts User", "Accounts Manager"):
		if frappe.db.exists("Custom DocPerm", {"parent": "Bin", "role": role, "permlevel": 0}):
			continue
		if frappe.db.exists("DocPerm", {"parent": "Bin", "role": role, "permlevel": 0}):
			continue
		add_permission("Bin", role, 0)
		update_permission_property("Bin", role, 0, "read", 1, validate=False)


def ensure_people_setup() -> None:
	"""P1.3: "must change password" flag on User, and login security settings (applied once)."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"User": [
				{
					"fieldname": "at_must_change_password",
					"label": "Must change password at next login",
					"fieldtype": "Check",
					"read_only": 1,
					"insert_after": "enabled",
					"description": "Set when the owner gives a temporary password; cleared when the "
					"person chooses their own.",
				}
			]
		}
	)
	flag = "adeel_traders_login_security_applied"
	if frappe.db.get_default(flag):
		return
	_apply_single("System Settings", data.SECURITY_SETTINGS)
	frappe.db.set_default(flag, "1")


def ensure_change_tracking() -> None:
	"""P1.5: keep a Version for every save of the business records (fixes I6). Uses Frappe's own
	track changes; nothing is copied. Who may read the history is the "Can See Change History"
	switch (the API reads the records itself), so nobody but System Manager gets raw access."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	for doctype in data.TRACKED_DOCTYPES:
		if frappe.db.exists("DocType", doctype) and not frappe.get_meta(doctype).track_changes:
			make_property_setter(
				doctype,
				None,
				"track_changes",
				1,
				"Check",
				for_doctype=True,
				validate_fields_for_doctype=False,
			)
	for role in data.RAW_VERSION_ACCESS_TO_REMOVE:  # given by the first version of P1.5
		for row in frappe.get_all(
			"Custom DocPerm", filters={"parent": "Version", "role": role}, pluck="name"
		):
			frappe.delete_doc("Custom DocPerm", row, force=1)
	frappe.clear_cache()  # role permissions are cached per user


def ensure_unit_category() -> str:
	"""The UOM Category our unit sizes (Piece, Dozen, Box, Carton) belong to."""
	if not frappe.db.exists("UOM Category", data.UNIT_CATEGORY):
		frappe.get_doc({"doctype": "UOM Category", "category_name": data.UNIT_CATEGORY}).insert(
			ignore_permissions=True
		)
	return data.UNIT_CATEGORY


def ensure_default_unit_sizes_once() -> None:
	"""SRS 3.2: a Dozen is 12 Pieces for every product, through Frappe's UOM Conversion Factor
	(ERPNext uses it when a product has no size of its own). Once, so the owner's later changes stay."""
	flag = "at_unit_sizes_seeded"
	if frappe.db.get_default(flag):
		return
	base = frappe.db.get_single_value("Stock Settings", "stock_uom") or "Piece"
	for unit, pieces in data.DEFAULT_UNIT_SIZES.items():
		if (
			frappe.db.exists("UOM", unit)
			and frappe.db.exists("UOM", base)
			and not frappe.db.exists("UOM Conversion Factor", {"from_uom": unit, "to_uom": base})
		):
			frappe.get_doc(
				{
					"doctype": "UOM Conversion Factor",
					"category": ensure_unit_category(),
					"from_uom": unit,
					"to_uom": base,
					"value": pieces,
				}
			).insert(ignore_permissions=True)
	frappe.db.set_default(flag, "1")


def disable_sample_uoms_once() -> None:
	"""P1.12: switch off ERPNext's sample units nobody uses (once; the owner may switch any back on
	from Settings > Units). Units in use, ours and a few common ones stay on."""
	if frappe.db.get_default("at_sample_uoms_disabled"):
		return
	keep = set(data.UOMS) | set(data.COMMON_UOMS)
	keep |= set(frappe.get_all("Item", pluck="stock_uom", distinct=True))
	keep |= set(frappe.get_all("UOM Conversion Detail", pluck="uom", distinct=True))
	keep |= set(frappe.get_all("Item Price", filters={"uom": ["is", "set"]}, pluck="uom", distinct=True))
	keep.add(frappe.db.get_single_value("Stock Settings", "stock_uom"))
	for uom in frappe.get_all("UOM", filters={"enabled": 1}, pluck="name"):
		if uom not in keep:
			frappe.db.set_value("UOM", uom, "enabled", 0, update_modified=False)
	frappe.db.set_default("at_sample_uoms_disabled", 1)


def ensure_owner_extra_permissions() -> None:
	"""Give the owner the few permissions ERPNext's standard roles lack (data.OWNER_EXTRA_PERMISSIONS)."""
	from frappe.permissions import add_permission, update_permission_property

	for doctype, role, ptype in data.OWNER_EXTRA_PERMISSIONS:
		if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
			add_permission(doctype, role, 0)
		update_permission_property(doctype, role, 0, ptype, 1, validate=False)
	frappe.clear_cache()


def ensure_report_roles() -> None:
	"""P1.10: give managers the stock reports through Frappe's Custom Role (no change to ERPNext).
	A Custom Role replaces the report's roles, so the report's own roles are copied in as well."""
	from frappe.permissions import add_permission, update_permission_property

	for report, extra in data.REPORT_EXTRA_ROLES.items():
		if not frappe.db.exists("Report", report):
			continue
		for role in extra:
			pa.set_report_role(report, role, True)
		# the report runner also needs "report" on the report's document type
		ref = frappe.db.get_value("Report", report, "ref_doctype")
		for role in extra:
			if not frappe.db.exists("Custom DocPerm", {"parent": ref, "role": role, "permlevel": 0}):
				add_permission(ref, role, 0)
			for ptype in ("read", "report"):
				update_permission_property(ref, role, 0, ptype, 1, validate=False)
	frappe.clear_cache()


def ensure_document_email_permissions() -> None:
	"""P1.8: the roles that may print a document may also email it (data.DOCUMENT_EMAIL_ROLES).
	Only roles that already have a permission row for that document get the "email" tick."""
	from frappe.permissions import update_permission_property

	for doctype, roles in data.DOCUMENT_EMAIL_ROLES.items():
		for role in roles:
			if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				update_permission_property(doctype, role, 0, "email", 1, validate=False)
	frappe.clear_cache()
