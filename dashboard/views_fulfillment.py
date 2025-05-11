from flask import request, render_template, jsonify
from . import bp
from utils import (
  get_distinct,
  compute_fulfillment_by_brand,
  compute_fulfillment_by_po
)
from models import FreightTrackingView, model

@bp.route("/fulfillment/drilldown", methods=["GET"])
def fulfillment_drilldown():
    brand = request.args["brand"]
    # 1) get all POs for that brand
    pos = [
      po for (po,) in model.query(FreightTrackingView.PONumber)
                          .filter(FreightTrackingView.Brand==brand)
                          .distinct()
                          .order_by(FreightTrackingView.PONumber)
                          .all()
    ]
    # 2) compute PO‐level %
    po_data = compute_fulfillment_by_po(pos=pos)

    # Chart.js needs `labels` + two `datasets`
    delivered = [r["delivered_pct"] for r in po_data]
    intransit = [r["intransit_pct"] for r in po_data]

    return jsonify({
      "labels":      [r["po"] for r in po_data],
      "datasets": [
        {
          "label": "Delivered %",
          "data":  delivered,
          "backgroundColor": "#4CAF50",
          "borderColor":     "#4CAF50",
        },
        {
          "label": "In-Transit %",
          "data":  intransit,
          "backgroundColor": "#2196F3",
          "borderColor":     "#2196F3",
        }
      ]
    })
