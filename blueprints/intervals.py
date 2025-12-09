# blueprints/intervals.py
from flask import Blueprint, render_template, request, redirect, flash
from flask_login import login_required, current_user
# from extensions import db
from models     import RFT_IntervalConfig, FreightTrackingView, model, Date, DateTime
# from datetime import datetime, date

bp = Blueprint('intervals', __name__, url_prefix='/admin/intervals')

@bp.route('/', methods=['GET','POST'])
@login_required
def manage_intervals():
    configs = model.query(RFT_IntervalConfig).order_by(RFT_IntervalConfig.ID).all()

    # build a list of all date/datetime column names from your view
    date_fields = [
        col.name
        for col in FreightTrackingView.__table__.columns
        if isinstance(col.type, (Date, DateTime))
    ]
    # print("DEBUG - date_fields:", date_fields)
    
    if request.method == 'POST':
        # 1) Deletion?
        if delete_id := request.form.get('delete'):
            cfg = model.get(RFT_IntervalConfig, int(delete_id))
            if cfg:
                model.delete(cfg)
                model.commit()
                flash(f"Interval #{delete_id} deleted.", "warning")
            return redirect(request.url)

        # 2) Updates…
        for cfg in configs:
            name  = request.form.get(f"name_{cfg.ID}", cfg.IntervalName).strip()
            start = request.form.get(f"start_{cfg.ID}", cfg.StartField).strip()
            end   = request.form.get(f"end_{cfg.ID}",   cfg.EndField).strip()
            cfg.IntervalName = name
            cfg.StartField   = start
            cfg.EndField     = end
            cfg.UpdatedBy    = current_user.username or 'system'
            
        # handle new
        new_name  = request.form.get('new_name','').strip()
        new_start = request.form.get('new_start','').strip()
        new_end   = request.form.get('new_end','').strip()
        if new_name and new_start and new_end:
            nc = RFT_IntervalConfig(
                IntervalName=new_name,
                StartField=new_start,
                EndField=new_end,
                CreatedBy=current_user.username or 'system'
            )
            model.add(nc)
        model.commit()
        flash("Intervals saved.", "success")
        return redirect(request.url)

    return render_template('intervals/manage_intervals.html',
                           configs=configs,
                           date_fields=date_fields)
