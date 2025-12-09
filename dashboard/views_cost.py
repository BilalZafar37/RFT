from flask import request, jsonify, render_template
from sqlalchemy import func, cast, Numeric
from . import bp
from models import ( model, FreightTrackingView, inspect,  
    RFT_Shipment    as S,
    RFT_ShipmentPOLine as SP,
    RFT_PurchaseOrderLine as POL,
    RFT_PurchaseOrder   as PO,
    RFT_Container       as C,
    RFT_CategoriesMappingMain as CM,
    RFT_PurchaseOrder as PO,
    or_
  )
from itertools import cycle
from utils import (
  get_distinct, get_distinct_format,
 compute_cost_by_shipment
)


@bp.route("/cost/drilldown", methods=["GET"])
def cost_drilldown():
  
  brand      = request.args["brand"]
  months     = request.args.getlist("cost_months[]") 
  # or get_distinct_format(PO, "PODate","yyyy-MM")
  categories = request.args.getlist("cost_categories[]") 
  # or get_distinct("CatName")
  
  filters = []
  
  if brand:
      filters.append(PO.Brand == brand)
  
  if months:
      filters.append(func.format(PO.PODate, 'yyyy-MM').in_(months))
  
  if categories:
    filters.append(
        or_(
            CM.CatName.in_(categories),
            CM.CatName.is_(None)
        )
    )
  
  # --- 1) find all shipments matching brand/month/category ---
  shipments = [
    s for (s,) in model.query(S.ShipmentNumber)
      .join(SP, SP.ShipmentID   == S.ShipmentID)
      .join(POL, POL.POLineID   == SP.POLineID)
      .join(PO,  PO.POID        == POL.POID)
      .outerjoin(CM, CM.ID      == POL.CategoryMappingID)
      .filter(*filters)
      .distinct()
      .order_by(S.ShipmentNumber)
      .all()
  ]
  
  # shipments.remove("RFT517284019")
  # shipments.remove("RFT204314178")
  # shipments.remove("RFT125014076")
  # shipments.remove("RFT441069197")

  # print(f"🟡 Found {len(shipments)} shipments: {shipments}")
  
  # --- 2) get per-shipment costs ---
  drill = compute_cost_by_shipment(shipments)

  # print(f"🟢 Cost breakdown for each shipment:")
  # for row in drill:
  #   print(f"  - {row['shipment']}:")
  #   for k, v in row.items():
  #       if k not in ("shipment", "num_containers", "cost_per_container", "total_expense"):
  #           print(f"      {k}: {v}")
  #   print(f"    ➤ Total: {row['total_expense']}, Per Container: {row['cost_per_container']}")
  
  
  
  # --- 3) palette & discover cost columns ---
  base_palette = [
    '#4e79a7','#f28e2b','#e15759','#76b7b2',
    '#59a14f','#edc949','#af7aa1','#ff9da7',
    '#9c755f','#bab0ab'
  ]
  pal = cycle(base_palette)

  cost_cols = [
      col.name
      for col in inspect(S).c
      if isinstance(col.type, Numeric)
          and (col.name.endswith("Cost") or col.name.endswith("Charges") or col.name in ("CustomDuties", "Penalties", "SaberSADDAD"))
  ]

  # --- 4) build datasets for Chart.js ---
  datasets = []
  for fld in cost_cols:
      color = next(pal)
      datasets.append({
          "label": fld,                        # exact column name
          "data":  [r.get(fld, 0) for r in drill],
          "backgroundColor": color,
          "borderColor":     color,
      })

  return jsonify({
    "labels": [r["shipment"] for r in drill],
    "datasets": datasets,
    "meta": {
      "num_containers":   [r.get("num_containers", 0) for r in drill],
      "cost_per_ctn":     [round(r["total_expense"] / r["num_containers"], 1) if r["num_containers"] else 0 for r in drill],
      "bl_list": [r.get("bl", "") for r in drill]
    }
  })
