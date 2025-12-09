# app.py
from flask import Flask
from extensions import login_manager
from blueprints.main      import bp as main_bp
from blueprints.dropdowns import bp as dropdowns_bp
from blueprints.intervals import bp as intervals_bp
from blueprints.auth import bp as auth_bp
from blueprints.auth import current_user
from blueprints.field_labels import bp as labels_bp
from blueprints.price_adjustment import bp as price_adj_bp
from dashboard import bp as dashboard_bp
from models import *
import json
import logging
import re
from datetime import datetime, date
from config import Config


# def create_app():
app = Flask(__name__)

app.config.from_object(Config)

login_manager.init_app(app)

from werkzeug.serving import WSGIRequestHandler

# keep a reference to the original
_orig_log_request = WSGIRequestHandler.log_request

def log_request_no_static(self, code='-', size='-'):
    # self.path is the raw path + querystring, e.g. "/static/js/app.js?…"
    path = self.path.split('?', 1)[0]

    # skip any you don't want to see
    if path.startswith((
        '/static/',
        '/.well-known/',
        '/favicon.ico',
    )):
        return

    # otherwise fall back to the normal logger
    return _orig_log_request(self, code, size)

# install our little patch
WSGIRequestHandler.log_request = log_request_no_static


# register blueprints
app.register_blueprint(main_bp)        # /
app.register_blueprint(dropdowns_bp)   # /admin/dropdowns
app.register_blueprint(intervals_bp)   # /admin/intervals
app.register_blueprint(auth_bp)        # /auth/login
app.register_blueprint(labels_bp)      # /admin/labels/-->FreightTrackingView
app.register_blueprint(dashboard_bp)   # /main/dashboard/-->FreightTrackingView
app.register_blueprint(price_adj_bp)   # /price_adjustment

# Register  filters
@app.template_filter('attr')
def attr_filter(obj, name):
    return getattr(obj, name, '')

@app.template_filter('usd')
def usd(value, places=2):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ''
    return f"{num:,.{places}f} USD"

@app.template_filter('pretty_date')
def pretty_date(value):
    if not value:
        return ""
    # handle both datetime.datetime and datetime.date
    if isinstance(value, (datetime, date)):
        day   = value.day
        month = value.strftime('%b').lower()  # e.g. “Apr” → “apr”
        year  = value.year
        return f"{day}-{month}-{year}"

    # if it’s a string in ISO form, try to parse it
    if isinstance(value, str):
        try:
            # date.fromisoformat will work if it’s “YYYY-MM-DD”
            d = date.fromisoformat(value)
            return f"{d.day}-{d.strftime('%b').lower()}-{d.year}"
        except ValueError:
            pass

    # fallback: render as-is
    return value

def find_biyan_file(existing_files, sn, tag):
    prefix = f"{sn}_{tag}"
    for f in existing_files:
        if f.startswith(prefix):
            return f
    return None

def format_month(value):
    try:
        return datetime.strptime(value, "%Y-%m").strftime("%Y-%B").upper()
    except Exception:
        return value  # fallback to original if parsing fails

app.jinja_env.globals['find_file'] = find_biyan_file
app.jinja_env.filters['format_month'] = format_month




@app.context_processor
def inject_user_settings():
    # 1) load global defaults
    rows = model.query(RFT_Settings).filter(RFT_Settings.UserID.is_(None)).all()
    # 2) overlay any user‐specific ones
    if current_user.is_authenticated:
        rows += model.query(RFT_Settings).filter_by(UserID=current_user.username).all()

    cfg = {}
    for r in rows:
        try:
            # try parse JSON, else use string
            cfg[r.SettingKey] = json.loads(r.SettingValue)
        except json.JSONDecodeError:
            cfg[r.SettingKey] = r.SettingValue
    return dict(settings=cfg)
    
    # return app



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
