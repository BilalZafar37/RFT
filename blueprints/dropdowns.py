# blueprints/dropdowns.py
from flask import Blueprint, render_template, request, redirect, flash, abort, session, url_for, send_file
from flask_login import login_required, current_user
from datetime import datetime
from models     import *
from io import BytesIO
import pandas as pd

bp = Blueprint('dropdowns', __name__, url_prefix='/admin/dropdowns')

dropdowns = {
    'incoterms':     {'model': RFT_IncoTerms,       'fields': ['code','description'], 'label':'Incoterms'},
    'statuses':      {'model': RFT_StatusManagement,'fields': ['Level','StatusName'], 'label':'statuses'},
    'modes':         {'model': RFT_ModeOfTransport, 'fields': ['mode'],               'label':'Modes of Transport'},
    'agents':        {'model': RFT_CustomAgents,    'fields': ['agent_name'],         'label':'Custom Agents'},
    'origins':       {'model': RFT_OriginPorts,     'fields': ['port_name'],          'label':'Origin Ports'},
    'destinations':  {'model': RFT_DestinationPorts,'fields': ['port_name'],          'label':'Destination Ports'},
    'lines':         {'model': RFT_ShipingLines,    'fields': ['ShipingLineName'],    'label':'Shipping Lines'},
    # NEW
    'brand_types':         {'model': RFT_BrandTypes,    'fields': ['BrandType', 'BrandName'],   'label':'Brand_types'},
    'cargo_types':         {'model': RFT_CargoTypes,    'fields': ['Type'],                     'label':'Cargo_types'},
    'weight':              {'model': RFT_ArticleWeight, 'fields': ['Article', 'WeightKG'],      'label':'Weight'},
}
@bp.route('/<string:table>', methods=['GET','POST'])
def manage_dropdown(table):
    cfg = dropdowns.get(table)
    if not cfg:
        abort(404)

    Model  = cfg['model']
    fields = cfg['fields']
    label  = cfg['label']

    if request.method == 'POST':
        # --- 1) Deletion?
        if delete_id := request.form.get('delete'):
            rec = model.get(Model, int(delete_id))
            if rec:
                model.delete(rec)
                model.commit()
                flash(f"{label} #{delete_id} deleted.", 'warning')
            return redirect(request.url)

        # 2) Insert?
        if request.form.get('add_new'):
            first_new_val = request.form.get(f'new_{fields[0]}', '').strip()
            if not first_new_val:
                flash(f"'{fields[0]}' cannot be empty.", 'danger')
                return redirect(request.url)

            new_data = {}
            for field in fields:
                v = request.form.get(f'new_{field}', '').strip()
                if not v:
                    flash(f"'{field}' cannot be empty.", 'danger')
                    return redirect(request.url)    
                new_data[field] = v

            new_rec = Model(**new_data)
            new_rec.CreatedBy = current_user.username or 'system'
            
            model.add(new_rec)
            model.commit()
            
            flash(f"New {label} added.", 'success')
            
            return redirect(request.url)      # <- early return

        # --- 2) Updates? ---
        did_update = False
        for key, val in request.form.items():
            if key.startswith('update_'):
                prefix, idstr = key.rsplit('_', 1)
                field_name    = prefix[len('update_'):]
                record_id     = int(idstr)

                rec = model.get(Model, record_id)
                if rec:
                    setattr(rec, field_name, val.strip())
                    rec.UpdatedBy = current_user.username or 'system'
                    rec.UpdatedAt = func.now()
                    did_update = True

        if did_update:
            model.commit()
            flash(f"{label} updated.", 'success')
            return redirect(request.url)

        
        return redirect(request.url)

    # GET — render table
    try:
        items = model.query(Model).order_by(Model.id).all()
    except:
        items = model.query(Model).order_by(Model.ID).all()
    return render_template(
        'dropdowns/manage_dropdown.html',
        table  = table,
        label  = label,
        fields = fields,
        items  = items,
    )

# Helper function: if value is NaN, return None; otherwise, cast to str.
def safe_str(val):
    if pd.isnull(val):
        return None
    return str(val)
@bp.route('/download_template') #categories excel template
def download_template():
    # Define the columns matching your table
    columns = ['Brand', 'CatCode', 'CatName', 'CatDesc', 'SubCat']
    
    # Create an empty DataFrame with only header row
    df = pd.DataFrame(columns=columns)
    
    # Create an in-memory output file for the Excel file
    output = BytesIO()
    
    # Write the DataFrame to the output (Excel format)
    # You can change the engine if needed (default is 'xlsxwriter' or 'openpyxl')
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template')
    
    # Reset the pointer to the beginning of the stream
    output.seek(0)
    
    # Return the file as an attachment so the browser prompts for download.
    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     download_name="categories_template.xlsx",
                     as_attachment=True)


