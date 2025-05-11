# blueprints/main.py
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_file,
    jsonify,
    # json,
    Blueprint,
    abort
)
import json
from decimal            import Decimal
import os
from io                 import BytesIO
import pandas           as pd
import uuid
from werkzeug.utils     import secure_filename
from flask_login        import login_required
from models             import *
from collections        import defaultdict
from blueprints.auth    import current_user
from utils              import (
    get_table_metadata, generate_unique_shipment_number, export_to_excel, etl_purchase_orders,
    get_countries, fetch_expense_data, build_expense_columns
)


bp = Blueprint('main', __name__, static_folder="./static")


# save settings (layout and stuff)
def save(key, value):
    row = model.query(RFT_Settings).filter_by(
      SettingKey=key, UserID=current_user.username
    ).one_or_none()
    if not row:
        row = RFT_Settings(SettingKey=key, UserID=current_user.username)
        model.add(row)
    row.SettingValue = value
    model.commit()


@bp.route("/", methods=['GET', 'POST']) # DASHBOARD
def call_home():
    return render_template("dashboard/dashboard.html")

@bp.route("/test", methods=["GET", "POST"])
def test():
    query = model.query(FreightTrackingView)

    rows = query.order_by(desc(FreightTrackingView.POID) ).all()
    
    # 2) load any saved labels for this view
    table_name = "FreightTrackingView"
    label_rows = (
        model
        .query(RFT_FieldLabels)
        .filter_by(TableName=table_name)
        .all()
    )
    # build a field → human label dict
    friendly = {lbl.FieldName: lbl.Label for lbl in label_rows}

    # rows = model.query(MyModel).all()
    columns = get_table_metadata(FreightTrackingView, rows, friendly)
    
    return render_template("base.html", rows=rows, columns=columns)

@bp.route("/freight_trackingView", methods=["GET", "POST"])
def freight_trackingView():
    query = model.query(FreightTrackingView)

    rows = query.order_by(desc(FreightTrackingView.POID) ).all()
    
    # 2) load any saved labels for this view
    table_name = "FreightTrackingView"
    label_rows = (
        model
        .query(RFT_FieldLabels)
        .filter_by(TableName=table_name)
        .all()
    )
    # build a field → human label dict
    friendly = {lbl.FieldName: lbl.Label for lbl in label_rows}

    columns = get_table_metadata(FreightTrackingView, rows, friendly)

    
    return render_template(
        "FreightTrackingView.html",
        rows=rows, 
        columns=columns
    )

@bp.route("/initialPO_Updates", methods=["GET", "POST"])
@login_required
def initialPO_Updates():
    # POST: apply the updates
    if request.method == "POST":
        form = request.form
        updated_any = False

        # collect all POIDs that appeared in the form
        updated_poids = {
            v for k, v in form.items()
            if k.startswith("POID_") and v
        }

        for poid in updated_poids:
            po = model.query(RFT_PurchaseOrder).get(int(poid))
            if not po:
                continue

            # pull out each field
            new_lcstatus = form.get(f"LCStatus_{poid}")
            new_lcnum    = form.get(f"LCNumber_{poid}")
            new_lcdate   = form.get(f"LCDate_{poid}")
            new_mot      = form.get(f"ModeOfTransport_{poid}")
            new_incot    = form.get(f"INCOTerms_{poid}")

            changed = False

            # Only write if different / not empty
            if new_lcnum and new_lcnum != po.LCNumber:
                po.LCNumber = new_lcnum
                changed = True

            if new_lcstatus and new_lcstatus != po.LCStatus:
                po.LCStatus = new_lcstatus
                changed = True

                # if they marked "No", add a history entry
                if new_lcstatus == "No":
                    h = RFT_StatusHistory(
                        EntityType  = "Purchase Order",
                        EntityID    = po.POID,
                        Status      = "PO-shared with supplier",
                        StatusDate  = datetime.utcnow(),
                        UpdatedBy   = session['username'],
                        Comments    = "Initial update: LCStatus=No"
                    )
                    model.add(h)

            if new_lcdate:
                # parse YYYY-MM-DD automatically via WTForms / HTML date
                dt = datetime.strptime(new_lcdate, "%Y-%m-%d")
                if dt != po.LCDate:
                    po.LCDate = dt
                    changed = True

            if new_mot and new_mot != po.ModeOfTransport:
                po.ModeOfTransport = new_mot
                changed = True

            if new_incot and new_incot != po.INCOTerms:
                po.INCOTerms = new_incot
                changed = True

            if changed:
                po.LastUpdatedBy = session['username']
                flash(f"PO {po.PONumber} updated", "success")
                updated_any = True

        if updated_any:
            model.commit()
        else:
            flash("No changes detected", "info")

        return redirect(url_for("main.initialPO_Updates"))

    # — GET: build the table of “initial‐PO” rows needing LC/INCOTerm/MOT
    # join PO → PO Line to get counts and sums
    sub = (
        model.query(
            RFT_PurchaseOrderLine.POID.label("POID"),
            func.count().label("TotalArticles"),
            func.sum(RFT_PurchaseOrderLine.Qty).label("TotalQty"),
            func.sum(RFT_PurchaseOrderLine.TotalValue).label("TotalValue"),
        )
        .group_by(RFT_PurchaseOrderLine.POID)
        .subquery()
    )

    # now join back to PO
    query = (
        model.query(
            RFT_PurchaseOrder.POID,
            RFT_PurchaseOrder.PONumber,
            RFT_PurchaseOrder.Supplier,
            RFT_PurchaseOrder.Brand,
            RFT_PurchaseOrder.PODate,
            RFT_PurchaseOrder.LCNumber,
            RFT_PurchaseOrder.LCStatus,
            # format LCDate to 'YYYY-MM-DD' for the template:
            func.convert(literal_column("varchar"), RFT_PurchaseOrder.LCDate, literal_column("23"))
              .label("FLCDate"),
            RFT_PurchaseOrder.ModeOfTransport,
            RFT_PurchaseOrder.INCOTerms,
            sub.c.TotalArticles,
            sub.c.TotalQty,
            sub.c.TotalValue,
        )
        .join(sub, sub.c.POID == RFT_PurchaseOrder.POID)
        # only POs where at least one of the “initial” fields is still empty:
        # if lcstatus 'yes' then i need lc & date. 
        # if lcstatus 'No' then dont need lc & date. 
        # if lcstatus NULL then lc & date also NUL except MOT and INCOTerms
        .filter(
            or_(
                and_( 
                        RFT_PurchaseOrder.LCNumber.is_(None),
                        RFT_PurchaseOrder.LCStatus.is_(None),
                        RFT_PurchaseOrder.LCDate.is_(None)
                    ),
                and_(
                    RFT_PurchaseOrder.LCStatus == 'Yes',
                    or_(
                        RFT_PurchaseOrder.LCDate.is_(None),
                        RFT_PurchaseOrder.LCNumber.is_(None)
                    )
                ),
                RFT_PurchaseOrder.INCOTerms.is_(None),
                RFT_PurchaseOrder.ModeOfTransport.is_(None)
            )
        )
        .order_by(RFT_PurchaseOrder.PODate.desc())
    )

    report_data = query.all()

    # dynamic-filters metadata:
    columns = [
      {"name":"PONumber",        "label":"PO Number"},
      {"name":"Supplier",        "label":"Supplier"},
      {"name":"Brand",           "label":"Brand"},
      {"name":"PODate",          "label":"PO Date",           "type":"date"},
      {"name":"LCStatus",        "label":"LC Status",        "filter_type":"select",
         "options":["Yes","No"]},
      {"name":"LCNumber",        "label":"LC Number"},
      {"name":"FLCDate",         "label":"LC Date",          "filter_type":"date"},
      {"name":"ModeOfTransport", "label":"Mode Of Transport", "filter_type":"select",
         "options":[m.mode for m in model.query(RFT_ModeOfTransport).all()]},
      {"name":"INCOTerms",       "label":"INCOTerms",        "filter_type":"select",
         "options":[c.code+" - "+c.description
                    for c in model.query(RFT_IncoTerms).all()]},
      {"name":"TotalArticles",   "label":"# Articles",      "type":"numeric"},
      {"name":"TotalQty",        "label":"Total Qty",       "type":"numeric"},
      {"name":"TotalValue",      "label":"Total Value",     "type":"numeric"},
    ]

    # load your drop‐downs (unchanged)
    modeOfTransport = model.query(RFT_ModeOfTransport).all()
    incoterms       = model.query(RFT_IncoTerms).all()
    
    return render_template(
      "InitialPO_Updates.html",
      rows    = [r._asdict() for r in report_data],
      columns = columns,
      # so your filters form can re-populate
      sel_PODate       = request.values.get("PODate",""),
      sel_LCNumber     = request.values.get("LCNumber",""),
      modeOfTransport  = modeOfTransport,
      incoterms        = incoterms
      # etc for any other filters…
    )

