"""P1.6: approvals on Frappe's Workflow, tried with a temporary sample workflow on ToDo."""

import frappe
from frappe.tests import IntegrationTestCase

from adeel_traders.api.v1 import approvals

WORKFLOW = "_Test AT Approval"
USERS = {
	"_test_at_appr_owner@example.com": "AT Owner",  # approver (System Manager)
	"_test_at_appr_counter@example.com": "AT Counter Staff",  # asks
	"_test_at_appr_store@example.com": "AT Store",  # neither
}


def _cleanup():
	frappe.set_user("Administrator")
	frappe.db.delete("Workflow Action", {"reference_doctype": "ToDo"})
	for todo in frappe.get_all("ToDo", filters={"description": ["like", "_test approval%"]}, pluck="name"):
		frappe.db.delete("Comment", {"reference_doctype": "ToDo", "reference_name": todo})
		frappe.delete_doc("ToDo", todo, force=1, ignore_permissions=True)
	if frappe.db.exists("Workflow", WORKFLOW):
		frappe.delete_doc("Workflow", WORKFLOW, force=1, ignore_permissions=True)
	if frappe.db.exists("Custom Field", "ToDo-workflow_state"):  # the sample workflow added it
		frappe.delete_doc("Custom Field", "ToDo-workflow_state", force=1, ignore_permissions=True)
	for email in USERS:
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=1, ignore_permissions=True)
	frappe.db.delete("Deleted Document", {"data": ["like", '%"\\_test%']})
	frappe.clear_cache(doctype="ToDo")


class TestApprovalsAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		for email, profile in USERS.items():
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Test",
					"send_welcome_email": 0,
					"role_profiles": [{"role_profile": profile}],
				}
			).insert()
		cls.owner, cls.counter, cls.store = USERS
		frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": WORKFLOW,
				"document_type": "ToDo",
				"is_active": 1,
				"send_email_alert": 0,
				"workflow_state_field": "workflow_state",
				"states": [
					{"state": "Pending", "doc_status": "0", "allow_edit": "All"},
					{"state": "Approved", "doc_status": "0", "allow_edit": "System Manager"},
					{"state": "Rejected", "doc_status": "0", "allow_edit": "System Manager"},
				],
				"transitions": [
					{
						"state": "Pending",
						"action": "Approve",
						"next_state": "Approved",
						"allowed": "System Manager",
					},
					{
						"state": "Pending",
						"action": "Reject",
						"next_state": "Rejected",
						"allowed": "System Manager",
					},
				],
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()  # the workflow adds a column to ToDo (schema change)

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _ask(self, text="_test approval: discount 15%"):
		frappe.set_user(self.counter)
		todo = frappe.get_doc({"doctype": "ToDo", "description": text}).insert()
		frappe.set_user("Administrator")
		return todo.name

	def test_inbox_shows_only_to_the_approver(self):
		name = self._ask()
		frappe.set_user(self.owner)
		mine = approvals.my_pending()
		item = next(i for i in mine["items"] if i["name"] == name)
		self.assertEqual(item["state"], "Pending")
		self.assertEqual(set(item["actions"]), {"Approve", "Reject"})
		self.assertEqual(item["asked_by"], self.counter)
		frappe.set_user(self.store)
		self.assertNotIn(name, [i["name"] for i in approvals.my_pending()["items"]])

	def test_approve_moves_on_and_leaves_the_inbox(self):
		name = self._ask()
		frappe.set_user(self.owner)
		state = approvals.act("ToDo", name, "Approve")
		self.assertEqual(state["state"], "Approved")
		self.assertTrue(state["final"])
		self.assertNotIn(name, [i["name"] for i in approvals.my_pending()["items"]])

	def test_reject_needs_a_reason_which_is_kept(self):
		name = self._ask()
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.ValidationError):
			approvals.act("ToDo", name, "Reject")
		approvals.act("ToDo", name, "Reject", comment="Too much discount")
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": name, "content": ["like", "%Too much discount%"]})
		)

	def test_wrong_role_cannot_act(self):
		name = self._ask()
		frappe.set_user(self.counter)  # the person who asked may not approve their own request
		state = approvals.get_state("ToDo", name)
		self.assertEqual(state["actions"], [])
		self.assertEqual(state["waiting_for"], ["System Manager"])
		with self.assertRaises(frappe.PermissionError):
			approvals.act("ToDo", name, "Approve")

	def test_no_workflow_means_nothing_to_approve(self):
		frappe.set_user(self.owner)
		user = frappe.get_doc("User", self.owner)
		self.assertEqual(approvals.get_state("User", user.name)["workflow"], None)

	def test_approvers_are_told_in_the_bell(self):
		name = self._ask("_test approval: bell")
		logs = frappe.get_all(
			"Notification Log", filters={"document_type": "ToDo", "document_name": name}, pluck="for_user"
		)
		self.assertIn(self.owner, logs)  # may approve
		self.assertNotIn(self.counter, logs)  # asked
		self.assertNotIn(self.store, logs)  # may not approve
		frappe.db.delete("Notification Log", {"document_type": "ToDo", "document_name": name})
