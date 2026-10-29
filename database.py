import aiosqlite
import os
from config import DATABASE_PATH


async def init_db():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id  TEXT NOT NULL,
            key       TEXT NOT NULL,
            value     TEXT NOT NULL,
            PRIMARY KEY (guild_id, key)
        );

        CREATE TABLE IF NOT EXISTS ticket_categories (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      TEXT NOT NULL,
            name          TEXT NOT NULL,
            emoji         TEXT,
            button_style  INTEGER DEFAULT 1,
            label         TEXT
        );

        CREATE TABLE IF NOT EXISTS ticket_panels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            embed_title TEXT DEFAULT 'Open a Ticket',
            embed_desc  TEXT DEFAULT 'Select a category below to open a support ticket. Staff will assist you as soon as possible.',
            embed_image TEXT,
            embed_thumbnail TEXT
        );

        CREATE TABLE IF NOT EXISTS ticket_threads (
            guild_id   TEXT NOT NULL,
            thread_id  TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            category   TEXT,
            status     TEXT DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS application_templates (
            guild_id              TEXT NOT NULL,
            app_name              TEXT NOT NULL,
            submission_channel_id TEXT,
            approved_channel_id   TEXT,
            denied_channel_id     TEXT,
            review_role_id        TEXT,
            approve_role_id       TEXT,
            submission_title      TEXT,
            submission_image      TEXT,
            submission_thumbnail  TEXT,
            PRIMARY KEY (guild_id, app_name)
        );

        CREATE TABLE IF NOT EXISTS application_questions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            app_name   TEXT NOT NULL,
            question   TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS application_submissions (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id               TEXT NOT NULL,
            app_name               TEXT NOT NULL,
            user_id                TEXT NOT NULL,
            user_name              TEXT,
            answers_json           TEXT,
            submission_message_id  TEXT,
            submission_channel_id  TEXT,
            status                 TEXT DEFAULT 'pending',
            submitted_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS autorole_config (
            guild_id TEXT PRIMARY KEY,
            enabled  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS autorole_roles (
            guild_id TEXT NOT NULL,
            role_id  TEXT NOT NULL,
            PRIMARY KEY (guild_id, role_id)
        );

        CREATE TABLE IF NOT EXISTS warnings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            moderator  TEXT NOT NULL,
            reason     TEXT,
            timestamp  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS welcome_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER DEFAULT 0,
            channel_id  TEXT,
            message     TEXT DEFAULT 'Welcome {user_mention} to {server_name}! Enjoy your stay.',
            goodbye_enabled INTEGER DEFAULT 0,
            goodbye_channel_id TEXT,
            goodbye_message TEXT DEFAULT '{user_mention} has left {server_name}. Goodbye!'
        );

        CREATE TABLE IF NOT EXISTS reaction_roles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            emoji       TEXT NOT NULL,
            role_id     TEXT NOT NULL,
            mode        TEXT DEFAULT 'toggle'
        );

        CREATE TABLE IF NOT EXISTS polls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            question    TEXT NOT NULL,
            options     TEXT NOT NULL,
            creator_id  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            ends_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS starred_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            content     TEXT,
            author_id   TEXT NOT NULL,
            author_name TEXT,
            stars       INTEGER DEFAULT 1,
            starboard_msg_id TEXT
        );

        CREATE TABLE IF NOT EXISTS giveaways (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        TEXT NOT NULL,
            channel_id      TEXT NOT NULL,
            message_id      TEXT NOT NULL,
            prize           TEXT NOT NULL,
            winners_count   INTEGER DEFAULT 1,
            ends_at         TEXT NOT NULL,
            required_role_id TEXT,
            host_id         TEXT NOT NULL,
            ended           INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL,
            user_id     TEXT NOT NULL,
            PRIMARY KEY (giveaway_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS leveling (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            level    INTEGER DEFAULT 1,
            xp       INTEGER DEFAULT 0,
            UNIQUE (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS partnerships (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         TEXT NOT NULL,
            partner_guild_id TEXT,
            partner_name     TEXT NOT NULL,
            invite           TEXT,
            member_count     INTEGER,
            added_by         TEXT,
            created_at       REAL
        );

        CREATE TABLE IF NOT EXISTS partner_tickets (
            channel_id        TEXT PRIMARY KEY,
            guild_id          TEXT NOT NULL,
            applicant_id      TEXT NOT NULL,
            stage             TEXT DEFAULT 'awaiting_ad',
            ad_text           TEXT,
            invite            TEXT,
            code              TEXT,
            member_count      INTEGER,
            invite_guild_name TEXT,
            proof_url         TEXT,
            proof_name        TEXT,
            opened_at         TEXT
        );

        CREATE TABLE IF NOT EXISTS counting_stats (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            count    INTEGER DEFAULT 0,
            UNIQUE (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS community_stats (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         TEXT NOT NULL,
            boost_count      INTEGER DEFAULT 0,
            member_milestone INTEGER DEFAULT 0,
            last_updated     REAL,
            UNIQUE (guild_id)
        );

        CREATE TABLE IF NOT EXISTS automod_offenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            count        INTEGER DEFAULT 0,
            last_offense REAL,
            UNIQUE (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS mod_actions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            action    TEXT NOT NULL,
            reason    TEXT,
            mod_id    TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS softmutes (
            guild_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            until    TEXT,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS ticket_stats (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            category TEXT NOT NULL,
            opened   INTEGER DEFAULT 0,
            closed   INTEGER DEFAULT 0,
            UNIQUE (guild_id, user_id, category)
        );

        CREATE TABLE IF NOT EXISTS ticket_category_questions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            question    TEXT NOT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS staff_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            staff_id   TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS whitelist_apps (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      TEXT NOT NULL,
            user_id       TEXT NOT NULL,
            user_name     TEXT,
            mc_username   TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            message_id    TEXT,
            reviewer_id   TEXT,
            reviewed_at   TEXT,
            created_at    TEXT NOT NULL,
            UNIQUE (guild_id, mc_username)
        );

        CREATE TABLE IF NOT EXISTS chatbridge_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            source      TEXT NOT NULL,
            username    TEXT,
            message     TEXT NOT NULL,
            event_type  TEXT NOT NULL DEFAULT 'message',
            created_at  REAL NOT NULL,
            posted      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS console_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            command     TEXT NOT NULL,
            output      TEXT,
            success     INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS console_relay (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            line        TEXT NOT NULL,
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS console_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            command     TEXT NOT NULL,
            executor    TEXT,
            executor_id TEXT,
            status      TEXT NOT NULL DEFAULT 'pending',
            output      TEXT,
            success     INTEGER,
            created_at  REAL NOT NULL,
            completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS mc_nodes (
            guild_id   TEXT PRIMARY KEY,
            token      TEXT NOT NULL,
            name       TEXT,
            last_seen  REAL
        );

        CREATE TABLE IF NOT EXISTS whitelist_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            command     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            result      TEXT,
            created_at  TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS link_codes (
            code        TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            uuid        TEXT NOT NULL,
            player_name TEXT,
            created_at  REAL NOT NULL,
            expires_at  REAL NOT NULL,
            used        INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS mc_links (
            guild_id        TEXT NOT NULL,
            uuid            TEXT NOT NULL,
            discord_id      TEXT NOT NULL,
            linked_at       REAL NOT NULL,
            sync_groups     TEXT,
            PRIMARY KEY (guild_id, uuid),
            UNIQUE (guild_id, discord_id)
        );

        CREATE TABLE IF NOT EXISTS link_audit (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            kind        TEXT NOT NULL,
            uuid        TEXT,
            discord_id  TEXT,
            detail      TEXT,
            created_at  REAL NOT NULL
        );
    """)

    try:
        await db.execute("ALTER TABLE ticket_categories ADD COLUMN description TEXT")
    except Exception:
        pass

    try:
        await db.execute("ALTER TABLE ticket_categories ADD COLUMN color TEXT")
    except Exception:
        pass

    for col in ["submission_channel_id", "approved_channel_id", "denied_channel_id"]:
        try:
            await db.execute(f"ALTER TABLE application_templates ADD COLUMN {col} TEXT")
        except Exception:
            pass

    for col in ["user_name", "answers_json", "submission_message_id", "submission_channel_id"]:
        try:
            await db.execute(f"ALTER TABLE application_submissions ADD COLUMN {col} TEXT")
        except Exception:
            pass

    try:
        async with db.execute("SELECT log_channel_id FROM application_templates LIMIT 1") as cursor:
            pass
        await db.execute("UPDATE application_templates SET submission_channel_id = log_channel_id WHERE submission_channel_id IS NULL")
    except Exception:
        pass

    try:
        await db.execute("ALTER TABLE ticket_threads ADD COLUMN claimed_by TEXT")
    except Exception:
        pass

    # Linking: display name of the linked Minecraft account (added after
    # launch; mc_links rows may predate it).
    try:
        await db.execute("ALTER TABLE mc_links ADD COLUMN player_name TEXT")
    except Exception:
        pass

    try:
        await db.execute("ALTER TABLE ticket_categories ADD COLUMN forum_id TEXT")
    except Exception:
        pass

    try:
        await db.execute("ALTER TABLE ticket_categories ADD COLUMN questions TEXT")
    except Exception:
        pass

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_channels (
            channel_id    TEXT PRIMARY KEY,
            guild_id      TEXT NOT NULL,
            creator_id    TEXT NOT NULL,
            category      TEXT,
            claimed_by    TEXT,
            priority      TEXT DEFAULT 'normal',
            status        TEXT DEFAULT 'open',
            opened_at     REAL,
            closed_at     REAL,
            last_activity REAL,
            warned        INTEGER DEFAULT 0
        );
    """)

    await db.commit()
    await db.close()


async def get_db():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    return db


async def get_setting(guild_id: int, key: str, default=None):
    db = await get_db()
    try:
        async with db.execute(
            "SELECT value FROM guild_settings WHERE guild_id=? AND key=?",
            (str(guild_id), key),
        ) as cursor:
            row = await cursor.fetchone()
        return row["value"] if row else default
    finally:
        await db.close()


async def get_settings_many(guild_id: int, keys: list[str] | None = None) -> dict:
    """Fetch several guild settings in one query (all keys if none given)."""
    db = await get_db()
    try:
        if keys:
            placeholders = ",".join("?" for _ in keys)
            sql = f"SELECT key, value FROM guild_settings WHERE guild_id=? AND key IN ({placeholders})"
            params = (str(guild_id), *keys)
        else:
            sql = "SELECT key, value FROM guild_settings WHERE guild_id=?"
            params = (str(guild_id),)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        await db.close()


async def set_setting(guild_id: int, key: str, value: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?)",
            (str(guild_id), key, value),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_setting(guild_id: int, key: str):
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM guild_settings WHERE guild_id=? AND key=?",
            (str(guild_id), key),
        )
        await db.commit()
    finally:
        await db.close()
