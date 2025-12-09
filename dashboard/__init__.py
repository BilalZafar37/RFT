from flask import Blueprint

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

from . import views_leadtime, views_cost, views_fulfillment, dashboard, views_supplier_per
