import streamlit as st
import requests
import pandas as pd
import time
import re
import html
import json
from datetime import datetime
from collections import defaultdict

# =========================================================
# PAGE
# =========================================================
st.set_page_config(
    page_title="Pipedrive Parser Pro",
    page_icon="🔄",
    layout="wide"
)

st.title("🔄 Pipedrive — Интерактивный парсер данных")
st.caption("Полные карточки заметок, комментарии, связанные сущности, гибкие фильтры и экспорт")

# =========================================================
# SECURITY
# =========================================================
APP_PIN_CODE = st.secrets.get("APP_PIN_CODE", "")

with st.sidebar:
    st.header("🔐 Доступ")

    if APP_PIN_CODE:
        user_pin = st.text_input("Пин-код приложения", type="password")
        if user_pin != APP_PIN_CODE:
            st.warning("⚠️ Введите корректный пин-код для доступа к приложению.")
            st.stop()
        else:
            st.success("Доступ разрешён")
    else:
        st.info("APP_PIN_CODE не задан — защита по пину отключена.")
        st.caption("При желании добавь APP_PIN_CODE в Streamlit Secrets.")

# =========================================================
# SESSION STATE
# =========================================================
if "raw_data" not in st.session_state:
    st.session_state["raw_data"] = {}

if "note_cards_df" not in st.session_state:
    st.session_state["note_cards_df"] = pd.DataFrame()

if "last_sync_meta" not in st.session_state:
    st.session_state["last_sync_meta"] = {}

# =========================================================
# HELPERS
# =========================================================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def safe_str(value):
    if value is None:
        return ""
    return str(value)

def clean_html_text(text):
    if not isinstance(text, str):
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

def extract_id(value):
    if isinstance(value, dict):
        return value.get("id")
    return value

def extract_name(value):
    if isinstance(value, dict):
        return value.get("name") or value.get("title") or ""
    return ""

def as_list(x):
    if isinstance(x, list):
        return x
    return []

def format_multiline_value(value):
    return safe_str(value).replace("\r\n", "\n").replace("\r", "\n").strip()

def build_text_area_height(text, min_h=120, max_h=420):
    lines = max(4, format_multiline_value(text).count("\n") + 3)
    return min(max_h, max(min_h, lines * 24))

def unique_preserve_order(items):
    seen = set()
    out = []
    for item in items:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str) if isinstance(item, dict) else str(item)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out

def json_download_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode("utf-8")

def csv_download_bytes(df):
    return df.to_csv(index=False).encode("utf-8-sig")

# =========================================================
# API SETTINGS
# =========================================================
with st.sidebar:
    st.header("⚙️ Настройки API")

    secret_token = st.secrets.get("PIPEDRIVE_API_TOKEN", "")
    secret_domain = st.secrets.get("PIPEDRIVE_DOMAIN", "")

    manual_override = st.checkbox("Редактировать токен и домен вручную", value=not (secret_token and secret_domain))

    if manual_override:
        api_token = st.text_input(
            "API Token",
            value=secret_token,
            type="password",
            help="Pipedrive → Personal preferences → API"
        )
        company_domain = st.text_input(
            "Company Domain",
            value=secret_domain,
            placeholder="yourcompany",
            help="Например: yourcompany.pipedrive.com → введи only 'yourcompany'"
        )
    else:
        api_token = secret_token
        company_domain = secret_domain
        st.success("Используются значения из Streamlit Secrets")

    batch_size = st.slider("Записей за запрос", 50, 500, 100, step=50)
    delay = st.slider("Задержка между запросами (сек)", 0.0, 2.0, 0.2, step=0.1)

    st.divider()
    st.subheader("Что загружать")
    fetch_comments_enabled = st.checkbox("Подтягивать комментарии к заметкам", value=True)
    fetch_activities_enabled = st.checkbox("Подтягивать активности", value=True)
    fetch_deals_enabled = st.checkbox("Подтягивать сделки", value=True)
    fetch_persons_enabled = st.checkbox("Подтягивать контакты", value=True)
    fetch_orgs_enabled = st.checkbox("Подтягивать организации", value=True)

    st.divider()
    st.caption("Токен не сохраняется в коде и не пишется в репозиторий.")

BASE_URL = f"https://{company_domain}.pipedrive.com/api/v1" if company_domain else ""

