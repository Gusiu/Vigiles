-- Registry schema for file transfer (grade 3.0): sites, zones, assets,
-- devices and the import journal. Values of the enumerations follow the
-- data contract in PLAN.md section 7.1.

CREATE TABLE sites (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code    text NOT NULL UNIQUE,
    name    text,
    address text
);

CREATE TABLE zones (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id bigint NOT NULL REFERENCES sites (id),
    code    text NOT NULL,
    name    text,
    floor   integer,
    UNIQUE (site_id, code)
);

CREATE TABLE assets (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                text NOT NULL UNIQUE,
    asset_type          text NOT NULL CHECK (asset_type IN (
                            'przejscie_instalacyjne', 'drzwi_ppoz', 'klapa_ppoz', 'czujka_dymu')),
    site_id             bigint REFERENCES sites (id),
    zone_id             bigint REFERENCES zones (id),
    building            text,
    floor               text,
    room                text,
    partition           text,
    installation        text,
    diameter_mm         integer CHECK (diameter_mm > 0),
    seal_system         text,
    fire_rating         text CHECK (fire_rating ~ '^EI (15|20|30|45|60|90|120|180|240)$'),
    installed_at        date,
    installer           text,
    -- field-owned: set by technicians, never by the importer
    condition           text NOT NULL DEFAULT 'sprawny' CHECK (condition IN (
                            'sprawny', 'do_naprawy', 'niesprawny', 'wycofany')),
    last_inspected_at   timestamptz,
    next_inspection_due date,
    notes               text,
    rev                 integer NOT NULL DEFAULT 1,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          text,
    origin              text
);

CREATE INDEX assets_site_zone_idx ON assets (site_id, zone_id);

CREATE TABLE devices (
    device_id  text PRIMARY KEY,
    vendor     text NOT NULL CHECK (vendor IN ('alfa', 'beta')),
    asset_id   uuid REFERENCES assets (id),
    gateway_id text
);

CREATE TABLE import_runs (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_name     text NOT NULL,
    sha256        text NOT NULL UNIQUE,
    format        text NOT NULL CHECK (format IN ('csv', 'json')),
    rows_total    integer NOT NULL DEFAULT 0,
    rows_ok       integer NOT NULL DEFAULT 0,
    rows_rejected integer NOT NULL DEFAULT 0,
    status        text NOT NULL CHECK (status IN (
                      'completed', 'completed_with_errors', 'failed')),
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz,
    error         text
);

-- Every real change of an asset bumps rev and records who made it. The
-- origin comes from SET LOCAL vigiles.origin in the writing transaction.
-- An upsert that changes nothing never reaches this trigger, which is why
-- writers filter with IS DISTINCT FROM.
CREATE FUNCTION touch_versioned_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        NEW.rev := OLD.rev + 1;
    END IF;
    NEW.updated_at := now();
    NEW.origin := coalesce(nullif(current_setting('vigiles.origin', true), ''), NEW.origin);
    RETURN NEW;
END;
$$;

CREATE TRIGGER assets_touch
    BEFORE INSERT OR UPDATE ON assets
    FOR EACH ROW EXECUTE FUNCTION touch_versioned_row();

-- The importer may read and upsert what it loads, but never delete.
GRANT SELECT, INSERT, UPDATE ON sites, zones, assets, devices, import_runs TO importer;
