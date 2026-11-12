"""P1.9: export matches the screen (filters, permissions, cost rule) and is logged."""

import io

import frappe
from frappe.tests import IntegrationTestCase
from openpyxl import load_workbook

from adeel_traders.api.v1 import export, products

USERS = {
	"_test_at_export_owner@example.com": "AT Owner",
	"_test_at_export_store@example.com": "AT Store",  # may export, may not see cost
	"_test_at_export_counter@example.com": "AT Counter Staff",  # may not export
}


def _sheet() -> list[list]:
	content = frappe.local.response.pop("filecontent")
	frappe.local.response.pop("type", None)
	frappe.local.response.pop("filename", None)
	ws = load_workbook(io.BytesIO(content)).active
	return [list(r) for r in ws.iter_rows(values_only=True)]


class TestExportAPI(IntegrationTestCase):
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
		cls.owner, cls.store, cls.counter = USERS

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in USERS:
			frappe.db.delete("Access Log", {"user": email})
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=1, ignore_permissions=True)
		frappe.db.delete("Deleted Document", {"data": ["like", '%"\\_test%']})
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_products_file_matches_the_screen(self):
		frappe.set_user(self.owner)
		export.export_list("products", status="all")
		sheet = _sheet()
		self.assertIn("Cost (Rs)", sheet[0])
		self.assertEqual(len(sheet) - 1, products.list_products(status="all")["total"])
		self.assertTrue(
			frappe.db.exists(
				"Access Log", {"user": self.owner, "method": "export_list", "file_type": "Excel"}
			)
		)

	def test_no_cost_for_people_who_cannot_see_it(self):
		frappe.set_user(self.store)
		export.export_list("products")
		header = _sheet()[0]
		self.assertNotIn("Cost (Rs)", header)
		self.assertFalse(any("margin" in str(h) for h in header))

	def test_export_needs_the_switch(self):
		frappe.set_user(self.counter)
		with self.assertRaises(frappe.PermissionError):
			export.export_list("products")

	def test_report_export_keeps_its_filters(self):
		frappe.set_user(self.owner)
		export.export_list("report", report="stock-balance", branch="Shop Counter", from_date="2020-01-01")
		sheet = _sheet()
		col = sheet[0].index("Location")
		allowed = set(frappe.get_all("Warehouse", filters={"branch": "Shop Counter"}, pluck="name"))
		self.assertTrue(all(r[col] in allowed for r in sheet[1:]))

	def test_people_export_needs_people_rights(self):
		frappe.set_user(self.store)  # may export lists, but may not see the people list
		with self.assertRaises(frappe.PermissionError):
			export.export_list("people")
		frappe.set_user(self.owner)
		export.export_list("people")
		self.assertEqual(_sheet()[0][0], "Name")