# =========================================================
# API CLIENT
# =========================================================
def safe_api_request(url, params=None):
    """
    Совместимый вариант для Pipedrive:
    токен передаётся как api_token, но он не хранится в коде, не пишется на диск
    и не логируется приложением.
    """
    params = dict(params or {})
    params["api_token"] = api_token

    max_retries = 4
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=25)

            if r.status_code == 429:
                wait_time = max(1, 2 ** attempt)
                time.sleep(wait_time)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.RequestException as e:
            st.error(f"Ошибка запроса: {e}")
            return None

    return None


def fetch_paginated(endpoint, params=None, status_box=None):
    all_items = []
    start = 0
    page = 1

    while True:
        p = {"start": start, "limit": batch_size}
        if params:
            p.update(params)

        data = safe_api_request(f"{BASE_URL}/{endpoint}", params=p)

        if not data or not data.get("success"):
            error_msg = data.get("error", "Unknown") if data else "Сетевая ошибка"
            st.error(f"API вернул ошибку для {endpoint}: {error_msg}")
            break

        items = data.get("data") or []
        if not items:
            break

        all_items.extend(items)

        pagination = data.get("additional_data", {}).get("pagination", {})
        more = pagination.get("more_items_in_collection", False)

        if status_box:
            status_box.info(f"⏳ {endpoint}: {len(all_items)} записей, страница {page}")

        if not more:
            break

        start = pagination.get("next_start", start + batch_size)
        page += 1

        if delay > 0:
            time.sleep(delay)

    return all_items


def fetch_note_comments(note_id):
    comments = []
    start = 0

    while True:
        data = safe_api_request(
            f"{BASE_URL}/notes/{note_id}/comments",
            params={"start": start, "limit": batch_size}
        )

        if not data or not data.get("success"):
            break

        items = data.get("data") or []
        if not items:
            break

        comments.extend(items)

        pagination = data.get("additional_data", {}).get("pagination", {})
        if not pagination.get("more_items_in_collection", False):
            break

        start = pagination.get("next_start", start + batch_size)

        if delay > 0:
            time.sleep(delay)

    return comments

# =========================================================
# FORMATTERS
# =========================================================
def format_comment_block(comment):
    comment_author = (
        extract_name(comment.get("user"))
        or safe_str(comment.get("user_name"))
        or safe_str(comment.get("added_by_user_id"))
        or "Неизвестный пользователь"
    )
    comment_time = safe_str(comment.get("add_time") or comment.get("update_time"))
    comment_text = clean_html_text(comment.get("content") or "")
    return f"[{comment_time}] {comment_author}\n{comment_text}".strip()

def format_activity_block(activity):
    parts = []

    activity_type = safe_str(activity.get("type") or "activity")
    subject = safe_str(activity.get("subject") or "Без темы")
    due_date = safe_str(activity.get("due_date") or "")
    done = activity.get("done")
    done_label = "done" if done in (1, True, "1", "true", "True") else "open"

    header = f"{activity_type.upper()} | {subject}"
    if due_date:
        header += f" | due: {due_date}"
    header += f" | {done_label}"

    parts.append(header)

    note = clean_html_text(activity.get("note") or "")
    if note:
        parts.append(note)

    return "\n".join(parts).strip()

# =========================================================
# BUILD LOOKUPS / CARDS
# =========================================================
def build_lookup(records):
    out = {}
    for record in records:
        out[record.get("id")] = record
    return out

def build_activity_indexes(activities):
    by_deal = defaultdict(list)
    by_person = defaultdict(list)
    by_org = defaultdict(list)

    for activity in activities:
        deal_id = activity.get("deal_id")
        person_id = activity.get("person_id")
        org_id = activity.get("org_id")

        if deal_id:
            by_deal[deal_id].append(activity)
        if person_id:
            by_person[person_id].append(activity)
        if org_id:
            by_org[org_id].append(activity)

    return by_deal, by_person, by_org


