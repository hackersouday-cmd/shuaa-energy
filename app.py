from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import sqlite3, os, secrets, io
from datetime import datetime, timedelta

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(BASE,'data.db')
app=Flask(__name__); app.secret_key='shuaa-energy-demo-secret-change-me'

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT, office_name TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY AUTOINCREMENT, queue_no INTEGER UNIQUE, name TEXT, car TEXT, origin TEXT, load TEXT, notes TEXT, office TEXT, tracking TEXT UNIQUE, registered_at TEXT, deadline TEXT, tonnage_received_at TEXT, loaded_at TEXT, status TEXT);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, driver_id INTEGER, old_queue INTEGER, action TEXT, details TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS load_batches(id INTEGER PRIMARY KEY AUTOINCREMENT, from_no INTEGER, to_no INTEGER, quantity INTEGER, created_at TEXT, user TEXT);
    ''')
    if not c.execute("SELECT 1 FROM settings WHERE key='tonnage_hours'").fetchone(): c.execute("INSERT INTO settings VALUES('tonnage_hours','6')")
    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone(): c.execute("INSERT INTO users(username,password,role,office_name) VALUES('admin','admin123','admin','المكتب الرئيسي')")
    for u,n in [('albashaer','البشائر'),('alaman','علامان'),('alfirdaws','الفردوس')]:
        if not c.execute('SELECT 1 FROM users WHERE username=?',(u,)).fetchone(): c.execute('INSERT INTO users(username,password,role,office_name) VALUES(?,?,?,?,?)'.replace('?,?,?,?,?','?,?,?,?'),(u,'1234','office',n))
    c.commit(); c.close()

def now(): return datetime.now()
def ts(d): return d.strftime('%Y-%m-%d %H:%M:%S')
def expire_pending():
    c=db(); rows=c.execute("SELECT * FROM drivers WHERE status='pending_tonnage'").fetchall(); changed=False
    for r in rows:
        if datetime.fromisoformat(r['deadline']) <= now():
            c.execute("UPDATE drivers SET status='expired' WHERE id=?",(r['id'],)); c.execute("INSERT INTO history(driver_id,old_queue,action,details,created_at) VALUES(?,?,?,?,?)",(r['id'],r['queue_no'],'انتهاء المهلة','لم يتم تسليم الطوناج خلال المهلة',ts(now()))); changed=True
    if changed:
        # compact active queue, preserving historical queue numbers in history
        active=c.execute("SELECT id FROM drivers WHERE status IN ('pending_tonnage','ready','waiting','loaded') ORDER BY queue_no").fetchall()
        for i,r in enumerate(active,1): c.execute('UPDATE drivers SET queue_no=? WHERE id=?',(i,r['id']))
    c.commit(); c.close()

def login_required(role=None):
    return session.get('role') and (role is None or session.get('role')==role)

@app.context_processor
def common():
    expire_pending(); c=db(); hours=c.execute("SELECT value FROM settings WHERE key='tonnage_hours'").fetchone()['value']; c.close(); return {'hours':hours,'user':session}

@app.route('/',methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=request.form['username'].strip(); p=request.form['password']
        c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=? AND active=1',(u,p)).fetchone(); c.close()
        if r:
            session.update(user_id=r['id'],username=r['username'],role=r['role'],office=r['office_name'])
            return redirect(url_for('dashboard'))
        flash('اسم المستخدم أو كلمة السر غير صحيحة','error')
    return render_template('login.html')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))
@app.route('/dashboard')
def dashboard():
    if not login_required(): return redirect(url_for('login'))
    c=db();
    if session['role']=='admin':
        rows=c.execute('SELECT * FROM drivers ORDER BY queue_no').fetchall(); offices=c.execute("SELECT * FROM users WHERE role='office' ORDER BY office_name").fetchall();
        stats={k:c.execute('SELECT COUNT(*) n FROM drivers WHERE status=?',(v,)).fetchone()['n'] for k,v in [('pending','pending_tonnage'),('ready','ready'),('loaded','loaded'),('expired','expired')]}; total=c.execute('SELECT COUNT(*) n FROM drivers WHERE status IN (\'pending_tonnage\',\'ready\',\'loaded\')').fetchone()['n']; c.close(); return render_template('admin.html',rows=rows,offices=offices,stats=stats,total=total)
    rows=c.execute('SELECT * FROM drivers WHERE office=? ORDER BY queue_no',(session['office'],)).fetchall(); c.close(); return render_template('office.html',rows=rows)
@app.route('/admin/drivers')
def admin_drivers():
    if not login_required('admin'): return redirect(url_for('login'))
    c=db();
    q=request.args.get('q','').strip(); status=request.args.get('status','').strip(); office=request.args.get('office','').strip()
    sql='SELECT * FROM drivers WHERE 1=1'; args=[]
    if q:
        sql += ' AND (name LIKE ? OR car LIKE ? OR CAST(queue_no AS TEXT) LIKE ?)'; like=f'%{q}%'; args += [like,like,like]
    if status:
        sql += ' AND status=?'; args.append(status)
    if office:
        sql += ' AND office=?'; args.append(office)
    sql += ' ORDER BY queue_no'
    rows=c.execute(sql,args).fetchall(); offices=c.execute("SELECT DISTINCT office FROM drivers WHERE office IS NOT NULL AND office<>'' ORDER BY office").fetchall(); c.close()
    return render_template('drivers.html',rows=rows,offices=offices,q=q,status=status,office=office)

@app.route('/driver/register',methods=['POST'])
def register_driver():
    if not login_required('office'): return redirect(url_for('login'))
    name=request.form['name'].strip(); car=request.form['car'].strip(); origin=request.form['origin'].strip(); load=request.form['load'].strip(); notes=request.form.get('notes','').strip()
    if not name or not car: flash('اسم السائق ورقم السيارة مطلوبان','error'); return redirect(url_for('dashboard'))
    c=db(); maxq=c.execute("SELECT COALESCE(MAX(queue_no),0) n FROM drivers").fetchone()['n']; q=maxq+1; tracking=secrets.token_urlsafe(6); h=int(c.execute("SELECT value FROM settings WHERE key='tonnage_hours'").fetchone()['value']); t=now(); deadline=t+timedelta(hours=h)
    c.execute('INSERT INTO drivers(queue_no,name,car,origin,load,notes,office,tracking,registered_at,deadline,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(q,name,car,origin,load,notes,session['office'],tracking,ts(t),ts(deadline),'pending_tonnage')); c.commit(); c.close(); flash(f'تم تسجيل السائق. رقم السِرّة الحالي: {q} | رمز متابعة السائق: {tracking}','ok'); return redirect(url_for('dashboard'))
@app.route('/admin/tonnage/<int:id>',methods=['POST'])
def tonnage(id):
    if not login_required('admin'): return redirect(url_for('login'))
    c=db(); c.execute("UPDATE drivers SET status='ready',tonnage_received_at=? WHERE id=? AND status='pending_tonnage'",(ts(now()),id)); c.commit(); c.close(); flash('تم تأكيد استلام الطوناج','ok'); return redirect(url_for('dashboard'))
@app.route('/admin/settings',methods=['POST'])
def settings():
    if not login_required('admin'): return redirect(url_for('login'))
    h=request.form.get('hours','6');
    try: h=max(1,min(72,int(h)))
    except: h=6
    c=db(); c.execute("UPDATE settings SET value=? WHERE key='tonnage_hours'",(str(h),)); c.commit(); c.close(); flash('تم تحديث مهلة الطوناج','ok'); return redirect(url_for('dashboard'))
@app.route('/admin/office',methods=['POST'])
def office():
    if not login_required('admin'): return redirect(url_for('login'))
    n=request.form['office_name'].strip(); u=request.form['username'].strip(); p=request.form['password'];
    if not n or not u or not p: flash('املأ جميع حقول المكتب','error'); return redirect(url_for('dashboard'))
    c=db()
    try: c.execute('INSERT INTO users(username,password,role,office_name) VALUES(?,?,?,?)',(u,p,'office',n)); c.commit(); flash('تمت إضافة المكتب','ok')
    except sqlite3.IntegrityError: flash('اسم المستخدم موجود مسبقاً','error')
    c.close(); return redirect(url_for('dashboard'))
@app.route('/admin/office/<int:id>/toggle',methods=['POST'])
def toggle_office(id):
    if not login_required('admin'): return redirect(url_for('login'))
    c=db(); c.execute('UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=? AND role=\'office\'',(id,)); c.commit(); c.close(); return redirect(url_for('dashboard'))
@app.route('/admin/load',methods=['POST'])
def load_range():
    if not login_required('admin'): return redirect(url_for('login'))
    mode=request.form.get('mode','range'); c=db()
    if mode=='qty':
        qty=max(0,int(request.form.get('qty',0))); rows=c.execute("SELECT id FROM drivers WHERE status='ready' ORDER BY queue_no LIMIT ?",(qty,)).fetchall()
        ids=[r['id'] for r in rows]
    else:
        a=int(request.form.get('from_no',0)); b=int(request.form.get('to_no',0)); rows=c.execute("SELECT id FROM drivers WHERE status='ready' AND queue_no BETWEEN ? AND ? ORDER BY queue_no",(a,b)).fetchall(); ids=[r['id'] for r in rows]
    for i in ids: c.execute("UPDATE drivers SET status='loaded',loaded_at=? WHERE id=?",(ts(now()),i)); c.execute("INSERT INTO history(driver_id,action,details,created_at) VALUES(?,?,?,?)",(i,'تم التحميل','تم التحميل إلى سوريا',ts(now())))
    c.commit(); c.close(); flash(f'تم تحميل {len(ids)} سائق','ok'); return redirect(url_for('dashboard'))
@app.route('/admin/export/drivers')
def export_drivers():
    if not login_required('admin'): return redirect(url_for('login'))
    # Export is intentionally restricted to the main office/admin account.
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    c=db()
    q=request.args.get('q','').strip(); status=request.args.get('status','').strip(); office=request.args.get('office','').strip()
    sql='SELECT * FROM drivers WHERE 1=1'; args=[]
    if q:
        sql += ' AND (name LIKE ? OR car LIKE ? OR CAST(queue_no AS TEXT) LIKE ?)'; like=f'%{q}%'; args += [like,like,like]
    if status:
        sql += ' AND status=?'; args.append(status)
    if office:
        sql += ' AND office=?'; args.append(office)
    sql += ' ORDER BY queue_no'
    rows=c.execute(sql,args).fetchall()
    c.execute('INSERT INTO history(driver_id,action,details,created_at) VALUES(NULL,?,?,?)',('تصدير Excel','تم تصدير قائمة السواق من حساب المكتب الرئيسي',ts(now())))
    c.commit(); c.close()

    wb=Workbook(); ws=wb.active; ws.title='السواق'
    headers=['رقم السِرّة','اسم السائق','رقم السيارة','العائدية','الحمل','المكتب','حالة الطوناج','الحالة','تاريخ التسجيل','موعد انتهاء المهلة','وقت استلام الطوناج','وقت التحميل']
    ws.append(headers)
    status_map={'pending_tonnage':'بانتظار الطوناج','ready':'مؤهل','loaded':'تم التحميل','expired':'منتهية المهلة'}
    tonnage_map={'pending_tonnage':'بانتظار التسليم','expired':'لم يُسلّم'}
    for r in rows:
        tonnage='تم التسليم' if r['tonnage_received_at'] else tonnage_map.get(r['status'],'—')
        ws.append([r['queue_no'],r['name'],r['car'],r['origin'] or '',r['load'] or '',r['office'],tonnage,status_map.get(r['status'],r['status']),r['registered_at'],r['deadline'],r['tonnage_received_at'] or '',r['loaded_at'] or ''])
    ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
    widths=[14,24,16,18,14,18,20,18,22,22,24,22]
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
    for cell in ws[1]: cell.font=cell.font.copy(bold=True)
    out=io.BytesIO(); wb.save(out); out.seek(0)
    filename=f"shuaa_drivers_{now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(out,as_attachment=True,download_name=filename,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/driver',methods=['GET','POST'])
def driver_portal():
    rows=[]; search=''
    if request.method=='POST':
        search=request.form.get('search','').strip()
        if search:
            c=db(); rows=c.execute('SELECT * FROM drivers WHERE name LIKE ? OR car LIKE ? ORDER BY queue_no',(f'%{search}%',f'%{search}%')).fetchall(); c.close()
            if not rows: flash('لم يتم العثور على سائق بهذا الاسم أو رقم السيارة','error')
        else:
            flash('اكتب اسم السائق أو رقم السيارة','error')
    return render_template('driver.html',rows=rows,search=search)
init()
if __name__=='__main__': app.run(debug=True)
