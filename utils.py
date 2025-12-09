from flask import (
  # Flask, 
  # render_template,
  # request,
  # redirect,
  # url_for,
  # flash,
  session,
  send_file,
  make_response, url_for
  # jsonify,
  # Blueprint,
  # abort
)
# from decimal import Decimal
from openpyxl.styles import  Border, Side, Alignment, Font
from io import BytesIO
from openpyxl import Workbook
import pandas as pd
import random
from datetime import datetime, timedelta
from collections import defaultdict, namedtuple
from models     import *
import pycountry
import re

# statuses that count as "delivered"
DELIVERED_STATUSES = ['Delivered']
OPEN_STATUSES = ["LC-Established", "PO-shared with supplier"]

def cost_columns():
  costcols = [
      col.name
      for col in inspect(RFT_Shipment).c
      if isinstance(col.type, Numeric)
          and (col.name.endswith("Cost") or col.name.endswith("Charges") or col.name in ("CustomDuties", "Penalties", "SaberSADDAD"))
  ]
  return costcols

# the four “real” container stages, in the order you want them reported:
CONTAINER_STAGES = [
    "IN-Transit",
    "Under Clearance",
    "In Yard",
    "Delivered"
]

# redefine your namedtuple to include "cat" between brand and article
ExpenseRow = namedtuple("ExpenseRow",
    ["brand", "cat", "article", "shipment", "delivery_date", "total_expense"]
)
####################                          ##################### 
####################  Computation functions   #####################
def get_distinct(column_name):
    """Return a sorted list of distinct non-null values for the given column."""
    col = getattr(FreightTrackingView, column_name)
    vals = (
      model.query(col)
           .filter(col.isnot(None))
           .distinct()
          #  .order_by(col)
           .all()
    )
    # unpack tuples and drop any empty
    return [v for (v,) in vals if v not in (None, '')]
    # return vals

# Now takes the table name 
def get_distinct_format(table, date_column, fmt):
    """
    table -- > table name as object (e.g RFT_PurchaseOrder)
    date_column -- > column name 
    fmt -- > format (e.g. yyyy-MM)
    
    Return distinct list of formatted dates (e.g. yyyy-MM), ordered.
    """
    col = getattr(table, date_column)
    fmt_expr = func.format(col, fmt).label("fmt")

    # Subquery to apply FORMAT first
    subq = (
        model.query(fmt_expr)
        .filter(col.isnot(None))
        .subquery()
    )

    # Now safely group by the formatted result
    vals = (
        model.query(subq.c.fmt)
        .group_by(subq.c.fmt)
        .order_by(subq.c.fmt)
        .all()
    )
    return [v for (v,) in vals if v]

# Cost chart
def compute_cost_by_brand(brands=None, months=None, shp_months=None, categories=None, sel_shp=None, sel_po=None):
  # brands=None
  # print(brands)
  S   = RFT_Shipment
  SP  = RFT_ShipmentPOLine
  POL = RFT_PurchaseOrderLine
  PO  = RFT_PurchaseOrder
  CM  = RFT_CategoriesMappingMain
  C   = RFT_Container

  # --- 1) auto-discover your cost columns on Shipment ---
  cost_cols = cost_columns()

  # --- 2) subquery: the DISTINCT shipments you care about ---
  ship_ids = (
    model.query(
      S.ShipmentID.label("ShipmentID"),
      PO.Brand.label("brand")
    )
    .join(SP,  SP.ShipmentID  == S.ShipmentID)
    .join(POL, POL.POLineID   == SP.POLineID)
    .join(PO,  PO.POID        == POL.POID)
    .outerjoin(CM, CM.ID       == POL.CategoryMappingID)
    .filter(
      *( [PO.Brand.in_(brands)]       if brands   else [] ),
      *( [func.format(PO.PODate,'yyyy-MM').in_(months)] if months else [] ),
      *( [CM.CatName.in_(categories)] if categories else [] )
    )
    .distinct()
    .subquery()
  )
  

  # --- 3) now sum each cost *once per shipment* by joining back to S ---
  sum_exprs = [
      func.coalesce(func.sum(getattr(S, col)), 0).label(col)
      for col in cost_cols
  ]

  brand_costs = (
    model.query(
      ship_ids.c.brand,
      *sum_exprs
    )
    .join(S, S.ShipmentID == ship_ids.c.ShipmentID)
    .group_by(ship_ids.c.brand)
    .all()
  )

  # 1) Sum qty shipped per brand (no container join here!)
  shipped_totals = (
      model
      .query(
          ship_ids.c.brand.label("brand"),
          func.sum(SP.QtyShipped).label("num_articles")
      )
      .join(SP, SP.ShipmentID == ship_ids.c.ShipmentID)
      .group_by(ship_ids.c.brand)
      .subquery()
  )

  # 2) Count shipments & containers per brand
  counts = (
      model
      .query(
          ship_ids.c.brand.label("brand"),
          func.count(distinct(ship_ids.c.ShipmentID)).label("num_shipments"),
          func.count(distinct(C.ContainerID))        .label("num_containers"),
          shipped_totals.c.num_articles
      )
      .outerjoin(C, C.ShipmentID == ship_ids.c.ShipmentID)
      # now bring in the pre-aggregated sums
      .join(shipped_totals, shipped_totals.c.brand == ship_ids.c.brand)
      .group_by(
          ship_ids.c.brand,
          shipped_totals.c.num_articles
      )
  ).all()

  # make a dict if you like
  counts = {r.brand: r for r in counts}
  # counts = {r.brand: r for r in cnt_q.all()}

  # --- 5) stitch into your output ---
  out = []
  for row in brand_costs:
      c = counts.get(row.brand)
      total = sum(getattr(row, col) for col in cost_cols)
      out.append({
        "brand":         row.brand,
        **{col: float(getattr(row, col)) for col in cost_cols},
        "total_expense": float(total),
        "num_shipments":  c.num_shipments  if c else 0,
        "num_containers": c.num_containers if c else 0,
        "num_articles":   c.num_articles   if c else 0,
        # For brands:
        "num_containers": c.num_containers if c else 0,
        "cost_per_container": float(total / c.num_containers) if c and c.num_containers else 0,
      })
  # print(out)
  return out

def compute_cost_by_shipment(shipment_numbers):
    S = RFT_Shipment
    C = RFT_Container

    # 1) auto-discover your cost columns on Shipment
    cost_cols = cost_columns()

    # 2) build one aggregate query: sum each cost per ShipmentNumber
    aggregates = [
        func.coalesce(func.sum(getattr(S, col)), 0).label(col)
        for col in cost_cols
    ]

    
    q = (
        model.query(
        S.ShipmentNumber.label("shipment"),
        S.BLNumber.label("bl"),
        *aggregates
        )
        .filter(S.ShipmentNumber.in_(shipment_numbers))
        .group_by(S.ShipmentNumber, S.BLNumber)  # Include BLNumber in group_by
    )

    results = []
    for row in q.all():
        out = {"shipment": row.shipment, "bl": row.bl}
        total = 0.0

        # 3) pull each cost, convert to float, accumulate
        for col in cost_cols:
            fv = float(getattr(row, col) or 0)
            out[col] = fv
            total += fv
        
        # # ✅ Skip shipment if all values are 0
        if total == 0:
            continue
        
        # print(f"[{row.shipment}] Total: {total}")
        # print({col: out[col] for col in cost_cols})  # DEBUG print
        
        shipment_id = model.query(S.ShipmentID).filter(S.ShipmentNumber == row.shipment).scalar()
        out["num_containers"] = (
            model.query(func.count(distinct(C.ContainerID)))
            .filter(C.ShipmentID == shipment_id)
            .scalar()
        )

        out["cost_per_container"] = (
            total / out["num_containers"] if out["num_containers"] else 0
        )

        out["total_expense"] = total
        results.append(out)

    return results


# Lead Time Chart
def compute_leadtime_by_brand(brands=None, months=None, shp_months = None):
    """
    Returns a list of dicts, one per brand, with avg lead‐time per configured interval.
    Optional filters: brands (list of brand names), months (list of 'yyyy-MM' strings).
    """
    # 1) load all intervals
    intervals = (
        model
        .query(RFT_IntervalConfig)
        .order_by(RFT_IntervalConfig.ID)
        .all()
    )

    results = []
    for b in (brands or []):
        row = {"brand": b}
        for cfg in intervals:
            sf = getattr(FreightTrackingView, cfg.StartField)
            ef = getattr(FreightTrackingView, cfg.EndField)

            q = (model.query(
                    func.avg(
                        func.datediff(literal_column("day"), sf, ef)
                    )
                )
                .filter(FreightTrackingView.Brand == b,
                        sf.isnot(None), ef.isnot(None))
            )

            # apply month filter if provided
            if months:
                q = q.filter(
                  func.format(FreightTrackingView.PODate,'yyyy-MM')
                  .in_(months)
                )
            
            # Shipment creation date
            if shp_months:
                q = q.filter(
                  func.format(FreightTrackingView.CreatedDate,'yyyy-MM')
                  .in_(shp_months)
                )

            avg_days = q.scalar() or 0
            row[cfg.IntervalName] = round(avg_days, 1)

        results.append(row)

    return results


# Fulfillment chart 
def format_million(value):
    if not value or value == 0:
        return "0M"
    if value < 10_000:
        return "<0.01M"
    return f"{round(value / 1_000_000, 2)}M" if value < 10_000_000 else f"{round(value / 1_000_000):.0f}M"

