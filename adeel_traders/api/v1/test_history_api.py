"""P1.5: change history reads Frappe's Versions in plain words, with our permission and cost rules."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import history, people

USERS = {
	"_test_at_history_owner@example.com": "AT Owner",
	"_test_at_history_store@example.com": "AT Store",
	"_test_at_history_counter@example.com": "AT Counter Staff",
}
ITEM = "_TEST-HISTORY-1"


def _cleanup():
	frappe.set_user("Administrator")
	for price in frappe.get_all("Item Price", filters={"item_code": ITEM}, pluck="name"):
		frappe.db.delete("Version", {"ref_doctype": "Item Price", "docname": price})
		frappe.delete_doc("Item Price", price, force=1, ignore_permissions=True)
	if frappe.db.exists("Item", ITEM):
		frappe.delete_doc("Item", ITEM, force=1, ignore_permissions=True)
	frappe.db.delete("Version", {"ref_doctype": "Item", "docname": ITEM})
	frappe.db.delete(
		"Deleted Document",
		{"deleted_doctype": ["in", ["Item", "Item Price"]], "deleted_name": ["like", "%HISTORY%"]},
	)
	for email in USERS:
		frappe.db.delete("Comment", {"reference_doctype": "User", "reference_name": email})
		frappe.db.delete("Version", {"ref_doctype": "User", "docname": email})
		if frappe.db.exists("User", email):
			frappe.db.delete("User Permission", {"user": email})
			frappe.delete_doc("User", email, force=1, ignore_permissions=True)
	frappe.db.delete("Deleted Document", {"deleted_doctype": "User", "deleted_name": ["in", list(USERS)]})


class TestHistoryAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		for email, profile in USERS.items():
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Test",
					"send_welcome_email": 0,
					"role_profiles": [{"role_profile": profile}],
				}
			).insert()
		cls.owner, cls.store, cls.counter = USERS
		group = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM,
				"item_name": "History Lipstick",
				"item_group": group,
				"stock_uom": "Nos",
				"valuation_rate": 100,
			}
		).insert()
		item.item_name = "History Lipstick Red"
		item.valuation_rate = 120
		item.save(ignore_version=False)  # Frappe skips history in tests unless asked
		price = frappe.get_doc(
			{"doctype": "Item Price", "item_code": ITEM, "price_list": "Wholesale", "price_list_rate": 450}
		).insert()
		price.price_list_rate = 500
		price.save(ignore_version=False)

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		frappe.db.delete(
			"Deleted Document", {"data": ["like", '%"\\_test%']}
		)  # test leftovers stay out of the change history
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _fields(self, out):
		return {(c["field"], c["from"], c["to"]) for e in out["events"] for c in e["changes"]}

	def test_product_history_in_plain_words_with_prices(self):
		frappe.set_user(self.owner)
		out = history.get_history("Item", ITEM)
		fields = self._fields(out)
		self.assertIn(("Item Name", "History Lipstick", "History Lipstick Red"), fields)
		self.assertTrue(any(f == ("Rate", "Rs 450", "Rs 500") for f in fields), fields)  # price change
		self.assertTrue(any(e["summary"].startswith("Set the Wholesale price") for e in out["events"]))
		self.assertIn(("Valuation Rate", "Rs 100", "Rs 120"), fields)  # owner sees cost
		self.assertFalse(any(f[1] == "0.0" for f in fields))  # "0 → empty" is not a change
		self.assertEqual(out["events"][-1]["kind"], "created")
		self.assertTrue(all(e["user_name"] for e in out["events"]))

	def test_cost_hidden_without_cost_view(self):
		frappe.set_user(self.store)
		fields = self._fields(history.get_history("Item", ITEM))
		self.assertIn(("Item Name", "History Lipstick", "History Lipstick Red"), fields)
		self.assertFalse(any(f[0] == "Valuation Rate" for f in fields))

	def test_record_permission_and_unknown_doctypes(self):
		frappe.set_user(self.counter)
		with self.assertRaises(frappe.PermissionError):
			history.get_history("User", self.owner)
		with self.assertRaises(frappe.ValidationError):
			history.get_history("ToDo", "x")

	def test_person_history_shows_branch_and_password_notes(self):
		frappe.set_user(self.owner)
		people.update_person(self.counter, branches=["Shop Counter"])
		people.reset_password(self.counter)
		summaries = [e["summary"] for e in history.get_history("User", self.counter)["events"]]
		self.assertIn("Works at: All branches → Shop Counter", summaries)
		self.assertIn("Password reset: a temporary password was given", summaries)

	def test_audit_log_for_owner_only_with_filters(self):
		frappe.set_user(self.owner)
		out = history.audit_log(area="Products", user="Administrator", page_length=100)
		self.assertTrue(out["events"])
		self.assertTrue(all(e["area"] == "Products" and e["user"] == "Administrator" for e in out["events"]))
		self.assertIn("Logins", out["areas"])
		with self.assertRaises(frappe.ValidationError):
			history.audit_log(from_date="2026-02-01", to_date="2026-01-01")
		frappe.set_user(self.counter)
		with self.assertRaises(frappe.PermissionError):
			history.audit_log()

	def test_tracking_switched_on(self):
		from adeel_traders.setup import data

		for doctype in data.TRACKED_DOCTYPES:
			self.assertTrue(frappe.get_meta(doctype).track_changes, doctype)

	def _failed(self, minutes_ago: int, ip: str = "10.0.0.9"):
		log = frappe.get_doc(
			{
				"doctype": "Activity Log",
				"user": self.counter,
				"operation": "Login",
				"status": "Failed",
				"subject": "_test failed",
				"ip_address": ip,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Activity Log",
			log.name,
			{
				"creation": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-minutes_ago),
				"ip_address": ip,
			},
			update_modified=False,
		)

	def test_failed_logins_are_grouped_and_kept_under_security(self):
		self.addCleanup(frappe.db.delete, "Activity Log", {"subject": "_test failed"})  # even when it fails
		frappe.db.delete("Activity Log", {"subject": "_test failed"})
		for m in (1, 2, 3):
			self._failed(m)
		self._failed(200, "10.0.0.8")  # much earlier: its own line
		frappe.set_user(self.owner)
		events = history.audit_log(area="Logins", user=self.counter, page_length=100)["events"]
		failed = [e for e in events if e["kind"] == "login_failed"]
		self.assertEqual([e["summary"] for e in failed], ["3 failed logins", "Failed login"])
		self.assertEqual(failed[0]["detail"], "From 10.0.0.9")  # the address is a detail, not the headline
		everything = history.audit_log(user=self.counter, page_length=100)["events"]
		self.assertFalse(any(e["kind"] == "login_failed" for e in everything))  # not in the business log

	def test_audit_log_words_search_and_system_changes(self):
		frappe.set_user(self.owner)
		out = history.audit_log(area="Products", page_length=100)
		self.assertFalse(any(e["user"] == "Administrator" for e in out["events"]))  # system changes hidden
		with_system = history.audit_log(area="Products", include_system=1, page_length=100)["events"]
		mine = [e for e in with_system if e["name"] == ITEM]
		self.assertEqual(mine[0]["type_word"], "product")
		found = history.audit_log(
			area="Products", include_system=1, search="history lipstick red", page_length=100
		)["events"]
		self.assertTrue(found and all(e["name"] in (ITEM,) or "History" in e["title"] for e in found))
