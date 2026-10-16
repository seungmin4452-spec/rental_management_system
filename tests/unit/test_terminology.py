"""화면·엑셀·오류 문구에 사장님이 헷갈리는 옛 용어가 남지 않았는지 검사 (USABILITY_PLAN U1).

문자열 리터럴만 본다(주석·독스트링·코드 이름은 제외). '보관'은 '보관 위치'처럼 정상 용법이 있어 제외.
"""

import ast
from pathlib import Path

import rentdesk

OLD_TERMS = ("은퇴", "순수금", "대금충당", "역거래", "전환 버퍼", "점검 버퍼", "재활성화", "연체 배수", "연체료 배수", "유예 ")


def _user_facing_strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def test_no_old_terms_in_user_facing_text():
    root = Path(rentdesk.__file__).parent
    found = [
        f"{path.relative_to(root)}:{line} {term!r}"
        for path in root.rglob("*.py")
        for line, text in _user_facing_strings(path)
        for term in OLD_TERMS
        if term in text
    ]
    assert not found, "옛 용어가 남아 있습니다:\n" + "\n".join(found)
