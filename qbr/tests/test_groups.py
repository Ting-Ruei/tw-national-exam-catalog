"""Question groups.

The rule being tested is the paper's, so the cases are printed-paper shapes: a head with one
continuation, a head with two, continuation markers the corpus actually contains, and the shapes
that must NOT group - a sentence that merely mentions 上題 in the middle, and a marker at the start
of a paper's first question.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qbr import groups


def q(number, stem):
    return {"question_number": number, "stem": stem, "candidate_key": f"p:question:q{number:03d}"}


def test_two_questions_sharing_a_stem_are_one_group():
    items = [q(49, "A、B、C、D 四位病人接受某抗生素治療…下列何者正確？"),
             q(50, "承上題，其分布體積約為多少L？")]
    found = groups.group_runs(items)
    assert [len(g) for g in found] == [2]


def test_a_group_of_three_is_one_group():
    items = [q(11, "共用題幹"), q(12, "承上題，甲"), q(13, "承上題，乙")]
    found = groups.group_runs(items)
    assert [len(g) for g in found] == [3]


def test_a_question_that_stands_alone_is_its_own_group():
    items = [q(1, "下列何者正確？"), q(2, "下列何者錯誤？")]
    assert [len(g) for g in groups.group_runs(items)] == [1, 1]


def test_every_marker_in_the_corpus_is_recognised():
    for marker in groups.CONTINUATION_MARKERS:
        assert groups.is_continuation(marker + "，某某？"), marker


def test_a_marker_in_the_middle_of_a_sentence_does_not_group():
    # The question's own sentence, not the paper's signal. Matching anywhere would pull this
    # question into the previous group and give it a shared stem it does not have.
    items = [q(1, "共用題幹"), q(2, "下列何者正確？上題的敘述何者錯誤？")]
    assert [len(g) for g in groups.group_runs(items)] == [1, 1]


def test_a_marker_on_the_first_question_of_a_paper_does_not_group():
    items = [q(1, "承上題，但這是本卷第一題")]
    assert [len(g) for g in groups.group_runs(items)] == [1]


def test_the_shared_stem_is_the_head_stem_and_is_not_given_to_the_head():
    items = [q(7, "共用題幹全文"), q(8, "承上題，甲？")]
    found = groups.group_runs(items)
    assert groups.shared_stem_of(found[0]) == "共用題幹全文"
    # A group of one has no shared stem - returning its own stem would make every question in a
    # paper look like a group member.
    assert groups.shared_stem_of([items[0]]) is None


def test_binding_gives_every_member_the_same_group_ref():
    items = [q(7, "共用題幹"), q(8, "承上題，甲？"), q(9, "下列何者正確？")]
    groups.bind(items, paper_key="moex:1")
    assert items[1]["group_ref"] == items[0]["group_ref"] == "moex:1:group:q007"
    assert items[2]["group_ref"] is None
    assert items[0]["group_position"] == 0 and items[1]["group_position"] == 1
    assert items[0]["shared_stem"] is None and items[1]["shared_stem"] == "共用題幹"


def test_binding_is_stable_when_the_input_is_out_of_order():
    items = [q(9, "承上題，甲？"), q(8, "共用題幹")]
    groups.bind(items, paper_key="moex:1")
    assert items[0]["group_ref"] == items[1]["group_ref"]


def test_summarise_counts_what_it_claims_to():
    items = [q(1, "甲"), q(2, "承上題，乙"), q(3, "丙")]
    s = groups.summarise(items)
    assert s["groups"] == 2
    assert s["grouped_questions"] == 2
    assert s["sizes"] == {1: 1, 2: 1}
    assert s["markers"] == ["承上題"]


def test_a_declared_group_binds_the_following_questions():
    """紙本自己宣告題組：「依序回答下列三題」→ 這一題加後面兩題是一組。

    量測 `1152_藥師(二)_藥學(四)` Q37：題幹結尾是「依序回答下列三題。」，
    Q38、Q39 跟在後面（Q39 是「上述劑量調整的原因為何？」），三題都沒有 `group_ref`，
    所以審題者看到三個沒有共同情境的題目，Q39 讀起來像在問一件不存在的事。
    """
    from qbr import groups
    items = [
        {"question_number": 37, "stem": "吳先生目前服用oxycodone 10 mg/day，依序回答下列三題。"},
        {"question_number": 38, "stem": "initial daily dose 應如何調整？"},
        {"question_number": 39, "stem": "上述劑量調整的原因為何？"},
        {"question_number": 40, "stem": "下列何種溶液的pH 值最低？"},
    ]
    groups.bind(items, paper_key="moex:1")
    assert items[0]["group_size"] == 3
    assert items[1]["group_size"] == 3
    assert items[2]["group_size"] == 3
    assert items[3]["group_ref"] is None, "第四題已經在題組之外"
    assert items[1]["shared_stem"] == items[0]["stem"]
    assert items[2]["shared_stem"] == items[0]["stem"]
    assert items[0]["shared_stem"] is None, "head 自己不需要重複"


def test_an_arabic_numeral_declaration_is_read():
    """「依序回答下列3 題」也要讀得懂（紙本兩種寫法都用）。"""
    from qbr import groups
    assert groups.declared_group_size("某抗生素…，依序回答下列3 題") == 3
    assert groups.declared_group_size("依序回答下列3題。") == 3
    assert groups.declared_group_size("依序回答下列四題。") == 4
    assert groups.declared_group_size("下列何者正確？") is None


def test_a_declaration_of_one_is_not_a_group():
    """宣告一題不是題組——否則那一題會變成「只有自己的題組」。"""
    from qbr import groups
    assert groups.declared_group_size("請回答下列一題") is None


def test_the_gap_rule_is_not_used_to_make_groups():
    """間距不能拿來分題組：兩群幾乎完全重疊。

    量測：377 個真題組轉換的間距中位數 104.9pt，28,137 個非題組相鄰題 99.8pt。
    掃過 20-300pt 每個門檻，最佳 precision 只有 6.9%（每抓到 1 個真題組誤判 13.4 個）。
    """
    from qbr import groups
    # 兩題之間有極大間距，但紙本沒有任何宣告或延續標記 -> 不是題組
    items = [
        {"question_number": 1, "stem": "第一題的題幹？"},
        {"question_number": 2, "stem": "第二題的題幹？"},
    ]
    groups.bind(items, paper_key="moex:1")
    assert items[0]["group_ref"] is None
    assert items[1]["group_ref"] is None
