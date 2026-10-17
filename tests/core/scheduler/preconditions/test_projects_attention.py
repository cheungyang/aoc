import unittest

from core.scheduler.preconditions import build_precondition
from core.scheduler.preconditions.projects_attention import ProjectsAttention


class TestProjectsAttention(unittest.TestCase):

    def test_is_declared_by_its_type(self):
        self.assertIsInstance(build_precondition({"type": "projects_attention"}), ProjectsAttention)


if __name__ == "__main__":
    unittest.main()
