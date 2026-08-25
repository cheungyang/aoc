import unittest
from core.voice.sentence_chunker import SentenceChunker

class TestSentenceChunker(unittest.TestCase):
    def setUp(self):
        self.chunker = SentenceChunker(min_chars=15, max_chars=100)

    def test_single_sentence_with_period(self):
        chunks = self.chunker.add_token("Hello world, this is a test sentence.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world, this is a test sentence.")
        self.assertEqual(self.chunker.flush(), [])

    def test_token_by_token_accumulation(self):
        tokens = ["This ", "is ", "the ", "first ", "sentence. ", "And ", "this ", "is ", "the ", "second ", "one!"]
        emitted = []
        for t in tokens:
            emitted.extend(self.chunker.add_token(t))
        emitted.extend(self.chunker.flush())

        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0], "This is the first sentence.")
        self.assertEqual(emitted[1], "And this is the second one!")

    def test_abbreviation_not_split(self):
        text = "Dr. Smith went to Washington D.C. for a conference."
        chunks = self.chunker.add_token(text)
        # Should not split on "Dr."
        self.assertTrue(any("Dr." in c for c in chunks) or any("Dr." in c for c in self.chunker.flush()))

    def test_flush_remaining_tokens(self):
        self.chunker.add_token("Unfinished trailing thought")
        flushed = self.chunker.flush()
        self.assertEqual(flushed, ["Unfinished trailing thought"])
        self.assertEqual(self.chunker.flush(), [])

    def test_cleans_xml_and_emojis_in_sentences(self):
        chunks = self.chunker.add_token("Here is the plan <poll><text>opt</text></poll> for today. ✨")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Here is the plan for today.")

if __name__ == "__main__":
    unittest.main()
