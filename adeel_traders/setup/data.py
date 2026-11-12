"""Business master data for the Adeel Traders core setup (Phase 1).

All business values live here so they can be reviewed and changed in one place.
Source: Adeel Traders SRS v1.0 (sections 1.2, 2.1, 3.1-3.3, 3.6) and the implementation plan.
Items marked "PENDING CLIENT" are assumptions until the client answers the open questions.
"""

# Units of measure (SRS 3.2). "Box" already ships with ERPNext.
# Per-item conversion factors (e.g. 1 Carton = 12 Box) are set on each Item, not globally,
# because box/carton sizes differ by product.
UOMS = ["Piece", "Dozen", "Box", "Carton"]
# SRS 3.2: how many Pieces a unit holds, for every product that has no size of its own
# (Frappe's UOM Conversion Factor; a product's own size always wins). Only what is the same for
# every product: a Dozen is 12. A Box and a Carton differ by product (D23), so they are set
# per product (P3.11), or by the owner here when they are the same for most products.
DEFAULT_UNIT_SIZES = {"Dozen": 12}
# Payment methods ERPNext's own code names (the till, payments): they keep their name and stay on.
# The business's real methods (JazzCash, Easypaisa, Bank Transfer...) are set up in Phase 2 (P2.3).
PROTECTED_PAYMENT_METHODS = ("Cash",)
# Frappe wants a category on every conversion factor; ERPNext's are Length, Mass... ours is "Packs".
UNIT_CATEGORY = "Packs"
# P1.12: units kept switched on besides ours and the ones in use; ERPNext's other sample units
# (Acre, Ampere, ...) are switched off once so the Units list is the business's own.
COMMON_UOMS = ["Nos", "Unit", "Pair", "Set", "Gram", "Kg", "Litre", "Millilitre", "Ounce"]

# Item group tree (SRS 3.1 and the sample screens). Key = group, value = children.
ITEM_GROUPS = {
	"Makeup": ["Lips", "Eyes", "Face"],
	"Skin Care": [],
	"Hair Care": [],
	"Fragrance": [],
	"Gift Sets": [],
}

# Selling price lists, one per sales channel (SRS 1.2, 3.2).
PRICE_LISTS = ["Wholesale", "Online", "Self-delivery"]

# Customer types and the price list each one gets automatically (SRS 3.6).
# PENDING CLIENT (Q2): is the Self-delivery price also used for walk-in shop customers?
CUSTOMER_GROUPS = {
	"Wholesale Dealer": "Wholesale",
	"Online Customer": "Online",
	"Walk-in Customer": "Self-delivery",
}

# Warehouses created under the company's root warehouse (SRS 3.3).
# PENDING CLIENT (Q1): number of branches / shops.
WAREHOUSES = [
	"Main Warehouse",
	"Shop Counter",
	"Damaged",
	"Returns - To Check",
	"Testers",
]

# Sales channels used by the "Sales Channel" accounting dimension (SRS 1.2, 3.14, 4).
SALES_CHANNELS = {
	"Wholesale": "Dealers and resellers; bulk orders, often on credit.",
	"Online": "Website, phone and social media orders delivered by courier.",
	"Shop / Self-delivery": "Walk-in counter sales and local orders delivered by own rider.",
}

ACCOUNTING_DIMENSION = "Sales Channel"

# Settings applied once at setup (later changes by an admin are never overwritten).
STOCK_SETTINGS = {
	"enable_stock_reservation": 1,  # SRS 3.3: hold stock for confirmed orders
	"auto_reserve_stock": 1,
	"allow_negative_stock": 0,
	"pick_serial_and_batch_based_on": "Expiry",  # FEFO for expiring cosmetics
	"auto_indent": 1,  # SRS 3.3/3.5: material request at reorder level
}

SELLING_SETTINGS = {
	"validate_selling_price": 1,  # SRS 3.2: block selling below cost
}


# ---------------------------------------------------------------------------
# Phase 2: products and stock
# ---------------------------------------------------------------------------

# Variant attributes (SRS 3.1). ERPNext already ships "Size" and "Colour".
# Attribute VALUES (e.g. "05 Berry Kiss") are catalogue data, added with the products.
ITEM_ATTRIBUTES = ["Shade", "Size", "Fragrance"]

# Stock adjustment types (SRS 3.3). Damaged and Tester stock is MOVED to its own warehouse
# (kept apart from sellable stock until checked); Expired and Lost stock is WRITTEN OFF.
STOCK_ENTRY_TYPES = {
	"Damaged": "Material Transfer",
	"Tester": "Material Transfer",
	"Expired": "Material Issue",
	"Lost": "Material Issue",
}
# Stock entries of these types must have a written reason (SRS 3.3).
ADJUSTMENT_TYPES_NEEDING_REASON = list(STOCK_ENTRY_TYPES)

