"""PostgreSQL: pool de conexões, migrações e consultas."""

import logging
import uuid
from datetime import timedelta
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# Schema próprio: no Supabase, o que fica em "public" é exposto pela API REST com a chave pública.
SCHEMA = "arcade"
# Namespace fixo: a mesma partida do publisher.py sempre gera o mesmo UUID, e o UNIQUE descarta a repetição.
MANUAL_NS = uuid.UUID("5b0e7c52-8f3a-4a61-9d0c-2f6a3c1e9b47")

log = logging.getLogger("backend")
pool: ConnectionPool | None = None


def connect(url: str) -> None:
    global pool
    kwargs = {"row_factory": dict_row, "prepare_threshold": None}  # None: compatível com poolers (PgBouncer/Supavisor)
    if "sslmode" not in conninfo_to_dict(url):
        kwargs["sslmode"] = "require"  # o padrão do libpq ("prefer") aceitaria cair para conexão sem TLS
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=5,
        open=False,
        check=ConnectionPool.check_connection,  # descarta conexões que o provedor derrubou por ociosidade
        kwargs=kwargs,
    )
    pool.open(wait=True, timeout=30)


def close() -> None:
    if pool:
        pool.close()


def migrate() -> None:
    """Aplica, em ordem, os arquivos migrations/*.sql que ainda não rodaram neste banco."""
    with pool.connection() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {row["version"] for row in conn.execute(f"SELECT version FROM {SCHEMA}.schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in applied:
                continue
            with conn.transaction():  # DDL no Postgres é transacional: a migração entra inteira ou nada
                conn.execute(f"SET LOCAL search_path TO {SCHEMA}")  # os .sql não precisam de prefixo
                conn.execute(path.read_text())
                conn.execute(f"INSERT INTO {SCHEMA}.schema_migrations (version) VALUES (%s)", (path.stem,))
            log.info("Migração aplicada: %s", path.name)


def _upsert_device(conn, device_id: str) -> int:
    return conn.execute(
        "INSERT INTO arcade.devices (device_id, last_seen_at) VALUES (%s, now()) "
        "ON CONFLICT (device_id) DO UPDATE SET last_seen_at = now() RETURNING id",
        (device_id,),
    ).fetchone()["id"]


def _upsert_game(conn, system: str, rom: str, title: str) -> int:
    # Se o título não mudou, o trigger não mexe no updated_at; o RETURNING devolve o id da linha existente.
    return conn.execute(
        "INSERT INTO arcade.games AS g (system, rom, title) VALUES (%s, %s, %s) "
        "ON CONFLICT (system, rom) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        (system, rom, title),
    ).fetchone()["id"]


def insert_manual_score(s: dict) -> bool:
    """Grava uma partida do contrato arcade/scores/submit. Devolve False se ela já existia."""
    params = {**s, "uuid": uuid.uuid5(MANUAL_NS, f"{s['device_id']}|{s['player_tag']}|{s['timestamp']}")}
    with pool.connection() as conn:  # commit ao sair do bloco, rollback se der erro
        params["device_pk"] = _upsert_device(conn, s["device_id"])
        params["game_pk"] = _upsert_game(conn, "manual", s["game"], s["game"])
        play = conn.execute(
            "INSERT INTO arcade.plays (uuid, user_id, device_id, game_id, started_at, ended_at) "
            "VALUES (%(uuid)s, (SELECT id FROM arcade.users WHERE player_tag = %(player_tag)s), %(device_pk)s, "
            "        %(game_pk)s, to_timestamp(%(timestamp)s), to_timestamp(%(timestamp)s)) "
            "ON CONFLICT (uuid) DO NOTHING RETURNING id",
            params,
        ).fetchone()
        if play is None:
            return False
        conn.execute(
            "INSERT INTO arcade.scores (play_id, player_tag, score, stage_reached) VALUES (%s, %s, %s, %s)",
            (play["id"], s["player_tag"], s["score"], s["stage_reached"]),
        )
        return True


def record_play(device_id: str, e: dict) -> bool:
    """Grava início ou fim de partida vindos do Batocera. Devolve True se gravou um score novo.

    Os dois eventos trazem todos os dados da partida, então o "stop" funciona mesmo se o "start"
    se perdeu, e uma reentrega do QoS 1 só regrava os mesmos valores.
    """
    with pool.connection() as conn:
        play_id = conn.execute(
            """
            INSERT INTO arcade.plays AS p (uuid, user_id, device_id, game_id, started_at, ended_at)
            VALUES (%(play_uuid)s, (SELECT id FROM arcade.users WHERE username = %(username)s),
                    %(device_pk)s, %(game_pk)s, to_timestamp(%(started_at)s), to_timestamp(%(ended_at)s))
            ON CONFLICT (uuid) DO UPDATE SET ended_at = coalesce(EXCLUDED.ended_at, p.ended_at)
            RETURNING id
            """,
            {
                **e,
                "device_pk": _upsert_device(conn, device_id),
                "game_pk": _upsert_game(conn, e["system"], e["rom"], e["title"]),
            },
        ).fetchone()["id"]
        if e.get("score") is None:
            return False
        return conn.execute(
            # Convidado aparece no ranking como "???".
            "INSERT INTO arcade.scores (play_id, player_tag, score, stage_reached) "
            "SELECT %(play_id)s, coalesce((SELECT player_tag FROM arcade.users WHERE username = %(username)s), '???'), "
            "       %(score)s, %(stage_reached)s "
            "ON CONFLICT (play_id) DO NOTHING",
            {**e, "play_id": play_id, "stage_reached": e.get("stage_reached") or 0},
        ).rowcount > 0


def device_login(device_id: str, username: str, ts: int) -> dict | None:
    """Abre a sessão do jogador no aparelho, encerrando a anterior. None se o usuário não existe."""
    with pool.connection() as conn:
        user = conn.execute(
            "SELECT id, username, player_tag FROM arcade.users WHERE username = %s", (username,)
        ).fetchone()
        if user is None:
            return None
        device_pk = _upsert_device(conn, device_id)
        _close_device_session(conn, device_pk, ts)
        conn.execute(
            "INSERT INTO arcade.device_sessions (device_id, user_id, started_at) VALUES (%s, %s, to_timestamp(%s))",
            (device_pk, user["id"], ts),
        )
        return user


def device_logout(device_id: str, ts: int) -> None:
    with pool.connection() as conn:
        _close_device_session(conn, _upsert_device(conn, device_id), ts)


def _close_device_session(conn, device_pk: int, ts: int) -> None:
    # greatest(): um relógio fora de ordem não pode gerar ended_at antes do started_at.
    conn.execute(
        "UPDATE arcade.device_sessions SET ended_at = greatest(to_timestamp(%s), started_at) "
        "WHERE device_id = %s AND ended_at IS NULL",
        (ts, device_pk),
    )


def leaderboard(game: str | None = None, limit: int = 10) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT COALESCE(u.player_tag, s.player_tag) AS player_tag,  -- conta logada usa a tag atual do perfil
                   g.title AS game, s.score, s.stage_reached,
                   extract(epoch FROM p.ended_at)::bigint AS timestamp
              FROM arcade.scores s
              JOIN arcade.plays p      ON p.id = s.play_id
              JOIN arcade.games g      ON g.id = p.game_id
              LEFT JOIN arcade.users u ON u.id = p.user_id
             WHERE %(game)s::text IS NULL OR g.title = %(game)s
             ORDER BY s.score DESC, p.ended_at  -- empate: quem fez primeiro fica na frente
             LIMIT %(limit)s
            """,
            {"game": game or None, "limit": limit},
        ).fetchall()


def create_user(username: str, player_tag: str, password_hash: str) -> dict:
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO arcade.users (username, player_tag, password_hash) VALUES (%s, %s, %s) "
            "RETURNING id, username, player_tag",
            (username, player_tag, password_hash),
        ).fetchone()


def user_by_username(username: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT id, username, player_tag, password_hash FROM arcade.users WHERE username = %s", (username,)
        ).fetchone()


def create_token(user_id: int, token_hash: bytes, ttl: timedelta) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM arcade.auth_tokens WHERE expires_at < now()")  # faxina das sessões vencidas
        conn.execute(
            "INSERT INTO arcade.auth_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, now() + %s)",
            (user_id, token_hash, ttl),
        )


def user_by_token(token_hash: bytes) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT u.id, u.username, u.player_tag FROM arcade.auth_tokens t "
            "JOIN arcade.users u ON u.id = t.user_id WHERE t.token_hash = %s AND t.expires_at > now()",
            (token_hash,),
        ).fetchone()


def delete_token(token_hash: bytes) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM arcade.auth_tokens WHERE token_hash = %s", (token_hash,))


def profile(user_id: int) -> dict:
    params = {"uid": user_id}
    with pool.connection() as conn:
        result = conn.execute(
            """
            SELECT u.username, u.player_tag, extract(epoch FROM u.created_at)::bigint AS created_at,
                   count(p.id) AS plays, count(DISTINCT p.game_id) AS games,
                   coalesce(sum(extract(epoch FROM p.ended_at - p.started_at)), 0)::bigint AS seconds_played
              FROM arcade.users u
              LEFT JOIN arcade.plays p ON p.user_id = u.id
             WHERE u.id = %(uid)s
             GROUP BY u.id
            """,
            params,
        ).fetchone()
        # Posição = lugar do melhor score do jogador no ranking daquele jogo (o mesmo de ?game= no leaderboard).
        result["records"] = conn.execute(
            """
            WITH mine AS (
                SELECT p.game_id, count(*) AS plays, max(s.score) AS best, max(p.started_at) AS last_played
                  FROM arcade.plays p
                  LEFT JOIN arcade.scores s ON s.play_id = p.id
                 WHERE p.user_id = %(uid)s
                 GROUP BY p.game_id
            )
            SELECT g.title AS game, m.plays, m.best,
                   CASE WHEN m.best IS NOT NULL THEN
                       1 + (SELECT count(*) FROM arcade.scores s JOIN arcade.plays p ON p.id = s.play_id
                             WHERE p.game_id = m.game_id AND s.score > m.best)
                   END AS position
              FROM mine m
              JOIN arcade.games g ON g.id = m.game_id
             ORDER BY m.last_played DESC
            """,
            params,
        ).fetchall()
        result["recent"] = conn.execute(
            """
            SELECT g.title AS game, extract(epoch FROM p.started_at)::bigint AS started_at,
                   extract(epoch FROM p.ended_at - p.started_at)::bigint AS seconds, s.score
              FROM arcade.plays p
              JOIN arcade.games g ON g.id = p.game_id
              LEFT JOIN arcade.scores s ON s.play_id = p.id
             WHERE p.user_id = %(uid)s
             ORDER BY p.started_at DESC
             LIMIT 20
            """,
            params,
        ).fetchall()
    return result


# ---------- conquistas (RetroAchievements) ----------


def record_unlock(e: dict) -> bool:
    """Põe a conquista no perfil de quem estava logado. False se o jogador já a tinha (o encore repete)."""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO arcade.achievements (id, title, description) VALUES (%(achievement_id)s, %(title)s, %(description)s) "
            "ON CONFLICT (id) DO NOTHING",
            e,
        )
        return conn.execute(
            """
            INSERT INTO arcade.user_achievements (user_id, achievement_id, play_id, unlocked_at)
            SELECT u.id, %(achievement_id)s, (SELECT id FROM arcade.plays WHERE uuid = %(play_uuid)s),
                   to_timestamp(%(unlocked_at)s)
              FROM arcade.users u
             WHERE u.username = %(username)s
            ON CONFLICT (user_id, achievement_id) DO NOTHING
            """,
            e,
        ).rowcount > 0


def ra_hashes_stale(console_id: int, max_age: timedelta) -> bool:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT coalesce(max(created_at) < now() - %s, true) AS stale FROM arcade.ra_hashes WHERE console_id = %s",
            (max_age, console_id),
        ).fetchone()["stale"]


def replace_ra_hashes(console_id: int, rows: list[tuple[str, int]]) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM arcade.ra_hashes WHERE console_id = %s", (console_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO arcade.ra_hashes (md5, ra_game_id, console_id) VALUES (%s, %s, %s) ON CONFLICT (md5) DO NOTHING",
                [(md5, game_id, console_id) for md5, game_id in rows],
            )


def ra_game_for_hash(md5: str) -> int | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT ra_game_id FROM arcade.ra_hashes WHERE md5 = %s", (md5,)).fetchone()
        return row and row["ra_game_id"]


def game_ra_id(system: str, rom: str) -> int | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT ra_game_id FROM arcade.games WHERE system = %s AND rom = %s", (system, rom)).fetchone()
        return row and row["ra_game_id"]


def set_game_ra(system: str, rom: str, ra_game_id: int, icon: str | None) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE arcade.games SET ra_game_id = %s, ra_icon = %s WHERE system = %s AND rom = %s",
            (ra_game_id, icon, system, rom),
        )


def achievement_ra_game(achievement_id: int) -> int | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT ra_game_id FROM arcade.achievements WHERE id = %s", (achievement_id,)).fetchone()
        return row and row["ra_game_id"]


def set_achievement_game(achievement_id: int, ra_game_id: int, points: int | None) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE arcade.achievements SET ra_game_id = %s, points = coalesce(points, %s) "
            "WHERE id = %s AND ra_game_id IS NULL",
            (ra_game_id, points, achievement_id),
        )


def link_play_game(play_uuid, ra_game_id: int, icon: str | None) -> None:
    """Liga ao RA o jogo da partida onde houve um desbloqueio, se ele ainda não estiver ligado."""
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE arcade.games g
               SET ra_game_id = %(ra)s,
                   ra_icon = coalesce(%(icon)s, (SELECT ra_icon FROM arcade.games
                                                  WHERE ra_game_id = %(ra)s AND ra_icon IS NOT NULL LIMIT 1))
              FROM arcade.plays p
             WHERE p.uuid = %(play)s AND g.id = p.game_id AND g.ra_game_id IS NULL
            """,
            {"ra": ra_game_id, "icon": icon, "play": play_uuid},
        )


def upsert_achievements(ra_game_id: int, rows: list[tuple]) -> None:
    """rows: (id, título, descrição, pontos, ícone, ordem) vindos da API do RA."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO arcade.achievements (id, ra_game_id, title, description, points, badge, display_order)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET ra_game_id = EXCLUDED.ra_game_id, title = EXCLUDED.title,
                description = EXCLUDED.description, points = EXCLUDED.points, badge = EXCLUDED.badge,
                display_order = EXCLUDED.display_order
            """,
            [(r[0], ra_game_id, *r[1:]) for r in rows],
        )


