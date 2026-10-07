"""Source GUI launcher. V2 does not rebuild the accepted EXE."""
import argparse,logging,sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from .adapters import ROOT
from .paths import ASSETS
from .v2 import Window,Reader,ForecastCard as Card
from .windowing import show_normal

def main():
    p=argparse.ArgumentParser();p.add_argument('--capture');p.add_argument('--preview',action='store_true');p.add_argument('--width',type=int,default=1920);p.add_argument('--height',type=int,default=1080);p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--exit-after',type=int);args=p.parse_args()
    folder=ROOT/'logs/gui';folder.mkdir(parents=True,exist_ok=True)
    log=logging.getLogger('phase11');log.setLevel(logging.INFO)
    handler=RotatingFileHandler(folder/'gui.log',maxBytes=2*1024*1024,backupCount=4,encoding='utf-8');handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));log.addHandler(handler);log.info('GUI V2 startup read-only')
    app=QApplication(sys.argv);app.setWindowIcon(QIcon(str(ASSETS/'zuuu.ico')));w=Window(args.root)
    if args.capture:w.setAttribute(Qt.WA_DontShowOnScreen,True)
    w.resize(args.width,args.height)
    if args.capture:w.show()
    else:show_normal(w)
    if args.capture:
        def capture():
            def save():
                path=Path(args.capture);path.parent.mkdir(parents=True,exist_ok=True);w.grab().save(str(path));w.close();app.quit()
            QTimer.singleShot(800,save)
        w.loaded.connect(capture)
    if args.preview:
        def preview_capture():
            w.loaded.disconnect(preview_capture)
            def save_preview():
                w.select_probability('T1');w.grab().save(str(folder/'GUI_PREVIEW_LATEST.png'));log.info('GUI preview screenshot saved; window remains open')
            QTimer.singleShot(1000,save_preview)
        w.loaded.connect(preview_capture)
    if args.exit_after:QTimer.singleShot(args.exit_after*1000,w.close)
    return app.exec()
if __name__=='__main__':sys.exit(main())
