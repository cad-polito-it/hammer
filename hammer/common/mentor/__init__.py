import os
from functools import reduce
from typing import Dict, List

from hammer.utils import add_dicts
from hammer.vlsi import TCLTool, HammerTool


class SiemensTool(TCLTool, HammerTool):
    """Mix-in trait with functions useful for Siemens EDA (formerly Mentor
    Graphics) tools, e.g. Tessent."""

    @property
    def env_vars(self) -> Dict[str, str]:
        # TODO

        return null

    def version_number(self, version: str) -> int:
        """
        Assumes versions look like YYYY.MM (e.g. "2022.3").
        """
        year = int(version.split(".")[0])
        minor = int(version.split(".")[1]) if "." in version else 0
        return year * 100 + minor

    @property
    def script_dir(self) -> str:
        dirname = os.path.join(self.run_dir, "scripts")
        os.makedirs(dirname, exist_ok=True)
        return dirname

    @property
    def report_dir(self) -> str:
        dirname = os.path.join(self.run_dir, "reports")
        os.makedirs(dirname, exist_ok=True)
        return dirname

    @property
    def result_dir(self) -> str:
        dirname = os.path.join(self.run_dir, "results")
        os.makedirs(dirname, exist_ok=True)
        return dirname

    @property
    def verilog(self) -> List[str]:
        return [v for v in list(self.input_files) if v.endswith(".v") or v.endswith(".sv")]
