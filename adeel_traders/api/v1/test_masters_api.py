"""P1.12: Settings lists (units, payment methods) and the numbering view."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import masters

USERS = {
	"_test_at_masters_owner@example.com": "AT Owner",
	"_test_at_masters_counter@example.com": "AT Counter Staff",
}


class TestMastersAPI(IntegrationTestCase):
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
		cls.owner, cls.counter = USERS

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_units_list_is_the_business_own(self):
		frappe.set_user(self.owner)
		out = masters.list_masters("units")
		names = {i["name"] for i in out["items"]}
		self.assertIn("Piece", names)
		self.assertNotIn("Ampere", names)  # ERPNext sample, switched off
		piece = next(i for i in out["items"] if i["name"] == "Piece")
		self.assertGreater(piece["used"], 0)

	def test_add_rename_switch_off_delete_unit(self):
		frappe.set_user(self.owner)
		masters.add_master("units", "_Test Tube")
		with self.assertRaises(frappe.ValidationError):
			masters.add_master("units", "_test tube")  # same name, other case
		self.assertEqual(masters.rename_master("units", "_Test Tube", "_Test Tubes")["name"], "_Test Tubes")
		self.assertFalse(masters.set_master_enabled("units", "_Test Tubes", 0)["enabled"])
		masters.delete_master("units", "_Test Tubes")
		self.assertFalse(frappe.db.exists("UOM", "_Test Tubes"))

	def test_unit_in_use_cannot_be_deleted(self):
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			masters.delete_master("units", "Piece")

	def test_switched_off_sample_unit_comes_back_on(self):
		frappe.set_user(self.owner)
		self.addCleanup(frappe.db.set_value, "UOM", "Ampere", "enabled", 0)
		self.assertEqual(masters.add_master("units", "ampere")["name"], "Ampere")
		self.assertTrue(frappe.db.get_value("UOM", "Ampere", "enabled"))

	def test_payment_methods_need_a_kind(self):
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			masters.add_master("payment-methods", "_Test JazzCash")
		masters.add_master("payment-methods", "_Test JazzCash", "Phone")
		row = next(
			i for i in masters.list_masters("payment-methods")["items"] if i["name"] == "_Test JazzCash"
		)
		self.assertEqual(row["type"], "Phone")

	def test_counter_cannot_change_lists_or_see_numbering(self):
		frappe.set_user(self.counter)
		self.assertFalse(masters.list_masters("units")["can_edit"])
		with self.assertRaises(frappe.PermissionError):
			masters.add_master("units", "_Test Bag")
		with self.assertRaises(frappe.PermissionError):
			masters.numbering()

	def test_numbering_shows_next_numbers(self):
		frappe.set_user(self.owner)
		docs = {d["doctype"]: d for d in masters.numbering()["documents"]}
		self.assertIn("Sales Invoice", docs)
		self.assertTrue(docs["Stock Entry"]["series"][0]["next"])

	# --- standard sizes (SRS 3.2) ----------------------------------------------------------

	def test_dozen_is_12_pieces_for_every_product_through_frappes_own_conversion(self):
		from erpnext.stock.get_item_details import get_conversion_factor

		frappe.set_user(self.owner)
		listed = {u["name"]: u for u in masters.list_masters("units")["items"]}
		self.assertEqual(listed["Dozen"]["standard"], 12)
		self.assertTrue(listed["Piece"]["base_unit"])
		code = frappe.get_all("Item", filters={"is_stock_item": 1, "has_variants": 0}, pluck="name", limit=1)[
			0
		]
		# the product has no size of its own for Dozen: ERPNext falls back to the standard size
		self.assertFalse(frappe.db.exists("UOM Conversion Detail", {"parent": code, "uom": "Dozen"}))
		self.assertEqual(get_conversion_factor(code, "Dozen")["conversion_factor"], 12)

	def test_set_change_and_clear_a_standard_size(self):
		frappe.set_user(self.owner)
		masters.add_master("units", "_Test Pack", pieces=6)
		unit = lambda: next(u for u in masters.list_masters("units")["items"] if u["name"] == "_Test Pack")  # noqa: E731
		self.assertEqual(unit()["standard"], 6)
		masters.set_standard_size("_Test Pack", 24)
		self.assertEqual(unit()["standard"], 24)
		masters.set_standard_size("_Test Pack", None)
		self.assertIsNone(unit()["standard"])
		for bad in (
			0,
			-3,
			2_000_000,
			2.5,
		):  # a pack of whole pieces: no zero, negative, huge or fractional size
			with self.assertRaises(frappe.ValidationError):
				masters.set_standard_size("_Test Pack", bad)
		masters.set_master_enabled("units", "_Test Pack", 0)
		masters.delete_master("units", "_Test Pack")  # its size goes with it
		self.assertFalse(frappe.db.exists("UOM Conversion Factor", {"from_uom": "_Test Pack"}))

	def test_a_unit_that_can_be_split_accepts_fractions(self):
		frappe.set_user(self.owner)
		masters.add_master("units", "_Test Litre Pack", whole_number=0)
		self.assertFalse(
			next(u for u in masters.list_masters("units")["items"] if u["name"] == "_Test Litre Pack")[
				"whole_number"
			]
		)
		masters.set_standard_size("_Test Litre Pack", 2.5)  # allowed: it is not counted in whole numbers

	def test_the_base_unit_and_the_apps_own_units_are_protected(self):
		frappe.set_user(self.owner)
		for call in (
			lambda: masters.rename_master("units", "Piece", "Pc"),
			lambda: masters.set_master_enabled("units", "Piece", 0),
			lambda: masters.delete_master("units", "Piece"),
			lambda: masters.rename_master("units", "Box", "Case"),
			lambda: masters.delete_master("units", "Carton"),
			lambda: masters.set_standard_size("Piece", 2),
		):
			with self.assertRaises(frappe.ValidationError):
				call()
		masters.set_master_enabled("units", "Carton", 1)  # switching on again is fine

	def test_counter_cannot_set_sizes(self):
		frappe.set_user(self.counter)
		with self.assertRaises(frappe.PermissionError):
			masters.set_standard_size("Dozen", 10)

	def test_a_unit_switched_off_stays_in_the_list_to_switch_on_again(self):
		frappe.set_user(self.owner)
		masters.add_master("units", "_Test Gone Pack", pieces=4)  # given a size, so it stays listed when off
		masters.set_master_enabled("units", "_Test Gone Pack", 0)
		item = next(u for u in masters.list_masters("units")["items"] if u["name"] == "_Test Gone Pack")
		self.assertFalse(item["enabled"])
		masters.set_master_enabled("units", "_Test Gone Pack", 1)
		self.assertTrue(
			next(u for u in masters.list_masters("units")["items"] if u["name"] == "_Test Gone Pack")[
				"enabled"
			]
		)
		masters.set_master_enabled("units", "_Test Gone Pack", 0)
		masters.delete_master("units", "_Test Gone Pack")

	def test_cash_is_protected_other_methods_are_free(self):
		frappe.set_user(self.owner)
		for call in (
			lambda: masters.rename_master("payment-methods", "Cash", "Notes"),
			lambda: masters.set_master_enabled("payment-methods", "Cash", 0),
			lambda: masters.delete_master("payment-methods", "Cash"),
		):
			with self.assertRaises(frappe.ValidationError):
				call()
		cash = next(i for i in masters.list_masters("payment-methods")["items"] if i["name"] == "Cash")
		self.assertTrue(cash["core"])
		self.assertFalse(cash["can_delete"])
		masters.add_master("payment-methods", "_Test Wallet", "Phone")
		masters.set_master_enabled("payment-methods", "_Test Wallet", 0)
		masters.set_master_enabled("payment-methods", "_Test Wallet", 1)
		masters.delete_master("payment-methods", "_Test Wallet")
