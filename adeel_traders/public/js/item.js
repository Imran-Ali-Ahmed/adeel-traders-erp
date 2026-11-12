// Item form: "Generate Barcode" button for items without a barcode (SRS 3.4).
// All rules (permission, no overwrite, uniqueness, EAN-13 check) are enforced on the server.
frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new() || (frm.doc.barcodes || []).length) return;

		frm.add_custom_button(__("Generate Barcode"), () => {
			frappe.call({
				method: "adeel_traders.stock.barcode.generate_item_barcode",
				args: { item_code: frm.doc.name },
				freeze: true,
				freeze_message: __("Generating barcode..."),
				callback(r) {
					if (r.message) {
						frappe.show_alert({
							message: __("Barcode {0} added", [r.message]),
							indicator: "green",
						});
						frm.reload_doc();
					}
				},
			});
		});
	},
});
