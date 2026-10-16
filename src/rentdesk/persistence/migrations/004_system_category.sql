-- M4.6 R1: 항상 있는 "기타" 카테고리. 애매한 장비를 넣을 곳이며 이름 변경·숨김·삭제를 막는다.
-- 같은 이름(대소문자 무시)이 이미 있으면 그 행을 시스템 카테고리로 표시하고, 숨김 상태였다면 다시 보이게 한다.
ALTER TABLE categories ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1));

INSERT INTO categories (name)
SELECT '기타' WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = '기타');

UPDATE categories SET is_system = 1, is_archived = 0 WHERE name = '기타';

-- 시스템 카테고리는 하나뿐
CREATE UNIQUE INDEX ux_categories_single_system ON categories(is_system) WHERE is_system = 1;

CREATE TRIGGER trg_categories_system_guard BEFORE UPDATE ON categories
WHEN OLD.is_system = 1
 AND (NEW.name IS NOT OLD.name OR NEW.is_archived <> 0 OR NEW.is_system <> 1)
BEGIN
    SELECT RAISE(ABORT, 'system category cannot be renamed or archived');
END;

CREATE TRIGGER trg_categories_system_no_delete BEFORE DELETE ON categories
WHEN OLD.is_system = 1
BEGIN
    SELECT RAISE(ABORT, 'system category cannot be deleted');
END;
