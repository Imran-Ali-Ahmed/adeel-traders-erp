"""P1.8: letterhead from the Company page, our print layouts (blind count kept), share links."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import printing, stock_count

WH = "Shop Counter - AT"
MAIN = "Main Warehouse - AT"
COUNTER = "_test_at_print_counter@example.com"
HR = "_test_at_print_hr@example.com"


class TestPrinting(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_Test AT Print Item",
				"item_name": "Print Lipstick",
				"item_group": "Lips",
				"stock_uom": "Piece",
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": "Adeel Traders",
				"items": [{"item_code": cls.item.name, "qty": 10, "t_warehouse": WH, "basic_rate": 10}],
			}
		).insert().submit()
		cls.move = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Transfer",
				"company": "Adeel Traders",
				"items": [{"item_code": cls.item.name, "qty": 3, "s_warehouse": WH, "t_warehouse": MAIN}],
			}
		).insert()
		cls.move.submit()
		counter = frappe.get_doc(
			{"doctype": "User", "email": COUNTER, "first_name": "Counter", "send_welcome_email": 0}
		).insert()
		counter.add_roles("Stock Counter", "Stock User")
		frappe.get_doc(
			{
				"doctype": "User",
				"email": HR,
				"first_name": "Hr",
				"send_welcome_email": 0,
				"role_profiles": [{"role_profile": "AT HR"}],
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.form_dict.pop("key", None)

	def test_letterhead_follows_the_company(self):
		printing.refresh_letterhead()
		head = frappe.get_doc("Letter Head", printing.LETTER_HEAD)
		self.assertTrue(head.is_default)
		for sample in printing.SAMPLE_LETTER_HEADS:
			if frappe.db.exists("Letter Head", sample):
				self.assertEqual(frappe.db.get_value("Letter Head", sample, "disabled"), 1)
		self.assertEqual(printing.pdf_options("Stock Move Slip"), {"no_letterhead": 1})
		self.assertEqual(printing.pdf_options("Stock Count Sheet"), {"letterhead": printing.LETTER_HEAD})
		info = printing.company_info()
		self.assertIn(info["name"], head.content)
		if info["phone"]:
			self.assertIn(info["phone"], head.content)

	def test_move_slip_is_an_80mm_receipt(self):
		html = frappe.get_print("Stock Entry", self.move.name, "Stock Move Slip")
		self.assertIn("Stock moved", html)
		self.assertIn("Main Warehouse", html)
		self.assertIn("Received by", html)
		self.assertEqual(
			frappe.db.get_value("Print Format", "Stock Move Slip", "paper_size"), "Receipt 80 mm"
		)

	def _open_count(self) -> str:
		frappe.set_user("Administrator")
		count = stock_count.start_count(WH)
		stock_count.scan(count["name"], self.item.name, qty=2)
		return count["name"]

	def test_count_sheet_keeps_the_count_blind(self):
		frappe.set_user(COUNTER)
		count = stock_count.start_count(WH)
		stock_count.scan(count["name"], self.item.name, qty=7)
		self.assertFalse(stock_count.get_count(count["name"])["can"]["email"])  # counters do not email it
		blind = frappe.get_print("Stock Count", count["name"], "Stock Count Sheet")
		self.assertIn("Print Lipstick", blind)
		self.assertNotIn(">System<", blind)
		frappe.set_user("Administrator")
		full = frappe.get_print("Stock Count", count["name"], "Stock Count Sheet")
		self.assertIn(">System<", full)
		self.assertTrue(stock_count.get_count(count["name"])["can"]["email"])

	def test_share_link_opens_one_document_without_login(self):
		from frappe.utils.print_format import validate_print_permission

		link = printing.share_link("Stock Entry", self.move.name, "Stock Move Slip")
		from urllib.parse import parse_qs

		query = parse_qs(link["url"].split("?", 1)[1])
		key = query["key"][0]
		self.assertEqual(query["no_letterhead"], ["1"])  # an 80 mm receipt
		frappe.set_user("Guest")
		frappe.form_dict.key = key
		validate_print_permission(self.move)  # no error: the key opens it
		frappe.form_dict.key = "wrong"
		with self.assertRaises(frappe.PermissionError):
			validate_print_permission(self.move)

	def test_share_link_needs_read_permission(self):
		frappe.set_user(HR)
		with self.assertRaises(frappe.PermissionError):
			printing.share_link("Stock Entry", self.move.name)

	def test_letterhead_follows_any_save_of_the_company(self):
		company = frappe.get_doc("Company", printing._company())
		phone = company.phone_no
		self.addCleanup(
			lambda: (
				frappe.db.set_value("Company", company.name, "phone_no", phone),
				printing.refresh_letterhead(),
			)
		)
		company.phone_no = "042-9876543"
		company.save(ignore_permissions=True)  # not through our Company page: the hook still runs
		self.assertIn("042-9876543", frappe.db.get_value("Letter Head", printing.LETTER_HEAD, "content"))

	def test_a4_format_prints_the_business_header(self):
		printing.refresh_letterhead()
		html = frappe.get_print(
			"Stock Count", self._open_count(), "Stock Count Sheet", letterhead=printing.LETTER_HEAD
		)
		self.assertIn(printing.company_info()["name"], html)
		self.assertIn("Stock count", html)