def build_note_cards(notes, deals, persons, orgs, activities, fetch_comments=True, progress_box=None):
    deal_lookup = build_lookup(deals)
    person_lookup = build_lookup(persons)
    org_lookup = build_lookup(orgs)

    activities_by_deal, activities_by_person, activities_by_org = build_activity_indexes(activities)

    cards = []
    total_notes = max(1, len(notes))

    for idx, note in enumerate(notes, start=1):
        note_id = note.get("id")
        note_add_time = safe_str(note.get("add_time"))
        note_update_time = safe_str(note.get("update_time"))

        note_text = clean_html_text(note.get("content") or "")
        note_html_raw = safe_str(note.get("content") or "")

        note_user_name = (
            extract_name(note.get("user"))
            or safe_str(note.get("user_name"))
            or safe_str(note.get("user_id"))
        )

        deal_id = extract_id(note.get("deal")) or note.get("deal_id")
        person_id = extract_id(note.get("person")) or note.get("person_id")
        org_id = extract_id(note.get("org")) or note.get("org_id")

        deal_obj = deal_lookup.get(deal_id, {})
        person_obj = person_lookup.get(person_id, {})
        org_obj = org_lookup.get(org_id, {})

        deal_title = (
            safe_str(deal_obj.get("title"))
            or extract_name(note.get("deal"))
            or ""
        )
        deal_status = safe_str(deal_obj.get("status") or "")
        deal_value = safe_str(deal_obj.get("value") or "")
        deal_currency = safe_str(deal_obj.get("currency") or "")
        deal_stage_id = safe_str(deal_obj.get("stage_id") or "")

        person_name = (
            safe_str(person_obj.get("name"))
            or extract_name(note.get("person"))
            or ""
        )
        org_name = (
            safe_str(org_obj.get("name"))
            or extract_name(note.get("org"))
            or ""
        )

        comments = fetch_note_comments(note_id) if fetch_comments else []
        comments = unique_preserve_order(comments)
        comments_count = len(comments)

        comments_full_text = "\n\n".join(
            [format_comment_block(c) for c in comments if format_comment_block(c)]
        ).strip()

        related_activities = []
        if deal_id:
            related_activities.extend(activities_by_deal.get(deal_id, []))
        if person_id:
            related_activities.extend(activities_by_person.get(person_id, []))
        if org_id:
            related_activities.extend(activities_by_org.get(org_id, []))

        related_activities = unique_preserve_order(related_activities)
        activities_count = len(related_activities)

        activities_full_text = "\n\n".join(
            [format_activity_block(a) for a in related_activities if format_activity_block(a)]
        ).strip()

        relation_types = []
        if deal_id:
            relation_types.append("deal")
        if person_id:
            relation_types.append("person")
        if org_id:
            relation_types.append("org")

        combined_text_parts = []
        if note_text:
            combined_text_parts.append(note_text)
        if comments_full_text:
            combined_text_parts.append(comments_full_text)
        if activities_full_text:
            combined_text_parts.append(activities_full_text)

        combined_full_text = "\n\n-----\n\n".join(combined_text_parts).strip()

        cards.append({
            "note_id": note_id,
            "note_add_time": note_add_time,
            "note_update_time": note_update_time,
            "owner_name": note_user_name,
            "deal_id": deal_id,
            "deal_title": deal_title,
            "deal_status": deal_status,
            "deal_value": deal_value,
            "deal_currency": deal_currency,
            "deal_stage_id": deal_stage_id,
            "person_id": person_id,
            "person_name": person_name,
            "org_id": org_id,
            "org_name": org_name,
            "relation_types": ", ".join(relation_types),
            "note_full_text": note_text,
            "note_html_raw": note_html_raw,
            "note_length": len(note_text),
            "comments_count": comments_count,
            "comments_full_text": comments_full_text,
            "activities_count": activities_count,
            "activities_full_text": activities_full_text,
            "combined_full_text": combined_full_text,
        })

        if progress_box:
            progress_box.progress(min(idx / total_notes, 1.0), text=f"Сбор карточек: {idx}/{total_notes}")

    df = pd.DataFrame(cards)

    if not df.empty:
        for col in [
            "note_add_time", "note_update_time", "owner_name", "deal_title", "deal_status",
            "person_name", "org_name", "relation_types", "note_full_text",
            "comments_full_text", "activities_full_text", "combined_full_text"
        ]:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str)

        df["has_comments"] = df["comments_count"].fillna(0).astype(int) > 0
        df["has_activities"] = df["activities_count"].fillna(0).astype(int) > 0

    return df

