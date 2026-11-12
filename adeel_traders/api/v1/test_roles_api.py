"""P1.2: the Roles & permissions API. A role the owner creates must give exactly what was ticked,
and the SERVER (not the screen) must refuse everything else."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders import permission_areas as pa
from adeel_traders import permissions
from adeel_traders.api.v1 import approvals, history, masters, products, reports, roles, stock_count

ROLE = "_Test Senior Cashier"
OTHER = "_Test Other Role"
SHOP = "Shop Counter - AT"
MAIN = "Main Warehouse - AT"
OWNER = "_test_at_roles_owner@example.com"
CASHIER = "_test_at_roles_cashier@example.com"
PLAIN = "_test_at_roles_plain@example.com"


def _user(email: str, *role_names: str, profile: str | None = None) -> str:
	if not frappe.db.exists("User", email):
		doc = {"doctype": "User", "email": email, "first_name": "Test", "send_welcome_email": 0}
		if profile:
			doc["role_profile_name"] = profile
		frappe.get_doc(doc).insert()
	if role_names:
		frappe.get_doc("User", email).add_roles(*role_names)
	return email


def _drop_role(name: str):
	frappe.db.delete("User Role Profile", {"role_profile": name})
	if frappe.db.exists("Role Profile", name):
		frappe.delete_doc("Role Profile", name, force=1)
	if frappe.db.exists("Role", name):
		frappe.db.delete("Has Role", {"role": name})
		frappe.db.delete("Custom DocPerm", {"role": name})
		frappe.delete_doc("Role", name, force=1)


class TestRolesAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.owner = _user(OWNER, profile="AT Owner")
		cls.plain = _user(PLAIN, profile="AT Counter Staff")
		cls.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Role Item",
				"item_name": "Role Lipstick",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"valuation_rate": 100,
			}
		).insert()
		products.set_price(cls.item.name, "Wholesale", 150)

	def setUp(self):
		frappe.set_user("Administrator")
		for name in (ROLE, OTHER):
			_drop_role(name)

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in (ROLE, OTHER):
			_drop_role(name)

	def _make(self, areas, cost=False, export=False, name=ROLE):
		frappe.set_user(self.owner)
		roles.create_role(name, "test role")
		out = roles.save_role(name, areas, {"cost": cost, "export": export})
		frappe.set_user("Administrator")
		return out

	def _cashier(self):
		frappe.set_user("Administrator")
		_user(CASHIER, ROLE)
		frappe.set_user(CASHIER)

	# --- what the ticks give ------------------------------------------------------------

	def test_ticks_give_exactly_those_capabilities(self):
		self._make({"products": ["view"], "prices": ["edit"], "stock": ["view"]})
		self._cashier()
		caps = {k for k, v in permissions.capabilities().items() if v}
		self.assertEqual(caps, {"products.view", "prices.edit", "stock.view"})

	def test_server_refuses_what_was_not_ticked(self):
		self._make({"products": ["view"], "prices": ["edit"]})
		self._cashier()
		self.assertTrue(products.list_products(search=self.item.name)["items"])
		products.set_price(self.item.name, "Wholesale", 160)  # ticked: allowed
		with self.assertRaises(frappe.PermissionError):
			products.create_product(item_code="_Test AT Role New", item_name="x", item_group="Lips")
		with self.assertRaises(frappe.PermissionError):
			products.transfer_stock(self.item.name, MAIN, SHOP, 1)
		with self.assertRaises(frappe.PermissionError):
			stock_count.start_count(MAIN)
		frappe.set_user("Administrator")
		products.set_price(self.item.name, "Wholesale", 150)

	def test_add_products_works_without_edit_or_price_rights(self):
		self._make({"products": ["view", "create"]})
		self._cashier()
		out = products.create_product(item_code="_Test AT Role New", item_name="Role New", item_group="Lips")
		self.addCleanup(frappe.delete_doc, "Item", out["item_code"], force=1)
		self.assertTrue(frappe.db.get_value("Item", out["item_code"], "name"))
		self.assertTrue(frappe.get_doc("Item", out["item_code"]).barcodes)  # internal barcode was added
		caps = permissions.capabilities()
		self.assertTrue(caps["products.create"])
		self.assertFalse(caps["products.edit"])
		with self.assertRaises(frappe.PermissionError):
			products.update_product(
				item_code=out["item_code"],
				item_name="y",
				item_group="Lips",
				reorder_level=0,
				reorder_qty=0,
				disabled=False,
			)

	def test_form_options_work_for_a_role_without_stock_access(self):
		"""A role that may add products but not see stock must still get a working New product form."""
		self._make({"products": ["view", "create"]})
		self._cashier()
		options = products.form_options()
		self.assertEqual(options["warehouses"], [])
		self.assertEqual(options["all_warehouses"], [])
		self.assertIn("Lips", options["item_groups"])
		self.assertTrue(options["price_lists"])

	def test_cost_and_export_switches(self):
		self._make({"products": ["view"]}, cost=False, export=False)
		self._cashier()
		caps = permissions.capabilities()
		self.assertFalse(caps["cost.view"] or caps["export.lists"])
		row = products.list_products(search=self.item.name)["items"][0]
		self.assertIsNone(row["cost"])
		frappe.set_user(self.owner)
		roles.save_role(ROLE, {"products": ["view"]}, {"cost": True, "export": True})
		frappe.set_user(CASHIER)
		self.assertTrue(permissions.capabilities()["cost.view"])
		self.assertTrue(permissions.capabilities()["export.lists"])
		self.assertEqual(products.list_products(search=self.item.name)["items"][0]["cost"], 100)

	def test_unticking_removes_access_again(self):
		self._make({"products": ["view", "create", "edit"]})
		self._cashier()
		self.assertTrue(permissions.capabilities()["products.edit"])
		frappe.set_user(self.owner)
		roles.save_role(ROLE, {"products": ["view"]}, {})
		frappe.set_user(CASHIER)
		self.assertFalse(permissions.capabilities()["products.edit"])

	# --- guard rails ----------------------------------------------------------------------

	def test_half_working_combinations_are_refused(self):
		frappe.set_user(self.owner)
		roles.create_role(ROLE)
		for areas in (
			{"products": ["create"]},  # add without see
			{"stock": ["view"]},  # stock needs products view
			{"stock_counts": ["view"], "products": ["view"]},  # counts need stock view
			{"prices": ["edit"]},  # prices need products view
			{"nonsense": ["view"]},
			{"products": ["fly"]},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(areas)):
				roles.save_role(ROLE, areas, {})

	def test_built_in_and_standard_roles_cannot_be_changed(self):
		frappe.set_user(self.owner)
		for name in ("AT Store", "Sales User", "System Manager"):
			with self.assertRaises(frappe.ValidationError, msg=name):
				roles.save_role(name, {"products": ["view"]}, {})
			with self.assertRaises(frappe.ValidationError, msg=name):
				roles.delete_role(name)

	def test_names_are_checked(self):
		frappe.set_user(self.owner)
		for bad in (
			"",
			"ab",
			"x" * 41,
			"AT Boss",
			"Sales User",
			"sales user",
			"Manager",
			"counter staff",
			"Store",
			"Sales",
			"inventory",
		):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				roles.create_role(bad)
		roles.create_role(ROLE)
		with self.assertRaises(frappe.ValidationError):
			roles.create_role(ROLE.lower())

	def test_role_in_use_cannot_be_deleted(self):
		self._make({"products": ["view"]})
		_user(CASHIER, ROLE)
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			roles.delete_role(ROLE)
		frappe.set_user("Administrator")
		frappe.db.delete("Has Role", {"role": ROLE, "parent": CASHIER})
		frappe.set_user(self.owner)
		self.assertEqual(roles.delete_role(ROLE), {"deleted": ROLE})
		self.assertFalse(frappe.db.exists("Role", ROLE))
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"role": ROLE}))

	def test_only_the_owner_may_use_the_api(self):
		frappe.set_user(self.plain)
		for call in (
			lambda: roles.list_roles(),
			lambda: roles.get_role("AT Store"),
			lambda: roles.create_role(ROLE),
			lambda: roles.save_role(ROLE, {}, {}),
			lambda: roles.delete_role(ROLE),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_changing_one_role_leaves_others_alone(self):
		store_before = pa.effective_grants(["Stock User", "Purchase User", "Stock Counter"])
		self._make({"products": ["view", "create", "edit"], "stock": ["view", "create"]}, name=OTHER)
		self._make({"products": ["view"]})
		self.assertEqual(pa.effective_grants(["Stock User", "Purchase User", "Stock Counter"]), store_before)
		frappe.set_user(self.owner)
		self.assertEqual(roles.get_role(OTHER)["areas"]["products"], ["view", "create", "edit"])

	# --- reading and copying --------------------------------------------------------------

	def test_built_in_roles_show_what_they_really_can_do(self):
		frappe.set_user(self.owner)
		by_name = {r["name"]: r for r in roles.list_roles()["roles"]}
		self.assertEqual(len(by_name), 7 + len(frappe.get_all("Role", filters={"at_managed": 1})))
		self.assertEqual(by_name["AT Counter Staff"]["areas"].get("products"), ["view"])
		self.assertNotIn("stock_counts", by_name["AT Counter Staff"]["areas"])
		self.assertEqual(by_name["AT Store"]["areas"]["stock_counts"], ["view", "create"])
		self.assertIn("post", by_name["AT Manager"]["areas"]["stock_counts"])
		self.assertTrue(by_name["AT Accountant"]["flags"]["cost"])
		self.assertFalse(by_name["AT Counter Staff"]["flags"]["cost"])
		self.assertTrue(all(r["built_in"] for n, r in by_name.items() if n.startswith("AT ")))
		self.assertTrue(all(len(r["description"]) > 20 for n, r in by_name.items() if r["built_in"]))
		# the overview table: one level per area, and the people behind each role
		self.assertEqual(by_name["AT Store"]["levels"]["stock"], "move")
		self.assertEqual(by_name["AT Store"]["levels"]["stock_counts"], "count")
		self.assertEqual(by_name["AT Manager"]["levels"]["stock_counts"], "post")
		self.assertEqual(by_name["AT Counter Staff"]["levels"]["products"], "see")
		self.assertEqual(by_name["AT Counter Staff"]["levels"]["prices"], "none")
		self.assertEqual(by_name["AT HR"]["levels"]["products"], "none")
		self.assertEqual(len(by_name["AT Store"]["people"]), by_name["AT Store"]["users"])

	def test_copy_from_built_in_role(self):
		frappe.set_user(self.owner)
		store = roles.get_role("AT Store")
		copy = roles.create_role(ROLE, "like store", copy_from="AT Store")
		self.assertEqual(copy["areas"], store["areas"])
		self.assertEqual(copy["flags"], store["flags"])
		self.assertFalse(copy["built_in"])

	def test_summary_is_plain_english_and_changes_are_logged(self):
		out = self._make({"products": ["view", "create"]}, cost=True)
		self.assertIn("Products and gift sets: see, add", out["summary"])
		self.assertIn("Can see cost, margin and stock value in Rs", out["summary"])
		comments = frappe.get_all(
			"Comment", filters={"reference_doctype": "Role", "reference_name": ROLE}, pluck="content"
		)
		self.assertTrue(any("Permissions changed" in c for c in comments))

	def test_save_role_works_over_http(self):
		"""The browser calls the API over HTTP, where Frappe reserves some argument names (for
		example "flags"). Direct Python calls would not notice, so go through the real handler."""
		import json

		from frappe.handler import execute_cmd

		self._make({"products": ["view"]})
		frappe.set_user(self.owner)
		frappe.local.form_dict = frappe._dict(
			cmd="adeel_traders.api.v1.roles.save_role",
			name=ROLE,
			areas=json.dumps({"products": ["view", "create"]}),
			switches=json.dumps({"cost": True, "export": False}),
			description="",
		)
		frappe.local.request = frappe._dict(method="POST")  # POST-only endpoint
		saved = execute_cmd("adeel_traders.api.v1.roles.save_role")
		self.assertEqual(saved["areas"]["products"], ["view", "create"])
		self.assertTrue(saved["flags"]["cost"])

	def test_saving_the_same_ticks_twice_is_safe(self):
		self._make({"products": ["view"], "stock": ["view"]})
		rows = frappe.db.count("Custom DocPerm", {"role": ROLE})
		frappe.set_user(self.owner)
		roles.save_role(ROLE, {"products": ["view"], "stock": ["view"]}, {})
		self.assertEqual(frappe.db.count("Custom DocPerm", {"role": ROLE}), rows)

	# --- the pages added in P1.5-P1.12 are ticks too (SRS 2.1) ---------------------------

	def test_owner_can_give_the_settings_pages_to_a_custom_role(self):
		self._make(
			{
				"products": ["view"],
				"stock": ["view"],
				"reports": ["view"],
				"units": ["edit"],
				"payment_methods": ["edit"],
				"email": ["edit"],
				"integrations": ["view", "edit"],
				"numbering": ["view"],
			}
		)
		frappe.set_user(self.owner)
		roles.save_role(ROLE, roles.get_role(ROLE)["areas"], {"history": True, "approve": True})
		self._cashier()
		caps = {k for k, v in permissions.capabilities().items() if v}
		self.assertTrue(
			{
				"reports.stock",
				"units.manage",
				"payments.manage",
				"email.manage",
				"integrations.view",
				"numbering.view",
				"audit.view",
				"approvals.inbox",
			}
			<= caps
		)
		# and the server really lets that person use them
		self.assertTrue(masters.list_masters("units")["can_edit"])
		self.assertIn("columns", reports.run_report("stock-balance"))
		self.assertIn("events", history.audit_log(page_length=1))
		self.assertIn("items", approvals.my_pending())
		# the switch is not raw access to Frappe's change records
		self.assertFalse(frappe.has_permission("Version", "read"))

	def test_taking_the_ticks_away_closes_the_pages_again(self):
		self._make({"products": ["view"], "stock": ["view"], "reports": ["view"], "units": ["edit"]})
		frappe.set_user(self.owner)
		roles.save_role(ROLE, {"products": ["view"]}, {"history": False, "approve": False})
		self._cashier()
		caps = permissions.capabilities()
		for name in ("reports.stock", "units.manage", "audit.view", "approvals.inbox"):
			self.assertFalse(caps[name], name)
		with self.assertRaises(frappe.PermissionError):
			reports.run_report("stock-balance")
		with self.assertRaises(frappe.PermissionError):
			history.audit_log()
		with self.assertRaises(frappe.PermissionError):
			masters.add_master("units", "_Test Nope")

	def test_deleting_the_role_removes_it_from_the_reports_list(self):
		self._make({"products": ["view"], "stock": ["view"], "reports": ["view"]})

		def on_list():
			return frappe.get_all(
				"Has Role", filters={"parenttype": "Custom Role", "role": ROLE}, pluck="parent"
			)

		self.assertTrue(on_list())
		frappe.set_user(self.owner)
		roles.delete_role(ROLE)
		self.assertFalse(on_list())

	def test_built_in_roles_show_what_they_have_on_the_new_areas(self):
		frappe.set_user(self.owner)
		levels = {r["name"]: r["levels"] for r in roles.list_roles()["roles"] if r["built_in"]}
		self.assertEqual(levels["AT Manager"]["units"], "manage")
		self.assertEqual(levels["AT Accountant"]["payment_methods"], "manage")
		self.assertEqual(levels["AT Counter Staff"]["reports"], "none")
		self.assertEqual(levels["AT Owner"]["integrations"], "manage")
