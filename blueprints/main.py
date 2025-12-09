# blueprints/main.py
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    current_app,
    Blueprint,
    abort,
    send_file,
    send_from_directory,
)

import re
import random
import logging
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
    get_countries, fetch_expense_data, build_expense_columns, export_shipment_expense_report,
    export_po_report, build_po_report_df, build_po_columns, DELIVERED_STATUSES
)


bp = Blueprint('main', __name__, static_folder="./static")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "..", "static")

SHP_DOC_UPLOAD_FOLDER = os.path.join(
        STATIC_DIR, "uploads", "shipment_docs"
)

CC_UPLOAD_FOLDER = os.path.join(
        STATIC_DIR, "uploads", "cc_inv_doc"
)

BIYAN_FOLDER  = os.path.join(STATIC_DIR, "uploads", "Biyan-files")
SADDAD_FOLDER = os.path.join(STATIC_DIR, "uploads", "SADDAD-files")



log = logging.getLogger('werkzeug')
log.setLevel(logging.DEBUG)
# log.debug("something")

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


@bp.route('/set-theme', methods=['POST'])
@login_required
def set_theme():
    data = request.get_json() or {}
    theme = data.get('theme')
    if theme not in ('light','dark'):
        return jsonify(error="bad theme"), 400

    current_user.theme = theme
    
    user = model.query(Users).filter(Users.Username == current_user.username).first()
    user.theme = theme
    
    model.commit()
    return jsonify(success=True)

@bp.route("/", methods=['GET', 'POST']) # DASHBOARD
def call_home():
    return render_template("dashboard/A-new_DASH.html")

