from flask import request, jsonify
from . import bp
from models import *
from collections import defaultdict
# from utils import get_distinct, get_distinct_format



# @bp.route("/supplier/performance", methods=["GET"])
# def supplier_performance():
#     brand_filter = request.args.get("brand")
#     shipment_filter = request.args.get("shipment")

#     PO = RFT_PurchaseOrder
#     POL = RFT_PurchaseOrderLine
#     SP  = RFT_ShipmentPOLine
#     SH  = RFT_Shipment
#     C   = RFT_Container

#     # Step 1: collect all PO–Container relationships with dates
#     rows = (
#         model.query(
#             PO.POID,
#             PO.PONumber,
#             PO.PODate,
#             PO.Brand,
#             POL.Qty.label("POQty"),
#             SP.QtyShipped,
#             SH.ShipmentID,
#             SH.ShipmentNumber,
#             C.ContainerID,
#             C.ATAOrigin
#         )
#         .join(POL, POL.POID == PO.POID)
#         .join(SP, SP.POLineID == POL.POLineID)
#         .join(SH, SH.ShipmentID == SP.ShipmentID)
#         .join(C, C.ShipmentID == SH.ShipmentID)
#         .filter(PO.PODate.isnot(None), C.ATAOrigin.isnot(None))
#     )

#     if brand_filter:
#         rows = rows.filter(PO.Brand == brand_filter)

#     if shipment_filter:
#         rows = rows.filter(SH.ShipmentID == shipment_filter)

#     results = rows.all()

#     # Step 2: compute performance per PO–Container pair
#     perf_data = []
#     for r in results:
#         diff = (r.ATAOrigin - datetime.combine(r.PODate, datetime.min.time())).days
#         perf_data.append({
#             "brand": r.Brand,
#             "shipment": r.ShipmentNumber,
#             "shipment_id": r.ShipmentID,
#             "po": r.PONumber,
#             "poid": r.POID,
#             "diff": diff
#         })

#     # Step 3: Group and aggregate based on level
#     if shipment_filter:
#         # 3rd face → PO-level fulfillment % in this shipment
#         po_group = defaultdict(lambda: {"total": 0, "shipped": 0})
        
#         for r in results:
#             po_group[r.PONumber]["total"] = r.POQty
#             po_group[r.PONumber]["shipped"] += r.QtyShipped
        
#         response = {
#             "labels": list(po_group.keys()),
#             "data": [
#                 round(v["shipped"] / v["total"] * 100, 1) if v["total"] > 0 else 0
#                 for v in po_group.values()
#             ]
#         }
        

#     elif brand_filter:
#         # 2nd face → Shipment-level
#         ship_group = defaultdict(list)
#         for r in perf_data:
#             ship_group[r["shipment"]].append(r["diff"])

#         response = {
#             "labels": list(ship_group.keys()),
#             "data": [ round(sum(v)/len(v), 1) for v in ship_group.values() ]
#         }

#     else:
#         # 1st face → Brand-level
#         brand_group = defaultdict(list)
#         for r in perf_data:
#             brand_group[r["brand"]].append(r["diff"])

#         response = {
#             "labels": list(brand_group.keys()),
#             "data": [ round(sum(v)/len(v), 1) for v in brand_group.values() ]
#         }

#     return jsonify(response)