@bp.route("/createShipments", methods=["GET", "POST"])
@login_required
def createShipments():
    if request.method == 'POST':
        selected_poids = request.form.getlist('POID')
        
        if not selected_poids:
            flash('No purchase orders selected.', 'warning')
            return redirect(url_for('main.createShipments')) 
        
        selected_qtys = []
        for ids in selected_poids:
            selected_qty = request.form.get(f'selected_qty_{ids}')
            selected_qtys.append(selected_qty)
            
        # 1. Create new Shipment
        new_shipment = RFT_Shipment(
            ShipmentNumber=generate_unique_shipment_number(),  # You can replace with better logic
            CreatedBy=session.get('username', 'system'),
            LastUpdatedBy=session.get('username', 'system')
        )
        model.add(new_shipment)
        model.flush()  # Get ShipmentID without committing

        # 2. Link POLines to the shipment
        for poid, qty in zip(selected_poids, selected_qtys):
            pid = int(poid)
            shipped_qty = int(qty)
            
            # load *that* one PurchaseOrderLine
            line = model.query(RFT_PurchaseOrderLine).get(pid)
            if not line:
                continue
            
            # decrement its balance
            line.BalanceQty = max(0, line.BalanceQty - shipped_qty)
            
            shipment_line = RFT_ShipmentPOLine(
                ShipmentID=new_shipment.ShipmentID,
                POLineID=line.POLineID,
                QtyShipped=shipped_qty,
                ECCDate=None,
                LastUpdatedBy=session.get('username', 'system')
            )
            model.add(shipment_line)

        # 3. Add to Status History
        status_history = RFT_StatusHistory(
            EntityType='Shipment',
            EntityID=new_shipment.ShipmentID,
            Status='Stocks not ready',
            StatusDate=datetime.now(),
            UpdatedBy=session.get('username', 'system'),
            Comments='Shipment created from selected POs'
        )
        model.add(status_history)

        model.commit()
        flash(f'Shipment {new_shipment.ShipmentNumber} created successfully.', 'success')
        return redirect(url_for('main.createShipments'))  # replace with your actual page
        
    # ————————— GET: fetch all PO‐lines that still have balance
    q = (
      model
      .query(RFT_PurchaseOrder, RFT_PurchaseOrderLine, RFT_CategoriesMappingMain)
      .join(RFT_PurchaseOrderLine, RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID)
      .join(RFT_CategoriesMappingMain, RFT_PurchaseOrderLine.CategoryMappingID == RFT_CategoriesMappingMain.ID)
      .filter(RFT_PurchaseOrderLine.BalanceQty != 0)
      .filter(
        or_(
          RFT_PurchaseOrder.LCStatus == 'No',
          and_(
            RFT_PurchaseOrder.LCDate.isnot(None),
            RFT_PurchaseOrder.LCNumber.isnot(None)
          )
        )
      )
      .order_by(desc(RFT_PurchaseOrder.CreatedDate))
    )
    raw = q.all()

    # 1) turn into a list of dicts for JS/templates
    rows = []
    for po, line, cat in raw:
        rows.append({
          "POLineID":       line.POLineID,
          "PONumber":       po.PONumber,
          "Supplier":       po.Supplier,
          "Brand":          po.Brand,
          "CatName":        cat.CatName,
          "CATDesc":        cat.CatDesc,
          "Article":        line.Article,
          "BalanceQty":     line.BalanceQty,
          "TotalValue":     float(line.TotalValue)
        })

    # 2) build column metadata for dynamic filters
    columns = [
      {"name":"PONumber",       "label":"PO Number",       "filter_type":"text"},
      {"name":"Supplier",       "label":"Supplier",        "filter_type":"text"},
      {"name":"Brand",          "label":"Brand",           "filter_type":"text"},
      {"name":"CatName",        "label":"Category",        "filter_type":"select",
       "options": sorted({r["CatName"] for r in rows})},
      {"name":"Article",        "label":"Article",         "filter_type":"text"},
      {"name":"BalanceQty",     "label":"Total Qty",       "filter_type":"numeric"},
      {"name":"TotalValue",     "label":"Total Value",     "filter_type":"numeric"},
    ]

    sda_options =  model.query(RFT_CategoriesMappingSDA).all()
    sda_options = sda_options,
    
    return render_template(
      "createShipments.html",
      rows=rows,
      columns=columns,
      sda_options = sda_options
    )

