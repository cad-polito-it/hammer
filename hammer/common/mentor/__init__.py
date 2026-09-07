import os
from functools import reduce
from typing import Optional, Dict, List

from hammer.utils import add_dicts
from hammer.vlsi import TCLTool, HammerTool


class SiemensTool(TCLTool, HammerTool):
    """Mix-in trait with functions useful for Siemens EDA (formerly Mentor
    Graphics) tools."""

    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        result = dict(super().env_vars)
        result.update({
            "MGLS_LICENSE_FILE": self.get_setting("mentor.MGLS_LICENSE_FILE"),
        })
        return result

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

    def generate_dofile_from_spf(self, spf_path: str, options: Optional[List[str]] = []) -> bool:
        """
        Generate a Tessent dofile (scan chain/clock setup, ...) from an SPF file using stil2tessent.

        :param spf_path: Path to the input SPF file.
        :param options: Optional list of additional command-line options for stil2tessent.
                        If None or empty, no additional options are passed.
        :return: True if the dofile was generated successfully.
        """
        args_testbench = ["stil2tessent", "-stil", spf_path]
        if options:
            args_testbench.extend(options)

        self.run_executable(args_testbench, self.run_dir)

        return True