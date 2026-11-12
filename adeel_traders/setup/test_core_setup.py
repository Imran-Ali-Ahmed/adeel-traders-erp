import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.setup import data
from adeel_traders.setup.core_setup import run_setup


def _counts() -> dict:
	return {
		doctype: frappe.db.count(doctype)
		for doctype in (
			"UOM",
			"Item Group",
			"Price List",
			"Customer Group",
			"Warehouse",
			"Sales Channel",
			"Accounting Dimension",
		)
	}


class TestCoreSetup(IntegrationTestCase):
	def test_masters_exist(self):
		run_setup()
		for uom in data.UOMS:
			self.assertTrue(frappe.db.exists("UOM", uom))
		for group, children in data.ITEM_GROUPS.items():
			self.assertTrue(frappe.db.exists("Item Group", group))
			for child in children:
				self.assertEqual(frappe.db.get_value("Item Group", child, "parent_item_group"), group)
		for channel in data.SALES_CHANNELS:
			self.assertTrue(frappe.db.exists("Sales Channel", channel))
		self.assertTrue(
			frappe.db.exists("Accounting Dimension", {"document_type": data.ACCOUNTING_DIMENSION})
		)

	def test_customer_group_gets_channel_price_list(self):
		run_setup()
		for group, price_list in data.CUSTOMER_GROUPS.items():
			self.assertEqual(frappe.db.get_value("Customer Group", group, "default_price_list"), price_list)
			self.assertTrue(frappe.db.get_value("Price List", price_list, "selling"))

	def test_running_twice_creates_no_duplicates(self):
		run_setup()
		before = _counts()
		run_setup()
		self.assertEqual(_counts(), before)

	def test_admin_changes_to_settings_are_kept(self):
		run_setup()
		original = frappe.db.get_single_value("Stock Settings", "auto_indent")
		self.addCleanup(frappe.db.set_single_value, "Stock Settings", "auto_indent", original)

		frappe.db.set_single_value("Stock Settings", "auto_indent", 0)
		run_setup()
		self.assertEqual(frappe.db.get_single_value("Stock Settings", "auto_indent"), 0)

	def test_admin_changes_to_masters_are_kept(self):
		run_setup()
		original = frappe.db.get_value("Customer Group", "Walk-in Customer", "default_price_list")
		self.addCleanup(
			frappe.db.set_value, "Customer Group", "Walk-in Customer", "default_price_list", original
		)

		frappe.db.set_value("Customer Group", "Walk-in Customer", "default_price_list", "Online")
		run_setup()
		self.assertEqual(
			frappe.db.get_value("Customer Group", "Walk-in Customer", "default_price_list"), "Online"
		)

	def test_stock_rules_are_set(self):
		run_setup()
		settings = frappe.get_single("Stock Settings")
		self.assertEqual(settings.auto_insert_price_list_rate_if_missing, 0)
		self.assertEqual(settings.role_allowed_to_create_edit_back_dated_transactions, "Stock Manager")
		self.assertEqual(
			frappe.db.get_value("Company", "Adeel Traders", "default_warehouse"), "Main Warehouse - AT"
		)

	def test_reorder_email_is_on(self):
		run_setup()
		self.assertEqual(frappe.db.get_single_value("Stock Settings", "reorder_email_notify"), 1)

	def test_unused_erpnext_warehouses_are_disabled(self):
		run_setup()
		default = frappe.db.get_value("Company", "Adeel Traders", "default_warehouse")
		for name in data.ERPNEXT_FACTORY_WAREHOUSES:
			warehouse = f"{name} - AT"
			if not frappe.db.exists("Warehouse", warehouse) or warehouse == default:
				continue
			if frappe.db.exists("Bin", {"warehouse": warehouse, "actual_qty": ["!=", 0]}):
				continue  # holds stock: never hidden
			self.assertEqual(frappe.db.get_value("Warehouse", warehouse, "disabled"), 1, warehouse)
		for name in data.WAREHOUSES:
			self.assertEqual(frappe.db.get_value("Warehouse", f"{name} - AT", "disabled"), 0, name)

	def test_stock_counter_cannot_open_stock_reconciliation(self):
		run_setup()
		self.assertFalse(
			frappe.db.exists(
				"Custom DocPerm", {"parent": "Stock Reconciliation", "role": data.STOCK_COUNTER_ROLE}
			)
		)
