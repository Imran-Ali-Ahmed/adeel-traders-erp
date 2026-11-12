"""P1.1: the permission matrix (docs/PERMISSIONS.md) is enforced by the server.

Every role is tried against the real APIs, not only through the capability map, because the
screens hide buttons but the server must refuse the call itself.
"""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders import permissions
from adeel_traders.api.v1 import products, session, stock_count

PROFILES = {
	"owner": "AT Owner",
	"manager": "AT Manager",
	"counter": "AT Counter Staff",
	"store": "AT Store",
	"accountant": "AT Accountant",
	"hr": "AT HR",
	"online": "AT Online Order Team",
}

ALL = {
	"products.view",
	"products.create",
	"products.edit",
	"catalogue.manage",
	"prices.edit",
	"cost.view",
	"stock.view",
	"stock.move",
	"stock.adjust",
	"stock.backdate",
	"stock.count",
	"stock.count.post",
	"barcode.generate_print",
	"export.lists",
	"company.manage",
	"users.manage",
	"roles.manage",
	# Phase 1, P1.5-P1.12
	"reports.stock",
	"audit.view",
	"approvals.inbox",
	"email.manage",
	"integrations.view",
	"units.manage",
	"payments.manage",
	"numbering.view",
}
OWNER_ONLY = {
	"users.manage",
	"roles.manage",
	"company.manage",
	"email.manage",
	"integrations.view",
	"numbering.view",
}
MANAGER = ALL - OWNER_ONLY - {"payments.manage"}
# What each role may do (PERMISSIONS.md part 1). Everything not listed is refused.
ALLOWED = {
	"owner": ALL,
	"manager": MANAGER,
	"counter": {"products.view", "stock.view"},
	"store": {
		"products.view",
		"stock.view",
		"stock.move",
		"stock.adjust",
		"stock.count",
		"barcode.generate_print",
		"export.lists",
		"reports.stock",
	},
	"accountant": {
		"products.view",
		"stock.view",
		"cost.view",
		"export.lists",
		"company.manage",
		"reports.stock",
		"audit.view",
		"payments.manage",
	},
	"hr": {"export.lists"},
	"online": {"products.view", "stock.view"},
}

SHOP = "Shop Counter - AT"
MAIN = "Main Warehouse - AT"


def _email(role: str) -> str:
	return f"_test_at_perm_{role}@example.com"


def _make_user(role: str, branch: str | None = None) -> str:
	email = _email(role) if not branch else f"_test_at_perm_{role}_{branch[:4].lower()}@example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Test",
				"send_welcome_email": 0,
				"role_profile_name": PROFILES[role],
			}
		).insert()
	if branch and not frappe.db.exists("User Permission", {"user": email, "allow": "Branch"}):
		frappe.get_doc(
			{"doctype": "User Permission", "user": email, "allow": "Branch", "for_value": branch}
		).insert()
	return email


def _receive(code: str, qty: float, warehouse: str):
	frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": "Adeel Traders",
			"items": [{"item_code": code, "qty": qty, "t_warehouse": warehouse, "basic_rate": 100}],
		}
	).insert().submit()