# Where Damaged / Tester stock is moved to (warehouse_name; company abbr is added).
ADJUSTMENT_TARGET_WAREHOUSES = {
	"Damaged": "Damaged",
	"Tester": "Testers",
}

# Stock in these warehouses is never counted as "on hand" or "free to sell" (SRS 3.3):
# damaged goods, goods waiting for a quality check, and shop testers cannot be sold.
NOT_FOR_SALE_WAREHOUSES = ["Damaged", "Returns - To Check", "Testers"]

# Batches expiring within this many days are flagged as "Expiring" (SRS 3.3, FEFO).
EXPIRY_WARNING_DAYS = 90

# Role that lets a user see cost price, margin and stock value in Rs (SRS 2.1: counter staff
# must not see cost). A real role, so the owner can give it to any custom role (Phase 1, P1.2).
COST_VIEWER_ROLE = "Can View Cost"

# Role that allows exporting lists and reports to Excel/PDF (SRS 6). Counter staff and the online
# team do not get it by default.
EXPORT_ROLE = "Can Export Lists"

# Switches on the Roles page: a yes/no on a role that is not a permission on one document type.
# key -> (Role field, real role carrying it, title, help, private, sentence for the role summary). The built-in role profiles
# include the carrying role; the owner switches it on or off for custom roles on the Roles page.
SWITCHES = {
	"cost": (
		"at_can_view_cost",
		"Can View Cost",
		"See cost, margin and stock value",
		"Without this, cost columns and margin are hidden from the person.",
		True,
		"Can see cost, margin and stock value in Rs",
	),
	"export": (
		"at_can_export",
		"Can Export Lists",
		"Export lists to Excel and PDF",
		"Shows the Export button on lists and reports.",
		False,
		"Can export lists to Excel and PDF",
	),
	"history": (
		"at_can_view_history",
		"Can See Change History",
		"See the change history",
		"Opens Settings > Change history: who changed what, and when, across the business.",
		True,
		"Can see the change history of the business",
	),
	"approve": (
		"at_can_approve",
		"Can Decide Approvals",
		"Decide approvals",
		"Shows the Approvals inbox. Approving or rejecting a request is still checked against its own approval rules.",
		False,
		"Can decide approvals",
	),
}

# The 7 staff roles of SRS 2.1 (Role Profile name -> label). They are locked: the owner can copy one
# into a new role but not change it, because they are built from ERPNext's standard roles.
BUILT_IN_ROLES = {
	"AT Owner": "Owner",
	"AT Manager": "Manager",
	"AT Counter Staff": "Counter staff",
	"AT Store": "Store",
	"AT Accountant": "Accountant",
	"AT HR": "HR",
	"AT Online Order Team": "Online order team",
}

# One sentence per built-in role for the Roles page (SRS 2.1, "Main access").
BUILT_IN_ROLE_NOTES = {
	"AT Owner": "Runs the whole business: everything, including prices, cost, users and settings.",
	"AT Manager": "Products, stock, sales, purchasing, approvals and reports. No payroll.",
	"AT Counter Staff": "Sells at the counter and handles returns in their own branch. Cannot change prices.",
	"AT Store": "Receives and moves stock, counts it, packs orders and prints barcode labels.",
	"AT Accountant": "Ledgers, receipts, payments, expenses and financial reports.",
	"AT HR": "Employees, attendance, leave, salary and payslips.",
	"AT Online Order Team": "Website orders, courier booking and order status.",
}

# People (Phase 1, P1.3) -------------------------------------------------------------------------
# The built-in role that runs the business. Giving it to someone needs an extra confirmation, and
# the last active person with it can never lose it or be disabled.
OWNER_PROFILE = "AT Owner"

# Only these roles may open Frappe Desk (decision D1 = B: Desk is for admins and the accountant's
# expert tools). Everyone else works in the staff app; opening /desk sends them there.
DESK_ROLES = {"System Manager", "Accounts Manager"}

# Login security (SRS 3.13), applied once so later admin changes are kept.
# session_expiry: log out after 8 hours without activity (one shop day).
# allow_consecutive_login_attempts / allow_login_after_fail: lock for 5 minutes after 10 wrong tries.
# 10 follows the common standard (Microsoft security baseline; NIST 800-63B: limit attempts without
# punishing normal mistakes). The lock is 5 minutes, not the usual 15, because Frappe also locks
# the network address and all shop staff share one; the owner can also press Unlock on People.
SECURITY_SETTINGS = {
	"session_expiry": "08:00",
	"allow_consecutive_login_attempts": 10,
	"allow_login_after_fail": 300,
}

