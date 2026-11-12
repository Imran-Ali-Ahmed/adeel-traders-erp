"""Who may do what (Phase 1, P1.1). See docs/PERMISSIONS.md for the agreed matrix.

Two ideas live here:

1. Capabilities: named abilities ("prices.edit", "stock.count.post") the staff app uses to show
   or hide menus and buttons. Each one is answered by a REAL Frappe permission check, never by a
   role name, so a custom role the owner creates later works without any code change.
   The screens only hide what the server would refuse anyway: every API still checks.

2. Branch rule: staff who are limited to a branch (a User Permission on "Branch") may act only on
   stock of that branch. Reading quantities of other branches stays open (matrix decision).
"""

from collections.abc import Callable

import frappe
from frappe import _

from adeel_traders.setup import data


def _perm(doctype: str, ptype: str, user: str) -> bool:
	return bool(frappe.has_permission(doctype, ptype, user=user))


def _has_role(role: str | None, user: str) -> bool:
	return bool(role) and role in frappe.get_roles(user)


def _role_flag(field_name: str, user: str) -> bool:
	"""True when any of the user's roles has the switch on (set on the Roles page, P1.2)."""
	return bool(frappe.db.exists("Role", {"name": ["in", frappe.get_roles(user)], field_name: 1}))


def _report_allowed(report: str, user: str) -> bool:
	"""Frappe's own rule for opening a report (see permission_areas._report_allowed)."""
	from adeel_traders import permission_areas

	return permission_areas._report_allowed(report, frappe.get_roles(user))


def _approver(user: str) -> bool:
	"""The "Can Decide Approvals" switch, or any role an active approval workflow names as a
	decider (e.g. expenses): the person then has something waiting for them."""
	roles = set(frappe.get_roles(user))
	if _role_flag("at_can_approve", user):
		return True
	active = frappe.get_all("Workflow", filters={"is_active": 1}, pluck="name")
	allowed = set(
		frappe.get_all("Workflow Transition", filters={"parent": ["in", active or [""]]}, pluck="allowed")
	)
	return bool(roles & allowed)


def _backdate_role() -> str | None:
	return frappe.db.get_single_value("Stock Settings", "role_allowed_to_create_edit_back_dated_transactions")


# capability name -> check(user). To add a capability, add one line here.
CAPABILITIES: dict[str, Callable[[str], bool]] = {
	"products.view": lambda u: _perm("Item", "read", u),
	"products.create": lambda u: _perm("Item", "create", u),
	"products.edit": lambda u: _perm("Item", "write", u),
	"catalogue.manage": lambda u: _perm("Item Group", "write", u),
	"prices.edit": lambda u: _perm("Item Price", "write", u),
	"cost.view": lambda u: _role_flag("at_can_view_cost", u),
	"stock.view": lambda u: _perm("Bin", "read", u),
	"stock.move": lambda u: _perm("Stock Entry", "create", u),
	"stock.adjust": lambda u: _perm("Stock Entry", "create", u),
	"stock.backdate": lambda u: _has_role(_backdate_role(), u),
	"stock.count": lambda u: _perm("Stock Count", "create", u),
	"stock.count.post": lambda u: _perm("Stock Count", "submit", u),
	"barcode.generate_print": lambda u: _perm("Item", "read", u)
	and (_perm("Item", "write", u) or _perm("Stock Entry", "create", u)),
	"export.lists": lambda u: _role_flag("at_can_export", u),
	"reports.stock": lambda u: _report_allowed("Stock Balance", u),
	# the change history (P1.5): the "Can See Change History" switch on a role (Roles page)
	"audit.view": lambda u: _role_flag("at_can_view_history", u),
	# the Approvals inbox (P1.6); the server checks every single decision through the workflow
	"approvals.inbox": _approver,
	# Settings > Email (P1.7): email templates, plus the account and queue status (owner)
	"email.manage": lambda u: _perm("Email Template", "write", u) and _perm("Email Queue", "read", u),
	# Settings > Integrations (P1.11): the log of outside calls (PostEx, gateway, attendance)
	"integrations.view": lambda u: _perm("Integration Request", "read", u),
	# Settings lists (P1.12): units, payment methods; numbering is view-only until Phase 2 (D6)
	"units.manage": lambda u: _perm("UOM", "create", u),
	"payments.manage": lambda u: _perm("Mode of Payment", "create", u),
	"numbering.view": lambda u: _perm("Document Naming Settings", "read", u),
	# the Company page also creates cost centers for new branches, so both are needed
	# (HR may edit the Company record in ERPNext, but not accounts)
	"company.manage": lambda u: _perm("Company", "write", u) and _perm("Cost Center", "create", u),
	"users.manage": lambda u: _perm("User", "create", u),
	"roles.manage": lambda u: _perm("Role", "create", u),
}


