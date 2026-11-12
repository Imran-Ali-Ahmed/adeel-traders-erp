app_name = "adeel_traders"
app_title = "Adeel Traders"
app_publisher = "Nesway"
app_description = "Business customisations for Adeel Traders on ERPNext v16"
app_email = "imran.ali@nesway.com"
app_license = "gpl-3.0"

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "adeel_traders",
# 		"logo": "/assets/adeel_traders/logo.png",
# 		"title": "Adeel Traders",
# 		"route": "/adeel_traders",
# 		"has_permission": "adeel_traders.api.permission.has_app_permission",
# 	}
# ]

# The dock, the rail down the left of the desk, is a document rather than a hook. Author it in
# Manage Dock on a developer-mode site and press Export to App, and it is written to
# `adeel_traders/dock/adeel_traders/adeel_traders.json` for git to carry. An app that ships none has no
# rail: its sidebar gets a switcher in the header instead.
#
# A companion app, one that extends a host app rather than standing on its own, says so with
# `mount_on` on that same record, and its entries are appended to the host's rail. Mounting keeps
# the companion off the apps screen, so it takes precedence over any add_to_apps_screen above.

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/adeel_traders/css/adeel_traders.css"
# app_include_js = "/assets/adeel_traders/js/adeel_traders.js"

# include js, css files in header of web template
# web_include_css = "/assets/adeel_traders/css/adeel_traders.css"
# web_include_js = "/assets/adeel_traders/js/adeel_traders.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "adeel_traders/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Item": "public/js/item.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_kanban_js = {"doctype" : "public/js/doctype_kanban.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "adeel_traders/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# P1.8: our print layouts ask for the company facts and our capabilities (blind count, cost)
jinja = {
	"methods": [
		"adeel_traders.api.v1.printing.company_info",
		"adeel_traders.permissions.can",
	],
}

# Installation
# ------------

# before_install = "adeel_traders.install.before_install"
after_install = "adeel_traders.setup.install.after_install"

# Runs after the setup wizard, once the company exists (same hook HRMS uses).
setup_wizard_complete = "adeel_traders.setup.install.after_setup_wizard"

# Records we own and ship with the app. Synced on install and on every `bench migrate`.
fixtures = [
	{
		"dt": "Role",
		"filters": [
			[
				"name",
				"in",
				[
					"Online Order Team",
					"Stock Counter",
					"Can View Cost",
					"Can Export Lists",
					"Can See Change History",
					"Can Decide Approvals",
				],
			]
		],
	},
	{"dt": "Role Profile", "filters": [["name", "like", "AT %"]]},
]

# Uninstallation
# ------------

# before_uninstall = "adeel_traders.uninstall.before_uninstall"
# after_uninstall = "adeel_traders.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "adeel_traders.utils.before_app_install"
# after_app_install = "adeel_traders.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "adeel_traders.utils.before_app_uninstall"
# after_app_uninstall = "adeel_traders.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "adeel_traders.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "adeel_traders.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "adeel_traders.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["adeel_traders.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	# P1.6: the people who may approve hear about it in the bell (and by email once sending is on)
	"Workflow Action": {"after_insert": "adeel_traders.api.v1.approvals.notify_approvers"},
	# P1.8: the printed header always matches the business details
	"Company": {"on_update": "adeel_traders.api.v1.printing.refresh_letterhead"},
	"Address": {"on_update": "adeel_traders.api.v1.printing.address_changed"},
	"Stock Entry": {
		"validate": [
			"adeel_traders.stock.stock_entry.validate_adjustment_reason",
			"adeel_traders.permissions.validate_stock_entry_branch",
		],
	},
}

# Staff (everyone except admins and the accountant) work in the staff app, not in Desk.
update_website_context = ["adeel_traders.permissions.keep_staff_out_of_desk"]

permission_query_conditions = {
	"Stock Count": "adeel_traders.permissions.stock_count_conditions",
}

has_permission = {
	"Stock Count": "adeel_traders.permissions.stock_count_has_permission",
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"adeel_traders.tasks.all"
# 	],
# 	"daily": [
# 		"adeel_traders.tasks.daily"
# 	],
# 	"hourly": [
# 		"adeel_traders.tasks.hourly"
# 	],
# 	"weekly": [
# 		"adeel_traders.tasks.weekly"
# 	],
# 	"monthly": [
# 		"adeel_traders.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "adeel_traders.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "adeel_traders.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "adeel_traders.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "adeel_traders.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["adeel_traders.utils.before_request"]
# after_request = ["adeel_traders.utils.after_request"]

# Job Events
# ----------
# before_job = ["adeel_traders.utils.before_job"]
# after_job = ["adeel_traders.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"adeel_traders.auth.validate"
# ]

# on_social_login = "adeel_traders.auth.on_social_login"

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
