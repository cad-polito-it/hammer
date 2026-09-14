#  hammer-vlsi plugin for Synopsys PrimeTime.
#
#  See LICENSE for licence details.

from typing import List, Dict, Tuple, Any

import os
import errno

from hammer.vlsi import HammerTool, HammerTimingTool, HammerToolStep, HammerToolHookAction, \
       MMMCCornerType
from hammer.logging import HammerVLSILogging
import hammer.tech as hammer_tech
from hammer.common.synopsys import SynopsysTool

class PrimeTime(HammerTimingTool, SynopsysTool):

    def fill_outputs(self) -> bool:
        if self.slack_file is not None:
            self.output_slack_file = self.slack_file
        else:
            self.logger.warning("Timing tool did not generate any slack file for Small Delay Faults")
        return True

    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = dict(super().export_config_outputs())
        # The output slack file can be used by the atpg and the fsim
        outputs["timing.outputs.output_slack_file"] = self.output_slack_file
        return outputs

    def tool_config_prefix(self) -> str:
        return "timing.primetime"

    @property
    def env_vars(self) -> Dict[str, str]:
        v = dict(super().env_vars)
        v["PRIMETIME_BIN"] = self.get_setting("timing.primetime.primetime_bin")
        return v

    @property
    def steps(self) -> List[HammerToolStep]:
        steps = [
            self.init_environment,
            self.init_design,
            self.set_constraints,
            self.run_sta,
            self.generate_reports,
            self.run_primetime
        ]
        return self.make_steps_from_methods(steps)

    def init_environment(self) -> bool:
        # Actually use specified number of cores
        self.append("set disable_multicore_resource_checks true")
        self.append("set_host_options -max_cores %d" % self.get_setting("vlsi.core.max_threads"))

        # Search Path Setup
        self.append("set_app_var search_path \". %s $search_path\"" % self.result_dir)

        corners = self.get_mmmc_corners()  # type: List[MMMCCorner]
        
        corner_tt = next((corner for corner in corners if corner.type == MMMCCornerType.Extra), None)

        dbs = []
        # Library setup
        for db in self.timing_dbs(corner = corner_tt):
            if not os.path.exists(db):
                self.logger.error("Cannot find %s" % db)
                return False
            dbs.append(db)
        self.append("set_app_var target_library \"%s\"" % ' '.join(dbs))
        self.append("set_app_var link_library \"* $target_library\"")

        return True


    def init_design(self) -> bool:
        """ Load design and analysis corners """
        
        # Read timing databases
        
        corners = self.get_mmmc_corners()  # type: List[MMMCCorner]
        
        corner_tt = next((corner for corner in corners if corner.type == MMMCCornerType.Extra), None)

        # Library setup
        for db in self.timing_dbs(corner = corner_tt):
            self.append("read_db %s" % db)
        
        if not self.check_input_files([".v", ".v.gz"]):
            return False

        verilog = self.verilog 
        for v in verilog:
            if not os.path.exists(v):
                self.logger.error("Cannot find %s" % v)
                return False
        self.append("read_verilog  \"%s\"" % ' '.join(verilog))
        self.append("set_design_top " + self.top_module)
        self.append("link_design " + self.top_module)

        
        # Specify timing and design rule constraints set from the synthesis
        self.append("read_sdc %s" % self.post_synth_sdc)
        
        # Read Back-annotated delay data
        if self.sdf_file is not None:
            self.append("read_sdf -cond_use max -load_delay net -verbose " + os.path.join(os.getcwd(), self.sdf_file))

        # Read parasitics
        if self.spefs is not None: # post-P&R
            self.append("read_parasitics -keep_capacitive_coupling" + os.path.join(os.getcwd(), self.spefs[0]))
        
        return True

    def set_constraints(self) -> bool:
        """ Set contraints for Static Timing Analysis """
        
        # The following settings are needed for generating a slack based report for Small Delay Faults testing with ATPG and FSIM actions
        self.append("set timing_use_zero_slew_for_annotated_arcs never")
        self.append("set timing_save_pin_arrival_and_slack TRUE")
        self.append("set timing_update_status_level high")
        self.append("set timing_prelayout_scaling false")
        self.append("set pin_arrival_and_slack TRUE")
        
        # set_input_delay
        # set_output_delay
        # set_min_pulse_width
        # set_max_capacitance
        # set_min_capacitance
        # set_max_fanout
        # set_max_transition


        # Specify clock characteristics create_clock
        self.append(self.sdc_clock_constraints)

        # Specify timing exceptions set_multicycle_path
        # set_false_path
        # set_disable_timing

        # Specify the environment and analysis conditions such as operating conditions and delay models
        # set_operating_conditions
        # set_driving_cell
        # set_load
        # set_wire_load_model

        # Specify case and mode analysis settings
        # set_case_analysis
        # set_mode


        # Read power intent
        if self.get_setting("vlsi.inputs.power_spec_mode") != "empty":
            # Setup power settings from cpf/upf
            for l in self.generate_power_spec_commands():
                self.append(l)
        return True

    def run_sta(self) -> bool:
        """ Run Static Timing Analysis """

        self.append("update_timing -full")

        return True

    def generate_reports(self) -> bool:
        """Generate reports"""
        ## This is crucial for Small Delay Faults
        self.slack_file = f"{self.report_dir}/report_global_slack.rpt"
        self.append(f"report_global_slack -max -nosplit > {self.slack_file}")
        self.append(f"report_timing -nworst {self.max_paths} > {self.report_dir}/report_timing.rpt")
 
        self.append(f"report_global_timing > {self.report_dir}/report_global_timing.rpt")
        self.append(f"report_delay_calculation > {self.report_dir}/report_delay_calculation.rpt")
        self.append(f"report_constraint > {self.report_dir}/report_constraints.rpt")
        return True
    

    def run_primetime(self) -> bool:
        
        # Write main dofile
        timing_tcl = os.path.join(self.script_dir, "timing.tcl")
        
        with open(timing_tcl, 'w') as _f:
            _f.write('\n'.join(self.output))
            _f.write('\nexit')
        
        # Build args
        args = [
            self.get_setting("timing.primetime.primetime_bin"),
            "-no_init", # Don't load any setup files
            "-file", timing_tcl
        ]

        # Temporarily disable colours/tag to make run output more readable.
        # TODO: think of a more elegant way to do this?
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        self.run_executable(args, cwd=self.run_dir)
        # TODO: check for errors and deal with them
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        return True

   
tool = PrimeTime
