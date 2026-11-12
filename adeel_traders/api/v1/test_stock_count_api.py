import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import stock_count

COUNTER = "_test_at_store_counter@example.com"
WH = "Shop Counter - AT"


def _receive(code, qty, warehouse=WH, rate=10):
	frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": "Adeel Traders",
			"items": [{"item_code": code, "qty": qty, "t_warehouse": warehouse, "basic_rate": rate}],
		}
	).insert().submit()


class TestStockCountAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Count Item",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"barcodes": [{"barcode": "5901234123457", "barcode_type": "EAN-13"}],
			}
		).insert()
		_receive(cls.item.name, 10)
		if not frappe.db.exists("User", COUNTER):
			user = frappe.get_doc(
				{"doctype": "User", "email": COUNTER, "first_name": "Counter", "send_welcome_email": 0}
			).insert()
			user.add_roles("Stock Counter", "Stock User")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_scan_counts_and_shows_difference(self):
		count = stock_count.start_count(WH)
		for _ in range(3):
			result = stock_count.scan(count["name"], "5901234123457")
		row = result["rows"][0]
		system = frappe.db.get_value("Bin", {"item_code": self.item.name, "warehouse": WH}, "actual_qty")
		self.assertEqual((row["counted"], row["system"], row["difference"]), (3, system, 3 - system))
		self.assertEqual(row["difference_value"], (3 - system) * 10)

		result = stock_count.set_counted(count["name"], row["row"], system)
		self.assertEqual(result["rows"][0]["difference"], 0)  # the row stays even with no difference
		self.assertEqual(result["totals"]["rows_with_difference"], 0)

		with self.assertRaises(frappe.ValidationError):
			stock_count.scan(count["name"], "0000000000000")

	def test_counter_cannot_submit_and_manager_submit_posts_stock(self):
		frappe.set_user(COUNTER)
		count = stock_count.start_count(WH)
		stock_count.scan(count["name"], self.item.name, qty=8)
		self.assertFalse(stock_count.get_count(count["name"])["can"]["submit"])
		with self.assertRaises(frappe.PermissionError):
			stock_count.submit_count(count["name"])

		frappe.set_user("Administrator")
		done = stock_count.submit_count(count["name"])
		self.assertEqual(done["status"], "Submitted")
		self.assertTrue(done["stock_reconciliation"])
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": self.item.name, "warehouse": WH}, "actual_qty"), 8
		)
		with self.assertRaises(frappe.ValidationError):  # submitted counts are closed
			stock_count.scan(count["name"], self.item.name)

	def test_gift_set_cannot_be_counted(self):
		part = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Count Part",
				"item_group": "Lips",
				"stock_uom": "Piece",
			}
		).insert()
		part2 = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Count Part 2",
				"item_group": "Lips",
				"stock_uom": "Piece",
			}
		).insert()
		gift = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Count Set",
				"item_group": "Gift Sets",
				"stock_uom": "Piece",
				"is_stock_item": 0,
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Product Bundle",
				"new_item_code": gift.name,
				"items": [{"item_code": part.name, "qty": 1}, {"item_code": part2.name, "qty": 1}],
			}
		).insert()
		count = stock_count.start_count(WH)
		with self.assertRaises(frappe.ValidationError):
			stock_count.scan(count["name"], gift.name)

	def test_counters_are_blind_on_the_server(self):
		frappe.set_user(COUNTER)
		count = stock_count.start_count(WH)
		result = stock_count.scan(count["name"], self.item.name, qty=2)
		row = result["rows"][0]
		self.assertEqual(row["counted"], 2)
		self.assertIsNone(row["system"])
		self.assertIsNone(row["difference"])
		self.assertIsNone(result["totals"]["difference"])
		self.assertEqual(result["unscanned"], [])

		listed = next(c for c in stock_count.list_counts()["counts"] if c["name"] == count["name"])
		self.assertIsNone(listed["difference"])  # the list is blind too

		frappe.set_user("Administrator")  # a manager sees the same count in full
		full = stock_count.get_count(count["name"])
		self.assertIsNotNone(full["rows"][0]["system"])

	def test_unscanned_as_zero_and_cancel_restores_stock(self):
		other = frappe.get_doc(
			{"doctype": "Item", "item_code": "_Test AT Unscanned", "item_group": "Lips", "stock_uom": "Piece"}
		).insert()
		_receive(other.name, 6)
		before = frappe.db.get_value("Bin", {"item_code": self.item.name, "warehouse": WH}, "actual_qty")
		count = stock_count.start_count(WH)
		stock_count.scan(
			count["name"], self.item.name, qty=before
		)  # this one matches; the other is not scanned

		plain = stock_count.get_count(count["name"])
		self.assertEqual(plain["unscanned"], [])  # off by default

		with_zero = stock_count.set_unscanned_as_zero(count["name"], True)
		missing = {m["item_code"]: m for m in with_zero["unscanned"]}
		self.assertEqual(missing[other.name]["difference"], -6)
		self.assertGreaterEqual(with_zero["totals"]["rows_with_difference"], 1)

		done = stock_count.submit_count(count["name"])
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": other.name, "warehouse": WH}, "actual_qty"), 0
		)
		self.assertEqual(done["status"], "Submitted")
		# the posted count keeps the line it posted, even though the stock is now zero
		kept = next(r for r in done["rows"] if r["item_code"] == other.name)
		self.assertTrue(kept["not_scanned"])
		self.assertEqual((kept["system"], kept["counted"], kept["difference"]), (6, 0, -6))
		self.assertEqual(done["unscanned"], [])
		self.assertGreaterEqual(done["totals"]["rows_with_difference"], 1)

		back = stock_count.cancel_count(count["name"])
		self.assertEqual(back["status"], "Cancelled")
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": other.name, "warehouse": WH}, "actual_qty"), 6
		)
		with self.assertRaises(frappe.ValidationError):  # only a posted count can be cancelled
			stock_count.cancel_count(count["name"])

	def test_counter_cannot_cancel(self):
		count = stock_count.start_count(WH)
		stock_count.scan(count["name"], self.item.name, qty=1)
		stock_count.submit_count(count["name"])
		frappe.set_user(COUNTER)
		with self.assertRaises(frappe.PermissionError):
			stock_count.cancel_count(count["name"])