@bp.route("/createdShipments", methods=["GET", "POST"]) # CREATED  !!!!!!!!    ED
@login_required
def createdShipments():
    BIYAN_FOLDER  = 'static/Biyan-files'
    SADDAD_FOLDER = 'static/SADDAD-files'

    if request.method == "POST":
        form = request.form.to_dict()
        updated, not_found = set(), []

        # collect all shipment numbers that were in the form
        ship_nums = {v for k, v in form.items() if k.startswith("ShipmentNumber_") and v}

        for sn in ship_nums:
            shp = model.query(RFT_Shipment).filter_by(ShipmentNumber=sn).first()
            if not shp:
                not_found.append(sn)
                continue

            any_change = False

            # Biyan / SADDAD fields
            for field in ("BiyanNumber", "SADDADNumber"):
                val = form.get(f"{field}_{sn}")
                if val:
                    setattr(shp, field, val)
                    any_change = True

            # Shipment-level status
            st = form.get(f"Status_{sn}")
            if st:
                model.add(RFT_StatusHistory(
                    EntityType ="Shipment",
                    EntityID   = shp.ShipmentID,
                    Status     = st,
                    StatusDate = datetime.utcnow(),
                    UpdatedBy  = session['username'],
                    Comments   = "Updated via createdShipments"
                ))
                # cascade to all containers if sailing / clearance
                lower = st.lower()
                if "sailing" in lower or "under clearance" in lower:
                    newstat = "IN-Transit" if "sailing" in lower else "Under Clearance"
                    for c in shp.containers:
                        model.add(RFT_StatusHistory(
                            EntityType ="Container",
                            EntityID   = c.ContainerID,
                            Status     = newstat,
                            StatusDate = datetime.utcnow(),
                            UpdatedBy  = session['username'],
                            Comments   = f"Auto when shipment status changed to: {st}"
                        ))
                any_change = True

            # PDF uploads
            for tag, folder in (("biyanPDF", BIYAN_FOLDER), ("saddadPDF", SADDAD_FOLDER)):
                f = request.files.get(f"{tag}_file_{sn}")
                if f and f.filename:
                    if f.filename.lower().endswith(".pdf"):
                        fn = secure_filename(f"{sn}_{tag}.pdf")
                        os.makedirs(folder, exist_ok=True)
                        f.save(os.path.join(folder, fn))
                        any_change = True
                    else:
                        flash(f"❌ {tag} for {sn} must be PDF", "danger")
                        return jsonify({"redirect": url_for("main.createdShipments")})

            if any_change:
                shp.LastUpdated   = datetime.utcnow()
                shp.LastUpdatedBy = session['username']
                updated.add(sn)

        model.commit()

        if updated:
            flash(f"✅ Updated: {', '.join(sorted(updated))}", "success")
        if not_found:
            flash(f"❌ Not found: {', '.join(sorted(not_found))}", "warning")
        return jsonify({"redirect": url_for("main.createdShipments")})

    # ─── GET ────────────────────────────────────────────────────────────

    # build the shipments + attach latest status
    H1 = aliased(RFT_StatusHistory)
    latest = (
      model.query(
        H1.EntityID.label("ShipmentID"),
        func.max(H1.StatusDate).label("mx")
      )
      .filter(H1.EntityType=="Shipment")
      .group_by(H1.EntityID)
      .subquery()
    )
    H2 = aliased(RFT_StatusHistory)
    q = (
      model.query(RFT_Shipment, H2.Status.label("ShipmentLevelStatus"))
      .outerjoin(latest, latest.c.ShipmentID==RFT_Shipment.ShipmentID)
      .outerjoin(H2, 
        (H2.EntityID==latest.c.ShipmentID) &
        (H2.StatusDate==latest.c.mx) &
        (H2.EntityType=="Shipment")
      )
    #   .order_by(RFT_Shipment.CreatedDate.desc())
    )
    shipments = []
    for shp, st in q:
        # print(shp.ShipmentLevelStatus)
        print(st)
        shp.ShipmentLevelStatus = st or ""
        shipments.append(shp)

    # columns metadata (no server‐side filtering)
    friendly = {
      "ShipmentNumber":      "Shipment #",
      "CreatedDate":         "Created",
      "BiyanNumber":         "Biyan #",
      "SADDADNumber":        "Saddad #",
      "ShipmentLevelStatus": "Status",
    }
    cols = get_table_metadata(RFT_Shipment, shipments, friendly)
    include = {"ShipmentNumber","BiyanNumber","SADDADNumber"}
    columns = [c for c in cols if c["name"] in include]

    # shipment‐level statuses for the Status <select>
    shipmentstatuses = [
      s.StatusName for s in model
        .query(RFT_StatusManagement)
        .filter_by(Level="Shipment Level")
        .order_by(RFT_StatusManagement.StatusName)
        .all()
    ]

    # existing PDF files
    existing_biyan  = os.listdir(BIYAN_FOLDER)  if os.path.isdir(BIYAN_FOLDER)  else []
    existing_saddad = os.listdir(SADDAD_FOLDER) if os.path.isdir(SADDAD_FOLDER) else []

    return render_template("createdShipments.html",
      rows                  = shipments,
      columns               = columns,
      shipmentstatuses      = shipmentstatuses,
      existing_biyan_files  = existing_biyan,
      existing_saddad_files = existing_saddad
    )

