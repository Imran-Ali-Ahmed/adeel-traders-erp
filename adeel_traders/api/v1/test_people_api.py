"""P1.3: the People API. The owner adds people, gives roles and a branch, disables and resets;
the server enforces every safety rule (not only the screen)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import check_password

from adeel_traders import permissions
from adeel_traders.api.v1 import people, roles, session
from adeel_traders.setup import data

OWNER = "_test_at_people_owner@example.com"
CLERK = "_test_at_people_clerk@example.com"
NEW = "_test_at_people_new@example.com"
CUSTOM_ROLE = "_Test People Cashier"


def _user(email: str, profile: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Test",
				"send_welcome_email": 0,
				"role_profiles": [{"role_profile": profile}],
			}
		).insert()
	return email


def _drop_user(email: str) -> None:
	if frappe.db.exists("User", email):
		frappe.db.delete("User Permission", {"user": email})
		frappe.db.delete("Activity Log", {"user": email})
		frappe.delete_doc("User", email, force=1, ignore_permissions=True)


class TestPeopleAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.owner = _user(OWNER, data.OWNER_PROFILE)
		cls.clerk = _user(CLERK, "AT Counter Staff")

	@classmethod
	def tearDownClass(cls):
		# Saving a password commits, so nothing here may rely on a rollback: remove what we made.
		frappe.set_user("Administrator")
		for email in (OWNER, CLERK, NEW):
			_drop_user(email)
		if frappe.db.exists("Role", CUSTOM_ROLE):
			roles.delete_role(CUSTOM_ROLE)
		frappe.db.delete(
			"Deleted Document", {"data": ["like", '%"\\_test%']}
		)  # test leftovers stay out of the change history
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		_drop_user(NEW)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _add(self, **kwargs):
		frappe.set_user(self.owner)
		args = {
			"full_name": "Nida Shah",
			"email": NEW,
			"roles": ["AT Counter Staff"],
			"branches": ["Shop Counter"],
		}
		args.update(kwargs)
		return people.add_person(**args)

	# --- adding -----------------------------------------------------------------------------

	def test_owner_adds_a_person_with_role_branch_and_temp_password(self):
		out = self._add()
		self.assertEqual(out["user"], NEW)
		check_password(NEW, out["temp_password"])  # the temporary password works
		person = people.get_person(NEW)
		self.assertEqual([r["label"] for r in person["roles"]], ["Counter staff"])
		self.assertEqual(person["branches"], ["Shop Counter"])
		self.assertTrue(person["must_change_password"])
		self.assertIn("Sales User", frappe.get_roles(NEW))  # roles came from the profile
		self.assertEqual(permissions.user_branches(NEW), {"Shop Counter"})

	def test_bad_input_is_refused(self):
		frappe.set_user(self.owner)
		for kwargs in (
			{"email": "not-an-email"},
			{"email": self.clerk},  # already has a login
			{"roles": []},
			{"roles": ["No Such Role"]},
			{"branches": ["Nowhere"]},
			{"full_name": ""},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(kwargs)):
				self._add(**kwargs)

	def test_owner_role_needs_confirmation(self):
		with self.assertRaises(frappe.ValidationError):
			self._add(roles=[data.OWNER_PROFILE])
		out = self._add(roles=[data.OWNER_PROFILE], confirm_owner=1)
		self.assertIn("System Manager", frappe.get_roles(out["user"]))

	def test_custom_role_stays_after_saving(self):
		"""Frappe drops roles that are not in a person's profiles; a role made on the Roles page has
		its own profile, so it survives every save."""
		frappe.set_user(self.owner)
		if frappe.db.exists("Role", CUSTOM_ROLE):
			roles.delete_role(CUSTOM_ROLE)
		roles.create_role(CUSTOM_ROLE)
		self.addCleanup(lambda: (frappe.set_user("Administrator"), frappe.delete_doc("User", NEW, force=1)))
		self._add(roles=["AT Counter Staff", CUSTOM_ROLE])
		frappe.get_doc("User", NEW).save(ignore_permissions=True)  # a later save keeps it
		self.assertIn(CUSTOM_ROLE, frappe.get_roles(NEW))
		frappe.set_user(NEW)
		self.assertEqual(session.me()["role_label"], f"Counter staff, {CUSTOM_ROLE}")

	# --- changing ---------------------------------------------------------------------------

	def test_change_role_branch_and_disable(self):
		self._add()
		person = people.update_person(NEW, roles=["AT Store"], branches=["Main Warehouse", "Shop Counter"])
		self.assertEqual(person["branches"], ["Main Warehouse", "Shop Counter"])
		self.assertEqual(permissions.user_branches(NEW), {"Main Warehouse", "Shop Counter"})
		person = people.update_person(NEW, branches=[])
		self.assertEqual([r["key"] for r in person["roles"]], ["AT Store"])
		self.assertEqual(person["branches"], [])  # all branches
		self.assertIn("Stock User", frappe.get_roles(NEW))
		self.assertNotIn("Sales User", frappe.get_roles(NEW))
		person = people.update_person(NEW, enabled=0)
		self.assertFalse(person["enabled"])

	def test_cannot_disable_yourself_or_drop_your_own_owner_role(self):
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			people.update_person(self.owner, enabled=0)
		with self.assertRaises(frappe.ValidationError):
			people.update_person(self.owner, roles=["AT Manager"])

	def test_leaving_branches_out_keeps_them(self):
		self._add()
		people.update_person(NEW, enabled=1)
		self.assertEqual(people.get_person(NEW)["branches"], ["Shop Counter"])

	def test_last_owner_is_protected(self):
		self._add(roles=[data.OWNER_PROFILE], confirm_owner=1)
		frappe.set_user("Administrator")
		with patch.object(people, "_active_owners", return_value=0):  # pretend nobody else is owner
			with self.assertRaises(frappe.ValidationError):
				people.update_person(NEW, roles=["AT Manager"])
			with self.assertRaises(frappe.ValidationError):
				people.update_person(NEW, enabled=0)
		people.update_person(NEW, enabled=0)  # with other owners around, allowed

	def test_reset_and_change_password(self):
		out = self._add()
		frappe.set_user(self.owner)
		temp = people.reset_password(NEW)["temp_password"]
		self.assertNotEqual(temp, out["temp_password"])
		check_password(NEW, temp)
		frappe.set_user(NEW)
		self.assertTrue(session.me()["must_change_password"])
		with self.assertRaises(frappe.AuthenticationError):
			people.change_my_password("wrong", "Bright-Lipstick-2026")
		with self.assertRaises(frappe.ValidationError):
			people.change_my_password(temp, "123")  # too weak
		self.assertEqual(people.change_my_password(temp, "Bright-Lipstick-2026"), {"ok": True})
		check_password(NEW, "Bright-Lipstick-2026")
		self.assertFalse(session.me()["must_change_password"])

	# --- who may ----------------------------------------------------------------------------

	def test_only_the_owner_may_manage_people(self):
		frappe.set_user(self.clerk)
		for call in (
			people.list_people,
			lambda: people.get_person(self.owner),
			lambda: people.add_person("X Y", NEW, ["AT Counter Staff"]),
			lambda: people.update_person(self.owner, enabled=0),
			lambda: people.reset_password(self.owner),
			lambda: people.unlock_login(self.owner),
			lambda: people.log_out_everywhere(self.owner),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_standard_users_are_not_managed_here(self):
		frappe.set_user(self.owner)
		names = {p["user"] for p in people.list_people()["people"]}
		self.assertNotIn("Administrator", names)
		self.assertNotIn("Guest", names)
		with self.assertRaises(frappe.ValidationError):
			people.update_person("Administrator", enabled=0)

	def test_list_and_history(self):
		self._add()
		frappe.set_user(self.owner)
		listed = people.list_people()
		self.assertIn(NEW, {p["user"] for p in listed["people"]})
		self.assertIn("Counter staff", [r["label"] for r in listed["roles"]])
		self.assertEqual(sorted(listed["branches"]), sorted(frappe.get_all("Branch", pluck="name")))
		before = people.get_person(NEW)["access"]["failed_today"]
		frappe.get_doc(
			{
				"doctype": "Activity Log",
				"user": NEW,
				"operation": "Login",
				"status": "Failed",
				"subject": "Invalid login credentials",
			}
		).insert(ignore_permissions=True)
		access = people.get_person(NEW)["access"]
		self.assertEqual(access["failed_today"], before + 1)
		self.assertEqual(len(access["days"]), 7)
		self.assertTrue(access["temporary_password"])
		self.assertFalse(access["locked"])

	def test_unlock_and_log_out_everywhere(self):
		self._add()
		tracker = people._tracker(NEW)
		shop_ip = people._tracker("10.9.8.7")  # the shop's shared network address
		log = frappe.get_doc(
			{
				"doctype": "Activity Log",
				"user": NEW,
				"operation": "Login",
				"status": "Failed",
				"subject": "Invalid login credentials",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Activity Log", log.name, "ip_address", "10.9.8.7")
		for _i in range(tracker.max_failed_logins + 1):  # Frappe locks after MORE than the limit
			tracker.add_failure_attempt()
			shop_ip.add_failure_attempt()
		self.assertTrue(people.get_person(NEW)["access"]["locked"])
		self.assertFalse(shop_ip.is_user_allowed())
		self.assertEqual(people.unlock_login(NEW), {"ok": True})
		self.assertFalse(people.get_person(NEW)["access"]["locked"])
		self.assertTrue(people._tracker("10.9.8.7").is_user_allowed())  # the whole shop can log in again

		frappe.db.sql(
			"insert into `tabSessions` (user, sid, sessiondata, ipaddress, lastupdate, status) values (%s, %s, '{}', '10.0.0.9', now(), 'Active')",
			(NEW, "test-sid-people"),
		)
		self.assertEqual(len(people.get_person(NEW)["access"]["sessions"]), 1)
		self.assertEqual(people.log_out_everywhere(NEW), {"signed_out": 1})
		self.assertEqual(people.get_person(NEW)["access"]["sessions"], [])
		with self.assertRaises(frappe.ValidationError):
			people.log_out_everywhere(self.owner)  # not yourself

	# --- Desk is for admins and the accountant only -----------------------------------------

	def test_staff_are_sent_from_desk_to_the_staff_app(self):
		frappe.local.request = frappe._dict(path="/desk/item")
		self.addCleanup(lambda: setattr(frappe.local, "request", None))
		frappe.set_user(self.clerk)
		with self.assertRaises(frappe.Redirect):
			permissions.keep_staff_out_of_desk({})
		frappe.set_user(self.owner)
		permissions.keep_staff_out_of_desk({})  # owner may use Desk
		frappe.local.request = frappe._dict(path="/products")
		frappe.set_user(self.clerk)
		permissions.keep_staff_out_of_desk({})  # other web pages are not touched