@bp.route("/freight_trackingView", methods=["GET", "POST"])
def freight_trackingView():
    model = None
    export_brands = None
    model = Session()
    
    limit_num = request.form.get("limit", 100)
    ofset_num = 100
    
    # ── 1) Latest Purchase-Order status ────────────────────────────────────
    Hpo1 = aliased(RFT_StatusHistory)
    latest_po = (
        model
        .query(
            Hpo1.EntityID.label("POID"),
            func.max(Hpo1.StatusDate).label("mx_po")
        )
        .filter(Hpo1.EntityType == literal("Purchase Order"))
        .group_by(Hpo1.EntityID)
        .subquery()
    )
    Hpo2 = aliased(RFT_StatusHistory)

    # ── 2) Latest Shipment status ─────────────────────────────────────────
    Hs1 = aliased(RFT_StatusHistory)
    latest_shp = (
        model
        .query(
            Hs1.EntityID.label("ShipmentID"),
            func.max(Hs1.StatusDate).label("mx_shp")
        )
        .filter(Hs1.EntityType == literal("Shipment"))
        .group_by(Hs1.EntityID)
        .subquery()
    )
    Hs2 = aliased(RFT_StatusHistory)

    # ── 3) Latest Container status ────────────────────────────────────────
    Hc1 = aliased(RFT_StatusHistory)
    latest_ctn = (
        model
        .query(
            Hc1.EntityID.label("ContainerID"),
            func.max(Hc1.StatusDate).label("mx_ctn")
        )
        .filter(Hc1.EntityType == literal("Container"))
        .group_by(Hc1.EntityID)
        .subquery()
    )
    Hc2 = aliased(RFT_StatusHistory)
    
    # ── 3) Latest PLANED Container status ────────────────────────────────────────
    Hcp1 = aliased(RFT_StatusHistory)
    latest_ctn_planed = (
        model
        .query(
            Hcp1.EntityID.label("ContainerID"),
            func.max(Hcp1.StatusDate).label("mx_ctn")
        )
        .filter(Hcp1.EntityType == literal("Planed-Container"))
        .group_by(Hcp1.EntityID)
        .subquery()
    )
    Hcp2 = aliased(RFT_StatusHistory)
    
    #  ————————————————————————————————————————————————————————————
    # Build the invoices subquery with STUFF(... FOR XML PATH(''))
    # ————————————————————————————————————————————————————————————
    invoice_subq = (
        select(
            literal_column("Inv.ShipmentID").label("ShipID"),
            # Use literal_column for the STUFF(...) expression so we can label it
            literal_column(
              """
              STUFF(
                (
                  SELECT ',' + i2.InvoiceNumber
                  FROM RFT_Invoices AS i2
                  WHERE i2.ShipmentID = Inv.ShipmentID
                  FOR XML PATH(''), TYPE
                ).value('.', 'NVARCHAR(MAX)')
              , 1, 1, '')
              """
            ).label("InvoiceNumbers")
        )
        .select_from(text("RFT_Invoices AS Inv"))
        .group_by(literal_column("Inv.ShipmentID"))
        .subquery()
    )

    
    # 1) build a join from PO → PO line → Shipment PO line → Container line → Container
    q = (
        model.query(
            RFT_PurchaseOrder.Brand.label("Brand"),
            RFT_Shipment.ShipmentNumber.label("ShipmentNumber"),
            RFT_Shipment.BLNumber.label("BLNumber"),
            RFT_PurchaseOrder.PODate.label("PODate"),
            RFT_PurchaseOrder.PONumber.label("PONumber"),
            RFT_PurchaseOrder.Site.label("Site"),
            RFT_PurchaseOrder.LCNumber.label("LCNumber"),
            RFT_Shipment.ModeOfTransport.label("ModeOfTransport"),
            RFT_PurchaseOrder.LCDate.label("LCDate"),
            RFT_PurchaseOrderLine.SapItemLine.label("SapItemLine"),
            RFT_PurchaseOrderLine.Article.label("Article"),
            RFT_CategoriesMappingMain.CatName.label("CategoryName"),
            RFT_CategoriesMappingMain.CatDesc.label("CategoryDesc"),
            RFT_Shipment.CreatedDate.label("ShipmentCreatedDate"),
            RFT_Container.ContainerNumber.label("ContainerNumber"),
            RFT_ContainerLine.QtyInContainer.label("QtyInContainer"),
            # New cols
            RFT_Shipment.OriginPort.label("OriginPort"),
            invoice_subq.c.InvoiceNumbers.label("InvoiceNumbers"),
            RFT_Shipment.POD.label("POD"),
            RFT_Shipment.ContainerDeadline.label("ContainerDeadline"),
            
            # Statuses
            Hpo2.Status.label("POStatus"),
            Hs2.Status.label("ShipmentStatus"),
            Hc2.Status.label("ContainerStatus"),
            Hcp2.Status.label("PlanedContainerStatus"),
            # DATES
            RFT_Shipment.ECCDate.label("ECCDate"),
            RFT_Shipment.ETAOrigin.label("ETAOrigin"),
            RFT_Shipment.ETDOrigin.label("ETDOrigin"),
            RFT_Shipment.ETADestination.label("ETADestination"),
            RFT_Shipment.ETDDestination.label("ETDDestination"),
            RFT_Shipment.ETAWH.label("ETAWH"),
            RFT_Container.CCDate.label("CCDate"),
            RFT_Container.ATAOrigin.label("ATAOrigin"),
            RFT_Container.ATDOrigin.label("ATDOrigin"),
            RFT_Container.ATADP.label("ATADP"),
            RFT_Container.ATDDPort.label("ATDDPort"),
            RFT_Container.ATAWH.label("ATAWH"),
            RFT_Container.YardInDate.label("YardInDate"),
            RFT_Container.YardOutDate.label("YardOutDate"),
            RFT_Shipment.BiyanNumber.label("BiyanNumber"),
            RFT_Shipment.SADDADNumber.label("SADDADNumber"),
        )
        # PO → PO lines
        .join(RFT_PurchaseOrderLine,
              RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID)
        # PO status
        .outerjoin(latest_po,
                latest_po.c.POID == RFT_PurchaseOrder.POID)
        .outerjoin(Hpo2,
                and_(
                Hpo2.EntityType == literal("PurchaseOrder"),
                Hpo2.EntityID   == latest_po.c.POID,
                Hpo2.StatusDate == latest_po.c.mx_po
                ))
        # ← here’s the new join to pull in your mapping table
        .outerjoin(RFT_CategoriesMappingMain,
          RFT_PurchaseOrderLine.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        # PO line → Shipment PO lines
        .join(RFT_ShipmentPOLine,
              RFT_PurchaseOrderLine.POLineID == RFT_ShipmentPOLine.POLineID)
        # Shipment PO line → Shipment (for the shipment number)
        .join(RFT_Shipment,
              RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID)
        
        .outerjoin(latest_shp,
            latest_shp.c.ShipmentID == RFT_Shipment.ShipmentID)
        
        # ← invoices concat subquery
        .outerjoin(invoice_subq,
                       invoice_subq.c.ShipID == RFT_Shipment.ShipmentID)
        
        .outerjoin(Hs2,
                and_(
                Hs2.EntityType == literal("Shipment"),
                Hs2.EntityID   == latest_shp.c.ShipmentID,
                Hs2.StatusDate == latest_shp.c.mx_shp
                ))
        # Shipment PO line → ContainerLine
        .join(RFT_ContainerLine,
              RFT_ShipmentPOLine.ShipmentPOLineID == RFT_ContainerLine.ShipmentPOLineID)
        # ContainerLine → Container
        .join(RFT_Container,
              RFT_ContainerLine.ContainerID == RFT_Container.ContainerID)
        .outerjoin(latest_ctn,
            latest_ctn.c.ContainerID == RFT_Container.ContainerID)
        .outerjoin(Hc2,
                and_(
                Hc2.EntityType == literal("Container"),
                Hc2.EntityID   == latest_ctn.c.ContainerID,
                Hc2.StatusDate == latest_ctn.c.mx_ctn
                ))
        .outerjoin(latest_ctn_planed,
            latest_ctn_planed.c.ContainerID == RFT_Container.ContainerID)
        .outerjoin(Hcp2,
                and_(
                Hcp2.EntityType == literal("Planed-Container"),
                Hcp2.EntityID   == latest_ctn_planed.c.ContainerID,
                Hcp2.StatusDate == latest_ctn_planed.c.mx_ctn
                ))
        .order_by(desc(RFT_Shipment.ShipmentNumber))
    )
    
    if 'export' not in request.form:
        print("applying limit")
        q = q.limit(limit_num)
        q = q.offset(ofset_num)
    else:
        export_brands = request.form.getlist("export_brands")

    # 2) pull it into a list of dicts so our template can use named keys
    # 2) materialize and pack into dicts
    keys = [
        "Brand", "ShipmentNumber", "BLNmuber", "PODate", "PONumber", "Site",  "LCNumber", "ModeOfTransport",
        "LCDate", "SapItemLine", "Article", "CategoryName", "CategoryDesc",
        "ShipmentCreatedDate", "ContainerNumber","QtyInContainer", "OriginPort", "InvoiceNumbers", "POD", "ContainerDeadline",
        "POStatus", "ShipmentStatus", "ContainerStatus", "PlanedContainerStatus",
        "ECCDate", "ETAOrigin", "ETDOrigin", "ETADestination", 
        "ETDDestination", "ETAWH","CCDate", "ATAOrigin", "ATDOrigin","ATADP", "ATDDPort", "ATAWH", 
        "YardInDate", "YardOutDate", "BiyanNumber", "SADDADNumber",
    ]
    
    if export_brands:
        q = q.filter(RFT_PurchaseOrder.Brand.in_(export_brands))
    
    results = q.all() 
    regular_rows  = [dict(zip(keys, row)) for row in results]

    
    # 3. Create additional rows for NON-PO items
    non_po_rows = []
    if 'export' in request.form:
        non_po_items = (
            model.query(
                RFT_NonPoItems.ShipmentID,
                RFT_NonPoItems.Brand.label("NonPo_Brand"),
                RFT_NonPoItems.Article.label("NonPo_Article"),
                RFT_NonPoItems.Qty.label("NonPo_Qty"),
                RFT_NonPoItems.Value.label("NonPo_Value"),
                RFT_Shipment.ShipmentNumber,
                RFT_Shipment.BLNumber
            )
            .join(RFT_Shipment, RFT_NonPoItems.ShipmentID == RFT_Shipment.ShipmentID)
        )
        
        if export_brands:
            non_po_items = non_po_items.filter(RFT_NonPoItems.Brand.in_(export_brands))
            
        
        non_po_items = non_po_items.all()
        
        
        for item in non_po_items:
            # Create a new row with mostly empty values
            new_row = {
                "Brand": item.NonPo_Brand,
                "ShipmentNumber": item.ShipmentNumber,
                "BLNmuber": item.BLNumber,
                "PONumber": "NON-PO",  # Only NON-PO fields filled
                "Article": item.NonPo_Article,
                "QtyInContainer": item.NonPo_Qty,
            }
            non_po_rows.append(new_row)
        
        # 4. Combine both sets of rows
        combined_rows = regular_rows + non_po_rows
    else:
        combined_rows = regular_rows
    
    # 5. Sort by ShipmentNumber (optional)
    combined_rows.sort(key=lambda x: x["ShipmentNumber"])

    
    # 3) load any saved labels for this “view”
    table_name = "FreightTrackingView"
    label_rows = (
        model.query(RFT_FieldLabels)
             .filter_by(TableName=table_name)
             .all()
    )
    friendly = {lbl.FieldName: lbl.Label for lbl in label_rows}

    # 4) build our columns metadata in the desired order
    columns = [
      {"name": k, "label": friendly.get(k, k)}
      for k in keys
    ]
    
    if request.method == 'POST' and 'export' in request.form:
        # 2) build DataFrame & write to Excel in-memory
        df = pd.DataFrame(combined_rows, columns=keys)
        
        # 2) convert any “date-like” columns to date-only
        #    – here we pick up every column whose name contains 'Date' or 'Deadline'
        # List all the substrings you care about
        keywords = [
            "Date", "Deadline",
            "ETAOrigin", "ETDOrigin", "ETADestination", "ETDDestination", "ETAWH",
            "ATAOrigin", "ATDOrigin", "ATADP", "ATDDPort", "ATAWH"
        ]

        # Pick only those columns whose name contains any of the keywords
        date_cols = [
            c for c in df.columns
            if any(kw in c for kw in keywords)
        ]
        for col in date_cols:
            # turn it into a proper datetime, then take only the date part
            # df[col] = pd.to_datetime(df[col], errors='coerce').dt.date
            df[col] = pd.to_datetime(df[col], errors='coerce')
        
        
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            # Sheet 1: FreightTracking
            df.to_excel(writer, index=False, sheet_name="FreightTracking")

        output.seek(0)

        # 3) send back to browser
        return send_file(
        output,
        download_name="FreightTracking.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        return render_template(
        "FreightTrackingView.html",
        rows=combined_rows,
        columns=columns
        )

@bp.route("/containers_deadline_report", methods=["GET", "POST"])
@login_required
def containers_deadline_report():
    model = None
    model = Session()
    
    export_brands = request.form.getlist("export_brands")
    
    # ── 3) Latest Container status ────────────────────────────────────────
    Hc1 = aliased(RFT_StatusHistory)
    latest_ctn = (
        model
        .query(
            Hc1.EntityID.label("ContainerID"),
            func.max(Hc1.StatusDate).label("mx_ctn")
        )
        .filter(Hc1.EntityType == literal("Container"))
        .group_by(Hc1.EntityID)
        .subquery()
    )
    Hc2 = aliased(RFT_StatusHistory)
    
    deadline_rows = (
        model.query(
            Hc2.Status.label("Status"),
            RFT_PurchaseOrder.Brand.label("Brand"),
            RFT_Shipment.OriginPort.label("Origin"),
            RFT_Shipment.POD.label("Destination"),
            (RFT_CategoriesMappingMain.CatName + "/" + RFT_CategoriesMappingMain.CatDesc).label("CAT"),
            func.count(RFT_Container.ContainerID).label("Cont."),
            RFT_Shipment.ModeOfTransport.label("Mode. T"),
            RFT_Container.ContainerRemarks.label("Remarks"),
            RFT_Shipment.ContainerDeadline.label("DeadlineDate")
        )
        .join(RFT_PurchaseOrderLine, RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID)
        .join(RFT_CategoriesMappingMain, RFT_PurchaseOrderLine.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        .join(RFT_ShipmentPOLine, RFT_PurchaseOrderLine.POLineID == RFT_ShipmentPOLine.POLineID)
        .join(RFT_Shipment, RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID)
        .join(RFT_ContainerLine, RFT_ShipmentPOLine.ShipmentPOLineID == RFT_ContainerLine.ShipmentPOLineID)
        .join(RFT_Container, RFT_ContainerLine.ContainerID == RFT_Container.ContainerID)
        .outerjoin(latest_ctn, latest_ctn.c.ContainerID == RFT_Container.ContainerID)
        .outerjoin(Hc2,
            and_(
                Hc2.EntityType == literal("Container"),
                Hc2.EntityID == latest_ctn.c.ContainerID,
                Hc2.StatusDate == latest_ctn.c.mx_ctn
            )
        )
        .filter(~ Hc2.Status.in_(DELIVERED_STATUSES))
        .group_by(
            Hc2.Status,
            RFT_PurchaseOrder.Brand,
            RFT_Shipment.OriginPort,
            RFT_Shipment.POD,
            RFT_CategoriesMappingMain.CatName,
            RFT_CategoriesMappingMain.CatDesc,
            RFT_Shipment.ModeOfTransport,
            RFT_Container.ContainerRemarks,
            RFT_Shipment.ContainerDeadline
        )
    )
    
    if export_brands and len(export_brands)>0:
        deadline_rows = deadline_rows.filter(RFT_PurchaseOrder.Brand.in_(export_brands))
    
    deadline_data = deadline_rows.all()
    deadline_keys = ["Status", "Brand", "Origin", "Destination", "CAT", "Cont.", "Mode. T", "Remarks", "DeadlineDate", "Days"]
    deadline_dicts = [dict(zip(deadline_keys, row)) for row in deadline_data]
    
    # Calculate days remaining (can be negative)
    for row in deadline_dicts:
        deadline = row.get("DeadlineDate")
        if deadline:
            deadline_dt = pd.to_datetime(deadline).date()
            row["Days"] = (deadline_dt - datetime.today().date()).days
        else:
            row["Days"] = None
    
    # 3) load any saved labels for this “view”
    table_name = "FreightTrackingView"
    label_rows = (
        model.query(RFT_FieldLabels)
             .filter_by(TableName=table_name)
             .all()
    )
    friendly = {lbl.FieldName: lbl.Label for lbl in label_rows}

    # 4) build our columns metadata in the desired order
    columns = [
      {"name": k, "label": friendly.get(k, k)}
      for k in deadline_keys
    ]
    
    
    if request.method == 'POST' and 'export' in request.form:
        output = BytesIO()

        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            deadline_df = pd.DataFrame(deadline_dicts, columns=deadline_keys)
            
            workbook = writer.book
        
            # Convert DeadlineDate to date and drop nulls
            deadline_df["DeadlineDate"] = pd.to_datetime(deadline_df["DeadlineDate"], errors='coerce')
            # deadline_df["ETA_Destination"] = pd.to_datetime(deadline_df["ETA_Destination"], errors='coerce')
            # deadline_df = deadline_df.dropna(subset=["DeadlineDate"])
        
            # Sort by DeadlineDate
            deadline_df = deadline_df.sort_values(by="DeadlineDate")
            
            # Write data first
            date_format = workbook.add_format({
                'num_format': 'd-mmmm-yyyy',
                'border': 1
            })
            header_format = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#D9E1F2'})
            missing_format = workbook.add_format({'bg_color': '#A93226', 'font_color': '#FFFFFF'})
            
            # Manually write column headers
            worksheet = writer.book.add_worksheet("Container Deadlines")
            writer.sheets["Container Deadlines"] = worksheet
            for col_num, col_name in enumerate(deadline_df.columns):
                worksheet.write(0, col_num, col_name, header_format)
            
            # Write row by row
            # With border
            bordered_format = workbook.add_format({'border': 1})  # Thin border on all sides
            for row_num, row in enumerate(deadline_df.itertuples(index=False), start=1):
                for col_num, value in enumerate(row):
                    if deadline_df.columns[col_num] == "DeadlineDate" and pd.notnull(value):
                        worksheet.write_datetime(row_num, col_num, value, date_format)
                    elif pd.isna(value):
                        worksheet.write_blank(row_num, col_num, None, bordered_format)
                    else:
                        worksheet.write(row_num, col_num, value, bordered_format)
            
            
            # Apply date format (e.g., 6-June-2025)
            deadline_col_idx = deadline_df.columns.get_loc("DeadlineDate")  # 0-based
            col_letter = chr(65 + deadline_col_idx)
            worksheet.set_column(f"{col_letter}:{col_letter}", 20, date_format)
            
            
            # For width formating 
            for idx, col in enumerate(deadline_df.columns):
                # Convert all values in this column to string and get their lengths
                series = deadline_df[col].astype(str)
                max_len = max(
                    series.map(len).max(),
                    len(str(col))  # Also consider the header
                )
                worksheet.set_column(idx, idx, max_len + 2)  # +2 for padding
            
            worksheet.autofilter(0, 0, worksheet.dim_rowmax, worksheet.dim_colmax)
            # Freez 1st row
            worksheet.freeze_panes(1, 0)

        
            # Define format styles
            red_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
            yellow_format = workbook.add_format({'bg_color': '#FFEB9C', 'font_color': '#9C5700'})
            light_green_format = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100'})
            dark_green_format = workbook.add_format({'bg_color': '#A9D08E', 'font_color': '#006100'})
        
            # Find the column index of DeadlineDate
            deadline_col_idx = deadline_df.columns.get_loc("DeadlineDate")
        
            # Apply conditional formatting based on date difference
            date_cell_range = f"{chr(65 + deadline_col_idx)}2:{chr(65 + deadline_col_idx)}{len(deadline_df)+1}"
        
            today_str = pd.Timestamp.today().strftime("%Y-%m-%d")

            # Highlight rows where DeadlineDate is blank (NaT or empty)
            worksheet.conditional_format(date_cell_range, {
                'type': 'blanks',
                'format': missing_format
            })
            
            
            # Red: Overdue or today
            worksheet.conditional_format(date_cell_range, {
                'type': 'formula',
                'criteria': f'=TODAY()-{chr(65 + deadline_col_idx)}2 >= 0',
                'format': red_format
            })
            
            # Yellow: 1–2 days left
            worksheet.conditional_format(date_cell_range, {
                'type': 'formula',
                'criteria': f'=AND({chr(65 + deadline_col_idx)}2 - TODAY() <= 2, {chr(65 + deadline_col_idx)}2 - TODAY() > 0)',
                'format': yellow_format
            })
            
            # Light green: 3–5 days left
            worksheet.conditional_format(date_cell_range, {
                'type': 'formula',
                'criteria': f'=AND({chr(65 + deadline_col_idx)}2 - TODAY() <= 5, {chr(65 + deadline_col_idx)}2 - TODAY() > 2)',
                'format': light_green_format
            })
            
            # Dark green: 6–10 days left
            worksheet.conditional_format(date_cell_range, {
                'type': 'formula',
                'criteria': f'=AND({chr(65 + deadline_col_idx)}2 - TODAY() <= 10, {chr(65 + deadline_col_idx)}2 - TODAY() > 5)',
                'format': dark_green_format
            })
            
            # Dark green: >10 days
            worksheet.conditional_format(date_cell_range, {
                'type': 'formula',
                'criteria': f'={chr(65 + deadline_col_idx)}2 - TODAY() > 10',
                'format': dark_green_format
            })
            
            # DAYS FORMATING 
            days_col_idx = deadline_df.columns.get_loc("Days")
            days_range = f"{chr(65 + days_col_idx)}2:{chr(65 + days_col_idx)}{len(deadline_df)+1}"
            
            worksheet.conditional_format(days_range, {
                'type': 'blanks',
                'format': missing_format
            })
            
            # Red (overdue or 0 days)
            worksheet.conditional_format(days_range, {
                'type': 'cell',
                'criteria': '<=',
                'value': 0,
                'format': red_format
            })
            
            # Yellow (1–2 days)
            worksheet.conditional_format(days_range, {
                'type': 'cell',
                'criteria': 'between',
                'minimum': 1,
                'maximum': 2,
                'format': yellow_format
            })
            
            # Light Green (3–5 days)
            worksheet.conditional_format(days_range, {
                'type': 'cell',
                'criteria': 'between',
                'minimum': 3,
                'maximum': 5,
                'format': light_green_format
            })
            
            # Dark Green (6+ days)
            worksheet.conditional_format(days_range, {
                'type': 'cell',
                'criteria': '>=',
                'value': 6,
                'format': dark_green_format
            })
            
        
        output.seek(0)
        
        # 3) send back to browser
        return send_file(
        output,
        download_name="Cont_-Deadline-report-RFT.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        return render_template(
        "containerDeadlineReport.html",
        rows=deadline_dicts,
        columns=columns
        )

@bp.route("/freight_tracking_report", methods=["GET", "POST"])
def freight_tracking_report():
    model = None
    export_brands = request.form.getlist("export_brands")
    model = Session()
    
    limit_num = request.form.get("limit", 100)
    ofset_num = 100
    
    # ── 1) Latest Purchase-Order status ────────────────────────────────────
    Hpo1 = aliased(RFT_StatusHistory)
    latest_po = (
        model
        .query(
            Hpo1.EntityID.label("POID"),
            func.max(Hpo1.StatusDate).label("mx_po")
        )
        .filter(Hpo1.EntityType == literal("Purchase Order"))
        .group_by(Hpo1.EntityID)
        .subquery()
    )
    Hpo2 = aliased(RFT_StatusHistory)

    # ── 2) Latest Shipment status ─────────────────────────────────────────
    Hs1 = aliased(RFT_StatusHistory)
    latest_shp = (
        model
        .query(
            Hs1.EntityID.label("ShipmentID"),
            func.max(Hs1.StatusDate).label("mx_shp")
        )
        .filter(Hs1.EntityType == literal("Shipment"))
        .group_by(Hs1.EntityID)
        .subquery()
    )
    Hs2 = aliased(RFT_StatusHistory)

    # ── 3) Latest Container status ────────────────────────────────────────
    Hc1 = aliased(RFT_StatusHistory)
    latest_ctn = (
        model
        .query(
            Hc1.EntityID.label("ContainerID"),
            func.max(Hc1.StatusDate).label("mx_ctn")
        )
        .filter(Hc1.EntityType == literal("Container"))
        .group_by(Hc1.EntityID)
        .subquery()
    )
    Hc2 = aliased(RFT_StatusHistory)
    
    # ── 3) Latest PLANED Container status ────────────────────────────────────────
    Hcp1 = aliased(RFT_StatusHistory)
    latest_ctn_planed = (
        model
        .query(
            Hcp1.EntityID.label("ContainerID"),
            func.max(Hcp1.StatusDate).label("mx_ctn")
        )
        .filter(Hcp1.EntityType == literal("Planed-Container"))
        .group_by(Hcp1.EntityID)
        .subquery()
    )
    Hcp2 = aliased(RFT_StatusHistory)
    
    #  ————————————————————————————————————————————————————————————
    # Build the invoices subquery with STUFF(... FOR XML PATH(''))
    # ————————————————————————————————————————————————————————————
    invoice_subq = (
        select(
            literal_column("Inv.ShipmentID").label("ShipID"),
            # Use literal_column for the STUFF(...) expression so we can label it
            literal_column(
              """
              STUFF(
                (
                  SELECT ',' + i2.InvoiceNumber
                  FROM RFT_Invoices AS i2
                  WHERE i2.ShipmentID = Inv.ShipmentID
                  FOR XML PATH(''), TYPE
                ).value('.', 'NVARCHAR(MAX)')
              , 1, 1, '')
              """
            ).label("InvoiceNumbers")
        )
        .select_from(text("RFT_Invoices AS Inv"))
        .group_by(literal_column("Inv.ShipmentID"))
        .subquery()
    )

    
    # 1) build a join from PO → PO line → Shipment PO line → Container line → Container
    q = (
        model.query(
            RFT_PurchaseOrder.Brand.label("Brand"),
            RFT_Shipment.BLNumber.label("BLNumber"),
            RFT_PurchaseOrder.PODate.label("PODate"),
            RFT_PurchaseOrder.PONumber.label("PONumber"),
            RFT_PurchaseOrderLine.SapItemLine.label("SapItemLine"),
            RFT_PurchaseOrderLine.Article.label("Article"),
            RFT_CategoriesMappingMain.CatName.label("CategoryName"),
            RFT_CategoriesMappingMain.CatDesc.label("CategoryDesc"),
            RFT_Container.ContainerNumber.label("ContainerNumber"),
            RFT_ContainerLine.QtyInContainer.label("QtyInContainer"),
            invoice_subq.c.InvoiceNumbers.label("InvoiceNumbers"),
            # RFT_Shipment.ECCDate.label("ECCDate"),
            RFT_Shipment.ETDOrigin.label("ETDOrigin"),
            RFT_Shipment.ETADestination.label("ETADestination"),
            RFT_Container.CCDate.label("CCDate"),
            RFT_Shipment.ContainerDeadline.label("ContainerDeadline"),
            # Hs2.Status.label("ShipmentStatus"),
            Hc2.Status.label("ContainerStatus"),
            Hcp2.Status.label("PlanedContainerStatus"),
        )
        # PO → PO lines
        .join(RFT_PurchaseOrderLine,
              RFT_PurchaseOrder.POID == RFT_PurchaseOrderLine.POID)
        # PO status
        .outerjoin(latest_po,
                latest_po.c.POID == RFT_PurchaseOrder.POID)
        .outerjoin(Hpo2,
                and_(
                Hpo2.EntityType == literal("PurchaseOrder"),
                Hpo2.EntityID   == latest_po.c.POID,
                Hpo2.StatusDate == latest_po.c.mx_po
                ))
        # ← here’s the new join to pull in your mapping table
        .outerjoin(RFT_CategoriesMappingMain,
          RFT_PurchaseOrderLine.CategoryMappingID == RFT_CategoriesMappingMain.ID)
        # PO line → Shipment PO lines
        .join(RFT_ShipmentPOLine,
              RFT_PurchaseOrderLine.POLineID == RFT_ShipmentPOLine.POLineID)
        # Shipment PO line → Shipment (for the shipment number)
        .join(RFT_Shipment,
              RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID)
        
        .outerjoin(latest_shp,
            latest_shp.c.ShipmentID == RFT_Shipment.ShipmentID)
        
        # ← invoices concat subquery
        .outerjoin(invoice_subq,
                       invoice_subq.c.ShipID == RFT_Shipment.ShipmentID)
        
        # .outerjoin(Hs2,
        #         and_(
        #         Hs2.EntityType == literal("Shipment"),
        #         Hs2.EntityID   == latest_shp.c.ShipmentID,
        #         Hs2.StatusDate == latest_shp.c.mx_shp
        #         ))
        # Shipment PO line → ContainerLine
        .join(RFT_ContainerLine,
              RFT_ShipmentPOLine.ShipmentPOLineID == RFT_ContainerLine.ShipmentPOLineID)
        # ContainerLine → Container
        .join(RFT_Container,
              RFT_ContainerLine.ContainerID == RFT_Container.ContainerID)
        .outerjoin(latest_ctn,
            latest_ctn.c.ContainerID == RFT_Container.ContainerID)
        .outerjoin(Hc2,
                and_(
                Hc2.EntityType == literal("Container"),
                Hc2.EntityID   == latest_ctn.c.ContainerID,
                Hc2.StatusDate == latest_ctn.c.mx_ctn
                ))
        .outerjoin(latest_ctn_planed,
            latest_ctn_planed.c.ContainerID == RFT_Container.ContainerID)
        .outerjoin(Hcp2,
                and_(
                Hcp2.EntityType == literal("Planed-Container"),
                Hcp2.EntityID   == latest_ctn_planed.c.ContainerID,
                Hcp2.StatusDate == latest_ctn_planed.c.mx_ctn
                ))
        .order_by(desc(RFT_Shipment.ShipmentNumber))
    )
    
    keys = ["Brand", "BL Number", "PO Date", "PO Number", "Item Line", "Article", "Category", "Desc",
            "Container Number", "Qty In Container", "Invoice Numbers", "ETD Origin", "ETA Destination",
            "Clearance Date", "Container Deadline", "Container Status", "Planed TO"]
    
    if export_brands:
        q = q.filter(RFT_PurchaseOrder.Brand.in_(export_brands))
    
    results = q.all() 
    regular_rows  = [dict(zip(keys, row)) for row in results]

    
    # 3. Create additional rows for NON-PO items
    non_po_rows = []
    # if 'export' in request.form:
    non_po_items = (
        model.query(
            RFT_NonPoItems.ShipmentID,
            RFT_NonPoItems.Brand.label("NonPo_Brand"),
            RFT_NonPoItems.Article.label("NonPo_Article"),
            RFT_NonPoItems.Qty.label("NonPo_Qty"),
            RFT_NonPoItems.Value.label("NonPo_Value"),
            RFT_Shipment.ShipmentNumber
        )
        .join(RFT_Shipment, RFT_NonPoItems.ShipmentID == RFT_Shipment.ShipmentID)
    )
    
    if export_brands:
        non_po_items = non_po_items.filter(RFT_NonPoItems.Brand.in_(export_brands))
        
    non_po_items = non_po_items.all()
    
    for item in non_po_items:
        # Create a new row with mostly empty values
        new_row = {
            "Brand": item.NonPo_Brand,
            # "ShipmentNumber": item.ShipmentNumber,
            "PONumber": "NON-PO",  # Only NON-PO fields filled
            "Article": item.NonPo_Article,
            "QtyInContainer": item.NonPo_Qty,
        }
        non_po_rows.append(new_row)
    
    # 4. Combine both sets of rows
    combined_rows = regular_rows + non_po_rows
    
    # 5. Sort by ShipmentNumber (optional)
    combined_rows.sort(key=lambda x: x["Brand"])

    
    # 3) load any saved labels for this “view”
    table_name = "FreightTrackingView"
    label_rows = (
        model.query(RFT_FieldLabels)
             .filter_by(TableName=table_name)
             .all()
    )
    friendly = {lbl.FieldName: lbl.Label for lbl in label_rows}

    # 4) build our columns metadata in the desired order
    columns = [
      {"name": k, "label": friendly.get(k, k)}
      for k in keys
    ]
    
    if request.method == 'POST' and 'export' in request.form:
        output = BytesIO()

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            worksheet = workbook.add_worksheet("Shipment Status")
            writer.sheets["Shipment Status"] = worksheet

            df = pd.DataFrame(combined_rows, columns=keys)

            # Define formats
            bold_header = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#D9E1F2'})
            bordered = workbook.add_format({
                'border': 1
            })
            format_map = {
                "Under Clearance": workbook.add_format({'bg_color': '#92D050', 'border': 1, 'num_format': 'd-mmmm-yyyy'}),
                "In Yard": workbook.add_format({'bg_color': "#F2A877", 'border': 1, 'num_format': 'd-mmmm-yyyy'}),
                "IN-Transit": workbook.add_format({'bg_color': '#9BC2E6', 'border': 1, 'num_format': 'd-mmmm-yyyy'}),
                "Under Collection": workbook.add_format({'bg_color': '#D9D9D9', 'border': 1, 'num_format': 'd-mmmm-yyyy'}),
                "Delivered": workbook.add_format({'bg_color': '#FFFFFF', 'border': 1, 'num_format': 'd-mmmm-yyyy'}),
            }

            # Write legend
            legend_data = [
                ["Under Clearance", "Green"],
                ["Storage Yard", "Orange"],
                ["In Transit", "Blue"],
                ["Under Collection", "Grey"],
                ["Delivered", "White"],
            ]
            worksheet.write(0, 0, "Legend", bold_header)
            for i, (status, color_name) in enumerate(legend_data, start=1):
                fmt = format_map.get(status, bordered)
                worksheet.write(i, 0, status, fmt)
                worksheet.write(i, 1, color_name, fmt)

            start_row = len(legend_data) + 2  # leave gap

            # Write headers
            for col_num, col_name in enumerate(df.columns):
                worksheet.write(start_row, col_num, col_name, bold_header)

            # Write data rows
            for row_idx, row in enumerate(df.itertuples(index=False), start=start_row + 1):
                row_fmt = format_map.get(row[-2], bordered)  # based on "Shipment Status"
                for col_idx, value in enumerate(row):
                    if df.columns[col_idx] != "Qty In Container" and pd.notnull(value):
                        worksheet.write(row_idx, col_idx, value, row_fmt)
                    elif df.columns[col_idx] == "Qty In Container" and pd.notnull(value):
                         worksheet.write(row_idx, col_idx, value, bordered)
                    else:
                        worksheet.write_blank(row_idx, col_idx, value, bordered)

            # Autofilter
            worksheet.autofilter(start_row, 0, row_idx, len(keys) - 1)
            # Freez rows
            worksheet.freeze_panes(8, 0)

            # Auto-adjust column widths
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(idx, idx, max_len)

        output.seek(0)
        
        now = datetime.now()
        formatted_time = now.strftime("[%#d %B] [%I;%M] %p")  # on Windows  # e.g., "7 July 12:10 PM"
        brands_str = " & ".join(export_brands)
        file_name = f"Freight Tracker [{brands_str}] {formatted_time}.xlsx"
        return send_file(
            output,
            download_name=file_name,
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    else:
        return render_template("freight_tracker_report.html", rows=combined_rows, columns=columns)



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
            # new_mot      = form.get(f"ModeOfTransport_{poid}")
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
                        StatusDate  = func.now(),
                        UpdatedBy   = session['username'],
                        Comments    = "Initial updates: LCStatus=No"
                    )
                    model.add(h)

            if new_lcdate:
                # parse YYYY-MM-DD automatically via WTForms / HTML date
                dt = datetime.strptime(new_lcdate, "%Y-%m-%d")
                if dt != po.LCDate:
                    po.LCDate = dt
                    changed = True
                    
                    i = RFT_StatusHistory(
                        EntityType  = "Purchase Order",
                        EntityID    = po.POID,
                        Status      = "LC-Established",
                        StatusDate  = func.now(),
                        UpdatedBy   = session['username'],
                        Comments    = "Initial updates: LCStatus=YES"
                    )
                    model.add(i)

            # if new_mot and new_mot != po.ModeOfTransport:
            #     po.ModeOfTransport = new_mot
            #     changed = True

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
            # RFT_PurchaseOrder.ModeOfTransport,
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
                # RFT_PurchaseOrder.ModeOfTransport.is_(None)
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
    #   {"name":"ModeOfTransport", "label":"Mode Of Transport", "filter_type":"select",
    #      "options":[m.mode for m in model.query(RFT_ModeOfTransport).all()]},
      {"name":"INCOTerms",       "label":"INCOTerms",        "filter_type":"select",
         "options":[c.code+" - "+c.description
                    for c in model.query(RFT_IncoTerms).all()]},
      {"name":"TotalArticles",   "label":"# Articles",      "type":"numeric"},
      {"name":"TotalQty",        "label":"Total Qty",       "type":"numeric"},
      {"name":"TotalValue",      "label":"Total Value",     "type":"numeric"},
    ]

    # load your drop‐downs (unchanged)
    # modeOfTransport = model.query(RFT_ModeOfTransport).all()
    incoterms       = model.query(RFT_IncoTerms).all()
    
    return render_template(
      "InitialPO_Updates.html",
      rows    = [r._asdict() for r in report_data],
      columns = columns,
      # so your filters form can re-populate
      sel_PODate       = request.values.get("PODate",""),
      sel_LCNumber     = request.values.get("LCNumber",""),
    #   modeOfTransport  = modeOfTransport,
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
            ModeOfTransport = 'Sea',
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
          "Site":           po.Site,
          "SapItemLine":    line.SapItemLine,
          "Supplier":       po.Supplier,
          "Brand":          po.Brand,
          "CatName":        cat.CatName,
          "CATDesc":        cat.CatDesc,
          "SubCat":         cat.SubCat,
          "Article":        line.Article,
          "BalanceQty":     line.BalanceQty,
          "TotalValue":     float(line.TotalValue)
        })

    # 2) build column metadata for dynamic filters
    columns = [
      {"name":"PONumber",       "label":"PO Number",       "filter_type":"text"},
      {"name":"Supplier",       "label":"Supplier",        "filter_type":"text"},
      {"name":"SapItemLine",    "label":"SapItemLine",     "filter_type":"numeric"},
      {"name":"Brand",          "label":"Brand",           "filter_type":"text"},
      {"name":"CatName",        "label":"Category",        "filter_type":"select",
       "options": sorted({r["CatName"] for r in rows})},
      {"name":"Article",        "label":"Article",         "filter_type":"text"},
      {"name":"Site",           "label":"Site",            "filter_type":"text"},
      {"name":"BalanceQty",     "label":"Total Qty",       "filter_type":"numeric"},
      {"name":"TotalValue",     "label":"Total Value",     "filter_type":"numeric"},
    ]

    sda_options =  model.query(RFT_CategoriesMappingSDA).all()
    # sda_options = sda_options,
    
    return render_template(
      "createShipments.html",
      rows=rows,
      columns=columns,
      sda_options = sda_options
    )