@bp.route("/updateShipments/<shipment_id>", methods=["GET", "POST"])
@login_required
def updateShipments(shipment_id):
    if request.method == "POST":
        print("debug: request received ")
        # -- Process Shipment Details --
        shipment_data = {
            "bl_number": request.form.get("bl_number"),
            "lc_number": request.form.get("lc_number"),
            "origin_country": request.form.get("origin_country"),
            "destination_country": request.form.get("destination_country"),
            "OriginPort": request.form.get("OriginPort"),
            "DestinationPort": request.form.get("DestinationPort"),
            "shipping_line": request.form.get("shipping_line"),
            "cc_agent": request.form.get("cc_agent"),         
            
            #####################################
            # Estimated time section
            "eta_origin": request.form.get("eta_origin"),
            "etd_origin": request.form.get("etd_origin"),
            "eta_destination": request.form.get("eta_destination"),
            "etd_destination": request.form.get("etd_destination"),
            "eta_wh": request.form.get("eta_wh"),
            "ecc_date": request.form.get("ecc_date"),
            
            #####################################
            # Cost Section
            "freight_cost":             request.form.get("freight_cost"),
            "custom_duties_fob":        request.form.get("custom_duties_fob"),
            "value_declared_customs":   request.form.get("value_declared_customs"),
            "clearance_transport":      request.form.get("clearance_transport"),
            "saber_saddad":             request.form.get("saber_saddad"),
            "do_charges":               request.form.get("do_charges"),
            "penalties":                request.form.get("penalties"),
            "demurrage_charges":        request.form.get("demurrage_charges"),
            "yard_charges":             request.form.get("yard_charges"),
            "other_charges":            request.form.get("other_charges"),
            "cost_remarks":            request.form.get("cost_remarks"),
        }

        shipment = model.query(RFT_Shipment).filter_by(ShipmentID=shipment_id).first()
        if shipment:
            print("debug: Shipment found ")
            # 1) pull existing invoices for this shipment
            existing = {
                inv.InvoiceNumber: inv
                for inv in model.query(RFT_Invoices)
                                .filter_by(ShipmentID=shipment_id)
                                .all()
            }
            
            # 2) collect submitted pairs, skipping any with empty number or empty value
            submitted = []
            for num, val in zip(
                request.form.getlist("invoice_numbers"),
                request.form.getlist("invoice_values")
            ):
                num = (num or "").strip()
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    continue
                if not num:
                    continue
                submitted.append((num, v))
            
            # 3) upsert each submitted invoice
            for num, v in submitted:
                if num in existing:
                    inv = existing.pop(num)        # remove from existing so leftover = to-delete
                    inv.InvoiceValue = v
                    inv.UpdatedBy    = session.get("username", "system")
                    inv.UpdatedAt    = datetime.utcnow()
                else:
                    inv = RFT_Invoices(
                        ShipmentID    = shipment_id,
                        InvoiceNumber = num,
                        InvoiceValue  = v,
                        CreatedBy     = session.get("username","system")
                    )
                    model.add(inv)
            
            # 4) delete any invoices the user removed
            for orphan in existing.values():
                model.delete(orphan)
            
            shipment.BLNumber           = shipment_data["bl_number"]
            shipment.ShippingLine       = shipment_data["shipping_line"]
            shipment.CCAgent            = shipment_data["cc_agent"]
            
            shipment.POD                = shipment_data["DestinationPort"]
            shipment.DestinationCountry = shipment_data["destination_country"]
            shipment.OriginPort         = shipment_data["OriginPort"]
            shipment.OriginCountry      = shipment_data["origin_country"]
            
            # Estimated times
            shipment.ECCDate        = shipment_data["ecc_date"] #ECC
            shipment.ETAOrigin      = shipment_data["eta_origin"]
            shipment.ETDOrigin      = shipment_data["etd_origin"]
            shipment.ETADestination = shipment_data["eta_destination"]
            shipment.ETADestination = shipment_data["etd_destination"]
            shipment.ETAWH          = shipment_data["eta_wh"]
            
            # Cost fields
            shipment.FreightCost       = float(request.form.get("freight_cost",0) or 0)
            shipment.CustomDuties      = float(request.form.get("custom_duties_fob",0) or 0)
            shipment.ValueDecByCC      = float(request.form.get("value_declared_customs",0) or 0)
            shipment.DO_Port_Charges   = float(request.form.get("do_charges",0) or 0)
            shipment.ClearanceTransportCharges   = float(request.form.get("clearance_transport",0) or 0)
            shipment.SaberSADDAD       = float(request.form.get("saber_saddad",0) or 0)
            shipment.DemurrageCharges  = float(request.form.get("demurrage_charges",0) or 0)
            shipment.Penalties         = float(request.form.get("penalties",0) or 0)
            shipment.YardCharges       = float(request.form.get("yard_charges",0) or 0)
            shipment.OtherCharges      = float(request.form.get("other_charges",0) or 0)
    
            shipment.CostRemarks       = shipment_data["cost_remarks"]
            
            shipment.LastUpdatedBy = session.get('username', 'system')
            shipment.LastUpdated = datetime.utcnow()

        # -- Process Non-PO Items (Cargo) -- TODO
        cargo_items = []
        if request.form.get("non_po_items"):
            idx = 0
            while True:
                supplier = request.form.get(f"cargo[{idx}][supplier]")
                if supplier is None:
                    break
                cargo_items.append({
                    "supplier": supplier,
                    "po": request.form.get(f"cargo[{idx}][po]"),
                    "article": request.form.get(f"cargo[{idx}][article]"),
                    "qty": request.form.get(f"cargo[{idx}][qty]"),
                    "value": request.form.get(f"cargo[{idx}][value]")
                })
                idx += 1
            # Save cargo_items as needed in your model

        # --- 3) Per-container upsert + deletion logic ---
        # idx = 0
        # while True:
        #     key = f"containers[{idx}][container_number]"
        #     if key not in request.form:
        #         break
            
        #     cn  = request.form[key]
        #     print(f"Adding container {idx} where key is {cn}")
        #     # find or new
        #     cont = (
        #         model.query(RFT_Container)
        #              .filter_by(ShipmentID=shipment_id,
        #                         ContainerNumber=cn)
        #              .first()
        #     ) or RFT_Container(
        #         ShipmentID = shipment_id,
        #         ContainerNumber = cn,
        #         UpdatedBy = session.get("username","system"),
        #     )

        #     # update its fields
        #     cont.ContainerType      = request.form.get(f"containers[{idx}][container_type]")
        #     cont.ATAOrigin          = request.form.get(f"containers[{idx}][ata_op]")     
        #     cont.ATDOrigin          = request.form.get(f"containers[{idx}][atd_op]")     
        #     cont.ATADP              = request.form.get(f"containers[{idx}][ata_dp]")    
        #     cont.ATDDPort           = request.form.get(f"containers[{idx}][atd_dp]")
        #     cont.ATAWH              = request.form.get(f"containers[{idx}][ata_wh]")
        #     cont.ATAWH              = request.form.get(f"containers[{idx}][ata_wh]")
        #     cont.YardInDate         = request.form.get(f"containers[{idx}][yard_in_date]")
        #     cont.YardOutDate        = request.form.get(f"containers[{idx}][yard_out_date]")
        #     cont.ContainerRemarks   = request.form.get(f"containers[{idx}][container_remarks]")  
            
        #     cont.CCDate             = request.form.get(f"containers[{idx}][ccdate]") #cc date ACTUAL
            
        #     cont.UpdatedBy          = session.get("username","system")
        #     cont.UpdatedAt          = datetime.utcnow()

        #     # add if new
        #     model.add(cont)
            
        #     # --- container items JSON (one hidden input per container) ---
        #     raw = request.form.get(f"containers[{idx}][items]")
        #     print(raw)
        #     if raw:
        #         print("YES")
        #         try:
        #             items = json.loads(raw)
        #         except json.JSONDecodeError:
        #             items = []
        #         for it in items:
        #             # pull the ShipmentPOLineID straight from the JSON
        #             poline_id = it.get("poline_id")
        #             sel_qty   = it.get("selected_qty", 0)
            
        #             if not poline_id:
        #                 # shouldn't happen if front-end validation is correct
        #                 print(f"container:{cont.ContainerID} missing poline_id in items JSON")
        #                 continue
            
        #             # look for an existing container‐line
        #             existing = (
        #                 model.query(RFT_ContainerLine)
        #                         .filter_by(
        #                             ContainerID      = cont.ContainerID,
        #                             ShipmentPOLineID = poline_id
        #                         )
        #                         .one_or_none()
        #             )
            
        #             if existing:
        #                 # update the quantity (and touch the LastUpdatedBy)
        #                 existing.QtyInContainer  = sel_qty
        #                 existing.LastUpdatedBy  = session.get("username", "system")
        #             else:
        #                 # insert a new row
        #                 cl = RFT_ContainerLine(
        #                     ContainerID       = cont.ContainerID,
        #                     ShipmentPOLineID  = poline_id,
        #                     QtyInContainer    = sel_qty,
        #                     LastUpdatedBy     = session.get("username", "system"),
        #                 )
        #                 model.add(cl)
        #     idx += 1
        submitted_numbers = set()
        idx = 0
        while True:
            key = f"containers[{idx}][container_number]"
            if key not in request.form:
                break
            cn = request.form.get(key).strip()
            # print(f"+{cn}+")
            if cn and cn != '':
                submitted_numbers.add(cn)
            idx += 1

        # 2) delete any containers in DB that belong to this shipment but whose number
        #    was NOT submitted back in the form
        to_delete = (
            model.query(RFT_Container)
                 .filter_by(ShipmentID=shipment_id)
                 .filter(
                         or_(
                                # explicitly catch NULL container numbers
                                RFT_Container.ContainerNumber.is_(None),
                                # explicitly catch empty‐string container numbers
                                RFT_Container.ContainerNumber == '',
                                # plus any non-blank container not resubmitted
                                ~RFT_Container.ContainerNumber.in_(submitted_numbers)
                            )
                        )
                 .all()
        )
        for orphan in to_delete:
            model.delete(orphan)

        # 3) now re-iterate through the form and upsert each container
        idx = 0
        while True:
            key = f"containers[{idx}][container_number]"
            if key not in request.form:
                break

            cn = request.form.get(key).strip()
            # SKIP any blank container_number entries
            if not cn:
                idx += 1
                continue
            
            cont = (
                model.query(RFT_Container)
                     .filter_by(ShipmentID=shipment_id, ContainerNumber=cn)
                     .first()
            ) or RFT_Container(
                ShipmentID      = shipment_id,
                ContainerNumber = cn,
                UpdatedBy       = session.get("username","system")
            )

            # update fields
            cont.ContainerType      = request.form.get(f"containers[{idx}][container_type]")
            cont.CCDate             = request.form.get(f"containers[{idx}][ccdate]")
            cont.ATAOrigin          = request.form.get(f"containers[{idx}][ata_op]")
            cont.ATDOrigin          = request.form.get(f"containers[{idx}][atd_op]")
            cont.ATADP              = request.form.get(f"containers[{idx}][ata_dp]")
            cont.ATDDPort           = request.form.get(f"containers[{idx}][atd_dp]")
            cont.ATAWH              = request.form.get(f"containers[{idx}][ata_wh]")
            cont.YardInDate         = request.form.get(f"containers[{idx}][yard_in_date]")
            cont.YardOutDate        = request.form.get(f"containers[{idx}][yard_out_date]")
            cont.ContainerRemarks   = request.form.get(f"containers[{idx}][container_remarks]")
            cont.UpdatedBy          = session.get("username","system")
            cont.UpdatedAt          = datetime.utcnow()

            model.add(cont)
            model.flush() 
            
            current_status = request.form.get(f"containers[{idx}][current_status]")
            planed_status  = request.form.get(f"containers[{idx}][planed_status]")
            
            # if Status, add a history entry
            if current_status and current_status != "":
                h = RFT_StatusHistory(
                    EntityType  = "Container",
                    EntityID    = cont.ContainerID,
                    Status      = current_status,
                    StatusDate  = datetime.utcnow(),
                    UpdatedBy   = session['username'],
                    Comments    = "Updated from Update shipmet mainpage"
                )
                model.add(h)
            if planed_status and planed_status != "":
                i = RFT_StatusHistory(
                    EntityType  = "Planed-Container",
                    EntityID    = cont.ContainerID,
                    Status      = planed_status,
                    StatusDate  = datetime.utcnow(),
                    UpdatedBy   = session['username'],
                    Comments    = "Updated from Update shipment mainpage"
                )
                model.add(i)
            

            # upsert its lines exactly as before
            raw = request.form.get(f"containers[{idx}][items]")
            if raw:
                try:
                    items = json.loads(raw)
                except json.JSONDecodeError:
                    items = []
                # collect submitted poline_ids for this container
                submitted_polines = {it["poline_id"] for it in items if it.get("poline_id")}
                # delete any container‐lines for this container not resubmitted:
                exists_cls = (
                  model.query(RFT_ContainerLine)
                       .filter_by(ContainerID=cont.ContainerID)
                       .all()
                )
                for cl in exists_cls:
                    if cl.ShipmentPOLineID not in submitted_polines:
                        model.delete(cl)

                # now upsert each line
                for it in items:
                    pid = it.get("poline_id")
                    qty = it.get("selected_qty", 0)
                    if not pid:
                        continue
                    existing = (
                        model.query(RFT_ContainerLine)
                             .filter_by(ContainerID=cont.ContainerID,
                                        ShipmentPOLineID=pid)
                             .one_or_none()
                    )
                    if existing:
                        existing.QtyInContainer = qty
                        existing.LastUpdatedBy = session.get("username","system")
                    else:
                        cl = RFT_ContainerLine(
                            ContainerID       = cont.ContainerID,
                            ShipmentPOLineID  = pid,
                            QtyInContainer    = qty,
                            LastUpdatedBy     = session.get("username","system")
                        )
                        model.add(cl)
                        
            idx += 1
            
        model.commit()
        
        flash("Shipment updated successfully.", "success")
        return redirect(url_for("main.updateShipments", shipment_id=shipment_id))

    ###########################################
    # -- GET request: load existing shipment data --

    current_form_data = (
        model
        .query(RFT_Shipment)
        .options(
            joinedload(RFT_Shipment.po_lines).joinedload(RFT_ShipmentPOLine.po_line)
            , joinedload(RFT_Shipment.containers).joinedload(RFT_Container.lines)
        )
        .get(shipment_id)
    )
    
    if not current_form_data:
        abort(404)
    
    shipment_number = model.query(RFT_Shipment.ShipmentNumber).filter(RFT_Shipment.ShipmentID == shipment_id).first()
    
    rows = (
        model
        .query(
            RFT_ShipmentPOLine.ShipmentPOLineID,
            RFT_PurchaseOrderLine.POID,
            RFT_PurchaseOrder.PONumber,
            RFT_PurchaseOrderLine.Article,
            RFT_PurchaseOrderLine.SapItemLine,
            RFT_ShipmentPOLine.QtyShipped,
            RFT_Container.ContainerID,
            RFT_ContainerLine.QtyInContainer
        )
        # join back to PO-Line & PO to get PO number & article text
        .join(RFT_PurchaseOrderLine,
              RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)
        .join(RFT_PurchaseOrder,
              RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID)
        # left-join into your container-line table
        .outerjoin(RFT_ContainerLine,
                   RFT_ContainerLine.ShipmentPOLineID ==
                     RFT_ShipmentPOLine.ShipmentPOLineID)
        # then bring in the actual Container row so we can get ContainerNumber (or any other fields)
        .outerjoin(RFT_Container,
                   RFT_Container.ContainerID ==
                     RFT_ContainerLine.ContainerID)
        .filter(RFT_ShipmentPOLine.ShipmentID == shipment_id)
        .all()
    )
    
    unique_cids = [c.ContainerID for c in current_form_data.containers]

    # 1b) build map from real ContainerID -> 0-based index
    cid_to_index = { cid: idx for idx, cid in enumerate(unique_cids) }

    # 1c) now tag each row with that index
    report_data_json = [
        {
        "ShipmentPOLineID"  : spoid,
        "POID"              : poid,
        "PONumber"          : pon,
        "SapItemLine"       : sil,
        "Article"           : art,
        "Qty"               : shipped,
        "ContainerID"       : cid,
        "container_index"   : cid_to_index.get(cid, 0),
        "QtyInContainer"    : in_ctn or 0
        }
        for (spoid, poid, pon, art, sil, shipped, cid, in_ctn) in rows
    ]
    
    # fetch all invoices for this shipment
    invoices = (
        model.query(RFT_Invoices)
            .filter(RFT_Invoices.ShipmentID == shipment_id)
            .order_by(RFT_Invoices.InvoiceID)
            .all()
    )
    
    
    # Grab all Container Level statuses, unconditionally
    containerstatuses = (
        model
        .query(RFT_StatusManagement)
        .filter(RFT_StatusManagement.Level == 'Container Level')
        .all()
    )
    
    # 2) for each container, look up its latest “live” and “planned” status
    for cont in current_form_data.containers:
        # latest live
        hist = (
          model
          .query(RFT_StatusHistory)
          .filter_by(EntityType="Container", EntityID=cont.ContainerID)
          .order_by(RFT_StatusHistory.StatusDate.desc())
          .first()
        )
        cont.current_status = hist.Status if hist else ""

        # latest planned
        hist2 = (
          model
          .query(RFT_StatusHistory)
          .filter_by(EntityType="Planed-Container", EntityID=cont.ContainerID)
          .order_by(RFT_StatusHistory.StatusDate.desc())
          .first()
        )
        cont.planned_status = hist2.Status if hist2 else ""
    
    custom_agents     = model.query(RFT_CustomAgents).all()
    origin_ports      = model.query(RFT_OriginPorts).all()
    shipping_lines    = model.query(RFT_ShipingLines).all()
    destination_ports = model.query(RFT_DestinationPorts).all()
    countries = get_countries()
    
    return render_template(
        "updateShipments.html",
        report_data         =current_form_data,
        report_data_json    =report_data_json,
        unique_cids      = unique_cids,
        shipment_id         =shipment_id,
        shipment_number     = shipment_number,
        containerstatuses   = containerstatuses,
        
        countries = countries,
        
        custom_agents      = custom_agents,
        origin_ports       = origin_ports,
        shipping_lines     = shipping_lines,
        destination_ports  = destination_ports,
        invoices           = invoices
    )

