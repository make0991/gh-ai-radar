import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("radar", ROOT / "scripts" / "radar.py")
radar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(radar)


def _repo(full_name):
    return {
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "stargazers_count": 123,
        "description": "Useful project",
    }


def _metrics():
    return {
        "star_velocity_7d": 4,
        "commits_30d": 12,
        "contributors": 3,
        "has_docs": True,
    }


def _cfg(tmp_path):
    return {
        "search": {"topics": ["ai-agent"]},
        "report": {
            "top_n": 10,
            "output_dir": str(tmp_path),
            "keep_history": True,
            "sent_state_file": "sent-projects.json",
        },
    }


class RadarDedupTests(unittest.TestCase):
    def test_filter_unsent_ranked_removes_projects_already_sent(self):
        ranked = [
            (_repo("owner/old"), _metrics(), 99.0),
            (_repo("owner/new"), _metrics(), 88.0),
        ]

        filtered = radar.filter_unsent_ranked(ranked, {"owner/old"})

        self.assertEqual([repo["full_name"] for repo, _, _ in filtered], ["owner/new"])

    def test_sent_project_state_round_trips_as_sorted_json(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "sent-projects.json"

            radar.save_sent_projects(state_path, {"owner/z", "owner/a"})

            self.assertEqual(radar.load_sent_projects(state_path), {"owner/a", "owner/z"})
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                ["owner/a", "owner/z"],
            )

    def test_empty_report_explains_no_new_projects(self):
        with tempfile.TemporaryDirectory() as td:
            report = radar.make_report(
                [], _cfg(Path(td)), evaluated_count=7, skipped_sent_count=7
            )

        self.assertIn("本次没有发现新的合适项目", report)
        self.assertIn("已过滤历史发送项目 7 个", report)
        self.assertNotIn("| # | 项目 |", report)

    def test_write_report_updates_sent_state_only_for_reported_projects(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            cfg = _cfg(tmp_path)
            state_path = tmp_path / "sent-projects.json"
            state_path.write_text(json.dumps(["owner/old"]), encoding="utf-8")
            ranked = [(_repo("owner/new"), _metrics(), 88.0)]

            radar.write_report("body", cfg, ranked)

            self.assertEqual(radar.load_sent_projects(state_path), {"owner/old", "owner/new"})


if __name__ == "__main__":
    unittest.main()
