"""Evidence evaluator: deterministic group operators."""
import pytest

from lotus.agents.evidence import evaluate
from lotus.domain.models import Questionnaire, QuestionnaireItem as Q, QuestionnaireResponse, ResponseItem as R


def build(group: Q, answers: list[bool]):
    qn = Questionnaire(id="T", title="t", source="test", item=[group])
    resp = QuestionnaireResponse(questionnaire="T", subject="X", item=[R(
        linkId=group.linkId, text=group.text,
        item=[R(linkId=c.linkId, text=c.text, answer=a) for c, a in zip(group.item, answers)])])
    return qn, resp


def leaves(n, points=None):
    return [Q(linkId=f"L{i}", text=f"leaf {i}", points=(points[i] if points else None)) for i in range(n)]


@pytest.mark.parametrize("op,n,answers,expected", [
    ("all", None, [True, True, True], True),
    ("all", None, [True, False, True], False),
    ("at-least-one", None, [False, True, False], True),   # >= 1, not "more than 1"
    ("at-least-one", None, [False, False, False], False),
    ("exactly-one", None, [False, True, False], True),
    ("exactly-one", None, [True, True, False], False),
    ("at-least-n", 2, [True, True, False], True),          # >= n
    ("at-least-n", 2, [True, False, False], False),
    ("not-exist", None, [False, False, False], True),
    ("not-exist", None, [False, True, False], False),
])
def test_operators(op, n, answers, expected):
    qn, resp = build(Q(linkId="G", text="g", type="group", operator=op, n=n, item=leaves(3)), answers)
    result = evaluate(qn, resp)
    assert result["met"] is expected
    assert resp.item[0].answer is expected  # written back into the response


@pytest.mark.parametrize("arith,answers,expected", [
    (">=", [True, True, False], True),   # 3 + 2 = 5 >= 5
    (">", [True, True, False], False),   # 5 > 5 is false
    (">=", [True, False, True], True),   # 3 + 2 = 5
    (">=", [False, True, True], False),  # 4
])
def test_points(arith, answers, expected):
    g = Q(linkId="BOA", text="boa", type="group", arithmetic=arith, points=5, item=leaves(3, [3, 2, 2]))
    qn, resp = build(g, answers)
    assert evaluate(qn, resp)["met"] is expected


def test_nested_groups():
    inner_a = Q(linkId="A", text="a", type="group", operator="at-least-one", item=[Q(linkId="a1", text="a1")])
    inner_b = Q(linkId="B", text="b", type="group", operator="not-exist", item=[Q(linkId="b1", text="b1")])
    root = Q(linkId="ROOT", text="root", type="group", operator="all", item=[inner_a, inner_b])
    qn = Questionnaire(id="T", title="t", source="s", item=[root])
    resp = QuestionnaireResponse(questionnaire="T", subject="X", item=[R(linkId="ROOT", text="root", item=[
        R(linkId="A", text="a", item=[R(linkId="a1", text="a1", answer=True)]),
        R(linkId="B", text="b", item=[R(linkId="b1", text="b1", answer=True)])])])
    out = evaluate(qn, resp)
    assert out["groups"]["A"]["met"] and not out["groups"]["B"]["met"] and not out["met"]
