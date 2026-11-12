"""People page (Phase 1, P1.3): who can log in, their roles, their branch, and login security.

Reuses Frappe for everything it already does: the User record, role profiles, User Permission on
Branch (the branch rule of P1.1), password strength rules, saving passwords, and the Activity Log
for login history. This module only adds the owner's rules on top.

Only users with "users.manage" (the owner) may call these, except change_my_password (everyone).

Contract used by web/apps/staff/lib/api.ts:
	list_people()                                         -> {people, roles, branches, counts}
	get_person(user)                                      -> person + access summary (see _access)
	add_person(full_name, email, roles, branches, confirm_owner) -> {user, temp_password}
	update_person(user, roles, branches, enabled, confirm_owner) -> person
	(branches: a list of branch names; an empty list means all branches)
	reset_password(user)                                  -> {temp_password}
	change_my_password(old_password, new_password)        -> {ok}
	log_out_everywhere(user)                              -> {signed_out: n}
	unlock_login(user)                                    -> {ok}
"""

import json
import secrets

import frappe
from frappe import _
from frappe.core.doctype.user.user import STANDARD_USERS, test_password_strength
from frappe.utils import validate_email_address
from frappe.utils.password import check_password, update_password

from adeel_traders import permissions
from adeel_traders.setup import data

ACTIVITY_DAYS = 7


def _json(value):
	return json.loads(value) if isinstance(value, str) else value


def _role_options() -> list[dict]:
	"""Roles a person can be given: the 7 built-in ones and the ones the owner made."""
	own = frappe.get_all("Role", filters={"at_managed": 1}, pluck="name", order_by="name asc")
	return [{"key": k, "label": v, "built_in": True} for k, v in data.BUILT_IN_ROLES.items()] + [
		{"key": name, "label": name, "built_in": False}
		for name in own
		if frappe.db.exists("Role Profile", name)
	]


def _role_label(key: str) -> str:
	return data.BUILT_IN_ROLES.get(key, key)


def _branches_of(user: str) -> list[str]:
	return sorted(
		frappe.get_all("User Permission", filters={"user": user, "allow": "Branch"}, pluck="for_value")
	)


def _person(user: str) -> dict:
	doc = frappe.get_doc("User", user)
	keys = [r.role_profile for r in doc.role_profiles]
	return {
		"user": doc.name,
		"name": doc.full_name or doc.name,
		"email": doc.email,
		"enabled": bool(doc.enabled),
		"roles": [{"key": k, "label": _role_label(k), "built_in": k in data.BUILT_IN_ROLES} for k in keys],
		# empty = all branches
		"branches": _branches_of(doc.name),
		"last_login": doc.last_login,
		"last_active": doc.last_active,
		"must_change_password": bool(doc.get("at_must_change_password")),
		"is_you": doc.name == frappe.session.user,
	}


def _branch_names() -> list[str]:
	"""All branches, read from the database (new ones are added on the Company page)."""
	return frappe.get_all("Branch", pluck="name", order_by="name asc")


def _staff_users() -> list[str]:
	return frappe.get_all(
		"User",
		filters={"user_type": "System User", "name": ["not in", STANDARD_USERS]},
		pluck="name",
		order_by="full_name asc",
	)


def _require_person(user: str):
	if user in STANDARD_USERS or not frappe.db.exists("User", user):
		frappe.throw(_("{0} is not a staff member who can be managed here.").format(user))


def _check_roles(roles, confirm_owner) -> list[str]:
	roles = [r for r in dict.fromkeys(_json(roles) or []) if r]
	if not roles:
		frappe.throw(_("Choose at least one role."))
	valid = {o["key"] for o in _role_options()}
	for key in roles:
		if key not in valid:
			frappe.throw(_("Unknown role {0}").format(key))
	if data.OWNER_PROFILE in roles and not frappe.utils.cint(confirm_owner):
		frappe.throw(
			_("The Owner role can do everything, including changing prices and users. Confirm to give it."),
			title=_("Confirm Owner role"),
		)
	return roles


def _check_branches(branches) -> list[str]:
	"""A list of branches the person works at; an empty list means all branches."""
	branches = [b for b in dict.fromkeys(_json(branches) or []) if b]
	for branch in branches:
		if not frappe.db.exists("Branch", branch):
			frappe.throw(_("Unknown branch {0}").format(branch))
	return branches


def _note(user: str, text: str) -> None:
	"""A line in the person's change history (P1.5), for changes kept outside the User record."""
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "User",
			"reference_name": user,
			"content": text,
		}
	).insert(ignore_permissions=True)


