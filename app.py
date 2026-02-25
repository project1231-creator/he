import os
import json
import hmac
import hashlib
import urllib.parse
import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, g
from functools import wraps

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
    BOT_TOKEN = "8534219584:AAHW2T8MTmoR3dJN_bQDtru49lUSx401QqA"
    # Render предоставляет DATABASE_URL. Если его нет, используем SQLite для теста.
    DATABASE_URL = os.environ.get('DATABASE_URL')

app = Flask(__name__)
app.config.from_object(Config)

# ==============================================================================
# РАБОТА С БАЗОЙ ДАННЫХ
# ==============================================================================
def get_db():
    if not hasattr(g, 'db_conn'):
        if app.config['DATABASE_URL']:
            # Подключение к PostgreSQL на Render
            import psycopg2
            g.db_conn = psycopg2.connect(app.config['DATABASE_URL'])
            g.db_conn.autocommit = True
            g.is_postgres = True
            g.cursor = g.db_conn.cursor()
        else:
            # Подключение к SQLite локально (для тестов)
            g.db_conn = sqlite3.connect('habitmaster.db')
            g.db_conn.row_factory = sqlite3.Row
            g.is_postgres = False
            g.cursor = g.db_conn.cursor()
    return g.cursor

@app.teardown_appcontext
def close_connection(exception):
    db_conn = getattr(g, 'db_conn', None)
    if db_conn and not getattr(g, 'is_postgres', False):
        db_conn.close()

