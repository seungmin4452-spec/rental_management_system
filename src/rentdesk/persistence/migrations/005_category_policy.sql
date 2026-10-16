-- M4.6 R2: 카테고리별 운영 설정(NULL = 가게 기본값 사용)과 품목 단위 준비 시간 스냅샷.
-- 범위는 SettingsService와 같다(분: 0~10080, 배수: 서비스에서 0~100 검증한 Decimal 문자열).
ALTER TABLE categories ADD COLUMN turnaround_buffer_minutes INTEGER
    CHECK (turnaround_buffer_minutes IS NULL OR (typeof(turnaround_buffer_minutes) = 'integer'
           AND turnaround_buffer_minutes BETWEEN 0 AND 10080));
ALTER TABLE categories ADD COLUMN grace_minutes INTEGER
    CHECK (grace_minutes IS NULL OR (typeof(grace_minutes) = 'integer' AND grace_minutes BETWEEN 0 AND 10080));
ALTER TABLE categories ADD COLUMN late_multiplier TEXT
    CHECK (late_multiplier IS NULL OR length(late_multiplier) BETWEEN 1 AND 20);

-- 준비 시간 스냅샷을 주문 단위에서 품목 단위로 옮긴다. 기존 품목은 주문 스냅샷 값으로 채운다.
-- 주문 열(rental_orders.turnaround_buffer_snapshot_minutes)은 호환을 위해 남기되 계산에 쓰지 않는다.
ALTER TABLE rental_items ADD COLUMN turnaround_buffer_snapshot_minutes INTEGER
    CHECK (turnaround_buffer_snapshot_minutes IS NULL OR (typeof(turnaround_buffer_snapshot_minutes) = 'integer'
           AND turnaround_buffer_snapshot_minutes >= 0));

UPDATE rental_items SET turnaround_buffer_snapshot_minutes = (
    SELECT o.turnaround_buffer_snapshot_minutes FROM rental_orders o WHERE o.id = rental_items.order_id
);