@bp.route("/update_containers", methods=["GET", "POST"])
def update_containers():
    # --- 1) master filter‐lists ---
    # All brands (distinct) from your PO → Shipment → Container chain
    all_brands = [
        b for (b,) in model
            .query(RFT_PurchaseOrder.Brand)
            # .join(RFT_Shipment, RFT_PurchaseOrder.POID==RFT_Shipment.POID)
            .distinct()
            .order_by(RFT_PurchaseOrder.Brand)
            .all()
    ]
    # All months (yyyy-MM) from the PO date
    all_months = [
        m for (m,) in model
            .query(func.format(RFT_PurchaseOrder.CreatedDate, "yyyy-MM"))
            .distinct()
            .order_by(literal_column("1"))
            .all()
    ]
    # All container‐level statuses from your master table
    all_status = [
        s.StatusName for s in model
            .query(RFT_StatusManagement)
            .filter_by(Level="Container Level")
            .order_by(RFT_StatusManagement.StatusName)
            .all()
    ]

    # --- 2) what the user selected (or “All”) ---
    sel_brands = request.values.getlist("brand_filter") or all_brands
    sel_months = request.values.getlist("month_filter") or all_months
    sel_status = request.values.getlist("status_filter") or all_status

    # --- 3) subquery: for each container, its latest status date ---
    Hist1 = aliased(RFT_StatusHistory)
    latest = (
        model
        .query(
            Hist1.EntityID.label("ContainerID"),
            func.max(Hist1.StatusDate).label("max_date")
        )
        .filter(Hist1.EntityType == "Container")
        .group_by(Hist1.EntityID)
        .subquery()
    )

    # --- 4) join that back to the history table to get the actual status text ---
    Hist2 = aliased(RFT_StatusHistory)
    q = (
      model
      .query(RFT_Container,
             RFT_Shipment.ShipmentNumber.label("ShipmentNumber"),
             Hist2.Status.label("ContainerLevelStatus"))
        
      .outerjoin(latest, latest.c.ContainerID == RFT_Container.ContainerID)
      .outerjoin(
          Hist2,
          (Hist2.EntityID   == latest.c.ContainerID) &
          (Hist2.StatusDate == latest.c.max_date)    &
          (Hist2.EntityType == "Container")
      )
      # your other joins (shipment, purchase‐order chain) if you need brand/month filters…
      .join(RFT_Shipment, RFT_Container.ShipmentID == RFT_Shipment.ShipmentID)
      .join(RFT_ShipmentPOLine,
            RFT_Shipment.ShipmentID == RFT_ShipmentPOLine.ShipmentID)
      .join(RFT_PurchaseOrderLine,
            RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)
      .join(RFT_PurchaseOrder,
            RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID)
      .filter(
         RFT_PurchaseOrder.Brand.in_(sel_brands),
         func.format(RFT_PurchaseOrder.CreatedDate,"yyyy-MM").in_(sel_months),
         # allow either a chosen status *or* no status at all
         or_(
           Hist2.Status.in_(sel_status),
           Hist2.Status.is_(None)
         )
      )
      .distinct()
      .order_by(RFT_Container.ContainerID)
    )
    
    # unpack into a list of containers, tacking on the latest status
    containers = []
    for cont, ship_no, status in q.all():
        cont.ShipmentNumber       = ship_no
        cont.ContainerLevelStatus = status
        containers.append(cont)

    # --- 5) build your columns metadata exactly as before ---
    friendly = {
      "ShipmentNumber":         "Shipment No.",
      "ContainerNumber":        "Container No.",
      "ContainerType":          "Type",
      "ContainerLevelStatus":   "Status",
      "ATAOrigin":              "Loading Time",
      "ATDOrigin":              "ATD Origin",
      "ATADP":                  "ATA Dest. Port",
      "ATDDPort":               "ATD Dest. Port",
      "ATAWH":                  "ATA Warehouse",
      "YardInDate":             "Yard In",
      "YardOutDate":            "Yard Out",
      "ContainerRemarks":       "Remarks",
    }
    cols = get_table_metadata(RFT_Container, containers, friendly)
    exclude = {"ContainerID","CreatedBy","UpdatedBy","UpdatedAt"}
    columns = [c for c in cols if c["name"] not in exclude]
    
    # Having issues if no containers have a status os below solution
    # Make sure Status is always in there, even if all rows have .ContainerLevelStatus == None
    if not any(c["name"]=="ContainerLevelStatus" for c in columns):
        columns.insert(
        # index - after ContainerType:
        next(i for i,c in enumerate(columns) if c["name"]=="ContainerType")+1,
        {
            "name":         "ContainerLevelStatus",
            "label":        "Status",
            "type":         "String",
            "filter_type":  "select",
            "options":      all_status
        }
        )
    
    # Adding ShipmentNumber
    if not any(c["name"]=="ShipmentNumber" for c in columns):
        columns.insert(
        # index - before ContainerNumber:
        next(i for i,c in enumerate(columns) if c["name"]=="ContainerNumber")-13,
        {
            "name":         "ShipmentNumber",
            "label":        "Shipment No.",
            "type":         "String",
            "filter_type":  "text"
            # "options":      all_status
        }
    )
    

    if request.method == "POST":
        # how many rows did we render?
        total = int(request.form.get("containers_count", 0))

        for idx in range(total):
            cid = request.form.get(f"containers[{idx}][id]", type=int)
            if not cid:
                continue

            # fetch the real container record
            rec = model.get(RFT_Container, cid)
            if not rec:
                continue

            # any date fields we want to parse?
            def parse_date(field):
                s = request.form.get(f"containers[{idx}][{field}]", "").strip()
                return datetime.strptime(s, "%Y-%m-%d").date() if s else None

            # 4) apply all the inputs back to the container
            # rec.ContainerLevelStatus = request.form.get(f"containers[{idx}][status]", rec.ContainerLevelStatus)
            rec.ATAOrigin            = parse_date("ata_op")
            rec.ATDOrigin            = parse_date("atd_op")
            rec.ATADP                = parse_date("ata_dp")
            rec.ATDDPort             = parse_date("atd_dp")
            rec.ATAWH                = parse_date("ata_wh")
            rec.YardInDate           = parse_date("yard_in_date")
            rec.YardOutDate          = parse_date("yard_out_date")
            rec.ContainerRemarks     = request.form.get(f"containers[{idx}][remarks]", "").strip()

        model.commit()
        flash("All container records updated.", "success")
        return redirect(url_for("dashboard.update_containers"))

    return render_template(
      "update_containers.html",
      rows             = containers,
      columns          = columns,
      all_brands       = all_brands,
      all_months       = all_months,
      all_status       = all_status,
      sel_brands       = sel_brands,
      sel_months       = sel_months,
      sel_status       = sel_status,
      containers_count = len(containers)
    )

