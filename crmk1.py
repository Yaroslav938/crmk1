import streamlit as st
import requests
import pandas as pd
import sqlite3
import time
from datetime import datetime
import re

st.set_page_config(page_title="Pipedrive Parser", page_icon="🔄", layout="wide")
st.title("🔄 Pipedrive — Интерактивный парсер данных")

# ─────────────────────────────────────────
# SIDEBAR: настройки подключения
# ─────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Настройки")
    api_token = st.text_input("API Token", type="password",
                               help="Settings → Personal preferences → API")
    company_domain = st.text_input("Company Domain",
                                    placeholder="yourcompany",
                                    help="yourcompany.pipedrive.com")
    db_path = st.text_input("Путь к БД", value="crm_data.db")
    batch_size = st.slider("Записей за запрос", 50, 500, 100, step=50)
    delay = st.slider("Задержка между запросами (сек)", 0.1, 2.0, 0.3, step=0.1)
    
    st.divider()
    st.caption("💡 API Token: Settings → Personal preferences → API")

BASE_URL = f"https://{company_domain}.pipedrive.com/api/v1" if company_domain else ""

# ─────────────────────────────────────────
# Функции выгрузки
# ─────────────────────────────────────────

def clean_html(text):
    """Убирает HTML-теги из текста заметок"""
    if not text:
        return ""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', str(text)).strip()

def fetch_endpoint(endpoint, params=None, progress_bar=None, status_text=None):
    """Универсальная выгрузка с пагинацией (offset-based)"""
    all_items = []
    start = 0
    page = 1
    
    while True:
        p = {"api_token": api_token, "start": start, "limit": batch_size}
        if params:
            p.update(params)
        
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=p, timeout=15)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            st.error(f"Ошибка запроса: {e}")
            break
        
        if not data.get("success"):
            st.error(f"API вернул ошибку: {data.get('error', 'Unknown')}")
            break
        
        items = data.get("data") or []
        if not items:
            break
            
        all_items.extend(items)
        
        pagination = data.get("additional_data", {}).get("pagination", {})
        more = pagination.get("more_items_in_collection", False)
        
        if status_text:
            status_text.text(f"⏳ {endpoint}: получено {len(all_items)} записей (страница {page})")
        if progress_bar and not more:
            progress_bar.progress(1.0)
        
        if not more:
            break
        
        start = pagination.get("next_start", start + batch_size)
        page += 1
        time.sleep(delay)
    
    return all_items

def fetch_cursor_endpoint(endpoint, progress_bar=None, status_text=None):
    """Выгрузка с cursor-based пагинацией (для /collection эндпоинтов)"""
    all_items = []
    cursor = None
    page = 1
    
    while True:
        p = {"api_token": api_token, "limit": batch_size}
        if cursor:
            p["cursor"] = cursor
        
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=p, timeout=15)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            st.error(f"Ошибка: {e}")
            break
        
        items = data.get("data") or []
        if not items:
            break
            
        all_items.extend(items)
        
        cursor = data.get("additional_data", {}).get("next_cursor")
        
        if status_text:
            status_text.text(f"⏳ {endpoint}: получено {len(all_items)} (страница {page})")
        
        if not cursor:
            break
        
        page += 1
        time.sleep(delay)
    
    return all_items

# ─────────────────────────────────────────
# Сохранение в SQLite
# ─────────────────────────────────────────

def save_to_db(df, table_name, db_path):
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()