@bp.route("/createdShipments", methods=["GET", "POST"]) # CREATED  !!!!!!!!    ED
@login_required
def createdShipments():
    """
    created )_) Shipments List
    """ 

    if request.method == 'POST' and 'export' in request.form:
        # print("exporting")
        shp_ids = request.form.getlist('Shipment_to_export') 
        # for ids in shp_ids:
        #     print(ids)

        return export_shipment_expense_report(shipment_id=shp_ids)
    
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
            log.debug(f"Status: {st}")
            if st:
                model.add(RFT_StatusHistory(
                    EntityType ="Shipment",
                    EntityID   = shp.ShipmentID,
                    Status     = st,
                    StatusDate =  func.now(),
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
                            StatusDate =  func.now(),
                            UpdatedBy  = session['username'],
                            Comments   = f"Auto when shipment status changed to: {st}"
                        ))
                any_change = True

            for tag, folder in (("biyanPDF", BIYAN_FOLDER), ("saddadPDF", SADDAD_FOLDER)):
                fieldname = f"{tag}_file_{sn}"
                f = request.files.get(fieldname)
                
                if not f:
                    print(f"No file in request.files for field {fieldname}")
                    continue

                if not f.filename:
                    print(f"Field {fieldname} submitted empty filename")
                    continue
                
                # Determine prefix pattern
                prefix = f"{sn}_{tag}"

                # Delete old files with this prefix
                for fname in os.listdir(folder):
                    if fname.startswith(prefix + "."):
                        try:
                            os.remove(os.path.join(folder, fname))
                            print(f"Removed old file: {fname}")
                        except Exception as e:
                            print(f"Failed to remove {fname}: {e}")

                # 4) build secure filename + ensure folder exists
                ext    = os.path.splitext(f.filename)[1]  # includes the dot, e.g. ".pdf", ".jpg"
                fn     = secure_filename(f"{sn}_{tag}{ext}")
                
                target = os.path.join(folder, fn)
                try:
                    os.makedirs(folder, exist_ok=True)
                except Exception as e:
                    print(f"Could not create directory {folder}: {e}")
                    flash("Server error preparing uploads folder", "danger")
                    return jsonify({"redirect": url_for("main.createdShipments")})
                
                # 5) actually save, catching any errors
                try:
                    f.save(target)
                    print(f"Saved uploaded PDF to {target}")
                    any_change = True
                except Exception as e:
                    print(f"Failed to save upload to {target}: {e}")
                    flash("Server error saving uploaded file", "danger")
                    return jsonify({"redirect": url_for("main.createdShipments")})

            new_mot      = form.get(f"ModeOfTransport_{sn}")
            if new_mot and new_mot != shp.ModeOfTransport:
                shp.ModeOfTransport = new_mot
                any_change = True
            
            
            if any_change:
                shp.LastUpdated   =  func.now()
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
        model
        .query(
        H1.EntityID.label("ShipmentID"),
        func.max(H1.StatusDate).label("mx")
        )
        .filter(H1.EntityType == literal("Shipment"))
        # .filter(~H1.Status.in_(DELIVERED_STATUSES) )
        .group_by(H1.EntityID)
        .subquery()
    )

    H2 = aliased(RFT_StatusHistory)
    PO     = aliased(RFT_PurchaseOrder)
    POLine = aliased(RFT_PurchaseOrderLine)
    q = (
        model.query(
            RFT_Shipment,
            PO.Brand.label("Brand"),   
            H2.Status.label("ShipmentLevelStatus")
        )
        # join PO via the ShipmentPO → PO‐Line → PO chain
        .join(
            RFT_ShipmentPOLine,
            RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID
        )
        .join(
            POLine,
            POLine.POLineID == RFT_ShipmentPOLine.POLineID
        )
        .join(
            PO,
            PO.POID == POLine.POID
        )
        # join to get each shipment’s max(StatusDate)
        .outerjoin(latest, latest.c.ShipmentID == RFT_Shipment.ShipmentID)
        # now join back *only* on Shipment history rows
        .outerjoin(
            H2,
            and_(
                H2.EntityType == literal("Shipment"),
                H2.EntityID == latest.c.ShipmentID,
                H2.StatusDate == latest.c.mx
            )
        )
        .filter(~H2.Status.in_(DELIVERED_STATUSES) )
        .order_by(desc(RFT_Shipment.CreatedDate))
    )
    shipments = []
    for shp, brand, st in q:
        shp.ShipmentLevelStatus = st or ""
        shp.Brand               = brand
        shipments.append(shp)

    # columns metadata (no server‐side filtering)
    friendly = {
      "Brand":               "Brand",
      "ShipmentNumber":      "Shipment #",
      "CreatedDate":         "Created",
      "BiyanNumber":         "Biyan #",
      "SADDADNumber":        "Saddad #",
      "ShipmentLevelStatus": "Status",
      "BLNumber":            "Bill of Lading #",
    }
    cols = get_table_metadata(RFT_Shipment, shipments, friendly)
    
    include = {"Brand", "ShipmentNumber", "BiyanNumber", "SADDADNumber", "BLNumber", "ModeOfTransport"}
    columns = [c for c in cols if c["name"] in include]

    for c in columns: # Changed filter types to text
        c["filter_type"] = "text"

    columns.append({"name": "Brand", "label": friendly["Brand"]})

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

    for shp in shipments:
        # 1) Any NULL columns on the shipment?
        #    We inspect every Column in the model and see if getattr is None.
        mapper = inspect(shp).mapper
        null_found = False
        for col in mapper.columns:
            if getattr(shp, col.key) is None:
                null_found = True
                break
        shp.has_null = null_found

        # 2) Is total QtyShipped == total QtyInContainer?
        total_shipped   = sum(line.QtyShipped for line in shp.po_lines)
        # print(shp.ShipmentNumber +" shipped = "+ str(total_shipped))
        total_contained = sum(
            cl.QtyInContainer
            for cont in shp.containers
            for cl   in cont.lines
        )
        # print(shp.ShipmentNumber +" contained = "+ str(total_contained))
        shp.is_qty_balanced = (total_shipped == total_contained)
        # you could also do shp.pending_qty = total_shipped - total_contained

    # load your drop‐downs (unchanged)
    modeOfTransport = model.query(RFT_ModeOfTransport).all()
    
    return render_template("createdShipments.html",
      rows                  = shipments,
      columns               = columns,
      shipmentstatuses      = shipmentstatuses,
      existing_biyan_files  = existing_biyan,
      existing_saddad_files = existing_saddad,
      modeOfTransport       = modeOfTransport,
      DELIVERED_STATUSES    = DELIVERED_STATUSES
    )

