import unittest
from unittest.mock import patch
from pathlib import Path
from src.gui import paths
from tests.phase11.fixtures import create

class PackagePaths(unittest.TestCase):
    def test_source_root_unchanged(self):
        with patch.object(paths.sys,'frozen',False,create=True):
            self.assertEqual(paths.project_root(),Path(__file__).resolve().parents[2])

    def test_onedir_reads_live_project_not_internal(self):
        root=create('windows_package_paths')
        with patch.object(paths.sys,'frozen',True,create=True),patch.object(paths.sys,'executable',str(root/'dist/APP/APP.exe')):
            self.assertEqual(paths.project_root(),root)
            self.assertNotIn('_internal',str(paths.project_root()))

    def test_moved_onedir_uses_authorized_project(self):
        root=create('windows_package_moved')
        with patch.object(paths.sys,'frozen',True,create=True),patch.object(paths.sys,'executable',str(root/'unrelated/dist/APP/APP.exe')):
            self.assertEqual(paths.project_root(),Path('C:/ZUUU_Prediction_System'))

    def test_gui_icon_is_local_resource(self):
        self.assertEqual(paths.ASSETS,Path(paths.__file__).resolve().parent/'assets')
        self.assertTrue((paths.ASSETS/'zuuu.ico').is_file())

if __name__=='__main__':unittest.main()