@bp.route("/inTransitDetails", methods=["GET", "POST"])
def inTransitDetails():
    # --- 1) open_qty per Article from the PO lines in the view 
    open_subq = (
        model
        .query(
            FreightTrackingView.Article.label("article"),
            FreightTrackingView.Brand.label("brand"),
            func.sum(FreightTrackingView.Qty).label("open_qty")
        )
        .group_by(FreightTrackingView.Article, FreightTrackingView.Brand)
        .subquery()
    )

    # --- 2) in_transit from shipment lines, EXCLUDING any delivered shipments ---
    intransit_subq = (
        model
        .query(
            FreightTrackingView.Brand.label("brand"),
            FreightTrackingView.Article.label("article"),
            func.sum(FreightTrackingView.QtyShipped).label("intransit_qty")
        )
        .filter(FreightTrackingView.ContainerLevelStatus not in('Delivered to WH', 'Direct to customer')) #TODO
        .group_by(FreightTrackingView.Brand,FreightTrackingView.Article)
        .subquery()
    )

    # --- 3) join them & compute balance ---
    summary_table = (
        model
        .query(
            open_subq.c.brand,
            open_subq.c.article,
            open_subq.c.open_qty,
            func.coalesce(intransit_subq.c.intransit_qty, 0)
                .label("intransit_qty"),
            (
                open_subq.c.open_qty
                - func.coalesce(intransit_subq.c.intransit_qty, 0)
            ).label("balance_qty")
        )
        .outerjoin(
            intransit_subq,
            # open_subq.c.article == intransit_subq.c.article
            and_(
                open_subq.c.brand   == intransit_subq.c.brand,
                open_subq.c.article == intransit_subq.c.article
            )
        )
        .order_by(open_subq.c.brand, open_subq.c.article)
        .all()
    )
    
    # Manually declare your summary fields & labels
    columns = [
      {"name":"article",       "label":"Article",        "filter_type":"text"  },
      {"name":"brand",         "label":"Brand",          "filter_type":"text"  },
      {"name":"open_qty",      "label":"Open Qty",       "filter_type":"text"  },
      {"name":"intransit_qty", "label":"In‑Transit Qty",  "filter_type":"text"  },
      {"name":"balance_qty",   "label":"Balance Qty",     "filter_type":"text"  },
    ]
    
    return render_template(
      "inTransitDetails.html",
      summary=summary_table,
      columns=columns
    )

