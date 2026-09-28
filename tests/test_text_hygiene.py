"""اختبار سلامة النصوص: مفيش محارف من لغات مش متوقّعة.

السبب: الكود والتعليقات كلها بالعربي، وأنا بحكم عربي فبيحصل إني
أكتب سطر عربي ويطلع فيه حرف صيني أو ياباني أو روسي من غير ما أحس.
الـglitches دي بتعدّي في الـreview من غير ما حد ياخد باله.

`tools/text_hygiene.py` بيعمل الفحص، والاختبار ده بيخلي الفشل
يوقف الـCI بدل ما يفضل رسالة في terminal حد ما يشوفهاش.
"""

from __future__ import annotations

import pathlib

import pytest

from tools.text_hygiene import scan_repo

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestTextHygiene:
    def test_the_scanner_itself_runs(self) -> None:
        """لو الـscanner وقع، الاختبارات التانية مالهاش معنى."""
        assert scan_repo(REPO_ROOT) is not None

    def test_no_unexpected_scripts_in_the_repo(self) -> None:
        problems = scan_repo(REPO_ROOT)
        if problems:
            report = "\n".join(f"  {problem}" for problem in problems[:40])
            pytest.fail(
                "لقيت محارف من لغات مش متوقّعة في النصوص "
                "(شغّل: python -m tools.text_hygiene):\n" + report
            )