def load_from_db(table_name, db_path):
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(f"SELECT * FROM {table_name}", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

# ─────────────────────────────────────────
# Проверка подключения
# ─────────────────────────────────────────

def test_connection():
    try:
        r = requests.get(f"{BASE_URL}/users/me",
                         params={"api_token": api_token}, timeout=10)
        data = r.json()
        if data.get("success"):
            user = data["data"]
            return True, f"{user.get('name')} ({user.get('email')})"
        return False, data.get("error", "Ошибка авторизации")
    except Exception as e:
        return False, str(e)

# ─────────────────────────────────────────
# UI: Проверка соединения
# ─────────────────────────────────────────

col_check, col_status = st.columns([1, 3])
with col_check:
    if st.button("🔌 Проверить подключение", disabled=not (api_token and company_domain)):
        ok, msg = test_connection()
        if ok:
            st.success(f"✅ Подключено: {msg}")
        else:
            st.error(f"❌ {msg}")

st.divider()

# ─────────────────────────────────────────
# Выбор что выгружать
# ─────────────────────────────────────────

st.subheader("📦 Выберите данные для выгрузки")

col1, col2, col3, col4 = st.columns(4)
do_notes    = col1.checkbox("📝 Заметки (Notes)", value=True)
do_deals    = col2.checkbox("💼 Сделки (Deals)", value=True)
do_persons  = col3.checkbox("👤 Контакты (Persons)")
do_activities = col4.checkbox("📅 Активности")

# Фильтры для заметок
if do_notes:
    with st.expander("⚙️ Фильтры для заметок"):
        note_col1, note_col2 = st.columns(2)
        date_from = note_col1.date_input("Дата от", value=None)
        date_to   = note_col2.date_input("Дата до", value=None)

st.divider()

# ─────────────────────────────────────────
# Кнопка запуска
# ─────────────────────────────────────────

if st.button("🚀 Начать выгрузку", type="primary",
             disabled=not (api_token and company_domain)):
    
    results = {}
    progress = st.progress(0)
    status = st.empty()
    log_container = st.container()
    
    tasks = []
    if do_notes:      tasks.append("notes")
    if do_deals:      tasks.append("deals")
    if do_persons:    tasks.append("persons")
    if do_activities: tasks.append("activities")
    
    for i, task in enumerate(tasks):
        progress.progress(i / len(tasks))
        
        if task == "notes":
            status.info("📝 Выгружаю заметки...")
            params = {}
            if date_from: params["start_date"] = str(date_from)
            if date_to:   params["end_date"]   = str(date_to)
            items = fetch_endpoint("notes", params, status_text=status)
            
            if items:
                df = pd.DataFrame(items)
                # Очистка HTML из контента заметок
                if "content" in df.columns:
                    df["content_clean"] = df["content"].apply(clean_html)
                # Нормализуем вложенные объекты
                for col in ["user", "deal", "person", "org"]:
                    if col in df.columns:
                        df[f"{col}_id"]   = df[col].apply(lambda x: x.get("id") if isinstance(x, dict) else None)
                        df[f"{col}_name"] = df[col].apply(lambda x: x.get("name") if isinstance(x, dict) else None)
                        df.drop(columns=[col], inplace=True, errors="ignore")
                
                save_to_db(df, "notes", db_path)
                results["notes"] = df
                log_container.success(f"✅ Заметки: {len(df)} записей сохранено")
        
        elif task == "deals":
            status.info("💼 Выгружаю сделки...")
            items = fetch_cursor_endpoint("deals/collection", status_text=status)
            if items:
                df = pd.DataFrame(items)
                save_to_db(df, "deals", db_path)
                results["deals"] = df
                log_container.success(f"✅ Сделки: {len(df)} записей сохранено")
        
        elif task == "persons":
            status.info("👤 Выгружаю контакты...")
            items = fetch_cursor_endpoint("persons/collection", status_text=status)
            if items:
                df = pd.DataFrame(items)
                save_to_db(df, "persons", db_path)
                results["persons"] = df
                log_container.success(f"✅ Контакты: {len(df)} записей сохранено")
        
        elif task == "activities":
            status.info("📅 Выгружаю активности...")
            items = fetch_cursor_endpoint("activities/collection", status_text=status)
            if items:
                df = pd.DataFrame(items)
                save_to_db(df, "activities", db_path)
                results["activities"] = df
                log_container.success(f"✅ Активности: {len(df)} записей сохранено")
    
    progress.progress(1.0)
    status.success("🎉 Выгрузка завершена!")
    st.session_state["results"] = results

# ─────────────────────────────────────────
# Предпросмотр и скачивание
# ─────────────────────────────────────────

st.divider()
st.subheader("👁️ Предпросмотр данных")

# Загружаем из БД если уже есть
preview_tables = {}
for tbl in ["notes", "deals", "persons", "activities"]:
    df = load_from_db(tbl, db_path)
    if not df.empty:
        preview_tables[tbl] = df

if preview_tables:
    tab_labels = list(preview_tables.keys())
    tabs = st.tabs([f"📋 {t.capitalize()} ({len(preview_tables[t])})" for t in tab_labels])
    
    for tab, name in zip(tabs, tab_labels):
        with tab:
            df = preview_tables[name]
            
            # Поиск по тексту (особенно полезно для заметок)
            if name == "notes" and "content_clean" in df.columns:
                search = st.text_input(f"🔍 Поиск по тексту заметок", key=f"search_{name}")
                if search:
                    df = df[df["content_clean"].str.contains(search, case=False, na=False)]
                    st.caption(f"Найдено: {len(df)}")
            
            st.dataframe(df, use_container_width=True, height=400)
            
            # Кнопка скачивания CSV
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=f"⬇️ Скачать {name}.csv",
                data=csv,
                file_name=f"pipedrive_{name}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                key=f"download_{name}"
            )
else:
    st.info("Данные ещё не выгружены. Настройте подключение и нажмите 'Начать выгрузку'.")