@bp.route("/shipment/status/<path:status>/<path:mot>", methods=["GET", "POST"])
@login_required
def shipments_status(status=None, mot=None):
    model = Session()

    # 1) Get all shipment-level statuses for dropdown filter
    all_status = [
        s.StatusName for s in model
            .query(RFT_StatusManagement)
            .filter_by(Level="Shipment Level")
            .order_by(RFT_StatusManagement.StatusName)
            .all()
    ]

    # 2) Latest shipment-level status
    Hist1 = aliased(RFT_StatusHistory)
    latest = (
        model
        .query(
            Hist1.EntityID.label("ShipmentID"),
            func.max(Hist1.StatusDate).label("mx")
        )
        .filter(Hist1.EntityType == literal("Shipment"))
        .group_by(Hist1.EntityID)
        .subquery()
    )

    Hist2 = aliased(RFT_StatusHistory)
    PO     = aliased(RFT_PurchaseOrder)
    POLine = aliased(RFT_PurchaseOrderLine)

    q = (
        model.query(
            RFT_Shipment,
            PO.Brand.label("Brand"),   
            Hist2.Status.label("ShipmentLevelStatus")
        )
        .join(RFT_ShipmentPOLine, RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID)
        .join(POLine, POLine.POLineID == RFT_ShipmentPOLine.POLineID)
        .join(PO, PO.POID == POLine.POID)
        .outerjoin(latest, latest.c.ShipmentID == RFT_Shipment.ShipmentID)
        .outerjoin(Hist2,
            and_(
                Hist2.EntityType == literal("Shipment"),
                Hist2.EntityID == latest.c.ShipmentID,
                Hist2.StatusDate == latest.c.mx
            )
        )
    )

    filters = [Hist2.Status == status]
    if mot:
        filters.append(RFT_Shipment.ModeOfTransport == mot)

    q = q.filter(*filters).order_by(desc(RFT_Shipment.CreatedDate))

    # 3) Process results
    shipments = []
    for shp, brand, st in q:
        shp.ShipmentLevelStatus = st or ""
        shp.Brand = brand
        shipments.append(shp)

    # 4) Columns setup
    friendly = {
      "Brand":               "Brand",
      "ShipmentNumber":      "Shipment #",
      "CreatedDate":         "Created",
      "BiyanNumber":         "Biyan #",
      "SADDADNumber":        "Saddad #",
      "ShipmentLevelStatus": "Status",
      "BLNumber":            "Bill of Lading #",
    }
    cols = get_table_metadata(RFT_Shipment, shipments, friendly)
    include = {"Brand", "ShipmentNumber", "BiyanNumber", "SADDADNumber", "BLNumber"}
    columns = [c for c in cols if c["name"] in include]

    for c in columns:
        c["filter_type"] = "text"

    columns.append({"name": "Brand", "label": friendly["Brand"]})

    # 5) Get existing files
    existing_biyan  = os.listdir(BIYAN_FOLDER)  if os.path.isdir(BIYAN_FOLDER)  else []
    existing_saddad = os.listdir(SADDAD_FOLDER) if os.path.isdir(SADDAD_FOLDER) else []

    for shp in shipments:
        # a) check nulls
        mapper = inspect(shp).mapper
        shp.has_null = any(getattr(shp, col.key) is None for col in mapper.columns)

        # b) check balance
        total_shipped = sum(line.QtyShipped for line in shp.po_lines)
        total_contained = sum(cl.QtyInContainer for cont in shp.containers for cl in cont.lines)
        shp.is_qty_balanced = (total_shipped == total_contained)

    return render_template("createdShipments.html",
        rows=shipments,
        columns=columns,
        shipmentstatuses=all_status,
        existing_biyan_files=existing_biyan,
        existing_saddad_files=existing_saddad,
        DELIVERED_STATUSES=DELIVERED_STATUSES
    )



