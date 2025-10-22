#  hammer-vlsi for Synopsys TestMax
#
#  See LICENSE for license details.

from hammer.vlsi import HammerATPGTool, HammerToolStep, HammerLSFSubmitCommand, HammerLSFSettings
from hammer.common.synopsys import SynopsysTool
from hammer.logging import HammerVLSILogging

from typing import Dict, List, Optional, Callable, Tuple

from hammer.vlsi import FlowLevel, TimeValue

import hammer.utils as hammer_utils
import hammer.tech as hammer_tech
from hammer.tech import HammerTechnologyUtils

import os
import re
import shutil
import json
from multiprocessing import Process

class TESTMAX(HammeraATPGTool, SynopsysTool):

    def tool_config_prefix(self) -> str:
        return "atpg.testmax"

    def fill_outputs(self) -> bool:
        # TODO: support automatic waveform generation in a similar fashion to SAIFs
        self.output_waveforms = []
        self.output_saifs = []
        self.output_top_module = self.top_module
        self.output_tb_name = self.get_setting("atpg.inputs.tb_name")
        self.output_tb_dut = self.get_setting("atpg.inputs.tb_dut")
        self.output_level = self.get_setting("atpg.inputs.level")
        return True

    @property
    def steps(self) -> List[HammerToolStep]:
        return self.make_steps_from_methods([
            self.run_build,
            self.run_drc,
            self.run_atpg,
            self.generate_reports
            ])

    def run_build(self) -> bool:
        ## placeholder
        return True

    def run_drc(self) -> bool:
        ## placeholder
        return True

    def run_atpg(self) -> bool:
        ## placeholder
        return True
    
    def generate_reports(self) -> bool:
        ## placeholder
        return True

    @property
    def env_vars(self) -> Dict[str, str]:
        env = dict(super().env_vars)
        env["PATH"] = "%s:%s" % (
            os.path.dirname(self.get_setting("atpg.testmax.testmax_bin")),
            os.environ["PATH"])
        return env

    def run_testmax(self) -> bool:
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        testmax_bin = os.path.basename(self.get_setting("atpg.testmax.dc_bin"))
        testmax_tcl = os.path.join(self.script_dir, "testmax.tcl")
        with open(dc_tcl, 'w') as _f:
            _f.write('\n'.join(self.output))
            _f.write('\nexit')
        args = [testmax_bin, "-64bit", "-f", testmax_tcl]
        # TODO: check outputs from lines?
        lines = self.run_executable(args, self.run_dir)
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True
        return True

tool = DC