def _set_branches(user: str, branches: list[str], note: bool = True) -> None:
	"""Uses Frappe's User Permission on Branch, as the branch rule (permissions.py) does."""
	current = set(_branches_of(user))
	if note and current != set(branches):
		words = lambda b: ", ".join(sorted(b)) or "All branches"  # noqa: E731
		_note(user, f"Works at: {words(current)} → {words(branches)}")
	for name in frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Branch", "for_value": ["not in", branches or [""]]},
		pluck="name",
	):
		frappe.delete_doc("User Permission", name, ignore_permissions=True)
	for branch in branches:
		if branch not in current:
			frappe.get_doc(
				{"doctype": "User Permission", "user": user, "allow": "Branch", "for_value": branch}
			).insert(ignore_permissions=True)


def _active_owners(excluding: str | None = None) -> int:
	owners = frappe.get_all("User Role Profile", filters={"role_profile": data.OWNER_PROFILE}, pluck="parent")
	return sum(1 for u in set(owners) if u != excluding and frappe.db.get_value("User", u, "enabled"))


def _temp_password() -> str:
	# 14 random characters: strong enough for the password policy, easy to read out once.
	return secrets.token_urlsafe(10)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def list_people() -> dict:
	permissions.require("users.manage")
	people = [_person(u) for u in _staff_users()]
	return {
		"people": people,
		"roles": _role_options(),
		"branches": _branch_names(),
		"counts": {
			"active": sum(1 for p in people if p["enabled"]),
			"disabled": sum(1 for p in people if not p["enabled"]),
			"must_change_password": sum(1 for p in people if p["must_change_password"]),
		},
	}


def _sessions(user: str) -> list[dict]:
	"""Where the person is signed in right now (Frappe's session table)."""
	rows = frappe.db.sql(
		"""select ipaddress, lastupdate from `tabSessions`
		where user=%s and (status is null or status='Active') order by lastupdate desc""",
		user,
		as_dict=True,
	)
	return [{"ip": r.ipaddress, "last_active": r.lastupdate} for r in rows]


def _tracker(user: str):
	from frappe.auth import LoginAttemptTracker

	settings = frappe.get_cached_doc("System Settings")
	return LoginAttemptTracker(
		user,
		max_consecutive_login_attempts=settings.allow_consecutive_login_attempts or 3,
		lock_interval=settings.allow_login_after_fail or 300,
	)


def _access(user: str, doc) -> dict:
	"""What the owner needs to act on, instead of a raw list of logins:
	is the person signed in now (and where), are they using the system, is the password still the
	temporary one, and is anyone guessing their password (or are they locked out)."""
	today = frappe.utils.getdate()
	since = frappe.utils.add_days(today, -(ACTIVITY_DAYS - 1))
	logins = frappe.db.sql(
		"""select date(creation) day, status, count(*) n from `tabActivity Log`
		where user=%s and operation='Login' and creation >= %s group by day, status""",
		(user, since),
		as_dict=True,
	)
	ok_by_day = {str(r.day): r.n for r in logins if r.status != "Failed"}
	failed_today = sum(r.n for r in logins if r.status == "Failed" and r.day == today)
	failed_week = sum(r.n for r in logins if r.status == "Failed")
	tracker = _tracker(user)
	locked = not tracker.is_user_allowed()
	return {
		"last_active": doc.last_active,
		"last_login": doc.last_login,
		"last_ip": doc.last_ip,
		"password_changed_on": doc.last_password_reset_date,
		"temporary_password": bool(doc.get("at_must_change_password")),
		"sessions": _sessions(user),
		"failed_today": failed_today,
		"failed_7_days": failed_week,
		"locked": locked,
		"locked_until": (tracker.login_failed_time + tracker.lock_interval) if locked else None,
		# oldest first, one entry per day, for a small bar chart
		"days": [
			{"date": str(d), "logins": ok_by_day.get(str(d), 0)}
			for d in (frappe.utils.add_days(since, i) for i in range(ACTIVITY_DAYS))
		],
	}


@frappe.whitelist(methods=["GET"])
def get_person(user: str) -> dict:
	permissions.require("users.manage")
	_require_person(user)
	doc = frappe.get_doc("User", user)
	return {
		**_person(user),
		"access": _access(user, doc),
		"roles_available": _role_options(),
		"branches_available": _branch_names(),
	}


# ---------------------------------------------------------------------------
# changing (owner only)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def add_person(
	full_name: str,
	email: str,
	roles: list | str,
	branches: list | str | None = None,
	confirm_owner: int = 0,
) -> dict:
	"""Create a login. The owner gets a temporary password to give the person once; the app makes
	them choose their own at the first login (no email account is needed)."""
	permissions.require("users.manage")
	full_name = (full_name or "").strip()
	if len(full_name) < 2:
		frappe.throw(_("Enter the person's name."))
	email = (email or "").strip().lower()
	validate_email_address(email, throw=True)
	if frappe.db.exists("User", email):
		frappe.throw(_("{0} already has a login.").format(email))
	roles = _check_roles(roles, confirm_owner)
	branches = _check_branches(branches)

	first_name, _sep, last_name = full_name.partition(" ")
	temp = _temp_password()
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"send_welcome_email": 0,
			"role_profiles": [{"role_profile": r} for r in roles],
			"at_must_change_password": 1,
		}
	)
	user.insert(ignore_permissions=True)  # permission checked above (users.manage)
	update_password(user.name, temp)
	_set_branches(user.name, branches, note=False)  # new person: the User record shows it
	return {"user": user.name, "temp_password": temp}


