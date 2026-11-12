"""DEVELOPMENT-ONLY demo products for testing Phase 2 end to end.

Run:  bench --site adeel.localhost execute adeel_traders.setup.demo.create_demo_data
Refuses to run unless developer_mode is on, so it can never create fake data in production.
Names and prices come from the sample screens in the SRS (section 10); they are not real data.
Safe to run again: existing records are skipped.
"""

import frappe
from frappe.utils import nowdate

from adeel_traders.stock.barcode import generate_item_barcode

SUPPLIER = "Demo Supplier"
SHADES = [("05 Berry Kiss", "05"), ("12 Ruby Red", "12")]

# item_code: (Wholesale, Online, Self-delivery) price per Piece
PRICES = {
	"VML-05": (520, 750, 690),
	"VML-12": (520, 750, 690),
	"HFW-150": (365, 520, 480),
	"GST-003": (1150, 1799, 1650),
}
WHOLESALE_BOX_PRICE = 6000  # 1 Box = 12 Piece, sold to dealers by the box (SRS 1.2)


def create_demo_data():
	if not frappe.conf.developer_mode:
		frappe.throw("Demo data can only be created on a development site (developer_mode).")

	company = frappe.defaults.get_global_default("company")
	abbr = frappe.get_cached_value("Company", company, "abbr")
	main_wh = f"Main Warehouse - {abbr}"

	_add_shade_values()
	_lipstick(company, main_wh)
	_face_wash(company, main_wh)
	_gift_set()
	_prices()
	for item_code in ("VML-05", "VML-12", "HFW-150"):
		if not frappe.get_doc("Item", item_code).barcodes:
			generate_item_barcode(item_code)
	_supplier()
	_opening_stock(company, abbr)
	_reorder_level("VML-12", f"Main Warehouse - {abbr}", level=20, qty=60)
	_demo_users()
	frappe.db.commit()
	print("Demo data ready: VML-05, VML-12 (lipstick variants), HFW-150 (batch+expiry), GST-003 (bundle)")


DEMO_USERS = [
	# (email, full name, role profile, branch or None). Realistic names, so a person is never
	# confused with the role they have ("Owner" the role vs a user called "Owner"). Dev-only logins, one per role, to check
	# what each role can see. Counter and Store are limited to one branch (docs/PERMISSIONS.md).
	("owner@adeel.test", "Adeel Ahmed", "AT Owner", None),
	("manager@adeel.test", "Bilal Khan", "AT Manager", None),
	("counter@adeel.test", "Sana Malik", "AT Counter Staff", "Shop Counter"),
	("store@adeel.test", "Usman Ali", "AT Store", "Main Warehouse"),
	("accountant@adeel.test", "Ayesha Raza", "AT Accountant", None),
	("hr@adeel.test", "Hina Tariq", "AT HR", None),
	("online@adeel.test", "Fahad Iqbal", "AT Online Order Team", None),
]
DEMO_PASSWORD = "Adeel@12345"


def _demo_users():
	for email, full_name, role_profile, branch in DEMO_USERS:
		first_name, _, last_name = full_name.partition(" ")
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
			if user.full_name != full_name:
				user.first_name, user.last_name = first_name, last_name
				user.save(ignore_permissions=True)  # also updates full_name
		else:
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"last_name": last_name,
					"send_welcome_email": 0,
					"new_password": DEMO_PASSWORD,
					"role_profile_name": role_profile,
				}
			).insert()
		if branch and not frappe.db.exists("User Permission", {"user": email, "allow": "Branch"}):
			frappe.get_doc(
				{"doctype": "User Permission", "user": email, "allow": "Branch", "for_value": branch}
			).insert()


def _add_shade_values():
	attribute = frappe.get_doc("Item Attribute", "Shade")
	existing = {v.attribute_value for v in attribute.item_attribute_values}
	for value, abbr in SHADES:
		if value not in existing:
			attribute.append("item_attribute_values", {"attribute_value": value, "abbr": abbr})
	attribute.save()


def _lipstick(company, main_wh):
	from erpnext.controllers.item_variant import create_variant

	if not frappe.db.exists("Item", "VML"):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "VML",
				"item_name": "Velvet Matte Lipstick",
				"item_group": "Lips",
				"stock_uom": "Piece",
				"has_variants": 1,
				"attributes": [{"attribute": "Shade"}],
				"uoms": [{"uom": "Box", "conversion_factor": 12}],
				"item_defaults": [{"company": company, "default_warehouse": main_wh}],
				"description": "Rich colour in one swipe with a soft matte finish.",
			}
		).insert()

	for shade, abbr in SHADES:
		if not frappe.db.exists("Item", f"VML-{abbr}"):
			create_variant("VML", {"Shade": shade}).insert()


def _face_wash(company, main_wh):
	if frappe.db.exists("Item", "HFW-150"):
		return
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": "HFW-150",
			"item_name": "Hydra Face Wash 150 ml",
			"item_group": "Skin Care",
			"stock_uom": "Piece",
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "HFW-.#####",
			"has_expiry_date": 1,
			"shelf_life_in_days": 730,
			"uoms": [{"uom": "Box", "conversion_factor": 12}],
			"item_defaults": [{"company": company, "default_warehouse": main_wh}],
		}
	).insert()