def compute_fulfillment_by_brand(brands=None):
    POL = RFT_PurchaseOrderLine
    SP = RFT_ShipmentPOLine
    PO = RFT_PurchaseOrder
    SH = RFT_StatusHistory
    C = RFT_Container

    shipped_subq = (
        model.query(
            SP.POLineID.label('POLineID'),
            func.sum(SP.QtyShipped).label('QtyShipped')
        )
        .group_by(SP.POLineID)
        .subquery()
    )

    latest_status_date_subq = (
        model.query(
            SH.EntityID.label("ContainerID"),
            func.max(SH.StatusDate).label("MaxDate")
        )
        .filter(SH.EntityType == 'Container')
        .group_by(SH.EntityID)
        .subquery()
    )

    latest_status_subq = (
        model.query(
            SH.EntityID.label("ContainerID"),
            SH.Status.label("Status")
        )
        .join(
            latest_status_date_subq,
            and_(
                SH.EntityID == latest_status_date_subq.c.ContainerID,
                SH.StatusDate == latest_status_date_subq.c.MaxDate
            )
        )
        .filter(SH.EntityType == 'Container')
        .subquery()
    )

    q = (
        model.query(
            PO.Brand.label("brand"),
            func.sum(POL.Qty).label("total_qty"),
            func.sum(POL.BalanceQty).label("open_qty"),
            func.coalesce(func.sum(
                case(
                    (latest_status_subq.c.Status.in_(DELIVERED_STATUSES), shipped_subq.c.QtyShipped),
                    else_=0
                )
            ), 0).label("delivered_qty"),
            func.coalesce(func.sum(
                case(
                    (~latest_status_subq.c.Status.in_(DELIVERED_STATUSES), shipped_subq.c.QtyShipped),
                    else_=0
                )
            ), 0).label("intransit_qty"),
            func.sum(
                case(
                    (POL.Qty > 0, POL.TotalValue * POL.BalanceQty / POL.Qty),
                    else_=0
                )
            ).label("open_value"),
            func.sum(
                case(
                    (
                        and_(
                            latest_status_subq.c.Status.in_(DELIVERED_STATUSES),
                            POL.Qty > 0
                        ),
                        POL.TotalValue * shipped_subq.c.QtyShipped / POL.Qty
                    ),
                    else_=0
                )
            ).label("delivered_value"),
            func.sum(
                case(
                    (
                        and_(
                            ~latest_status_subq.c.Status.in_(DELIVERED_STATUSES),
                            POL.Qty > 0
                        ),
                        POL.TotalValue * shipped_subq.c.QtyShipped / POL.Qty
                    ),
                    else_=0
                )
            ).label("intransit_value")
        )
        .join(POL, POL.POID == PO.POID)
        .outerjoin(shipped_subq, shipped_subq.c.POLineID == POL.POLineID)
        .outerjoin(SP, SP.POLineID == POL.POLineID)
        .outerjoin(C, C.ShipmentID == SP.ShipmentID)
        .outerjoin(latest_status_subq, latest_status_subq.c.ContainerID == C.ContainerID)
    )

    if brands:
        q = q.filter(PO.Brand.in_(brands))

    rows = q.group_by(PO.Brand).all()

    results = []
    for r in rows:
        if r.total_qty:
            dp = r.delivered_qty / r.total_qty * 100
            ip = r.intransit_qty / r.total_qty * 100
            op = r.open_qty / r.total_qty * 100
        else:
            dp = ip = op = 0
        results.append({
            "brand": r.brand,
            "open_pct": round(op, 1),
            "delivered_pct": round(dp, 1),
            "intransit_pct": round(ip, 1),
            "open_value": round(r.open_value or 0, 2),
            "delivered_value": round(r.delivered_value or 0, 2),
            "intransit_value": round(r.intransit_value or 0, 2),
            "delivered_label": f"{round(dp,1)}% ({format_million(r.delivered_value)})",
            "intransit_label": f"{round(ip,1)}% ({format_million(r.intransit_value)})",
            "open_label": f"{round(op,1)}% ({format_million(r.open_value)})"
        })

    return results

def compute_fulfillment_by_po(pos=None):
    POL = RFT_PurchaseOrderLine
    SP = RFT_ShipmentPOLine
    PO = RFT_PurchaseOrder
    SH = RFT_StatusHistory
    C = RFT_Container

    # Subquery: sum of QtyShipped per POLineID
    shipped_subq = (
        model.query(
            SP.POLineID.label('POLineID'),
            func.sum(SP.QtyShipped).label('QtyShipped')
        )
        .group_by(SP.POLineID)
        .subquery()
    )

    # Subquery: latest Container Status per ContainerID
    # Subquery: latest Container Status per ContainerID
    latest_status_date_subq = (
        model.query(
            SH.EntityID.label("ContainerID"),
            func.max(SH.StatusDate).label("MaxDate")
        )
        .filter(SH.EntityType == 'Container')
        .group_by(SH.EntityID)
        .subquery()
    )
    
    latest_status_subq = (
        model.query(
            SH.EntityID.label("ContainerID"),
            SH.Status.label("Status")
        )
        .join(
            latest_status_date_subq,
            and_(
                SH.EntityID == latest_status_date_subq.c.ContainerID,
                SH.StatusDate == latest_status_date_subq.c.MaxDate
            )
        )
        .filter(SH.EntityType == 'Container')
        .subquery()
    )
    

    # Main query
    q = (
        model.query(
            PO.PONumber.label("po"),
            func.sum(POL.Qty).label("total_qty"),
            func.sum(POL.BalanceQty).label("open_qty"),
            func.coalesce(func.sum(
                case(
                    (latest_status_subq.c.Status.in_(DELIVERED_STATUSES), SP.QtyShipped),
                    else_=0
                )
            ), 0).label("delivered_qty"),
            func.coalesce(func.sum(
                case(
                    (~latest_status_subq.c.Status.in_(DELIVERED_STATUSES), SP.QtyShipped),
                    else_=0
                )
            ), 0).label("intransit_qty"),
            func.sum(
                case(
                    (POL.Qty > 0, POL.TotalValue * POL.BalanceQty / POL.Qty),
                    else_=0
                )
            ).label("open_value"),
            func.sum(
                case(
                    (
                        and_(
                            latest_status_subq.c.Status.in_(DELIVERED_STATUSES),
                            POL.Qty > 0
                        ),
                        POL.TotalValue * SP.QtyShipped / POL.Qty
                    ),
                    else_=0
                )
            ).label("delivered_value"),
            func.sum(
                case(
                    (
                        and_(
                            ~latest_status_subq.c.Status.in_(DELIVERED_STATUSES),
                            POL.Qty > 0
                        ),
                        POL.TotalValue * SP.QtyShipped / POL.Qty
                    ),
                    else_=0
                )
            ).label("intransit_value")
        )
        .join(POL, POL.POID == PO.POID)
        .join(SP, SP.POLineID == POL.POLineID)
        .join(C, C.ShipmentID == SP.ShipmentID)
        .outerjoin(shipped_subq, shipped_subq.c.POLineID == POL.POLineID)
        .outerjoin(latest_status_subq, latest_status_subq.c.ContainerID == C.ContainerID)
    )

    if pos:
        q = q.filter(PO.PONumber.in_(pos))

    rows = q.group_by(PO.PONumber).all()

    results = []
    for r in rows:
        if r.total_qty:
            del_pct = r.delivered_qty / r.total_qty * 100
            in_pct = r.intransit_qty / r.total_qty * 100
            open_pct = r.open_qty / r.total_qty * 100
        else:
            del_pct = in_pct = open_pct = 0

        results.append({
            "po": r.po,
            "delivered_pct": round(del_pct, 1),
            "intransit_pct": round(in_pct, 1),
            "open_pct": round(open_pct, 1),
            "delivered_value": round(r.delivered_value or 0, 2),
            "intransit_value": round(r.intransit_value or 0, 2),
            "open_value": round(r.open_value or 0, 2),
            "delivered_label": f"{round(del_pct,1)}% ({format_million(r.delivered_value)})",
            "intransit_label": f"{round(in_pct,1)}% ({format_million(r.intransit_value)})",
            "open_label":      f"{round(open_pct,1)}% ({format_million(r.open_value)})"
        })

    return results


# Plan containers and pivot to correct dynamic format 
def pivot_matrix_to_rows(matrix, plan_groups, container_stages):
    """
    Turn matrix={stage:{plan_status:count}} into a list of dicts like:
        [ {"stage": stage, "total": total_count, "plan_statuses": [{label, original, count}, ...]}, … ]
    in the order of container_stages.
    """
    def clean_label(plan_status):
        return plan_status.replace("Planed ", "").replace("Delivery", "").strip()

    rows = []
    for stage in container_stages:
        ct_map = matrix.get(stage, {})
        total = sum(ct_map.get(grp, 0) for grp in plan_groups)

        row = {
            "stage": stage,
            "total": total,
            "plan_statuses": []
        }

        for grp in plan_groups:
            row["plan_statuses"].append({
                "plan_status": grp,
                "label": clean_label(grp),
                "count": ct_map.get(grp, 0)
            })

        rows.append(row)

    return rows

