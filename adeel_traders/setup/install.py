"""Install hooks for adeel_traders (registered in hooks.py)."""

from adeel_traders.setup.core_setup import run_setup


def after_install() -> None:
	"""Runs once when the app is installed on a site.

	On a brand-new site the company does not exist yet, so only company-independent
	masters are created here; the rest runs from after_setup_wizard.
	"""
	run_setup()


def after_setup_wizard(args: dict | None = None) -> None:
	"""Runs after the setup wizard (hook: setup_wizard_complete), once the company exists."""
	company = (args or {}).get("company_name")
	run_setup(company=company)
