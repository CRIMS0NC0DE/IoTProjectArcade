-- Saves na nuvem: a versão mais recente de cada arquivo de save de cada jogador (.srm, .state, .state1...).
-- O Pi guarda uma cópia por jogador e troca os arquivos no início da partida; aqui fica a cópia de segurança
-- que permite continuar de onde parou, inclusive em outro aparelho.
CREATE TABLE saves (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    system      TEXT        NOT NULL CHECK (system ~ '^[a-z0-9_]{1,32}$'),
    filename    TEXT        NOT NULL CHECK (filename !~ '[/\\]' AND filename !~ '^\.'),
    sha256      TEXT        NOT NULL,
    size        INTEGER     NOT NULL,
    data        BYTEA       NOT NULL,
    modified_at TIMESTAMPTZ NOT NULL,  -- quando o emulador gravou o arquivo (vale a versão mais nova)
    device_id   BIGINT      REFERENCES devices (id),  -- aparelho que enviou a versão atual
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, system, filename)
);

CREATE TRIGGER saves_updated_at BEFORE UPDATE ON saves FOR EACH ROW EXECUTE FUNCTION set_updated_at();
