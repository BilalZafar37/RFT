# app.py
from flask import Flask
from extensions import login_manager
from blueprints.main      import bp as main_bp
from blueprints.dropdowns import bp as dropdowns_bp
from blueprints.intervals import bp as intervals_bp
from blueprints.auth import bp as auth_bp
from blueprints.auth import current_user
from blueprints.field_labels import bp as labels_bp
from dashboard import bp as dashboard_bp
from models import *
import json


from config import Config


def create_app():
    app = Flask(__name__)
    # app.config.from_object('config.Config')  # or your config
    # app.secret_key = "AsauDaisFdoijaGosancKanc"
    app.config.from_object(Config)

    login_manager.init_app(app)

    # register blueprints
    app.register_blueprint(main_bp)        # /
    app.register_blueprint(dropdowns_bp)   # /admin/dropdowns
    app.register_blueprint(intervals_bp)   # /admin/intervals
    app.register_blueprint(auth_bp)        # /auth/login
    app.register_blueprint(labels_bp)      # /admin/labels/-->FreightTrackingView
    app.register_blueprint(dashboard_bp)   # /main/dashboard/-->FreightTrackingView
    
    
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
    
    return app



if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=8080, debug=True)
