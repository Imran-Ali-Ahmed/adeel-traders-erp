"""Session helpers for the Next.js staff app.

Login/logout use Frappe's standard endpoints (/api/method/login, /api/method/logout).
"""

import frappe
from frappe.sessions import get_csrf_token

from adeel_traders import permissions
from adeel_traders.setup import data


@frappe.whitelist(methods=["GET"])
def me() -> dict:
	"""Current user, plus the CSRF token the browser must send with every write (X-Frappe-CSRF-Token)."""
	user = frappe.session.user
	caps = permissions.capabilities(user)
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"csrf_token": get_csrf_token(),
		"company": frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_global_default("company"),
		"can_view_cost": caps["cost.view"],
		"capabilities": caps,
		"branches": sorted(permissions.user_branches(user)),
		"role_label": _role_label(user),
		# Set after the owner gives a temporary password: the app asks for a new one first.
		"must_change_password": bool(frappe.db.get_value("User", user, "at_must_change_password")),
		# P1.7: an outgoing email account exists (until then Frappe refuses to send, D16 pending)
		"email_ready": bool(frappe.db.exists("Email Account", {"enable_outgoing": 1, "default_outgoing": 1})),
	}


def _role_label(user: str) -> str:
	"""What the person is, in words: their built-in role(s) and any role the owner made for them,
	e.g. "Owner" or "Counter staff, Senior cashier". Empty when they have none of ours."""
	if user == "Administrator":
		return "Administrator"
	profiles = frappe.get_all("User Role Profile", filters={"parent": user}, pluck="role_profile")
	labels = [data.BUILT_IN_ROLES[p] for p in profiles if p in data.BUILT_IN_ROLES]
	own_roles = frappe.get_all("Role", filters={"at_managed": 1}, pluck="name")
	if own_roles:
		labels += sorted(
			frappe.get_all(
				"Has Role",
				filters={"parent": user, "parenttype": "User", "role": ["in", own_roles]},
				pluck="role",
			)
		)
	return ", ".join(dict.fromkeys(labels))
