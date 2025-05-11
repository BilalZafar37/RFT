from flask import (
  # Flask, 
  # render_template,
  # request,
  # redirect,
  # url_for,
  # flash,
  session,
  send_file,
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

# statuses that count as "delivered"
DELIVERED_STATUSES = ['Delivered']
OPEN_STATUSES = ["LC-Established", "PO-shared with supplier"]

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

def get_distinct_format(date_column, fmt):
  """Return distinct list of formatted dates (e.g. yyyy-MM), ordered."""
  col  = getattr(FreightTrackingView, date_column)
  expr = func.format(col, fmt).label('fmt')

  vals = (
    model.query(expr)
          .filter(col.isnot(None))
          .group_by(expr, FreightTrackingView.POCreatedDate)       # ← use GROUP BY instead of DISTINCT
          .order_by(expr)       # ← now this ORDER BY is legal in SQL Server
          .all()
  )
  return [v for (v,) in vals if v]

# Cost analysis chart
def compute_cost_by_brand(brands=None, months=None, categories=None):
    """
    Returns a list of dicts, one per brand, with cost sums and invoice/expense %.
    Optional filters: brands, months (['yyyy-MM',...]), categories.
    """
    q = model.query(
        FreightTrackingView.Brand.label("brand"),
        func.coalesce(func.sum(FreightTrackingView.FreightCost),0).label("freight_cost"),
        func.coalesce(func.sum(FreightTrackingView.CustomDuties),0).label("custom_duties"),
        func.coalesce(func.sum(FreightTrackingView.SaberSADDAD),0).label("saber"),
        func.coalesce(func.sum(FreightTrackingView.DemurrageCharges),0).label("demurrage"),
        func.coalesce(func.sum(FreightTrackingView.Penalties),0).label("penalties"),
        func.coalesce(func.sum(FreightTrackingView.OtherCharges),0).label("others"),
        # func.coalesce(func.sum(FreightTrackingView.InvoiceValue),0).label("invoice_total"),
        func.count(func.distinct(FreightTrackingView.ShipmentNumber)).label("num_shipments"),
        func.count(func.distinct(FreightTrackingView.ContainerID)).label("num_containers"),
        func.count(func.distinct(FreightTrackingView.Article)).label("num_articles"),
    )

    if brands:
        q = q.filter(FreightTrackingView.Brand.in_(brands))
    if months:
        q = q.filter(func.format(FreightTrackingView.POCreatedDate,'yyyy-MM').in_(months))
    if categories:
        q = q.filter(FreightTrackingView.CatName.in_(categories))

    rows = q.group_by(FreightTrackingView.Brand).all()

    results = []
    for r in rows:
        total_expense = (
            r.freight_cost
          + r.custom_duties
          + r.saber
          + r.demurrage
          + r.penalties
          + r.others
        )
        # pct = (total_expense / r.invoice_total * 100) if r.invoice_total else 0

        results.append({
          "brand":         r.brand,
          "freight_cost":  float(r.freight_cost),
          "custom_duties": float(r.custom_duties),
          "saber":         float(r.saber),
          "demurrage":     float(r.demurrage),
          "penalties":     float(r.penalties),
          "others":        float(r.others),
          "total_expense": float(total_expense),
          # "invoice_total": float(r.invoice_total),
          # "expense_pct":   round(pct,1),
          "num_shipments": r.num_shipments,
          "num_containers":r.num_containers,
          "num_articles":  r.num_articles,
        })
    return results

def compute_cost_by_shipment(shipments):
    """
    Given a list of shipment numbers, returns list of dicts per shipment
    with the same cost keys (no invoice/%, as those remain brand-level).
    """
    results = []
    for ship in shipments:
        r = (
          model.query(
            func.coalesce(func.sum(FreightTrackingView.FreightCost),0),
            func.coalesce(func.sum(cast(FreightTrackingView.CustomDuties,Numeric)),0),
            func.coalesce(func.sum(cast(FreightTrackingView.SaberSADDAD,Numeric)),0),
            func.coalesce(func.sum(FreightTrackingView.DemurrageCharges),0),
            func.coalesce(func.sum(FreightTrackingView.Penalties),0),
            func.coalesce(func.sum(FreightTrackingView.OtherCharges),0),
          )
          .filter(FreightTrackingView.ShipmentNumber==ship)
          .one()
        )
        results.append({
          "shipment":      ship,
          "freight_cost":  float(r[0]),
          "custom_duties": float(r[1]),
          "saber":         float(r[2]),
          "demurrage":     float(r[3]),
          "penalties":     float(r[4]),
          "others":        float(r[5]),
        })
    return results

# Lead Time Chart
def compute_leadtime_by_brand(brands=None, months=None):
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

            q = (model
                 .query(func.avg(
                      func.datediff(literal_column("day"), sf, ef)
                 ))
                 .filter(FreightTrackingView.Brand == b,
                         sf.isnot(None), ef.isnot(None))
            )

            # apply month filter if provided
            if months:
                q = q.filter(
                  func.format(FreightTrackingView.POCreatedDate,'yyyy-MM')
                  .in_(months)
                )

            avg_days = q.scalar() or 0
            row[cfg.IntervalName] = round(avg_days, 1)

        results.append(row)

    return results

def compute_leadtime_by_shipment(shipments, months=None, limit = 10):
    """
    Given a list of shipment numbers, returns list of dicts per shipment
    with avg lead‐time per configured interval.
    Optional: only include POCreatedDate in provided months.
    """
    # 1) load all intervals
    intervals = (
        model
        .query(RFT_IntervalConfig)
        .order_by(RFT_IntervalConfig.ID)
        .all()
    )

    results = []
    for ship in shipments:
        rec = {"shipment": ship}
        for cfg in intervals:
            sf = getattr(FreightTrackingView, cfg.StartField)
            ef = getattr(FreightTrackingView, cfg.EndField)

            q = (model
                 .query(func.avg(
                       func.datediff(literal_column("day"), sf, ef)
                 ))
                 .filter(FreightTrackingView.ShipmentNumber == ship,
                         sf.isnot(None), ef.isnot(None))
            )

            # apply month filter if provided
            if months:
                q = q.filter(
                  func.format(FreightTrackingView.POCreatedDate,'yyyy-MM')
                  .in_(months)
                )

            avg_days = q.scalar() or 0
            rec[cfg.IntervalName] = round(avg_days, 1)

        results.append(rec)

    return results

# Fulfillment chart 
def compute_fulfillment_by_brand(brands=None):
  """
  Returns list of dicts, one per Brand, with delivered% & in-transit%.
  Optional filter: only those brands in the provided list.
  """
  delivered_expr = case(
      (FreightTrackingView.ContainerLevelStatus.in_(DELIVERED_STATUSES),
        FreightTrackingView.QtyShipped),
      else_=0
  )
  intransit_expr = case(
      (
        or_(
          FreightTrackingView.ContainerLevelStatus.is_(None),
          ~FreightTrackingView.ContainerLevelStatus.in_(DELIVERED_STATUSES)
        ),
        FreightTrackingView.QtyShipped
      ),
      else_=0
  )

  q = model.query(
      FreightTrackingView.Brand.label("brand"),
      func.coalesce(func.sum(FreightTrackingView.Qty),         0).label("total_qty"),
      func.coalesce(func.sum(FreightTrackingView.BalanceQty),  0).label("open_qty"),
      func.coalesce(func.sum(delivered_expr),                  0).label("delivered_qty"),
      func.coalesce(func.sum(intransit_expr),                  0).label("intransit_qty"),
      # func.coalesce(func.sum(open_expr),                     0).label("open_qty"),
  )
  if brands:
      q = q.filter(FreightTrackingView.Brand.in_(brands))

  rows = q.group_by(FreightTrackingView.Brand).all()

  results = []
  for r in rows:
      if r.total_qty:
        dp = r.delivered_qty / r.total_qty * 100
        ip = r.intransit_qty / r.total_qty * 100
        op = r.open_qty / r.total_qty * 100
      else:
          dp = ip = 0
      results.append({
        "brand":         r.brand,
        "open_pct":      round(op, 1),
        "delivered_pct": round(dp, 1),
        "intransit_pct": round(ip, 1),
      })
  return results

def compute_fulfillment_by_po(pos=None):
  delivered = case(
    (FreightTrackingView.ContainerLevelStatus.in_(DELIVERED_STATUSES),
      FreightTrackingView.QtyShipped),
    else_=0
  )
  intransit = case(
    (
      or_(
        FreightTrackingView.ContainerLevelStatus.is_(None),
        ~FreightTrackingView.ContainerLevelStatus.in_(DELIVERED_STATUSES)
      ),
      FreightTrackingView.QtyShipped
    ),
    else_=0
  )

  q = model.query(
    FreightTrackingView.PONumber.label("po"),
    func.coalesce(func.sum(FreightTrackingView.Qty),       0).label("open_qty"),
    func.coalesce(func.sum(delivered),                 0).label("delivered_qty"),
    func.coalesce(func.sum(intransit),                 0).label("intransit_qty"),
  )
  
  if pos:
      q = q.filter(FreightTrackingView.PONumber.in_(pos))

  rows = q.group_by(FreightTrackingView.PONumber).all()

  results = []
  for r in rows:
      if r.open_qty:
          del_pct = r.delivered_qty  / r.open_qty * 100
          in_pct  = r.intransit_qty  / r.open_qty * 100
      else:
          del_pct = in_pct = 0
      results.append({
        "po":             r.po,
        "delivered_pct":  round(del_pct, 1),
        "intransit_pct":  round(in_pct,  1),
      })
  return results

# Planed containers DTC & WH
def compute_container_plan_stage_counts(plan_status_name):
    """
    For all containers whose *latest* Planed-Container status
    is exactly `plan_status_name`, count how many are currently
    in each of the four real container stages.
    Returns a list of dicts: [{"stage": stage, "count": n}, …]
    in the order defined by CONTAINER_STAGES.
    """
    S = RFT_StatusHistory

    # 1) find for each container its latest Planed-Container update
    latest_plan = (
      model.query(
        S.EntityID.label("ContainerID"),
        func.max(S.StatusDate).label("maxdate")
      )
      .filter(S.EntityType == "Planed-Container")
      .group_by(S.EntityID)
      .subquery()
    )

    # 2) join back to get the actual status text at that time
    latest_plan_status = (
      model.query(
        latest_plan.c.ContainerID,
        S.Status.label("plan_status")
      )
      .join(S, and_(
        S.EntityType   == "Planed-Container",
        S.EntityID     == latest_plan.c.ContainerID,
        S.StatusDate   == latest_plan.c.maxdate
      ))
      .subquery()
    )

    # 3) restrict to containers whose latest plan status matches
    planned_ctn_ids = (
      model.query(latest_plan_status.c.ContainerID)
           .filter(latest_plan_status.c.plan_status == plan_status_name)
           .subquery()
    )

    # 4) find each container’s current *actual* status
    latest_act = (
      model.query(
        S.EntityID.label("ContainerID"),
        func.max(S.StatusDate).label("maxdate")
      )
      .filter(S.EntityType == "Container")
      .group_by(S.EntityID)
      .subquery()
    )
    latest_act_status = (
      model.query(
        latest_act.c.ContainerID,
        S.Status.label("act_status")
      )
      .join(S, and_(
        S.EntityType == "Container",
        S.EntityID   == latest_act.c.ContainerID,
        S.StatusDate == latest_act.c.maxdate
      ))
      .subquery()
    )

    # 5) restrict to just our planned‐group containers and count by actual status
    counts = dict(
      model.query(
        latest_act_status.c.act_status,
        func.count().label("ct")
      )
      .filter(latest_act_status.c.ContainerID.in_(planned_ctn_ids))
      .group_by(latest_act_status.c.act_status)
      .all()
    )

    # 6) assemble in the fixed stage‐order (zero if missing)
    return [
      {"stage": st, "count": counts.get(st, 0)}
      for st in CONTAINER_STAGES
    ]

# Shipment statusses
def compute_shipment_status_counts():
    """
    For each Shipment, find its latest StatusDate where EntityType='Shipment',
    then count how many shipments are in each of those statuses.
    Returns a list of dicts: [{"status": status, "count": n}, …],
    ordered by count descending.
    """
    S = RFT_StatusHistory

    # 1) per‐shipment max date
    latest = (
      model.query(
        S.EntityID.label("ShipmentID"),
        func.max(S.StatusDate).label("maxdate")
      )
      .filter(S.EntityType == "Shipment")
      .group_by(S.EntityID)
      .subquery()
    )

    # 2) join back to get the actual status text
    latest_status = (
      model.query(
        S.Status.label("status"),
        func.count(S.EntityID).label("ct")
      )
      .join(latest, (S.EntityID == latest.c.ShipmentID) &
                    (S.StatusDate == latest.c.maxdate)   &
                    (S.EntityType == "Shipment"))
      .group_by(S.Status)
      .order_by(func.count(S.EntityID).desc())
      .all()
    )

    # 3) turn into list of dicts
    return [{"status": st, "count": n} for st, n in latest_status]

# Upcomming ETA
def compute_upcoming_eta(days_ahead: int = 7):
    """
    Return all shipments whose ETADestination is between now and now+days_ahead,
    ordered by ETADestination ascending.
    """
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days_ahead)

    q = (
      model.query(
          RFT_Shipment.ShipmentNumber   .label("shipment"),
          RFT_Shipment.ETADestination   .label("eta"),
          RFT_Shipment.OriginPort       .label("origin_port"),
          func.count(RFT_Container.ContainerID).label("containers_num"),
          RFT_Shipment.POD              .label("dest_country"),
      )
      # LEFT-join so shipments with zero containers will still show up as 0
      .outerjoin(RFT_Shipment.containers)
      .filter(
          RFT_Shipment.ETADestination.isnot(None),
          RFT_Shipment.ETADestination >= now,
          RFT_Shipment.ETADestination <= cutoff,
      )
      # Any column in the SELECT list that isn’t aggregated must be GROUP BY’d
      .group_by(
          RFT_Shipment.ShipmentNumber,
          RFT_Shipment.ETADestination,
          RFT_Shipment.OriginPort,
          RFT_Shipment.POD,
      )
      .order_by(RFT_Shipment.ETADestination)
    )

    return [
        {
            "shipment": shp,
            "eta": eta,
            "origin_port": op,
            "containers_num": cn,
            "dest_country": dc
        }
        for shp, eta, op, cn, dc in q.all()
    ]

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
    # preload your lookup maps
    brand_rows = model.query(RFT_BrandTypes.BrandType, RFT_BrandTypes.BrandName).all()
    brand_map  = { t:name for t,name in brand_rows }

    cat_rows = model.query(
        RFT_CategoriesMappingMain.CatCode,
        RFT_CategoriesMappingMain.ID
    ).all()
    cat_map = { code:cid for code,cid in cat_rows }

    uploaded = (
      model.query(RFT_PurchaseOrderUpload)
           .filter_by(UploadBatch=batch_id)
           .all()
    )

    po_cache = {}

    for u in uploaded:
        po_num     = u.PurchaseOrder
        real_brand = brand_map.get(u.Type, u.MdseCat)
        prefix     = (u.MdseCat or "")[:3].upper()
        cat_id     = cat_map.get(prefix)

        # 1) either pull from cache or from DB
        if po_num in po_cache:
            po = po_cache[po_num]
        else:
            po = (
              model.query(RFT_PurchaseOrder)
                   .filter_by(PONumber=po_num)
                   .one_or_none()
            )
            if not po:
                po = RFT_PurchaseOrder(
                  PONumber=po_num,
                  Supplier = u.VendorSupplyingSite,
                  Brand    = real_brand,
                  PODate   = u.DocDate,
                )
                model.add(po)
                model.flush()     # get po.POID
            po_cache[po_num] = po

        # 2) now create the line
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

