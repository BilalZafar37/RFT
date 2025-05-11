from flask import request, jsonify, render_template
from sqlalchemy import func, literal_column
from . import bp
from models import model, FreightTrackingView, RFT_IntervalConfig
from utils import get_distinct, get_distinct_format


@bp.route("/leadtime/drilldown", methods=["GET"])
def leadtime_drilldown():
    brand = request.args["brand"]
    months = request.args.getlist("lt_months") or get_distinct_format("POCreatedDate","yyyy-MM")
    limit  = request.args.get("lt_limit", 10, type=int)

    intervals = model.query(RFT_IntervalConfig).order_by(RFT_IntervalConfig.ID).all()

    # shipments for that brand/month
    shipments = [s for (s,) in 
      model.query(FreightTrackingView.ShipmentNumber)
           .filter(FreightTrackingView.Brand==brand,
                   func.format(FreightTrackingView.POCreatedDate,'yyyy-MM').in_(months))
           .filter(FreightTrackingView.ShipmentNumber.is_not(None))
           .distinct()
           .order_by(FreightTrackingView.ShipmentNumber)
           .limit(limit)
           .all()
    ]

    # compute per-shipment lead times
    drill = []
    for ship in shipments:
        rec = {}
        for cfg in intervals:
            sf, ef = getattr(FreightTrackingView, cfg.StartField), getattr(FreightTrackingView, cfg.EndField)
            avgd = (model.query(func.avg(func.datediff(literal_column("day"), sf, ef)))
                         .filter(FreightTrackingView.ShipmentNumber==ship,
                                 sf.isnot(None), ef.isnot(None))
                         .scalar() or 0)
            rec[cfg.IntervalName] = round(avgd,1)
        drill.append(rec)

    # build Chart.js JSON
    palette = ['#FF6384','#36A2EB','#FFCE56','#4BC0C0','#9966FF','#FF9F40','#66FF66']
    datasets = [{
      "label": cfg.IntervalName,
      "data":  [r[cfg.IntervalName] for r in drill],
      "backgroundColor": palette[i],
      "borderColor":     palette[i]
    } for i,cfg in enumerate(intervals)]

    return jsonify(labels=shipments, datasets=datasets)
