import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import catalogue

COUNTER_USER = "_test_at_counter@example.com"


class TestCatalogueAPI(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_add_and_rename_category_keeps_product_links(self):
		catalogue.add_category("_Test AT Serums", "Skin Care") if frappe.db.get_value(
			"Item Group", "Skin Care", "is_group"
		) else catalogue.add_category("_Test AT Serums")
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Serum 1",
				"item_group": "_Test AT Serums",
				"stock_uom": "Piece",
			}
		).insert()
		catalogue.rename("Item Group", "_Test AT Serums", "_Test AT Face Serums")
		self.assertEqual(frappe.db.get_value("Item", item.name, "item_group"), "_Test AT Face Serums")
		names = [c["name"] for c in catalogue.get_catalogue()["categories"]]
		self.assertIn("_Test AT Face Serums", names)

	def test_brand_in_use_cannot_be_deleted(self):
		catalogue.add_brand("_Test AT Glow Co")
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Branded",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"brand": "_Test AT Glow Co",
			}
		).insert()
		with self.assertRaises(frappe.ValidationError):
			catalogue.delete_brand("_Test AT Glow Co")
		catalogue.add_brand("_Test AT Unused Brand")
		catalogue.delete_brand("_Test AT Unused Brand")
		self.assertFalse(frappe.db.exists("Brand", "_Test AT Unused Brand"))

	def test_only_categories_and_brands_can_be_renamed(self):
		with self.assertRaises(frappe.ValidationError):
			catalogue.rename("Item", "VML-05", "X")

	def test_counter_staff_cannot_edit_catalogue(self):
		if not frappe.db.exists("User", COUNTER_USER):
			user = frappe.get_doc(
				{"doctype": "User", "email": COUNTER_USER, "first_name": "Test", "send_welcome_email": 0}
			).insert()
			user.add_roles("Sales User", "Stock User")
		frappe.set_user(COUNTER_USER)
		self.assertFalse(catalogue.get_catalogue()["can_edit"])
		with self.assertRaises(frappe.PermissionError):
			catalogue.add_brand("_Test AT Not Allowed")

	def test_unused_erpnext_default_categories_are_hidden(self):
		names = [c["name"] for c in catalogue.get_catalogue()["categories"]]
		for unused in ("Raw Material", "Sub Assemblies"):
			if not frappe.db.exists("Item", {"item_group": unused}):
				self.assertNotIn(unused, names)