# Article wise expense report
def fetch_expense_data(brands=None, start_date=None, end_date=None):
    F = FreightTrackingView
    S = RFT_StatusHistory

    # A) latest delivered date per shipment
    latest = (
      model.query(
        S.EntityID.label("ShipmentID"),
        func.max(S.StatusDate).label("delivery_date")
      )
      .filter(
        S.EntityType == literal("Shipment"),
        S.Status.in_(DELIVERED_STATUSES)
      )
      .group_by(S.EntityID)
      .subquery()
    )

    # B) build the category expression
    cat1 = func.coalesce(F.CATDesc, literal(""))
    cat2 = func.coalesce(F.SubCat,   literal(""))
    trim1 = func.ltrim(func.rtrim(cat1))
    trim2 = func.ltrim(func.rtrim(cat2))
    same_text = func.lower(trim1) == func.lower(trim2)

    category_expr = case(
      (same_text, trim1),
      else_=func.concat(trim1, literal(" "), trim2)
    ).label("cat")

    # C) collect filters into a list
    row_filters = []
    if brands:
        row_filters.append(F.Brand.in_(brands))
    # note: we compare against `latest.c.delivery_date`, not F.POD
    if start_date:
        row_filters.append(latest.c.delivery_date >= start_date)
    if end_date:
        row_filters.append(latest.c.delivery_date <= end_date)

    # D) raw subquery with all columns
    raw = (
      model.query(
        F.Brand.label("brand"),
        category_expr,
        F.Article.label("article"),
        F.ShipmentNumber.label("shipment"),
        latest.c.delivery_date,
        F.QtyShipped  .label("qty"),
        F.FreightCost,
        F.CustomDuties.label("custom_duties"),
        F.SaberSADDAD.label("saber"),
        F.DemurrageCharges,
        F.OtherCharges,
        F.DO_Port_Charges,
        F.Penalties,
        F.YardCharges,
        F.ValueDecByCC.label("valuecc")
      )
      .outerjoin(latest, latest.c.ShipmentID == F.ShipmentID)
      .filter(*row_filters)         # unpack list of filters here
      .subquery()
    )

    # E) aggregate in outer query
    total_expr = (
        func.coalesce(func.sum(raw.c.FreightCost),      0)
      + func.coalesce(func.sum(raw.c.custom_duties),    0)
      + func.coalesce(func.sum(raw.c.saber),            0)
      + func.coalesce(func.sum(raw.c.DemurrageCharges), 0)
      + func.coalesce(func.sum(raw.c.OtherCharges),     0)
      + func.coalesce(func.sum(raw.c.DO_Port_Charges),  0)
      + func.coalesce(func.sum(raw.c.Penalties),        0)
      + func.coalesce(func.sum(raw.c.YardCharges),      0)
      + func.coalesce(func.sum(raw.c.valuecc),          0)
    )
    total_qty = func.sum(raw.c.qty)

    expense_per_unit = (
      ( total_expr / func.nullif(total_qty, 0) )
    ).label("expense_per_unit")

    q = (
      model.query(
        raw.c.brand,
        raw.c.cat,
        raw.c.article,
        raw.c.shipment,
        raw.c.delivery_date,
        expense_per_unit
      )
      .group_by(
        raw.c.brand,
        raw.c.cat,
        raw.c.article,
        raw.c.shipment,
        raw.c.delivery_date
      )
    )

    return [ExpenseRow(*r) for r in q.all()]

