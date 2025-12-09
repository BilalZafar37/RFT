from flask import request, render_template, redirect, url_for, jsonify
from sqlalchemy import func, literal_column, cast, Numeric
from . import bp
from models import model, FreightTrackingView, RFT_IntervalConfig
from utils import (
    get_distinct, get_distinct_format,
    compute_leadtime_by_brand, compute_fulfillment_by_brand,
    compute_cost_by_brand,    compute_fulfillment_by_po, compute_container_plan_stage_counts,
    compute_shipment_status_counts, compute_upcoming_eta
)

@bp.route("/", methods=["GET", "POST"])
def dashboard():
    # 1) Lead-time chart data
    # load intervals
    intervals = model.query(RFT_IntervalConfig).order_by(RFT_IntervalConfig.ID).all()
    iv_data = [{"name":c.IntervalName,"start":c.StartField,"end":c.EndField}
               for c in intervals]

    # all possible brands/months
    all_lt_brands = get_distinct("Brand")
    all_lt_months = get_distinct_format("POCreatedDate","yyyy-MM")

    # read selected or default
    sel_lt_brands = request.values.getlist("lt_brands[]") or all_lt_brands
    sel_lt_months = request.values.getlist("lt_months") or all_lt_months
    

    # compute brand-wise lead-time
    lt_lead_data = compute_leadtime_by_brand(
        brands=sel_lt_brands, months=sel_lt_months
    )

    # —————————————
    # 2) Cost chart data

    all_cost_brands = get_distinct("Brand")
    all_cost_months = get_distinct_format("POCreatedDate","yyyy-MM")
    all_cost_cats   = get_distinct("CatName")

    sel_cost_brands = request.values.getlist("cost_brands[]")    or all_cost_brands
    sel_cost_months = request.values.getlist("cost_months[]")    or all_cost_months
    sel_cost_cats   = request.values.getlist("cost_categories[]") or all_cost_cats

    cost_data = compute_cost_by_brand(
        brands=sel_cost_brands,
        months=sel_cost_months,
        categories=sel_cost_cats
    )

    # 5) compute overall totals across all brands
    total_expense    = sum(d["total_expense"]   for d in cost_data)
    total_shipments  = sum(d["num_shipments"]   for d in cost_data)
    # print(total_shipments)
    total_containers = sum(d["num_containers"]  for d in cost_data)
    total_articles   = sum(d["num_articles"]    for d in cost_data)

    # 6) compute per‐X averages (guard against division by zero)
    avg_per_shipment  = (total_expense / total_shipments ) if total_shipments  else 0
    avg_per_container = (total_expense / total_containers) if total_containers else 0
    avg_per_article   = (total_expense / total_articles  ) if total_articles   else 0
    print(total_articles)
    print(avg_per_article)
    print(round(avg_per_article, 2))
    
    # fulfillment % chart
    # 1) all brands for multi‐select filter
    all_brands = get_distinct("Brand")
    all_pos = get_distinct("PONumber")
    
    sel_pos = request.values.getlist("po_list[]") or all_pos
    sel_brands = request.values.getlist("ful_brands[]") or all_brands

    #) compute brand‐level fulfillment %
    brand_data = compute_fulfillment_by_brand(brands=sel_brands)
    
    # 3) compute the percentages
    fulfill_data = compute_fulfillment_by_po(pos=sel_pos)
    
    # Container status report
    dtc_counts = compute_container_plan_stage_counts("Planed DTC Delivery")
    wh_counts  = compute_container_plan_stage_counts("Planed WH Delivery")
    
    # For shipment statues report
    shipment_status_report = compute_shipment_status_counts()
    
    # upcoming ETA report (7 days)
    upcoming_eta = compute_upcoming_eta(7)

    return render_template("dashboard/A-new_DASH.html",
        # lead-time context
        lt_intervals       = intervals,
        lt_intervals_data  = iv_data,
        lt_lead_data       = lt_lead_data,
        all_lt_brands      = all_lt_brands,
        all_lt_months      = all_lt_months,
        sel_lt_brands      = sel_lt_brands,
        sel_lt_months      = sel_lt_months,

        # cost context
        cost_data          = cost_data,
        all_cost_brands    = all_cost_brands,
        all_cost_months    = all_cost_months,
        all_cost_cats      = all_cost_cats,
        sel_cost_brands    = sel_cost_brands,
        sel_cost_months    = sel_cost_months,
        sel_cost_cats      = sel_cost_cats,
        
        # new summary metrics
        avg_per_shipment   = round(avg_per_shipment, 2),
        avg_per_container  = round(avg_per_container, 2),
        avg_per_article    = round(avg_per_article, 2),
        
        #fulfillment %
        all_brands = all_brands,
        sel_brands = sel_brands,
        brand_data = brand_data,
        # pass both your existing chart data *and*:
        fulfill_data = fulfill_data,
        all_pos      = all_pos,
        sel_pos      = sel_pos,
        
        # Containers report
        dtc_container_report = dtc_counts,
        wh_container_report  = wh_counts,
        
        # Shipment report
        shipment_status_report=shipment_status_report,
        
        # Upcomming ETA
        upcoming_eta = upcoming_eta
    )