def init_db():
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    try:
        # Создание таблиц
        if is_pg:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, telegram_id TEXT UNIQUE, username TEXT, 
                first_name TEXT, photo_url TEXT, balance INTEGER DEFAULT 100, 
                xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS articles (
                id SERIAL PRIMARY KEY, title TEXT, category TEXT, content TEXT, 
                read_time TEXT, tags TEXT
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS user_reads (
                user_id INTEGER, article_id INTEGER, is_read BOOLEAN DEFAULT FALSE, 
                PRIMARY KEY (user_id, article_id)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY, name TEXT, price INTEGER, icon TEXT, desc TEXT, type TEXT
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS purchases (
                user_id INTEGER, product_id INTEGER, PRIMARY KEY (user_id, product_id)
            )""")
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id TEXT UNIQUE, username TEXT, 
                first_name TEXT, photo_url TEXT, balance INTEGER DEFAULT 100, 
                xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, category TEXT, content TEXT, 
                read_time TEXT, tags TEXT
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS user_reads (
                user_id INTEGER, article_id INTEGER, is_read BOOLEAN DEFAULT 0, 
                PRIMARY KEY (user_id, article_id)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, icon TEXT, desc TEXT, type TEXT
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS purchases (
                user_id INTEGER, product_id INTEGER, PRIMARY KEY (user_id, product_id)
            )""")

        # Заполнение данными (только если пусто)
        cur.execute("SELECT count(*) FROM articles")
        count = cur.fetchone()[0]
        
        if count == 0:
            print("📚 Загрузка демо-контента...")
            # Добавляем несколько коротких статей для теста
            demos = [
                ("Энергия", "Батарейка: почему без энергии не работают техники", "Сон, еда, вода — база. Без энергии ты не сможешь ничего.", "5 мин", "База"),
                ("Привычки", "Система Минимума", "Делай смехотворно мало, но каждый день. 1 отжимание лучше 0.", "4 мин", "Система"),
                ("Дофамин", "Дофаминовая яма", "Телефон убивает мотивацию. Устрой детокс на 72 часа.", "6 мин", "Психология")
            ]
            for cat, title, content, time, tags in demos:
                if is_pg:
                    cur.execute("INSERT INTO articles (category, title, content, read_time, tags) VALUES (%s, %s, %s, %s, %s)", (cat, title, content, time, tags))
                else:
                    cur.execute("INSERT INTO articles (category, title, content, read_time, tags) VALUES (?, ?, ?, ?, ?)", (cat, title, content, time, tags))
            
            # Товары
            prods = [
                ("Защита серии", 500, "🛡️", "Сохраняет серию", "booster"),
                ("XP Бустер", 300, "⚡", "x2 опыта", "booster"),
                ("Набор мотивации", 200, "🔥", "+100 монет", "lootbox")
            ]
            for name, price, icon, desc, type_ in prods:
                if is_pg:
                    cur.execute("INSERT INTO products (name, price, icon, desc, type) VALUES (%s, %s, %s, %s, %s)", (name, price, icon, desc, type_))
                else:
                    cur.execute("INSERT INTO products (name, price, icon, desc, type) VALUES (?, ?, ?, ?, ?)", (name, price, icon, desc, type_))
            
            if not is_pg: g.db_conn.commit()
            print("✅ База готова!")
    except Exception as e:
        print(f"❌ Ошибка БД: {e}")
        if not is_pg: g.db_conn.rollback()
        raise e  # Важно: выбрасываем ошибку, чтобы Render показал её в логах

# ==============================================================================
# АВТОРИЗАЦИЯ И МАРШРУТЫ
# ==============================================================================
def check_telegram_auth(init_data_str):
    if not init_data_str: return None
    try:
        parsed = urllib.parse.parse_qs(init_data_str)
        hash_val = parsed.get('hash', [''])[0]
        data_list = [f"{k}={parsed[k][0]}" for k in sorted(parsed.keys()) if k != 'hash']
        secret = hashlib.sha256(Config.BOT_TOKEN.encode()).digest()
        hmac_hash = hmac.new(secret, '\n'.join(data_list).encode(), hashlib.sha256).hexdigest()
        if hmac_hash == hash_val:
            return json.loads(parsed.get('user', ['{}'])[0])
    except Exception as e:
        print(f"Auth Error: {e}")
    return None

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return wrap

@app.route('/')
def index():
    init_data = request.args.get('tgWebAppData', '')
    user = check_telegram_auth(init_data)
    
    if user:
        session['user_id'] = str(user['id'])
        session['name'] = user.get('first_name', 'User')
        session['username'] = user.get('username', '')
        session['photo'] = user.get('photo_url', '')
    else:
        # Демо режим
        session['user_id'] = 'demo_' + str(os.urandom(4).hex())
        session['name'] = 'Гость'
        session['username'] = 'demo'
        session['photo'] = ''
    
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    try:
        if is_pg:
            cur.execute("""INSERT INTO users (telegram_id, username, first_name, photo_url, balance, xp, level) 
                          VALUES (%s, %s, %s, %s, 100, 0, 1) ON CONFLICT (telegram_id) DO NOTHING""",
                       (session['user_id'], session['username'], session['name'], session['photo']))
        else:
            cur.execute("""INSERT OR IGNORE INTO users (telegram_id, username, first_name, photo_url, balance, xp, level) 
                          VALUES (?, ?, ?, ?, 100, 0, 1)""",
                       (session['user_id'], session['username'], session['name'], session['photo']))
            g.db_conn.commit()
    except Exception as e:
        print(f"DB Insert Error: {e}")
        
    return redirect(url_for('home'))

@app.route('/home')
@login_required
def home():
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    query = "SELECT * FROM users WHERE telegram_id = %s" if is_pg else "SELECT * FROM users WHERE telegram_id = ?"
    cur.execute(query, (session['user_id'],))
    row = cur.fetchone()
    
    if not row: return redirect(url_for('index'))
    
    user = {'id': row[0], 'telegram_id': row[1], 'username': row[2], 'first_name': row[3], 
            'photo_url': row[4], 'balance': row[5], 'xp': row[6], 'level': row[7], 'streak': row[8]} if is_pg else dict(row)
        
    return render_template_string(HTML_HOME, user=user)

@app.route('/library')
@login_required
def library():
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    cur.execute("SELECT * FROM articles ORDER BY category")
    rows = cur.fetchall()
    
    uid_query = "SELECT id FROM users WHERE telegram_id = %s" if is_pg else "SELECT id FROM users WHERE telegram_id = ?"
    cur.execute(uid_query, (session['user_id'],))
    uid_row = cur.fetchone()
    uid = uid_row[0] if uid_row else 0
    
    read_query = "SELECT article_id FROM user_reads WHERE user_id = %s" if is_pg else "SELECT article_id FROM user_reads WHERE user_id = ?"
    cur.execute(read_query, (uid,))
    read_rows = cur.fetchall()
    read_ids = [r[0] for r in read_rows]
    
    categories = {}
    for row in rows:
        if is_pg:
            art = {'id': row[0], 'category': row[1], 'title': row[2], 'content': row[3], 'read_time': row[4], 'tags': row[5]}
        else:
            art = dict(row)
        
        cat = art['category']
        if cat not in categories: categories[cat] = []
        art['is_read'] = art['id'] in read_ids
        categories[cat].append(art)
        
    return render_template_string(HTML_LIBRARY, categories=categories, total=len(rows), read_count=len(read_ids))

@app.route('/shop')
@login_required
def shop():
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    u_query = "SELECT * FROM users WHERE telegram_id = %s" if is_pg else "SELECT * FROM users WHERE telegram_id = ?"
    cur.execute(u_query, (session['user_id'],))
    row = cur.fetchone()
    user = {'id': row[0], 'balance': row[5]} if is_pg else dict(row)
    uid = user['id']
    bal = user['balance']
    
    cur.execute("SELECT * FROM products")
    items = cur.fetchall()
    
    b_query = "SELECT product_id FROM purchases WHERE user_id = %s" if is_pg else "SELECT product_id FROM purchases WHERE user_id = ?"
    cur.execute(b_query, (uid,))
    bought_rows = cur.fetchall()
    bought_ids = [r[0] for r in bought_rows]
    
    shop_items = []
    for item in items:
        d = {'id': item[0], 'name': item[1], 'price': item[2], 'icon': item[3], 'desc': item[4], 'type': item[5]} if is_pg else dict(item)
        d['bought'] = d['id'] in bought_ids
        d['can_buy'] = bal >= d['price']
        shop_items.append(d)
        
    return render_template_string(HTML_SHOP, items=shop_items, user=user)

@app.route('/stats')
@login_required
def stats():
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    cur.execute("SELECT first_name, xp, level FROM users ORDER BY xp DESC LIMIT 10")
    top_rows = cur.fetchall()
    top = [{'first_name': r[0], 'xp': r[1], 'level': r[2]} for r in top_rows]
    
    u_query = "SELECT * FROM users WHERE telegram_id = %s" if is_pg else "SELECT * FROM users WHERE telegram_id = ?"
    cur.execute(u_query, (session['user_id'],))
    row = cur.fetchone()
    me = {'id': row[0], 'balance': row[5], 'xp': row[6], 'level': row[7]} if is_pg else dict(row)
    
    return render_template_string(HTML_STATS, top=top, me=me)

@app.route('/profile')
@login_required
def profile():
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    u_query = "SELECT * FROM users WHERE telegram_id = %s" if is_pg else "SELECT * FROM users WHERE telegram_id = ?"
    cur.execute(u_query, (session['user_id'],))
    row = cur.fetchone()
    user = {'id': row[0], 'telegram_id': row[1], 'username': row[2], 'first_name': row[3], 'photo_url': row[4], 'streak': row[8]} if is_pg else dict(row)
    
    return render_template_string(HTML_PROFILE, user=user, earned_ach=0, total_ach=5)

@app.route('/api/read/<int:aid>', methods=['POST'])
@login_required
def api_read(aid):
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    uid_query = "SELECT id FROM users WHERE telegram_id = %s" if is_pg else "SELECT id FROM users WHERE telegram_id = ?"
    cur.execute(uid_query, (session['user_id'],))
    uid = cur.fetchone()[0]
    
    try:
        if is_pg:
            cur.execute("""INSERT INTO user_reads (user_id, article_id, is_read) VALUES (%s, %s, TRUE) 
                          ON CONFLICT (user_id, article_id) DO UPDATE SET is_read=TRUE""", (uid, aid))
            cur.execute("UPDATE users SET xp = xp + 10, balance = balance + 5 WHERE id = %s", (uid,))
        else:
            cur.execute("INSERT OR REPLACE INTO user_reads (user_id, article_id, is_read) VALUES (?, ?, 1)", (uid, aid))
            cur.execute("UPDATE users SET xp = xp + 10, balance = balance + 5 WHERE id = ?", (uid,))
            g.db_conn.commit()
    except Exception as e:
        print(f"Read Error: {e}")
        return jsonify({'ok': False}), 500
        
    return jsonify({'ok': True})

@app.route('/api/buy/<int:pid>', methods=['POST'])
@login_required
def api_buy(pid):
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    u_query = "SELECT id, balance FROM users WHERE telegram_id = %s" if is_pg else "SELECT id, balance FROM users WHERE telegram_id = ?"
    cur.execute(u_query, (session['user_id'],))
    u = cur.fetchone()
    
    p_query = "SELECT price FROM products WHERE id = %s" if is_pg else "SELECT price FROM products WHERE id = ?"
    cur.execute(p_query, (pid,))
    p = cur.fetchone()
    
    if not u or not p:
        return jsonify({'ok': False, 'error': 'Ошибка данных'}), 400
        
    if u[1] >= p[0]:
        try:
            if is_pg:
                cur.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (p[0], u[0]))
                cur.execute("INSERT INTO purchases (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (u[0], pid))
                if pid == 3: # Lootbox
                    bonus = 100
                    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (bonus, u[0]))
            else:
                cur.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (p[0], u[0]))
                cur.execute("INSERT OR IGNORE INTO purchases (user_id, product_id) VALUES (?, ?)", (u[0], pid))
                if pid == 3:
                    cur.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (100, u[0]))
                g.db_conn.commit()
                
            new_bal = u[1] - p[0]
            if pid == 3: new_bal += 100
            
            return jsonify({'ok': True, 'new_bal': new_bal, 'msg': 'Куплено!'})
        except Exception as e:
            print(f"Buy Error: {e}")
            return jsonify({'ok': False, 'error': str(e)}), 500
    
    return jsonify({'ok': False, 'error': 'Недостаточно монет'}), 400

