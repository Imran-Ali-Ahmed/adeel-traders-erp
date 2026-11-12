import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.setup.core_setup import run_stock_setup
from adeel_traders.stock.barcode import ean13_check_digit, generate_item_barcode
from adeel_traders.stock.stock_entry import validate_adjustment_reason


class TestBarcode(IntegrationTestCase):
	def test_check_digit_known_codes(self):
		# Published EAN-13 examples
		self.assertEqual(ean13_check_digit("400638133393"), "1")  # 4006381333931
		self.assertEqual(ean13_check_digit("590123412345"), "7")  # 5901234123457

	def test_check_digit_rejects_bad_input(self):
		with self.assertRaises(ValueError):
			ean13_check_digit("123")

	def test_generate_adds_valid_internal_barcode_once(self):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Barcode Item",
				"item_group": "Lips",
				"stock_uom": "Piece",
			}
		).insert()

		code = generate_item_barcode(item.name)

		self.assertEqual(len(code), 13)
		self.assertTrue(code.startswith("20"))
		self.assertEqual(code[-1], ean13_check_digit(code[:12]))
		self.assertEqual(frappe.get_doc("Item", item.name).barcodes[0].barcode, code)

		# never replaces an existing barcode
		with self.assertRaises(frappe.ValidationError):
			generate_item_barcode(item.name)


class TestAdjustmentReason(IntegrationTestCase):
	def test_reason_required_for_adjustment_types(self):
		for entry_type in ("Damaged", "Tester", "Expired", "Lost"):
			doc = frappe._dict(stock_entry_type=entry_type, at_adjustment_reason="  ")
			with self.assertRaises(frappe.ValidationError):
				validate_adjustment_reason(doc)

	def test_reason_given_passes(self):
		validate_adjustment_reason(
			frappe._dict(stock_entry_type="Damaged", at_adjustment_reason="Box crushed")
		)

	def test_normal_transfer_needs_no_reason(self):
		validate_adjustment_reason(frappe._dict(stock_entry_type="Material Transfer"))


class TestStockSetup(IntegrationTestCase):
	def test_rerun_is_safe_and_keeps_manager_access(self):
		run_stock_setup()
		before = frappe.db.count("Custom DocPerm", {"parent": "Stock Reconciliation"})
		run_stock_setup()
		self.assertEqual(frappe.db.count("Custom DocPerm", {"parent": "Stock Reconciliation"}), before)

		# the permission Frappe applies: our own row if there is one, else ERPNext's standard one
		manager = next(
			p
			for p in frappe.get_meta("Stock Reconciliation").permissions
			if p.role == "Stock Manager" and p.permlevel == 0
		)
		self.assertEqual((manager.submit, manager.create), (1, 1))

		# Store staff count in our own Stock Count (blind); they get no Stock Reconciliation access.
		self.assertFalse(
			frappe.db.exists("Custom DocPerm", {"parent": "Stock Reconciliation", "role": "Stock Counter"})
		)
