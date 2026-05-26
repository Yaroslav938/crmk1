import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime
import re
import html

# Настройка страницы
st.set_page_config(page_title="Pipedrive Parser", page_icon="🔄", layout="wide")
st.title("🔄 Pipedrive — Интерактивный парсер данных")

# ─────────────────────────────────────────
# БЕЗОПАСНОСТЬ: Базовая защита приложения
# ─────────────────────────────────────────
# Если хотите ограничить доступ к приложению по ссылке, 
# задайте пин-код. По умолчанию '1234'. Можете поменять в коде.
APP_PIN_CODE = "1234" 

with st.sidebar:
    st.header("🔒 Доступ")
    user_pin = st.text_input("Пин-код приложения", type="password")
    
if user_pin != APP_PIN_CODE:
    st.warning("⚠️ Пожалуйста, введите правильный пин-код в боковой панели для доступа к парсеру.")
    st.stop() # Останавливает выполнение кода дальше этой строки

# ─────────────────────────────────────────
# ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ (Session State)
# ─────────────────────────────────────────
# Это заменяет SQLite. Данные хранятся только пока открыта вкладка.
if "crm_data" not in st.session_state:
    st.session_state["crm_data"] = {}

# ─────────────────────────────────────────
# SIDEBAR: настройки подключения
# ─────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Настройки API")
    api_token = st.text_input("API Token", type="password",
                               help="Settings → Personal preferences → API")
    company_domain = st.text_input("Company Domain",
                                    placeholder="yourcompany",
                                    help="yourcompany.pipedrive.com")
    batch_size = st.slider("Записей за запрос", 50, 500, 100, step=50)
    delay = st.slider("Задержка между запросами (сек)", 0.1, 2.0, 0.3, step=0.1)
    
    st.divider()
    st.caption("💡 API Token: Settings → Personal preferences → API")

BASE_URL = f"https://{company_domain}.pipedrive.com/api/v1" if company_domain else ""

# ─────────────────────────────────────────
# Функции обработки и API
# ─────────────────────────────────────────

def clean_html(text):
    """Безопасно убирает HTML-теги из текста"""
    if not isinstance(text, str):
        return ""
    # Декодируем спецсимволы (&quot;, &amp; и т.д.)
    text = html.unescape(text)
    # Заменяем теги на пробелы, чтобы слова не склеивались
    text = re.sub(r'<[^>]+>', ' ', text)
    # Убираем лишние пробелы
    return re.sub(r'\s+', ' ', text).strip()

def safe_api_request(url, params):
    """Делает запрос с защитой от Rate Limit (ошибка 429)"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            # Если Pipedrive просит притормозить
            if r.status_code == 429:
                time.sleep(2 ** attempt) # Экспоненциальная задержка: 1с, 2с, 4с...
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            st.error(f"Ошибка запроса: {e}")
            return None
    return None

def fetch_endpoint(endpoint, params=None, progress_bar=None, status_text=None):
    """Универсальная выгрузка с пагинацией (offset-based)"""
    all_items = []
    start = 0
    page = 1
    
    while True:
        p = {"api_token": api_token, "start": start, "limit": batch_size}
        if params:
            p.update(params)
        
        data = safe_api_request(f"{BASE_URL}/{endpoint}", params=p)
        if not data or not data.get("success"):
            error_msg = data.get('error', 'Unknown') if data else 'Сетевая ошибка'
            st.error(f"API вернул ошибку: {error_msg}")
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
        
        data = safe_api_request(f"{BASE_URL}/{endpoint}", params=p)
        if not data:
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
# Проверка подключения
# ─────────────────────────────────────────

def test_connection():
    data = safe_api_request(f"{BASE_URL}/users/me", params={"api_token": api_token})
    if data and data.get("success"):
        user = data["data"]
        return True, f"{user.get('name')} ({user.get('email')})"
    error_msg = data.get("error", "Ошибка авторизации") if data else "Нет ответа сервера"
    return False, error_msg

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
    
    progress = st.progress(0)
    status = st.empty()
    log_container = st.container()
    
    tasks = []
    if do_notes:      tasks.append("notes")
    if do_deals:      tasks.append("deals")
    if do_persons:    tasks.append("persons")
    if do_activities: tasks.append("activities")
    
    # Очищаем старые данные только для тех сущностей, которые собираемся обновить
    for task in tasks:
        if task in st.session_state["crm_data"]:
            del st.session_state["crm_data"][task]
            
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
                if "content" in df.columns:
                    df["content_clean"] = df["content"].apply(clean_html)
                # Нормализация данных
                for col in ["user", "deal", "person", "org"]:
                    if col in df.columns:
                        df[f"{col}_id"]   = df[col].apply(lambda x: x.get("id") if isinstance(x, dict) else None)
                        df[f"{col}_name"] = df[col].apply(lambda x: x.get("name") if isinstance(x, dict) else None)
                        df.drop(columns=[col], inplace=True, errors="ignore")
                
                st.session_state["crm_data"]["notes"] = df
                log_container.success(f"✅ Заметки: {len(df)} записей загружено")
        
        elif task == "deals":
            status.info("💼 Выгружаю сделки...")
            items = fetch_cursor_endpoint("deals/collection", status_text=status)
            if items:
                df = pd.DataFrame(items)
                st.session_state["crm_data"]["deals"] = df
                log_container.success(f"✅ Сделки: {len(df)} записей загружено")
        
        elif task == "persons":
            status.info("👤 Выгружаю контакты...")
            items = fetch_cursor_endpoint("persons/collection", status_text=status)
            if items:
                df = pd.DataFrame(items)
                st.session_state["crm_data"]["persons"] = df
                log_container.success(f"✅ Контакты: {len(df)} записей загружено")
        
        elif task == "activities":
            status.info("📅 Выгружаю активности...")
            items = fetch_cursor_endpoint("activities/collection", status_text=status)
            if items:
                df = pd.DataFrame(items)
                st.session_state["crm_data"]["activities"] = df
                log_container.success(f"✅ Активности: {len(df)} записей загружено")
    
    progress.progress(1.0)
    status.success("🎉 Выгрузка завершена!")

# ─────────────────────────────────────────
# Предпросмотр и скачивание
# ─────────────────────────────────────────

st.divider()
st.subheader("👁️ Предпросмотр данных")

# Данные берутся исключительно из безопасного session_state
preview_tables = st.session_state.get("crm_data", {})

if preview_tables:
    tab_labels = list(preview_tables.keys())
    tabs = st.tabs([f"📋 {t.capitalize()} ({len(preview_tables[t])})" for t in tab_labels])
    
    for tab, name in zip(tabs, tab_labels):
        with tab:
            df = preview_tables[name]
            
            # Интерактивный поиск
            if name == "notes" and "content_clean" in df.columns:
                search = st.text_input(f"🔍 Поиск по тексту заметок", key=f"search_{name}")
                if search:
                    # Экранируем спецсимволы в запросе пользователя для безопасного поиска
                    safe_search = re.escape(search)
                    df = df[df["content_clean"].str.contains(safe_search, case=False, na=False)]
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
    st.info("Данные ещё не выгружены. Введите данные, выберите сущности и нажмите 'Начать выгрузку'.")