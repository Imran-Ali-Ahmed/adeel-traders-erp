"""Areas: how the owner thinks about permissions (Phase 1, P1.2).

Owners think in areas ("Prices"), Frappe thinks in doctypes ("Item Price"). This list translates
one into the other. The Roles page, docs/PERMISSIONS.md and the server checks all read it, so every
later phase adds its own areas HERE and nothing is built twice.

An action is a list of (doctype, permissions) pairs. A role "has" an action when it holds all of them.
Delete is offered only where a record may really be deleted (submitted records are never deleted,
SRS 6), so most areas have no delete action.
"""

from dataclasses import dataclass, field

import frappe

from adeel_traders.setup import data

VIEW = ("read", "print", "report")
READ = ("read",)
# Every permission type this screen may switch on or off. Others (share, import, export...) are
# never touched.
MANAGED_PTYPES = ("read", "print", "report", "write", "create", "delete", "submit", "cancel")

ACTION_LABELS = {
	"view": "See",
	"create": "Add",
	"edit": "Change",
	"delete": "Delete",
	"post": "Post counts",
}

# Settings areas written as one action ("Change") offer no separate "See": reading the list
# without being able to change it would open a page that does nothing for that person.


@dataclass(frozen=True)
class Area:
	label: str
	help: str
	# One word for table headings ("Prices").
	short: str
	# (doctype, permissions) or (doctype, permissions, required). An optional pair is written when
	# the action is ticked, but a role is not shown as lacking the action without it (ERPNext's own
	# roles do not always hold print/report or extra reads).
	actions: dict[str, list[tuple]]
	# (area, action) pairs a role needs as well, so the role is not half-working.
	requires: list[tuple[str, str]] = field(default_factory=list)
	# What the owner picks, from least to most: (key, label, sentence, actions). The first level is
	# always "none". Levels are shortcuts for sets of actions; the server still stores actions, so
	# any combination a built-in role has can be shown (as "custom" if it fits no level).
	levels: list[tuple[str, str, str, list[str]]] = field(default_factory=list)
	# Frappe reports this area opens. Frappe lets a role run a report only when the role is on the
	# report's list (Custom Role), so ticking "See" also puts the role on that list.
	report_roles: tuple[str, ...] = ()