# ==============================================================================
# HTML ШАБЛОНЫ (Упрощенные для надежности)
# ==============================================================================
CSS_STYLE = """
:root { --bg: #0f0f13; --card: #1e1e24; --accent: #00ff88; --text: #fff; }
body { background: var(--bg); color: var(--text); font-family: sans-serif; margin: 0; padding-bottom: 70px; }
.container { max-width: 600px; margin: 0 auto; padding: 20px; }
h1 { color: var(--accent); }
.card { background: var(--card); padding: 15px; border-radius: 12px; margin-bottom: 15px; border: 1px solid #333; cursor: pointer; }
.btn { background: var(--accent); color: #000; border: none; padding: 10px; width: 100%; border-radius: 8px; font-weight: bold; margin-top: 10px; cursor: pointer; }
.btn:disabled { background: #444; color: #888; }
.nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--card); display: flex; justify-content: space-around; padding: 10px; border-top: 1px solid #333; }
.nav a { color: #aaa; text-decoration: none; font-size: 12px; text-align: center; }
.nav a.active { color: var(--accent); }
.modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 1000; justify-content: center; align-items: center; }
.modal.open { display: flex; }
.modal-content { background: var(--card); padding: 20px; border-radius: 12px; width: 90%; max-height: 80vh; overflow-y: auto; position: relative; }
.close-btn { position: absolute; top: 10px; right: 15px; font-size: 24px; cursor: pointer; color: #fff; background: none; border: none; }
.modal-text { white-space: pre-wrap; line-height: 1.6; margin-top: 15px; }
"""