def _gift_set():
	if not frappe.db.exists("Item", "GST-003"):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "GST-003",
				"item_name": "Glow Trio Gift Set",
				"item_group": "Gift Sets",
				"stock_uom": "Piece",
				"is_stock_item": 0,  # ERPNext rule: a bundle's parent must be a non-stock item
			}
		).insert()
	if not frappe.db.exists("Product Bundle", "GST-003"):
		frappe.get_doc(
			{
				"doctype": "Product Bundle",
				"new_item_code": "GST-003",
				"items": [{"item_code": "VML-05", "qty": 1}, {"item_code": "HFW-150", "qty": 1}],
			}
		).insert()


def _prices():
	for item_code, rates in PRICES.items():
		for price_list, rate in zip(("Wholesale", "Online", "Self-delivery"), rates, strict=True):
			_price(item_code, price_list, rate, "Piece")
	for item_code in ("VML-05", "VML-12"):
		_price(item_code, "Wholesale", WHOLESALE_BOX_PRICE, "Box")


def _price(item_code, price_list, rate, uom):
	if frappe.db.exists("Item Price", {"item_code": item_code, "price_list": price_list, "uom": uom}):
		return
	frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": price_list,
			"uom": uom,
			"price_list_rate": rate,
		}
	).insert()


def _reorder_level(item_code, warehouse, level, qty):
	"""Demo of a low-stock product: VML-12 has 14 pieces, reorder level 20."""
	item = frappe.get_doc("Item", item_code)
	if any(r.warehouse == warehouse for r in item.reorder_levels):
		return
	item.append(
		"reorder_levels",
		{
			"warehouse": warehouse,
			"warehouse_reorder_level": level,
			"warehouse_reorder_qty": qty,
			"material_request_type": "Purchase",
		},
	)
	item.save()


def _supplier():
	if frappe.db.exists("Supplier", SUPPLIER):
		return
	group = frappe.db.get_value("Supplier Group", {"is_group": 0})
	frappe.get_doc({"doctype": "Supplier", "supplier_name": SUPPLIER, "supplier_group": group}).insert()


def _opening_stock(company, abbr):
	if frappe.db.exists("Stock Reconciliation", {"purpose": "Opening Stock", "docstatus": 1}):
		return
	reco = frappe.get_doc(
		{
			"doctype": "Stock Reconciliation",
			"company": company,
			"purpose": "Opening Stock",
			"posting_date": nowdate(),
			"expense_account": f"Temporary Opening - {abbr}",
			"items": [
				{
					"item_code": "VML-05",
					"warehouse": f"Main Warehouse - {abbr}",
					"qty": 110,
					"valuation_rate": 410,
				},
				{
					"item_code": "VML-05",
					"warehouse": f"Shop Counter - {abbr}",
					"qty": 24,
					"valuation_rate": 410,
				},
				{
					"item_code": "VML-12",
					"warehouse": f"Main Warehouse - {abbr}",
					"qty": 14,
					"valuation_rate": 410,
				},
			],
		}
	)
	reco.insert()
	reco.submit()


# Test records made while building the screens. Not part of the demo data above.
DUMMY_ITEMS = ["TEST", "1"]


def remove_dummy_data():
	"""Remove the test items and test stock counts (DESIGN.md rule 10: no dummy data in a demo).

	Run:  bench --site adeel.localhost execute adeel_traders.setup.demo.remove_dummy_data
	Development sites only. A test count that was posted is cancelled first, which puts the
	counted stock back to what it was before the count."""
	if not frappe.conf.developer_mode:
		frappe.throw("Dummy data can only be removed on a development site (developer_mode).")

	for name in frappe.get_all("Stock Count", pluck="name"):
		doc = frappe.get_doc("Stock Count", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Stock Count", name, force=1)

	for code in DUMMY_ITEMS:
		if not frappe.db.exists("Item", code):
			continue
		if frappe.db.exists("Stock Ledger Entry", {"item_code": code}):
			print(f"{code} has stock history, left in place")
			continue
		frappe.db.delete("Item Price", {"item_code": code})
		frappe.delete_doc("Item", code, force=1)
	frappe.db.commit()
	print("Dummy items and test counts removed")


def return_demo_stock_to_main(item_code: str = "VML-12", from_wh: str = "Finished Goods", qty: float = 2):
	"""Move stock from a test warehouse back to Main Warehouse (dev sites only)."""
	if not frappe.conf.developer_mode:
		frappe.throw("Only on a development site (developer_mode).")
	company = frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name")[0]
	abbr = frappe.get_cached_value("Company", company, "abbr")
	entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Transfer",
			"company": company,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": f"{from_wh} - {abbr}",
					"t_warehouse": f"Main Warehouse - {abbr}",
				}
			],
		}
	)
	entry.insert()
	entry.submit()
	frappe.db.commit()
	print("Moved", qty, item_code, "back to Main Warehouse:", entry.name)
