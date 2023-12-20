CREATE TYPE calculation_status_enum AS ENUM ('PROCESSING', 'FINISHED', 'ERROR');

CREATE TABLE plan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ui_id UUID UNIQUE,
    user_id VARCHAR,
    data JSONB,
    created_ts TIMESTAMP DEFAULT current_timestamp(0),
    updated_ts TIMESTAMP DEFAULT current_timestamp(0),
    total_indices INTEGER,
    last_index INTEGER,
    last_area_calculation_status calculation_status_enum,
    calculated_ts TIMESTAMP,
    calculation_updated_ts TIMESTAMP,
    calculation_status calculation_status_enum,
    report_areas JSONB,
    report_totals JSONB
);

CREATE INDEX idx_plan_ui_id ON plan (ui_id);

-- GRANT ALL PRIVILEGES ON TABLE plan TO <username>;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO <username>;