# ────────────────────────────────────────────────────────────────────────────────
@bp.route("/supplier/performance", methods=["GET"])
def supplier_performance():
    """
    Three-face drill-down data feed
      •  no params     → brand level
      •  ?brand=XYZ    → shipment level
      •  ?shipment=123 → PO level     (fulfilment % + days + colour)
    """

    brand_q     = request.args.get("brand")
    shipment_q  = request.args.get("shipment")

    # ── Table aliases ──────────────────────────────────────────────────────────
    PO   = RFT_PurchaseOrder
    POL  = RFT_PurchaseOrderLine
    SP   = RFT_ShipmentPOLine
    SH   = RFT_Shipment
    C    = RFT_Container

    # ── Base query: every PO ⇄ container pairing with qty & dates ─────────────
    rows = (
        model.query(
            PO.POID,
            PO.PONumber,
            PO.Brand,
            PO.PODate,
            SH.ShipmentID,
            SH.ShipmentNumber,
            func.sum(func.distinct(POL.POLineID)).label("poline_count"),
            func.sum(POL.Qty).label("total_qty"),
            func.sum(SP.QtyShipped).label("shipped_qty"),
            func.min(C.ATAOrigin).label("ATA")       # rename for brevity
        )
        .join(POL,  POL.POID      == PO.POID)
        .join(SP,   SP.POLineID   == POL.POLineID)
        .join(SH,   SH.ShipmentID == SP.ShipmentID)
        .join(C,    C.ShipmentID  == SH.ShipmentID)
        .filter(PO.PODate.isnot(None), C.ATAOrigin.isnot(None))
        .group_by(PO.POID, PO.PONumber, PO.Brand, PO.PODate, SH.ShipmentID, SH.ShipmentNumber)
        .order_by(PO.PONumber)
    )

    if brand_q:
        rows = rows.filter(PO.Brand == brand_q)
    if shipment_q:
        rows = rows.filter(SH.ShipmentID == shipment_q)

    rows = rows.all()

    # Compute day delta once
    records = []
    for r in rows:
        diff_days = (
            r.ATA - datetime.combine(r.PODate, datetime.min.time())
        ).days
        records.append({
            "brand":        r.Brand,
            "shipment_id":  r.ShipmentID,
            "shipment_no":  r.ShipmentNumber,
            "po":           r.PONumber,
            "poid":         r.POID,
            "total_qty":    r.total_qty,
            "shipped_qty":  r.shipped_qty,
            "days":         diff_days
        })

    # ── PO-level (deepest face) ───────────────────────────────────────────────
    if shipment_q:
        by_po = defaultdict(lambda: {"tot": 0, "ship": 0, "days": []})
        for r in records:
            by_po[r["po"]]["tot"]  = r["total_qty"]
            by_po[r["po"]]["ship"] += r["shipped_qty"]
            by_po[r["po"]]["days"].append(r["days"])

        labels, percent, days, colours = [], [], [], []

        baseline_days = None
        for i, (po, data) in enumerate(by_po.items()):
            avg_days = round(sum(data["days"]) / len(data["days"]), 1)
            pct      = round((data["ship"] / data["tot"]) * 100, 1) if data["tot"] else 0

            # colour rule vs first PO segment
            if i == 0:
                baseline_days = avg_days
                colour = "#4CAF50"
            else:
                if avg_days <= baseline_days:
                    colour = "#4CAF50"
                elif avg_days <= baseline_days * 2:
                    colour = "#4CAF50"
                else:
                    colour = "#4CAF50"

            labels.append(po)
            percent.append(pct)
            days.append(avg_days)
            colours.append(colour)

        return jsonify({
            "level"  : "po",
            "labels" : labels,
            "percent": percent,
            "days"   : days,
            "colors" : colours
        })

    # ── Shipment-level ────────────────────────────────────────────────────────
    if brand_q:
        ships = defaultdict(list)
        for r in records:
            key = f"{r['shipment_no']} ({r['shipment_id']})"
            ships[key].append(r["days"])

        labels, days, colours = [], [], []
        baseline = None
        for i, (ship, lst) in enumerate(ships.items()):
            avg_d = round(sum(lst) / len(lst), 1)
            if i == 0:
                baseline = avg_d
                colour = "#4CAF50"
            else:
                colour = "#4CAF50" if avg_d <= baseline else (
                         "#FFC107" if avg_d <= baseline*2 else "#F44336")
            labels.append(ship)
            days.append(avg_d)
            colours.append(colour)

        return jsonify({
            "level" : "shipment",
            "labels": labels,
            "days"  : days,
            "colors": colours
        })

    # ── Brand-level (top face) ────────────────────────────────────────────────
    brands = defaultdict(list)
    for r in records:
        brands[r["brand"]].append(r["days"])

    labels, days, colours = [], [], []
    baseline = None
    for i, (br, lst) in enumerate(brands.items()):
        avg_d = round(sum(lst) / len(lst), 1)
        if i == 0:
            baseline = avg_d
            colour = "#4CAF50"
        else:
            colour = "#4CAF50" if avg_d <= baseline else (
                     "#FFC107" if avg_d <= baseline*2 else "#F44336")
        labels.append(br)
        days.append(avg_d)
        colours.append(colour)

    return jsonify({
        "level" : "brand",
        "labels": labels,
        "days"  : days,
        "colors": colours
    })