def user_achievements(user_id: int) -> list[dict]:
    """Vitrine estilo Steam: todas as conquistas dos jogos que o jogador já jogou, desbloqueadas ou não."""
    params = {"uid": user_id}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            WITH my_games AS (
                SELECT DISTINCT ON (g.ra_game_id) g.ra_game_id, g.title, g.ra_icon
                  FROM arcade.plays p JOIN arcade.games g ON g.id = p.game_id
                 WHERE p.user_id = %(uid)s AND g.ra_game_id IS NOT NULL
                 ORDER BY g.ra_game_id, p.started_at DESC
            ),
            players AS (  -- perfis que já jogaram cada jogo: base da porcentagem de jogadores
                SELECT g.ra_game_id, count(DISTINCT p.user_id) AS n
                  FROM arcade.plays p JOIN arcade.games g ON g.id = p.game_id
                 WHERE p.user_id IS NOT NULL AND g.ra_game_id IS NOT NULL
                 GROUP BY g.ra_game_id
            )
            SELECT mg.ra_game_id, mg.title AS game, mg.ra_icon AS icon,
                   a.id, a.title, a.description, a.points, a.badge,
                   extract(epoch FROM ua.unlocked_at)::bigint AS unlocked_at,
                   least(100, round(100.0 * (SELECT count(*) FROM arcade.user_achievements x
                                              WHERE x.achievement_id = a.id) / greatest(pl.n, 1)))::int AS rarity
              FROM my_games mg
              JOIN arcade.achievements a ON a.ra_game_id = mg.ra_game_id
              LEFT JOIN arcade.user_achievements ua ON ua.achievement_id = a.id AND ua.user_id = %(uid)s
              LEFT JOIN players pl ON pl.ra_game_id = mg.ra_game_id
             ORDER BY mg.title, a.display_order NULLS LAST, a.id
            """,
            params,
        ).fetchall()
        # Sem a chave da API do RA não há lista completa: mostra ao menos o que foi desbloqueado.
        loose = conn.execute(
            """
            SELECT NULL::bigint AS ra_game_id, 'Outras conquistas' AS game, NULL AS icon,
                   a.id, a.title, a.description, a.points, a.badge,
                   extract(epoch FROM ua.unlocked_at)::bigint AS unlocked_at, NULL::int AS rarity
              FROM arcade.user_achievements ua JOIN arcade.achievements a ON a.id = ua.achievement_id
             WHERE ua.user_id = %(uid)s AND a.ra_game_id IS NULL
             ORDER BY ua.unlocked_at
            """,
            params,
        ).fetchall()
    games: dict = {}
    for row in rows + loose:
        game = games.setdefault(row["ra_game_id"], {
            "ra_game_id": row["ra_game_id"], "game": row["game"], "icon": row["icon"], "achievements": [],
        })
        game["achievements"].append({k: row[k] for k in ("id", "title", "description", "points", "badge", "unlocked_at", "rarity")})
    for game in games.values():
        unlocked = [a for a in game["achievements"] if a["unlocked_at"]]
        game |= {
            "total": len(game["achievements"]),
            "unlocked": len(unlocked),
            "points_total": sum(a["points"] or 0 for a in game["achievements"]),
            "points_unlocked": sum(a["points"] or 0 for a in unlocked),
        }
    return list(games.values())


# ---------- saves na nuvem ----------


def upsert_save(device_id: str, s: dict, data: bytes) -> bool:
    """Guarda a versão enviada se for a mais nova (a última gravação vence). False se o usuário não existe
    ou se a nuvem já tinha esta versão ou uma mais nova."""
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO arcade.saves AS sv (user_id, system, filename, sha256, size, data, modified_at, device_id)
            SELECT u.id, %(system)s, %(filename)s, %(sha256)s, %(size)s, %(data)s, to_timestamp(%(modified_at)s), %(device_pk)s
              FROM arcade.users u
             WHERE u.username = %(username)s
            ON CONFLICT (user_id, system, filename) DO UPDATE
               SET sha256 = EXCLUDED.sha256, size = EXCLUDED.size, data = EXCLUDED.data,
                   modified_at = EXCLUDED.modified_at, device_id = EXCLUDED.device_id
             WHERE sv.modified_at <= EXCLUDED.modified_at AND sv.sha256 <> EXCLUDED.sha256
            """,
            {**s, "size": len(data), "data": data, "device_pk": _upsert_device(conn, device_id)},
        ).rowcount > 0


