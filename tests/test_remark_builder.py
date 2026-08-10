from __future__ import annotations

import unittest

from remark_builder import build_result_remark


class RemarkBuilderTests(unittest.TestCase):
    def test_location_match_explains_method_and_identity_reason(self) -> None:
        remark = build_result_remark(
            {
                "query_strategy": "姓名+城市州邮编→年龄分层→多信号",
                "status": "已匹配生日（高置信度）",
                "message": "先限定 1 个同龄候选；证据=姓名完全一致、当前邮编一致",
            }
        )
        self.assertIn("搜索方式：姓名+城市州邮编", remark)
        self.assertIn("结果：已匹配生日（高置信度）", remark)
        self.assertIn("原因：先限定 1 个同龄候选", remark)

    def test_both_searches_empty_explain_fallback(self) -> None:
        remark = build_result_remark(
            {
                "query_strategy": "姓名+城市州邮编→姓名",
                "status": "无结果",
                "message": "两级搜索均无可采集结果",
            }
        )
        self.assertIn("搜索方式：姓名+城市州邮编", remark)
        self.assertIn("两级搜索均无可采集结果", remark)

    def test_winning_alias_method_and_full_search_coverage_are_both_recorded(self) -> None:
        remark = build_result_remark(
            {
                "query_strategy": "曾用名(Kyasia Smith)+城市州邮编→年龄分层→多信号",
                "search_coverage": "姓名+城市州邮编→姓名→曾用名(Kyasia Smith)+城市州邮编",
                "status": "已匹配生日（高置信度）",
                "message": "先限定 1 个同龄候选",
            }
        )
        self.assertIn("搜索方式：曾用名(Kyasia Smith)+城市州邮编", remark)
        self.assertIn("搜索范围：姓名+城市州邮编→姓名→曾用名(Kyasia Smith)+城市州邮编", remark)

    def test_demographic_field_sources_are_recorded(self) -> None:
        remark = build_result_remark(
            {
                "query_strategy": "姓名",
                "status": "已匹配生日（高置信度）",
                "message": "年龄和地址一致",
                "demographics_note": "生日=MyLife详情、星座=完整生日确定",
            }
        )
        self.assertIn("字段来源：生日=MyLife详情、星座=完整生日确定", remark)

    def test_invalid_input_explains_no_search(self) -> None:
        remark = build_result_remark(
            {"query_strategy": "输入校验", "status": "输入无效", "message": "姓名至少需要名和姓"}
        )
        self.assertIn("搜索方式：输入校验", remark)
        self.assertIn("姓名至少需要名和姓", remark)


if __name__ == "__main__":
    unittest.main()