@bp.route("/coastAnalysis", methods=["GET", "POST"])
def coastAnalysis():
    # 1) grab everything from the view
    rows = model.query(FreightTrackingView).filter(FreightTrackingView.ShipmentID.is_not(None)).all()

    # 2) roll up per shipment
    summary_map = {}
    for r in rows:
        sid = r.ShipmentID
        if sid not in summary_map:
            summary_map[sid] = {
                "shipment_id":       sid,
                "shipment_number":   r.ShipmentNumber,
                "bill_of_lading":    r.BillOfLading,
                "port_of_loading":   r.LoadingPort,
                "port_of_delivery":  r.PortOfArrival,
                "brands":            set(),
                "po_numbers":        set(),
                "total_qty_shipped": 0,
                "total_value_shipped": 0,
                "container_ids":     set(),
                # initialize cost buckets
                "invoice_total":     Decimal('0'),
                "freight_cost":      Decimal('0'),
                "custom_duties":     Decimal('0'),
                "saber_saddad":      Decimal('0'),
                "penalties":         Decimal('0'),
                "demurrage_charges": Decimal('0'),
                "others":            Decimal('0'),
            }
        grp = summary_map[sid]

        # collect
        if r.Brand:     grp["brands"].add(r.Brand)
        if r.PONumber:  grp["po_numbers"].add(r.PONumber)
        grp["total_qty_shipped"] += (r.QtyShipped or 0)
        grp["total_value_shipped"] += (r.TotalValue or 0)
        if r.ContainerID:
            grp["container_ids"].add(r.ContainerID)

        # cost fields (assume one‐time per shipment for shipment‐level costs)
        grp["freight_cost"]   = r.FreightCost   or grp["freight_cost"]
        grp["invoice_total"]   = r.InvoiceValue   or grp["invoice_total"]
        grp["custom_duties"]  = Decimal(r.CustomDuties or grp["custom_duties"])
        grp["saber_saddad"]   = Decimal(r.SaberSADDAD or grp["saber_saddad"])

        # container‐level costs we sum across all containers
        grp["penalties"]         += Decimal(r.Penalties or 0)
        grp["demurrage_charges"] += Decimal(r.DemurrageCharges or 0)
        grp["others"]            += Decimal(r.OtherCharges or 0)

    # 3) finalize each summary record
    summary = []
    for grp in summary_map.values():
        # flatten sets to comma‑strings and counts
        grp["brands"]           = ", ".join(sorted(grp["brands"]))
        grp["po_numbers"]       = ", ".join(sorted(grp["po_numbers"]))
        grp["container_count"]  = len(grp["container_ids"])

        # compute a grand total value (you can adjust which fields to include)
        grp["total_expense"] = (
            grp["freight_cost"]
          + grp["custom_duties"]
          + grp["penalties"]
          + grp["demurrage_charges"]
          + grp["saber_saddad"]
          + grp["others"]
        )

        summary.append(grp)

    # 3) build column metadata exactly as get_table_metadata would—
    #    but here we do it by hand on our summary dicts:
    sample = summary[0] if summary else {}
    cols = []
    # define the order and labels you want
    specs = [
      ("shipment_number",   "Shipment #"),
      ("bill_of_lading",    "B/L"),
      ("po_numbers",        "PO Numbers"),
      ("brands",            "Brands"),
      ("port_of_loading",   "Port of Loading"),
      ("port_of_delivery",  "Port of Delivery"),
      ("total_qty_shipped", "Total Qty Shipped"),
      ("total_value_shipped", "Total Value Shipped"),
      ("container_count",   "# Containers"),
      ("freight_cost",      "Freight Cost"),
      ("custom_duties",     "Custom Duties"),
      ("saber_saddad",      "Saber SADDAD"),
      ("penalties",         "Penalties"),
      ("demurrage_charges","Demurrage Charges"),
      ("others",            "Others"),
      ("total_expense",       "Total Expense"),
      ("invoice_total",       "Total Invoice"),
    ]

    # gather distinct values for any “select”‐style filters
    distinct = defaultdict(set)
    for row in summary:
      for key,_ in specs:
        distinct[key].add(row[key])

    for key,label in specs:
      dtype = type(sample.get(key)).__name__
      col = {"name":key, "label":label}
      # if small set of distinct strings → select
      vals = {v for v in distinct[key] if v not in (None,"")}
      if dtype=="str" and 1 < len(vals) <= 50:
        col["filter_type"] = "select"
        col["options"]     = sorted(vals)
      # numeric → text search (you could extend to min/max)
      elif dtype in ("int","float"):
        col["filter_type"] = "text"
      else:
        col["filter_type"] = "text"
      cols.append(col)

    return render_template(
      "coastAnalysis.html",
      columns = cols,
      rows    = summary
    )
    
@bp.route("/shipment/<int:shipment_id>")
def view_shipment_details(shipment_id):
    # Shipment
    shipment = model.query(RFT_Shipment).filter_by(ShipmentID=shipment_id).first()

    if not shipment:
        flash("Shipment not found", "danger")
        return redirect(url_for("home"))

    # Purchase Order(s)
    po_lines = (
        model.query(RFT_PurchaseOrderLine, RFT_PurchaseOrder)
        .join(RFT_PurchaseOrder, RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID)
        .join(RFT_ShipmentPOLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)
        .filter(RFT_ShipmentPOLine.ShipmentID == shipment_id)
        .all()
    )

    # Containers
    containers = model.query(RFT_Container).filter_by(ShipmentID=shipment_id).all()

    # Status History
    status_history = (
        model.query(RFT_StatusHistory)
        .filter_by(EntityID=shipment_id, EntityType="Shipment")
        .order_by(RFT_StatusHistory.StatusDate.desc())
        .all()
    )

    return render_template(
        "view.html",
        shipment=shipment,
        po_lines=po_lines,
        containers=containers,
        status_history=status_history
    )