def build_expense_columns(rows):
    """
    Takes a list of ExpenseRow(brand,cat,article,shipment,delivery_date,total_expense)
    and pivots it to wide form, one dict per (brand,cat,article),
    with shipment columns like "RFT123 (2025-04-20)" -> expense.
    Returns (wide_rows, columns).
    """
    # 1) pivot into a dict of dicts
    pivot = defaultdict(lambda: {"brand":None, "cat":None, "article":None})
    for r in rows:
        key = (r.brand, r.cat, r.article)
        grp = pivot[key]
        grp["brand"], grp["cat"], grp["article"] = r.brand, r.cat, r.article
        
        # format the delivery_date safely
        if r.delivery_date:
            date_str = r.delivery_date.strftime("%Y-%m-%d")
        else:
            date_str = "Unknown"
            
        col_name = f"{r.shipment} ({date_str})"
        grp[col_name] = r.total_expense

    wide_rows = list(pivot.values())

    # 2) build the columns metadata
    columns = [
      {"name":"brand",   "label":"Brand",    "type":"String"},
      {"name":"cat",     "label":"Category", "type":"String"},
      {"name":"article", "label":"Article",  "type":"String"},
    ]

    # any extra keys beyond brand/cat/article are shipment columns
    if wide_rows:
      shipment_cols = sorted(
        k for k in wide_rows[0].keys()
        if k not in {"brand","cat","article"}
      )
      for col in shipment_cols:
        columns.append({
          "name": col,
          "label": col,
          "type": "Numeric"
        })

    return wide_rows, columns


#########################################################################################
######################## |HELPER FUNCTIONS| #############################################
#########################################################################################