@bp.route("/categories", methods=["GET", "POST"])
def manage_categories():
    if request.method == "POST":
        action = request.form.get("action", "").strip().lower()
        try:
            if action == "edit":
                # Editing an existing category
                cat_id = request.form.get("ID")
                if not cat_id:
                    flash("No category ID provided for edit.", "warning")
                    return redirect(url_for("dropdowns.manage_categories"))
                category = model.query(RFT_CategoriesMappingMain).filter(RFT_CategoriesMappingMain.ID == int(cat_id)).first()
                if category:
                    # category.Brand = request.form.get("Brand", category.Brand)
                    category.CatCode = request.form.get("CatCode", category.CatCode)
                    category.CatName = request.form.get("CatName", category.CatName)
                    category.CatDesc = request.form.get("CatDesc", category.CatDesc)
                    category.SubCat = request.form.get("SubCat", category.SubCat)
                    category.UpdatedBy = request.form.get("UpdatedBy", category.UpdatedBy)
                    category.UpdatedAt = func.now()
                else:
                    flash("Category not found for edit.", "warning")
            
            elif action == "delete":
                # Deleting an existing category
                cat_id = request.form.get("ID")
                if not cat_id:
                    flash("No category ID provided for delete.", "warning")
                    return redirect(url_for("dropdowns.manage_categories"))
                category = model.query(RFT_CategoriesMappingMain).filter(RFT_CategoriesMappingMain.ID == int(cat_id)).first()
                if category:
                    model.delete(category)
                else:
                    flash("Category not found for deletion.", "warning")
            
            elif action == "add":
                # Manually adding a new category
                new_category = RFT_CategoriesMappingMain(
                    # Brand = request.form.get("Brand"),
                    CatCode = request.form.get("CatCode"),
                    CatName = request.form.get("CatName"),
                    CatDesc = request.form.get("CatDesc"),
                    SubCat = request.form.get("SubCat"),
                    UpdatedBy = session.get('username', 'system'),
                    UpdatedAt = func.now()
                )
                model.add(new_category)
            
            elif action == "upload":
                # Upload an Excel file containing one or more records.
                file = request.files.get("file")
                if file is None or file.filename == "":
                    flash("No file selected.", "warning")
                    return redirect(url_for("dropdowns.manage_categories"))
                try:
                    df = pd.read_excel(file)
                except Exception as e:
                    flash("Error reading file: " + str(e), "danger")
                    return redirect(url_for("dropdowns.manage_categories"))
                
                expected_columns = {"Brand", "CatCode", "CatName", "CatDesc", "SubCat"}
                if not expected_columns.issubset(set(df.columns)):
                    flash("Excel file is missing one or more required columns.", "danger")
                    return redirect(url_for("dropdowns.manage_categories"))
                
                # # Convert UpdatedAt to datetime if needed.
                # if df["UpdatedAt"].dtype == "object":
                #     df["UpdatedAt"] = pd.to_datetime(df["UpdatedAt"], errors='coerce')
                
                for index, row in df.iterrows():
                    new_rec = RFT_CategoriesMappingMain(
                        # Brand = safe_str(row["Brand"]),
                        CatCode = safe_str(row["CatCode"]),
                        CatName = safe_str(row["CatName"]),
                        CatDesc = safe_str(row["CatDesc"]),
                        SubCat = safe_str(row["SubCat"]),
                        UpdatedBy = session.get('username', 'system'),
                        UpdatedAt = func.now()
                    )
                    model.add(new_rec)
            else:
                flash("No valid action provided.", "warning")
                return redirect(url_for("dropdowns.manage_categories"))
            
            # Commit changes if no exceptions occur
            model.commit()
            flash("Action '" + action + "' completed successfully.", "success")
        except Exception as e:
            model.rollback()
            flash("Error during '" + action + "': " + str(e), "danger")
        return redirect(url_for("dropdowns.manage_categories"))
    else:
        # On GET, show all categories.
        categories = model.query(RFT_CategoriesMappingMain).order_by(RFT_CategoriesMappingMain.ID).all()
        return render_template("dropdowns/categories.html", categories=categories)