class TestPermissionMatrix(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = {role: _make_user(role) for role in PROFILES}
		cls.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Perm Item",
				"item_name": "Perm Lipstick",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"valuation_rate": 100,
			}
		).insert()
		products.set_price(cls.item.name, "Wholesale", 150)
		_receive(cls.item.name, 10, MAIN)
		_receive(cls.item.name, 10, SHOP)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _as(self, role: str):
		frappe.set_user(self.users[role])

	# --- capability map ---------------------------------------------------------------

	def test_each_role_gets_exactly_its_capabilities(self):
		for role, allowed in ALLOWED.items():
			self._as(role)
			caps = permissions.capabilities()
			granted = {name for name, ok in caps.items() if ok}
			self.assertEqual(granted, allowed, f"{role}: capabilities differ from the matrix")

	def test_session_returns_capabilities_and_branches(self):
		self._as("counter")
		me = session.me()
		self.assertEqual(me["capabilities"], permissions.capabilities())
		self.assertEqual(me["can_view_cost"], False)
		self.assertEqual(me["branches"], [])
		self.assertEqual(me["role_label"], "Counter staff")

	# --- the server refuses, whatever the screen shows ---------------------------------

	def test_hr_cannot_open_products(self):
		self._as("hr")
		with self.assertRaises(frappe.PermissionError):
			products.list_products()

	def test_only_product_editors_create_and_price(self):
		for role in ("counter", "store", "accountant", "online"):
			self._as(role)
			with self.assertRaises(frappe.PermissionError, msg=role):
				products.create_product(
					item_code="_Test AT Perm New",
					item_name="x",
					item_group="Lips",
					stock_uom="Piece",
				)
			with self.assertRaises(frappe.PermissionError, msg=role):
				products.set_price(self.item.name, "Wholesale", 1)
		self.assertEqual(
			frappe.db.get_value(
				"Item Price", {"item_code": self.item.name, "price_list": "Wholesale"}, "price_list_rate"
			),
			150,
		)

	def test_cost_is_visible_only_with_can_view_cost(self):
		for role in PROFILES:
			if role == "hr":
				continue
			self._as(role)
			row = next(
				r
				for r in products.list_products(search=self.item.name)["items"]
				if r["item_code"] == self.item.name
			)
			if role in ("owner", "manager", "accountant"):
				self.assertEqual(row["cost"], 100, role)
			else:
				self.assertIsNone(row["cost"], role)
				self.assertTrue(all(p["margin"] is None for p in row["prices"]), role)

	def test_accountant_sees_stock_but_cannot_move_it(self):
		self._as("accountant")
		row = next(
			r
			for r in products.list_products(search=self.item.name)["items"]
			if r["item_code"] == self.item.name
		)
		self.assertEqual(row["stock_qty"], 20)
		with self.assertRaises(frappe.PermissionError):
			products.transfer_stock(self.item.name, MAIN, SHOP, 1)

	def test_counter_and_online_cannot_move_or_adjust_stock(self):
		for role in ("counter", "online"):
			self._as(role)
			with self.assertRaises(frappe.PermissionError, msg=role):
				products.transfer_stock(self.item.name, MAIN, SHOP, 1)
			with self.assertRaises(frappe.PermissionError, msg=role):
				products.adjust_stock(self.item.name, MAIN, 1, "Lost", "test")

	def test_store_can_move_and_adjust_stock(self):
		self._as("store")
		self.assertIn("stock_entry", products.transfer_stock(self.item.name, MAIN, SHOP, 1))
		self.assertIn("stock_entry", products.adjust_stock(self.item.name, MAIN, 1, "Lost", "test loss"))

	def test_barcode_generation_is_for_editors_and_store_only(self):
		from adeel_traders.stock.barcode import generate_item_barcode

		for role in ("counter", "accountant", "online", "hr"):
			self._as(role)
			with self.assertRaises(frappe.PermissionError, msg=role):
				generate_item_barcode(self.item.name)
		self._as("store")
		code = generate_item_barcode(self.item.name)
		self.assertEqual(len(code), 13)

	def test_stock_count_roles(self):
		for role in ("counter", "online", "accountant", "hr"):
			self._as(role)
			with self.assertRaises(frappe.PermissionError, msg=role):
				stock_count.start_count(MAIN)
		self._as("store")
		count = stock_count.start_count(MAIN)
		stock_count.scan(count["name"], self.item.name, 1)  # store staff can scan (blind)
		self.assertFalse(stock_count.get_count(count["name"])["can"]["submit"])
		with self.assertRaises(frappe.PermissionError):  # but cannot post
			stock_count.submit_count(count["name"])
		self._as("manager")
		self.assertTrue(stock_count.get_count(count["name"])["can"]["submit"])
		frappe.set_user("Administrator")
		stock_count.delete_count(count["name"])

	# --- branch rule -----------------------------------------------------------------

	def test_branch_limited_store_acts_only_on_own_branch(self):
		email = _make_user("store", "Shop Counter")
		frappe.set_user(email)
		self.assertEqual(session.me()["branches"], ["Shop Counter"])
		with self.assertRaises(frappe.PermissionError):
			products.transfer_stock(self.item.name, MAIN, SHOP, 1)  # leaves the other branch
		with self.assertRaises(frappe.PermissionError):
			products.adjust_stock(self.item.name, MAIN, 1, "Lost", "x")
		# own branch works, and may send stock to another branch
		self.assertIn("stock_entry", products.transfer_stock(self.item.name, SHOP, MAIN, 1))
		with self.assertRaises(frappe.PermissionError):
			stock_count.start_count(MAIN)
		count = stock_count.start_count(SHOP)
		self.assertEqual(count["warehouse"], SHOP)
		frappe.set_user("Administrator")
		stock_count.delete_count(count["name"])

	def test_branch_limited_user_still_reads_other_branch_quantities(self):
		frappe.set_user(_make_user("counter", "Shop Counter"))
		row = next(
			r
			for r in products.list_products(search=self.item.name)["items"]
			if r["item_code"] == self.item.name
		)
		self.assertEqual(row["stock_qty"], 20)  # Main + Shop, read-only

	def test_branch_limited_user_sees_only_own_counts(self):
		frappe.set_user("Administrator")
		main_count = stock_count.start_count(MAIN)
		shop_count = stock_count.start_count(SHOP)
		frappe.set_user(_make_user("store", "Shop Counter"))
		names = {c["name"] for c in stock_count.list_counts()["counts"]}
		self.assertIn(shop_count["name"], names)
		self.assertNotIn(main_count["name"], names)
		with self.assertRaises(frappe.PermissionError):
			stock_count.get_count(main_count["name"])
		frappe.set_user("Administrator")
		stock_count.delete_count(main_count["name"])
		stock_count.delete_count(shop_count["name"])

	def test_pickers_for_branch_limited_user(self):
		frappe.set_user(_make_user("store", "Shop Counter"))
		options = products.form_options()
		self.assertIn(SHOP, options["warehouses"])
		self.assertNotIn(MAIN, options["warehouses"])
		self.assertIn(MAIN, options["all_warehouses"])
		self.assertIn("Damaged - AT", options["warehouses"])  # shared location
