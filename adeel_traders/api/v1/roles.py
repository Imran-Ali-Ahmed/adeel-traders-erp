"""Roles & permissions page (Phase 1, P1.2). Only users with the "roles.manage" capability.

A role the owner creates is a normal Frappe Role (marked at_managed) plus Custom DocPerm rows, so the
server enforces it on every API call, not only on the screens. The owner works with AREAS
(adeel_traders/permission_areas.py); this module turns ticks into permissions and back.

The 7 built-in roles are locked: they are built from ERPNext's standard roles, which other parts of
the system rely on. The owner copies one into a new role to change it.

Contract used by web/apps/staff/lib/api.ts:
	list_roles()                              -> {roles: [...], registry: [...]}
	get_role(name)                            -> one role with ticks, switches and a plain summary
	create_role(name, description, copy_from) -> the new role
	save_role(name, areas, switches)          -> the saved role
	(areas = ticks per area; switches = yes/no per data.SWITCHES key)

Note: never name an API argument "flags": Frappe's HTTP layer drops it (direct Python calls and unit
tests would still pass), so the browser would get "missing argument".
	delete_role(name)                         -> {deleted: name}
"""

import json

import frappe
from frappe import _
from frappe.permissions import setup_custom_perms

from adeel_traders import permission_areas as pa
from adeel_traders import permissions
from adeel_traders.setup import data

NAME_MIN, NAME_MAX = 3, 40


def _json(value):
	return json.loads(value) if isinstance(value, str) else value


def _flags_of(roles: list[str]) -> dict[str, bool]:
	"""The switches (data.SWITCHES) any of these roles has on."""
	fields = [v[0] for v in data.SWITCHES.values()]
	rows = frappe.get_all("Role", filters={"name": ["in", roles]}, fields=fields)
	return {key: any(r.get(v[0]) for r in rows) for key, v in data.SWITCHES.items()}


def _roles_of(name: str) -> list[str]:
	"""The Frappe roles behind a role on this page (a built-in profile bundles several)."""
	if name in data.BUILT_IN_ROLES:
		return [r.role for r in frappe.get_doc("Role Profile", name).roles]
	return [name]


def _managed_role(name: str):
	"""The Role document of a role the owner created; refuses built-in and standard roles."""
	if name in data.BUILT_IN_ROLES:
		frappe.throw(_("Built-in roles cannot be changed. Copy it into a new role instead."))
	if not frappe.db.exists("Role", name) or not frappe.db.get_value("Role", name, "at_managed"):
		frappe.throw(_("Role {0} was not created on this page and cannot be changed here.").format(name))
	return frappe.get_doc("Role", name)


def _people(name: str) -> list[dict]:
	"""The users who have this role: [{user, name}]."""
	users = frappe.get_all("User Role Profile", filters={"role_profile": name}, pluck="parent")
	if name not in data.BUILT_IN_ROLES:
		users += frappe.get_all("Has Role", filters={"role": name, "parenttype": "User"}, pluck="parent")
	users = sorted(set(users))
	return [{"user": u, "name": frappe.utils.get_fullname(u)} for u in users]


def _detail(name: str) -> dict:
	roles = _roles_of(name)
	grants = pa.effective_grants(roles)
	flags = _flags_of(roles)
	built_in = name in data.BUILT_IN_ROLES
	people = _people(name)
	return {
		"name": name,
		"label": data.BUILT_IN_ROLES.get(name, name),
		"built_in": built_in,
		"description": data.BUILT_IN_ROLE_NOTES[name]
		if built_in
		else (frappe.db.get_value("Role", name, "at_description") or ""),
		"users": len(people),
		"people": people,
		"areas": grants,
		"levels": {area: pa.level_of(area, grants.get(area, [])) for area in pa.AREAS},
		"flags": flags,
		"summary": pa.describe(grants, flags),
	}


@frappe.whitelist(methods=["GET"])
def list_roles() -> dict:
	permissions.require("roles.manage")
	custom = frappe.get_all("Role", filters={"at_managed": 1}, pluck="name", order_by="name asc")
	return {
		"roles": [_detail(n) for n in [*data.BUILT_IN_ROLES, *custom]],
		"registry": pa.registry(),
		"switches": pa.switches(),
	}


@frappe.whitelist(methods=["GET"])
def get_role(name: str) -> dict:
	permissions.require("roles.manage")
	if name not in data.BUILT_IN_ROLES:
		_managed_role(name)
	return {**_detail(name), "registry": pa.registry(), "switches": pa.switches()}


