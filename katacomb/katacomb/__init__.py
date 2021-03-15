# flake8: noqa F401
from .obit_context import (obit_context,
                           obit_err,
                           obit_sys,
                           handle_obit_err)

from .aips_parser import obit_config_from_aips

from .aips_path import AIPSPath

from .util import (task_factory,
                   normalise_target_name,
                   task_defaults)

from .katdal_adapter import (KatdalAdapter,
                             time_chunked_scans,
                             aips_ant_nr,
                             katdal_ant_name,
                             aips_timestamps,
                             katdal_timestamps)
from .aips_table import (AIPSTable,
                         AIPSHistory)
from .uv_facade import (UVFacade,
                        open_uv,
                        uv_factory)
from .img_facade import (ImageFacade,
                         open_img,
                         img_factory,
                         obit_image_mf_planes,
                         obit_image_mf_rms)
from .uv_export import (uv_export,
                        uv_history_obs_description,
                        uv_history_selection)

from .aips_export import (export_calibration_solutions,
                          export_clean_components,
                          export_images)

from .qa_report import (make_pbeam_images,
                        make_qa_report,
                        organise_qa_output)

from .continuum_pipeline import pipeline_factory

# BEGIN VERSION CHECK
# Get package version when locally imported from repo or via -e develop install
try:
    import katversion as _katversion
except ImportError:  # pragma: no cover
    import time as _time
    __version__ = "0.0+unknown.{}".format(_time.strftime('%Y%m%d%H%M'))
else:  # pragma: no cover
    __version__ = _katversion.get_version(__path__[0])    # type: ignore
# END VERSION CHECK