@bp.route("/completedShipments", methods=["GET", "POST"]) 
@login_required
def completedShipments():
    """
    All Shipments / completed
    """
    
    H2 = aliased(RFT_StatusHistory)
    PO     = aliased(RFT_PurchaseOrder)
    POLine = aliased(RFT_PurchaseOrderLine)

    if request.method == 'POST' and 'export' in request.form:
        shp_ids = request.form.getlist('export') #TODO
        return export_shipment_expense_report()
    
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
            log.debug(f"Status: {st}")
            if st:
                model.add(RFT_StatusHistory(
                    EntityType ="Shipment",
                    EntityID   = shp.ShipmentID,
                    Status     = st,
                    StatusDate = func.now(),
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
                            StatusDate = func.now(),
                            UpdatedBy  = session['username'],
                            Comments   = f"Auto when shipment status changed to: {st}"
                        ))
                any_change = True

            # PDF uploads
            for tag, folder in (("biyanPDF", BIYAN_FOLDER), ("saddadPDF", SADDAD_FOLDER)):
                fieldname = f"{tag}_file_{sn}"
                f = request.files.get(fieldname)
                
                if not f:
                    print(f"No file in request.files for field {fieldname}")
                    continue

                if not f.filename:
                    print(f"Field {fieldname} submitted empty filename")
                    continue
                
                # Determine prefix pattern
                prefix = f"{sn}_{tag}"

                # Delete old files with this prefix
                for fname in os.listdir(folder):
                    if fname.startswith(prefix + "."):
                        try:
                            os.remove(os.path.join(folder, fname))
                            print(f"Removed old file: {fname}")
                        except Exception as e:
                            print(f"Failed to remove {fname}: {e}")

                # 4) build secure filename + ensure folder exists
                ext    = os.path.splitext(f.filename)[1]  # includes the dot, e.g. ".pdf", ".jpg"
                fn     = secure_filename(f"{sn}_{tag}{ext}")
                
                target = os.path.join(folder, fn)
                try:
                    os.makedirs(folder, exist_ok=True)
                except Exception as e:
                    print(f"Could not create directory {folder}: {e}")
                    flash("Server error preparing uploads folder", "danger")
                    return jsonify({"redirect": url_for("main.completedShipments")})
                
                # 5) actually save, catching any errors
                try:
                    f.save(target)
                    print(f"Saved uploaded PDF to {target}")
                    any_change = True
                except Exception as e:
                    print(f"Failed to save upload to {target}: {e}")
                    flash("Server error saving uploaded file", "danger")
                    return jsonify({"redirect": url_for("main.completedShipments")})

            if any_change:
                shp.LastUpdated   = func.now()
                shp.LastUpdatedBy = session['username']
                updated.add(sn)

        model.commit()

        if updated:
            flash(f"✅ Updated: {', '.join(sorted(updated))}", "success")
        if not_found:
            flash(f"❌ Not found: {', '.join(sorted(not_found))}", "warning")
            
        return jsonify({"redirect": url_for("main.completedShipments")})

    # ─── GET ────────────────────────────────────────────────────────────

    # build the shipments + attach latest status
    H1 = aliased(RFT_StatusHistory)
    latest = (
        model
        .query(
        H1.EntityID.label("ShipmentID"),
        func.max(H1.StatusDate).label("mx")
        )
        .filter(H1.EntityType == literal("Shipment"))
        .group_by(H1.EntityID)
        .subquery()
    )

    H2 = aliased(RFT_StatusHistory)
    q = (
        model
        .query(
        RFT_Shipment,
        PO.Brand.label("Brand"),   
        H2.Status.label("ShipmentLevelStatus")
        )
        # join PO via the ShipmentPO → PO‐Line → PO chain
        .join(
            RFT_ShipmentPOLine,
            RFT_ShipmentPOLine.ShipmentID == RFT_Shipment.ShipmentID
        )
        .join(
            POLine,
            POLine.POLineID == RFT_ShipmentPOLine.POLineID
        )
        .join(
            PO,
            PO.POID == POLine.POID
        )
        # join to get each shipment’s max(StatusDate)
        .outerjoin(latest, latest.c.ShipmentID == RFT_Shipment.ShipmentID)
        # now join back *only* on Shipment history rows
        .outerjoin(
        H2,
        and_(
            H2.EntityType == literal("Shipment"),
            H2.EntityID == latest.c.ShipmentID,
            H2.StatusDate == latest.c.mx
        )
        )
        .filter(H2.Status.in_(DELIVERED_STATUSES) )
        .order_by(desc(RFT_Shipment.CreatedDate))
    )
    shipments = []
    for shp, brand, st in q:
        shp.ShipmentLevelStatus = st or ""
        shp.Brand               = brand
        shipments.append(shp)

    # columns metadata (no server‐side filtering)
    friendly = {
      "Brand":               "Brand",
      "ShipmentNumber":      "Shipment #",
      "CreatedDate":         "Created",
      "BiyanNumber":         "Biyan #",
      "SADDADNumber":        "Saddad #",
      "ShipmentLevelStatus": "Status",
      "BLNumber":            "Bill of Lading #",
    }
    cols = get_table_metadata(RFT_Shipment, shipments, friendly)
    
    include = {"Brand", "ShipmentNumber", "BiyanNumber", "SADDADNumber", "BLNumber"}
    columns = [c for c in cols if c["name"] in include]
    for c in columns:
        # if c["name"] == "BLNumber":
        c["filter_type"] = "text"
    columns.append({"name": "Brand", "label": friendly["Brand"]})

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
    
    for shp in shipments:
        # 1) Any NULL columns on the shipment?
        #    We inspect every Column in the model and see if getattr is None.
        mapper = inspect(shp).mapper
        null_found = False
        for col in mapper.columns:
            if getattr(shp, col.key) is None:
                null_found = True
                break
        shp.has_null = null_found

        # 2) Is total QtyShipped == total QtyInContainer?
        total_shipped   = sum(line.QtyShipped for line in shp.po_lines)
        # print(shp.ShipmentNumber +" shipped = "+ str(total_shipped))
        total_contained = sum(
            cl.QtyInContainer
            for cont in shp.containers
            for cl   in cont.lines
        )
        # print(shp.ShipmentNumber +" contained = "+ str(total_contained))
        shp.is_qty_balanced = (total_shipped == total_contained)
        # you could also do shp.pending_qty = total_shipped - total_contained
    

    return render_template("createdShipments.html",
      rows                  = shipments,
      columns               = columns,
      shipmentstatuses      = shipmentstatuses,
      existing_biyan_files  = existing_biyan,
      existing_saddad_files = existing_saddad,
      DELIVERED_STATUSES    = DELIVERED_STATUSES
    )

