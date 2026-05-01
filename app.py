import os
import re
import json
import hmac
import hashlib
import secrets
import subprocess
from functools import wraps

from flask import (
    Flask, request, session, redirect, url_for,
    render_template, jsonify, Response, stream_with_context
)
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

# ── Config ────────────────────────────────────────────────
SERVER_PASSWORD = os.environ.get('SERVER_PASSWORD')
if not SERVER_PASSWORD:
    raise RuntimeError('SERVER_PASSWORD غير محدد في ملف .env — لا يمكن تشغيل التطبيق')
ENV_FILE         = '/home/ubuntu/samsung_screen/.env'
SERVICE_NAME     = 'samsung-screen'
DEPLOY_APPS_CONFIG = '/home/ubuntu/deploy_apps.json'

MANAGED_APPS = [
    {
        'id':      'samsung-screen',
        'name':    'شاشة المدرسة',
        'desc':    'شاشة العرض المدرسية والجدول الدراسي',
        'icon':    '🖥️',
        'service': 'samsung-screen',
        'url':     'http://84.8.100.70',
        'admin':   'http://84.8.100.70/cp',
        'color':   '#3b82f6',
    },
    {
        'id':      'tg-transfer',
        'name':    'نقل تيليجرام',
        'desc':    'أداة نقل الملفات بين قنوات تيليجرام',
        'icon':    '📨',
        'service': 'tg-transfer',
        'url':     'http://84.8.100.70:5000',
        'admin':   '',
        'color':   '#6366f1',
    },
]


# ── Helpers ───────────────────────────────────────────────

