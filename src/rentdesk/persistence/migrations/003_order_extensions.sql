-- M4.5 U6: 대여 연장 이력. 연장할 때마다 1행(이전·새 반납 예정, 추가 일수·금액). 추가만 하고 수정·삭제하지 않는다.
-- 연장 대여료 자체는 charge_lines에 '조정'(ADJUSTMENT)으로 남는다(장비별 RENTAL 1건 제약 유지).
CREATE TABLE order_extensions (
    id                  INTEGER PRIMARY KEY,
    order_id            INTEGER NOT NULL REFERENCES rental_orders(id) ON DELETE RESTRICT,
    previous_due_at_utc TEXT    NOT NULL
                        CHECK (previous_due_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    new_due_at_utc      TEXT    NOT NULL
                        CHECK (new_due_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    added_days          INTEGER NOT NULL CHECK (typeof(added_days) = 'integer' AND added_days >= 0),
    amount_won          INTEGER NOT NULL CHECK (typeof(amount_won) = 'integer' AND amount_won >= 0),
    note                TEXT CHECK (note IS NULL OR length(note) <= 500),
    created_at_utc      TEXT    NOT NULL,
    CHECK (previous_due_at_utc < new_due_at_utc)
);
CREATE INDEX ix_extensions_order ON order_extensions(order_id);

CREATE TRIGGER trg_extensions_no_update BEFORE UPDATE ON order_extensions
BEGIN
    SELECT RAISE(ABORT, 'order_extensions are append-only');
END;
CREATE TRIGGER trg_extensions_no_delete BEFORE DELETE ON order_extensions
BEGIN
    SELECT RAISE(ABORT, 'order_extensions are append-only');
END;