@bp.route("/updateShipments/<shipment_id>", methods=["GET", "POST"])
@login_required
def updateShipments(shipment_id):
    if request.method == "POST":
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
            "container_deadline": request.form.get("container_deadline"), #NEW
            
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
            "cost_remarks":             request.form.get("cost_remarks"),
            
            "mawani_charges":           request.form.get("mawani_charges"),
            "inspection_charges":       request.form.get("inspection_charges"),
            "cc_agent_invoice":         request.form.get("cc_agent_invoice"),
        }

        shipment = model.query(RFT_Shipment).filter_by(ShipmentID=shipment_id).first()
        if shipment:
            log.debug(f"matching Shipment found id:{shipment_id}")
            for pol in shipment.po_lines:
                key = pol.ShipmentPOLineID
                raw = request.form.get(f"weight[{key}]", "").strip()
                if not raw:
                    continue
                try:
                    total_wt = float(raw)
                except ValueError:
                    continue

                qty = pol.QtyShipped or 1
                unit_wt = total_wt / qty

                # upsert by article name
                article_name = pol.po_line.Article
                aw = (
                    model.query(RFT_ArticleWeight)
                        .filter_by(Article=article_name)
                        .one_or_none()
                )
                if aw:
                    aw.WeightKG   = unit_wt
                    aw.UpdatedBy  = session.get("username","system")
                    aw.UpdatedAt  = func.now()
                else:
                    aw = RFT_ArticleWeight(
                        Article   = article_name,
                        WeightKG  = unit_wt,
                        UpdatedBy = session.get("username","system")
                    )
                    model.add(aw)

            # ―――― Now commit everything at once ―――― #
            model.commit()
            
            
            # 1) pull existing invoices for this shipment
            existing = {
                inv.InvoiceNumber: inv
                for inv in model.query(RFT_Invoices)
                                .filter_by(ShipmentID=shipment_id)
                                .all()
            }
            
            # 2) collect submitted pairs, skipping any with empty number or empty value
            submitted = []
            for num, val in zip(request.form.getlist("invoice_numbers"),request.form.getlist("invoice_values") ):
                num = (num or "").strip()
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    continue
                if not num:
                    continue
                submitted.append((num, v))
                    
            # 3) upsert each submitted invoice
            for i, (num, v) in enumerate(submitted):
                file = request.files.get(f"invoice_files_{i}")
                if num in existing:
                    inv = existing.pop(num)        # remove from existing so leftover = to-delete
                    inv.InvoiceValue = v
                    inv.UpdatedBy    = session.get("username", "system")
                    inv.UpdatedAt    = func.now()
                else:
                    inv = RFT_Invoices(
                        ShipmentID    = shipment_id,
                        InvoiceNumber = num,
                        InvoiceValue  = v,
                        CreatedBy     = session.get("username","system"),
                        UpdatedBy     = session.get("username","system")
                    )
                    model.add(inv)
                # model.commit()
                model.flush()
                
                # --- now handle the uploaded file for this invoice ---
                if file and file.filename:
                    original_name = secure_filename(file.filename)
                    name_part, ext = os.path.splitext(original_name)
                    filename = f"{num}_{uuid.uuid4().hex[:8]}_{shipment.ShipmentNumber}{ext}"
                    # define an upload path, e.g. <app>/static/uploads/invoices/
                    upload_folder = os.path.join(
                        STATIC_DIR, "uploads", "invoices"
                    )
                    os.makedirs(upload_folder, exist_ok=True)
                    filepath = os.path.join(upload_folder, filename)
                    # IF it fails
                    try:
                        file.save(filepath)
                        relpath = os.path.join("uploads", "invoices", filename)
                        # flip Windows backslashes into URL slashes
                        relpath = relpath.replace(os.path.sep, "/")
                        inv.DocumentPath = relpath
                    except OSError as e:
                        flash(f"Could not save invoice file for {num}: {e}", "danger")
                        
            # 4) delete any invoices the user removed
            for orphan in existing.values():
                model.delete(orphan)
                
            model.commit()
            
            shipment.BLNumber           = shipment_data["bl_number"]
            shipment.ShippingLine       = shipment_data["shipping_line"]
            shipment.CCAgent            = shipment_data["cc_agent"]
            
            shipment.POD                = shipment_data["DestinationPort"]
            shipment.DestinationCountry = shipment_data["destination_country"]
            shipment.OriginPort         = shipment_data["OriginPort"]
            shipment.OriginCountry      = shipment_data["origin_country"]
            
            # Estimated times
            shipment.ECCDate            = shipment_data["ecc_date"]             or None
            shipment.ETAOrigin          = shipment_data["eta_origin"]           or None
            shipment.ETDOrigin          = shipment_data["etd_origin"]           or None
            shipment.ETADestination     = shipment_data["eta_destination"]      or None
            shipment.ETDDestination     = shipment_data["etd_destination"]      or None
            shipment.ETAWH              = shipment_data["eta_wh"]               or None
            shipment.ContainerDeadline  = shipment_data["container_deadline"]   or None
            
            # Cost fields
            shipment.FreightCost       = float(shipment_data["freight_cost"] or 0)
            shipment.CustomDuties      = float(shipment_data["custom_duties_fob"] or 0)
            shipment.ValueDecByCC      = float(shipment_data["value_declared_customs"] or 0)
            shipment.DO_Port_Charges   = float(shipment_data["do_charges"] or 0)
            shipment.ClearanceTransportCharges\
                                       = float(shipment_data["clearance_transport"] or 0)
            shipment.SaberSADDAD       = float(shipment_data["saber_saddad"] or 0)
            shipment.DemurrageCharges  = float(shipment_data["demurrage_charges"] or 0)
            shipment.Penalties         = float(shipment_data["penalties"] or 0)
            shipment.YardCharges       = float(shipment_data["yard_charges"] or 0)
            shipment.OtherCharges      = float(shipment_data["other_charges"] or 0)
            # new charges (undecided)
            shipment.MAWANICharges     = float(shipment_data["mawani_charges"] or 0)
            shipment.InspectionCharges = float(shipment_data["inspection_charges"] or 0)
            # new invoice
            shipment.CcAgentInvoice    = shipment_data["cc_agent_invoice"]

            shipment.CostRemarks       = shipment_data["cost_remarks"]
            
            shipment.LastUpdatedBy = session.get('username', 'system')
            shipment.LastUpdated = func.now()

        # -- Process Non-PO Items (Cargo) ----------------------------------------- #
        nonpo_items = []
        new_item_row_num = request.form.getlist("non_po_items")
        if new_item_row_num:
            # idx = int(new_item_row_num)
            for idx in new_item_row_num:
                article = request.form.get(f"cargo[{idx}][article]")
                if article is None:
                    break
                
                non_po_brand = shipment.po_lines[0].po_line.purchase_order.Brand
                
                nonpo_items.append({
                    "id": request.form.get(f"cargo[{idx}][id]"),
                    "supplier": request.form.get(f"cargo[{idx}][supplier]"),
                    "po": request.form.get(f"cargo[{idx}][po]"),
                    "article":  article,
                    "sap_line": request.form.get(f"cargo[{idx}][sap_line]"),
                    "qty": request.form.get(f"cargo[{idx}][qty]"),
                    "value": request.form.get(f"cargo[{idx}][value]"),
                    "Brand": non_po_brand
                })
                # idx += 1
        
        # build a map of existing rows keyed however you like (e.g. by supplierId or PO+line)
        existing = {
            rec.ID: rec
            for rec in model.query(RFT_NonPoItems)
                            .filter_by(ShipmentID=shipment_id)
                            .all()
        }
        
        # then for each submitted item:
        for itm in nonpo_items:
            key = itm['id']   # or whatever unique key you choose
            if key in existing:
                rec = existing.pop(key)
                rec.PONumber    = itm['po']
                rec.SAPItemLine = itm['sap_line']
                rec.Supplier     = itm['supplier']
                rec.Qty         = float(itm['qty'] or 0)
                rec.Value       = float(itm['value'] or 0)
                rec.Brand       = itm['Brand'] or "None"
            else:
                rec = RFT_NonPoItems(
                    ShipmentID= shipment_id,
                    PONumber    = itm['po'],
                    Supplier    = itm['supplier'],
                    SAPItemLine = itm['sap_line'],
                    Article     = itm['article'],
                    Qty         = float(itm['qty'] or 0),
                    Value       = float(itm['value'] or 0),
                    Brand       = itm['Brand'] or "None"
                )
                model.add(rec)
        
        # delete any leftover:
        for orphan in existing.values():
            model.delete(orphan)
        
        model.commit()

        # --- 3) Per-container upsert + deletion logic ---
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


        # 1) Build a sorted list of all the container‐indices the user actually submitted
        
        pattern = re.compile(r'^containers\[(\d+)\]\[container_number\]$')
        submitted_idxs = sorted(
            int(m.group(1))
            for key in request.form.keys()
            if (m := pattern.match(key))
        )
        
        # 2) Loop through only those indices
        for idx in submitted_idxs:
            cn = request.form.get(f"containers[{idx}][container_number]", "").strip()
            # skip blank container numbers
            if not cn:
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
            cont.CCDate             = request.form.get(f"containers[{idx}][ccdate]") or None
            cont.ATAOrigin          = request.form.get(f"containers[{idx}][ata_op]") or None
            cont.ATDOrigin          = request.form.get(f"containers[{idx}][atd_op]") or None
            cont.ATADP              = request.form.get(f"containers[{idx}][ata_dp]") or None
            cont.ATDDPort           = request.form.get(f"containers[{idx}][atd_dp]") or None
            cont.ATAWH              = request.form.get(f"containers[{idx}][ata_wh]") or None
            cont.YardInDate         = request.form.get(f"containers[{idx}][yard_in_date]") or None
            cont.YardOutDate        = request.form.get(f"containers[{idx}][yard_out_date]") or None
            # cont.ContainerDeadline  = request.form.get(f"containers[{idx}][container_deadline]") or None
            cont.ContainerRemarks   = request.form.get(f"containers[{idx}][container_remarks]")
            cont.UpdatedBy          = session.get("username","system")
            cont.UpdatedAt          = func.now()

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
                    StatusDate  = func.now(),
                    UpdatedBy   = session['username'],
                    Comments    = "Updated from Update shipmet mainpage"
                )
                model.add(h)
            if planed_status and planed_status != "":
                i = RFT_StatusHistory(
                    EntityType  = "Planed-Container",
                    EntityID    = cont.ContainerID,
                    Status      = planed_status,
                    StatusDate  = func.now(),
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
        
        # --- H) If every container’s latest status is “Delivered”, mark shipment Delivered ---
        # grab all container IDs for this shipment
        container_ids = [
            cont.ContainerID
            for cont in model.query(RFT_Container.ContainerID)
                             .filter(RFT_Container.ShipmentID == shipment_id)
                             .all()
        ]
        
        if container_ids:
            all_delivered = True
            for cid in container_ids:
                latest = (
                    model.query(RFT_StatusHistory.Status)
                         .filter_by(EntityType="Container", EntityID=cid)
                         .order_by(RFT_StatusHistory.StatusDate.desc())
                         .first()
                )
                if not latest or latest.Status != 'Delivered':
                    all_delivered = False
                    break
        
            if all_delivered:
                i = RFT_StatusHistory(
                    EntityType  = "Shipment",
                    EntityID    = shipment_id,
                    Status      = 'Delivered',
                    StatusDate  = func.now(),
                    UpdatedBy   = session['username'],
                    Comments    = "Updated automatically upon complition"
                )
                model.add(i)
        
        try:
            model.commit()
            flash("Shipment updated successfully.", "success")
        except:
            model.rollback()
            flash("An Error occurred", "danger")
        
        
        return redirect(url_for("main.updateShipments", shipment_id=shipment_id))


    ############################################# --
    # -- GET request: load existing shipment data --
    ############################################# --
    current_form_data = (
        model
        .query(RFT_Shipment)
        .options(
            #  load po_lines → po_line → article_weight
            joinedload(RFT_Shipment.po_lines).joinedload(RFT_ShipmentPOLine.po_line).joinedload(RFT_PurchaseOrderLine.article_weight)
            , joinedload(RFT_Shipment.containers).joinedload(RFT_Container.lines)
            , joinedload(RFT_Shipment.non_po_items).joinedload(RFT_NonPoItems.shipment)
        )
        .filter(RFT_Shipment.ShipmentID == shipment_id).first()
    )
    
    # print(current_form_data.ECCDate)
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
    
    # ship_docs_extra = os.listdir(SHP_DOC_UPLOAD_FOLDER)
    # cc_inv_files = os.listdir(CC_UPLOAD_FOLDER)
    sn = shipment_number.ShipmentNumber  

    # Get only files starting with this shipment number
    ship_docs_extra = [
        f for f in os.listdir(SHP_DOC_UPLOAD_FOLDER)
        if f.startswith(sn + "_")
    ]

    cc_inv_files = [
        f for f in os.listdir(CC_UPLOAD_FOLDER)
        if f.startswith(sn + "_")
    ]
        
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
    cargo_types = model.query(RFT_CargoTypes).all()
    
    return render_template(
        "updateShipments.html",
        report_data         = current_form_data,
        report_data_json    = report_data_json,
        unique_cids         = unique_cids,
        shipment_id         = shipment_id,
        shipment_number     = shipment_number,
        containerstatuses   = containerstatuses,
        
        countries       = countries,
        uploaded_files  = ship_docs_extra,
        cc_inv_files    = cc_inv_files,
        
        custom_agents      = custom_agents,
        origin_ports       = origin_ports,
        shipping_lines     = shipping_lines,
        destination_ports  = destination_ports,
        invoices           = invoices,
        cargo_types        = cargo_types
    )



# Handle file upload for filepond js
@bp.route('/upload', methods=['POST'])
def upload():
    """
    Handle file upload FOR:
    Shipment docs
    CC invoices
    
    """
    files = request.files.getlist('filepond')
    shipment_number = request.form.get('shipment_number')
    
    if not files or not shipment_number:
        return "Missing file or shipment number", 400
    
    saved_filenames = []
    today_str = datetime.now().strftime('%d%m%y')
    
    for f in files: 
        original_name = secure_filename(f.filename)
        ext = os.path.splitext(original_name)[1]
        new_filename = secure_filename(f"{shipment_number}_{os.path.splitext(original_name)[0]}_{today_str}{ext}")
        f.save(os.path.join(SHP_DOC_UPLOAD_FOLDER, new_filename))
        saved_filenames.append(new_filename)
        
    return jsonify({"filename": saved_filenames})
# cc incoice
@bp.route('/upload_cc_invoice_files', methods=['POST'])
def upload_cc():
    files = request.files.getlist('cc_invoices_files')
    shipment_number = request.form.get('shipment_number')
    
    if not files or not shipment_number:
        return "Missing file or shipment number", 400
    
    saved_filenames = []
    today_str = datetime.now().strftime('%d%m%y')
    
    for f in files: 
        original_name = secure_filename(f.filename)
        ext = os.path.splitext(original_name)[1]
        new_filename = secure_filename(f"{shipment_number}_{os.path.splitext(original_name)[0]}_{today_str}{ext}")
        f.save(os.path.join(CC_UPLOAD_FOLDER, new_filename))
        saved_filenames.append(new_filename)
        
    return jsonify({"filename": saved_filenames})

@bp.route('/delete', methods=['DELETE'])
def delete():
    data = request.get_json()
    if not data or 'filename' not in data or 'shipment_number' not in data:
        return "Missing filename", 400

    # shipment_number = secure_filename(data['shipment_number'])
    filename = secure_filename(os.path.basename(data['filename']))

    print(f"Looking for file starting with: {filename}")

    deleted = False
    for folder in [SHP_DOC_UPLOAD_FOLDER, CC_UPLOAD_FOLDER]:
        try:
            for fname in os.listdir(folder):
                if fname.startswith(f"{filename}"):
                    file_path = os.path.join(folder, fname)
                    os.remove(file_path)
                    print(f"Deleted: {file_path}")
                    deleted = True
                    break
            if deleted:
                break
        except Exception as e:
            print(f"Error while deleting in {folder}: {e}")

    return ('', 200) if deleted else ("File not found", 404)

@bp.route('/delete_realtime', methods=['DELETE'])
def delete_realtime():
    data = request.get_json()  # Get JSON payload from body
    if not data or 'filename' not in data:
        return "No filename provided", 400

    print("ORIGINAL NAME : "+ str(data['filename']))
    
    filename = os.path.basename(data['filename'])
    
    # if isinstance(filename, list):
    filename = filename[0]
    filename = secure_filename(filename)

    print("AFTER SECURE: "+ filename)
    
    deleted = False
    for folder in [SHP_DOC_UPLOAD_FOLDER, CC_UPLOAD_FOLDER]:
        try:
            for fname in os.listdir(folder):
                if fname.startswith(f"{filename}"):
                    file_path = os.path.join(folder, fname)
                    os.remove(file_path)
                    print(f"Deleted: {file_path}")
                    deleted = True
                    break
            if deleted:
                break
        except Exception as e:
            print(f"Error while deleting in {folder}: {e}")
            break
    
    return ('', 200) if deleted else ("File not found", 404)

@bp.route('/files/<path:filename>')  # allow for slashes or encoded chars
def serve_file(filename):
    filename = os.path.basename(secure_filename(filename))

    full_path_ship = os.path.join(SHP_DOC_UPLOAD_FOLDER, filename)
    full_path_cc = os.path.join(CC_UPLOAD_FOLDER, filename)

    print("Trying:", full_path_ship, full_path_cc)

    if os.path.exists(full_path_ship):
        return send_from_directory(SHP_DOC_UPLOAD_FOLDER, filename)
    elif os.path.exists(full_path_cc):
        return send_from_directory(CC_UPLOAD_FOLDER, filename)
    else:
        return "File not found", 404
##


##


# Delete Po
@bp.route('/delete-po', defaults={'po_id': None}, methods=['GET', 'POST'])
@bp.route('/delete-po/<int:po_id>', methods=['GET', 'POST'])
@login_required
def delete_po(po_id):
    model = Session()

    # if request.method == 'POST':
    po_id = request.form.get("po_id", type=int) or po_id

    if not po_id:
        flash("Invalid PO ID when deleting.", "danger")
        return redirect(url_for('main.createdShipments'))

    po = model.query(RFT_PurchaseOrder).filter_by(POID=po_id).first()

    if not po:
        flash("Purchase Order not found To DELETE.", "danger")
        return redirect(url_for('main.initialPO_Updates'))

    # Check usage
    used_shipment_lines = model.query(RFT_ShipmentPOLine)\
        .join(RFT_PurchaseOrderLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)\
        .filter(RFT_PurchaseOrderLine.POID == po_id).all()

    used_container_lines = model.query(RFT_ContainerLine)\
        .join(RFT_ShipmentPOLine, RFT_ContainerLine.ShipmentPOLineID == RFT_ShipmentPOLine.ShipmentPOLineID)\
        .join(RFT_PurchaseOrderLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)\
        .filter(RFT_PurchaseOrderLine.POID == po_id).all()

    if (used_shipment_lines or used_container_lines) and 'confirm' not in request.form:
        related_shipments = list({line.ShipmentID for line in used_shipment_lines})
        related_containers = list({line.ContainerID for line in used_container_lines})

        shipments = model.query(RFT_Shipment).filter(RFT_Shipment.ShipmentID.in_(related_shipments)).all()
        containers = model.query(RFT_Container).filter(RFT_Container.ContainerID.in_(related_containers)).all()

        return render_template("confirmDeletePO.html", id=po_id, shipments=shipments, containers=containers)

    # Delete related container lines
    container_lines = model.query(RFT_ContainerLine)\
        .join(RFT_ShipmentPOLine)\
        .join(RFT_PurchaseOrderLine)\
        .filter(RFT_PurchaseOrderLine.POID == po_id).all()
    for cl in container_lines:
        model.delete(cl)

    # Delete shipment lines
    shipment_lines = model.query(RFT_ShipmentPOLine)\
        .join(RFT_PurchaseOrderLine)\
        .filter(RFT_PurchaseOrderLine.POID == po_id).all()
    for sp in shipment_lines:
        model.delete(sp)

    # Delete PO lines
    for line in po.order_lines:
        model.delete(line)

    # Finally delete PO
    model.delete(po)
    model.commit()

    flash(f"PO {po.PONumber} and related records deleted.", "success")
    return redirect(url_for('main.initialPO_Updates'))

@bp.route('/delete-pol', defaults={'poline_id': None}, methods=['GET', 'POST'])
@bp.route('/delete-pol/<int:poline_id>', methods=['GET', 'POST'])
@login_required
def delete_po_line(poline_id):
    model = Session()

    # Find the PO line
    pol = model.query(RFT_PurchaseOrderLine).filter_by(POLineID=poline_id).first()

    if not pol:
        flash("PO Line not found.", "danger")
        return redirect(request.referrer or url_for('main.createShipments'))

    # Check if this PO line is used in any shipment or container
    used_shipment_lines = model.query(RFT_ShipmentPOLine).filter_by(POLineID=poline_id).all()
    used_container_lines = model.query(RFT_ContainerLine)\
        .join(RFT_ShipmentPOLine, RFT_ContainerLine.ShipmentPOLineID == RFT_ShipmentPOLine.ShipmentPOLineID)\
        .filter(RFT_ShipmentPOLine.POLineID == poline_id).all()

    if (used_shipment_lines or used_container_lines) and 'confirm' not in request.form:
        related_shipments = list({line.ShipmentID for line in used_shipment_lines})
        related_containers = list({line.ContainerID for line in used_container_lines})

        shipments = model.query(RFT_Shipment).filter(RFT_Shipment.ShipmentID.in_(related_shipments)).all()
        containers = model.query(RFT_Container).filter(RFT_Container.ContainerID.in_(related_containers)).all()

        return render_template("confirmDeletePO.html", id=pol, shipments=shipments, containers=containers)

    # Delete related container lines
    for cl in used_container_lines:
        model.delete(cl)

    # Delete shipment lines
    for sp in used_shipment_lines:
        model.delete(sp)

    # Delete PO line
    model.delete(pol)
    model.commit()

    flash("PO Line and related records deleted.", "success")
    return redirect(url_for('main.createShipments'))


@bp.route('/reverse-shipment', defaults={'shipment_id': None}, methods=['GET', 'POST'])
@bp.route('/reverse-shipment/<int:shipment_id>', methods=['GET', 'POST'])
def reverse_shipment(shipment_id):
    if request.method == 'POST':  
        shipment_id = request.form.get("shipment_id", type=int)
        
        if not shipment_id:
            flash("Invalid shipment ID when DELETING.", "danger")
            return redirect(url_for('main.createdShipments'))
        
        # Load shipment and related data
        shipment = model.query(RFT_Shipment).filter_by(ShipmentID=shipment_id).first()

        if not shipment:
            flash("Shipment not found.", "danger")
            return redirect(url_for('main.createdShipments'))
        
        # Check if any containers exist
        if shipment.containers:  # assuming `containers` is a relationship
            flash("Cannot reverse shipment with containers.", "warning")
            return redirect(url_for('main.createdShipments'))

        # Reverse each shipment PO line
        for sp_line in shipment.po_lines:
            pol = sp_line.po_line  # assuming `po_line` is the relationship to RFT_PurchaseOrderLine

            if pol:
                pol.BalanceQty = pol.BalanceQty + sp_line.QtyShipped  

            model.delete(sp_line)

        # Delete the shipment itself
        model.delete(shipment)

        model.commit()
        
        flash(f"Shipment {shipment.ShipmentNumber} reversed.", "success")
        return redirect(url_for('main.createdShipments'))
    else:
        shipments = model.query(RFT_Shipment).all()
        
        # For GET: render the page with or without shipment_id
        return render_template(
            'reverseShipment.html',
            shipments=shipments,
            preselected_shipment_id=shipment_id
        )

@bp.route('/api/shipment-info/<int:shipment_id>')
def get_shipment_info(shipment_id):
    shipment = model.query(RFT_Shipment).filter_by(ShipmentID=shipment_id).first()
    if not shipment:
        return jsonify({"error": "Shipment not found"}), 404

    po_numbers = set()
    po_lines_data = []

    for sp_line in shipment.po_lines:
        if not shipment.containers:
            locked = False 
        else:
            locked = True # Containers will act as my lock
        
        pol = sp_line.po_line
        if pol:
            desc = getattr(pol, "Article", "N/A")
            po = pol.purchase_order

            po_lines_data.append({
                "id": pol.POLineID,
                "description": desc,
                "qty": sp_line.QtyShipped,
                "locked": locked
            })

            if po:
                po_numbers.add(po.PONumber)

    return jsonify({
        "po_numbers": sorted(po_numbers),
        "po_lines": po_lines_data
    })


@bp.route('/shipment/<int:shipment_id>/remove-line/<int:shipment_po_line_id>', methods=['POST'])
def remove_shipment_line(shipment_id, shipment_po_line_id):
    model = Session()

    shipment = model.query(RFT_Shipment).filter_by(ShipmentID=shipment_id).first()
    if not shipment:
        return jsonify({"status": "error", "message": "Shipment not found."}), 404

    # if shipment.containers:
    #     return jsonify({"status": "warning", "message": "Cannot modify shipment. Containers already exist."}), 400

    line = model.query(RFT_ShipmentPOLine).filter_by(ShipmentPOLineID=shipment_po_line_id).first()
    if not line:
        return jsonify({"status": "error", "message": "Shipment line not found."}), 404
    
    c_line = model.query(RFT_ContainerLine).filter_by(ShipmentPOLineID=shipment_po_line_id).first()
    if c_line:
        return jsonify({"status": "error", "message": "Please remove this Qty from the container first."}), 404

    po_line = line.po_line
    if po_line:
        po_line.BalanceQty += line.QtyShipped

    model.delete(line)
    model.commit()
    
    return jsonify({"status": "success", "message": "Shipment line removed and quantity returned to PO line."})

@bp.route('/shipment/add-line', methods=['POST'])
def add_shipment_line():
    model = Session()

    shipment_num = request.form.get("shipment_num").strip()
    
    shipment = model.query(RFT_Shipment).filter_by(ShipmentNumber = shipment_num).first()
    
    if not shipment:
        flash("Shipment not found.", "danger")
        return redirect(request.referrer)
    
    # if shipment.containers:
    #     flash("Cannot modify shipment. Containers already exist.", "warning")
    #     return redirect(request.referrer)
    
    selected_poids = request.form.getlist('POID')
    if not selected_poids:
        flash('No purchase orders selected.', 'warning')
        return redirect(url_for('main.createShipments')) 
    
    selected_qtys = []
    for ids in selected_poids:
        selected_qty = request.form.get(f'selected_qty_{ids}')
        selected_qtys.append(selected_qty)
    
    for poid, qty in zip(selected_poids, selected_qtys):
        poline_id = int(poid)
        qty_to_ship = int(qty)
    
        po_line = model.query(RFT_PurchaseOrderLine).filter_by(POLineID=poline_id).first()
        if not po_line:
            flash("PO line not found.", "danger")
            continue
        
        po_number = po_line.purchase_order.PONumber
        article = po_line.Article
        
        # ✅ Check if this PO Line is already in the shipment
        existing_line = model.query(RFT_ShipmentPOLine).filter_by(
            ShipmentID=shipment.ShipmentID,
            POLineID=poline_id
        ).first()
        if existing_line:
            flash(
                f"⚠️ Line already exists in shipment: PO {po_number}, Article {article}. "
                f"To update, remove the line first and refresh this page.",
                "warning"
            )
            continue
        
        if qty_to_ship <= 0 or qty_to_ship > po_line.BalanceQty:
            # flash(f"Invalid quantity to ship. Selected-QTY={qty_to_ship} Available-QTY={po_line.BalanceQty}", "danger")
            flash(
                f"❌ Invalid qty ({qty_to_ship}) for: PO {po_number}, Article {article}. "
                f"Available: {po_line.BalanceQty}.",
                "danger"
            )
            continue

        new_line = RFT_ShipmentPOLine(
            ShipmentID=shipment.ShipmentID,
            POLineID=po_line.POLineID,
            QtyShipped=qty_to_ship,
            LastUpdatedBy=session.get("username", "system")
        )
        model.add(new_line)
        
        po_line.BalanceQty -= qty_to_ship

        model.commit()
        flash("Line added to shipment.", "success")
        
    return redirect(request.referrer)


@bp.route("/update_containers", methods=["GET", "POST"])
def update_containers():
    if request.method == "POST":
        # how many rows did we render?
        num_cunt = model.query(RFT_Container.ContainerID).count()
        idx = 0
        ids_list = []
        while True:
            ids = request.form.get(f"containers[{idx}][id]", None)
            if idx<=num_cunt:
                idx +=1
                if ids:
                    ids_list.append(ids.strip())
            else:
                break
        
        if not ids_list:
            flash("No Containers selected !", "warning")
            return redirect(url_for("main.update_containers"))
        
        updated_records = []
        for cid in ids_list:
            log.debug("Loop started in update containers")

            # fetch the real container record
            rec = model.get(RFT_Container, cid)
            if not rec:
                continue
            
            # TODO container status update from containers update page
            # status = request.form.get(f"containers[{cid}][status]")
            
            # current = model.query(RFT_StatusHistory)\
            #             .filter(RFT_StatusHistory.EntityID == int(cid))\
            #             .filter(RFT_StatusHistory.EntityType == "Container")
            
            # # if Status, add a history entry
            # if current_status and current_status != "":
            #     h = RFT_StatusHistory(
            #         EntityType  = "Container",
            #         EntityID    = cont.ContainerID,
            #         Status      = current_status,
            #         StatusDate  = func.now(),
            #         UpdatedBy   = session['username'],
            #         Comments    = "Updated from Update shipmet mainpage"
            #     )
            #     model.add(h)
            
            # any date fields we want to parse?
            def parse_date(field):
                s = request.form.get(f"containers[{cid}][{field}]", "").strip()
                if s and rec.ContainerNumber not in updated_records:
                    updated_records.append(rec.ContainerNumber)
                return datetime.strptime(s, "%Y-%m-%d") if s else None

            # 4) apply all the inputs back to the container
            rec.CCDate               = parse_date("CCDate")
            rec.ATAOrigin            = parse_date("ATAOrigin")
            rec.ATDOrigin            = parse_date("ATDOrigin")
            rec.ATADP                = parse_date("ATADP")
            rec.ATDDPort             = parse_date("ATDDPort")
            rec.ATAWH                = parse_date("ATAWH")
            rec.YardInDate           = parse_date("YardInDate")
            rec.YardOutDate          = parse_date("YardOutDate")
            rec.ContainerRemarks     = request.form.get(f"containers[{cid}][ContainerRemarks]", "").strip()
            
            
        model.commit()
        for updated_record in updated_records:
            flash(f"Container '{updated_record}' Updated", "success" )
        return redirect(url_for("main.update_containers"))
    
    # =============GET
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
             RFT_PurchaseOrder.Brand,
             RFT_Shipment.BLNumber,
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
    for cont, brand, bl, ship_no, status in q.all():
        # print(brand)
        cont.Brand = brand
        cont.BL = bl
        cont.ShipmentNumber       = ship_no
        cont.ContainerLevelStatus = status
        containers.append(cont)

    # --- 5) build your columns metadata exactly as before ---
    friendly = {
        "Brand":                  "Brand",
        "BL":                     "BL #",
        "ShipmentNumber":         "Shipment No.",
        "ContainerNumber":        "Container No.",
        # "ContainerType":          "Type",
        "ContainerLevelStatus":   "Status",
        "ATAOrigin":              "Loading Time",
        "ATDOrigin":              "ATD Origin",
        "ATADP":                  "ATA Dest. Port",
        "ATDDPort":               "ATD Release- Dport:",
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
        
    
    # Adding Brand
    if not any(c["name"]=="Brand" for c in columns):
        columns.insert(
        # index - before ContainerNumber:
        next(i for i,c in enumerate(columns) if c["name"]=="ShipmentNumber")-14,
        {
            "name":         "Brand",
            "label":        "Brand",
            "type":         "String",
            "filter_type":  "text"
            # "options":      all_status
        }
    )
        
    # Adding BL
    if not any(c["name"]=="BL" for c in columns):
        columns.insert(
        # index - before ContainerNumber:
        next(i for i,c in enumerate(columns) if c["name"]=="ShipmentNumber")+1,
        {
            "name":         "BL",
            "label":        "BL#",
            "type":         "String",
            "filter_type":  "text"
            # "options":      all_status
        }
    )
    

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

@bp.route("/container/status/<path:status>/<path:sel_entity>/<path:mot>", methods=["GET", "POST"])
def containers_status(status = None, sel_entity = None, mot=None):
    # All container‐level statuses from your master table
    all_status = [
        s.StatusName for s in model
            .query(RFT_StatusManagement)
            .filter_by(Level="Container Level")
            .order_by(RFT_StatusManagement.StatusName)
            .all()
    ]

    sel_status = status
    sel_entity = sel_entity  # e.g. 'Planned-DTC' or 'Planned-WH' ANY NAME OF WH
    mot = mot

    # 2️⃣ Latest Normal Status (Container)
    HistC = aliased(RFT_StatusHistory)
    latest_container = (
        model.query(
            HistC.EntityID.label("ContainerID"),
            func.max(HistC.StatusDate).label("max_date")
        )
        .filter(HistC.EntityType == "Container")
        .group_by(HistC.EntityID)
        .subquery()
    )

    HistC2 = aliased(RFT_StatusHistory)
    latest_status = (
        model.query(
            latest_container.c.ContainerID,
            HistC2.Status.label("NormalStatus")
        )
        .join(HistC2, (HistC2.EntityID == latest_container.c.ContainerID) & (HistC2.StatusDate == latest_container.c.max_date))
        .subquery()
    )

    # 3️⃣ Latest Planned Status (Planed-Container)
    HistP = aliased(RFT_StatusHistory)
    latest_planned = (
        model.query(
            HistP.EntityID.label("ContainerID"),
            func.max(HistP.StatusDate).label("max_date")
        )
        .filter(HistP.EntityType == "Planed-Container")
        .group_by(HistP.EntityID)
        .subquery()
    )

    HistP2 = aliased(RFT_StatusHistory)
    latest_plan_status = (
        model.query(
            latest_planned.c.ContainerID,
            HistP2.Status.label("PlannedStatus")
        )
        .join(HistP2, (HistP2.EntityID == latest_planned.c.ContainerID) & (HistP2.StatusDate == latest_planned.c.max_date))
        .subquery()
    )

    # 4️⃣ Now Join Containers with latest statuses
    q = (
      model.query(
            RFT_Container,
            RFT_PurchaseOrder.Brand,
            RFT_Shipment.BLNumber,
            RFT_Shipment.ShipmentNumber,
            latest_status.c.NormalStatus,
            latest_plan_status.c.PlannedStatus
        )
      .join(RFT_Shipment, RFT_Container.ShipmentID == RFT_Shipment.ShipmentID)
      .join(RFT_ShipmentPOLine, RFT_Shipment.ShipmentID == RFT_ShipmentPOLine.ShipmentID)
      .join(RFT_PurchaseOrderLine, RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID)
      .join(RFT_PurchaseOrder, RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID)
      .outerjoin(latest_status, latest_status.c.ContainerID == RFT_Container.ContainerID)
      .outerjoin(latest_plan_status, latest_plan_status.c.ContainerID == RFT_Container.ContainerID)
    )

    filters = []

    # normal status filter (always exact)
    filters.append(latest_status.c.NormalStatus == sel_status)
    
    if mot:
        filters.append(RFT_Shipment.ModeOfTransport == mot)        

    # planned status filter (two cases)
    if sel_entity == "Planed DTC Delivery":
        filters.append(latest_plan_status.c.PlannedStatus == "Planed DTC Delivery")
    elif sel_entity == "Planed-WH":
        filters.append(
            and_(
                latest_plan_status.c.PlannedStatus.like("Planed%"),
                latest_plan_status.c.PlannedStatus != "Planed DTC Delivery"
            )
        )
    elif sel_entity != 'Planed-All':
        filters.append(
            latest_plan_status.c.PlannedStatus == sel_entity
        )

    q = q.filter(*filters).distinct().order_by(RFT_Container.ContainerID)

    # 6️⃣ Build result objects
    containers = []
    for cont, brand, bl, ship_no, normal_status, planned_status in q.all():
        cont.Brand = brand
        cont.BL = bl
        cont.ShipmentNumber = ship_no
        cont.ContainerLevelStatus = normal_status  # latest container status
        cont.PlannedLevelStatus = planned_status   # latest planned status
        containers.append(cont)

    # --- 5) build your columns metadata exactly as before ---
    friendly = {
        "Brand":                  "Brand",
        "BL":                     "BL #",
        "ShipmentNumber":         "Shipment No.",
        "ContainerNumber":        "Container No.",
        # "ContainerType":          "Type",
        "ContainerLevelStatus":   "Status",
        "ATAOrigin":              "Loading Time",
        "ATDOrigin":              "ATD Origin",
        "ATADP":                  "ATA Dest. Port",
        "ATDDPort":               "ATD Release- Dport:",
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
        
    # Adding Brand
    if not any(c["name"]=="Brand" for c in columns):
        columns.insert(
        # index - before ContainerNumber:
        next(i for i,c in enumerate(columns) if c["name"]=="ShipmentNumber")-14,
        {
            "name":         "Brand",
            "label":        "Brand",
            "type":         "String",
            "filter_type":  "text"
            # "options":      all_status
        }
    )
        
    # Adding BL
    if not any(c["name"]=="BL" for c in columns):
        columns.insert(
        # index - before ContainerNumber:
        next(i for i,c in enumerate(columns) if c["name"]=="ShipmentNumber")+1,
        {
            "name":         "BL",
            "label":        "BL#",
            "type":         "String",
            "filter_type":  "text"
            # "options":      all_status
        }
    )
    

    return render_template(
      "update_containers.html",
      rows             = containers,
      columns          = columns,
      all_status       = all_status,
      sel_status       = sel_status,
      containers_count = len(containers)
    )




@bp.route("/inTransitDetails", methods=["GET", "POST"])
def inTransitDetails():
    # --- A) open_qty & total_value per Article/Brand from PO lines ---
    open_subq = (
        model.query(
            RFT_PurchaseOrderLine.Article.label("article"),
            RFT_PurchaseOrder.Brand.label("brand"),
            func.sum(RFT_PurchaseOrderLine.Qty).label("open_qty"),
            # assume UnitPrice on PO line; adjust field name if different
            func.sum(
                RFT_PurchaseOrderLine.TotalValue
            ).label("total_value")
        )
        .join(
            RFT_PurchaseOrder,
            RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID
        )
        .group_by(
            RFT_PurchaseOrderLine.Article,
            RFT_PurchaseOrder.Brand
        )
        .subquery()
    )

    # --- B) find latest status per container ---
    H1 = aliased(RFT_StatusHistory)
    latest_cont = (
        model.query(
            H1.EntityID.label("ContainerID"),
            func.max(H1.StatusDate).label("MaxDate")
        )
        .filter(H1.EntityType == literal("Container"))
        .group_by(H1.EntityID)
        .subquery()
    )
    H2 = aliased(RFT_StatusHistory)
    cont_status = (
        model.query(
            H2.EntityID.label("ContainerID"),
            H2.Status.label("status")
        )
        .join(
            latest_cont,
            and_(
                H2.EntityID   == latest_cont.c.ContainerID,
                H2.StatusDate == latest_cont.c.MaxDate,
                H2.EntityType == literal("Container")
            )
        )
        .subquery()
    )

    # --- C) intransit_qty per Article/Brand from non-delivered containers ---
    intransit_subq = (
        model.query(
            RFT_PurchaseOrderLine.Article.label("article"),
            RFT_PurchaseOrder.Brand.label("brand"),
            func.coalesce(func.sum(RFT_ContainerLine.QtyInContainer), 0)
                .label("intransit_qty")
        )
        .join(
            RFT_ShipmentPOLine,
            RFT_ContainerLine.ShipmentPOLineID == RFT_ShipmentPOLine.ShipmentPOLineID
        )
        .join(
            RFT_Container,
            RFT_ContainerLine.ContainerID == RFT_Container.ContainerID
        )
        .join(
            cont_status,
            cont_status.c.ContainerID == RFT_Container.ContainerID
        )
        # only count containers *not* yet delivered
        .filter(~cont_status.c.status.in_(DELIVERED_STATUSES))
        .join(
            RFT_PurchaseOrderLine,
            RFT_ShipmentPOLine.POLineID == RFT_PurchaseOrderLine.POLineID
        )
        .join(
            RFT_PurchaseOrder,
            RFT_PurchaseOrderLine.POID == RFT_PurchaseOrder.POID
        )
        .group_by(
            RFT_PurchaseOrderLine.Article,
            RFT_PurchaseOrder.Brand
        )
        .subquery()
    )

    # --- D) combine & compute balance ---
    summary = (
        model.query(
            open_subq.c.brand,
            open_subq.c.article,
            open_subq.c.open_qty,
            open_subq.c.total_value,
            func.coalesce(intransit_subq.c.intransit_qty, 0)
                .label("intransit_qty"),
            (
                open_subq.c.open_qty
                - func.coalesce(intransit_subq.c.intransit_qty, 0)
            ).label("balance_qty")
        )
        .outerjoin(
            intransit_subq,
            and_(
                open_subq.c.brand   == intransit_subq.c.brand,
                open_subq.c.article == intransit_subq.c.article
            )
        )
        .order_by(open_subq.c.brand, open_subq.c.article)
        .all()
    )

    # --- E) column metadata for template ---
    columns = [
        {"name":"brand",         "label":"Brand",         "filter_type":"text"},
        {"name":"article",       "label":"Article",       "filter_type":"text"},
        {"name":"open_qty",      "label":"Open Qty",      "filter_type":"numeric"},
        {"name":"intransit_qty", "label":"In-Transit Qty", "filter_type":"numeric"},
        {"name":"balance_qty",   "label":"Balance Qty",    "filter_type":"numeric"},
        {"name":"total_value",   "label":"Total Value",    "filter_type":"numeric"}
    ]

    return render_template(
        "inTransitDetails.html",
        summary=summary,
        columns=columns
    )

@bp.route("/coastAnalysis", methods=["GET", "POST"])
def coastAnalysis():
    export_brands = request.form.getlist("export_brands")
    
    # 1) Load all shipments that have a ShipmentNumber
    shipments = model.query(RFT_Shipment)

    if export_brands:
        shipments = (
            shipments
            .join(RFT_Shipment.po_lines)
            .join(RFT_ShipmentPOLine.po_line)
            .join(RFT_PurchaseOrderLine.purchase_order)
            .filter(RFT_PurchaseOrder.Brand.in_(export_brands))
        )

    shipments = shipments.options(
        joinedload(RFT_Shipment.po_lines)
        .joinedload(RFT_ShipmentPOLine.po_line)
        .joinedload(RFT_PurchaseOrderLine.purchase_order)
    )
    
    shipments = shipments.all()
    
    # 2) roll up per shipment
    summary_map = {}
    for s in shipments:
        sid = s.ShipmentID
        if sid not in summary_map:
            summary_map[sid] = {
                "shipment_id":       sid,
                "shipment_number":   s.ShipmentNumber,
                "bill_of_lading":    s.BLNumber,
                "port_of_loading":   s.OriginPort,
                "port_of_delivery":  s.POD,
                "brands":            set(),
                "po_numbers":        set(),
                "total_qty_shipped": 0,
                "total_value_shipped": 0,
                "container_ids":     set(),
                "invoice_total":     Decimal('0'),
                "freight_cost":      s.FreightCost or Decimal('0'),
                "custom_duties":     s.CustomDuties or Decimal('0'),
                "saber_saddad":      s.SaberSADDAD or Decimal('0'),
                "penalties":         s.Penalties or Decimal('0'),
                "demurrage_charges": s.DemurrageCharges or Decimal('0'),
                "others":            s.OtherCharges or Decimal('0'),
                "DO_Port_Charges":   s.DO_Port_Charges or Decimal('0'),
                "ClearanceTransportCharges": s.ClearanceTransportCharges or Decimal('0'),
                "InspectionCharges": s.InspectionCharges or Decimal('0'),
                "MAWANICharges":     s.MAWANICharges or Decimal('0'),
                "YardCharges":       s.YardCharges or Decimal('0'),
            }

        grp = summary_map[sid]

        for sp_line in s.po_lines:
            po_line = sp_line.po_line
            if po_line:
                po = po_line.purchase_order
                if po:
                    grp["brands"].add(po.Brand)
                    grp["po_numbers"].add(po.PONumber)
                grp["total_qty_shipped"] += sp_line.QtyShipped or 0
                grp["total_value_shipped"] += po_line.TotalValue or 0

        for container in s.containers:
            grp["container_ids"].add(container.ContainerID)

        # Sum invoice values
        grp["invoice_total"] += sum(inv.InvoiceValue for inv in s.invoices if inv.InvoiceValue)

    # 3) finalize each summary record
    summary = []
    for grp in summary_map.values():
        grp["brands"] = ", ".join(sorted(grp["brands"]))
        grp["po_numbers"] = ", ".join(sorted(grp["po_numbers"]))
        grp["container_count"] = len(grp["container_ids"])

        grp["total_expense"] = (
            grp["freight_cost"] 
            + grp["custom_duties"] 
            + grp["penalties"] 
            + grp["demurrage_charges"] 
            + grp["saber_saddad"] 
            + grp["others"] 
            + grp["DO_Port_Charges"] 
            + grp["ClearanceTransportCharges"] 
            + grp["InspectionCharges"] 
            + grp["MAWANICharges"]
        )

        grp["cost_per_Container"] = round(grp["total_expense"] / grp["container_count"], 2) if grp["container_count"] else 0
        summary.append(grp)

    # 4) build column metadata manually
    sample = summary[0] if summary else {}
    cols = []
    specs = [
        ("shipment_number",   "Shipment #"),
        ("bill_of_lading",    "B/L"),
        ("po_numbers",        "PO Numbers"),
        ("brands",            "Brand"),
        ("port_of_loading",   "Loading Port"),
        ("port_of_delivery",  "Release Port"),
        ("total_qty_shipped", "Total Qty Shipped"),
        ("total_value_shipped","Total Value Shipped"),
        ("container_count",   "Num. Containers"),
        ("cost_per_Container","Cost Per.Container"),
        ("total_expense",     "Total Expensees"),
        ("freight_cost",      "Freight Cost"),
        ("custom_duties",     "Custom Duties"),
        ("saber_saddad",      "Saber SADDAD"),
        ("penalties",         "Penalties"),
        ("demurrage_charges", "Demurrage Charges"),
        ("others",            "Others"),
        ("invoice_total",     "Total supplier Invoices"),
        ("DO_Port_Charges",   "DO_Port_Charges"),
        ("ClearanceTransportCharges", "ClearanceTransportCharges"),
        ("InspectionCharges", "InspectionCharges"),
        ("MAWANICharges",     "MAWANICharges"),
        ("YardCharges",       "YardCharges")
    ]

    if request.method == 'POST' and 'export' in request.form:
        df = pd.DataFrame(summary)
        df = df[[key for key, _ in specs]]
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name="Shipment Costs")
            workbook  = writer.book
            worksheet = writer.sheets["Shipment Costs"]

            header_format = workbook.add_format({
                'bold': True, 'bg_color': '#D9E1F2',
                'border': 1, 'text_wrap': True, 'align': 'center'
            })
            for col_num, (key, label) in enumerate(specs):
                worksheet.write(0, col_num, label, header_format)

            worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)

            for i, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, max_len)

            cell_format = workbook.add_format({'border': 1})
            worksheet.conditional_format(
                1, 0, len(df), len(df.columns) - 1,
                {'type': 'no_errors', 'format': cell_format}
            )

            worksheet.freeze_panes(1, 0)

        output.seek(0)
        
        now = datetime.now()
        formatted_time = now.strftime("[%#d %B] [%I;%M] %p")  # on Windows  # e.g., "7 July 12:10 PM"
        # brands_str = " & ".join(export_brands)
        file_name = f"Cost_Analysis_Report_{formatted_time}.xlsx"
        
        return send_file(output, download_name=file_name, as_attachment=True)
    
    distinct = defaultdict(set)
    for row in summary:
        for key,_ in specs:
            distinct[key].add(row[key])

    for key,label in specs:
        dtype = type(sample.get(key)).__name__
        col = {"name":key, "label":label}
        vals = {v for v in distinct[key] if v not in (None,"")}
        if dtype=="str" and 1 < len(vals) <= 50:
            col["filter_type"] = "select"
            col["options"]     = sorted(vals)
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