AREAS: dict[str, Area] = {
	"products": Area(
		label="Products and gift sets",
		short="Products",
		help="Find products (with their selling prices), and add or change products, shades, units and gift sets.",
		actions={
			"view": [
				("Item", VIEW),
				("Brand", READ),
				("Item Group", READ),
				("Product Bundle", VIEW, False),
				("UOM", READ, False),
			],
			"create": [("Item", ("create",)), ("Product Bundle", ("create",))],
			"edit": [("Item", ("write",)), ("Product Bundle", ("write",))],
		},
		levels=[
			("none", "No access", "Cannot see products or gift sets at all.", []),
			("see", "See", "See products and their prices. Cannot add or change anything.", ["view"]),
			(
				"add",
				"Add",
				"See products and add new ones. Cannot change existing products.",
				["view", "create"],
			),
			(
				"change",
				"Change",
				"See, add and change products, shades, units and gift sets.",
				["view", "create", "edit"],
			),
		],
	),
	"prices": Area(
		label="Selling prices",
		short="Prices",
		help="Change the three selling prices. Everyone who can see products can already see the prices.",
		actions={
			"edit": [("Item Price", ("write", "create")), ("Price List", READ, False)],
		},
		requires=[("products", "view")],
		levels=[
			(
				"none",
				"No access",
				"Cannot change prices. Prices are still visible wherever products are shown.",
				[],
			),
			("change", "Change", "Change the three selling prices of any product.", ["edit"]),
		],
	),
	"catalogue": Area(
		label="Categories and brands",
		short="Catalogue",
		help="Manage the categories and brands that products belong to.",
		actions={
			"view": [("Item Group", READ), ("Brand", READ)],
			"create": [("Item Group", ("create",)), ("Brand", ("create",))],
			"edit": [("Item Group", ("write",)), ("Brand", ("write",))],
			"delete": [("Brand", ("delete",))],
		},
		levels=[
			("none", "No access", "Cannot see categories or brands.", []),
			("see", "See", "See the categories and brands. Cannot change them.", ["view"]),
			("manage", "Manage", "Add and rename categories and brands.", ["view", "create", "edit"]),
			(
				"full",
				"Full",
				"Add, rename, and delete brands that no product uses.",
				["view", "create", "edit", "delete"],
			),
		],
	),
	"stock": Area(
		label="Stock and movements",
		short="Stock",
		help="See stock in every location, and move, damage or write off stock.",
		actions={
			"view": [
				("Bin", READ),
				("Warehouse", READ),
				("Stock Ledger Entry", ("read", "report"), False),
				("Batch", READ, False),
			],
			"create": [("Stock Entry", ("create", "write", "submit"))],
		},
		requires=[("products", "view")],
		levels=[
			("none", "No access", "Cannot see how much stock there is.", []),
			("see", "See", "See how much stock is in every location. Cannot move it.", ["view"]),
			("move", "Move", "See stock, and move, damage or write off stock.", ["view", "create"]),
		],
	),
	"stock_counts": Area(
		label="Stock counts",
		short="Counts",
		help="Count stock by scanning. Posting a count shows the system numbers and changes the stock.",
		actions={
			"view": [("Stock Count", VIEW)],
			"create": [("Stock Count", ("create", "write"))],
			"post": [("Stock Count", ("submit", "cancel"))],
		},
		requires=[("products", "view"), ("stock", "view")],
		levels=[
			("none", "No access", "Cannot see or start stock counts.", []),
			("see", "See", "See stock counts. Cannot start or change one.", ["view"]),
			("count", "Count", "Count stock by scanning. Cannot post the count.", ["view", "create"]),
			(
				"post",
				"Post",
				"Count, and post counts. Posting shows the system numbers and changes the stock.",
				["view", "create", "post"],
			),
		],
	),
	"reports": Area(
		label="Reports",
		short="Reports",
		help="Open the reports (stock balance) and filter them by dates, branch, category and brand.",
		actions={"view": [("Stock Ledger Entry", ("read", "report"))]},
		requires=[("products", "view"), ("stock", "view")],
		report_roles=("Stock Balance",),
		levels=[
			("none", "No access", "Cannot open reports.", []),
			("see", "See", "Open the reports. Cost columns still need the cost switch.", ["view"]),
		],
	),
	"units": Area(
		label="Units",
		short="Units",
		help="Add, rename and switch off the units products are counted in (piece, box, carton).",
		actions={"edit": [("UOM", ("read", "create", "write"))]},
		levels=[
			("none", "No access", "Cannot open the Units page.", []),
			("manage", "Manage", "Add, rename, switch off and delete units that nothing uses.", ["edit"]),
		],
	),
	"payment_methods": Area(
		label="Payment methods",
		short="Payments",
		help="Add, rename and switch off the ways of paying: cash, bank, JazzCash, Easypaisa.",
		actions={"edit": [("Mode of Payment", ("read", "create", "write"))]},
		levels=[
			("none", "No access", "Cannot open the Payment methods page.", []),
			(
				"manage",
				"Manage",
				"Add, rename, switch off and delete payment methods that nothing uses.",
				["edit"],
			),
		],
	),
	"email": Area(
		label="Email settings",
		short="Email",
		help="See the email queue and edit the wording of emails. Sending itself needs the business email account.",
		actions={"edit": [("Email Template", ("read", "create", "write")), ("Email Queue", READ)]},
		levels=[
			("none", "No access", "Cannot open the Email page.", []),
			("manage", "Manage", "See the queue and edit email templates.", ["edit"]),
		],
	),
	"integrations": Area(
		label="Integrations",
		short="Integrations",
		help="See the calls to PostEx, the payment gateway and the attendance machine, and try failed ones again.",
		actions={
			"view": [("Integration Request", READ)],
			"edit": [("Integration Request", ("write",))],
		},
		levels=[
			("none", "No access", "Cannot open the Integrations page.", []),
			("see", "See", "See the services and their calls. Cannot try a failed call again.", ["view"]),
			("manage", "Manage", "See the calls and try failed ones again.", ["view", "edit"]),
		],
	),
	"numbering": Area(
		label="Numbering",
		short="Numbering",
		help="See how each document is numbered and the next number. Changing numbers is decided in Phase 2.",
		actions={"view": [("Document Naming Settings", READ)]},
		levels=[
			("none", "No access", "Cannot open the Numbering page.", []),
			("see", "See", "See the numbering of documents.", ["view"]),
		],
	),
}

# Every doctype the screen may change, with the permission types it may touch.
MANAGED_DOCTYPES: set[str] = {
	pair[0] for area in AREAS.values() for pairs in area.actions.values() for pair in pairs
}


def wanted_permissions(grants: dict[str, list[str]]) -> dict[str, set[str]]:
	"""{area: [actions]} -> {doctype: {permission types}}."""
	wanted: dict[str, set[str]] = {}
	for area_key, actions in grants.items():
		for action in actions:
			for pair in AREAS[area_key].actions[action]:
				wanted.setdefault(pair[0], set()).update(pair[1])
	return wanted


def check_grants(grants: dict) -> dict[str, list[str]]:
	"""Reject unknown areas/actions and half-working combinations. Returns a clean copy."""
	clean: dict[str, list[str]] = {}
	for area_key, actions in (grants or {}).items():
		area = AREAS.get(area_key)
		if not area:
			frappe.throw(f"Unknown area {area_key}")
		actions = [a for a in dict.fromkeys(actions or [])]
		for action in actions:
			if action not in area.actions:
				frappe.throw(f"{area.label} has no action {action}")
		if actions:
			clean[area_key] = actions
	for area_key, actions in clean.items():
		area = AREAS[area_key]
		if "view" in area.actions and actions != ["view"] and "view" not in actions:
			frappe.throw(
				frappe._("{0}: to {1} a role must also be able to see it").format(
					area.label, ", ".join(ACTION_LABELS[a].lower() for a in actions)
				)
			)
		for need_area, need_action in area.requires:
			if need_action not in clean.get(need_area, []):
				frappe.throw(
					frappe._("{0} needs {1} ({2}) as well").format(
						area.label, AREAS[need_area].label, ACTION_LABELS[need_action].lower()
					)
				)
	return clean


