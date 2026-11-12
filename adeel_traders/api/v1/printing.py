"""Printing (Phase 1, P1.8): letterhead, the facts our print layouts need, and share links.

Frappe does the printing itself:
  - Print Format (Jinja) renders the page; ours live in adeel_traders/print_format and use the base
    layouts in templates/print (A4 with the letterhead, 80 mm receipts),
  - frappe.utils.print_format.download_pdf makes the PDF and checks the reader may print it
    (a Document Share Key lets a customer open one document's PDF without logging in),
  - Letter Head puts the business header on every A4 print, ERPNext's own documents included.
This module keeps the letterhead equal to the Company page, gives the layouts the company facts,
and makes share links (Frappe has the key, but no web method to create one).

Contract used by web/apps/staff/lib/api.ts:
	share_link(doctype, name, print_format) -> {url, expires_on}
	pdf_options(print_format) is mirrored in components/ui-kit/document-actions.tsx (receipt = no letterhead)
Jinja (hooks.jinja): company_info() -> {name, address, phone, email, website, tax_id, logo}
"""

import base64

import frappe
from frappe import _
from frappe.utils import add_days, escape_html, get_url, today

LETTER_HEAD = "Adeel Traders"
SHARE_DAYS = 7
# Letterheads the ERPNext setup wizard made as samples (roadmap issue I10): switched off.
SAMPLE_LETTER_HEADS = ("Company Letterhead", "Company Letterhead - Grey")


def _company() -> str:
	return (
		frappe.defaults.get_global_default("company") or frappe.get_all("Company", pluck="name", limit=1)[0]
	)


def _logo_data_uri(file_url: str | None) -> str | None:
	"""The logo inside the page itself, so the PDF engine never has to fetch it over the network."""
	if not file_url:
		return None
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return None
	file = frappe.get_doc("File", name)
	ext = (file.file_name or "").rsplit(".", 1)[-1].lower()
	mime = {"svg": "image/svg+xml", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(
		ext, "image/png"
	)
	return f"data:{mime};base64,{base64.b64encode(file.get_content()).decode()}"


def company_info() -> dict:
	"""Business facts for print layouts (Jinja) and the letterhead."""
	company = _company()
	doc = frappe.get_doc(
		"Company", company
	)  # not the cached copy: this runs while the Company is being saved
	address = frappe.db.sql(
		"""select a.address_line1, a.city from tabAddress a
		join `tabDynamic Link` l on l.parent = a.name and l.parenttype = 'Address'
		where l.link_doctype = 'Company' and l.link_name = %s and a.disabled = 0 limit 1""",
		company,
		as_dict=True,
	)
	return {
		"name": doc.company_name,
		"address": ", ".join(filter(None, [address[0].address_line1, address[0].city])) if address else "",
		"phone": doc.phone_no or "",
		"email": doc.email or "",
		"website": (doc.website or "").removeprefix("https://").removeprefix("http://"),
		"tax_id": doc.tax_id or "",
		"logo": _logo_data_uri(doc.company_logo),
	}


def _letterhead_html(info: dict) -> str:
	e = escape_html
	brand = (
		f'<img src="{info["logo"]}" style="max-height:56px;max-width:200px" alt="">'
		if info["logo"]
		else f'<div style="font-size:20px;font-weight:700">{e(info["name"])}</div>'
	)
	lines = [
		e(info["address"]),
		" · ".join(filter(None, [e(info["phone"]), e(info["email"]), e(info["website"])])),
		f"NTN {e(info['tax_id'])}" if info["tax_id"] else "",
	]
	contact = "<br>".join(line for line in lines if line)
	return f"""<table style="width:100%;border-collapse:collapse;border-bottom:2px solid #1f2937;margin-bottom:12px">
<tr><td style="vertical-align:bottom;padding-bottom:8px">{brand}{"" if not info["logo"] else f'<div style="font-size:12px;font-weight:600;margin-top:4px">{e(info["name"])}</div>'}</td>
<td style="vertical-align:bottom;text-align:right;font-size:11px;line-height:1.5;color:#374151;padding-bottom:8px">{contact}</td></tr></table>"""


def address_changed(doc, method=None) -> None:
	"""Hook: only the company's own address matters for the letterhead."""
	if doc.get("is_your_company_address") or any(link.link_doctype == "Company" for link in doc.links):
		refresh_letterhead()


def refresh_letterhead(doc=None, method=None) -> None:
	"""Keep the default letterhead equal to the Company record. Runs from hooks whenever the
	Company (name, phone, logo...) or its address is saved, so no way of changing them (the
	Company page, Desk, an import) can leave an old header on printed documents."""
	html = _letterhead_html(company_info())
	if frappe.db.exists("Letter Head", LETTER_HEAD):
		doc = frappe.get_doc("Letter Head", LETTER_HEAD)
	else:
		doc = frappe.new_doc("Letter Head")
		doc.letter_head_name = LETTER_HEAD
	doc.update({"source": "HTML", "content": html, "is_default": 1, "disabled": 0})
	doc.save(ignore_permissions=True)  # follows a Company page save, which checked company.manage
	for sample in SAMPLE_LETTER_HEADS:
		if frappe.db.get_value("Letter Head", sample, "disabled") == 0:
			frappe.db.set_value("Letter Head", sample, {"disabled": 1, "is_default": 0})


def pdf_options(print_format: str | None) -> dict:
	"""A4 prints carry our letterhead; receipts (80/58 mm) are printed without it. Documents made
	before the letterhead existed name an old sample letterhead, so ours is always named here."""
	paper = frappe.db.get_value("Print Format", print_format, "paper_size") if print_format else None
	if paper and paper.startswith("Receipt"):
		return {"no_letterhead": 1}
	return {"letterhead": LETTER_HEAD}


@frappe.whitelist(methods=["POST"])
def share_link(doctype: str, name: str, print_format: str | None = None) -> dict:
	"""A link to this document's PDF that works without logging in, for 7 days (WhatsApp, email)."""
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found").format(_(doctype), name), frappe.DoesNotExistError)
	doc = frappe.get_doc(doctype, name)
	if not (doc.has_permission("print") or doc.has_permission("read")):
		frappe.throw(_("You do not have permission to share this document."), frappe.PermissionError)
	expires_on = add_days(today(), SHARE_DAYS)
	key = doc.get_document_share_key(expires_on=expires_on)
	query = frappe.utils.urlencode(
		{
			"doctype": doctype,
			"name": name,
			"format": print_format or "",
			"key": key,
			**pdf_options(print_format),
		}
	)
	return {
		"url": get_url(f"/api/method/frappe.utils.print_format.download_pdf?{query}"),
		"expires_on": str(expires_on),
	}