@bp.route("/shipment/<int:shipment_id>") #######################################TODO
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
            df['Netprice'] = df['Netprice'].astype(Float) # Float
            df['QtyToBeDelivered'] = df['QtyToBeDelivered'].astype(int)
            df['ValueToBeDelivered'] = df['ValueToBeDelivered'].astype(Float)
            df['DocDate'] = pd.to_datetime(df['DocDate'], format=date_format, errors='coerce')
            df['UploadBatch'] = str(uuid.uuid4())
            df['UploadedBy'] = session.get('username', 'system')

            # Fix missing articles based on ShortText
            def generate_article_name(row):
                if row['Article'].strip() and row['Article'].lower() != 'nan':
                    return row['Article']
                short = row['ShortText'].strip() if isinstance(row['ShortText'], str) else ""
                if short:
                    return f"POP-{short[:8]}"
                else:
                    return f"POP-{random.randint(100000, 999999)}"

            df['Article'] = df.apply(generate_article_name, axis=1)
            
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

    # print(rows)
    # print(columns)
    
    return render_template("articleExpenseReport.html",
                           rows=rows,
                           columns=columns,
                           sel_brands=sel_brands,
                           sel_start=sel_start,
                           sel_end=sel_end)

@bp.route("/po_report", methods=["GET","POST"])
def po_report_page():
    if request.method == 'POST' and 'export' in request.form:
        shp_ids = request.form.getlist('export') #TODO
        return export_po_report()
    
    # read any filters from the form
    shipments = request.values.getlist("shipment") or None

    # 1) get the DataFrame
    df = build_po_report_df(shipments)

    # 2) turn into rows+columns for DataTables
    rows, columns = build_po_columns(df)

    # 3) pass down as JSON
    return render_template(
      "poReport.html",
      rows    = rows,
      columns = columns,
      sel_shipments= shipments
    )

@bp.route('/settings', methods=['GET','POST']) ###########################TODO
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