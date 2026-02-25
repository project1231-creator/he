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
                ("Привычки", "Система Мин