def capabilities(user: str | None = None) -> dict[str, bool]:
	user = user or frappe.session.user
	return {name: bool(check(user)) for name, check in CAPABILITIES.items()}


def can(capability: str, user: str | None = None) -> bool:
	"""Server-side check for one capability (use in APIs where a role name would be wrong)."""
	return bool(CAPABILITIES[capability](user or frappe.session.user))


def require(capability: str) -> None:
	if not can(capability):
		frappe.throw(_("You do not have permission to do this."), frappe.PermissionError)


# ---------------------------------------------------------------------------
# Branch rule
# ---------------------------------------------------------------------------


def user_branches(user: str | None = None) -> set[str]:
	"""Branches the user is limited to. Empty set = not limited (owner, manager, accountant...)."""
	user = user or frappe.session.user
	return set(
		frappe.get_all("User Permission", filters={"user": user, "allow": "Branch"}, pluck="for_value")
	)


def own_warehouses(user: str | None = None) -> list[str] | None:
	"""Warehouses the user may act on, or None when not limited. Warehouses that belong to no
	branch (Damaged, Returns - To Check, Testers) are open to everybody."""
	branches = user_branches(user)
	if not branches:
		return None
	return frappe.get_all(
		"Warehouse",
		or_filters=[["branch", "in", list(branches)], ["branch", "is", "not set"]],
		pluck="name",
	)


def assert_can_act_on_warehouse(warehouse: str | None, user: str | None = None) -> None:
	branches = user_branches(user)
	if not branches or not warehouse:
		return
	branch = frappe.db.get_value("Warehouse", warehouse, "branch")
	if branch and branch not in branches:
		frappe.throw(
			_("You can only work on stock of your own branch. {0} belongs to {1}.").format(
				frappe.bold(warehouse), frappe.bold(branch)
			),
			frappe.PermissionError,
		)


def validate_stock_entry_branch(doc, method=None):
	"""Stock Entry hook: the stock leaves (or arrives in) a warehouse of your own branch.
	A transfer may still go TO another branch."""
	sources = {r.s_warehouse for r in doc.items if r.s_warehouse} | (
		{doc.from_warehouse} if doc.get("from_warehouse") else set()
	)
	if not sources:
		sources = {r.t_warehouse for r in doc.items if r.t_warehouse}
	for warehouse in sources:
		assert_can_act_on_warehouse(warehouse)


def stock_count_conditions(user: str | None = None) -> str:
	"""Stock Counts of other branches are not listed for branch-limited staff."""
	warehouses = own_warehouses(user)
	if warehouses is None:
		return ""
	if not warehouses:
		return "1=0"
	return "`tabStock Count`.warehouse in ({})".format(", ".join(frappe.db.escape(w) for w in warehouses))


def stock_count_has_permission(doc, ptype=None, user=None):
	warehouses = own_warehouses(user)
	if warehouses is not None and doc.warehouse not in warehouses:
		return False
	return True


# ---------------------------------------------------------------------------
# Desk (decision D1 = B): only admins and the accountant use Frappe Desk
# ---------------------------------------------------------------------------

DESK_PATHS = ("/desk", "/app")


def can_use_desk(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & data.DESK_ROLES)


def keep_staff_out_of_desk(context=None):
	"""update_website_context hook: staff who open Desk are sent to the staff app instead.

	It runs while Frappe builds a web page (that is where Frappe turns frappe.Redirect into a real
	redirect; a before_request hook cannot). Only Desk pages are affected; the API the staff app
	uses (/api/...) never builds a web page, so it is untouched."""
	request = getattr(frappe.local, "request", None)
	path = getattr(request, "path", "") or ""
	if not any(path == p or path.startswith(p + "/") for p in DESK_PATHS):
		return
	user = frappe.session.user if getattr(frappe.local, "session", None) else "Guest"
	if user == "Guest" or can_use_desk(user):
		return  # guests get Frappe's own login page
	frappe.flags.redirect_location = frappe.conf.get("staff_app_url") or "/"
	raise frappe.Redirect(302)
