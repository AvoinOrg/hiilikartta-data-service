CREATE TYPE calculation_status_enum AS ENUM ('PROCESSING', 'FINISHED', 'ERROR');

CREATE TABLE plan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ui_id VARCHAR,
    user_id VARCHAR,
    data JSONB,
    created_ts TIMESTAMP DEFAULT current_timestamp(0),
    updated_ts TIMESTAMP DEFAULT current_timestamp(0),
    calculated_ts TIMESTAMP,
    calculation_updated_ts TIMESTAMP,
    calculation_status calculation_status_enum,
    report_areas JSONB,
    report_totals JSONB
);

-- GRANT ALL PRIVILEGES ON TABLE plan TO <username>;