def save_index(username: str) -> list[dict]:
    """Saves do jogador sem o conteúdo, para comparar com a lista que o Pi mandou."""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT sv.id, sv.system, sv.filename, sv.sha256, extract(epoch FROM sv.modified_at)::bigint AS modified_at "
            "FROM arcade.saves sv JOIN arcade.users u ON u.id = sv.user_id WHERE u.username = %s",
            (username,),
        ).fetchall()


def save_data(save_id: int) -> bytes:
    with pool.connection() as conn:
        return conn.execute("SELECT data FROM arcade.saves WHERE id = %s", (save_id,)).fetchone()["data"]


def user_saves(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT id, system, filename, size, extract(epoch FROM modified_at)::bigint AS modified_at "
            "FROM arcade.saves WHERE user_id = %s ORDER BY modified_at DESC",
            (user_id,),
        ).fetchall()


def user_save_file(user_id: int, save_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT filename, data FROM arcade.saves WHERE id = %s AND user_id = %s", (save_id, user_id)
        ).fetchone()


# ---------- ESP32 (telemetria) ----------


def record_telemetry(device_id: str, temperature: float, humidity: float | None) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO arcade.telemetry (device_id, temperature, humidity) VALUES (%s, %s, %s)",
            (_upsert_device(conn, device_id), temperature, humidity),
        )


def telemetry_history(device_id: str | None, hours: int) -> list[dict]:
    """Leituras das últimas horas; sem device_id, as do aparelho que mandou a leitura mais recente."""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT d.device_id, extract(epoch FROM t.measured_at)::bigint AS ts,
                   t.temperature::float8 AS temperature, t.humidity::float8 AS humidity
              FROM arcade.telemetry t
              JOIN arcade.devices d ON d.id = t.device_id
             WHERE t.measured_at > now() - make_interval(hours => %(hours)s)
               AND t.device_id = CASE WHEN %(device)s::text IS NULL
                                      THEN (SELECT device_id FROM arcade.telemetry ORDER BY measured_at DESC LIMIT 1)
                                      ELSE (SELECT id FROM arcade.devices WHERE device_id = %(device)s) END
             ORDER BY t.measured_at
            """,
            {"device": device_id, "hours": hours},
        ).fetchall()
