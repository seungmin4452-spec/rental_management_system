-- M4.6 R4: 장비 세트(키트)와 구성품 체크리스트.
-- 세트는 예약을 빠르게 만드는 묶음일 뿐이며 가용성·출고·반납·요금은 구성 장비(asset_code) 하나하나 기준이다.
-- 예약에 담은 세트는 order_kits에 이름·할인·체크리스트를 스냅샷으로 남긴다(세트를 고쳐도 기존 예약 불변).
-- 값 검증(이름·할인 범위·체크리스트·대체 규칙)은 서비스 계층(domain.kits)에서 하고, DB는 형식만 확인한다.

CREATE TABLE kits (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL COLLATE NOCASE UNIQUE
                     CHECK (length(name) BETWEEN 1 AND 50 AND name = trim(name)),
    discount_kind    TEXT    NOT NULL DEFAULT 'FIXED' CHECK (discount_kind IN ('FIXED', 'PERCENT')),
    discount_won     INTEGER NOT NULL DEFAULT 0 CHECK (typeof(discount_won) = 'integer' AND discount_won >= 0),
    discount_percent TEXT    CHECK (discount_percent IS NULL OR length(discount_percent) BETWEEN 1 AND 20),
    note             TEXT    CHECK (note IS NULL OR length(note) <= 1000),
    is_archived      INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc   TEXT    NOT NULL,
    updated_at_utc   TEXT    NOT NULL,
    CHECK ((discount_kind = 'PERCENT') = (discount_percent IS NOT NULL)),
    CHECK (discount_kind = 'FIXED' OR discount_won = 0)
);

-- 세트 구성 장비(특정 장비 고정). 이력이 아니라 설정이므로 세트를 지우면 함께 지운다.
CREATE TABLE kit_assets (
    kit_id   INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (typeof(position) = 'integer' AND position >= 0),
    PRIMARY KEY (kit_id, asset_id)
);
CREATE INDEX ix_kit_assets_asset ON kit_assets(asset_id);

-- 관리번호 없는 구성품("배터리 2개", "충전기", "가방")
CREATE TABLE kit_checklist_items (
    id       INTEGER PRIMARY KEY,
    kit_id   INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (typeof(position) = 'integer' AND position >= 0),
    label    TEXT    NOT NULL CHECK (length(label) BETWEEN 1 AND 100 AND label = trim(label)),
    UNIQUE (kit_id, position)
);

-- 예약별 세트 스냅샷. 체크 결과는 체크리스트 줄마다 true/false인 JSON 배열(NULL = 아직 확인 안 함).
CREATE TABLE order_kits (
    id                   INTEGER PRIMARY KEY,
    order_id             INTEGER NOT NULL REFERENCES rental_orders(id) ON DELETE RESTRICT,
    kit_id               INTEGER NOT NULL REFERENCES kits(id) ON DELETE RESTRICT,
    name                 TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 50),
    discount_kind        TEXT    NOT NULL CHECK (discount_kind IN ('FIXED', 'PERCENT')),
    discount_won         INTEGER NOT NULL CHECK (typeof(discount_won) = 'integer' AND discount_won >= 0),
    discount_percent     TEXT    CHECK (discount_percent IS NULL OR length(discount_percent) BETWEEN 1 AND 20),
    checklist_json       TEXT    NOT NULL DEFAULT '[]'
                         CHECK (json_valid(checklist_json) AND json_type(checklist_json) = 'array'
                                AND length(checklist_json) <= 10000),
    checkout_checks_json TEXT    CHECK (checkout_checks_json IS NULL OR (json_valid(checkout_checks_json)
                                        AND json_type(checkout_checks_json) = 'array')),
    return_checks_json   TEXT    CHECK (return_checks_json IS NULL OR (json_valid(return_checks_json)
                                        AND json_type(return_checks_json) = 'array')),
    UNIQUE (order_id, kit_id),
    CHECK ((discount_kind = 'PERCENT') = (discount_percent IS NOT NULL)),
    CHECK (discount_kind = 'FIXED' OR discount_won = 0)
);
CREATE INDEX ix_order_kits_kit ON order_kits(kit_id);

-- 출고 전 예약(DRAFT/CONFIRMED)에서만 세트를 뺄 수 있다. 출고 이후는 이력.
CREATE TRIGGER trg_order_kits_delete_guard BEFORE DELETE ON order_kits
WHEN (SELECT status FROM rental_orders WHERE id = OLD.order_id) NOT IN ('DRAFT', 'CONFIRMED')
BEGIN
    SELECT RAISE(ABORT, 'order_kits rows cannot be deleted after checkout');
END;

-- 예약 품목이 속한 세트(NULL = 세트 아님). 같은 예약의 세트만 가리킬 수 있다.
ALTER TABLE rental_items ADD COLUMN order_kit_id INTEGER REFERENCES order_kits(id);
CREATE INDEX ix_items_order_kit ON rental_items(order_kit_id);

CREATE TRIGGER trg_items_kit_same_order_insert BEFORE INSERT ON rental_items
WHEN NEW.order_kit_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM order_kits WHERE id = NEW.order_kit_id AND order_id = NEW.order_id)
BEGIN
    SELECT RAISE(ABORT, 'rental_items.order_kit_id must belong to the same order');
END;

CREATE TRIGGER trg_items_kit_same_order_update BEFORE UPDATE OF order_kit_id, order_id ON rental_items
WHEN NEW.order_kit_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM order_kits WHERE id = NEW.order_kit_id AND order_id = NEW.order_id)
BEGIN
    SELECT RAISE(ABORT, 'rental_items.order_kit_id must belong to the same order');
END;

-- 세트 할인은 주문 할인과 별도의 DISCOUNT 줄(세트마다 1건)로 청구한다.
-- 기존 "주문당 DISCOUNT 1건" 제약을 "주문 할인 1건 + 세트마다 1건"으로 바꾼다.
ALTER TABLE charge_lines ADD COLUMN order_kit_id INTEGER REFERENCES order_kits(id)
    CHECK (order_kit_id IS NULL OR kind = 'DISCOUNT');
DROP INDEX ux_charges_order_discount;
CREATE UNIQUE INDEX ux_charges_order_discount ON charge_lines(order_id, IFNULL(order_kit_id, 0))
    WHERE kind = 'DISCOUNT';
