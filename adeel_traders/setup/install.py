"""Install hooks for adeel_traders (registered in hooks.py)."""

import frappe

from adeel_traders.setup.core_setup import run_setup


def after_install() -> None:
	"""Runs once when the app is installed on a site.

	On a brand-new site ERPNext has not run its setup wizard yet: there is no company, and not
	even the root "All Item Groups" our categories hang from. So nothing is created here; the
	whole setup runs from after_setup_wizard. A site that already finished its wizard (an
	existing business installing the app) is set up right away.
	"""
	if not frappe.is_setup_complete():
		return
	run_setup()


def after_setup_wizard(args: dict | None = None) -> None:
	"""Runs after the setup wizard (hook: setup_wizard_complete), once the company exists."""
	company = (args or {}).get("company_name")
	run_setup(company=company)