# Branches (decision D4, 11 Nov 2026): one company, two branches to start. A warehouse belongs
# to one branch. Warehouses not listed here (Damaged, Returns - To Check, Testers) belong to
# no branch: staff of any branch may use them.
BRANCHES = ["Main Warehouse", "Shop Counter"]
WAREHOUSE_BRANCH = {
	"Main Warehouse": "Main Warehouse",
	"Shop Counter": "Shop Counter",
}
# Each branch has its own cost center, so sales and expenses can be reported per branch (P1.4,
# used from Phase 2). Main Warehouse uses the company's existing "Main" cost center.
BRANCH_COST_CENTER = {
	"Main Warehouse": "Main",
	"Shop Counter": "Shop Counter",
}

# Store staff can COUNT stock (draft Stock Reconciliation); a Stock Manager submits it.
STOCK_COUNTER_ROLE = "Stock Counter"

STOCK_SETTINGS_PHASE_2 = {
	"stock_uom": "Piece",  # default unit for new items (SRS 3.2)
}

# Rules agreed before the next feature (applied once, so later admin changes are kept):
# - a price typed on an order must never silently create or change a price list entry
# - only a Stock Manager may enter or change stock transactions dated in the past
STOCK_RULES_SETTINGS = {
	"auto_insert_price_list_rate_if_missing": 0,
	"role_allowed_to_create_edit_back_dated_transactions": "Stock Manager",
}
# Categories ERPNext creates on its own. Hidden in our screens while no product uses them.
ERPNEXT_FACTORY_ITEM_GROUPS = ["Products", "Raw Material", "Services", "Sub Assemblies", "Consumable"]

# Gift sets are created in this category unless another is chosen.
GIFT_SET_GROUP = "Gift Sets"

# Email the Purchase/Stock Managers when ERPNext raises purchase requests at the reorder level.
REORDER_EMAIL_SETTINGS = {"reorder_email_notify": 1}

# New receipts and stock entries default to this warehouse (company abbr is added).
DEFAULT_WAREHOUSE = "Main Warehouse"
# Warehouses ERPNext creates on its own; none of them is used by Adeel Traders.
ERPNEXT_FACTORY_WAREHOUSES = ["Stores", "Goods In Transit", "Finished Goods", "Work In Progress"]

# v16 ships with batch/serial tracking switched OFF. Cosmetics expire, so batches with
# expiry dates (and FEFO picking) are needed (SRS 3.3).
BATCH_TRACKING_SETTINGS = {
	"enable_serial_and_batch_no_for_item": 1,
}

# Internal EAN-13 barcodes for items without one (SRS 3.4).
# GS1 reserves prefixes 20-29 for in-store / restricted use, so they never clash with
# real manufacturer barcodes. Format: "20" + 10-digit counter + check digit.
BARCODE_PREFIX = "20"
BARCODE_SERIES_KEY = "AT-INTERNAL-EAN-"


# P1.5 (SRS 3.13 "record of every change"): ERPNext keeps a Version for every save of these
# records. Item, Item Price, Stock Entry, User, Role, Company and Warehouse track changes already;
# Stock Count tracks them in its own doctype.
TRACKED_DOCTYPES = (
	"Branch",
	"Stock Reconciliation",
	"Brand",
	"Item Group",
	"Product Bundle",
	"Pricing Rule",
	"Address",
	"UOM",
	"Mode of Payment",
	"Email Template",
	"Letter Head",
	"Role Profile",
)
# P1.8: who may email a document's PDF (the managers who post it). Custom DocPerm rows copied
# by the Roles page override the doctype file, so the patch sets them too.
DOCUMENT_EMAIL_ROLES = {"Stock Count": ("System Manager", "Stock Manager")}
# P1.10: extra roles that may open a standard report (managers run stock reports). Added with a
# Custom Role, which keeps the report's own roles too.
REPORT_EXTRA_ROLES = {"Stock Balance": ("Stock Manager",)}
# Permissions the owner needs that ERPNext does not give System Manager: (doctype, role, type).
# "Try again" on a failed outside call writes to Integration Request, which System Manager only reads.
OWNER_EXTRA_PERMISSIONS = (("Integration Request", "System Manager", "write"),)

# Roles the first version of P1.5 gave raw read access to Frappe's change records. That access is
# removed: the change history is now the "Can See Change History" switch, and reading the raw
# records through Frappe's own API could show cost to people who must not see it.
RAW_VERSION_ACCESS_TO_REMOVE = ("Accounts Manager", "Sales Manager")