def _check_new_name(name: str) -> str:
	name = (name or "").strip()
	if not NAME_MIN <= len(name) <= NAME_MAX:
		frappe.throw(_("The role name must have {0} to {1} characters.").format(NAME_MIN, NAME_MAX))
	if name.upper().startswith("AT ") or name.lower() in {v.lower() for v in data.BUILT_IN_ROLES.values()}:
		frappe.throw(_("{0} is the name of a built-in role. Choose another name.").format(name))
	if (
		frappe.db.exists("Role", {"name": name})
		or frappe.db.sql("select name from tabRole where lower(name)=%s", name.lower())
		# ERPNext ships its own role profiles too ("Sales", "Inventory"...); a role made here gets
		# a profile of the same name, so that name must be free as well.
		or frappe.db.sql("select name from `tabRole Profile` where lower(name)=%s", name.lower())
	):
		frappe.throw(_("A role called {0} already exists. Choose another name.").format(name))
	return name


@frappe.whitelist(methods=["POST"])
def create_role(name: str, description: str = "", copy_from: str | None = None) -> dict:
	permissions.require("roles.manage")
	name = _check_new_name(name)
	grants, flags = {}, dict.fromkeys(data.SWITCHES, False)
	if copy_from:
		if copy_from not in data.BUILT_IN_ROLES:
			_managed_role(copy_from)
		source = _detail(copy_from)
		grants, flags = source["areas"], source["flags"]
	role = frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": name,
			"is_custom": 1,
			"desk_access": 1,  # keeps staff users "System Users" (Desk is closed to staff in P1.3)
			"at_managed": 1,
			"at_description": (description or "").strip(),
		}
	).insert()
	# People get roles through Role Profiles. Frappe removes any role that is not in one of the
	# person's profiles, so a role made here also gets a profile of the same name.
	frappe.get_doc({"doctype": "Role Profile", "role_profile": name, "roles": [{"role": name}]}).insert()
	_apply(role, pa.check_grants(grants), flags)
	role.add_comment("Info", _("Created{0}.").format(f" as a copy of {copy_from}" if copy_from else ""))
	return _detail(name)


@frappe.whitelist(methods=["POST"])
def save_role(name: str, areas: dict | str, switches: dict | str, description: str | None = None) -> dict:
	permissions.require("roles.manage")
	role = _managed_role(name)
	grants = pa.check_grants(_json(areas))
	switches = _json(switches) or {}
	before = _detail(name)
	if description is not None:
		role.at_description = description.strip()
	_apply(role, grants, {key: bool(switches.get(key)) for key in data.SWITCHES})
	after = _detail(name)
	if before["summary"] != after["summary"]:
		role.add_comment(
			"Info",
			_("Permissions changed.<br>Before: {0}<br>After: {1}").format(
				"; ".join(before["summary"]), "; ".join(after["summary"])
			),
		)
	return after


@frappe.whitelist(methods=["POST"])
def delete_role(name: str) -> dict:
	permissions.require("roles.manage")
	role = _managed_role(name)
	users = len(_people(name))
	if users:
		frappe.throw(_("{0} people have this role. Take it away from them first.").format(users))
	frappe.db.delete("Custom DocPerm", {"role": name})
	for area in pa.AREAS.values():
		for report in area.report_roles:
			pa.set_report_role(report, name, False)
	if frappe.db.exists("Role Profile", name):
		frappe.delete_doc("Role Profile", name, force=1)
	frappe.delete_doc("Role", role.name, force=1)
	frappe.clear_cache()
	return {"deleted": name}


# ---------------------------------------------------------------------------
# turning ticks into permissions
# ---------------------------------------------------------------------------


def _apply(role, grants: dict[str, list[str]], flags: dict[str, bool]) -> None:
	"""Write the role's permissions so they match the ticks exactly. One transaction (Frappe rolls
	everything back if anything fails). Rows of other roles are never touched."""
	wanted = pa.wanted_permissions(grants)
	for doctype in sorted(pa.MANAGED_DOCTYPES):
		_set_role_permissions(doctype, role.name, wanted.get(doctype, set()))
	for key, (field_name, *_rest) in data.SWITCHES.items():
		role.set(field_name, int(bool(flags.get(key))))
	for area_key, area in pa.AREAS.items():
		for report in area.report_roles:  # Frappe lets a role run a report only from its list
			pa.set_report_role(report, role.name, "view" in grants.get(area_key, []))
	role.save()
	frappe.clear_cache()


def _set_role_permissions(doctype: str, role: str, ptypes: set[str]) -> None:
	filters = {"parent": doctype, "role": role, "permlevel": 0}
	row = frappe.db.get_value("Custom DocPerm", filters, "name")
	if not ptypes:
		if row:
			frappe.delete_doc("Custom DocPerm", row, force=1)
		return
	setup_custom_perms(doctype)  # first time: copy ERPNext's standard permissions, nothing is lost
	doc = (
		frappe.get_doc("Custom DocPerm", row)
		if row
		else frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": doctype,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
			}
		)
	)
	for ptype in pa.MANAGED_PTYPES:
		doc.set(ptype, int(ptype in ptypes))
	doc.save() if row else doc.insert()
