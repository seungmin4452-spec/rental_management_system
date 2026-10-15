-- M2: 출고 시 장비 외관/구성품 메모 (PRD S-05)
-- 출고 메모는 IN_RENTAL/COMPLETED 주문에만 의미가 있으나, NULL 허용으로 기존 행과 호환된다.
ALTER TABLE rental_orders ADD COLUMN checkout_note TEXT
    CHECK (checkout_note IS NULL OR length(checkout_note) <= 1000);
