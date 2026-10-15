-- RentDesk Lite 초기 스키마 (PRD 8.1)
-- 규칙
--  * 시각 컬럼은 UTC 'YYYY-MM-DDTHH:MM:SSZ' 텍스트 (텍스트 정렬 = 시간 순서)
--  * 금액은 KRW INTEGER. typeof 검사로 실수/문자열 저장 차단
--  * rental_orders / rental_items / charge_lines / money_entries 에 CASCADE DELETE 금지
--  * 원장(charge_lines, money_entries)은 수정·삭제 금지(트리거) — 정정은 역거래로
-- schema_migrations 테이블은 마이그레이션 실행기(db.py)가 생성·기록한다.

CREATE TABLE categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL COLLATE NOCASE UNIQUE
                CHECK (length(name) BETWEEN 1 AND 50 AND name = trim(name)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1))
);

CREATE TABLE assets (
    id               INTEGER PRIMARY KEY,
    asset_code       TEXT    NOT NULL COLLATE NOCASE UNIQUE
                     CHECK (length(asset_code) BETWEEN 1 AND 30 AND asset_code = trim(asset_code)),
    name             TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100 AND name = trim(name)),
    category_id      INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    brand            TEXT CHECK (brand IS NULL OR length(brand) <= 100),
    model            TEXT CHECK (model IS NULL OR length(model) <= 100),
    serial_no        TEXT CHECK (serial_no IS NULL OR length(serial_no) <= 100),
    location         TEXT CHECK (location IS NULL OR length(location) <= 100),
    daily_rate_won   INTEGER NOT NULL CHECK (typeof(daily_rate_won) = 'integer' AND daily_rate_won >= 0),
    deposit_won      INTEGER NOT NULL CHECK (typeof(deposit_won) = 'integer' AND deposit_won >= 0),
    lifecycle_status TEXT    NOT NULL DEFAULT 'ACTIVE' CHECK (lifecycle_status IN ('ACTIVE', 'RETIRED')),
    note             TEXT CHECK (note IS NULL OR length(note) <= 1000),
    created_at_utc   TEXT    NOT NULL
                     CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);
CREATE INDEX ix_assets_category ON assets(category_id);

-- 이력(예약 품목·정비 블록)이 있는 장비는 FK(RESTRICT)로 물리 삭제가 차단된다 (PRD S-02).

-- 관리번호는 변경 불가 (PRD 7.5)
CREATE TRIGGER trg_assets_code_immutable BEFORE UPDATE OF asset_code ON assets
WHEN NEW.asset_code IS NOT OLD.asset_code
BEGIN
    SELECT RAISE(ABORT, 'asset_code is immutable');
END;

CREATE TABLE customers (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100 AND name = trim(name)),
    customer_type  TEXT    NOT NULL DEFAULT 'INDIVIDUAL' CHECK (customer_type IN ('INDIVIDUAL', 'BUSINESS')),
    phone          TEXT CHECK (phone IS NULL OR length(phone) <= 30),   -- UNIQUE 금지(중복 허용)
    email          TEXT CHECK (email IS NULL OR length(email) <= 100),
    note           TEXT CHECK (note IS NULL OR length(note) <= 1000),
    is_archived    INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT    NOT NULL
                   CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);
CREATE INDEX ix_customers_name ON customers(name);

-- 예약 이력이 있는 고객은 FK(RESTRICT)로 물리 삭제가 차단된다 (PRD S-03).