def _run(cmd: list, timeout=10) -> str:
    """تشغيل أمر بشكل آمن وإرجاع المخرجات."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return str(e)


def _read_env():
    """قراءة ملف .env كقاموس."""
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip()
    return env


def _write_env(env: dict):
    """كتابة القاموس إلى ملف .env."""
    with open(ENV_FILE, 'w') as f:
        for k, v in env.items():
            f.write(f'{k}={v}\n')


# ── Decorator ─────────────────────────────────────────────

def server_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('_server_admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Routes ────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == SERVER_PASSWORD:
            session['_server_admin'] = True
            return redirect(url_for('dashboard'))
        error = 'كلمة المرور غير صحيحة'
    return render_template('server/login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    response = redirect(url_for('login'))
    response.delete_cookie(app.session_cookie_name)
    return response


@app.route('/')
@server_required
def dashboard():
    return render_template('server/dashboard.html', apps=MANAGED_APPS)


@app.route('/api/status')
@server_required
def api_status():
    svc = request.args.get('service', SERVICE_NAME)
    status = _run(['sudo', 'systemctl', 'is-active', svc])
    uptime = _run(['sudo', 'systemctl', 'show', svc,
                   '--property=ActiveEnterTimestamp', '--value'])
    mem    = _run(['sudo', 'systemctl', 'show', svc,
                   '--property=MemoryCurrent', '--value'])
    try:
        mem_mb = round(int(mem) / 1024 / 1024, 1) if mem.isdigit() else '—'
    except Exception:
        mem_mb = '—'
    return jsonify({'status': status, 'uptime': uptime, 'memory_mb': mem_mb})


@app.route('/api/system')
@server_required
def api_system():
    """إحصائيات السيرفر العامة: CPU، الذاكرة، القرص."""
    # الذاكرة من /proc/meminfo
    mem_total = mem_avail = 0
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    mem_avail = int(line.split()[1])
    except Exception:
        pass
    mem_used = mem_total - mem_avail
    mem_pct  = round(mem_used / mem_total * 100) if mem_total else 0

    # القرص
    disk_out = _run(['df', '-BM', '/'])
    disk_used = disk_total = disk_pct = 0
    try:
        lines = disk_out.strip().split('\n')
        parts = lines[1].split()
        disk_total = int(parts[1].rstrip('M'))
        disk_used  = int(parts[2].rstrip('M'))
        disk_pct   = int(parts[4].rstrip('%'))
    except Exception:
        pass

    # تحميل المعالج
    load_out = _run(['uptime'])
    load_1 = '—'
    try:
        m = re.search(r'load average[s]?:\s*([\d.]+)', load_out)
        if m:
            load_1 = m.group(1)
    except Exception:
        pass

    # وقت تشغيل السيرفر
    uptime_out = _run(['uptime', '-p'])

    return jsonify({
        'mem_total_mb':  round(mem_total / 1024),
        'mem_used_mb':   round(mem_used  / 1024),
        'mem_pct':       mem_pct,
        'disk_total_gb': round(disk_total / 1024, 1),
        'disk_used_gb':  round(disk_used  / 1024, 1),
        'disk_pct':      disk_pct,
        'load':          load_1,
        'uptime':        uptime_out,
    })


@app.route('/api/apps')
@server_required
def api_apps():
    """حالة جميع التطبيقات المُدارة."""
    result = []
    for app_cfg in MANAGED_APPS:
        svc = app_cfg['service']
        status = _run(['sudo', 'systemctl', 'is-active', svc])
        mem    = _run(['sudo', 'systemctl', 'show', svc,
                       '--property=MemoryCurrent', '--value'])
        try:
            mem_mb = round(int(mem) / 1024 / 1024, 1) if mem.isdigit() else '—'
        except Exception:
            mem_mb = '—'
        result.append({
            'id':     app_cfg['id'],
            'status': status.strip(),
            'mem_mb': mem_mb,
        })
    return jsonify(result)


@app.route('/api/logs')
@server_required
def api_logs():
    svc = request.args.get('service', SERVICE_NAME)
    lines = request.args.get('lines', '80')
    try:
        n = min(int(lines), 300)
    except Exception:
        n = 80
    logs = _run(['sudo', 'journalctl', '-u', svc,
                 '-n', str(n), '--no-pager', '--output=short-iso'])
    return jsonify({'logs': logs})


@app.route('/api/restart', methods=['POST'])
@server_required
def api_restart():
    svc = request.json.get('service', SERVICE_NAME) if request.is_json else request.form.get('service', SERVICE_NAME)
    allowed = {a['service'] for a in MANAGED_APPS}
    if svc not in allowed:
        return jsonify({'ok': False, 'error': 'خدمة غير معروفة'}), 400
    out = _run(['sudo', 'systemctl', 'restart', svc], timeout=20)
    return jsonify({'ok': True, 'output': out})


@app.route('/api/app/add', methods=['POST'])
@server_required
def api_app_add():
    """إضافة تطبيق جديد للقائمة المُدارة."""
    data = request.get_json() or {}
    new_entry = {
        'id':      data.get('id', '').strip(),
        'name':    data.get('name', '').strip(),
        'desc':    data.get('desc', '').strip(),
        'icon':    data.get('icon', '🔧'),
        'service': data.get('service', '').strip(),
        'url':     data.get('url', '').strip(),
        'admin':   data.get('admin', '').strip(),
        'color':   data.get('color', '#6366f1'),
    }
    if not new_entry['id'] or not new_entry['service']:
        return jsonify({'ok': False, 'error': 'id و service مطلوبان'}), 400
    # حفظ في deploy_apps.json
    try:
        with open(DEPLOY_APPS_CONFIG) as f:
            apps_cfg = json.load(f)
    except Exception:
        apps_cfg = []
    apps_cfg = [a for a in apps_cfg if a.get('id') != new_entry['id']]
    apps_cfg.append(new_entry)
    try:
        with open(DEPLOY_APPS_CONFIG, 'w') as f:
            json.dump(apps_cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    MANAGED_APPS.append(new_entry)
    return jsonify({'ok': True})


@app.route('/apps/new')
@server_required
def new_app():
    return render_template('server/new_app.html')


@app.route('/apps/deploy', methods=['POST'])
@server_required
def deploy_app():
    """نشر تطبيق جديد من GitHub — streaming SSE response."""
    data      = request.get_json() or {}
    repo      = data.get('repo', '').strip()
    name      = data.get('name', '').strip()
    app_id    = data.get('id', '').strip()
    start_cmd = data.get('cmd', '').strip()
    port      = data.get('port', '').strip()
    icon      = data.get('icon', '🔧')
    color     = data.get('color', '#6366f1')
    gh_token  = data.get('gh_token', '').strip()

    app_dir  = f'/home/ubuntu/{app_id}'
    svc_name = app_id

    def stream():
        def step(msg, ok=True):
            symbol = '✓' if ok else '✗'
            return f"data: {json.dumps({'msg': msg, 'ok': ok, 'symbol': symbol})}\n\n"

        yield step(f'بدء نشر {name}...')

        # 1. Clone
        yield step('استنساخ المستودع من GitHub...')
        out = _run(['git', 'clone', f'git@github.com:{repo}.git', app_dir], timeout=60)
        if 'fatal' in out.lower() or 'error' in out.lower():
            yield step(f'فشل الاستنساخ: {out[:200]}', False)
            yield "data: DONE\n\n"; return
        yield step('تم استنساخ المستودع')

        # 2. pip install
        req = f'{app_dir}/requirements.txt'
        out2 = _run(['bash', '-c', f'[ -f {req} ] && pip3 install -q -r {req} 2>&1 | tail -3 || echo no-req'], timeout=120)
        if 'no-req' not in out2:
            yield step('تم تثبيت المتطلبات')

        # 3. systemd service
        yield step('إنشاء خدمة النظام...')
        svc_content = f"""[Unit]