HTML_BASE = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HabitMaster Pro</title>
    <style>{CSS_STYLE}</style>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script>
        Telegram.WebApp.ready();
        Telegram.WebApp.expand();
        function openModal(id, title, content, isRead) {{
            document.getElementById('mTitle').innerText = title;
            document.getElementById('mText').innerText = content;
            const btn = document.getElementById('mBtn');
            if(isRead) {{
                btn.disabled = true; btn.innerText = '✓ Прочитано'; btn.style.background = '#444';
            }} else {{
                btn.disabled = false; btn.innerText = 'Отметить прочитанным (+10 XP)'; btn.style.background = 'var(--accent)';
                btn.onclick = function() {{ markRead(id); }};
            }}
            document.getElementById('articleModal').classList.add('open');
        }}
        function closeModal() {{ document.getElementById('articleModal').classList.remove('open'); }}
        async function markRead(id) {{
            const r = await fetch('/api/read/' + id, {{method: 'POST'}});
            if((await r.json()).ok) {{ alert('+10 XP'); location.reload(); }}
        }}
        async function buyItem(id) {{
            if(!confirm('Купить?')) return;
            const r = await fetch('/api/buy/' + id, {{method: 'POST'}});
            const d = await r.json();
            alert(d.msg || d.error);
            if(d.ok) location.reload();
        }}
    </script>
