import os
import json
import hmac
import hashlib
import urllib.parse
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g
from functools import wraps

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'habitmaster-super-secret-key-2026')
    BOT_TOKEN = "8534219584:AAHW2T8MTmoR3dJN_bQDtru49lUSx401QqA"
    DATABASE_URL = os.environ.get('DATABASE_URL')

app = Flask(__name__)
app.config.from_object(Config)

# ==============================================================================
# БАЗА ЗНАНИЙ (Сокращенная для примера, но рабочая)
# ==============================================================================
KNOWLEDGE_BASE = [
    {
        "category": "Энергия",
        "articles": [
            {"id": 101, "title": "Батарейка", "content": "Энергия — это база. Сон, еда, вода.", "read_time": "5 мин", "tags": ["База"]},
            {"id": 102, "title": "Сон", "content": "Спи 8 часов. Это суперсила.", "read_time": "4 мин", "tags": ["Сон"]},
            {"id": 103, "title": "Еда", "content": "Сахар дает обвал. Ешь белок.", "read_time": "4 мин", "tags": ["Еда"]}
        ]
    },
    {
        "category": "Привычки",
        "articles": [
            {"id": 201, "title": "Система Минимума", "content": "Делай смешно мало, но каждый день.", "read_time": "6 мин", "tags": ["Система"]},
            {"id": 202, "title": "Не двух нулей", "content": "Не пропускай два дня подряд.", "read_time": "3 мин", "tags": ["Дисциплина"]}
        ]
    },
    {
        "category": "Психология",
        "articles": [
            {"id": 301, "title": "Дофамин", "content": "Телефон убивает мотивацию.", "read_time": "5 мин", "tags": ["Дофамин"]},
            {"id": 302, "title": "Сила воли", "content": "Это бензин. Экономь его.", "read_time": "4 мин", "tags": ["Ресурс"]}
        ]
    }
]

SHOP_ITEMS = [
    {"id": 1, "name": "Защита серии", "price": 500, "icon": "🛡️", "desc": "Сохраняет серию", "type": "booster"},
    {"id": 2, "name": "XP Бустер", "price": 300, "icon": "⚡", "desc": "x2 опыта", "type": "booster"},
    {"id": 3, "name": "Набор мотивации", "price": 200, "icon": "🔥", "desc": "+100 монет", "type": "lootbox"}
]

# ==============================================================================
# БАЗА ДАННЫХ
# ==============================================================================
def get_db():
    if not hasattr(g, 'db_conn'):
        if app.config['DATABASE_URL']:
            import psycopg2
            g.db_conn = psycopg2.connect(app.config['DATABASE_URL'])
            g.db_conn.autocommit = True
            g.is_postgres = True
            g.cursor = g.db_conn.cursor()
        else:
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
        if is_pg:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, telegram_id TEXT UNIQUE, username TEXT, first_name TEXT, photo_url TEXT, balance INTEGER DEFAULT 100, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS articles (id SERIAL PRIMARY KEY, title TEXT, category TEXT, content TEXT, read_time TEXT, tags TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS user_reads (user_id INTEGER, article_id INTEGER, is_read BOOLEAN DEFAULT FALSE, PRIMARY KEY (user_id, article_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, name TEXT, price INTEGER, icon TEXT, desc TEXT, type TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS purchases (user_id INTEGER, product_id INTEGER, PRIMARY KEY (user_id, product_id))""")
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id TEXT UNIQUE, username TEXT, first_name TEXT, photo_url TEXT, balance INTEGER DEFAULT 100, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, category TEXT, content TEXT, read_time TEXT, tags TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS user_reads (user_id INTEGER, article_id INTEGER, is_read BOOLEAN DEFAULT 0, PRIMARY KEY (user_id, article_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, icon TEXT, desc TEXT, type TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS purchases (user_id INTEGER, product_id INTEGER, PRIMARY KEY (user_id, product_id))""")

        # Заполнение данными
        cur.execute("SELECT count(*) FROM articles")
        if cur.fetchone()[0] == 0:
            for theme in KNOWLEDGE_BASE:
                for art in theme['articles']:
                    tags_str = ",".join(art.get('tags', []))
                    if is_pg:
                        cur.execute("INSERT INTO articles (title, category, content, read_time, tags) VALUES (%s, %s, %s, %s, %s)", (art['title'], theme['category'], art['content'], art['read_time'], tags_str))
                    else:
                        cur.execute("INSERT INTO articles (title, category, content, read_time, tags) VALUES (?, ?, ?, ?, ?)", (art['title'], theme['category'], art['content'], art['read_time'], tags_str))
            
            for p in SHOP_ITEMS:
                if is_pg:
                    cur.execute("INSERT INTO products (name, price, icon, desc, type) VALUES (%s, %s, %s, %s, %s)", (p['name'], p['price'], p['icon'], p['desc'], p['type']))
                else:
                    cur.execute("INSERT INTO products (name, price, icon, desc, type) VALUES (?, ?, ?, ?, ?)", (p['name'], p['price'], p['icon'], p['desc'], p['type']))
            
            if not is_pg: g.db_conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")

# ==============================================================================
# МАРШРУТЫ
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
    except: pass
    return None

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('index'))
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
        session['user_id'] = 'demo_' + str(os.urandom(4).hex())
        session['name'] = 'Гость'
        session['username'] = 'demo'
        session['photo'] = ''
    
    cur = get_db()
    is_pg = getattr(g, 'is_postgres', False)
    
    try:
        if is_pg:
            cur.execute("""INSERT INTO users (telegram_id, username, first_name, photo_url, balance, xp, level) VALUES (%s, %s, %s, %s, 
