from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from database import db, User, Wish
import os

try:
    from anthropic import Anthropic
    claude = Anthropic(api_key=os.environ.get('CLAUDE_API_KEY', ''))
    HAS_CLAUDE = True
except:
    HAS_CLAUDE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///wishlist.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'برای ادامه ابتدا وارد شو.'

WISH_PRICE = 50000  # تومان

# ── کلمات ممنوع (فیلتر ساده) ──────────────────────────
BANNED_WORDS = [
    'مواد مخدر', 'هروئین', 'کوکائین', 'شیشه', 'ماری جوانا', 'حشیش',
    'اسلحه', 'تفنگ', 'کلاشنیکف', 'چاقو', 'قمه', 'بمب', 'انفجار',
    'سیگار', 'دخانیات', 'الکل', 'مشروب', 'عرق',
    'فحش', 'توهین', 'تجاوز', 'قتل', 'خودکشی',
]

def is_content_ok(title, description):
    text = (title + ' ' + description).lower()
    for word in BANNED_WORDS:
        if word in text:
            return False, word

    # اگه Claude API داریم، اون هم چک کنه
    if HAS_CLAUDE and os.environ.get('CLAUDE_API_KEY'):
        try:
            msg = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=50,
                messages=[{
                    "role": "user",
                    "content": f"""این آرزو رو بررسی کن. اگه محتوای نامناسب، غیرقانونی یا مضر داره فقط بنویس: REJECT
در غیر اینصورت فقط بنویس: OK

عنوان: {title}
توضیح: {description}"""
                }]
            )
            result = msg.content[0].text.strip()
            if result == "REJECT":
                return False, "هوش مصنوعی"
        except:
            pass

    return True, None

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── صفحه اصلی ─────────────────────────────────────────
@app.route('/')
def index():
    wishes = Wish.query.filter_by(status='approved').order_by(Wish.created_at.desc()).limit(30).all()
    fulfilled = Wish.query.filter_by(status='fulfilled').order_by(Wish.created_at.desc()).limit(6).all()
    total = Wish.query.count()
    total_fulfilled = Wish.query.filter_by(status='fulfilled').count()
    return render_template('index.html',
        wishes=wishes,
        fulfilled=fulfilled,
        total=total,
        total_fulfilled=total_fulfilled
    )

# ── ثبت‌نام ────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        if len(password) < 6:
            flash('رمز عبور باید حداقل ۶ کاراکتر باشه.', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('این ایمیل قبلاً ثبت شده.', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('این نام کاربری قبلاً استفاده شده.', 'error')
            return redirect(url_for('register'))
        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('خوش اومدی! 🎉', 'success')
        return redirect(url_for('dashboard'))
    return render_template('register.html')

# ── ورود ───────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('ایمیل یا رمز اشتباهه.', 'error')
    return render_template('login.html')

# ── خروج ───────────────────────────────────────────────
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# ── داشبورد ────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    my_wishes = Wish.query.filter_by(user_id=current_user.id).order_by(Wish.created_at.desc()).all()
    return render_template('dashboard.html', wishes=my_wishes)

# ── ثبت آرزو ───────────────────────────────────────────
@app.route('/wish/new', methods=['GET', 'POST'])
@login_required
def new_wish():
    if request.method == 'POST':
        title = request.form['title'].strip()
        description = request.form['description'].strip()

        if len(title) < 5:
            flash('عنوان خیلی کوتاهه. بیشتر بنویس.', 'error')
            return redirect(url_for('new_wish'))
        if len(description) < 20:
            flash('توضیحات بیشتری بنویس (حداقل ۲۰ کاراکتر).', 'error')
            return redirect(url_for('new_wish'))

        ok, bad_word = is_content_ok(title, description)
        if not ok:
            flash(f'❌ آرزوی شما با قوانین سایت مطابقت ندارد.', 'error')
            return redirect(url_for('new_wish'))

        if current_user.balance < WISH_PRICE:
            flash(f'موجودی کافی نیست. باید {WISH_PRICE:,} تومان موجودی داشته باشی.', 'error')
            return redirect(url_for('topup'))

        current_user.balance -= WISH_PRICE
        wish = Wish(title=title, description=description, user_id=current_user.id)
        db.session.add(wish)
        db.session.commit()
        flash('✅ آرزوت ثبت شد! بعد از تایید ادمین نمایش داده میشه.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('new_wish.html', price=WISH_PRICE, balance=current_user.balance)

# ── گزارش تخلف ─────────────────────────────────────────
@app.route('/wish/<int:wish_id>/report', methods=['POST'])
@login_required
def report_wish(wish_id):
    wish = Wish.query.get_or_404(wish_id)
    wish.is_reported = True
    db.session.commit()
    return jsonify({'ok': True})

# ── شارژ موجودی ────────────────────────────────────────
@app.route('/topup')
@login_required
def topup():
    card = os.environ.get('CARD_NUMBER', '---- ---- ---- ----')
    cardholder = os.environ.get('CARD_HOLDER', 'نام صاحب حساب')
    support_email = os.environ.get('SUPPORT_EMAIL', 'support@wishlist.ir')
    return render_template('topup.html', card=card, cardholder=cardholder, support_email=support_email)

# ── پنل ادمین ──────────────────────────────────────────
@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    pending = Wish.query.filter_by(status='pending').order_by(Wish.created_at.asc()).all()
    reported = Wish.query.filter_by(is_reported=True).filter(Wish.status != 'rejected').all()
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin.html', pending=pending, reported=reported, all_users=all_users)

@app.route('/admin/wish/<int:wish_id>/<action>')
@login_required
def admin_action(wish_id, action):
    if not current_user.is_admin:
        return redirect(url_for('index'))
    wish = Wish.query.get_or_404(wish_id)
    if action == 'approve':
        wish.status = 'approved'
        wish.is_reported = False
    elif action == 'reject':
        wish.status = 'rejected'
        wish.user.balance += WISH_PRICE  # پول برمیگرده
    elif action == 'fulfill':
        wish.status = 'fulfilled'
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:user_id>/charge', methods=['POST'])
@login_required
def admin_charge(user_id):
    if not current_user.is_admin:
        return redirect(url_for('index'))
    user = User.query.get_or_404(user_id)
    amount = int(request.form.get('amount', 0))
    if amount > 0:
        user.balance += amount
        db.session.commit()
        flash(f'✅ موجودی {user.username} به مقدار {amount:,} تومان شارژ شد.', 'success')
    return redirect(url_for('admin'))

# ── ساخت ادمین اول (فقط یه بار) ───────────────────────
@app.route('/setup')
def setup():
    if User.query.count() > 0:
        return '❌ قبلاً setup شده.'
    admin_user = User(
        username='admin',
        email='admin@wishlist.ir',
        password=generate_password_hash('admin1234'),
        is_admin=True,
        balance=999999999
    )
    db.session.add(admin_user)
    db.session.commit()
    return '✅ ادمین ساخته شد.<br>ایمیل: admin@wishlist.ir<br>رمز: admin1234<br><br>⚠️ بعد از ورود، رمزت رو عوض کن و این آدرس رو از app.py حذف کن!'

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
