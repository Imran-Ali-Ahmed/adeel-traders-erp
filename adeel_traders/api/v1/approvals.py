"""Approvals (Phase 1, P1.6): one inbox and one approve/reject call for every document that
needs a yes from someone (discounts, credit, purchase orders, expenses, salaries in later phases).

Frappe does the work:
  - Workflow decides the states, who may move a document on, and self-approval,
  - Workflow Action keeps an "Open" task per waiting document with the roles that may act
    (Frappe's own permission query shows each user only the tasks of their roles),
  - frappe.model.workflow.get_transitions / apply_workflow check and apply the move,
  - the move and our comment land in the document's change history (P1.5).
This module adds our rules: a reason is required to reject or send back, the inbox shows the
amount and who asked, and the document must be readable by the approver.

Contract used by web/apps/staff/lib/api.ts:
	my_pending()                            -> {items[{doctype, name, title, state, amount, asked_by, asked_by_name, since, actions[]}], count}
	get_state(doctype, name)                -> {workflow, state, actions[], waiting_for[], final}
	act(doctype, name, action, comment)     -> get_state(...)

notify_approvers is a hook (Workflow Action after_insert): the people who may decide get a bell
notification (and an email when sending is set up) through Frappe's own notification log.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name
from frappe.utils import cstr, flt

# Actions that turn a request down; the person asking deserves a reason.
NEEDS_REASON = ("reject", "send back", "return", "decline")
AMOUNT_FIELDS = ("rounded_total", "grand_total", "total", "amount", "discount_amount")
INBOX_MAX = 200


def _amount(doc) -> float | None:
	for field in AMOUNT_FIELDS:
		if doc.meta.has_field(field) and doc.get(field) is not None:
			return flt(doc.get(field))
	return None


def _state_field(workflow: str) -> str:
	return frappe.get_cached_value("Workflow", workflow, "workflow_state_field") or "workflow_state"


def _readable(doctype: str, name: str):
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found").format(_(doctype), name), frappe.DoesNotExistError)
	doc = frappe.get_doc(doctype, name)
	if not doc.has_permission("read"):
		frappe.throw(_("You do not have permission to see this document."), frappe.PermissionError)
	return doc


def notify_approvers(task, method=None) -> None:
	"""A document started waiting: tell every enabled person whose role may decide (not the asker)."""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	roles = [r.role for r in task.permitted_roles]
	if not roles or not frappe.db.exists(task.reference_doctype, task.reference_name):
		return
	doc = frappe.get_doc(task.reference_doctype, task.reference_name)
	asker = doc.modified_by or doc.owner
	users = {
		u
		for u in frappe.get_all(
			"Has Role", filters={"role": ["in", roles], "parenttype": "User"}, pluck="parent", distinct=True
		)
		if u not in (asker, "Administrator", "Guest")
		and frappe.db.get_value("User", u, ["enabled", "user_type"]) == (1, "System User")
	}
	if not users:
		return
	who = frappe.db.get_value("User", asker, "full_name") or asker
	enqueue_create_notification(
		list(users),
		{
			"type": "Alert",
			"document_type": doc.doctype,
			"document_name": doc.name,
			"subject": _("{0} is waiting for your approval: {1}").format(
				who, cstr(doc.get_title()) or doc.name
			),
			"from_user": asker,
			"link": "/approvals",  # the staff app page where they decide
		},
	)


@frappe.whitelist(methods=["GET"])
def my_pending() -> dict:
	"""Documents waiting for the current user's decision, oldest first."""
	tasks = frappe.get_list(  # Frappe limits this to tasks of the user's roles
		"Workflow Action",
		filters={"status": "Open"},
		fields=["reference_doctype", "reference_name", "workflow_state", "creation"],
		order_by="creation asc",
		limit=INBOX_MAX,
	)
	items, seen = [], set()
	for t in tasks:
		key = (t.reference_doctype, t.reference_name)
		if key in seen or not frappe.db.exists(*key):
			continue
		seen.add(key)
		doc = frappe.get_doc(*key)
		if not doc.has_permission("read"):
			continue
		actions = [tr.action for tr in get_transitions(doc)]
		if not actions:  # e.g. your own request, when the workflow does not allow self-approval
			continue
		asked_by = doc.modified_by or doc.owner
		items.append(
			{
				"doctype": doc.doctype,
				"name": doc.name,
				"title": cstr(doc.get_title()) or doc.name,
				"state": t.workflow_state,
				"amount": _amount(doc),
				"asked_by": asked_by,
				"asked_by_name": frappe.db.get_value("User", asked_by, "full_name") or asked_by,
				"since": cstr(t.creation),
				"actions": actions,
			}
		)
	return {"items": items, "count": len(items)}


@frappe.whitelist(methods=["GET"])
def get_state(doctype: str, name: str) -> dict:
	"""Where a document is in its approval, and what the current user may do (for the banner)."""
	doc = _readable(doctype, name)
	workflow = get_workflow_name(doctype)
	if not workflow:
		return {"workflow": None, "state": None, "actions": [], "waiting_for": [], "final": True}
	state = doc.get(_state_field(workflow))
	waiting_for = sorted(
		set(
			frappe.get_all(
				"Workflow Transition", filters={"parent": workflow, "state": state}, pluck="allowed"
			)
		)
	)
	return {
		"workflow": workflow,
		"state": state,
		"actions": [t.action for t in get_transitions(doc)],
		"waiting_for": waiting_for,
		"final": not waiting_for,
	}


@frappe.whitelist(methods=["POST"])
def act(doctype: str, name: str, action: str, comment: str | None = None) -> dict:
	"""Approve, reject or send back. Frappe checks the action is allowed for this user now."""
	doc = _readable(doctype, name)
	comment = (comment or "").strip()
	if any(word in action.lower() for word in NEEDS_REASON) and not comment:
		frappe.throw(_("Say why, so the person who asked knows what to change."))
	allowed = {t.action for t in get_transitions(doc)}
	if action not in allowed:
		frappe.throw(_("You cannot {0} this now.").format(action.lower()), frappe.PermissionError)
	doc = apply_workflow(doc, action)
	if comment:
		doc.add_comment("Comment", f"{action}: {comment}")
	return get_state(doctype, name)