Description={name}
After=network.target

[Service]
User=ubuntu
WorkingDirectory={app_dir}
ExecStart={start_cmd}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        _run(['sudo', 'bash', '-c',
              f'cat > /etc/systemd/system/{svc_name}.service << \'SVC_EOF\'\n{svc_content}\nSVC_EOF'])
        _run(['sudo', 'systemctl', 'daemon-reload'])
        _run(['sudo', 'systemctl', 'enable', svc_name])
        start_out = _run(['sudo', 'systemctl', 'start', svc_name], timeout=15)
        status = _run(['sudo', 'systemctl', 'is-active', svc_name])
        if status.strip() == 'active':
            yield step('الخدمة شغّالة')
        else:
            yield step(f'الخدمة لم تبدأ: {status}', False)

        # 4. deploy_apps.json
        yield step('تسجيل التطبيق في نظام النشر التلقائي...')
        try:
            with open(DEPLOY_APPS_CONFIG) as f:
                apps_cfg = json.load(f)
        except Exception:
            apps_cfg = []
        apps_cfg = [a for a in apps_cfg if a.get('repo') != repo]
        apps_cfg.append({'name': name, 'repo': repo, 'branch': 'refs/heads/main',
                         'dir': app_dir, 'service': svc_name, 'secret': 'deploy2026'})
        with open(DEPLOY_APPS_CONFIG, 'w') as f:
            json.dump(apps_cfg, f, ensure_ascii=False, indent=2)
        yield step('تم تسجيل التطبيق في النشر التلقائي')

        # 5. MANAGED_APPS
        new_entry = {'id': app_id, 'name': name, 'desc': repo, 'icon': icon,
                     'service': svc_name, 'url': f'http://84.8.100.70:{port}' if port else '',
                     'admin': '', 'color': color}
        MANAGED_APPS.append(new_entry)

        # 6. GitHub Webhook
        if gh_token and repo:
            yield step('إضافة Webhook على GitHub...')
            import urllib.request, urllib.error
            hook_data = json.dumps({
                'name': 'web', 'active': True, 'events': ['push'],
                'config': {'url': 'http://84.8.100.70:9000/deploy',
                           'content_type': 'json', 'secret': 'deploy2026'}
            }).encode()
            req_obj = urllib.request.Request(
                f'https://api.github.com/repos/{repo}/hooks',
                data=hook_data,
                headers={'Authorization': f'token {gh_token}',
                         'Accept': 'application/vnd.github+json',
                         'Content-Type': 'application/json'}
            )
            try:
                urllib.request.urlopen(req_obj, timeout=10)
                yield step('تم إضافة Webhook على GitHub')
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                if 'already exists' in body:
                    yield step('Webhook موجود مسبقاً')
                else:
                    yield step(f'تحذير: Webhook لم يُضَف ({e.code})', False)

        yield step(f'تم نشر {name} بنجاح!')
        yield "data: DONE\n\n"

    return Response(stream_with_context(stream()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/env', methods=['GET', 'POST'])
@server_required
def env():
    saved = False
    env_vars = _read_env()
    if request.method == 'POST':
        new_env = {}
        keys   = request.form.getlist('key')
        values = request.form.getlist('value')
        for k, v in zip(keys, values):
            k = k.strip()
            if k:
                new_env[k] = v.strip()
        _write_env(new_env)
        for k, v in new_env.items():
            os.environ[k] = v
        env_vars = new_env
        saved = True
    return render_template('server/env.html', env=env_vars, saved=saved)


if __name__ == '__main__':
    load_dotenv()
    app.run(host='0.0.0.0', port=4998, debug=False, use_reloader=False)