def _role_permissions(doctype: str, roles: list[str]) -> set[str]:
	"""Permission types the given roles hold on a doctype (level 0). Uses Custom DocPerm when the
	doctype has been customised, otherwise ERPNext's standard permissions."""
	if not roles:
		return set()
	table = "Custom DocPerm" if frappe.db.exists("Custom DocPerm", {"parent": doctype}) else "DocPerm"
	rows = frappe.db.sql(
		f"select * from `tab{table}` where parent=%s and permlevel=0 and role in %s",
		(doctype, tuple(roles)),
		as_dict=True,
	)
	return {p for row in rows for p in MANAGED_PTYPES if row.get(p)}


def _report_allowed(report: str, roles: list[str]) -> bool:
	"""Frappe's rule (Report.is_permitted): the Custom Role list when there is one, else the
	report's own roles; a report with no roles at all is open to everybody."""
	custom = _custom_role(report)
	allowed = (
		set(frappe.get_all("Has Role", filters={"parenttype": "Custom Role", "parent": custom}, pluck="role"))
		if custom
		else set()
	)
	allowed = allowed or set(
		frappe.get_all("Has Role", filters={"parenttype": "Report", "parent": report}, pluck="role")
	)
	return not allowed or bool(allowed & set(roles))


def _custom_role(report: str) -> str | None:
	return frappe.db.get_value("Custom Role", {"report": report}, "name")


def set_report_role(report: str, role: str, on: bool) -> None:
	"""Put a role on (or take it off) a Frappe report's list of roles (Custom Role). A Custom
	Role replaces the report's own list, so the report's own roles are copied in the first time."""
	name = _custom_role(report)
	doc = frappe.get_doc("Custom Role", name) if name else frappe.new_doc("Custom Role")
	if not name:
		if not on:
			return
		doc.report = report
		for standard in frappe.get_all(
			"Has Role", filters={"parenttype": "Report", "parent": report}, pluck="role"
		):
			doc.append("roles", {"role": standard})
	have = {r.role for r in doc.roles}
	if on and role not in have:
		doc.append("roles", {"role": role})
	elif not on and role in have:
		doc.roles = [r for r in doc.roles if r.role != role]
	else:
		return
	doc.save(ignore_permissions=True)  # the Roles page checked roles.manage


def effective_grants(roles: list[str]) -> dict[str, list[str]]:
	"""What the given roles can do, expressed in areas and actions."""
	held = {doctype: _role_permissions(doctype, roles) for doctype in MANAGED_DOCTYPES}
	result: dict[str, list[str]] = {}
	for area_key, area in AREAS.items():
		actions = [
			action
			for action, pairs in area.actions.items()
			if all(
				{p for p in pair[1] if p not in ("print", "report")} <= held[pair[0]]
				for pair in pairs
				if len(pair) == 2 or pair[2]
			)
		]
		if area.report_roles and not all(_report_allowed(r, roles) for r in area.report_roles):
			actions = [a for a in actions if a != "view"]
		if actions:
			result[area_key] = actions
	return result


def level_of(area_key: str, actions: list[str]) -> str:
	"""The level that matches the actions exactly, "none" for no actions, else "custom"."""
	if not actions:
		return "none"
	for key, _label, _sentence, level_actions in AREAS[area_key].levels:
		if set(level_actions) == set(actions):
			return key
	return "custom"


def describe(grants: dict[str, list[str]], flags: dict[str, bool]) -> list[str]:
	"""Plain-English lines for the Roles page (words come from the server, one source)."""
	lines = []
	for area_key, area in AREAS.items():
		actions = grants.get(area_key)
		if not actions:
			continue
		words = ", ".join(ACTION_LABELS[a].lower() for a in actions)
		lines.append(f"{area.label}: {words}")
	for key, (*_head, sentence) in data.SWITCHES.items():
		if flags.get(key):
			lines.append(sentence)
	return lines or ["No access to anything yet"]


def registry() -> list[dict]:
	"""What the Roles page draws its grid from."""
	return [
		{
			"area": key,
			"label": area.label,
			"short": area.short,
			"help": area.help,
			"actions": [{"key": a, "label": ACTION_LABELS[a]} for a in area.actions],
			"requires": [{"area": a, "action": act} for a, act in area.requires],
			"levels": [
				{"key": k, "label": label, "sentence": sentence, "actions": actions, "rank": rank}
				for rank, (k, label, sentence, actions) in enumerate(area.levels)
			],
		}
		for key, area in AREAS.items()
	]


def switches() -> list[dict]:
	"""The yes/no switches of the Roles page (cost, export, change history, approvals)."""
	return [
		{"key": key, "title": title, "help": help_text, "private": private}
		for key, (_field, _role, title, help_text, private, _sentence) in data.SWITCHES.items()
	]