# =========================================================
# HTML TABLE RENDER
# =========================================================
def dataframe_to_full_html_table(df):
    if df.empty:
        return "<p>Нет данных для отображения.</p>"

    def escape_cell(v):
        text = format_multiline_value(v)
        text = html.escape(text)
        text = text.replace("\n", "<br>")
        return text if text else "&nbsp;"

    columns = list(df.columns)

    header_html = "".join([f"<th>{html.escape(str(col))}</th>" for col in columns])

    body_rows = []
    for _, row in df.iterrows():
        cells = "".join([f"<td>{escape_cell(row[col])}</td>" for col in columns])
        body_rows.append(f"<tr>{cells}</tr>")

    table_html = f"""
    <style>
    .pd-full-table-wrap {{
        width: 100%;
        overflow-x: auto;
        border: 1px solid #d9d9d9;
        border-radius: 12px;
        background: white;
    }}
    .pd-full-table {{
        border-collapse: collapse;
        width: 100%;
        min-width: 1400px;
        font-size: 14px;
    }}
    .pd-full-table thead th {{
        position: sticky;
        top: 0;
        background: #f7f7f7;
        z-index: 1;
        text-align: left;
        border-bottom: 1px solid #ddd;
        padding: 10px;
        vertical-align: top;
    }}
    .pd-full-table td {{
        border-bottom: 1px solid #eee;
        padding: 10px;
        vertical-align: top;
        white-space: normal;
        word-break: break-word;
        min-width: 180px;
    }}
    .pd-full-table td:nth-child(1),
    .pd-full-table th:nth-child(1) {{
        min-width: 100px;
    }}
    .pd-full-table td:nth-child(2),
    .pd-full-table th:nth-child(2),
    .pd-full-table td:nth-child(3),
    .pd-full-table th:nth-child(3) {{
        min-width: 160px;
    }}
    .pd-full-table td:hover {{
        background: #fafafa;
    }}
    </style>
    <div class="pd-full-table-wrap">
        <table class="pd-full-table">
            <thead><tr>{header_html}</tr></thead>
            <tbody>{''.join(body_rows)}</tbody>
        </table>
    </div>
    """
    return table_html

# =========================================================
# FILTERS
# =========================================================
def apply_filters(df, search_text="", owners=None, statuses=None, relation_filters=None,
                  only_comments=False, only_activities=False, date_from=None, date_to=None,
                  min_note_length=0):
    if df.empty:
        return df.copy()

    filtered = df.copy()

    if owners:
        filtered = filtered[filtered["owner_name"].isin(owners)]

    if statuses:
        filtered = filtered[filtered["deal_status"].isin(statuses)]

    if relation_filters:
        mask = pd.Series(False, index=filtered.index)
        for rel in relation_filters:
            mask = mask | filtered["relation_types"].str.contains(rel, case=False, na=False)
        filtered = filtered[mask]

    if only_comments:
        filtered = filtered[filtered["comments_count"] > 0]

    if only_activities:
        filtered = filtered[filtered["activities_count"] > 0]

    if min_note_length > 0:
        filtered = filtered[filtered["note_length"] >= min_note_length]

    if search_text:
        pattern = re.escape(search_text.strip())
        search_mask = (
            filtered["note_full_text"].str.contains(pattern, case=False, na=False) |
            filtered["comments_full_text"].str.contains(pattern, case=False, na=False) |
            filtered["activities_full_text"].str.contains(pattern, case=False, na=False) |
            filtered["deal_title"].str.contains(pattern, case=False, na=False) |
            filtered["person_name"].str.contains(pattern, case=False, na=False) |
            filtered["org_name"].str.contains(pattern, case=False, na=False) |
            filtered["owner_name"].str.contains(pattern, case=False, na=False)
        )
        filtered = filtered[search_mask]

    if date_from:
        filtered = filtered[
            pd.to_datetime(filtered["note_add_time"], errors="coerce").dt.date >= date_from
        ]

    if date_to:
        filtered = filtered[
            pd.to_datetime(filtered["note_add_time"], errors="coerce").dt.date <= date_to
        ]

    return filtered

# =========================================================
# SYNC ACTIONS
# =========================================================
col_a, col_b, col_c = st.columns([1, 1, 4])

with col_a:
    do_test = st.button("🔌 Проверить подключение", disabled=not (api_token and company_domain))

with col_b:
    do_sync = st.button("🚀 Синхронизировать данные", type="primary", disabled=not (api_token and company_domain))

