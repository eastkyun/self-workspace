from __future__ import annotations

import sqlite3

import pytest

from identity.database import (
    IDENTITY_SCHEMA_VERSION,
    IdentityDatabaseError,
    REQUIRED_COLUMNS,
    database_connection,
    initialize_database,
    verify_database,
)
from identity.primitives import new_uuid


NOW = "2026-08-03T00:00:00Z"


def _seed_owner(connection: sqlite3.Connection):
    user_id, workspace_id, role_id, membership_id = (new_uuid() for _ in range(4))
    connection.execute(
        "INSERT INTO users VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, NULL)",
        (user_id, f"{user_id}@example.com", "Owner", "hash", NOW, NOW),
    )
    connection.execute(
        "INSERT INTO workspaces VALUES (?, 'TEAM', 'Team', '', NULL, 'Asia/Seoul', 'ACTIVE', ?, ?, ?, NULL)",
        (workspace_id, user_id, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO roles VALUES (?, ?, 'owner', 'Owner', '', 1, 'OWNER', 'ACTIVE', ?, ?, ?)",
        (role_id, workspace_id, user_id, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
        (membership_id, workspace_id, user_id, role_id, NOW, NOW, NOW),
    )
    return user_id, workspace_id, role_id, membership_id


def test_fresh_init_has_exact_v1_tables_and_is_idempotent(tmp_path):
    path = tmp_path / "identity.db"
    initialize_database(path)
    initialize_database(path)
    verify_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == IDENTITY_SCHEMA_VERSION
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == set(REQUIRED_COLUMNS)
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v1_database_migrates_to_current_without_losing_identity_rows(tmp_path):
    from identity.database import MIGRATIONS
    from shared.migrations import migrate

    path = tmp_path / "identity.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        migrate(connection, target_version=1, migrations=MIGRATIONS)
        user_id, workspace_id, _, _ = _seed_owner(connection)
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == IDENTITY_SCHEMA_VERSION
        assert connection.execute("SELECT id FROM users").fetchone()[0] == user_id
        assert connection.execute("SELECT id FROM workspaces").fetchone()[0] == workspace_id
        assert connection.execute("SELECT COUNT(*) FROM password_reset_tokens").fetchone()[0] == 0


def test_v7_activity_log_migrates_forward_without_persisting_titles_or_urls(tmp_path):
    from identity.auth import AuthenticationService
    from identity.database import PLATFORM_V7_SCHEMA_SQL
    from identity.notifications import MemoryNotificationProvider
    from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
    from workspace_platform.app_catalog import AppCatalogService

    path = initialize_database(tmp_path / "identity.db")
    authentication = AuthenticationService(
        path, notification_provider=MemoryNotificationProvider(),
        public_origin="http://testserver", hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    credentials = authentication.signup("owner@example.com", "password", "Owner")
    AppCatalogService(path).sync(runtime_app_keys=frozenset(
        {"home", "notes", "calendar", "search", "publishing", "assets", "workspace"}
    ))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE activity_logs")
        connection.executescript(PLATFORM_V7_SCHEMA_SQL)
        connection.execute("PRAGMA user_version=7")
        connection.execute(
            "INSERT INTO activity_logs(workspace_id,actor_user_id,app_key,action,resource_type,"
            "resource_id,label,href,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (credentials.workspace_id, credentials.user_id, "notes", "notes.created", "note",
             "42", "Sensitive title", f"/w/{credentials.workspace_id}/notes?note_id=42", "{}", NOW),
        )

    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert tuple(row[1] for row in connection.execute("PRAGMA table_info(activity_logs)")) == REQUIRED_COLUMNS["activity_logs"]
        assert connection.execute(
            "SELECT app_key,action,resource_type,resource_id,metadata_json FROM activity_logs"
        ).fetchone() == ("notes", "notes.created", "note", "42", "{}")


def test_all_uuid_primary_keys_are_explicitly_not_null(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    uuid_tables = {
        "users", "workspaces", "workspace_members", "sessions",
        "workspace_invitations", "apps", "roles", "permissions",
    }
    with sqlite3.connect(path) as connection:
        for table in uuid_tables:
            id_column = next(
                row for row in connection.execute(f"PRAGMA table_info({table})")
                if row[1] == "id"
            )
            assert id_column[3] == 1


def test_active_membership_is_unique_but_left_history_is_retained(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        user_id, workspace_id, role_id, _ = _seed_owner(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
                (new_uuid(), workspace_id, user_id, role_id, NOW, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'LEFT', ?, ?, ?)",
            (new_uuid(), workspace_id, user_id, role_id, NOW, NOW, NOW),
        )


def test_database_rejects_noncanonical_uuid_text(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    value = new_uuid()
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO users VALUES (?, 'user@example.com', 'User', 'hash', 'ACTIVE', ?, ?, NULL)",
            (value.upper(), NOW, NOW),
        )


def test_role_and_membership_must_belong_to_same_workspace(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        user_id, _, role_id, _ = _seed_owner(connection)
        other_workspace = new_uuid()
        connection.execute(
            "INSERT INTO workspaces VALUES (?, 'TEAM', 'Other', '', NULL, 'Asia/Seoul', 'DELETED', ?, ?, ?, ?)",
            (other_workspace, user_id, NOW, NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
                (new_uuid(), other_workspace, user_id, role_id, NOW, NOW, NOW),
            )


def test_last_active_owner_cannot_be_removed(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _, _, _, membership_id = _seed_owner(connection)
        with pytest.raises(sqlite3.IntegrityError, match="retain an active owner"):
            connection.execute(
                "UPDATE workspace_members SET status='LEFT' WHERE id=?", (membership_id,)
            )


def test_sole_owner_cannot_be_disabled_and_successful_disable_revokes_sessions(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        user_id, workspace_id, role_id, _ = _seed_owner(connection)
        session_id = new_uuid()
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, 'token', 'csrf', ?, ?, ?, NULL, ?)",
            (session_id, user_id, workspace_id, NOW, NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="sole active owner"):
            connection.execute(
                "UPDATE users SET status='DISABLED', disabled_at=?, updated_at=? WHERE id=?",
                (NOW, NOW, user_id),
            )

        other_user, other_membership = new_uuid(), new_uuid()
        connection.execute(
            "INSERT INTO users VALUES (?, ?, 'Other', 'hash', 'ACTIVE', ?, ?, NULL)",
            (other_user, f"{other_user}@example.com", NOW, NOW),
        )
        connection.execute(
            "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (other_membership, workspace_id, other_user, role_id, NOW, NOW, NOW),
        )
        connection.execute(
            "UPDATE users SET status='DISABLED', disabled_at=?, updated_at=? WHERE id=?",
            (NOW, NOW, user_id),
        )
        assert connection.execute(
            "SELECT revoked_at FROM sessions WHERE id=?", (session_id,)
        ).fetchone()[0] == NOW


def test_database_connection_owns_immediate_transaction_and_blocks_early_commit(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    user_id = new_uuid()
    workspace_id = new_uuid()
    with pytest.raises(IdentityDatabaseError, match="no active owner"):
        with database_connection(path) as transaction:
            assert transaction.in_transaction is True
            assert not hasattr(transaction, "commit")
            assert not hasattr(transaction, "rollback")
            assert not hasattr(transaction, "set_authorizer")
            cursor = transaction.execute(
                "INSERT INTO users VALUES (?, ?, 'User', 'hash', 'ACTIVE', ?, ?, NULL)",
                (user_id, f"{user_id}@example.com", NOW, NOW),
            )
            assert not hasattr(cursor, "connection")
            assert not hasattr(iter(cursor), "connection")
            transaction.execute(
                "INSERT INTO workspaces VALUES (?, 'TEAM', 'Bad', '', NULL, 'Asia/Seoul', 'ACTIVE', ?, ?, ?, NULL)",
                (workspace_id, user_id, NOW, NOW),
            )
            with pytest.raises(sqlite3.DatabaseError):
                transaction.execute("COMMIT")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone() is None
    with database_connection(path) as transaction:
        transaction.execute(
            "INSERT INTO users VALUES (?, ?, 'User', 'hash', 'ACTIVE', ?, ?, NULL)",
            (user_id, f"{user_id}@example.com", NOW, NOW),
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() == (1,)


def test_owner_override_is_forbidden_and_audit_is_append_only(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        user_id, workspace_id, _, membership_id = _seed_owner(connection)
        app_id, permission_id = new_uuid(), new_uuid()
        connection.execute(
            "INSERT INTO apps VALUES (?, 'notes', 'Notes', '', 'note', 'work', 1, '1', 'ACTIVE', 'sum', ?, ?)",
            (app_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO permissions VALUES (?, 'notes.read', ?, 'Read', '', 0, ?, ?)",
            (permission_id, app_id, NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="owner memberships"):
            connection.execute(
                "INSERT INTO member_permission_overrides VALUES (?, ?, ?, 'DENY', ?, ?, ?)",
                (workspace_id, membership_id, permission_id, user_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO audit_logs VALUES (?, ?, 'workspace.created', 'workspace', ?, '{}', ?)",
            (workspace_id, user_id, workspace_id, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_logs")


def test_owner_only_permission_rejects_admin_custom_and_member_override(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        owner_id, workspace_id, owner_role_id, _ = _seed_owner(connection)
        member_id, membership_id = new_uuid(), new_uuid()
        connection.execute(
            "INSERT INTO users VALUES (?, ?, 'Member', 'hash', 'ACTIVE', ?, ?, NULL)",
            (member_id, f"{member_id}@example.com", NOW, NOW),
        )
        admin_role_id, custom_role_id = new_uuid(), new_uuid()
        connection.execute(
            "INSERT INTO roles VALUES (?, ?, 'admin', 'Admin', '', 1, 'ADMIN', 'ACTIVE', ?, ?, ?)",
            (admin_role_id, workspace_id, owner_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO roles VALUES (?, ?, 'custom', 'Custom', '', 0, NULL, 'ACTIVE', ?, ?, ?)",
            (custom_role_id, workspace_id, owner_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (membership_id, workspace_id, member_id, admin_role_id, NOW, NOW, NOW),
        )
        app_id, permission_id = new_uuid(), new_uuid()
        connection.execute(
            "INSERT INTO apps VALUES (?, 'admin', 'Admin', '', 'lock', 'system', 1, '1', 'ACTIVE', 'sum', ?, ?)",
            (app_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO permissions VALUES (?, 'workspace.owner', ?, 'Owner', '', 1, ?, ?)",
            (permission_id, app_id, NOW, NOW),
        )
        for role_id in (admin_role_id, custom_role_id):
            with pytest.raises(sqlite3.IntegrityError, match="built-in Owner"):
                connection.execute(
                    "INSERT INTO role_permissions VALUES (?, ?, 'ALLOW', ?)",
                    (role_id, permission_id, NOW),
                )
        with pytest.raises(sqlite3.IntegrityError, match="member overrides"):
            connection.execute(
                "INSERT INTO member_permission_overrides VALUES (?, ?, ?, 'ALLOW', ?, ?, ?)",
                (workspace_id, membership_id, permission_id, owner_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO role_permissions VALUES (?, ?, 'ALLOW', ?)",
            (owner_role_id, permission_id, NOW),
        )


def test_verification_rejects_ownerless_active_workspace(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        user_id = new_uuid()
        connection.execute(
            "INSERT INTO users VALUES (?, ?, 'User', 'hash', 'ACTIVE', ?, ?, NULL)",
            (user_id, f"{user_id}@example.com", NOW, NOW),
        )
        connection.execute(
            "INSERT INTO workspaces VALUES (?, 'TEAM', 'Bad', '', NULL, 'Asia/Seoul', 'ACTIVE', ?, ?, ?, NULL)",
            (new_uuid(), user_id, NOW, NOW),
        )
    with pytest.raises(IdentityDatabaseError, match="no active owner"):
        verify_database(path)


def test_builtin_role_contract_is_enforced(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        user_id, workspace_id, _, _ = _seed_owner(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO roles VALUES (?, ?, 'bad', 'Bad', '', 0, 'ADMIN', 'ACTIVE', ?, ?, ?)",
                (new_uuid(), workspace_id, user_id, NOW, NOW),
            )


def test_verification_rejects_extra_sql_schema_objects(tmp_path):
    path = initialize_database(tmp_path / "identity.db")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE VIEW unexpected_identity_view AS SELECT id FROM users")
    with pytest.raises(IdentityDatabaseError, match="object set"):
        verify_database(path)


@pytest.mark.parametrize(
    ("email", "created_at", "message"),
    [
        ("User@Example.com", NOW, "noncanonical normalized email"),
        ("user@example.com", "2026-08-03 00:00:00", "UTC RFC3339"),
    ],
)
def test_verification_rejects_noncanonical_identity_values(
    tmp_path, email, created_at, message
):
    path = initialize_database(tmp_path / f"{message}.db")
    user_id = new_uuid()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO users VALUES (?, ?, 'User', 'hash', 'ACTIVE', ?, ?, NULL)",
            (user_id, email, created_at, NOW),
        )
    with pytest.raises(IdentityDatabaseError, match=message):
        verify_database(path)
