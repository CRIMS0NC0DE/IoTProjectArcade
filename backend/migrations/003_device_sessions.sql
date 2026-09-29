-- Quem está logado em cada aparelho (login pelo menu do Batocera) e o histórico desses logins.
CREATE TABLE device_sessions (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id  BIGINT      NOT NULL REFERENCES devices (id),
    user_id    BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at   TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ended_at >= started_at)
);

-- No máximo uma sessão aberta por aparelho: um login novo precisa encerrar o anterior.
CREATE UNIQUE INDEX device_sessions_open_idx ON device_sessions (device_id) WHERE ended_at IS NULL;
CREATE INDEX device_sessions_user_id_idx ON device_sessions (user_id);

CREATE TRIGGER device_sessions_updated_at BEFORE UPDATE ON device_sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
