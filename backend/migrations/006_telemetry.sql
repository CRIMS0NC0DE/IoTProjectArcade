-- Leituras do ESP32 ligado ao arcade (DHT11). O painel recebe todas ao vivo; aqui fica uma por minuto, para o gráfico.
CREATE TABLE telemetry (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id   BIGINT       NOT NULL REFERENCES devices (id),
    temperature NUMERIC(4,1) NOT NULL CHECK (temperature BETWEEN -40 AND 125),  -- °C
    humidity    NUMERIC(4,1) CHECK (humidity BETWEEN 0 AND 100),  -- %; NULL se o sensor não mandou
    measured_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX telemetry_device_measured_idx ON telemetry (device_id, measured_at);

CREATE TRIGGER telemetry_updated_at BEFORE UPDATE ON telemetry FOR EACH ROW EXECUTE FUNCTION set_updated_at();
