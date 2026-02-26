# -*- coding: utf-8 -*-
"""
БД для Telegram-бота: пользователи, фото, примерки, оценки.
SQLite.
"""
import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "bot_data.db")
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                file_path TEXT NOT NULL,
                file_id_telegram TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tryons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                tryon_type TEXT NOT NULL,  -- 'single' | 'multi' | 'repeat'
                previous_tryon_id INTEGER REFERENCES tryons(id),
                person_photo_path TEXT NOT NULL,
                product_links TEXT NOT NULL,  -- JSON array for multi
                product_titles TEXT NOT NULL,  -- JSON array
                product_brands TEXT NOT NULL,  -- JSON array
                product_photos_paths TEXT NOT NULL,  -- JSON array (local paths)
                result_photo_path TEXT,
                result_photo_url TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tryon_id INTEGER NOT NULL REFERENCES tryons(id),
                stars INTEGER NOT NULL CHECK(stars >= 1 AND stars <= 5),
                comment TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_user_photos_user_id ON user_photos(user_id);
            CREATE INDEX IF NOT EXISTS idx_tryons_user_id ON tryons(user_id);
            CREATE INDEX IF NOT EXISTS idx_ratings_tryon_id ON ratings(tryon_id);
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_user(telegram_id: int, first_name: str, last_name: str) -> int:
    """Возвращает user_id (id в таблице users)."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = c.fetchone()
        if row:
            c.execute(
                "UPDATE users SET first_name = ?, last_name = ? WHERE telegram_id = ?",
                (first_name, last_name, telegram_id)
            )
            conn.commit()
            return row[0]
        c.execute(
            "INSERT INTO users (telegram_id, first_name, last_name, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, first_name, last_name, datetime.utcnow().isoformat())
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def get_user_by_telegram_id(telegram_id: int) -> Optional[Tuple[int, str, str]]:
    """Возвращает (user_id, first_name, last_name) или None."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, first_name, last_name FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        return c.fetchone()
    finally:
        conn.close()


def save_user_photo(user_id: int, file_path: str, file_id_telegram: Optional[str] = None) -> int:
    """Сохраняет запись о фото. Возвращает user_photos.id."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO user_photos (user_id, file_path, file_id_telegram, created_at) VALUES (?, ?, ?, ?)",
            (user_id, file_path, file_id_telegram, datetime.utcnow().isoformat())
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def get_latest_user_photo_path(user_id: int) -> Optional[str]:
    """Путь к последнему загруженному фото человека для user_id."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT file_path FROM user_photos WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        row = c.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def insert_tryon(
    user_id: int,
    tryon_type: str,
    previous_tryon_id: Optional[int],
    person_photo_path: str,
    product_links: List[str],
    product_titles: List[str],
    product_brands: List[str],
    product_photos_paths: List[str],
    result_photo_path: Optional[str] = None,
    result_photo_url: Optional[str] = None,
) -> int:
    """Записывает примерку. product_* — списки (для multi). Возвращает tryon id."""
    import json
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            """INSERT INTO tryons (
                user_id, tryon_type, previous_tryon_id,
                person_photo_path, product_links, product_titles, product_brands, product_photos_paths,
                result_photo_path, result_photo_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                tryon_type,
                previous_tryon_id,
                person_photo_path,
                json.dumps(product_links, ensure_ascii=False),
                json.dumps(product_titles, ensure_ascii=False),
                json.dumps(product_brands, ensure_ascii=False),
                json.dumps(product_photos_paths, ensure_ascii=False),
                result_photo_path,
                result_photo_url,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def insert_rating(tryon_id: int, stars: int, comment: Optional[str] = None) -> int:
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO ratings (tryon_id, stars, comment, created_at) VALUES (?, ?, ?, ?)",
            (tryon_id, stars, comment or "", datetime.utcnow().isoformat())
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def get_tryon(tryon_id: int) -> Optional[dict]:
    """Одна запись примерки с полями как в БД + user first_name, last_name."""
    import json
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT t.id, t.user_id, t.tryon_type, t.previous_tryon_id,
                      t.person_photo_path, t.product_links, t.product_titles, t.product_brands,
                      t.product_photos_paths, t.result_photo_path, t.result_photo_url, t.created_at,
                      u.first_name, u.last_name, u.telegram_id
               FROM tryons t
               JOIN users u ON u.id = t.user_id
               WHERE t.id = ?""",
            (tryon_id,),
        )
        row = c.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "tryon_type": row[2],
            "previous_tryon_id": row[3],
            "person_photo_path": row[4],
            "product_links": json.loads(row[5]) if row[5] else [],
            "product_titles": json.loads(row[6]) if row[6] else [],
            "product_brands": json.loads(row[7]) if row[7] else [],
            "product_photos_paths": json.loads(row[8]) if row[8] else [],
            "result_photo_path": row[9],
            "result_photo_url": row[10],
            "created_at": row[11],
            "first_name": row[12],
            "last_name": row[13],
            "telegram_id": row[14],
        }
    finally:
        conn.close()


def get_all_tryons(limit: int = 500) -> List[dict]:
    """Список примерок для админки."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT t.id, t.user_id, t.tryon_type, t.previous_tryon_id,
                      t.person_photo_path, t.product_links, t.product_titles, t.product_brands,
                      t.product_photos_paths, t.result_photo_path, t.result_photo_url, t.created_at,
                      u.first_name, u.last_name, u.telegram_id
               FROM tryons t
               JOIN users u ON u.id = t.user_id
               ORDER BY t.id DESC
               LIMIT ?""",
            (limit,),
        )
        import json
        rows = c.fetchall()
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "tryon_type": r[2],
                "previous_tryon_id": r[3],
                "person_photo_path": r[4],
                "product_links": json.loads(r[5]) if r[5] else [],
                "product_titles": json.loads(r[6]) if r[6] else [],
                "product_brands": json.loads(r[7]) if r[7] else [],
                "product_photos_paths": json.loads(r[8]) if r[8] else [],
                "result_photo_path": r[9],
                "result_photo_url": r[10],
                "created_at": r[11],
                "first_name": r[12],
                "last_name": r[13],
                "telegram_id": r[14],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_all_users_with_photos(limit: int = 200) -> List[dict]:
    """Для админки: пользователи и путь к последнему фото."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            """SELECT u.id, u.telegram_id, u.first_name, u.last_name, u.created_at,
                      (SELECT file_path FROM user_photos WHERE user_id = u.id ORDER BY id DESC LIMIT 1)
               FROM users u
               ORDER BY u.id DESC
               LIMIT ?""",
            (limit,),
        )
        rows = c.fetchall()
        return [
            {
                "id": r[0],
                "telegram_id": r[1],
                "first_name": r[2],
                "last_name": r[3],
                "created_at": r[4],
                "last_photo_path": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_ratings_for_tryons(tryon_ids: List[int]) -> dict:
    """tryon_id -> {stars, comment}."""
    if not tryon_ids:
        return {}
    conn = get_connection()
    try:
        c = conn.cursor()
        placeholders = ",".join("?" * len(tryon_ids))
        c.execute(
            f"SELECT tryon_id, stars, comment FROM ratings WHERE tryon_id IN ({placeholders})",
            tryon_ids,
        )
        return {r[0]: {"stars": r[1], "comment": r[2]} for r in c.fetchall()}
    finally:
        conn.close()
