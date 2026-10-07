"""GUI-owned locations: bundle resources and live project data are separate."""
import sys
from pathlib import Path

def project_root():
    if not getattr(sys,'frozen',False):
        return Path(__file__).resolve().parents[2]
    # Required onedir layout: PROJECT/dist/APP/APP.exe. Never use _MEIPASS for live DBs.
    candidate=Path(sys.executable).resolve().parents[2]
    if (candidate/'config/phase10_realtime_v1.json').is_file() and (candidate/'config/t0_experimental_v1.json').is_file():
        return candidate
    # Moving just the onedir folder still reads the explicitly authorized local project.
    return Path('C:/ZUUU_Prediction_System')

ROOT=project_root()
ASSETS=Path(__file__).resolve().parent/'assets'

def resource_path(relative):
    """Static resources only; bundled _internal is never the live database root."""
    if getattr(sys,'frozen',False):
        return Path(getattr(sys,'_MEIPASS',Path(sys.executable).parent/'_internal'))/relative
    return ROOT/relative
