"""Room switching by voice: matching "take me to X" against known rooms."""
import unittest

from core.voice.room_switch import build_room_aliases, match_room, parse_room_request, room_label

ROOMS = build_room_aliases([
    ("day-planning-voice", ["Daisy", "day-planner"]),
    ("fitness-coach-voice", ["Frankie", "fitness-coach"]),
    ("meal-planning-voice", ["Ramsay", "meal-planner"]),
    ("home-automation-voice", ["Butler", "home-steward"]),
])


class TestMatching(unittest.TestCase):

    def test_commands_that_switch(self):
        cases = {
            "Take me to day planning.": "day-planning-voice",
            "take me to Daisy's room": "day-planning-voice",
            "Let's go to the fitness coach room": "fitness-coach-voice",
            "Hey concierge, switch to meal planning please": "meal-planning-voice",
            "Can you take me to Butler?": "home-automation-voice",
            "I want to talk to Ramsay": "meal-planning-voice",
            "Okay, go to Frankie.": "fitness-coach-voice",
            "move me into home automation": "home-automation-voice",
            "switch over to the day planner": "day-planning-voice",
        }
        for transcript, room in cases.items():
            with self.subTest(transcript=transcript):
                self.assertEqual(match_room(transcript, ROOMS), room)

    def test_ordinary_requests_are_not_switches(self):
        for transcript in (
            "go to day planning and add a task",
            "What's on my day planning list?",
            "Talk to me about dinner",
            "take me to the airport on Friday",
            "Daisy, what's next?",
        ):
            with self.subTest(transcript=transcript):
                self.assertIsNone(match_room(transcript, ROOMS))

    def test_parse_extracts_the_room_phrase(self):
        self.assertEqual(parse_room_request("Take me to the Day-Planning room, please!"), "day planning")
        self.assertIsNone(parse_room_request("Turn off the kitchen lights"))


class TestAliases(unittest.TestCase):

    def test_room_label(self):
        self.assertEqual(room_label("day-planning-voice"), "day planning")
        self.assertEqual(room_label("voice-general"), "general")

    def test_an_alias_two_rooms_claim_is_dropped(self):
        aliases = build_room_aliases([
            ("weekly-planning-voice", ["Grace", "goal-setter"]),
            ("project-planning-voice", ["Grace", "goal-setter"]),
        ])
        self.assertNotIn("grace", aliases)
        self.assertEqual(aliases["weekly planning"], "weekly-planning-voice")


if __name__ == "__main__":
    unittest.main()
