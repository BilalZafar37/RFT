# blueprints/field_labels.py
from flask import Blueprint, render_template, request, redirect, flash, abort
from flask_login import login_required, current_user
# from extensions    import db
from models        import RFT_FieldLabels, model, Base, inspect

bp = Blueprint('field_labels', __name__, url_prefix='/admin/labels')

@bp.route('/<string:table_name>', methods=['GET','POST'])
@login_required
def manage_labels(table_name):
    # 1) load existing labels
    labels = (
        model
          .query(RFT_FieldLabels)
          .filter_by(TableName=table_name)
          .order_by(RFT_FieldLabels.FieldName)
          .all()
    )

    # 2) introspect the table to get all its columns
    mapper = Base.registry._class_registry.get(table_name)
    if not mapper:
        abort(404, f"No such table/class: {table_name}")
    table_cols = [c.name for c in inspect(mapper).columns]

    # 3) figure out which columns still lack a label
    labeled_fields = {lbl.FieldName for lbl in labels}
    available_fields = [f for f in table_cols if f not in labeled_fields]

    if request.method == 'POST':
        # … (deletion & updates as before) …

        # d) adding new label via select now
        new_fn = request.form.get("new_FieldName","").strip()
        new_lb = request.form.get("new_Label","").strip()
        if new_fn and new_lb:
            nl = RFT_FieldLabels(
                TableName  = table_name,
                FieldName  = new_fn,
                Label      = new_lb,
                CreatedBy  = current_user.username or 'system'
            )
            model.add(nl)
            model.commit()
            flash("New label added.", "success")
        return redirect(request.url)

    return render_template(
        'manage_labels.html',
        table_name      = table_name,
        labels          = labels,
        available_fields= available_fields
    )