@frappe.whitelist(methods=["POST"])
def update_person(
	user: str,
	roles: list | str | None = None,
	branches: list | str | None = None,
	enabled: int | None = None,
	confirm_owner: int = 0,
) -> dict:
	"""Change a person's roles, branches (empty list = all branches) or enabled. Leave an argument
	out to keep it as it is."""
	permissions.require("users.manage")
	_require_person(user)
	doc = frappe.get_doc("User", user)
	is_owner_now = data.OWNER_PROFILE in {r.role_profile for r in doc.role_profiles}

	if roles is not None:
		roles = _check_roles(roles, confirm_owner or is_owner_now)
		if is_owner_now and data.OWNER_PROFILE not in roles:
			if user == frappe.session.user:
				frappe.throw(_("You cannot take the Owner role away from yourself. Ask another owner."))
			if _active_owners(excluding=user) == 0:
				frappe.throw(_("This is the last owner. Give the Owner role to someone else first."))
		doc.role_profiles = []
		for r in roles:
			doc.append("role_profiles", {"role_profile": r})

	if enabled is not None:
		enabled = frappe.utils.cint(enabled)
		if not enabled and user == frappe.session.user:
			frappe.throw(_("You cannot disable your own login."))
		if not enabled and is_owner_now and _active_owners(excluding=user) == 0:
			frappe.throw(_("This is the last owner. Give the Owner role to someone else first."))
		doc.enabled = enabled

	branches = None if branches is None else _check_branches(branches)
	doc.save(ignore_permissions=True)  # permission checked above (users.manage)
	if branches is not None:
		_set_branches(user, branches)
	return _person(user)


@frappe.whitelist(methods=["POST"])
def reset_password(user: str) -> dict:
	"""Give a forgotten password a new temporary one; the person must change it at the next login."""
	permissions.require("users.manage")
	_require_person(user)
	if user == frappe.session.user:
		frappe.throw(_("Change your own password from your profile menu."))
	temp = _temp_password()
	update_password(user, temp, logout_all_sessions=True)
	frappe.db.set_value("User", user, "at_must_change_password", 1)
	_note(user, "Password reset: a temporary password was given")
	return {"temp_password": temp}


@frappe.whitelist(methods=["POST"])
def change_my_password(old_password: str, new_password: str) -> dict:
	"""Anyone logged in: choose a new password (required after a temporary one)."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Log in first."), frappe.PermissionError)
	check_password(user, old_password)  # raises if the current password is wrong
	if old_password == new_password:
		frappe.throw(_("Choose a password different from the current one."))
	feedback = (test_password_strength(new_password) or {}).get("feedback") or {}
	if not feedback.get("password_policy_validation_passed", False):
		frappe.throw(
			_("This password is too easy to guess. Use a longer one, with words and numbers."),
			title=_("Password too weak"),
		)
	update_password(user, new_password)
	frappe.db.set_value("User", user, "at_must_change_password", 0)
	frappe.db.set_value("User", user, "last_password_reset_date", frappe.utils.today())
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def log_out_everywhere(user: str) -> dict:
	"""Sign the person out on every device (lost phone, person leaving). They can log in again."""
	permissions.require("users.manage")
	_require_person(user)
	if user == frappe.session.user:
		frappe.throw(_("Use Log out in your own menu."))
	from frappe.sessions import delete_session

	sids = frappe.db.sql_list("select sid from `tabSessions` where user=%s", user)
	for sid in sids:
		delete_session(sid, reason="Signed out by the owner")
	_note(user, f"Logged out on every device ({len(sids)} open)")
	return {"signed_out": len(sids)}


@frappe.whitelist(methods=["POST"])
def unlock_login(user: str) -> dict:
	"""Let a person who typed the wrong password too often try again now, without waiting.

	Frappe locks the person AND the network address the wrong passwords came from. In a shop all
	staff share one address, so one person's mistakes lock everyone; clear those addresses too."""
	permissions.require("users.manage")
	_require_person(user)
	_tracker(user).add_success_attempt()  # Frappe's own reset of the failure count
	since = frappe.utils.add_days(frappe.utils.now_datetime(), -1)
	for ip in frappe.get_all(
		"Activity Log",
		filters={"user": user, "operation": "Login", "status": "Failed", "creation": [">=", since]},
		pluck="ip_address",
		distinct=True,
	):
		if ip:
			_tracker(ip).add_success_attempt()
	_note(user, "Login unlocked")
	return {"ok": True}
