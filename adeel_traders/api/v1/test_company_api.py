"""P1.4: the Company page API (details, logo, branches). Everything it makes is removed again,
because renaming and saving can commit (no test may rely on a rollback)."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders import permissions
from adeel_traders.api.v1 import company, people, printing

OWNER = "_test_at_company_owner@example.com"
CLERK = "_test_at_company_clerk@example.com"
BRANCH = "_Test Branch Gulberg"
RENAMED = "_Test Branch Johar Town"


def _user(email: str, profile: str) -> str:
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
	return email


def _drop_branch(name: str) -> None:
	abbr = frappe.get_cached_value("Company", company._company(), "abbr")
	frappe.db.delete("User Permission", {"allow": "Branch", "for_value": name})
	for address in frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Branch", "link_name": name, "parenttype": "Address"},
		pluck="parent",
	):
		frappe.delete_doc("Address", address, force=1, ignore_permissions=True)
	for doctype, docname in (
		("Warehouse", f"{name} - {abbr}"),
		("Cost Center", f"{name} - {abbr}"),
		("Branch", name),
	):
		if frappe.db.exists(doctype, docname):
			frappe.delete_doc(doctype, docname, force=1, ignore_permissions=True)


class TestCompanyAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.owner = _user(OWNER, "AT Owner")
		cls.clerk = _user(CLERK, "AT Counter Staff")
		cls.saved = frappe.db.get_value(
			"Company",
			company._company(),
			["tax_id", "phone_no", "email", "website", "company_logo"],
			as_dict=True,
		)
		cls.real_address = company._address_of("Company", company._company())  # the real one is put back
		cls.app_logo = frappe.db.get_single_value("Website Settings", "app_logo")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in (BRANCH, RENAMED):
			_drop_branch(name)
		frappe.db.set_value("Company", company._company(), dict(cls.saved))
		cls._restore_address()
		printing.refresh_letterhead()  # restoring with db.set_value runs no hooks: bring the header back
		frappe.db.set_single_value("Website Settings", "app_logo", cls.app_logo)
		for email in (OWNER, CLERK):
			if frappe.db.exists("User", email):
				frappe.db.delete("User Permission", {"user": email})
				frappe.delete_doc("User", email, force=1, ignore_permissions=True)
		frappe.db.delete(
			"Deleted Document", {"data": ["like", '%"\\_test%']}
		)  # test leftovers stay out of the change history
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _restore_address(cls):
		name, real = company._company(), cls.real_address
		for parent in frappe.get_all(
			"Dynamic Link",
			filters={"link_doctype": "Company", "link_name": name, "parenttype": "Address"},
			pluck="parent",
		):
			if parent != real["_name"]:
				frappe.delete_doc("Address", parent, force=1, ignore_permissions=True)
		if real["_name"]:
			doc = frappe.get_doc("Address", real["_name"])
			doc.update({k: real[k] for k in ("address_line1", "city", "phone")})
			doc.update({"disabled": 0, "is_your_company_address": 1})
			if not any(l.link_doctype == "Company" and l.link_name == name for l in doc.links):
				doc.append("links", {"link_doctype": "Company", "link_name": name})
			doc.save(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		for name in (BRANCH, RENAMED):
			_drop_branch(name)
		frappe.set_user(self.owner)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_owner_and_accountant_may_manage_others_not(self):
		self.assertTrue(permissions.capabilities()["company.manage"])
		frappe.set_user(self.clerk)
		self.assertFalse(permissions.capabilities()["company.manage"])
		for call in (
			company.get_company,
			lambda: company.update_company(tax_id="1"),
			lambda: company.set_company_logo(None),
			lambda: company.add_branch(BRANCH),
			lambda: company.update_branch("Shop Counter", new_name=RENAMED),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_details_and_one_address(self):
		out = company.update_company(
			tax_id="1234567-8",
			phone_no="042-111-222",
			email="info@adeeltraders.example",
			website="adeeltraders.example",
			address_line1="12 Mall Road",
			city="Lahore",
		)
		self.assertEqual(out["details"]["tax_id"], "1234567-8")
		self.assertEqual(out["address"]["city"], "Lahore")
		out = company.update_company(tax_id="1234567-8", address_line1="14 Mall Road", city="Lahore")
		self.assertEqual(out["address"]["address_line1"], "14 Mall Road")
		links = frappe.get_all(
			"Dynamic Link",
			filters={"link_doctype": "Company", "link_name": company._company(), "parenttype": "Address"},
		)
		self.assertEqual(len(links), 1)  # updated, not duplicated

	def test_phone_needs_no_address_and_empty_address_is_removed(self):
		company.update_company(phone_no="042-111-222", address_line1="12 Mall Road", city="Lahore")
		out = company.update_company(phone_no="042-333-444")  # address emptied, phone kept
		self.assertEqual(out["details"]["phone_no"], "042-333-444")
		self.assertEqual(out["address"]["address_line1"], "")
		with self.assertRaises(frappe.ValidationError):
			company.add_branch(BRANCH, phone="042-1")  # a branch phone lives on its address

	def test_rename_company_and_back(self):
		old = company._company()
		new = "_Test Adeel Traders Renamed"
		try:
			out = company.update_company(company_name=new)
			self.assertEqual(out["details"]["name"], new)
			self.assertEqual(company._company(), new)  # the default company followed
			self.assertTrue(frappe.db.exists("Warehouse", {"company": new}))  # records followed
			with self.assertRaises(frappe.ValidationError):
				company.update_company(company_name="x")
		finally:
			frappe.set_user("Administrator")
			if frappe.db.exists("Company", new):
				frappe.rename_doc("Company", new, old, force=True)
				frappe.clear_cache()
			frappe.db.commit()
		self.assertEqual(company._company(), old)

	def test_bad_details_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			company.update_company(email="not-an-email")
		with self.assertRaises(frappe.ValidationError):
			company.update_company(address_line1="Only a street")  # city missing

	def test_add_branch_sets_up_stock_and_accounts(self):
		branch = company.add_branch(BRANCH, address_line1="5 Main Boulevard", city="Lahore", phone="042-1")
		abbr = frappe.get_cached_value("Company", company._company(), "abbr")
		self.assertEqual(branch["warehouses"], [f"{BRANCH} - {abbr}"])
		self.assertEqual(branch["cost_center"], f"{BRANCH} - {abbr}")
		self.assertEqual(frappe.db.get_value("Warehouse", f"{BRANCH} - {abbr}", "branch"), BRANCH)
		self.assertEqual(branch["address"]["city"], "Lahore")
		self.assertIn(BRANCH, people.list_people()["branches"])  # people can be given the new branch
		with self.assertRaises(frappe.ValidationError):
			company.add_branch(BRANCH.lower())  # same name, other case
		with self.assertRaises(frappe.ValidationError):
			company.add_branch("x")

	def test_rename_branch_keeps_every_link(self):
		company.add_branch(BRANCH)
		frappe.set_user("Administrator")
		frappe.get_doc(
			{"doctype": "User Permission", "user": self.clerk, "allow": "Branch", "for_value": BRANCH}
		).insert()
		frappe.set_user(self.owner)
		branch = company.update_branch(BRANCH, new_name=RENAMED)
		self.assertEqual(branch["name"], RENAMED)
		self.assertEqual(branch["people"], 1)  # the person's branch limit followed the rename
		self.assertEqual(permissions.user_branches(self.clerk), {RENAMED})
		abbr = frappe.get_cached_value("Company", company._company(), "abbr")
		self.assertEqual(frappe.db.get_value("Warehouse", f"{BRANCH} - {abbr}", "branch"), RENAMED)
		self.assertFalse(frappe.db.exists("Branch", BRANCH))

	def test_logo_must_be_uploaded_to_the_company(self):
		with self.assertRaises(frappe.ValidationError):
			company.set_company_logo("/files/not-ours.png")
		frappe.set_user("Administrator")
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "_test_logo.png",
				"content": b"\x89PNG\r\n\x1a\n" + b"0" * 16,
				"attached_to_doctype": "Company",
				"attached_to_name": company._company(),
				"is_private": 0,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("File", file.name, force=1, ignore_permissions=True))
		frappe.set_user(self.owner)
		self.assertEqual(company.set_company_logo(file.file_url), {"logo": file.file_url})
		frappe.set_user("Administrator")
		text = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "_test_logo.txt",
				"content": b"not an image",
				"attached_to_doctype": "Company",
				"attached_to_name": company._company(),
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("File", text.name, force=1, ignore_permissions=True))
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			company.set_company_logo(text.file_url)  # checked on the server too, not only the browser
		self.assertEqual(company.get_company()["logo"], file.file_url)
		self.assertEqual(
			frappe.db.get_single_value("Website Settings", "app_logo"), file.file_url
		)  # email logo

	def test_page_shows_branches_and_shared_locations(self):
		out = company.get_company()
		names = {b["name"] for b in out["branches"]}
		self.assertTrue({"Main Warehouse", "Shop Counter"} <= names)
		shop = next(b for b in out["branches"] if b["name"] == "Shop Counter")
		self.assertTrue(shop["cost_center"])
		self.assertTrue(any(w.startswith("Damaged") for w in out["shared_locations"]))
