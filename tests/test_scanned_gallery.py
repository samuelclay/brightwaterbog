import tempfile
import unittest
from pathlib import Path

from tools.scanned_gallery import crop_scanned_page, segment_scanned_page, trash_scanned_image


class ScannedGalleryDeleteTests(unittest.TestCase):
    def test_delete_moves_image_to_recoverable_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scanned = root / "scanned"
            image = scanned / "piece" / "scan.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"jpeg")

            rel, destination = trash_scanned_image(
                scanned,
                "piece/scan.jpg",
                trash_root=root / "trash",
            )

            self.assertEqual(rel, Path("piece/scan.jpg"))
            self.assertFalse(image.exists())
            self.assertEqual(destination.read_bytes(), b"jpeg")

    def test_delete_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scanned = root / "scanned"
            scanned.mkdir()
            outside = root / "outside.jpg"
            outside.write_bytes(b"jpeg")

            with self.assertRaisesRegex(ValueError, "invalid image path"):
                trash_scanned_image(
                    scanned,
                    "../outside.jpg",
                    trash_root=root / "trash",
                )

            self.assertTrue(outside.exists())


class ScannedGallerySegmentTests(unittest.TestCase):
    def test_segment_replaces_page_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scanned = root / "scanned"
            page = scanned / "piece" / "20260807_120000_flatbed_001.jpg"
            page.parent.mkdir(parents=True)
            page.write_bytes(b"original page")

            fake_python = root / "fake-python"
            fake_python.write_text(
                "#!/bin/sh\n"
                "outdir=\"$3\"\n"
                "printf first > \"$outdir/crop_001.jpg\"\n"
                "printf second > \"$outdir/crop_002.jpg\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            rel, outputs, trashed = segment_scanned_page(
                scanned,
                "piece/20260807_120000_flatbed_001.jpg",
                trash_root=root / "trash",
                captures_root=root / "captures",
                python=fake_python,
                crop_script=root / "crop.py",
            )

            self.assertEqual(rel, Path("piece/20260807_120000_flatbed_001.jpg"))
            self.assertEqual(len(outputs), 2)
            self.assertEqual(trashed.read_bytes(), b"original page")
            self.assertEqual((scanned / outputs[0]).read_bytes(), b"first")
            self.assertEqual((scanned / outputs[1]).read_bytes(), b"second")

    def test_segment_can_crop_one_drawing_from_a_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scanned = root / "scanned"
            page = scanned / "piece" / "20260807_120000_flatbed_001.jpg"
            page.parent.mkdir(parents=True)
            page.write_bytes(b"original page")

            fake_python = root / "fake-python"
            fake_python.write_text(
                "#!/bin/sh\noutdir=\"$3\"\nprintf only > \"$outdir/crop_001.jpg\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            _, outputs, trashed = segment_scanned_page(
                scanned,
                page.relative_to(scanned),
                trash_root=root / "trash",
                captures_root=root / "captures",
                python=fake_python,
                crop_script=root / "crop.py",
            )

            self.assertEqual(outputs, [Path("piece/20260807_120000_flatbed_001.jpg")])
            self.assertEqual(page.read_bytes(), b"only")
            self.assertEqual(trashed.read_bytes(), b"original page")


class ScannedGalleryCropTests(unittest.TestCase):
    def test_crop_replaces_page_with_one_composition_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scanned = root / "scanned"
            page = scanned / "piece" / "20260807_120000_flatbed_001.jpg"
            page.parent.mkdir(parents=True)
            page.write_bytes(b"original page")

            fake_python = root / "fake-python"
            fake_python.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in *\" --crop-sketch \"*) ;; *) exit 9 ;; esac\n"
                "outdir=\"$3\"\n"
                "printf cropped > \"$outdir/crop_001.jpg\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            rel, outputs, trashed = crop_scanned_page(
                scanned,
                page.relative_to(scanned),
                trash_root=root / "trash",
                captures_root=root / "captures",
                python=fake_python,
                crop_script=root / "crop.py",
            )

            self.assertEqual(rel, Path("piece/20260807_120000_flatbed_001.jpg"))
            self.assertEqual(outputs, [rel])
            self.assertEqual(page.read_bytes(), b"cropped")
            self.assertEqual(trashed.read_bytes(), b"original page")


if __name__ == "__main__":
    unittest.main()
