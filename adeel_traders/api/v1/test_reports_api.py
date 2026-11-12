"""P1.10: the report kit serves ERPNext reports with our filters and our cost rule."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import reports

USERS = {
	"_test_at_reports_owner@example.com": "AT Owner",
	"_test_at_reports_store@example.com": "AT Store",
	"_test_at_reports_hr@example.com": "AT HR",
}


class TestReportsAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for email, profile in USERS.items():
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
		cls.owner, cls.store, cls.hr = USERS

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in USERS:
			frappe.delete_doc("User", email, force=1, ignore_permissions=True)
		frappe.db.delete(
			"Deleted Document", {"data": ["like", '%"\\_test%']}
		)  # test leftovers stay out of the change history
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _keys(self, out):
		return [c["key"] for c in out["columns"]]

	def test_owner_sees_value_store_does_not(self):
		frappe.set_user(self.owner)
		out = reports.run_report("stock-balance", from_date="2020-01-01")
		self.assertIn("bal_val", self._keys(out))
		self.assertIn("bal_val", out["totals"])
		frappe.set_user(self.store)
		out = reports.run_report("stock-balance", from_date="2020-01-01")
		self.assertNotIn("bal_val", self._keys(out))
		self.assertTrue(all("bal_val" not in r for r in out["rows"]))

	def test_branch_filter_keeps_only_its_locations(self):
		frappe.set_user(self.owner)
		out = reports.run_report(
			"stock-balance", from_date="2020-01-01", branch="Shop Counter", page_length=500
		)
		allowed = set(frappe.get_all("Warehouse", filters={"branch": "Shop Counter"}, pluck="name"))
		self.assertTrue(all(r["warehouse"] in allowed for r in out["rows"]))
		with self.assertRaises(frappe.ValidationError):
			reports.run_report("stock-balance", branch="No Such Branch")

	def test_paging_and_sorting(self):
		frappe.set_user(self.owner)
		full = reports.run_report("stock-balance", from_date="2020-01-01", sort="-bal_qty", page_length=500)
		qty = [r["bal_qty"] for r in full["rows"]]
		self.assertEqual(qty, sorted(qty, reverse=True))
		if full["total"] > 1:
			page = reports.run_report(
				"stock-balance", from_date="2020-01-01", sort="-bal_qty", start=1, page_length=1
			)
			self.assertEqual(page["rows"][0], full["rows"][1])

	def test_denied_without_report_permission(self):
		frappe.set_user(self.hr)
		with self.assertRaises(frappe.PermissionError):
			reports.run_report("stock-balance")

	def test_bad_dates_and_unknown_report(self):
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			reports.run_report("stock-balance", from_date="2026-02-01", to_date="2026-01-01")
		with self.assertRaises(frappe.ValidationError):
			reports.run_report("nope")

	def test_not_for_sale_stock_is_kept_out_of_the_totals(self):
		frappe.set_user(self.owner)
		out = reports.run_report("stock-balance", from_date="2020-01-01", page_length=500)
		blocked = [r for r in out["rows"] if r["note"]]
		self.assertTrue(blocked)  # the demo site has damaged stock
		self.assertTrue(
			all(
				"Damaged" in r["warehouse"] or "Returns" in r["warehouse"] or "Testers" in r["warehouse"]
				for r in blocked
			)
		)
		sellable = sum(r["bal_qty"] for r in out["rows"] if not r["note"])
		self.assertEqual(out["totals"]["bal_qty"], round(sellable, 2))
		self.assertEqual(out["not_for_sale_totals"]["bal_qty"], round(sum(r["bal_qty"] for r in blocked), 2))

	def test_hide_zero_drops_empty_balances(self):
		frappe.set_user(self.owner)
		out = reports.run_report("stock-balance", from_date="2020-01-01", hide_zero=1, page_length=500)
		self.assertTrue(all(r["bal_qty"] for r in out["rows"]))

	def test_movement_columns_only_for_managers(self):
		frappe.set_user(self.store)
		keys = self._keys(reports.run_report("stock-balance"))
		self.assertNotIn("in_qty", keys)
		self.assertIn("bal_qty", keys)
		frappe.set_user(self.owner)
		self.assertIn("in_qty", self._keys(reports.run_report("stock-balance")))