@bp.route("/upload_file", methods=['GET', 'POST'])
def upload_file():
     # ———————— 0) grab all table names from the current database
    inspector   = inspect(model.bind)
    table_namesz = inspector.get_table_names()
    table_names = []
    for name in table_namesz:
        # print(name)
        if str(name).startswith("RFT"):
            table_names.append(name)
    # ———————— 1) pull your existing upload‐batches summary
    batch_rows = (
      model
      .query(
        RFT_PurchaseOrderUpload.UploadBatch.label("batch_id"),
        func.count(func.distinct(RFT_PurchaseOrderUpload.PurchaseOrder))
            .label("num_unique_po"),
        func.min(RFT_PurchaseOrderUpload.UploadedAt)
            .label("upload_time"),
        RFT_PurchaseOrderUpload.UploadedBy.label("uploaded_by"),
      )
      .group_by(
        RFT_PurchaseOrderUpload.UploadBatch,
        RFT_PurchaseOrderUpload.UploadedBy
      )
      .order_by(func.min(RFT_PurchaseOrderUpload.UploadedAt).desc())
      .all()
    )
    # turn them into plain dicts
    batches = [
      {
        "batch_id"     : row.batch_id,
        "num_unique_po": row.num_unique_po,
        "upload_time"  : row.upload_time,
        "uploaded_by"  : row.uploaded_by
      }
      for row in batch_rows
    ]

    # defaults
    batch_id   = None
    table_view = []
    
    if request.method == 'POST' and 'upload-file' in request.form:
        # Get the uploaded file from the form
        file = request.files['upload-file']

        # Check if the file is an Excel file
        if file.filename.endswith('.xlsx') or file.filename.endswith('.xls'):
            # Read the Excel file into a pandas DataFrame
            df = pd.read_excel(file)

            # Define the correct column names in the order of your database table
            correct_columns = [
                'PurchaseOrder',
                'Item',
                'Type',
                'PGR',
                'VendorSupplyingSite',
                'Article',
                'ShortText',
                'MdseCat',
                'Site',
                'SLoc',
                'DocDate',
                'Quantity',
                'Netprice',
                'QtyToBeDelivered',
                'ValueToBeDelivered',
            ]

            # Replace the DataFrame's columns with the correct database column names
            df.columns = correct_columns
            date_format = "%d.%m.%Y"

            df['PurchaseOrder'] = df['PurchaseOrder'].astype(str)
            df['Item'] = df['Item'].astype(str)
            df['Type'] = df['Type'].astype(str)
            df['PGR'] = df['PGR'].astype(str)
            df['VendorSupplyingSite'] = df['VendorSupplyingSite'].astype(str)
            df['Article'] = df['Article'].astype(str)
            df['ShortText'] = df['ShortText'].astype(str)
            df['MdseCat'] = df['MdseCat'].astype(str)
            df['Site'] = df['Site'].astype(str)
            df['SLoc'] = df['SLoc'].astype(str)
            df['Quantity'] = df['Quantity'].astype(int)
            df['Netprice'] = df['Netprice'].astype(int) # INTIGER
            df['QtyToBeDelivered'] = df['QtyToBeDelivered'].astype(int)
            df['ValueToBeDelivered'] = df['ValueToBeDelivered'].astype(int)
            df['DocDate'] = pd.to_datetime(df['DocDate'], format=date_format, errors='coerce')
            df['UploadBatch'] = str(uuid.uuid4())
            df['UploadedBy'] = session.get('username', 'system')


            # Bulk insert via pandas:
            df.to_sql(
                RFT_PurchaseOrderUpload.__tablename__,
                model.bind,
                if_exists='append',
                index=False
            ) 
            
            # Show the staging rows back to user for validation
            table_view = df.to_dict(orient='records')
            batch_id   = df['UploadBatch'].iloc[0]
            
            # re‐refresh your batches list to include the one we just inserted
            batch_rows = (
              model
              .query(
                RFT_PurchaseOrderUpload.UploadBatch.label("batch_id"),
                func.count(func.distinct(RFT_PurchaseOrderUpload.PurchaseOrder))
                    .label("num_unique_po"),
                func.min(RFT_PurchaseOrderUpload.UploadedAt)
                    .label("upload_time"),
                RFT_PurchaseOrderUpload.UploadedBy.label("uploaded_by"),
              )
              .group_by(
                RFT_PurchaseOrderUpload.UploadBatch,
                RFT_PurchaseOrderUpload.UploadedBy
              )
              .order_by(func.min(RFT_PurchaseOrderUpload.UploadedAt).desc())
              .all()
            )
            batches = [
              {
                "batch_id"     : r.batch_id,
                "num_unique_po": r.num_unique_po,
                "upload_time"  : r.upload_time,
                "uploaded_by"  : r.uploaded_by
              }
              for r in batch_rows
            ]

    if request.method == "POST" and "finalize_upload" in request.form:
        batch_id = request.form.get("finalize_upload")
        etl_purchase_orders(batch_id)
        flash("Imported into PurchaseOrder/POLines tables", "success")
        return redirect(url_for('main.upload_file'))
    # Handle export if requested for batches
    if request.method == "POST" and "export_batch" in request.form:
        export_Batch = request.form.get("export_batch")
        qyery = text("""
                SELECT * 
                FROM RFT_PurchaseOrderUpload
                WHERE UploadBatch = :export_Batch        
            """)
        records = model.execute(qyery, {"export_Batch": export_Batch})
        # records = qyery.all() 
        sheet_name = f"""RFT_Batch_{export_Batch}_{datetime.now().strftime("%d/%m/%Y")}"""
        table_view = []
        columns_of_tables = records.keys()
        table_view = [dict(zip(columns_of_tables, row)) for row in records.all()]

        return export_to_excel(sheet_name, table_view)
    
    # Export request for tables 
    if request.method == "POST" and "export_table" in request.form:
        export_table = request.form.get("export_table_name")
        qyery = text("""
                SELECT * 
                FROM :export_table    
            """)
        records = model.execute(qyery, {"export_table":export_table})
        # records = qyery.all() 
        sheet_name = f"""{export_table}_{datetime.now().strftime("%d/%m/%Y")}"""
        table_view = []
        columns_of_tables = records.keys()
        table_view = [dict(zip(columns_of_tables, row)) for row in records.all()]

        return export_to_excel(sheet_name, table_view)
    

    return render_template('upload_file.html', 
                           batch_id=batch_id, 
                           table_view = table_view,
                           table_names = table_names,
                           batches = batches) 

@bp.route("/expense_report", methods=["GET","POST"])
def expense_report():
    # read filter form-values
    sel_brands    = request.values.getlist("brand") or None
    sel_start     = request.values.get("start_date") or None
    sel_end       = request.values.get("end_date") or None

    rows    = fetch_expense_data(sel_brands, sel_start, sel_end)
    rows,  columns   = build_expense_columns(rows)

    print(rows)
    print(columns)
    
    return render_template("articleExpenseReport.html",
                           rows=rows, columns=columns,
                           sel_brands=sel_brands,
                           sel_start=sel_start,
                           sel_end=sel_end)

# TODO below code:
@bp.route("/expense_report_download", methods=["GET","POST"]) #TODO
def expense_report_download():
    # 1) base query 
    q = ( 
        model.query
        (
            FreightTrackingView.Brand.label("brand"),
            FreightTrackingView.Article.label("article"),
            FreightTrackingView.ShipmentNumber.label("shipment"),
            FreightTrackingView.POD.label("delivery_date"),
            (
                func.coalesce(func.sum(FreightTrackingView.FreightCost),0)
                + func.coalesce(func.sum(FreightTrackingView.CustomDuties, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.SaberSADDAD, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.DemurrageCharges, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.OtherCharges, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.DO_Port_Charges, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.Penalties, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.YardCharges, Numeric),0)
                + func.coalesce(func.sum(FreightTrackingView.ValueDecByCC, Numeric),0)
            ).label("total_expense")   
        ).group_by(
            FreightTrackingView.Brand,
            FreightTrackingView.Article,
            FreightTrackingView.ShipmentNumber,
            FreightTrackingView.POD
        )
    )
    
    if request.method == 'POST' and 'export' in request.form:
    
        # 1) collect filters from request.form (brand, date range)
        brands = request.form.getlist("brands[]")
        start  = request.form["start_date"]
        end    = request.form["end_date"]

        # 2) run the query into a DataFrame
        df = pd.read_sql(q.filter(...).statement, model.bind)  # or build list of dicts

        # 3) pivot if you like, or just leave as flat “long” table
        #    e.g. df.pivot_table(index=["brand","article"], columns="shipment", values="total_expense")

        # 4) write to Excel in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Expenses")
            # you can even add an Excel chart using xlsxwriter here…

        output.seek(0)
        return send_file(output,
                        attachment_filename="expense_report.xlsx",
                        as_attachment=True,
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    
    
    return render_template("articleExpenseReport.html")

@bp.route('/settings', methods=['GET','POST']) #TODO
@login_required
def settings():
    if request.method=='POST':
        # read the two JSON inputs
        order_json  = request.form['charts_order']
        layout_json = request.form['charts_layout']

        save('dashboard.charts_order',   order_json)
        save('dashboard.chart_layout',   layout_json)

        flash("Settings saved", "success")
        return redirect(url_for('settings'))

    # GET: just render form as above
    return render_template('settings.html')