-- Conquistas do RetroAchievements por perfil (estilo Steam).
-- O arcade usa uma única conta RA em modo "encore"; quem recebe cada conquista é o perfil logado no Pi.

-- Jogo correspondente no RetroAchievements, descoberto pelo hash da ROM.
ALTER TABLE games ADD COLUMN ra_game_id BIGINT;
ALTER TABLE games ADD COLUMN ra_icon TEXT;  -- caminho do ícone em media.retroachievements.org

-- Cache da lista oficial de hashes do RA (API_GetGameList com h=1); a API pede cache agressivo.
CREATE TABLE ra_hashes (
    md5        TEXT        PRIMARY KEY,
    ra_game_id BIGINT      NOT NULL,
    console_id INTEGER     NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ra_hashes_console_id_idx ON ra_hashes (console_id);

CREATE TABLE achievements (
    id            BIGINT      PRIMARY KEY,  -- ID da conquista no RetroAchievements
    ra_game_id    BIGINT,
    title         TEXT        NOT NULL,
    description   TEXT        NOT NULL DEFAULT '',
    points        INTEGER,
    badge         TEXT,  -- ícone: media.retroachievements.org/Badge/<badge>.png (e <badge>_lock.png)
    display_order INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX achievements_ra_game_id_idx ON achievements (ra_game_id);

CREATE TABLE user_achievements (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    achievement_id BIGINT      NOT NULL REFERENCES achievements (id),
    play_id        BIGINT      REFERENCES plays (id) ON DELETE SET NULL,
    unlocked_at    TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- O modo encore repete o desbloqueio a cada partida: vale a primeira vez de cada jogador.
    UNIQUE (user_id, achievement_id)
);
CREATE INDEX user_achievements_achievement_id_idx ON user_achievements (achievement_id);

CREATE TRIGGER achievements_updated_at BEFORE UPDATE ON achievements FOR EACH ROW EXECUTE FUNCTION set_updated_at();
