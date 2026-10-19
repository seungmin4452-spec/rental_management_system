-- M4.6 R3: 요금 규칙(대여일수 계산 방식, 주말·주·월 단가, 연체료 계산 방식).
-- 가게 기본값은 app_settings 'rate_rule', 카테고리 규칙은 categories.rate_rule_json(NULL = 가게 기본값 사용),
-- 예약 품목은 확정 때 실제 적용 규칙을 rental_items.rate_rule_snapshot_json으로 고정한다.
-- 값 검증은 서비스 계층(domain.rates.validate_rate_rule)에서 하고, DB는 JSON 형식·길이만 확인한다.
-- 아래 기본 규칙 JSON은 domain.rates.rate_rule_to_json(DEFAULT_RATE_RULE)과 같다(테스트로 확인).
ALTER TABLE categories ADD COLUMN rate_rule_json TEXT
    CHECK (rate_rule_json IS NULL OR (json_valid(rate_rule_json) AND length(rate_rule_json) <= 2000));

ALTER TABLE rental_items ADD COLUMN rate_rule_snapshot_json TEXT
    CHECK (rate_rule_snapshot_json IS NULL
           OR (json_valid(rate_rule_snapshot_json) AND length(rate_rule_snapshot_json) <= 2000));

-- 기존 품목은 R3 이전 방식(24시간 올림, 일 요금만, 하루 단위 연체)으로 채운다.
UPDATE rental_items SET rate_rule_snapshot_json =
    '{"day_count":"HOURS_24","late_hourly_divisor":10,"late_method":"DAILY","monthly_enabled":false,"monthly_multiplier":"20","return_grace_minutes":0,"weekend_enabled":false,"weekend_end_minute":720,"weekend_end_weekday":0,"weekend_multiplier":"2","weekend_start_minute":720,"weekend_start_weekday":4,"weekly_enabled":false,"weekly_multiplier":"5"}';

INSERT OR IGNORE INTO app_settings (key, value_json) VALUES ('rate_rule',
    '{"day_count":"HOURS_24","late_hourly_divisor":10,"late_method":"DAILY","monthly_enabled":false,"monthly_multiplier":"20","return_grace_minutes":0,"weekend_enabled":false,"weekend_end_minute":720,"weekend_end_weekday":0,"weekend_multiplier":"2","weekend_start_minute":720,"weekend_start_weekday":4,"weekly_enabled":false,"weekly_multiplier":"5"}');