CREATE TABLE rental_orders (
    id                                 INTEGER PRIMARY KEY,
    order_no                           TEXT    NOT NULL UNIQUE,
    customer_id                        INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    status                             TEXT    NOT NULL
                                       CHECK (status IN ('DRAFT', 'CONFIRMED', 'IN_RENTAL', 'COMPLETED', 'CANCELED')),
    start_at_utc                       TEXT    NOT NULL
                                       CHECK (start_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    due_at_utc                         TEXT    NOT NULL
                                       CHECK (due_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    actual_checkout_at_utc             TEXT
                                       CHECK (actual_checkout_at_utc IS NULL OR actual_checkout_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    turnaround_buffer_snapshot_minutes INTEGER NOT NULL
                                       CHECK (typeof(turnaround_buffer_snapshot_minutes) = 'integer' AND turnaround_buffer_snapshot_minutes >= 0),
    discount_won                       INTEGER NOT NULL DEFAULT 0
                                       CHECK (typeof(discount_won) = 'integer' AND discount_won >= 0),
    notes                              TEXT CHECK (notes IS NULL OR length(notes) <= 2000),
    created_at_utc                     TEXT    NOT NULL,
    updated_at_utc                     TEXT    NOT NULL,
    CHECK (start_at_utc < due_at_utc),
    -- 출고 시각은 IN_RENTAL/COMPLETED에만 존재
    CHECK ((actual_checkout_at_utc IS NOT NULL) = (status IN ('IN_RENTAL', 'COMPLETED')))
);
CREATE INDEX ix_orders_status_period ON rental_orders(status, start_at_utc, due_at_utc);
CREATE INDEX ix_orders_customer ON rental_orders(customer_id);

-- 주문은 삭제하지 않고 CANCELED 처리
CREATE TRIGGER trg_orders_no_delete BEFORE DELETE ON rental_orders
BEGIN
    SELECT RAISE(ABORT, 'rental_orders rows cannot be deleted; cancel instead');
END;

CREATE TABLE rental_items (
    id                       INTEGER PRIMARY KEY,
    order_id                 INTEGER NOT NULL REFERENCES rental_orders(id) ON DELETE RESTRICT,
    asset_id                 INTEGER NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    status                   TEXT    NOT NULL CHECK (status IN ('RESERVED', 'OUT', 'RETURNED', 'CANCELED')),
    daily_rate_snapshot_won  INTEGER NOT NULL
                             CHECK (typeof(daily_rate_snapshot_won) = 'integer' AND daily_rate_snapshot_won >= 0),
    deposit_snapshot_won     INTEGER NOT NULL
                             CHECK (typeof(deposit_snapshot_won) = 'integer' AND deposit_snapshot_won >= 0),
    late_multiplier_snapshot TEXT    NOT NULL CHECK (length(late_multiplier_snapshot) BETWEEN 1 AND 20),  -- Decimal 문자열
    grace_minutes_snapshot   INTEGER NOT NULL
                             CHECK (typeof(grace_minutes_snapshot) = 'integer' AND grace_minutes_snapshot >= 0),
    actual_return_at_utc     TEXT
                             CHECK (actual_return_at_utc IS NULL OR actual_return_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    return_condition         TEXT CHECK (return_condition IS NULL OR return_condition IN ('GOOD', 'MAINTENANCE_REQUIRED')),
    return_note              TEXT CHECK (return_note IS NULL OR length(return_note) <= 1000),
    UNIQUE (order_id, asset_id),
    -- 반납 정보는 RETURNED 품목에만, RETURNED 품목에는 반드시 존재
    CHECK ((status = 'RETURNED') = (actual_return_at_utc IS NOT NULL)),
    CHECK ((status = 'RETURNED') = (return_condition IS NOT NULL))
);
CREATE INDEX ix_items_asset_status ON rental_items(asset_id, status);

-- 출고 전 주문(DRAFT/CONFIRMED)의 RESERVED 품목만 삭제 가능(장비 목록 수정용).
-- 출고 이후 품목은 이력이므로 삭제 금지.
CREATE TRIGGER trg_items_delete_guard BEFORE DELETE ON rental_items
WHEN OLD.status <> 'RESERVED'
  OR (SELECT status FROM rental_orders WHERE id = OLD.order_id) NOT IN ('DRAFT', 'CONFIRMED')
BEGIN
    SELECT RAISE(ABORT, 'rental_items rows cannot be deleted after checkout');
END;

CREATE TABLE maintenance_blocks (
    id             INTEGER PRIMARY KEY,
    asset_id       INTEGER NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    start_at_utc   TEXT    NOT NULL
                   CHECK (start_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    end_at_utc     TEXT
                   CHECK (end_at_utc IS NULL OR end_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    reason         TEXT    NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    created_at_utc TEXT    NOT NULL,
    closed_at_utc  TEXT,
    CHECK (end_at_utc IS NULL OR start_at_utc < end_at_utc)
);
CREATE INDEX ix_blocks_asset_period ON maintenance_blocks(asset_id, start_at_utc, end_at_utc);

CREATE TABLE charge_lines (
    id             INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL REFERENCES rental_orders(id) ON DELETE RESTRICT,
    rental_item_id INTEGER REFERENCES rental_items(id) ON DELETE RESTRICT,
    kind           TEXT    NOT NULL CHECK (kind IN ('RENTAL', 'DISCOUNT', 'LATE', 'DAMAGE', 'ADJUSTMENT')),
    amount_won     INTEGER NOT NULL CHECK (typeof(amount_won) = 'integer'),
    description    TEXT CHECK (description IS NULL OR length(description) <= 500),
    created_at_utc TEXT    NOT NULL,
    -- 부호 및 품목 연결 제약
    CHECK (
        (kind = 'RENTAL' AND amount_won >= 0 AND rental_item_id IS NOT NULL)
     OR (kind IN ('LATE', 'DAMAGE') AND amount_won > 0 AND rental_item_id IS NOT NULL)
     OR (kind = 'DISCOUNT' AND amount_won < 0 AND rental_item_id IS NULL)
     OR (kind = 'ADJUSTMENT')
    )
);
CREATE INDEX ix_charges_order ON charge_lines(order_id);
-- 장비별 RENTAL/LATE/DAMAGE 1건, 주문당 DISCOUNT 1건 (중복 청구 방지)
CREATE UNIQUE INDEX ux_charges_item_kind ON charge_lines(rental_item_id, kind)
    WHERE kind IN ('RENTAL', 'LATE', 'DAMAGE');
CREATE UNIQUE INDEX ux_charges_order_discount ON charge_lines(order_id)
    WHERE kind = 'DISCOUNT';

CREATE TRIGGER trg_charges_no_update BEFORE UPDATE ON charge_lines
BEGIN
    SELECT RAISE(ABORT, 'charge_lines are append-only');
END;
CREATE TRIGGER trg_charges_no_delete BEFORE DELETE ON charge_lines
BEGIN
    SELECT RAISE(ABORT, 'charge_lines are append-only');
END;

CREATE TABLE money_entries (
    id               INTEGER PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES rental_orders(id) ON DELETE RESTRICT,
    kind             TEXT    NOT NULL CHECK (kind IN (
                         'RENTAL_RECEIPT', 'RENTAL_REFUND', 'DEPOSIT_IN', 'DEPOSIT_OUT',
                         'DEPOSIT_APPLIED', 'DEPOSIT_APPLICATION_REVERSAL')),
    amount_won       INTEGER NOT NULL CHECK (typeof(amount_won) = 'integer' AND amount_won > 0),
    payment_method   TEXT CHECK (payment_method IS NULL OR payment_method IN ('CASH', 'TRANSFER', 'CARD', 'OTHER')),
    note             TEXT CHECK (note IS NULL OR length(note) <= 500),
    created_at_utc   TEXT    NOT NULL,
    related_entry_id INTEGER REFERENCES money_entries(id) ON DELETE RESTRICT
);
CREATE INDEX ix_money_order_kind ON money_entries(order_id, kind);

CREATE TRIGGER trg_money_no_update BEFORE UPDATE ON money_entries
BEGIN
    SELECT RAISE(ABORT, 'money_entries are append-only');
END;
CREATE TRIGGER trg_money_no_delete BEFORE DELETE ON money_entries
BEGIN
    SELECT RAISE(ABORT, 'money_entries are append-only');
END;

CREATE TABLE app_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json))
);

INSERT INTO app_settings (key, value_json) VALUES
    ('grace_minutes', '60'),
    ('late_multiplier', '"1"'),
    ('turnaround_buffer_minutes', '60'),
    ('business_name', '""'),
    ('business_phone', '""'),
    ('default_backup_dir', 'null');

CREATE TABLE audit_events (
    id              INTEGER PRIMARY KEY,
    occurred_at_utc TEXT    NOT NULL,
    entity_type     TEXT    NOT NULL,
    entity_id       INTEGER,
    action          TEXT    NOT NULL,
    description     TEXT CHECK (description IS NULL OR length(description) <= 500)
);
CREATE INDEX ix_audit_entity ON audit_events(entity_type, entity_id);