if do_test:
    test_data = safe_api_request(f"{BASE_URL}/users/me")
    if test_data and test_data.get("success"):
        user = test_data.get("data", {})
        st.success(f"✅ Подключено: {user.get('name', 'Unknown')} ({user.get('email', 'no email')})")
    else:
        error_msg = test_data.get("error", "Ошибка авторизации") if test_data else "Нет ответа от сервера"
        st.error(f"❌ {error_msg}")

if do_sync:
    st.session_state["raw_data"] = {}
    st.session_state["note_cards_df"] = pd.DataFrame()

    status_box = st.empty()
    card_progress = st.empty()

    notes = fetch_paginated("notes", status_box=status_box)
    deals = fetch_paginated("deals", status_box=status_box) if fetch_deals_enabled else []
    persons = fetch_paginated("persons", status_box=status_box) if fetch_persons_enabled else []
    orgs = fetch_paginated("organizations", status_box=status_box) if fetch_orgs_enabled else []
    activities = fetch_paginated("activities", status_box=status_box) if fetch_activities_enabled else []

    note_cards_df = build_note_cards(
        notes=notes,
        deals=deals,
        persons=persons,
        orgs=orgs,
        activities=activities,
        fetch_comments=fetch_comments_enabled,
        progress_box=card_progress
    )

    st.session_state["raw_data"] = {
        "notes": notes,
        "deals": deals,
        "persons": persons,
        "organizations": orgs,
        "activities": activities
    }
    st.session_state["note_cards_df"] = note_cards_df
    st.session_state["last_sync_meta"] = {
        "synced_at": now_str(),
        "notes": len(notes),
        "deals": len(deals),
        "persons": len(persons),
        "organizations": len(orgs),
        "activities": len(activities),
        "cards": len(note_cards_df),
    }

    status_box.success("✅ Синхронизация завершена")
    st.success(f"Готово: собрано {len(note_cards_df)} полных карточек заметок")

# =========================================================
# DATA AVAILABILITY
# =========================================================
cards_df = st.session_state.get("note_cards_df", pd.DataFrame())
raw_data = st.session_state.get("raw_data", {})
meta = st.session_state.get("last_sync_meta", {})