def compute_container_plan_stage_counts_grouped(plan_prefixes, mot, brands=None, months=None,
                                                shp_months=None, categories=None, sel_shp=None, sel_po=None):
    """
    Returns a dict-of-dicts for all containers whose latest planned-status starts with any of plan_prefixes,
    filtered by ModeOfTransport == mot and other optional filters.
    Output: { actual_status: { plan_status: count, ... }, ... }
    Also returns a sorted list of all distinct plan_status values seen.
    """
    model = None
    model = Session()
    S   = RFT_StatusHistory
    C   = RFT_Container
    SP  = RFT_ShipmentPOLine
    POL = RFT_PurchaseOrderLine
    PO  = RFT_PurchaseOrder
    SM  = RFT_StatusManagement

    # Step A: Latest planned-container status
    latest_plan = (
        model.query(
            S.EntityID.label("ContainerID"),
            func.max(S.StatusDate).label("maxdate")
        )
        .filter(S.EntityType == literal("Planed-Container"))
        .group_by(S.EntityID)
        .subquery()
    )
    latest_plan_status = (
        model.query(
            latest_plan.c.ContainerID,
            S.Status.label("plan_status")
        )
        .join(S, and_(
            S.EntityType == literal("Planed-Container"),
            S.EntityID   == latest_plan.c.ContainerID,
            S.StatusDate == latest_plan.c.maxdate
        ))
        .subquery()
    )

    # Step B: Filter plan statuses matching any prefix
    allowed_plan_set = set()
    for prefix in plan_prefixes:
        results = (
            model.query(SM.StatusName)
                 .filter(SM.Level == "Container Level")
                 .filter(SM.StatusName.like(f"{prefix}%"))
                 .all()
        )
        allowed_plan_set.update(r[0] for r in results)

    if not allowed_plan_set:
        return {}, []

    # Step C: Latest actual container status
    latest_act = (
        model.query(
            S.EntityID.label("ContainerID"),
            func.max(S.StatusDate).label("maxdate")
        )
        .filter(S.EntityType == literal("Container"))
        .group_by(S.EntityID)
        .subquery()
    )
    latest_act_status = (
        model.query(
            latest_act.c.ContainerID,
            S.Status.label("act_status")
        )
        .join(S, and_(
            S.EntityType == literal("Container"),
            S.EntityID   == latest_act.c.ContainerID,
            S.StatusDate == latest_act.c.maxdate
        ))
        .subquery()
    )

    # Step D: Join and filter by optional dimensions
    pairs = (
        model.query(
            latest_act_status.c.act_status.label("stage"),
            latest_plan_status.c.plan_status.label("plan_status"),
            func.count(func.distinct(latest_act_status.c.ContainerID)).label("ct")
        )
        .join(latest_plan_status,
              latest_act_status.c.ContainerID == latest_plan_status.c.ContainerID)
        .join(C, C.ContainerID == latest_act_status.c.ContainerID)
        .join(SP, SP.ShipmentID == C.ShipmentID)
        .join(RFT_Shipment, RFT_Shipment.ShipmentID == SP.ShipmentID)
        .join(POL, POL.POLineID == SP.POLineID)
        .join(PO,  PO.POID == POL.POID)
        .outerjoin(RFT_CategoriesMappingMain, POL.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        .filter(
            latest_plan_status.c.plan_status.in_(allowed_plan_set),
            RFT_Shipment.ModeOfTransport == mot,
            *( [PO.Brand.in_(brands)] if brands else [] ),
            *( [func.format(PO.PODate,'yyyy-MM').in_(months)] if months else [] ),
            *( [func.format(RFT_Shipment.CreatedDate,'yyyy-MM').in_(shp_months)] if shp_months else [] ),
            *( [RFT_CategoriesMappingMain.CatName.in_(categories)] if categories else []),
            *( [or_(
                RFT_Shipment.ShipmentNumber == sel_shp,
                RFT_Shipment.BLNumber == sel_shp
            )] if sel_shp else [] ),
            *( [PO.POID == sel_po] if sel_po else [] )
        )
        .group_by(
            latest_act_status.c.act_status,
            latest_plan_status.c.plan_status
        )
        .all()
    )

    # Custom preferred order
    priority_order = ["Planed DTC Delivery", "Planed GES-RYD", "Planed LSC-JED", "Planed LSC", "Planed RDC", "Planed JDC"]

    # Fallback for statuses not in priority_order → push to end
    plan_groups = sorted(
        { ps for (_, ps, _) in pairs },
        key=lambda x: priority_order.index(x) if x in priority_order else len(priority_order)
    )

    # Step E: Pivot result into matrix
    # plan_groups = sorted({ ps for (st, ps, ct) in pairs }, key=priority_order)
    matrix     = defaultdict(lambda: defaultdict(int))
    totals     = defaultdict(int)
    for stage, ps, ct in pairs:
        matrix[stage][ps] = ct
        totals[stage] += ct

    return matrix, plan_groups


# Shipment statuses
def compute_shipment_status_counts(mot, brands=None, months=None, 
                                   shp_months=None, categories=None, sel_shp=None, sel_po=None):
  model = None
  model = Session()
  S   = RFT_StatusHistory
  SP  = RFT_ShipmentPOLine
  POL = RFT_PurchaseOrderLine
  PO  = RFT_PurchaseOrder

  # 1) find each shipment’s max status date
  latest = (
    model.query(
      S.EntityID.label("ShipmentID"),
      func.max(S.StatusDate).label("maxdate")
    )
    .filter(S.EntityType == literal("Shipment"))
    .group_by(S.EntityID)
    .subquery("latest")
  )

  # 2) get the status text at that date
  latest_status = (
    model.query(
      S.EntityID.label("ShipmentID"),
      S.Status.label("status")
    )
    .join(latest,
          and_(
            S.EntityType  == literal("Shipment"),
            S.EntityID    == latest.c.ShipmentID,
            S.StatusDate  == latest.c.maxdate
          ))
    .subquery("latest_status")
  )

  # 3) join through your PO‐chain, filter by MOT, count DISTINCT shipments
  q = (
    model.query(
      latest_status.c.status,
      func.count(distinct(latest_status.c.ShipmentID)).label("count")
    )
    .join(SP, SP.ShipmentID == latest_status.c.ShipmentID)
    .join(RFT_Shipment, RFT_Shipment.ShipmentID == SP.ShipmentID) #new
    .join(POL, POL.POLineID   == SP.POLineID)
    .join(PO,  PO.POID        == POL.POID)
    .outerjoin(RFT_CategoriesMappingMain, POL.CategoryMappingID == RFT_CategoriesMappingMain.ID) #new
    # .filter(RFT_Shipment.ModeOfTransport == mot)
    .filter(
        RFT_Shipment.ModeOfTransport == mot,
        *( [PO.Brand.in_(brands)] if brands else [] ),
        *( [func.format(PO.PODate,'yyyy-MM').in_(months)] if months else [] ),
        *( [func.format(RFT_Shipment.CreatedDate,'yyyy-MM').in_(shp_months)] if shp_months else [] ),
        *( [RFT_CategoriesMappingMain.CatName.in_(categories)] if categories else []),
        # *( [RFT_Shipment.ShipmentNumber == sel_shp] if sel_shp else [] ),
        *( [or_(
            RFT_Shipment.ShipmentNumber == sel_shp,
            RFT_Shipment.BLNumber == sel_shp
        )] if sel_shp else [] ),
        *( [PO.POID == sel_po] if sel_po else [] )
    )
    .group_by(latest_status.c.status)
    .order_by(desc("count"))
  )

  return [{"status": st, "count": ct} for st, ct in q.all()]

# Upcoming ETAs calc for DASH
def compute_upcoming_eta(mot, brands = None, days_ahead: int = 7):
  """
  Return all shipments whose ETADestination is between now and now+days_ahead,
  for the given ModeOfTransport, ordered by ETADestination ascending.
  """
  model = None
  model = Session()
  now    = datetime.utcnow()
  cutoff = now + timedelta(days=days_ahead)

  q = (
    model.query(
        RFT_Shipment.ShipmentNumber   .label("shipment"),
        RFT_Shipment.ETADestination   .label("eta"),
        RFT_Shipment.OriginPort       .label("origin_port"),
        func.count(distinct(RFT_Container.ContainerID)).label("containers_num"),
        RFT_Shipment.POD              .label("dest_country"),
        RFT_Shipment.BLNumber         .label("BL"),
        RFT_PurchaseOrder.Brand       .label("Brand"),
    )
    # ensure we only include shipments whose PO has the desired MOT
    .join(
        RFT_ShipmentPOLine,
        RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID
    )
    .join(
        RFT_PurchaseOrderLine,
        RFT_PurchaseOrderLine.POLineID == RFT_ShipmentPOLine.POLineID
    )
    .join(
        RFT_PurchaseOrder,
        RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID
    )
    .filter(
        RFT_Shipment.ModeOfTransport == mot,
        RFT_Shipment.ETADestination.isnot(None),
        RFT_Shipment.ETADestination >= now,
        RFT_Shipment.ETADestination <= cutoff,
        *([RFT_PurchaseOrder.Brand.in_(brands)] if brands else [])
    )
    # include shipments with zero containers
    .outerjoin(RFT_Shipment.containers)
    .group_by(
        RFT_Shipment.ShipmentNumber,
        RFT_Shipment.ETADestination,
        RFT_Shipment.OriginPort,
        RFT_Shipment.POD,
        RFT_Shipment.BLNumber,
        RFT_PurchaseOrder.Brand,
    )
    .order_by(RFT_Shipment.ETADestination)
  )
  

  return [
      {
          "shipment":      shp,
          "eta":           eta,
          "origin_port":   op,
          "containers_num":cn,
          "dest_country":  dc,
          "BL":            bl,
          "Brand":         br
      }
      for shp, eta, op, cn, dc, bl, br in q.all()
  ]

# By Brand x Warehouse shipment statuses
def compute_plan_status_by_brand(plan_prefixes, brands=None, months=None, categories=None, shp_months=None, sel_shp=None, sel_po=None):
    """
    Returns a matrix of the form: { plan_status: { brand: count, ... }, ... }
    """
    model = Session()
    S   = RFT_StatusHistory
    C   = RFT_Container
    SP  = RFT_ShipmentPOLine
    POL = RFT_PurchaseOrderLine
    PO  = RFT_PurchaseOrder
    SM  = RFT_StatusManagement

    # Step A: Latest planned-container status
    latest_plan = (
        model.query(S.EntityID.label("ContainerID"), func.max(S.StatusDate).label("maxdate"))
        .filter(S.EntityType == literal("Planed-Container"))
        .group_by(S.EntityID)
        .subquery()
    )
    latest_plan_status = (
        model.query(latest_plan.c.ContainerID, S.Status.label("plan_status"))
        .join(S, and_(
            S.EntityType == literal("Planed-Container"),
            S.EntityID   == latest_plan.c.ContainerID,
            S.StatusDate == latest_plan.c.maxdate
        ))
        .subquery()
    )

    # ✅ Step B: Latest *actual* container status (we'll filter for "Delivered" here)
    latest_actual = (
        model.query(S.EntityID.label("ContainerID"), func.max(S.StatusDate).label("maxdate"))
        .filter(S.EntityType == literal("Container"))
        .group_by(S.EntityID)
        .subquery()
    )
    latest_actual_status = (
        model.query(latest_actual.c.ContainerID, S.Status.label("actual_status"))
        .join(S, and_(
            S.EntityType == literal("Container"),
            S.EntityID   == latest_actual.c.ContainerID,
            S.StatusDate == latest_actual.c.maxdate
        ))
        .filter(S.Status.in_(DELIVERED_STATUSES))  # ✅ Filter only Delivered containers
        .subquery()
    )
    
    # Step B: Get allowed planned statuses
    allowed_plan_set = set()
    for prefix in plan_prefixes:
        results = model.query(SM.StatusName)\
            .filter(SM.Level == "Container Level")\
            .filter(SM.StatusName.like(f"{prefix}%")).all()
        allowed_plan_set.update(r[0] for r in results)

    if not allowed_plan_set:
        return {}, []


    # Step D: Join and filter by optional dimensions
    pairs = (
        model.query(
            latest_plan_status.c.plan_status,
            PO.Brand,
            func.count(func.distinct(C.ContainerID)).label("ct")
        )
        .join(latest_actual_status, latest_actual_status.c.ContainerID == latest_plan_status.c.ContainerID)
        .join(C, C.ContainerID == latest_plan_status.c.ContainerID)
        .join(SP, SP.ShipmentID == C.ShipmentID)
        .join(RFT_Shipment, RFT_Shipment.ShipmentID == SP.ShipmentID)
        .join(POL, POL.POLineID == SP.POLineID)
        .join(PO,  PO.POID == POL.POID)
        .outerjoin(RFT_CategoriesMappingMain, POL.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        .filter(
            latest_plan_status.c.plan_status.in_(allowed_plan_set),
            # RFT_Shipment.ModeOfTransport == mot,
            *( [PO.Brand.in_(brands)] if brands else [] ),
            *( [func.format(PO.PODate,'yyyy-MM').in_(months)] if months else [] ),
            *( [func.format(RFT_Shipment.CreatedDate,'yyyy-MM').in_(shp_months)] if shp_months else [] ),
            *( [RFT_CategoriesMappingMain.CatName.in_(categories)] if categories else []),
            *( [or_(
                RFT_Shipment.ShipmentNumber == sel_shp,
                RFT_Shipment.BLNumber == sel_shp
            )] if sel_shp else [] ),
            *( [PO.POID == sel_po] if sel_po else [] )
        )
        .group_by(latest_plan_status.c.plan_status, PO.Brand)
        .all()
    )

    # Step E: Pivot
    matrix = defaultdict(lambda: defaultdict(int))
    all_brands = set()
    for plan_status, brand, ct in pairs:
        matrix[plan_status][brand] += ct
        all_brands.add(brand)

    
    # Desired order and display labels for warehouses
    warehouse_order = [
        "Planed RDC",
        "Planed JDC",
        "Planed KDC",
        "Planed GES-RYD",
        "Planed LSC-JED",
        "Planed LSC",
        "Planed DTC Delivery"
    ]
    
    warehouse_label_map = {
        "Planed DTC Delivery":  "Direct-to-customer",
        "Planed GES-RYD":       "GES-Riyadh",
        "Planed JDC":           "Jeddah DC",
        "Planed KDC":           "Dammam DC",
        "Planed LSC":           "LSC-Ryd",
        "Planed LSC-JED":       "LSC-JED",
        "Planed RDC":           "Riyadh DC"
    }
    
    # Sort by predefined order, then fallback to alphabetical
    plan_rows = sorted(matrix.keys(), key=lambda x: warehouse_order.index(x) if x in warehouse_order else x)
    brand_cols = sorted(all_brands)
    
    table_data = []
    for ps in plan_rows:
        label = warehouse_label_map.get(ps, ps)  # use alias or original
        row = [label]  # display label instead of raw status
        total = 0
        for brand in brand_cols:
            count = matrix[ps].get(brand, 0)
            row.append(count)
            total += count
        row.append(total)
        table_data.append(row)
    
    return table_data, brand_cols

# POD x Brand
def compute_pod_by_brand_only_delivered(brands=None, months=None, categories=None, shp_months=None, sel_shp=None, sel_po=None):
    """
    Returns a matrix of the form: { POD: { brand: count, ... }, ... }
    Only includes containers whose latest actual status is in DELIVERED_STATUSES and POD is not null.
    """
    model = Session()
    S   = RFT_StatusHistory
    C   = RFT_Container
    SP  = RFT_ShipmentPOLine
    POL = RFT_PurchaseOrderLine
    PO  = RFT_PurchaseOrder
    SHP = RFT_Shipment

    # Step A: Latest actual container status
    latest_actual = (
        model.query(S.EntityID.label("ContainerID"), func.max(S.StatusDate).label("maxdate"))
        .filter(S.EntityType == literal("Container"))
        .group_by(S.EntityID)
        .subquery()
    )

    latest_actual_status = (
        model.query(latest_actual.c.ContainerID, S.Status.label("actual_status"))
        .join(S, and_(
            S.EntityType == literal("Container"),
            S.EntityID   == latest_actual.c.ContainerID,
            S.StatusDate == latest_actual.c.maxdate
        ))
        .filter(S.Status.in_(DELIVERED_STATUSES))
        .subquery()
    )

    # Step B: Join and filter
    pairs = (
        model.query(
            SHP.POD,
            PO.Brand,
            func.count(func.distinct(C.ContainerID)).label("ct")
        )
        .join(latest_actual_status, latest_actual_status.c.ContainerID == C.ContainerID)
        .join(SP, SP.ShipmentID == C.ShipmentID)
        .join(SHP, SHP.ShipmentID == SP.ShipmentID)
        .join(POL, POL.POLineID == SP.POLineID)
        .join(PO, PO.POID == POL.POID)
        .outerjoin(RFT_CategoriesMappingMain, POL.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        .filter(
            SHP.POD.isnot(None),
            *( [PO.Brand.in_(brands)] if brands else [] ),
            *( [func.format(PO.PODate,'yyyy-MM').in_(months)] if months else [] ),
            *( [func.format(SHP.CreatedDate,'yyyy-MM').in_(shp_months)] if shp_months else [] ),
            *( [RFT_CategoriesMappingMain.CatName.in_(categories)] if categories else [] ),
            *( [or_(
                SHP.ShipmentNumber == sel_shp,
                SHP.BLNumber == sel_shp
            )] if sel_shp else [] ),
            *( [PO.POID == sel_po] if sel_po else [] )
        )
        .group_by(SHP.POD, PO.Brand)
        .all()
    )

    # Step C: Pivot into table
    matrix = defaultdict(lambda: defaultdict(int))
    all_brands = set()

    for pod, brand, ct in pairs:
        matrix[pod][brand] += ct
        all_brands.add(brand)

    pod_rows = sorted(matrix.keys())
    brand_cols = sorted(all_brands)

    table_data = []
    for pod in pod_rows:
        row = [pod]
        total = 0
        for brand in brand_cols:
            count = matrix[pod].get(brand, 0)
            row.append(count)
            total += count
        row.append(total)
        table_data.append(row)

    return table_data, brand_cols

# ATA-Wh x Month
def compute_monthly_dtc_vs_total(plan_prefixes, brands=None, months=None, categories=None, shp_months=None, sel_shp=None, sel_po=None):
    """
    Returns a matrix:
        {
            "DTC": { "2025-01": 30, "2025-02": 9, ... },
            "Total (excluding DTC)": { "2025-01": 10, "2025-02": 5, ... }
        }
    """
    model = Session()
    S = RFT_StatusHistory
    C = RFT_Container
    SP = RFT_ShipmentPOLine
    POL = RFT_PurchaseOrderLine
    PO = RFT_PurchaseOrder
    SM = RFT_StatusManagement
    SHP = RFT_Shipment

    # Get latest planned-container statuses
    latest_plan = (
        model.query(S.EntityID.label("ContainerID"), func.max(S.StatusDate).label("maxdate"))
        .filter(S.EntityType == literal("Planed-Container"))
        .group_by(S.EntityID)
        .subquery()
    )

    latest_plan_status = (
        model.query(latest_plan.c.ContainerID, S.Status.label("plan_status"))
        .join(S, and_(
            S.EntityType == literal("Planed-Container"),
            S.EntityID == latest_plan.c.ContainerID,
            S.StatusDate == latest_plan.c.maxdate
        ))
        .subquery()
    )

    # Step A: Latest actual container status
    latest_actual = (
        model.query(S.EntityID.label("ContainerID"), func.max(S.StatusDate).label("maxdate"))
        .filter(S.EntityType == literal("Container"))
        .group_by(S.EntityID)
        .subquery()
    )

    latest_actual_status = (
        model.query(latest_actual.c.ContainerID, S.Status.label("actual_status"))
        .join(S, and_(
            S.EntityType == literal("Container"),
            S.EntityID   == latest_actual.c.ContainerID,
            S.StatusDate == latest_actual.c.maxdate
        ))
        .filter(S.Status.in_(DELIVERED_STATUSES))
        .subquery()
    )
    
    # Get allowed plan statuses
    allowed_plan_set = set()
    for prefix in plan_prefixes:
        results = model.query(SM.StatusName).filter(
            SM.Level == "Container Level",
            SM.StatusName.like(f"{prefix}%")
        ).all()
        allowed_plan_set.update(r[0] for r in results)

    if not allowed_plan_set:
        return {}, []

    # Main query
    rows = (
        model.query(
            func.cast(func.DATEFROMPARTS(func.YEAR(C.ATAWH), func.MONTH(C.ATAWH), 1), Date).label('month_raw'),
            latest_plan_status.c.plan_status,
            func.count(func.distinct(C.ContainerID))
        )
        .join(latest_plan_status, latest_plan_status.c.ContainerID == C.ContainerID)
        .join(latest_actual_status, latest_actual_status.c.ContainerID == C.ContainerID)
        # .join(latest_plan_status, latest_plan_status.c.ContainerID == C.ContainerID)
        .join(SP, SP.ShipmentID == C.ShipmentID)
        .join(POL, POL.POLineID == SP.POLineID)
        .join(PO, PO.POID == POL.POID)
        .join(SHP, SHP.ShipmentID == SP.ShipmentID)
        .outerjoin(RFT_CategoriesMappingMain, POL.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        .filter(
            latest_plan_status.c.plan_status.in_(allowed_plan_set),
            C.ATAWH != None,
            *( [PO.Brand.in_(brands)] if brands else [] ),
            *( [func.format(PO.PODate,'yyyy-MM').in_(months)] if months else [] ),
            *( [func.format(SHP.CreatedDate,'yyyy-MM').in_(shp_months)] if shp_months else [] ),
            *( [RFT_CategoriesMappingMain.CatName.in_(categories)] if categories else [] ),
            *( [or_(
                SHP.ShipmentNumber == sel_shp,
                SHP.BLNumber == sel_shp
            )] if sel_shp else [] ),
            *( [PO.POID == sel_po] if sel_po else [] )
        )
        .group_by(C.ATAWH, latest_plan_status.c.plan_status)
        .all()
    )

    matrix = defaultdict(lambda: defaultdict(int))
    month_set = set()
    
    
    # Step 3: Add hardcoded data (convert keys to datetime)
    hardcoded_data = {
        "Jan-25": {"Total (excluding DTC)": 207, "DTC": 7},
        "Feb-25": {"Total (excluding DTC)": 145, "DTC": 6},
        "Mar-25": {"Total (excluding DTC)": 187, "DTC": 3},
        "Apr-25": {"Total (excluding DTC)": 181, "DTC": 16},
        "May-25": {"Total (excluding DTC)": 170, "DTC": 93},
    }
    
    # Prepare hardcoded month set to skip during query data merge
    hardcoded_months = {
        datetime.strptime(k, "%b-%y").date() for k in hardcoded_data
    }

    # Step 2: Add queried rows unless already in hardcoded data
    for raw_month, plan_status, count in rows:
        if raw_month is None:
            continue
        raw_month = raw_month.date() if isinstance(raw_month, datetime) else raw_month
        if raw_month in hardcoded_months:
            continue  # skip overwriting hardcoded values
        month_set.add(raw_month)
        if plan_status == "Planed DTC Delivery":
            matrix["DTC"][raw_month] += count
        else:
            matrix["Total (excluding DTC)"][raw_month] += count
    
    
    for str_month, values in hardcoded_data.items():
        dt_month = datetime.strptime(str_month, "%b-%y").date()
        month_set.add(dt_month)
        for label, count in values.items():
            matrix[label][dt_month] += count
    
    # Step 4: Sort and format months
    sorted_months_raw = sorted(month_set)
    sorted_months = [dt.strftime('%b-%y') for dt in sorted_months_raw]
    
    # Step 5: Build final table
    table_data = []
    for label in ["Total (excluding DTC)", "DTC"]:
        row = [label]
        for m in sorted_months_raw:
            row.append(matrix[label].get(m, 0))
        table_data.append(row)
    
    return table_data, sorted_months


####################          END            #####################
####################  Computation functions  #####################




#########################################################################################
######################## |HELPER FUNCTIONS| #############################################
#########################################################################################

def get_table_metadata(model_class, rows, friendly_names=None):
    # from sqlalchemy import inspect, Date, DateTime, Integer, Numeric

    mapper = inspect(model_class)
    cols = []
    # Step A: basic schema
    for col in mapper.columns:
        name = col.key
        default_label = name.replace('_',' ').title()
        cols.append({
            "name":  name,
            "label": (friendly_names or {}).get(name, default_label),
            "type":  type(col.type).__name__,
        })

    # Step B: gather distinct values
    distinct = defaultdict(set)
    for r in rows:
        for c in cols:
            distinct[c["name"]].add(getattr(r, c["name"]))

    # Step C: decide filter_type + options
    for c in cols:
        dtype = c["type"]
        vals  = {v for v in distinct[c["name"]] if v is not None}

        # If it's a "small" enum‑like set, make a select
        if dtype in ("String",) and 1 < len(vals) <= 50:
            c["filter_type"] = "select"
            c["options"]     = sorted(vals)

        # Numbers get range inputs
        elif dtype in ("Integer","Numeric"):
            c["filter_type"] = "text"

        # Dates get date inputs
        elif dtype in ("Date","DateTime"):
            c["filter_type"] = "date"

        # Everything else falls back to text search
        else:
            c["filter_type"] = "text"

    return cols

def generate_unique_shipment_number():
    while True:
        random_number = random.randint(10000000, 999999999)
        shipment_number = f"RFT{random_number}"
        exists = model.query(RFT_Shipment).filter_by(ShipmentNumber=shipment_number).first()
        if not exists:
            return shipment_number

def export_to_excel(sheet_name, table_view): # Function to export to excel with formating
    # Create a workbook and worksheet
    df = pd.DataFrame.from_dict(table_view)
    wb = Workbook()
    ws = wb.active

    # Add the table headers from the DataFrame columns
    header = df.columns.tolist()
    ws.append(header)

    # Apply formatting to the table headers
    bold_font = Font(bold=True)
    all_borders = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    center_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    for cell in ws[1]:
        cell.font = bold_font
        cell.border = all_borders
        cell.alignment = center_alignment

    for row in df.itertuples(index=False):
        ws.append(row)

    # Save the workbook to a BytesIO object
    excel_file = BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)

    # Return the Excel file as a response
    return send_file(excel_file, mimetype='application/vnd.ms-excel', as_attachment=True, download_name=f"{sheet_name}.xlsx")

def etl_purchase_orders(batch_id):
  # preload brand & category maps
  brand_rows = model.query(
      RFT_BrandTypes.BrandType,
      RFT_BrandTypes.BrandName
  ).all()
  brand_map = { t: name for t, name in brand_rows }

  cat_rows = model.query(
      RFT_CategoriesMappingMain.CatCode,
      RFT_CategoriesMappingMain.ID
  ).all()
  cat_map = { code: cid for code, cid in cat_rows }

  # fetch all uploads for this batch
  uploaded = (
      model.query(RFT_PurchaseOrderUpload)
            .filter_by(UploadBatch=batch_id)
            .all()
  )
  if not uploaded:
      return

  # find which POs already exist in the DB
  po_nums = { u.PurchaseOrder for u in uploaded }
  existing_pos = {
      p for (p,) in
      model.query(RFT_PurchaseOrder.PONumber)
            .filter(RFT_PurchaseOrder.PONumber.in_(po_nums))
            .all()
  }

  # cache for newly created POs (so we can add multiple lines)
  po_cache = {}

  for u in uploaded:
    po_num = u.PurchaseOrder

    # skip any uploads whose PO already exists
    if po_num in existing_pos:
        continue

    # determine brand & category
    real_brand = brand_map.get(u.Type, u.MdseCat)
    prefix     = (u.MdseCat or "")[:3].upper()
    cat_id     = cat_map.get(prefix)

    # create or reuse the PO object
    po = po_cache.get(po_num)
    if not po:
        po = RFT_PurchaseOrder(
            PONumber = po_num,
            Supplier = u.VendorSupplyingSite,
            Brand    = real_brand,
            PODate   = u.DocDate,
            Site     = u.Site
        )
        model.add(po)
        model.flush()   # so po.POID is populated
        po_cache[po_num] = po

    # add the PO line for this upload record
    line = RFT_PurchaseOrderLine(
        POID              = po.POID,
        SapItemLine       = u.Item,
        Article           = u.Article,
        Qty               = u.QtyToBeDelivered,
        BalanceQty        = u.QtyToBeDelivered,
        TotalValue        = u.ValueToBeDelivered,
        CategoryMappingID = cat_id,
        LastUpdatedBy     = session.get('username','system')
    )
    model.add(line)

  model.commit()

def get_countries():
    """
    Returns a list of dicts like:
      [ {'code':'AF', 'name':'Afghanistan'}, … ]
    """
    return [
        {'code': c.alpha_2, 'name': c.name}
        for c in pycountry.countries
    ]

ExpenseRow = namedtuple(
    "ExpenseRow",
    ["brand", "cat", "article", "shipment", "delivery_date", "total_expense"]
)

def fetch_expense_data(brands=None, start_date=None, end_date=None):
    S       = RFT_StatusHistory
    SH      = RFT_Shipment
    SP      = RFT_ShipmentPOLine
    PO      = RFT_PurchaseOrder
    POLine  = RFT_PurchaseOrderLine
    CatMap  = RFT_CategoriesMappingMain

    # ── A) Latest delivery date per Shipment ─────────────────────────────
    latest = (
      model.query(
        S.EntityID.label("ShipmentID"),
        func.max(S.StatusDate).label("delivery_date")
      )
      .filter(
        S.EntityType  == literal("Shipment"),
        S.Status.in_(DELIVERED_STATUSES)
      )
      .group_by(S.EntityID)
      .subquery()
    )

    # B) shipment-level aggregates: total_cost & total_qty
    total_cost = (
        func.coalesce(func.sum(SH.FreightCost),               0) +
        func.coalesce(func.sum(SH.CustomDuties),              0) +
        func.coalesce(func.sum(SH.SaberSADDAD),               0) +
        func.coalesce(func.sum(SH.DemurrageCharges),          0) +
        func.coalesce(func.sum(SH.Penalties),                 0) +
        func.coalesce(func.sum(SH.OtherCharges),              0) +
        func.coalesce(func.sum(SH.YardCharges),               0) +
        func.coalesce(func.sum(SH.ClearanceTransportCharges), 0) +
        func.coalesce(func.sum(SH.DO_Port_Charges),           0) +
        func.coalesce(func.sum(SH.MAWANICharges),             0) +
        func.coalesce(func.sum(SH.InspectionCharges),         0)
    ).label("total_cost")

    shipment_costs = (
      model.query(
        SH.ShipmentID,
        total_cost,
        func.sum(SP.QtyShipped).label("total_qty"),
        latest.c.delivery_date
      )
      .join(latest, latest.c.ShipmentID == SH.ShipmentID)
      .join(SP,      SP.ShipmentID    == SH.ShipmentID)
      .group_by(SH.ShipmentID, latest.c.delivery_date)
      .subquery()
    )

    # expense_per_unit = total_cost / total_qty
    expense_per_unit = (
      (shipment_costs.c.total_cost / 
       func.nullif(shipment_costs.c.total_qty, 0))
    ).label("expense_per_unit")

    # ── C) Category expression for PO lines ──────────────────────────────
    # If SubCat exists, concat; else just CatName
    cat1 = func.coalesce(CatMap.CatDesc, literal(""))
    cat2 = func.coalesce(CatMap.SubCat,   literal(""))
    trim1 = func.ltrim(func.rtrim(cat1))
    trim2 = func.ltrim(func.rtrim(cat2))
    same_text = func.lower(trim1) == func.lower(trim2)

    cat_expr  = case(
      (same_text, trim1),
      else_=func.concat(trim1, literal(" "), trim2)
    ).label("cat")

    # E) main query: one row per (PO-line × delivered-shipment)
    q = (
      model.query(
        PO.Brand.label("brand"),
        cat_expr,
        POLine.Article.label("article"),
        SH.ShipmentNumber.label("shipment"),
        shipment_costs.c.delivery_date,
        # total expense for this article = qty_shipped × expense_per_unit
        (SP.QtyShipped * expense_per_unit).label("total_expense")
      )
      .join(POLine, PO.POID == POLine.POID)
      .outerjoin(CatMap,
                 POLine.CategoryMappingID == CatMap.ID)
      .join(SP, POLine.POLineID == SP.POLineID)
      .join(shipment_costs,
            shipment_costs.c.ShipmentID == SP.ShipmentID)
      .join(SH, SH.ShipmentID == shipment_costs.c.ShipmentID)
    )

    # F) apply filters
    if brands:
        q = q.filter(PO.Brand.in_(brands))
    if start_date:
        q = q.filter(shipment_costs.c.delivery_date >= start_date)
    if end_date:
        q = q.filter(shipment_costs.c.delivery_date <= end_date)

    # G) materialize
    return [
      ExpenseRow(*row)
      for row in q.order_by(
        PO.Brand, cat_expr, POLine.Article,
        shipment_costs.c.delivery_date
      ).all()
    ]

def build_expense_columns(rows):
    """
    Pivot the flat list of ExpenseRow into a wide table:
      [brand, cat, article, 'SHIP# (YYYY-MM-DD)', ...]
    """
    pivot = defaultdict(lambda: {"brand":None, "cat":None, "article":None})
    for r in rows:
        key = (r.brand, r.cat, r.article)
        grp = pivot[key]
        grp["brand"], grp["cat"], grp["article"] = r.brand, r.cat, r.article

        date_str = r.delivery_date.strftime("%Y-%m-%d") if r.delivery_date else "Unknown"
        col_name = f"{r.shipment} ({date_str})"
        grp[col_name] = float(r.total_expense or 0)

    wide_rows = list(pivot.values())

    # build column metadata
    columns = [
      {"name":"brand",   "label":"Brand",    "type":"String"},
      {"name":"cat",     "label":"Category", "type":"String"},
      {"name":"article", "label":"Article",  "type":"String"},
    ]

    if wide_rows:
      # any extra keys beyond brand/cat/article are shipment columns
      shipment_cols = sorted(
        k for k in wide_rows[0].keys()
        if k not in {"brand","cat","article"}
      )
      for col in shipment_cols:
        columns.append({
          "name":  col,
          "label": col,
          "type":  "Numeric"
        })

    return wide_rows, columns

def export_shipment_expense_report(shipment_id=None):
  """
  Export an Excel report of per-article expense.
  If shipment_id is provided, exports that shipment alone;
  otherwise exports all shipments in one sheet.
  """
  # 1) discover your cost columns
  cost_cols = cost_columns()  # e.g. ["FreightCost", "CustomDuties", …]

  # 1) one SUM() per cost column, labeled with its own name
  sum_exprs = [
      func.coalesce(func.sum(getattr(RFT_Shipment, col)), 0).label(col)
      for col in cost_cols
  ]

  # 2) build TotalExpense by SQL-adding *those* labeled sums
  #    reduce(add, sum_exprs) → e.g. "FreightCost + CustomDuties + …"
  total_expr = reduce(add, sum_exprs).label("TotalExpense")

  # 3) ship-level subquery now becomes
  ship_tot = (
    model.query(
      RFT_Shipment.ShipmentID,
      RFT_Shipment.ShipmentNumber,
      RFT_Shipment.BLNumber,
      RFT_PurchaseOrder.Brand.label("Brand"),
      *sum_exprs,
      total_expr
    )
    .join(RFT_ShipmentPOLine,   RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID)
    .join(RFT_PurchaseOrderLine, RFT_PurchaseOrderLine.POLineID == RFT_ShipmentPOLine.POLineID)
    .join(RFT_PurchaseOrder,     RFT_PurchaseOrder.POID   == RFT_PurchaseOrderLine.POID)
    .group_by(
      RFT_Shipment.ShipmentID,
      RFT_Shipment.ShipmentNumber,
      RFT_Shipment.BLNumber,
      RFT_PurchaseOrder.Brand,
      *[getattr(RFT_Shipment, col) for col in cost_cols]
    )
    .subquery()
  )
  # 5) total shipped quantity per shipment
  qty_tot = (
    model.query(
      RFT_ShipmentPOLine.ShipmentID,
      func.coalesce(func.sum(RFT_ShipmentPOLine.QtyShipped), 0).label("ShipmentQty")
    )
    .group_by(RFT_ShipmentPOLine.ShipmentID)
    .subquery()
  )

  # 6) line-level join
  q = (
    model.query(
      ship_tot.c.ShipmentNumber,
      ship_tot.c.Brand,
      ship_tot.c.BLNumber,
      RFT_PurchaseOrder.PONumber,
      RFT_PurchaseOrderLine.SapItemLine.label("SAPLineItem"),
      RFT_PurchaseOrderLine.Article,
      RFT_ShipmentPOLine.QtyShipped,
      *[ship_tot.c[col] for col in cost_cols],
      ship_tot.c.TotalExpense,
      qty_tot.c.ShipmentQty
    )
    .join( RFT_Shipment,          RFT_Shipment.ShipmentID == ship_tot.c.ShipmentID )
    .join( RFT_ShipmentPOLine,    RFT_ShipmentPOLine.ShipmentID == ship_tot.c.ShipmentID )
    .join( RFT_PurchaseOrderLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID )
    .join( RFT_PurchaseOrder,     RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID )
    .join( qty_tot,               qty_tot.c.ShipmentID == ship_tot.c.ShipmentID )
  )
  if shipment_id:
      q = q.filter(ship_tot.c.ShipmentID.in_(shipment_id))

  rows = q.all()

  # 7) to DataFrame
  df = pd.DataFrame([r._asdict() for r in rows])
  
  cost_cols.append("TotalExpense") # Add this in the last. will find a batter solution
  
  # 8) compute per-article prorated cost columns
  for col in cost_cols:
      per_col = col + "PerArticle"
      df[per_col] = (
          (df[col] / df["ShipmentQty"]).astype(float) * df["QtyShipped"].astype(float)
      ).fillna(0).round(2)

  # 9) select only the final columns
  out_cols = (
    ["BLNumber","ShipmentNumber","Brand","PONumber","SAPLineItem","Article","QtyShipped"]
    + [c + "PerArticle" for c in cost_cols]
  )
  df = df[out_cols]

  # 10) write the Excel (same as before)
  output = BytesIO()
  with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
      sheet = "ArticleExp"
      df.to_excel(writer, sheet_name=sheet, index=False, startrow=1)

      wb = writer.book
      ws = writer.sheets[sheet]

      # write the totals row in row 0
      ws.write(0, 0, f"Brand: {df['Brand'].iat[0]}")
      ws.write(0, 6, f"TotalQty: {df['QtyShipped'].sum()}")
      col_idx = 7
      for c in cost_cols:
          total = df[c + "PerArticle"].sum()
          ws.write(0, col_idx, f"Total: {total:.2f}")
          col_idx += 1

      # adjust column widths
      widths = {"A":15,"B":15,"C":12,"D":12,"E":12,"F":15,"G":10}
      for i, _ in enumerate(cost_cols, start=7):
          widths[chr(65 + i)] = 18
      for col, w in widths.items():
          ws.set_column(f"{col}:{col}", w)

  output.seek(0)
  # return send_file(
  #     output,
  #     as_attachment=True,
  #     download_name="Shipment_expense_report.xlsx",
  #     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  # )
  
  resp = make_response(send_file(
      output,
      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      as_attachment=True,
      download_name='shipments.xlsx'
  ))
  # tack on the header your JS can read
  resp.headers['X-Redirect-URL'] = url_for('main.createdShipments')
  return resp

# PO wise report front end helpers SAME used in below export func
def build_po_report_df(shipment_numbers=None):
    # A) filter shipments if requested
  ship_q = model.query(RFT_Shipment)
  if shipment_numbers:
      ship_q = ship_q.filter(RFT_Shipment.ShipmentNumber.in_(shipment_numbers))
  ship_q = ship_q.subquery()

  # B) per‐shipment totals for each cost category
  ship_totals = (
      model.query(
          ship_q.c.ShipmentID,
          ship_q.c.ShipmentNumber,
          ship_q.c.BLNumber,
          *(func.coalesce(func.sum(getattr(ship_q.c, col)), 0).label(col) for col in [
              "FreightCost","CustomDuties","SaberSADDAD","DemurrageCharges",
              "Penalties","OtherCharges","DO_Port_Charges",
              "ClearanceTransportCharges","YardCharges"
          ])
      )
      .group_by(ship_q.c.ShipmentID, ship_q.c.ShipmentNumber, ship_q.c.BLNumber)
      .subquery()
  )

  # C) total shipped qty per shipment
  shipped_qty = (
      model.query(
          RFT_ShipmentPOLine.ShipmentID,
          func.coalesce(func.sum(RFT_ShipmentPOLine.QtyShipped), 0).label("TotalShippedQty")
      )
      .group_by(RFT_ShipmentPOLine.ShipmentID)
      .subquery()
  )

  # D) delivered qty per PO‐line
  H1 = aliased(RFT_StatusHistory)
  latest_cont = (
      model.query(
          H1.EntityID.label("ContainerID"),
          func.max(H1.StatusDate).label("MaxDate")
      )
      .filter(H1.EntityType=="Container")
      .group_by(H1.EntityID)
      .subquery()
  )
  H2 = aliased(RFT_StatusHistory)
  cont_status = (
      model.query(latest_cont.c.ContainerID, H2.Status)
      .join(H2,
            (H2.EntityID==latest_cont.c.ContainerID)&
            (H2.StatusDate==latest_cont.c.MaxDate)&
            (H2.EntityType=="Container")
      )
      .subquery()
  )
  delivered_qty = (
      model.query(
          RFT_ContainerLine.ShipmentPOLineID,
          func.coalesce(func.sum(RFT_ContainerLine.QtyInContainer), 0).label("DeliveredQty")
      )
      .join(RFT_Container, RFT_ContainerLine.ContainerID==RFT_Container.ContainerID)
      .join(cont_status, cont_status.c.ContainerID==RFT_Container.ContainerID)
      .filter(cont_status.c.Status.in_(DELIVERED_STATUSES))
      .group_by(RFT_ContainerLine.ShipmentPOLineID)
      .subquery()
  )

  # E) original PO‐line qty
  po_total_line = (
      model.query(
          RFT_PurchaseOrderLine.POLineID,
          RFT_PurchaseOrderLine.Qty.label("PoTotalQty")
      )
      .subquery()
  )

  # F) pull each PO-line’s prorated costs and quantities
  rows = (
      model.query(
          RFT_PurchaseOrder.PONumber.label("PONumber"),
          RFT_PurchaseOrder.Brand.label("Brand"),
          RFT_ShipmentPOLine.QtyShipped,
          po_total_line.c.PoTotalQty,
          func.coalesce(delivered_qty.c.DeliveredQty, 0).label("DeliveredQty"),
          # prorated costs per line
          (ship_totals.c.FreightCost  / shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("FreightCostPerLine"),
          (ship_totals.c.CustomDuties/ shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("CustomDutiesPerLine"),
          (ship_totals.c.SaberSADDAD / shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("SaberSADDADPerLine"),
          (ship_totals.c.DemurrageCharges/ shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("DemurrageChargesPerLine"),
          (ship_totals.c.Penalties   / shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("PenaltiesPerLine"),
          (ship_totals.c.OtherCharges / shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("OtherChargesPerLine"),
          (ship_totals.c.DO_Port_Charges/ shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("DO_Port_ChargesPerLine"),
          (ship_totals.c.ClearanceTransportCharges/ shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("ClearanceTransportChargesPerLine"),
          (ship_totals.c.YardCharges  / shipped_qty.c.TotalShippedQty * RFT_ShipmentPOLine.QtyShipped).label("YardChargesPerLine"),
      )
      .select_from(ship_totals)
      .join(RFT_ShipmentPOLine,   ship_totals.c.ShipmentID == RFT_ShipmentPOLine.ShipmentID)
      .join(RFT_PurchaseOrderLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)
      .join(RFT_PurchaseOrder,     RFT_PurchaseOrderLine.POID   == RFT_PurchaseOrder.POID)
      .outerjoin(delivered_qty, delivered_qty.c.ShipmentPOLineID == RFT_ShipmentPOLine.ShipmentPOLineID)
      .join(shipped_qty, shipped_qty.c.ShipmentID == ship_totals.c.ShipmentID)
      .join(po_total_line, po_total_line.c.POLineID == RFT_PurchaseOrderLine.POLineID)
      .all()
  )

  # G) line-level DataFrame
  df_lines = pd.DataFrame([r._asdict() for r in rows])
  df_lines["BalanceQty"] = df_lines["PoTotalQty"] - df_lines["DeliveredQty"]
  money_cols = [c for c in df_lines.columns if c.endswith("PerLine")]
  df_lines[money_cols] = df_lines[money_cols].astype(float).round(2)

    # — H) build a master list of all POs (even those without shipments), now including Total PO value
  po_master = (
      model.query(
          RFT_PurchaseOrder.Brand.label("Brand"),
          RFT_PurchaseOrder.PONumber.label("PONumber"),
          func.sum(RFT_PurchaseOrderLine.Qty).label("PoTotalQty"),
          # assume you have a unit‐price column on the PO line:
          func.sum(RFT_PurchaseOrderLine.TotalValue).label("TotalValue")
      )
      .join(RFT_PurchaseOrderLine, RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID)
      .group_by(RFT_PurchaseOrder.Brand, RFT_PurchaseOrder.PONumber)
      .all()
  )
  df_po = pd.DataFrame([r._asdict() for r in po_master])

  # — I) aggregate line‐level to PO‐level (as before)
  agg_ship = {
      "QtyShipped": "sum",
      "DeliveredQty": "sum",
      **{col: "sum" for col in money_cols}
  }
  df_ship = (
      df_lines
      .groupby(["Brand","PONumber"], as_index=False)
      .agg(agg_ship)
  )

  # — J) merge, fill zeros, and compute the revised BalanceQty
  df = df_po.merge(df_ship, on=["Brand","PONumber"], how="left")
  df.fillna({**{"QtyShipped":0,"DeliveredQty":0}, **{col:0 for col in money_cols}}, inplace=True)


  # 2) BalanceQty = PoTotalQty - ((REMOVED: QtyShipped +) DeliveredQty)
  df["BalanceQty"] = df["PoTotalQty"] - (df["QtyShipped"])

  # 3) Total Cost = sum of all cost columns
  df["TotalCost"] = df[money_cols].sum(axis=1)

  # 4) % delivered vs PO
  df["FL%(PO VS Delivered)"] = df["DeliveredQty"] / df["PoTotalQty"] * 100

  # 5) Overhead Cost % = TotalCost / TotalValue
  df["OH-Cost %"] = df["TotalCost"].astype(int) / df["TotalValue"].astype(int) 

  # — L) rebuild two-row header (same as before, just include the new columns)
  header1 = {
      "Brand":"", 
      "PONumber":"",
      "TotalValue":  f"{df['TotalValue'].sum():,.2f} USD",
      "PoTotalQty": f" {df['PoTotalQty'].sum()}",
      "QtyShipped": f"{df['QtyShipped'].sum()}",
      "DeliveredQty":f"{df['DeliveredQty'].sum()}",
      "BalanceQty": f"{df['BalanceQty'].sum()}",
      "TotalCost":   f"{df['TotalCost'].sum():,.2f}",
      **{c:"" for c in  ["FL%(PO VS Delivered)","OH-Cost %"] + money_cols }
  }
  df = pd.concat([pd.DataFrame([header1]), df], ignore_index=True, sort=False)

  # — M) final column order
  final_cols = [
      "Brand","PONumber", "TotalValue","PoTotalQty", "QtyShipped",
      "DeliveredQty","BalanceQty","TotalCost"
  ] + ["FL%(PO VS Delivered)","OH-Cost %"] + money_cols
  df = df[final_cols]
  
  return df

def build_po_columns(df):
    """
    df: a pandas.DataFrame whose columns are exactly the
    final_cols you want to show in the table.
    Returns (list_of_dicts, list_of_column_defs)
    """
    # rows as JSON-serializable dicts:
    rows = df.to_dict(orient="records")

    # build DataTables‐style column defs
    columns = []
    COL_LABELS = {
      "PONumber"                          :"PO Number",
      "QtyShipped"                        :"Qty Shipped",
      "PoTotalQty"                        :"PO Qty",
      "TotalValue"                        :"PO Value (USD)",
      "DeliveredQty"                      :"Delivered Qty",
      "BalanceQty"                        :"Balance Qty",
      "TotalCost"                         :"Total Cost",
      "FL%(PO VS Delivered)"              :"FL%(PO VS Delivered)",
      "OH-Cost %"                         :"Overhead Cost %",
      "FreightCostPerLine"                :'Freight Cost',
      "CustomDutiesPerLine"               :'Custom Duties',
      "SaberSADDADPerLine"                :'Saber SADDAD',
      "DemurrageChargesPerLine"           :'Demurrage Charges',
      "PenaltiesPerLine"                  :'Penalties',
      "OtherChargesPerLine"               :'Other Charges',
      "DO_Port_ChargesPerLine"            :'DO Port Charges',
      "ClearanceTransportChargesPerLine"  :'Clearance Transport',
      "YardChargesPerLine"                :'Yard Charges'
    }
    for col in df.columns:
        # choose type based on dtype
        col_type = "numeric" if pd.api.types.is_numeric_dtype(df[col]) else "string"
        columns.append({
            "name":  col,
            "label": COL_LABELS.get(col, col),
            "type":  col_type
        })

    return rows, columns

# EXPORT PO wise report of expense and fulfillment and costs
def export_po_report(shipment_numbers=None):
  
  df = build_po_report_df()

  final_cols = [
      "Brand","PONumber", "TotalValue","PoTotalQty", "QtyShipped",
      "DeliveredQty","BalanceQty","TotalCost"
  ] + ["FL%(PO VS Delivered)","OH-Cost %"]
  
  # — N) write out to Excel *with* conditional formatting
  out = BytesIO()
  with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
      df.to_excel(writer, index=False, sheet_name="PO_report")
      workbook  = writer.book
      worksheet = writer.sheets["PO_report"]

      # formats
      green_fmt = workbook.add_format({'font_color': 'green'})
      red_fmt   = workbook.add_format({'font_color': 'red'})
      # define a percent format (two decimals, e.g. 12.34%)
      percent_fmt = workbook.add_format({'num_format': '0.00%'})

      # locate zero-based column indexes
      fl_col = final_cols.index("FL%(PO VS Delivered)")
      oh_col = final_cols.index("OH-Cost %")
      
      # apply the percent format to the entire column (rows 1..n)
      worksheet.set_column(fl_col, fl_col, 12, percent_fmt)
      worksheet.set_column(oh_col, oh_col, 12, percent_fmt)

      nrows = len(df)  # includes header1 row
      # locate columns by index
      fl_col = final_cols.index("FL%(PO VS Delivered)")
      oh_col = final_cols.index("OH-Cost %")

      # FL% == 100 → green
      worksheet.conditional_format(
          2, fl_col, nrows, fl_col,
          {'type':'cell','criteria':'==','value':100,'format': green_fmt}
      )
      # OH-Cost % < 10 → green
      worksheet.conditional_format(
          2, oh_col, nrows, oh_col,
          {'type':'cell','criteria':'<','value':10,'format': green_fmt}
      )
      # OH-Cost % >= 10 → red
      worksheet.conditional_format(
          2, oh_col, nrows, oh_col,
          {'type':'cell','criteria':'>=','value':10,'format': red_fmt}
      )

  out.seek(0)
  return send_file(
      out,
      download_name="PO_report.xlsx",
      as_attachment=True,
      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  )



#########################################################################################
######################## |HELPER FUNCTIONS| #############################################
#########################################################################################


# Shipment export
# article expense report both are the same as of my current understanding 
# def export_shipment_expense_report(shipment_id=None):
#   """
#   Export an Excel report of per-article expense.
#   If shipment_id is provided, exports that shipment alone;
#   otherwise exports all shipments in one sheet.
#   """
  
#   # — A) build shipment-level totals subquery including each cost component + BLNumber + Brand:
#   ship_tot = (
#     model.query(
#       RFT_Shipment.ShipmentID,
#       RFT_Shipment.ShipmentNumber,
#       RFT_Shipment.BLNumber,
#       RFT_PurchaseOrder.Brand.label("Brand"),
#       func.coalesce(RFT_Shipment.FreightCost, 0).label("FreightCost"),
#       func.coalesce(RFT_Shipment.CustomDuties,0).label("CustomDuties"),
#       func.coalesce(RFT_Shipment.SaberSADDAD,0).label("SaberSADDAD"),
#       func.coalesce(RFT_Shipment.DemurrageCharges,0).label("DemurrageCharges"),
#       func.coalesce(RFT_Shipment.Penalties,0).label("Penalties"),
#       func.coalesce(RFT_Shipment.OtherCharges,0).label("OtherCharges"),
#       func.coalesce(RFT_Shipment.DO_Port_Charges,0).label("DO_Port_Charges"),
#       func.coalesce(RFT_Shipment.ClearanceTransportCharges,0).label("ClearanceTransportCharges"),
#       func.coalesce(RFT_Shipment.YardCharges,0).label("YardCharges"),
#       # total expense as sum of all above
#       (
#         func.coalesce(RFT_Shipment.FreightCost, 0)
#         + func.coalesce(RFT_Shipment.CustomDuties,0)
#         + func.coalesce(RFT_Shipment.SaberSADDAD,0)
#         + func.coalesce(RFT_Shipment.DemurrageCharges,0)
#         + func.coalesce(RFT_Shipment.Penalties,0)
#         + func.coalesce(RFT_Shipment.OtherCharges,0)
#         + func.coalesce(RFT_Shipment.DO_Port_Charges,0)
#         + func.coalesce(RFT_Shipment.ClearanceTransportCharges,0)
#         + func.coalesce(RFT_Shipment.YardCharges,0)
#       ).label("TotalExpense")
#     )
#     .join(RFT_ShipmentPOLine, RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID)
#     .join(RFT_PurchaseOrderLine, RFT_PurchaseOrderLine.POLineID == RFT_ShipmentPOLine.POLineID)
#     .join(RFT_PurchaseOrder, RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID)
#     .group_by(
#       RFT_Shipment.ShipmentID,
#       RFT_Shipment.ShipmentNumber,
#       RFT_Shipment.BLNumber,
#       RFT_PurchaseOrder.Brand,
#       RFT_Shipment.FreightCost,
#       RFT_Shipment.CustomDuties,
#       RFT_Shipment.SaberSADDAD,
#       RFT_Shipment.DemurrageCharges,
#       RFT_Shipment.Penalties,
#       RFT_Shipment.OtherCharges,
#       RFT_Shipment.DO_Port_Charges,
#       RFT_Shipment.ClearanceTransportCharges,
#       RFT_Shipment.YardCharges
#     )
#     .subquery()
#   )

#   # — B) total shipped quantity per shipment
#   qty_tot = (
#     model.query(
#       RFT_ShipmentPOLine.ShipmentID,
#       func.coalesce(func.sum(RFT_ShipmentPOLine.QtyShipped), 0).label("ShipmentQty")
#     )
#     .group_by(RFT_ShipmentPOLine.ShipmentID)
#     .subquery()
#   )

#   # — C) line-level details join both
#   q = model.query(
#       ship_tot.c.ShipmentNumber,
#       ship_tot.c.Brand,
#       ship_tot.c.BLNumber,
#       RFT_PurchaseOrder.PONumber,
#       RFT_PurchaseOrderLine.SapItemLine.label("SAPLineItem"),
#       RFT_PurchaseOrderLine.Article,
#       RFT_ShipmentPOLine.QtyShipped,
#       ship_tot.c.FreightCost,
#       ship_tot.c.CustomDuties,
#       ship_tot.c.SaberSADDAD,
#       ship_tot.c.DemurrageCharges,
#       ship_tot.c.Penalties,
#       ship_tot.c.OtherCharges,
#       ship_tot.c.DO_Port_Charges,
#       ship_tot.c.ClearanceTransportCharges,
#       ship_tot.c.YardCharges,
#       ship_tot.c.TotalExpense,
#       qty_tot.c.ShipmentQty
#   ).join(
#       RFT_Shipment, RFT_Shipment.ShipmentID == ship_tot.c.ShipmentID
#   ).join(
#       RFT_ShipmentPOLine, RFT_ShipmentPOLine.ShipmentID == ship_tot.c.ShipmentID
#   ).join(
#       RFT_PurchaseOrderLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID
#   ).join(
#       RFT_PurchaseOrder, RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID
#   ).join(
#       qty_tot, qty_tot.c.ShipmentID == ship_tot.c.ShipmentID
#   )

#   if shipment_id:
#       q = q.filter(ship_tot.c.ShipmentID == shipment_id)

#   rows = q.all()

#   # — D) build DataFrame
#   df = pd.DataFrame([r._asdict() for r in rows])

#   # compute per-article for each cost component
#   cost_cols = [
#     "FreightCost", "CustomDuties", "SaberSADDAD", "DemurrageCharges",
#     "Penalties","OtherCharges","DO_Port_Charges","ClearanceTransportCharges","YardCharges","TotalExpense"
#   ]
#   for col in cost_cols:
#     per_col = col + "PerArticle"
#     df[per_col] = ((df[col] / df["ShipmentQty"]) * df["QtyShipped"]).fillna(0).astype(float).round(2)

#   # — E) reorder & trim to only the columns you need
#   out_cols = [
#     "BLNumber", "ShipmentNumber","Brand","PONumber","SAPLineItem","Article","QtyShipped",
#   ] + [c + "PerArticle" for c in cost_cols]
#   df = df[out_cols]

#   # — F) write to Excel with a totals row at the top
#   output = BytesIO()
#   with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
#       sheet = "ArticleExp"
#       df.to_excel(writer, index=False, sheet_name=sheet, startrow=1)

#       wb  = writer.book
#       ws  = writer.sheets[sheet]

#       # write header for totals in row 0
#       # e.g. A1: "Brand: ADMIRAL", B1: "TotalQty: 1234", then each cost total
#       total_qty = df["QtyShipped"].sum()
#       totals = { f: df[f].sum() for f in df.columns if f.endswith("PerArticle") }
#       ws.write(0, 0, f"Brand: {df['Brand'].iat[0]}")
#       ws.write(0, 6, f"TotalQty: {total_qty}")
#       col_idx = 7
#       for k,v in totals.items():
#         ws.write(0, col_idx, f"Total: {v:.2f}")
#         col_idx += 1

#       # set column widths for readability
#       widths = {
#         "A":15, "B":15, "C":12, "D":12, "E":12, "F":15, "G":10, "O":31
#       }
#       # for the cost columns, give a bit more room
#       for i in range(7, 7+len(cost_cols)):
#         widths[chr(65+i)] = 18
#       for col, w in widths.items():
#         ws.set_column(f"{col}:{col}", w)

#       # you might also add conditional formatting, e.g. highlight low value rows:
#       # ws.conditional_format(1, 6, len(df), 6, {'type':'3_color_scale'})

#   output.seek(0)
#   return send_file(
#     output,
#     as_attachment=True,
#     download_name="Shipment_expense_report.xlsx",
#     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#   )


# Upcomming ETA
# def compute_upcoming_eta(mot, days_ahead: int = 7):
#   """
#   Return all shipments whose ETADestination is between now and now+days_ahead,
#   ordered by ETADestination ascending.
#   """
#   now = datetime.utcnow()
#   cutoff = now + timedelta(days=days_ahead)

#   q = (
#     model.query(
#         RFT_Shipment.ShipmentNumber   .label("shipment"),
#         RFT_Shipment.ETADestination   .label("eta"),
#         RFT_Shipment.OriginPort       .label("origin_port"),
#         func.count(RFT_Container.ContainerID).label("containers_num"),
#         RFT_Shipment.POD              .label("dest_country"),
#     )
#     # LEFT-join so shipments with zero containers will still show up as 0
#     .outerjoin(RFT_Shipment.containers)
#     .filter(
#         RFT_Shipment.ETADestination.isnot(None),
#         RFT_Shipment.ETADestination >= now,
#         RFT_Shipment.ETADestination <= cutoff,
#     )
#     # Any column in the SELECT list that isn’t aggregated must be GROUP BY’d
#     .group_by(
#         RFT_Shipment.ShipmentNumber,
#         RFT_Shipment.ETADestination,
#         RFT_Shipment.OriginPort,
#         RFT_Shipment.POD,
#     )
#     .order_by(RFT_Shipment.ETADestination)
#   )

#   return [
#       {
#           "shipment": shp,
#           "eta": eta,
#           "origin_port": op,
#           "containers_num": cn,
#           "dest_country": dc
#       }
#       for shp, eta, op, cn, dc in q.all()
#   ]




# def etl_purchase_orders(batch_id):
#     # preload your lookup maps
#     brand_rows = model.query(RFT_BrandTypes.BrandType, RFT_BrandTypes.BrandName).all()
#     brand_map  = { t:name for t,name in brand_rows }

#     cat_rows = model.query(
#         RFT_CategoriesMappingMain.CatCode,
#         RFT_CategoriesMappingMain.ID
#     ).all()
#     cat_map = { code:cid for code,cid in cat_rows }

#     uploaded = (
#       model.query(RFT_PurchaseOrderUpload)
#            .filter_by(UploadBatch=batch_id)
#            .all()
#     )

#     po_cache = {}

#     for u in uploaded:
#         po_num     = u.PurchaseOrder
#         real_brand = brand_map.get(u.Type, u.MdseCat)
#         prefix     = (u.MdseCat or "")[:3].upper()
#         cat_id     = cat_map.get(prefix)

#         # 1) either pull from cache or from DB
#         if po_num in po_cache:
#             po = po_cache[po_num]
#         else:
#             po = (
#               model.query(RFT_PurchaseOrder)
#                    .filter_by(PONumber=po_num)
#                    .one_or_none()
#             )
#             if not po:
#                 po = RFT_PurchaseOrder(
#                   PONumber=po_num,
#                   Supplier = u.VendorSupplyingSite,
#                   Brand    = real_brand,
#                   PODate   = u.DocDate,
#                 )
#                 model.add(po)
#                 model.flush()     # get po.POID
#             po_cache[po_num] = po

#         # 2) now create the line
#         line = RFT_PurchaseOrderLine(
#           POID              = po.POID,
#           SapItemLine       = u.Item,
#           Article           = u.Article,
#           Qty               = u.QtyToBeDelivered,
#           BalanceQty        = u.QtyToBeDelivered,
#           TotalValue        = u.ValueToBeDelivered,
#           CategoryMappingID = cat_id,
#           LastUpdatedBy     = session.get('username','system')
#         )
#         model.add(line)

#     model.commit()



# Article wise expense report
# def fetch_expense_data(brands=None, start_date=None, end_date=None):
#     F = FreightTrackingView
#     S = RFT_StatusHistory

#     # A) latest delivered date per shipment
#     latest = (
#       model.query(
#         S.EntityID.label("ShipmentID"),
#         func.max(S.StatusDate).label("delivery_date")
#       )
#       .filter(
#         S.EntityType == literal("Shipment"),
#         S.Status.in_(DELIVERED_STATUSES)
#       )
#       .group_by(S.EntityID)
#       .subquery()
#     )

#     # B) build the category expression
#     cat1 = func.coalesce(F.CATDesc, literal(""))
#     cat2 = func.coalesce(F.SubCat,   literal(""))
#     trim1 = func.ltrim(func.rtrim(cat1))
#     trim2 = func.ltrim(func.rtrim(cat2))
#     same_text = func.lower(trim1) == func.lower(trim2)

#     category_expr = case(
#       (same_text, trim1),
#       else_=func.concat(trim1, literal(" "), trim2)
#     ).label("cat")

#     # C) collect filters into a list
#     row_filters = []
#     if brands:
#         row_filters.append(F.Brand.in_(brands))
#     # note: we compare against `latest.c.delivery_date`, not F.POD
#     if start_date:
#         row_filters.append(latest.c.delivery_date >= start_date)
#     if end_date:
#         row_filters.append(latest.c.delivery_date <= end_date)

#     # D) raw subquery with all columns
#     raw = (
#       model.query(
#         F.Brand.label("brand"),
#         category_expr,
#         F.Article.label("article"),
#         F.ShipmentNumber.label("shipment"),
#         latest.c.delivery_date,
#         F.QtyShipped  .label("qty"),
#         F.FreightCost,
#         F.CustomDuties.label("custom_duties"),
#         F.SaberSADDAD.label("saber"),
#         F.DemurrageCharges,
#         F.OtherCharges,
#         F.DO_Port_Charges,
#         F.Penalties,
#         F.YardCharges,
#         F.ValueDecByCC.label("valuecc")
#       )
#       .outerjoin(latest, latest.c.ShipmentID == F.ShipmentID)
#       .filter(*row_filters)         # unpack list of filters here
#       .subquery()
#     )

#     # E) aggregate in outer query
#     total_expr = (
#         func.coalesce(func.sum(raw.c.FreightCost),      0)
#       + func.coalesce(func.sum(raw.c.custom_duties),    0)
#       + func.coalesce(func.sum(raw.c.saber),            0)
#       + func.coalesce(func.sum(raw.c.DemurrageCharges), 0)
#       + func.coalesce(func.sum(raw.c.OtherCharges),     0)
#       + func.coalesce(func.sum(raw.c.DO_Port_Charges),  0)
#       + func.coalesce(func.sum(raw.c.Penalties),        0)
#       + func.coalesce(func.sum(raw.c.YardCharges),      0)
#       + func.coalesce(func.sum(raw.c.valuecc),          0)
#     )
#     total_qty = func.sum(raw.c.qty)

#     expense_per_unit = (
#       ( total_expr / func.nullif(total_qty, 0) )
#     ).label("expense_per_unit")

#     q = (
#       model.query(
#         raw.c.brand,
#         raw.c.cat,
#         raw.c.article,
#         raw.c.shipment,
#         raw.c.delivery_date,
#         expense_per_unit
#       )
#       .group_by(
#         raw.c.brand,
#         raw.c.cat,
#         raw.c.article,
#         raw.c.shipment,
#         raw.c.delivery_date
#       )
#     )

#     return [ExpenseRow(*r) for r in q.all()]
# def build_expense_columns(rows):
#     """
#     Takes a list of ExpenseRow(brand,cat,article,shipment,delivery_date,total_expense)
#     and pivots it to wide form, one dict per (brand,cat,article),
#     with shipment columns like "RFT123 (2025-04-20)" -> expense.
#     Returns (wide_rows, columns).
#     """
#     # 1) pivot into a dict of dicts
#     pivot = defaultdict(lambda: {"brand":None, "cat":None, "article":None})
#     for r in rows:
#         key = (r.brand, r.cat, r.article)
#         grp = pivot[key]
#         grp["brand"], grp["cat"], grp["article"] = r.brand, r.cat, r.article
        
#         # format the delivery_date safely
#         if r.delivery_date:
#             date_str = r.delivery_date.strftime("%Y-%m-%d")
#         else:
#             date_str = "Unknown"
            
#         col_name = f"{r.shipment} ({date_str})"
#         grp[col_name] = r.total_expense

#     wide_rows = list(pivot.values())

#     # 2) build the columns metadata
#     columns = [
#       {"name":"brand",   "label":"Brand",    "type":"String"},
#       {"name":"cat",     "label":"Category", "type":"String"},
#       {"name":"article", "label":"Article",  "type":"String"},
#     ]

#     # any extra keys beyond brand/cat/article are shipment columns
#     if wide_rows:
#       shipment_cols = sorted(
#         k for k in wide_rows[0].keys()
#         if k not in {"brand","cat","article"}
#       )
#       for col in shipment_cols:
#         columns.append({
#           "name": col,
#           "label": col,
#           "type": "Numeric"
#         })

#     return wide_rows, columns