</head>
<body>
    <div class="container">{{{{ block_content }}}}</div>
    <div class="modal" id="articleModal">
        <div class="modal-content">
            <button class="close-btn" onclick="closeModal()">×</button>
            <h2 id="mTitle" style="color:var(--accent)"></h2>
            <div id="mText" class="modal-text"></div>
            <button id="mBtn" class="btn" style="margin-top:20px;">Отметить прочитанным</button>
        </div>
    </div>
    <nav class="nav">
        <a href="/home" class="{{{{ 'active' if request.endpoint == 'home' else '' }}}}" >🏠<br>Главная</a>
        <a href="/library" class="{{{{ 'active' if request.endpoint == 'library' else '' }}}}" >📚<br>Книги</a>
        <a href="/shop" class="{{{{ 'active' if request.endpoint == 'shop' else '' }}}}" >🛒<br>Магазин</a>
        <a href="/stats" class="{{{{ 'active' if request.endpoint == 'stats' else '' }}}}" >📊<br>Топ</a>
        <a href="/profile" class="{{{{ 'active' if request.endpoint == 'profile' else '' }}}}" >👤<br>Профиль</a>
    </nav>
</body>
</html>
"""

HTML_HOME = """{% extends "base" %}{% block content %}
<h1>Привет, {{ user.first_name }}!</h1>
<div class="card" style="text-align:center; margin-bottom:20px;">
    <h2 style="color:var(--accent); margin:0;">💰 {{ user.balance }}</h2>
    <p>Уровень: {{ user.level }} | XP: {{ user.xp }}</p>
</div>
<div class="card" onclick="location.href='/library'" style="display:flex; align-items:center; gap:15px;">
    <div style="font-size:30px;">📚</div>
    <div><b>Библиотека</b><br><small style="color:#aaa">Читать и получать XP</small></div>
</div>
<div class="card" onclick="location.href='/shop'" style="display:flex; align-items:center; gap:15px;">
    <div style="font-size:30px;">🛒</div>
    <div><b>Магазин</b><br><small style="color:#aaa">Тратить монеты</small></div>
