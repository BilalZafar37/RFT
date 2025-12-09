from flask import request, render_template, redirect, url_for, jsonify
from sqlalchemy import func, literal_column, cast, Numeric
from . import bp
from models import *
from utils import (
    get_distinct, get_distinct_format,
    compute_leadtime_by_brand, compute_cost_by_brand,
    compute_fulfillment_by_brand,
    compute_container_plan_stage_counts_grouped, compute_shipment_status_counts,
    compute_upcoming_eta, pivot_matrix_to_rows, compute_plan_status_by_brand,
    compute_pod_by_brand_only_delivered, compute_monthly_dtc_vs_total, CONTAINER_STAGES
)
All_HAP = ['ADMIRAL','BISSELL','GIBSON','HITACHI','PANASONIC', 'RUUD', 'UFESA']


@bp.route("/", methods=["GET", "POST"])
def dashboard():
    model = None
    model = Session()
    
    # 1) Get the universe of options
    all_brands  = get_distinct("Brand")
    all_months  = get_distinct_format(RFT_PurchaseOrder ,"PODate","yyyy-MM")
    all_shp_months = get_distinct_format(RFT_Shipment, "CreatedDate","yyyy-MM")
    all_cats    = get_distinct("CatName")
    
    
    # 2) Read exactly what the user picked (could be an empty list)
    raw_brands   = request.values.getlist("brands[]")    # e.g. ['BrandA','BrandB'] or []
    raw_months   = request.values.getlist("created_months[]")
    raw_shp_months   = request.values.getlist("shp_created_months[]")
    raw_cats     = request.values.getlist("categories[]")
    raw_pos      = request.values.get("filter_po")   # if this is a multi-select use getlist, otherwise single get()
    raw_shipment      = request.values.get("filter_shipment")
    
    # 3) For the UI: we’ll render exactly what they picked (Keeping selected and ALL saperate so they don’t see “everything selected”)
    sel_brands_display = raw_brands
    sel_months_display = raw_months
    sel_shp_months_display = raw_shp_months
    sel_cats_display   = raw_cats
    sel_pos_display    = raw_pos
    sel_shp_display    = raw_shipment
    
    # 4) Differentiate the values for the query and display and adding logic for ALL_HAP
    if 'All' in raw_brands and len(raw_brands)==1 :
        # All was selected
        sel_brands_query = all_brands
    elif 'ALL HAP' in raw_brands:
        sel_brands_query = All_HAP
        sel_brands_display = All_HAP
    elif len(raw_brands)>= 1:
        sel_brands_query = raw_brands
    else:
        sel_brands_query = all_brands
    
    
    if 'All' in raw_cats and len(raw_cats)==1 :
        # All was selected
        sel_cats_query = all_cats
    elif len(raw_cats)>=1: # Something is selected and its not ONLY "All"
        sel_cats_query = raw_cats
    else:
        sel_cats_query = all_cats
    
    if 'All' in raw_months and len(raw_months)==1 :
        sel_months_query = all_months
    elif len(raw_months)>=1:
        sel_months_query = raw_months
    else:
        sel_months_query = all_months
        
    if 'All' in raw_shp_months and len(raw_shp_months)==1 :
        sel_shp_months_query = all_shp_months
    elif len(raw_shp_months)>=1:
        sel_shp_months_query = raw_shp_months
    else:
        sel_shp_months_query = all_shp_months
    

    sel_po_query    = raw_pos  
    sel_shp_query   = raw_shipment  
    
    # — 2) lead‐time (uses only brands+months)
    intervals     = model.query(RFT_IntervalConfig).order_by(RFT_IntervalConfig.ID).all()
    iv_data       = [{"name":c.IntervalName,"start":c.StartField,"end":c.EndField} for c in intervals]
    lt_data       = compute_leadtime_by_brand(
        brands      =sel_brands_query,
        months      =sel_months_query,
        shp_months  = sel_shp_months_query # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
    )
    ##
    
    # — 3) cost (brands, months, categories)
    cost_data      = compute_cost_by_brand(
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )
    # derive totals/averages...
    total_expense    = sum(d["total_expense"]   for d in cost_data)
    total_shipments  = sum(d["num_shipments"]   for d in cost_data) or 1
    total_containers = sum(d["num_containers"]  for d in cost_data) or 1
    total_articles   = sum(d["num_articles"]    for d in cost_data) or 1
    avg_per_shipment  = round(total_expense/total_shipments, 2)
    avg_per_container = round(total_expense/total_containers,2)
    avg_per_article   = round(total_expense/total_articles,2)

    # — 4) fulfillment % by brand & by PO
    fulfill_brand = compute_fulfillment_by_brand(brands=sel_brands_query)
 
    ############################################# ─── SEA SHIP ─────────────────────────────────────────────────────────
    sea_matrix, sea_plan_groups = compute_container_plan_stage_counts_grouped(
        ["Planed"],
        mot="Sea",
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )

    wh_rows_sea  = pivot_matrix_to_rows(
        sea_matrix,
        plan_groups=sea_plan_groups,
        container_stages=CONTAINER_STAGES
    )

    sea_plan_groups = [
        {"plan_status": grp, "label": grp.replace("Planed ", "").replace("Delivery", "").strip()}
        for grp in sea_plan_groups
    ]
    
    ######################################### ─── LAND SHIP ────────────────────────────────────────────────────────────
    land_matrix, land_plan_groups = compute_container_plan_stage_counts_grouped(
        ["Planed"],
        mot="Land",
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )

    # 4) Build the WH rows 
    wh_rows_land = pivot_matrix_to_rows(
        land_matrix,
        plan_groups=land_plan_groups,
        container_stages=CONTAINER_STAGES
    )
    
    land_plan_groups = [
        {"plan_status": grp, "label": grp.replace("Planed ", "").replace("Delivery", "").strip()}
        for grp in land_plan_groups
    ]
    ########################################## ─── AIR SHIP ─────────────────────────────────────────────────────────────
    air_matrix, air_plan_groups = compute_container_plan_stage_counts_grouped(
        ["Planed"],
        mot="Air",
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )

    # 4) Build the WH rows 
    wh_rows_air = pivot_matrix_to_rows(
        air_matrix,
        plan_groups=air_plan_groups,
        container_stages=CONTAINER_STAGES
    )
    
    air_plan_groups = [
        {"plan_status": grp, "label": grp.replace("Planed ", "").replace("Delivery", "").strip()}
        for grp in air_plan_groups
    ]
    
    ########################################## END OF PLAN GROUP CASESE ##########################################
    
    ################################                          ################################ 
    ################################ Warehouse x Brands table ################################ 
    ################################                          ################################ 
    table_data, brand_cols = compute_plan_status_by_brand(
                    ["Planed"],
                    # mot="Land",
                    brands     = sel_brands_query,
                    months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
                    shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
                    categories = sel_cats_query, #list
                    sel_shp    = sel_shp_query, #str
                    sel_po     = sel_po_query #str
                )
    
    # Warehouse x Brands cheart
    pie_labels = [row[0] for row in table_data]      # Warehouse names
    pie_values = [row[-1] for row in table_data]
    
    ##################################              ################################ 
    ################################## POD x Brands ################################ 
    ##################################              ################################ 
    table_data2, brand_cols2 = compute_pod_by_brand_only_delivered(
                    brands     = sel_brands_query,
                    months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
                    shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
                    categories = sel_cats_query, #list
                    sel_shp    = sel_shp_query, #str
                    sel_po     = sel_po_query #str
                )
    
    # Warehouse x Brands cheart
    pie_labels2 = [row[0] for row in table_data2]      # Warehouse names
    pie_values2 = [row[-1] for row in table_data2]
    
    ##################################              ################################ 
    ################################## ATA-WH x Months ################################ 
    ##################################              ################################ 
    table_data3, brand_cols3 = compute_monthly_dtc_vs_total(
                    ["Planed"],
                    brands     = sel_brands_query,
                    months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
                    shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
                    categories = sel_cats_query, #list
                    sel_shp    = sel_shp_query, #str
                    sel_po     = sel_po_query #str
                )
    
    # Warehouse x Brands cheart
    pie_labels3 = [row[0] for row in table_data3]      # Warehouse names
    pie_values3 = [row[-1] for row in table_data3]
    
    # For shipment statuses 
    shipment_status_sea      = compute_shipment_status_counts(
        "Sea", 
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )
    shipment_status_land     = compute_shipment_status_counts(
        "Land",
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )
    shipment_status_air      = compute_shipment_status_counts(
        "Air",
        brands     = sel_brands_query,
        months     = sel_months_query, # RFT_PurchaseOrder.PODate  --> (yyyy-MM) formated list
        shp_months = sel_shp_months_query, # RFT_Shipment.CreatedDate --> (yyyy-MM) formated list
        categories = sel_cats_query, #list
        sel_shp    = sel_shp_query, #str
        sel_po     = sel_po_query #str
    )

    # For Upcomming ETA
    upcoming_eta_sea         = compute_upcoming_eta("Sea",   brands     = sel_brands_query, days_ahead  = 7)
    upcoming_eta_land        = compute_upcoming_eta("Land",  brands     = sel_brands_query, days_ahead  = 7)
    upcoming_eta_air         = compute_upcoming_eta("Air",   brands     = sel_brands_query, days_ahead  = 7)

    return render_template("dashboard/A-new_DASH.html",
        # lead‐time
        lt_intervals     = intervals,
        lt_intervals_data= iv_data,
        lt_lead_data     = lt_data,

        # cost
        cost_data        = cost_data,

        # fulfillment
        brand_data       = fulfill_brand,

        # Sea containers report (planed /wh )
        wh_rows_sea = wh_rows_sea,
        wh_plan_groups_sea = sea_plan_groups,
        
        # Land containers report (planed /wh )
        wh_rows_land = wh_rows_land,
        wh_plan_groups_land = land_plan_groups,
        
        # Air containers report (planed /wh )
        wh_rows_air = wh_rows_air,
        wh_plan_groups_air = air_plan_groups,
        
        # Brand x WH PIVOT
        table_data = table_data,
        brand_cols = brand_cols,
        
        # Warehouse x Brands chart
        pie_labels=pie_labels, 
        pie_values=pie_values,
        
        # POD x Brand
        table_data2 = table_data2,
        brand_cols2 = brand_cols2,
        
        # POD x Brands chart
        pie_labels2=pie_labels2, 
        pie_values2=pie_values2,
        
        # ATA-WH x Month
        table_data3 = table_data3,
        brand_cols3 = brand_cols3,
        
        # ATA-WH x Month chart
        pie_labels3=pie_labels3, 
        pie_values3=pie_values3,
        
        # Shipment Level Statuses
        # Sea # Land # Air
        shipment_status_report_sea  = shipment_status_sea,
        shipment_status_report_land = shipment_status_land,
        shipment_status_report_air  = shipment_status_air,
        
        # ETA UPCOMMING
        upcoming_eta_sea          = upcoming_eta_sea,
        upcoming_eta_land         = upcoming_eta_land,
        upcoming_eta_air          = upcoming_eta_air,

        # re‐inject “universal” + current selections
        all_brands        = all_brands,
        sel_brands        = sel_brands_display,
        # 
        all_created_months= all_months,
        sel_months        = sel_months_display,
        # 
        all_categories    = all_cats,
        sel_categories    = sel_cats_display,
        # 
        all_shp_months    =all_shp_months,
        sel_shp_months    =sel_shp_months_display,
        # 
        filter_shipment   = sel_shp_display, # Actual shipmentNumber filter
        filter_po         = sel_pos_display, # PONumber Filter
        # 
        
        avg_per_shipment  = avg_per_shipment,
        avg_per_container = avg_per_container,
        avg_per_article   = avg_per_article,
    )
