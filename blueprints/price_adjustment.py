import os
import pandas as pd
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, send_file, flash, render_template
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from io import BytesIO

from models import model, RFT_PurchaseOrder, RFT_PurchaseOrderLine

bp = Blueprint('price_adjustment', __name__)

@bp.route('/price-adjustment', methods=['GET', 'POST'])
def price_adjustment():
    if request.method == 'POST' and "upload-file" in request.form:
        file = request.files.get('upload-file')
        if not file or not file.filename.endswith('.xlsx'):
            flash("Please upload a valid Excel (.xlsx) file.")
            return redirect(request.url)

        df = pd.read_excel(file)

        if not all(col in df.columns for col in ['PONumber', 'Article', 'Unit_Price']):
            flash("Excel must have columns: PONumber, Article, Unit_Price")
            return redirect(request.url)

        results = []
        for _, row in df.iterrows():
            po_number = str(row['PONumber']).strip()
            article = str(row['Article']).strip()
            new_unit = float(row['Unit_Price'])

            po = model.query(RFT_PurchaseOrder)\
                        .options(joinedload(RFT_PurchaseOrder.order_lines)).filter_by(PONumber=po_number).first()
            if not po:
                continue

            for line in po.order_lines:
                if line.Article == article:
                    old_unit = float(line.TotalValue) / line.Qty if line.Qty else 0
                    old_total = float(line.TotalValue)
                    new_total = new_unit * line.Qty
                    changed = "Changed" if round(old_total, 2) != round(new_total, 2) else "Unchanged"
                    
                    # Update only if changed
                    if changed == "Changed":
                        line.TotalValue = new_total
                        line.LastUpdatedBy = "price_adjustment_route"

                    results.append({
                        "Brand": po.Brand,
                        "PONumber": po_number,
                        "Article": article,
                        "Old_Unit_Value": round(old_unit, 2),
                        "Old_Total_Value": round(old_total, 2),
                        "New_Unit_Value": round(new_unit, 2),
                        "New_Total_Value": round(new_total, 2),
                        "Status": changed
                    })

        model.commit()

        # Create Excel
        result_df = pd.DataFrame(results)
        timestamp = datetime.now().strftime('%d-%m-%y %H-%M')
        filename = f"Price Adjustment {timestamp}.xlsx"
        output = BytesIO()
        result_df.to_excel(output, index=False)
        output.seek(0)

        return send_file(output, download_name=filename, as_attachment=True)

    if request.method == 'POST' and "download_template" in request.form:
        df = pd.DataFrame(columns=["PONumber", "Article", "Unit_Price"])
        stream = BytesIO()
        df.to_excel(stream, index=False)
        stream.seek(0)
        return send_file(
            stream,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="Price_Adjustment_Template.xlsx"
        )
    
    return render_template("price_adjustment.html")
