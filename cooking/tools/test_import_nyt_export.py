"""Offline integration tests using invented recipes, not publisher content."""
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from import_nyt_export import OUTPUTS, import_export


def fixture(rid=90000001, **changes):
    return {
        "@type": "Recipe", "url": f"https://cooking.nytimes.com/recipes/{rid}-test-beans",
        "name": "Test beans", "recipeIngredient": ["2 cups beans", "1 cup water"],
        "recipeInstructions": [{"@type": "HowToStep", "text": "Combine the test ingredients."}],
        "recipeYield": "4 servings", "totalTime": "PT20M", "_vegetarian": True,
        **changes,
    }


def run_import(path, root):
    import_export(path, "brittany", root=Path(root))


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def supplied(self, recipes, name="input.json"):
        path = self.root / name
        path.write_text(json.dumps(recipes))
        return path

    def rows(self, collection="brittany"):
        path = self.root / "data" / OUTPUTS[collection][0]
        return json.loads(path.read_text().split("=", 1)[1].strip().rstrip(";"))

    def test_preserves_content_servings_and_separates_collections(self):
        path = self.supplied([fixture()])
        import_export(path, "brittany", root=self.root)
        import_export(path, "sam", root=self.root)
        row = self.rows()[0]
        self.assertEqual(row["servings"], "4")
        self.assertEqual(row["yield_count"], 4)
        self.assertEqual(row["cook_time"], "20 minutes")
        self.assertEqual(row["steps"][0]["text"], "Combine the test ingredients.")
        self.assertEqual(row["collections"], ["brittany"])
        self.assertEqual(self.rows("sam")[0]["collections"], ["sam"])

    def test_reruns_merge_without_duplicates_and_hold_unsafe_reimport(self):
        path = self.supplied([fixture()])
        import_export(path, "brittany", root=self.root)
        import_export(path, "brittany", root=self.root)
        self.assertEqual(len(self.rows()), 1)
        path = self.supplied([fixture(recipeIngredient=["1 tablespoon fish sauce"])])
        result = import_export(path, "brittany", root=self.root)
        self.assertEqual((result["accepted"], result["held"], result["total"]), (0, 1, 0))
        report = json.loads((self.root / "data/nyt/imports/brittany.review.json").read_text())
        self.assertIn("fish", report[0]["reason"])

    def test_unreviewed_entries_held_and_report_survives_other_batches(self):
        result = import_export(self.supplied([fixture(_vegetarian=None)]), "brittany", root=self.root)
        self.assertEqual(result["held"], 1)
        import_export(self.supplied([fixture(90000002)]), "brittany", root=self.root)
        report = json.loads((self.root / "data/nyt/imports/brittany.review.json").read_text())
        self.assertEqual([r["id"] for r in report], [90000001])

    def test_invalid_url_does_not_modify_existing_collection(self):
        path = self.supplied([fixture()])
        import_export(path, "brittany", root=self.root)
        before = self.rows()
        path = self.supplied([fixture(90000002), fixture(url="https://example.com/recipes/90000003-test")])
        with self.assertRaises(ValueError):
            import_export(path, "brittany", root=self.root)
        self.assertEqual(self.rows(), before)

    def test_optional_instructions_and_plain_author(self):
        recipe = fixture(author="Test cook")
        recipe.pop("recipeInstructions")
        import_export(self.supplied([recipe]), "brittany", root=self.root)
        self.assertEqual(self.rows()[0]["steps"], [])
        self.assertEqual(self.rows()[0]["author"], "Test cook")

    def test_local_photos_are_resized_and_kept_collection_specific(self):
        from PIL import Image
        photos = self.root / "supplied-photos"
        photos.mkdir()
        Image.new("RGB", (2400, 1600), "green").save(photos / "90000001.jpg")
        import_export(self.supplied([fixture()]), "brittany", images=photos, root=self.root)
        self.assertEqual(self.rows()[0]["front"], "nyt_brittany_90000001.jpg")
        for size, dimensions in (("card", (900, 600)), ("full", (2000, 1333))):
            with Image.open(self.root / "images" / size / "nyt_brittany_90000001.jpg") as image:
                self.assertEqual(image.size, dimensions)

    def test_concurrent_batches_do_not_lose_recipes(self):
        context = multiprocessing.get_context("fork")
        paths = [self.supplied([fixture(90000100 + i)], f"batch-{i}.json") for i in range(6)]
        processes = [context.Process(target=run_import, args=(path, self.root)) for path in paths]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual({r["pair"] for r in self.rows()}, set(range(90000100, 90000106)))


if __name__ == "__main__":
    unittest.main()