</div>
{% endblock %}"""

HTML_LIBRARY = """{% extends "base" %}{% block content %}
<h1>Библиотека</h1>
<p>Прочитано: {{ read_count }}/{{ total }}</p>
{% for cat, arts in categories.items() %}
    <h3 style="color:var(--accent); margin-top:20px;">📂 {{ cat }}</h3>
    {% for art in arts %}
    <div class="card" onclick="openModal({{ art.id }}, '{{ art.title }}', '{{ art.content|replace("'", "\\'")|replace('\n', '\\n') }}', {{ 'true' if art.is_read else 'false' }})">
        <div style="display:flex; justify-content:space-between;">
            <b>{{ art.title }}</b>
            <span style="background:#333; padding:2px 6px; border-radius:4px; font-size:11px;">{{ '✓' if art.is_read else art.read_time }}</span>
        </div>
        <p style="font-size:13px; color:#aaa; margin-top:5px;">{{ art.content[:80] }}...</p>
    </div>
    {% endfor %}
{% endfor %}
{% endblock %}"""

HTML_SHOP = """{% extends "base" %}{% block content %}
<h1>Магазин</h1>
<div class="card" style="text-align:center"><h2>💰 {{ user.balance }}</h2></div>
{% for item in items %}
<div class="card" style="display:flex; align-items:center; gap:10px;">
    <div style="font-size:30px;">{{ item.icon }}</div>
    <div style="flex:1;">
        <b>{{ item.name }}</b><br><small style="color:#aaa">{{ item.desc }}</small><br>
        <span style="color:var(--accent); font-weight:bold;">{{ item.price }} монет</span>
    </div>
    <div style="width:80px;">
        {% if item.bought %}
            <button class="btn" disabled style="padding:5px; font-size:12px;">Куплено</button>
        {% elif item.can_buy %}
            <button class="btn" onclick="buyItem({{ item.id }})" style="padding:5px; font-size:12px;">Купить</button>
        {% else %}
            <button class="btn" disabled style="background:#444; padding:5px; font-size:12px;">Нет ₿</button>
        {% endif %}
    </div>
</div>
{% endfor %}
{% endblock %}"""

HTML_STATS = """{% extends "base" %}{% block content %}
<h1>Рейтинг</h1>
<div class="card">
    {% for p in top %}
    <div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #333;">
        <span>#{{ loop.index }} {{ p.first_name }} (Lvl {{ p.level }})</span>
        <span style="color:var(--accent); font-weight:bold;">{{ p.xp }} XP</span>
    </div>
    {% endfor %}
</div>
<div class="card" style="margin-top:20px;">
    <h3>Твоя статистика</h3>
    <p>Уровень: <b>{{ me.level }}</b></p>
    <p>XP: <b>{{ me.xp }}</b></p>
    <p>Баланс: <b>{{ me.balance }}</b></p>
</div>
{% endblock %}"""

HTML_PROFILE = """{% extends "base" %}{% block content %}
<h1>Профиль</h1>
<div class="card" style="text-align:center; padding:30px 20px;">
    <div style="width:80px; height:80px; background:#333; border-radius:50%; margin:0 auto 15px; display:flex; align-items:center; justify-content:center; font-size:30px; border:3px solid var(--accent);">
        {{ user.first_name[0] if user.first_name else 'U' }}
    </div>
    <h2>{{ user.first_name }}</h2>
    <p style="color:#aaa;">@{{ user.username or 'anon' }}</p>
    <p style="color:#666; font-size:12px;">ID: {{ user.telegram_id }}</p>
</div>
{% endblock %}"""

def render_template_string(template, **kwargs):
    from flask import render_template_string as rts
    final_html = template.replace('{% extends "base" %}', '').replace('{% block content %}', '').replace('{% endblock %}', '')
    full_page = HTML_BASE.replace('{{{{ block_content }}}}', final_html)
    full_page = full_page.replace('{{{{', '{{').replace('}}}}', '}}')
    return rts(full_page, **kwargs)

if __name__ == '__main__':
    print("🚀 Запуск HabitMaster Pro...")
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
