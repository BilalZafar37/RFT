from flask import request, render_template, jsonify
from . import bp
from utils import (
  get_distinct,
  compute_fulfillment_by_brand,
  compute_fulfillment_by_po
)
from models import RFT_PurchaseOrderLine, RFT_PurchaseOrder, model

@bp.route("/fulfillment/drilldown", methods=["GET"])
def fulfillment_drilldown():
  brand = request.args.get("brand")

  # First: fetch list of PO numbers for that brand
  POL = RFT_PurchaseOrderLine
  PO  = RFT_PurchaseOrder

  # Get POs for selected brand
  po_list = (
      model.query(PO.PONumber)
      .join(POL, POL.POID == PO.POID)
      .filter(PO.Brand == brand)
      .distinct()
      .all()
  )

  # Convert list of tuples to simple list
  sel_pos = [r[0] for r in po_list]

  # Now call your existing PO calculation function
  fulfill_po = compute_fulfillment_by_po(pos=sel_pos)

  # Prepare JSON structure
  labels = [row['po'] for row in fulfill_po]
  delivered = [row['delivered_pct'] for row in fulfill_po]
  intransit = [row['intransit_pct'] for row in fulfill_po]
  open_qty = [row['open_pct'] for row in fulfill_po]


  # Build final response
  return jsonify({
    "labels": labels,
    "delivered": delivered,
    "intransit": intransit,
    "open": open_qty,
    "delivered_label": [row['delivered_label'] for row in fulfill_po],
    "intransit_label": [row['intransit_label'] for row in fulfill_po],
    "open_label":      [row['open_label'] for row in fulfill_po]
    
  })