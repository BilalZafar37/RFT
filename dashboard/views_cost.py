from flask import request, jsonify, render_template
from sqlalchemy import func, cast, Numeric
from . import bp
from models import model, FreightTrackingView
from utils import (
  get_distinct, get_distinct_format,
  compute_cost_by_brand, compute_cost_by_shipment
)


@bp.route("/cost/drilldown", methods=["GET"])
def cost_drilldown():
    brand    = request.args["brand"]
    months   = request.args.getlist("cost_months[]") or get_distinct_format("POCreatedDate","yyyy-MM")
    categories = request.args.getlist("cost_categories[]") or get_distinct("CatName")

    # 1) shipments for that brand/month/category
    shipments = [s for (s,) in 
      model.query(FreightTrackingView.ShipmentNumber)
           .filter(FreightTrackingView.Brand==brand,
                   func.format(FreightTrackingView.POCreatedDate,'yyyy-MM').in_(months),
                   FreightTrackingView.CatName.in_(categories))
           .distinct()
           .order_by(FreightTrackingView.ShipmentNumber)
           .all()
    ]

    # 2) cost by shipment
    drill = compute_cost_by_shipment(shipments)

    # 3) build Chart.js datasets
    palette = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF','#FF9F40','#66FF66']
    fields  = ['freight_cost','custom_duties','saber','demurrage','penalties','others']
    datasets = [{
      "label": fld.replace('_',' ').title(),
      "data":  [r[fld] for r in drill],
      "backgroundColor": palette[i],
      "borderColor":     palette[i]
    } for i,fld in enumerate(fields)]

    return jsonify(labels=shipments, datasets=datasets)
