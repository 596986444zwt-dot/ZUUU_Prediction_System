# PyInstaller 6.22.2: onedir, windowed. No DB, models, config or workers bundled.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root=Path(SPECPATH).resolve().parents[2]
gui=root/'src/gui'
a=Analysis([str(gui/'packaging/launch_gui.py')],pathex=[str(root)],binaries=[],
    datas=[(str(gui/'assets/zuuu.ico'),'src/gui/assets'),(str(gui/'assets/zuuu.svg'),'src/gui/assets'),
           (str(root/'resources/images/header_airport.png'),'resources/images')]+collect_data_files('tzdata'),
    hiddenimports=['tzdata','PySide6.QtSvg','src.gui.package_smoke'],
    hookspath=[],runtime_hooks=[],
    excludes=['src.gui.verify','src.gui.verify_v1_1','src.gui.verify_windows_package','src.gui.audit',
              'src.realtime.engine','src.realtime.archive','src.realtime.t0.worker','src.realtime.t0.archive',
              'src.realtime.settlement','src.realtime.handoff','tkinter','PyQt5','PyQt6'],
    noarchive=False,optimize=0)
for name,*_ in a.pure:
    if name.startswith('src.realtime.') and name!='src.realtime.contracts':
        raise RuntimeError('Unexpected backend in GUI bundle: '+name)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='ZUUU温度预测系统',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False,icon=str(gui/'assets/zuuu.ico'),contents_directory='_internal')
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='ZUUU温度预测系统')
