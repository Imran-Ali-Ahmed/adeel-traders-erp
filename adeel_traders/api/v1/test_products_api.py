import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import products

COUNTER_USER = "_test_at_counter@example.com"


def _make_user(email: str, roles: list[str]) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Test", "send_welcome_email": 0}
		).insert()
		user.add_roles(*roles)
	return email


class TestProductsAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT API Item",
				"item_name": "Test API Lipstick",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"valuation_rate": 400,
			}
		).insert()
		products.set_price(cls.item.name, "Wholesale", 500)
		products.set_price(cls.item.name, "Online", 800)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _row(self, **kwargs):
		result = products.list_products(search=self.item.name, **kwargs)
		return next(r for r in result["items"] if r["item_code"] == self.item.name)

	def test_margin_is_computed_on_server(self):
		row = self._row()
		prices = {p["price_list"]: p for p in row["prices"]}
		self.assertEqual(row["cost"], 400)
		self.assertEqual(prices["Wholesale"]["margin"], 20.0)  # (500-400)/500
		self.assertEqual(prices["Online"]["margin"], 50.0)
		self.assertIsNone(prices["Self-delivery"]["rate"])

	def test_counter_staff_never_sees_cost(self):
		frappe.set_user(_make_user(COUNTER_USER, ["Sales User", "Stock User"]))
		row = self._row()
		self.assertIsNone(row["cost"])
		self.assertTrue(all(p["margin"] is None for p in row["prices"]))
		self.assertFalse(products.get_product(self.item.name)["can"]["view_cost"])

	def test_set_price_rejects_bad_input(self):
		with self.assertRaises(frappe.ValidationError):
			products.set_price(self.item.name, "Not A List", 10)
		with self.assertRaises(frappe.ValidationError):
			products.set_price(self.item.name, "Online", -1)

	def test_set_price_updates_existing_price(self):
		products.set_price(self.item.name, "Online", 900)
		self.assertEqual(
			frappe.db.get_value(
				"Item Price",
				{"item_code": self.item.name, "price_list": "Online", "uom": "Piece"},
				"price_list_rate",
			),
			900,
		)
		self.assertEqual(
			frappe.db.count("Item Price", {"item_code": self.item.name, "price_list": "Online"}), 1
		)

	def test_adjust_requires_known_type(self):
		with self.assertRaises(frappe.ValidationError):
			products.adjust_stock(self.item.name, "Main Warehouse - AT", 1, "Stolen", "x")

	def test_bundle_sets_counted_per_warehouse(self):
		def item(code):
			return frappe.get_doc(
				{"doctype": "Item", "item_code": code, "item_group": "Lips", "stock_uom": "Piece"}
			).insert()

		def receive(code, warehouse, qty):
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"company": "Adeel Traders",
					"items": [{"item_code": code, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10}],
				}
			).insert()
			se.submit()

		part_a, part_b = item("_Test AT Part A"), item("_Test AT Part B")
		bundle = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Set",
				"item_group": "Gift Sets",
				"is_stock_item": 0,
				"stock_uom": "Piece",
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Product Bundle",
				"new_item_code": bundle.name,
				"items": [{"item_code": part_a.name, "qty": 2}, {"item_code": part_b.name, "qty": 1}],
			}
		).insert()

		receive(part_a.name, "Main Warehouse - AT", 7)  # 3 sets' worth of A in Main
		receive(part_b.name, "Main Warehouse - AT", 5)
		receive(part_b.name, "Shop Counter - AT", 10)  # B alone in Shop Counter makes no set

		row = next(
			r for r in products.list_products(search=bundle.name)["items"] if r["item_code"] == bundle.name
		)
		self.assertEqual(row["bundle_available"], 3)

		detail = products.get_product(bundle.name)
		self.assertFalse(detail["can"]["move_stock"])  # a gift set has no stock of its own
		self.assertEqual(detail["bundle"]["sets_available"], 3)
		self.assertEqual([p["item_code"] for p in detail["bundle"]["parts"]], [part_a.name, part_b.name])

		# cost of one set = 2 x part A (10) + 1 x part B (10); the list and the page agree
		self.assertEqual(detail["cost"], 30)
		self.assertEqual(row["cost"], 30)
		parts = {p["item_code"]: p for p in detail["bundle"]["parts"]}
		self.assertEqual(parts[part_a.name]["enough_for"], 3)  # 7 units, 2 per set
		self.assertEqual(parts[part_b.name]["enough_for"], 15)
		self.assertTrue(parts[part_a.name]["limiting"])
		self.assertFalse(parts[part_b.name]["limiting"])
		self.assertIsNone(detail["bundle"]["blocked_by"])

		# a set whose part has no stock at all explains why it is 0
		empty_part = item("_Test AT Part C")
		empty_set = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Empty Set",
				"item_group": "Gift Sets",
				"is_stock_item": 0,
				"stock_uom": "Piece",
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Product Bundle",
				"new_item_code": empty_set.name,
				"items": [{"item_code": empty_part.name, "qty": 1}],
			}
		).insert()
		blocked = products.get_product(empty_set.name)["bundle"]
		self.assertEqual(blocked["sets_available"], 0)
		self.assertEqual(blocked["blocked_by"]["type"], "part_out_of_stock")
		self.assertEqual(blocked["blocked_by"]["item_code"], empty_part.name)
		self.assertIsNone(products.get_product(empty_set.name)["cost"])  # part cost unknown

	def test_stock_status_tabs_are_classified_on_server(self):
		def stocked_item(code, qty, reorder_level=0):
			doc = frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_group": "Face",
					"stock_uom": "Piece",
					"reorder_levels": [
						{
							"warehouse": "Main Warehouse - AT",
							"warehouse_reorder_level": reorder_level,
							"warehouse_reorder_qty": 10,
							"material_request_type": "Purchase",
						}
					]
					if reorder_level
					else [],
				}
			).insert()
			if qty:
				se = frappe.get_doc(
					{
						"doctype": "Stock Entry",
						"stock_entry_type": "Material Receipt",
						"company": "Adeel Traders",
						"items": [
							{
								"item_code": code,
								"qty": qty,
								"t_warehouse": "Main Warehouse - AT",
								"basic_rate": 5,
							}
						],
					}
				).insert()
				se.submit()
			return doc.name

		low = stocked_item("_Test AT Status Low", qty=3, reorder_level=5)
		ok = stocked_item("_Test AT Status OK", qty=50, reorder_level=5)
		out = stocked_item("_Test AT Status Out", qty=0)

		result = products.list_products(search="_Test AT Status")
		status = {r["item_code"]: r["status"] for r in result["items"]}
		self.assertEqual(status, {low: "low_stock", ok: "in_stock", out: "out_of_stock"})
		self.assertEqual(
			{k: result["counts"][k] for k in ("all", "in_stock", "low_stock", "out_of_stock")},
			{"all": 3, "in_stock": 1, "low_stock": 1, "out_of_stock": 1},
		)

		# only items WITH a reorder level count as needing restocking; "Out" has none
		restock = result["restock"]
		self.assertEqual(restock["count"], 1)
		self.assertEqual(
			restock["items"],
			[{"item_code": low, "item_name": low, "on_hand": 3, "reorder_level": 5}],
		)
		self.assertEqual(restock["out_without_level"], 1)
		self.assertEqual(result["counts"]["restock"], 1)
		# the filter returns exactly the items the card counts
		only_restock = products.list_products(search="_Test AT Status", status="restock")
		self.assertEqual([r["item_code"] for r in only_restock["items"]], [low])

		only_low = products.list_products(search="_Test AT Status", status="low_stock")
		self.assertEqual([r["item_code"] for r in only_low["items"]], [low])
		self.assertEqual(only_low["total"], 1)

		with self.assertRaises(frappe.ValidationError):
			products.list_products(status="nonsense")

	def test_transfer_validates_input(self):
		with self.assertRaises(frappe.ValidationError):
			products.transfer_stock(self.item.name, "Main Warehouse - AT", "Main Warehouse - AT", 1)
		with self.assertRaises(frappe.ValidationError):
			products.transfer_stock(self.item.name, "Main Warehouse - AT", "Shop Counter - AT", 0)

	def test_damaged_and_tester_stock_is_not_for_sale(self):
		def receive(code, warehouse, qty):
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"company": "Adeel Traders",
					"items": [{"item_code": code, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10}],
				}
			).insert()
			se.submit()

		code = (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "_Test AT Not For Sale",
					"item_group": "Lips",
					"stock_uom": "Piece",
				}
			)
			.insert()
			.name
		)
		receive(code, "Main Warehouse - AT", 10)
		receive(code, "Damaged - AT", 2)
		receive(code, "Testers - AT", 1)

		listing = products.list_products(search=code)
		row = next(r for r in listing["items"] if r["item_code"] == code)
		self.assertEqual(listing["summary"]["not_for_sale_units"], 3)
		self.assertEqual(listing["summary"]["not_for_sale_value"], 30)
		self.assertEqual(row["stock_qty"], 10)  # only sellable stock
		self.assertEqual(row["not_for_sale_qty"], 3)

		detail = products.get_product(code)
		self.assertEqual(detail["stock_value"], 100)  # 10 sellable x 10; damaged and testers excluded
		self.assertEqual(detail["physical_qty"], 13)  # 10 + 2 damaged + 1 tester
		self.assertEqual(detail["not_for_sale_qty"], 3)
		self.assertEqual(detail["not_for_sale_value"], 30)
		for_sale = {w["warehouse"]: w["for_sale"] for w in detail["warehouses"]}
		self.assertEqual(
			for_sale, {"Main Warehouse - AT": True, "Damaged - AT": False, "Testers - AT": False}
		)

		self.assertEqual(
			detail["status"], "in_stock"
		)  # status shown in the panel header comes from the server

		# an item with stock ONLY in Damaged is out of stock, not in stock
		only_damaged = (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "_Test AT Only Damaged",
					"item_group": "Lips",
					"stock_uom": "Piece",
				}
			)
			.insert()
			.name
		)
		receive(only_damaged, "Damaged - AT", 4)
		row = next(
			r for r in products.list_products(search=only_damaged)["items"] if r["item_code"] == only_damaged
		)
		self.assertEqual(row["status"], "out_of_stock")

	def test_update_product_edits_basics_and_reorder_level(self):
		products.update_product(
			self.item.name, "Test API Lipstick Renamed", "Eyes", reorder_level=15, reorder_qty=40
		)
		item = frappe.get_doc("Item", self.item.name)
		self.assertEqual((item.item_name, item.item_group), ("Test API Lipstick Renamed", "Eyes"))
		self.assertEqual(products.get_product(self.item.name)["reorder_main"], {"level": 15, "qty": 40})

		products.update_product(self.item.name, "Test API Lipstick", "Lips", reorder_level=0, disabled=True)
		item.reload()
		self.assertEqual(len(item.reorder_levels), 0)  # level 0 removes the row
		self.assertEqual(item.disabled, 1)
		with self.assertRaises(frappe.ValidationError):
			products.update_product(self.item.name, "  ", "Lips")
		products.update_product(self.item.name, "Test API Lipstick", "Lips", disabled=False)

	def test_counter_staff_cannot_edit_products(self):
		frappe.set_user(_make_user(COUNTER_USER, ["Sales User", "Stock User"]))
		detail = products.get_product(self.item.name)
		self.assertFalse(detail["can"]["edit_product"])
		with self.assertRaises(frappe.PermissionError):
			products.update_product(self.item.name, "Hacked", "Lips")
		with self.assertRaises(frappe.PermissionError):
			products.add_unit(self.item.name, "Box", 12)

	def test_units_and_unit_prices(self):
		products.add_unit(self.item.name, "Box", 12)
		with self.assertRaises(frappe.ValidationError):
			products.add_unit(self.item.name, "Box", 6)  # already there
		with self.assertRaises(frappe.ValidationError):
			products.add_unit(self.item.name, "Piece", 1)  # the stock unit itself
		with self.assertRaises(frappe.ValidationError):
			products.add_unit(self.item.name, "Barrel", 10)  # not one of our units
		with self.assertRaises(frappe.ValidationError):
			products.add_unit(self.item.name, "Carton", 0)

		products.set_price(self.item.name, "Wholesale", 5500, uom="Box")
		boxes = [u for u in products.get_product(self.item.name)["unit_prices"] if u["uom"] == "Box"]
		self.assertEqual(boxes[0]["rate"], 5500)
		self.assertEqual(boxes[0]["discount"], round((500 * 12 - 5500) / (500 * 12) * 100, 1))  # 8.3%
		self.assertEqual(boxes[0]["margin"], round((5500 / 12 - 400) / (5500 / 12) * 100, 1))
		self.assertAlmostEqual(boxes[0]["per_piece"], 5500 / 12, places=2)
		with self.assertRaises(frappe.ValidationError):
			products.set_price(self.item.name, "Wholesale", 100, uom="Carton")  # unit not added

	def test_add_variant_with_a_new_shade_uses_erpnext_variants(self):
		from erpnext.controllers.item_variant import create_variant

		attribute = frappe.get_doc("Item Attribute", "Shade")
		attribute.append("item_attribute_values", {"attribute_value": "T1 Test Rose", "abbr": "T1"})
		attribute.save()
		template = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_TSH",
				"item_name": "Test Shade Product",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"has_variants": 1,
				"variant_based_on": "Item Attribute",
				"attributes": [{"attribute": "Shade"}],
			}
		).insert()
		first = create_variant(template.name, {"Shade": "T1 Test Rose"})
		first.insert()
		self.assertEqual(first.variant_of, template.name)

		created = products.add_variant(first.name, {"Shade": {"value": "T2 Test Plum", "abbr": "T2"}})
		variant = frappe.get_doc("Item", created["item_code"])
		self.assertEqual(variant.variant_of, template.name)
		self.assertEqual([a.attribute_value for a in variant.attributes], ["T2 Test Plum"])
		self.assertTrue(frappe.db.exists("Item Attribute Value", {"parent": "Shade", "abbr": "T2"}))

		with self.assertRaises(frappe.ValidationError):
			products.add_variant(
				first.name, {"Shade": {"value": "T2 Test Plum", "abbr": "T2"}}
			)  # same shade twice
		with self.assertRaises(frappe.ValidationError):
			products.add_variant(
				first.name, {"Shade": {"value": "T3 Other", "abbr": "T2"}}
			)  # code already used
		with self.assertRaises(frappe.ValidationError):
			products.add_variant(template.name, {"Shade": {"value": "T4 Foo", "abbr": "T4"}})  # not a variant
		self.assertTrue(products.get_product(first.name)["can"]["add_shade"])

	def test_create_product_with_scanned_or_generated_barcode(self):
		scanned = products.create_product("_Test AT Scanned", "Test Scanned", "Lips", barcode="5901234123457")
		self.assertEqual(
			frappe.db.get_value("Item Barcode", {"parent": scanned["item_code"]}, "barcode"), "5901234123457"
		)
		with self.assertRaises(frappe.ValidationError):  # the same barcode on a second product
			products.create_product("_Test AT Scanned 2", "Test Scanned 2", "Lips", barcode="5901234123457")
		with self.assertRaises(frappe.ValidationError):  # wrong EAN-13 check digit
			products.create_product("_Test AT Bad EAN", "Test Bad EAN", "Lips", barcode="5901234123458")

		generated = products.create_product("_Test AT No Barcode", "Test No Barcode", "Lips")
		code = frappe.db.get_value("Item Barcode", {"parent": generated["item_code"]}, "barcode")
		self.assertTrue(code and code.startswith("20") and len(code) == 13)

	def test_brand_description_and_photo(self):
		if not frappe.db.exists("Brand", "_Test AT Brand"):
			frappe.get_doc({"doctype": "Brand", "brand": "_Test AT Brand"}).insert()
		products.update_product(
			self.item.name,
			"Test API Lipstick",
			"Lips",
			brand="_Test AT Brand",
			description="Soft matte finish",
		)
		detail = products.get_product(self.item.name)
		self.assertEqual((detail["brand"], detail["description"]), ("_Test AT Brand", "Soft matte finish"))

		by_brand = products.list_products(brand="_Test AT Brand")
		self.assertEqual([r["item_code"] for r in by_brand["items"]], [self.item.name])
		searched = products.list_products(search="_Test AT Brand")
		self.assertIn(self.item.name, [r["item_code"] for r in searched["items"]])
		self.assertIn("_Test AT Brand", products.form_options()["brands"])

		with self.assertRaises(frappe.ValidationError):  # a file not attached to this product
			products.set_product_image(self.item.name, "/files/not-mine.png")
		photo = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "lipstick.png",
				"content": b"fake-png",
				"attached_to_doctype": "Item",
				"attached_to_name": self.item.name,
				"is_private": 0,
			}
		).insert()
		self.assertEqual(products.set_product_image(self.item.name, photo.file_url)["image"], photo.file_url)
		frappe.set_user(_make_user(COUNTER_USER, ["Sales User", "Stock User"]))
		with self.assertRaises(frappe.PermissionError):
			products.set_product_image(self.item.name, None)

	def test_add_variant_with_two_attributes(self):
		from erpnext.controllers.item_variant import create_variant

		for attribute, value, abbr in (("Shade", "V1 Test Nude", "V1"), ("Size", "V Small", "VS")):
			doc = frappe.get_doc("Item Attribute", attribute)
			if value not in {v.attribute_value for v in doc.item_attribute_values}:
				doc.append("item_attribute_values", {"attribute_value": value, "abbr": abbr})
				doc.save()
		template = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_TV2",
				"item_name": "Test Two Attribute Product",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"has_variants": 1,
				"variant_based_on": "Item Attribute",
				"attributes": [{"attribute": "Shade"}, {"attribute": "Size"}],
			}
		).insert()
		first = create_variant(template.name, {"Shade": "V1 Test Nude", "Size": "V Small"})
		first.insert()

		needed = products.variant_attributes(first.name)
		self.assertEqual([a["attribute"] for a in needed], ["Shade", "Size"])

		made = products.add_variant(
			first.name, {"Shade": {"value": "V1 Test Nude"}, "Size": {"value": "V Large", "abbr": "VL"}}
		)
		variant = frappe.get_doc("Item", made["item_code"])
		self.assertEqual(
			{a.attribute: a.attribute_value for a in variant.attributes},
			{"Shade": "V1 Test Nude", "Size": "V Large"},
		)
		with self.assertRaises(frappe.ValidationError):  # same combination again
			products.add_variant(
				first.name, {"Shade": {"value": "V1 Test Nude"}, "Size": {"value": "V Large"}}
			)
		with self.assertRaises(frappe.ValidationError):  # new value without a code
			products.add_variant(first.name, {"Shade": {"value": "V9 New"}, "Size": {"value": "V Small"}})

	def _stock_item(self, code, qty=0, warehouse="Main Warehouse - AT", rate=10):
		item = frappe.get_doc(
			{"doctype": "Item", "item_code": code, "item_group": "Lips", "stock_uom": "Piece"}
		).insert()
		if qty:
			frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"company": "Adeel Traders",
					"items": [{"item_code": code, "qty": qty, "t_warehouse": warehouse, "basic_rate": rate}],
				}
			).insert().submit()
		return item.name

	def test_create_and_edit_gift_set(self):
		a = self._stock_item("_Test AT GS Part A", qty=10)
		b = self._stock_item("_Test AT GS Part B", qty=4)
		made = products.create_gift_set(
			"_Test AT GS",
			"Test Gift Set",
			[{"item_code": a, "qty": 1}, {"item_code": b, "qty": 2}],
			prices={"Online": 999},
		)
		item = frappe.get_doc("Item", made["item_code"])
		self.assertEqual(item.is_stock_item, 0)
		self.assertTrue(item.barcodes)  # generated
		detail = products.get_product(item.name)
		self.assertEqual(detail["bundle"]["sets_available"], 2)  # 4 of B, 2 per set

		products.update_gift_set_parts(item.name, [{"item_code": a, "qty": 1}, {"item_code": b, "qty": 1}])
		self.assertEqual(products.get_product(item.name)["bundle"]["sets_available"], 4)

		with self.assertRaises(frappe.ValidationError):  # a gift set cannot be a part
			products.update_gift_set_parts(
				item.name, [{"item_code": a, "qty": 1}, {"item_code": item.name, "qty": 1}]
			)
		with self.assertRaises(frappe.ValidationError):  # needs two parts
			products.update_gift_set_parts(item.name, [{"item_code": a, "qty": 1}])
		with self.assertRaises(frappe.ValidationError):  # zero quantity
			products.update_gift_set_parts(
				item.name, [{"item_code": a, "qty": 0}, {"item_code": b, "qty": 1}]
			)

	def test_stock_history_after_receipt_and_move(self):
		code = self._stock_item("_Test AT History", qty=10)
		products.transfer_stock(code, "Main Warehouse - AT", "Shop Counter - AT", 3)
		rows = products.get_stock_history(code)["rows"]
		self.assertEqual(rows[-1]["what"], "Received")
		moved = [r for r in rows if r["what"] == "Moved"]
		self.assertEqual(sorted(r["qty"] for r in moved), [-3, 3])
		main = next(r for r in moved if r["location"] == "Main Warehouse - AT")
		self.assertEqual(main["balance_here"], 7)

		frappe.set_user(_make_user(COUNTER_USER, ["Sales User", "Stock User"]))
		self.assertTrue(all(r["value"] is None for r in products.get_stock_history(code)["rows"]))

	def test_value_by_location_matches_total(self):
		code = self._stock_item("_Test AT Loc Value", qty=5, rate=20)
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": "Adeel Traders",
				"items": [
					{"item_code": code, "qty": 2, "t_warehouse": "Shop Counter - AT", "basic_rate": 20}
				],
			}
		).insert().submit()
		summary = products.list_products(search=code)["summary"]
		by = {r["warehouse"]: r for r in summary["by_location"]}
		self.assertEqual((by["Main Warehouse - AT"]["value"], by["Shop Counter - AT"]["value"]), (100, 40))
		self.assertEqual(
			sum(r["value"] for r in summary["by_location"] if r["for_sale"]), summary["stock_value"]
		)
		detail_values = {w["warehouse"]: w["value"] for w in products.get_product(code)["warehouses"]}
		self.assertEqual(detail_values["Shop Counter - AT"], 40)
