"""主程式單元測試。"""

import unittest

from src.main import main


class TestMain(unittest.TestCase):
    """測試主程式。"""

    def test_main_runs(self):
        """測試主程式可正常執行。"""
        # 目前主程式僅包含 logging，應能正常執行不拋出例外
        main()


if __name__ == "__main__":
    unittest.main()
