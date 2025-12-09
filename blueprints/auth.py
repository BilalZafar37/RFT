from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, UserMixin, current_user
from extensions import login_manager
from models import Users, model, UserBrands, UserSites
from datetime import datetime, timedelta


bp = Blueprint('auth', __name__, url_prefix='/auth',
               template_folder='../templates/auth')



class User(UserMixin):
    def __init__(self, user_id, username, role, department, ip, email, Fullname, theme):
        self.id = user_id
        self.username = username
        self.role = role
        self.department = department
        self.ip = ip
        self.email = email
        self.fullname = Fullname
        self.theme = theme


@login_manager.user_loader
def load_user(user_id):
    user = model.query(Users).get(user_id)
    if user:
        return User(user.UserID, user.Username, user.Role, user.Department, user.IP, user.Email, user.Fullname, user.theme)
    return None

@bp.route("/sign-in", methods=["GET", "POST"])  # SIGN-IN /LOGIN PAGE
@bp.route("/login", methods=["GET", "POST"])
def login():
    if "username" in session:
        session.pop("username")
        logout_user()
    if request.method == "POST":
        if "login_attempts" not in session:
            session["login_attempts"] = 0
            session["lockout_time"] = None  # No lockout initially

        # Check if the user is locked out
        if session["login_attempts"] >= 10:
            # Check if lockout time has passed
            if session["lockout_time"]:
                lockout_end = datetime.strptime(
                    session["lockout_time"], "%Y-%m-%d %H:%M:%S"
                ) + timedelta(minutes=5)
                if datetime.now() > lockout_end:
                    # Reset attempts after 5 minutes
                    session["login_attempts"] = 0
                    session["lockout_time"] = None
                else:
                    return redirect(url_for("no_access"))  # Still locked out
            else:
                # Set lockout time if not already set
                session["lockout_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return redirect(url_for("no_access"))  # Initial lockout

        user = request.form["username"]
        password = request.form["password"]
        user_data = (
            model.query(Users).filter_by(Username=user, Password=password).first()
        )
        if user_data:
            # If credentials are valid, reset attempts
            session["login_attempts"] = 0

            # Create User object and log the user in
            user_obj = User(
                user_data.UserID,
                user_data.Username,
                user_data.Role,
                user_data.Department,
                user_data.IP,
                user_data.Email,
                user_data.Fullname,
                user_data.theme,
            )
            login_user(user_obj)

            session["role"] = user_data.Role
            session["username"] = user
            session["user_id"] = user_data.UserID
            session["department"] = user_data.Department

            # Get current access for each user
            user_site_access = (
                model.query(UserSites).filter_by(UserID=current_user.id).all()
            )
            user_site_access = [u.site.SiteName for u in user_site_access]
            session["user_site_access"] = user_site_access
            
            user_brand_access = (
                model.query(UserBrands).filter_by(UserID=current_user.id).all()
            )
            user_brand_access = [u.brand.BrandName for u in user_brand_access]
            session["user_brand_access"] = user_brand_access

            if user_brand_access:
                return redirect(url_for("dashboard.dashboard"))
            elif user_site_access:
                return redirect(url_for("orders_delivery_update"))
        else:
            # Increment attempt count
            session["login_attempts"] += 1

        return render_template("auth/sign-in.html", error="Invalid Credentials")
    return render_template("auth/sign-in.html")




# @app.before_request
# def require_login():
#     # Bypass login check for certain routes like the login or signup page
#     if request.endpoint not in ['static', 'login', 'signup', 'no_access'] and not current_user.is_authenticated:
#         return redirect(url_for('login'))