if not cards_df.empty:
    st.divider()

    # -----------------------------------------------------
    # METRICS
    # -----------------------------------------------------
    total_comments = int(cards_df["comments_count"].fillna(0).sum())
    notes_with_comments = int((cards_df["comments_count"].fillna(0) > 0).sum())
    total_activities = int(cards_df["activities_count"].fillna(0).sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Карточек заметок", len(cards_df))
    m2.metric("Заметок с комментариями", notes_with_comments)
    m3.metric("Всего комментариев", total_comments)
    m4.metric("Связанных активностей", total_activities)
    m5.metric("Последняя синхронизация", meta.get("synced_at", "—"))

    # -----------------------------------------------------
    # FILTERS
    # -----------------------------------------------------
    st.divider()
    st.subheader("🎛️ Фильтры и настройка вывода")

    owners_list = sorted([x for x in cards_df["owner_name"].dropna().unique().tolist() if str(x).strip()])
    statuses_list = sorted([x for x in cards_df["deal_status"].dropna().unique().tolist() if str(x).strip()])

    with st.sidebar:
        st.header("🔎 Фильтры вывода")
        search_text = st.text_input("Поиск по заметкам / комментариям / активностям")
        owners_filter = st.multiselect("Менеджер / владелец", owners_list)
        statuses_filter = st.multiselect("Статус сделки", statuses_list)
        relation_filter = st.multiselect("Тип связи", ["deal", "person", "org"])
        only_comments = st.checkbox("Только с комментариями")
        only_activities = st.checkbox("Только с активностями")
        min_note_length = st.slider("Минимальная длина заметки", 0, 5000, 0, step=50)
        sort_mode = st.selectbox(
            "Сортировка",
            [
                "Новые сверху",
                "Старые сверху",
                "Больше комментариев",
                "Больше активностей",
                "Длинные заметки"
            ]
        )

    f1, f2 = st.columns(2)
    filter_date_from = f1.date_input("Дата заметки: от", value=None)
    filter_date_to = f2.date_input("Дата заметки: до", value=None)

    filtered_df = apply_filters(
        cards_df,
        search_text=search_text,
        owners=owners_filter,
        statuses=statuses_filter,
        relation_filters=relation_filter,
        only_comments=only_comments,
        only_activities=only_activities,
        date_from=filter_date_from,
        date_to=filter_date_to,
        min_note_length=min_note_length
    )

    if sort_mode == "Новые сверху":
        filtered_df = filtered_df.sort_values("note_add_time", ascending=False, na_position="last")
    elif sort_mode == "Старые сверху":
        filtered_df = filtered_df.sort_values("note_add_time", ascending=True, na_position="last")
    elif sort_mode == "Больше комментариев":
        filtered_df = filtered_df.sort_values(["comments_count", "note_add_time"], ascending=[False, False])
    elif sort_mode == "Больше активностей":
        filtered_df = filtered_df.sort_values(["activities_count", "note_add_time"], ascending=[False, False])
    elif sort_mode == "Длинные заметки":
        filtered_df = filtered_df.sort_values(["note_length", "note_add_time"], ascending=[False, False])

    st.info(f"После фильтрации: {len(filtered_df)} карточек")

    # -----------------------------------------------------
    # TABS
    # -----------------------------------------------------
    tab_cards, tab_table, tab_export, tab_raw = st.tabs([
        "🗂️ Карточки",
        "📋 Полная таблица",
        "⬇️ Экспорт",
        "🧪 Сырые данные"
    ])

    # -----------------------------------------------------
    # CARDS TAB
    # -----------------------------------------------------
    with tab_cards:
        st.subheader("Карточки заметок целиком")

        c1, c2, c3 = st.columns([1, 1, 2])
        page_size = c1.selectbox("Карточек на странице", [5, 10, 20, 30, 50], index=1)
        expand_all = c2.checkbox("Открыть все карточки", value=False)

        total_rows = len(filtered_df)
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        page_num = c3.number_input("Страница", min_value=1, max_value=total_pages, value=1, step=1)

        start_idx = (page_num - 1) * page_size
        end_idx = start_idx + page_size
        page_df = filtered_df.iloc[start_idx:end_idx]

        if page_df.empty:
            st.warning("Нет карточек для отображения.")
        else:
            for _, row in page_df.iterrows():
                title_parts = [
                    f"#{row['note_id']}",
                    row["deal_title"] or row["person_name"] or row["org_name"] or "Без привязки",
                    row["owner_name"] or "Без владельца",
                    row["note_add_time"][:19] if row["note_add_time"] else "",
                    f"комм.: {row['comments_count']}",
                    f"акт.: {row['activities_count']}",
                ]
                expander_title = " | ".join([x for x in title_parts if x])

                with st.expander(expander_title, expanded=expand_all):
                    meta1, meta2, meta3, meta4 = st.columns(4)
                    meta1.metric("Комментарии", int(row["comments_count"]))
                    meta2.metric("Активности", int(row["activities_count"]))
                    meta3.metric("Длина заметки", int(row["note_length"]))
                    meta4.metric("Статус сделки", row["deal_status"] or "—")

                    st.markdown("### Метаданные")
                    md1, md2 = st.columns(2)
                    with md1:
                        st.write(f"**Note ID:** {row['note_id']}")
                        st.write(f"**Добавлена:** {row['note_add_time'] or '—'}")
                        st.write(f"**Обновлена:** {row['note_update_time'] or '—'}")
                        st.write(f"**Владелец:** {row['owner_name'] or '—'}")
                        st.write(f"**Тип связи:** {row['relation_types'] or '—'}")
                    with md2:
                        st.write(f"**Deal:** {row['deal_title'] or '—'}")
                        st.write(f"**Deal status:** {row['deal_status'] or '—'}")
                        st.write(f"**Person:** {row['person_name'] or '—'}")
                        st.write(f"**Organization:** {row['org_name'] or '—'}")

                    st.markdown("### Полный текст заметки")
                    st.text_area(
                        "note_text",
                        value=row["note_full_text"],
                        height=build_text_area_height(row["note_full_text"], 160, 500),
                        key=f"note_text_{row['note_id']}",
                        label_visibility="collapsed"
                    )

                    st.markdown("### Комментарии к заметке")
                    if row["comments_count"] > 0:
                        st.text_area(
                            "comments_text",
                            value=row["comments_full_text"],
                            height=build_text_area_height(row["comments_full_text"], 140, 520),
                            key=f"comments_text_{row['note_id']}",
                            label_visibility="collapsed"
                        )
                    else:
                        st.info("У этой заметки нет комментариев.")

                    st.markdown("### Связанные активности")
                    if row["activities_count"] > 0:
                        st.text_area(
                            "activities_text",
                            value=row["activities_full_text"],
                            height=build_text_area_height(row["activities_full_text"], 140, 520),
                            key=f"activities_text_{row['note_id']}",
                            label_visibility="collapsed"
                        )
                    else:
                        st.info("Для этой заметки не найдено связанных активностей.")

                    with st.expander("Показать объединённый текст для аналитики"):
                        st.text_area(
                            "combined_text",
                            value=row["combined_full_text"],
                            height=build_text_area_height(row["combined_full_text"], 180, 560),
                            key=f"combined_text_{row['note_id']}",
                            label_visibility="collapsed"
                        )

    # -----------------------------------------------------
    # FULL TABLE TAB
    # -----------------------------------------------------
    with tab_table:
        st.subheader("Таблица без обрезки содержимого")

        default_columns = [
            "note_id", "note_add_time", "owner_name",
            "deal_title", "deal_status", "person_name", "org_name",
            "note_full_text", "comments_full_text", "activities_full_text"
        ]
        available_columns = filtered_df.columns.tolist()

        selected_columns = st.multiselect(
            "Колонки таблицы",
            available_columns,
            default=[c for c in default_columns if c in available_columns]
        )

        max_rows_html = st.slider("Сколько строк выводить в HTML-таблице", 10, 500, 100, step=10)

        if selected_columns:
            html_df = filtered_df[selected_columns].head(max_rows_html).copy()
            st.markdown(
                dataframe_to_full_html_table(html_df),
                unsafe_allow_html=True
            )
        else:
            st.warning("Выбери хотя бы одну колонку.")

        with st.expander("Тот же набор данных в st.dataframe"):
            st.dataframe(
                filtered_df[selected_columns] if selected_columns else filtered_df,
                use_container_width=True,
                height=500
            )

    # -----------------------------------------------------
    # EXPORT TAB
    # -----------------------------------------------------
    with tab_export:
        st.subheader("Экспорт")

        export_df = filtered_df.copy()

        st.download_button(
            label="⬇️ Скачать отфильтрованные карточки как CSV",
            data=csv_download_bytes(export_df),
            file_name=f"pipedrive_note_cards_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv"
        )

        st.download_button(
            label="⬇️ Скачать отфильтрованные карточки как JSON",
            data=json_download_bytes(export_df.to_dict(orient="records")),
            file_name=f"pipedrive_note_cards_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json"
        )

        combined_export_cols = [
            col for col in [
                "note_id", "note_add_time", "owner_name", "deal_title", "deal_status",
                "person_name", "org_name", "combined_full_text"
            ] if col in export_df.columns
        ]

        if combined_export_cols:
            combined_df = export_df[combined_export_cols].copy()
            st.download_button(
                label="⬇️ Скачать компактный экспорт для NLP / LLM",
                data=csv_download_bytes(combined_df),
                file_name=f"pipedrive_llm_ready_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv"
            )

    # -----------------------------------------------------
    # RAW TAB
    # -----------------------------------------------------
    with tab_raw:
        st.subheader("Сырые сущности")

        raw_tabs = []
        raw_names = []

        for key in ["notes", "deals", "persons", "organizations", "activities"]:
            records = raw_data.get(key, [])
            if records:
                raw_names.append(f"{key} ({len(records)})")
                raw_tabs.append((key, records))

        if not raw_tabs:
            st.warning("Сырые данные ещё не загружены.")
        else:
            tabs = st.tabs(raw_names)
            for tab, (key, records) in zip(tabs, raw_tabs):
                with tab:
                    rdf = pd.DataFrame(records)
                    st.dataframe(rdf, use_container_width=True, height=450)

                    st.download_button(
                        label=f"⬇️ Скачать {key}.json",
                        data=json_download_bytes(records),
                        file_name=f"pipedrive_raw_{key}_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                        mime="application/json",
                        key=f"download_raw_{key}"
                    )

else:
    st.divider()
    st.info(
        "Сначала укажи API Token и Company Domain, затем нажми «Синхронизировать данные». "
        "После этого появятся полные карточки заметок, полная таблица и экспорт."
    )