-- Modelo base: jogadores, aparelhos, jogos, partidas e scores.

-- Mantém updated_at em dia sem depender de quem faz o UPDATE.
-- Só quando algo mudou: um UPDATE que regrava os mesmos valores não conta como modificação.
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    IF NEW IS DISTINCT FROM OLD THEN
        NEW.updated_at = now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9_]{3,20}$'),
    player_tag    VARCHAR(3)  NOT NULL UNIQUE CHECK (player_tag ~ '^[A-Z0-9]{1,3}$'),
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE devices (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id    TEXT        NOT NULL UNIQUE,  -- também é o client_id MQTT do aparelho
    name         TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE games (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    system     TEXT        NOT NULL,  -- pasta do Batocera (mame, fbneo, snes...) ou "manual" para o publisher.py
    rom        TEXT        NOT NULL,  -- nome do arquivo da ROM, sem caminho
    title      TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (system, rom)
);

CREATE TABLE plays (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    uuid       UUID        NOT NULL UNIQUE,  -- chave de idempotência: QoS 1 pode entregar a mesma mensagem 2x
    user_id    BIGINT      REFERENCES users (id) ON DELETE SET NULL,  -- NULL = convidado (ninguém logado)
    device_id  BIGINT      NOT NULL REFERENCES devices (id),
    game_id    BIGINT      NOT NULL REFERENCES games (id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at   TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ended_at >= started_at)
);

CREATE TABLE scores (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    play_id       BIGINT      NOT NULL UNIQUE REFERENCES plays (id) ON DELETE CASCADE,
    player_tag    VARCHAR(3)  NOT NULL,  -- tag informada na partida; vale para convidados
    score         BIGINT      NOT NULL CHECK (score >= 0),
    stage_reached INTEGER     NOT NULL CHECK (stage_reached >= 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX plays_game_id_idx ON plays (game_id);
CREATE INDEX plays_user_id_idx ON plays (user_id);
CREATE INDEX scores_score_idx  ON scores (score DESC);

CREATE TRIGGER users_updated_at   BEFORE UPDATE ON users   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER devices_updated_at BEFORE UPDATE ON devices FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER games_updated_at   BEFORE UPDATE ON games   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER plays_updated_at   BEFORE UPDATE ON plays   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER scores_updated_at  BEFORE UPDATE ON scores  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
