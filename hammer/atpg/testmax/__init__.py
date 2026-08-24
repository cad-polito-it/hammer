#  hammer-vlsi for Synopsys TestMax
#
#  See LICENSE for license details.

from hammer.vlsi import HammerATPGTool, HammerToolStep
from hammer.common.synopsys import SynopsysTool
from hammer.logging import HammerVLSILogging

from typing import Dict, List, Any, Optional
import hammer.tech
from hammer.tech import HammerTechnologyUtils

import os
import re
from multiprocessing import Process
import re
import shutil

class TESTMAX(HammerATPGTool, SynopsysTool):

    _additional_modules: Optional[List[str]] = None

    @property
    def additional_modules(self) -> Optional[List[str]]:
        return self._additional_modules

    @additional_modules.setter
    def additional_modules(self, value: Optional[List[str]]) -> None:
        self._additional_modules = value

    def tool_config_prefix(self) -> str:
        return "atpg.testmax"

    def fill_outputs(self) -> bool:
        self.output_top_module = self.top_module
        self.output_tb_name = self.get_setting("atpg.inputs.tb_name")
        self.output_tb_dut = self.get_setting("atpg.inputs.tb_dut")
        self.output_level = self.get_setting("atpg.inputs.level")
        self.output_create_patterns = self.create_patterns 
        if self.create_patterns:
            self.output_patterns_file = self.patterns
        else:
            self.output_patterns_file = self.patterns_file
        self.executed_fault_sim = self.did_fault_sim
        self.executed_generate_patterns = self.did_generate_patterns
        self.output_patterns_source_kind = self.patterns_source_kind
        # Input fault list for ATPG run
        self.output_input_faults_file = self.faults_file
        return True 
    
    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = dict(super().export_config_outputs())
        outputs["atpg.outputs.output_top_module"] = self.output_top_module
        outputs["atpg.outputs.output_tb_name"] = self.output_tb_name
        outputs["atpg.outputs.output_tb_dut"] = self.output_tb_dut
        outputs["atpg.outputs.output_level"] = self.output_level
        outputs["atpg.outputs.output_create_patterns"] = self.output_create_patterns
        if self.create_patterns:
            outputs["atpg.outputs.output_patterns_file"] = self.output_patterns_file
        else:
            outputs["atpg.outputs.output_patterns_file"] = self.output_patterns_file
        outputs["atpg.outputs.output_executed_fault_sim"] = self.executed_fault_sim
        outputs["atpg.outputs.output_executed_generate_patterns"] = self.executed_generate_patterns
        outputs["atpg.outputs.output_patterns_source_kind"] = self.output_patterns_source_kind
        outputs["atpg.outputs.output_input_faults_file"] = self.output_input_faults_file
        if self.fault_model == "sdf": 
            outputs["atpg.outputs.sdf_time_margin"] = self.output_time_margin
        else:
            outputs["atpg.outputs.sdf_time_margin"] = 0.0
        return outputs
    
    @property
    def atpg_fault_model(self) -> str:
        if self.fault_model == "saf":
            return "stuck"
        elif self.fault_model == "tdf":
            return "transition"
        elif self.fault_model == "sdf":
            # Small delay faults are special transition delay faults
            return "transition"
        else:
            self.logger.warning(f"Fault model {self.fault_model} not yet supported. Defaulting to Stuck-at fault model")
            return "stuck" # in case of different values not supported at the moment, it returns the default value

    # Override to create folders for the specified fault model
    @property
    def result_dir(self) -> str:
        base_result_dir = super().result_dir
        specific_dir = os.path.join(base_result_dir, self.fault_model)
        os.makedirs(specific_dir, exist_ok=True)
        return specific_dir

    @property
    def report_dir(self) -> str:
        base_report_dir = super().report_dir
        specific_dir = os.path.join(base_report_dir, self.fault_model)
        os.makedirs(specific_dir, exist_ok=True)
        return specific_dir

    @property
    def steps(self) -> List[HammerToolStep]:
        return self.make_steps_from_methods([
            self.run_build,
            self.run_drc,
            self.run_atpg,
            self.generate_generation_reports,
            self.run_fault_sim,
            self.generate_fault_sim_reports
            ])

    def do_post_steps(self) -> bool:
        assert super().do_post_steps()
        return self.run_testmax()

    def run_build(self) -> bool:
        """Perform ATPG build-related steps (1-4):

        1. Read netlist(s)
        2. Read library models
        3. Build the ATPG design model

        Writes initial TCL lines into `self.output` for consumption by `run_atpg`.

        If present, include optional arguments from the Hammer config:
        - atpg.testmax.build_rules: list of dictionaries containing rules and their severity to set
        """
        log = HammerVLSILogging.context("atpg.testmax.build")

        if not hasattr(self, "_output"):
            self.attr_setter("_output", [])

        self.append('# TestMax ATPG build script')
        self.append(f'# top_module = {self.top_module}')

        # 1-2) Read the netlist(s) + the library
        verilog = self.verilog + self.technology.read_libs([
            hammer.tech.filters.verilog_sim_filter
        ], HammerTechnologyUtils.to_plain_item,
        extra_pre_filters=[hammer.tech.filters.technology_filter])
        for v in verilog:
            if not os.path.exists(v):
                self.logger.error("Cannot find %s" % v)
                return False

        for v in verilog:
            self.append(f"read_netlist {v}")

        self.append("set_build -merge noglobal_tie_propagate")
        self.append("set_build -nodelete_unused_gates")
        # The following line is important for beign able to fault simulate stil patterns in VC_Z01X/Z01X
        self.append("set_build -add_celldefine_nets")

        # Setting the severity of rules
        build_rules = self.get_setting("atpg.testmax.build_rules", nullvalue=[])  # type: List[dict]
        for rule in build_rules:
            self.append(f"set_rules {rule['rule_code']} {rule['severity']}")
        self.append("set_netlist -escape all")
        # 3) Build the ATPG design model
        self.append(f'run_build_model {self.top_module}')

        return True

    def run_drc(self) -> bool:
        """Run TestMAX DRC on the SPF produced after synthesis.

        If present, include optional arguments from the Hammer config:
        - atpg.testmax.set_drc_args: list of extra arguments for the "set_drc" command
        - atpg.testmax.run_drc_args: list of extra arguments for the "run_drc" command
        """

        # Optional "set_drc" arguments (can be an empty list or contain empty strings).
        set_drc_args = self.get_setting("atpg.testmax.set_drc_args", nullvalue=[])  # type: List[str]
        # Allow unstable set reset signals
        set_drc_args.append("-allow_unstable_set_reset")
        set_drc_args_str = " ".join([a for a in set_drc_args if a])

        if set_drc_args_str:
            # Example: set_drc -my_switch on
            self.append(f"set_drc {set_drc_args_str}")

        # Optional extra args to "run_drc".
        run_drc_args = self.get_setting("atpg.testmax.run_drc_args", nullvalue=[])  # type: List[str]
        run_drc_args_str = " ".join([a for a in run_drc_args if a])

        if self.fault_model == "sdf" :
            # This is a Tmax dependent flow
            self.slack_file = self.get_setting('timing.outputs.output_slack_file')

            self.append(f"read_timing {self.slack_file}")
            # Defines the cutoff for faults of interest for slack-based transition fault testing
            # generation. Faults with minimum slacks larger than the -max_tmgn parameter
            # are targeted by the normal transition fault ATPG algorithm rather than by the
            # slack-based algorithm.
            self.clocks = self.get_setting("vlsi.inputs.clocks")
            # Extract the clock value, for future use
            match = re.search(r"(\d*\.?\d+)\s*([a-zA-Z]+)", self.clocks[0].get('period'))
            if match:
                clock_value = match.group(1) 
                clock_time_unit = match.group(2)  
            else:
                self.logger.error("No clock value from clock specification")
                return False

            # Sets a level between the longest path and the path on which the fault is detected.
            # Full detection is still credited, and the fault is dropped from further 
            # The default is zero (full credit is given only when detection is on the minimum slack path).
            self.append("set_delay -max_delta_per_fault 0.0")
            
        spf_path = os.path.abspath(self.spf_file) if self.spf_file is not None else ""
        if run_drc_args_str:
            # Example: run_drc -my_option value <spf>
            self.append(f"run_drc {spf_path} {run_drc_args_str}")
        else:
            self.append(f"run_drc {spf_path}")
        return True

    def run_atpg(self) -> bool:
        """Run the ATPG stage following the canonical ATPG flow.
        4. Prepare design for ATPG, set up fault list and options
        5. Run ATPG for pattern generation or fault simulation
            5a. Write/save test patterns in case of generation

        This method builds `self.output` (TCL/script lines) which `run_testmax`
        writes and passes to the TestMax binary.
        """
        
        # Track whether we actually invoked generation in this run.
        self.did_generate_patterns = False
        
        # If the user specified a name for the patterns, the generation is 
        # skipped and only fault simulation is performed
        if self.patterns_file != None:
            self.create_patterns = False
        
        # Track the origin of the patterns used for fault simulation
        # ("generated" vs "user").
        self.patterns_source_kind = ""
        
        log = HammerVLSILogging.context("atpg")

        # Announce configuration
        log.debug(
            f"ATPG create_patterns={self.create_patterns}, fault_model={self.fault_model} "
        )

        # Ensure a build-stage has initialized the TCL output buffer. If not,
        # create a minimal header so this step can run standalone for now.
        # Accessing `self.output` via the property will create the underlying
        # storage (via attr_getter), so use it and mutate the list rather than
        # assigning to the read-only property.
        try:
            _ = self.output
        except Exception:
            # Force-create the underlying storage
            self.attr_setter("_output", [])
        # Now safely append lines to the TCL buffer
        self.append('# TestMax ATPG flow script')
        self.append(f'# top_module = {self.top_module}')

        # 4) Prepare for ATPG: set options and create fault list
        # Optional extra args to "set_faults".
        set_faults_args = self.get_setting("atpg.testmax.set_faults_args", nullvalue=[])  # type: List[str]
        set_faults_args_str = " ".join([a for a in set_faults_args if a])

        self.append(f'set_atpg -coverage {self.get_setting("atpg.target_coverage", nullvalue=100.00)}')
        self.append(f'set_faults -model {self.atpg_fault_model}')

        # Additional options
        if set_faults_args_str:
            self.append(f'set_faults {set_faults_args_str}')
        
        self._load_faults()

        if self.fault_model == "sdf" :
            self.append(f"set_delay -slackdata_for_atpg")

        if self.fault_model == "sdf" or self.fault_model == "tdf":
            self.append("set_delay -pi_changes")
            self.append("set_delay -nopo_measures")
        
        # if self.pattern_format is not None:
        #     self.append(f'# set_pattern_format {self.pattern_format}')

        # 5) Run automatic test pattern generation
        if self.create_patterns:
            set_atpg_args = self.get_setting("atpg.testmax.set_atpg_args", nullvalue=[])
            set_atpg_args_str = " ".join([a for a in set_atpg_args if a])

            target_max_patterns = f'-patterns {self.get_setting("atpg.max_test_patterns", nullvalue=0)}'

            self.append(f'set_atpg {target_max_patterns}')

            if set_atpg_args_str:
                self.append(f'set_atpg {set_atpg_args_str}')

            self.generated_pattern_path = os.path.join(self.result_dir, f'{self.top_module}_patterns')
            self.generated_testbench_path = os.path.join(self.result_dir, f'{self.top_module}_testbench.stil')
            self.append(f'# run_atpg -> generate patterns to {self.generated_pattern_path}')

            # Optional extra args to "run_atpg" from the Hammer config.
            run_atpg_args = self.get_setting("atpg.testmax.run_atpg_args", nullvalue=[])  # type: List[str]
            run_atpg_args_str = " ".join([a for a in run_atpg_args if a])
            # Setting the time margin for slack-based algorithm
            if self.fault_model == "sdf":
                self.time_margin = self.get_setting("atpg.testmax.time_margin", nullvalue='50%')  # type: str
                self.append(f"set_delay -max_tmgn {self.time_margin}")
            if run_atpg_args_str:
                self.append(f'run_atpg {run_atpg_args_str}')
            else:
                self.append('run_atpg')

            # 5a) Write and save test patterns (already covered by generated_pattern_path)
            self.append(f'write_patterns {self.generated_pattern_path} -internal -format stil -replace')
            # Stil pattern suitable for VC_Z01X/Z01X
            self.append(f'write_pattern {self.generated_pattern_path}.zoix -cellnames module  -format stil99 -replace -parallel -nocompaction -order_pins -nocompaction -internal_scancells')
            self.patterns = self.generated_pattern_path

            self.did_generate_patterns = True
            self.patterns_source_kind = "generated"

        return True

    def run_fault_sim(self) -> bool:
        """Run fault simulation either on generated patterns (default) or on
        a user-specified pattern file (PATTERNS_FILE=...)."""
        
        # Track whether we actually invoked fault simulation in this run.
        self.did_fault_sim = False

        log = HammerVLSILogging.context("atpg.fault_sim")

        generated_pattern_path = os.path.join(self.result_dir, f'{self.top_module}_patterns')
        user_patterns_file = self.patterns_file

        patterns_source = ""

        if self.fault_model == "sdf" :
            self.append(f"set_delay -slackdata_for_faultsim")

        if user_patterns_file:
            # Mode 2: only fault simulation of an explicit user pattern file.
            patterns_source = os.path.abspath(user_patterns_file)
            self.patterns_source_kind = "user"
            log.debug(f"Fault simulation on user pattern file: {patterns_source}")
        elif self.did_generate_patterns == True:
            # Mode 1: fault simulation of freshly generated patterns.
            patterns_source = generated_pattern_path
            self.patterns_source_kind = "generated"
            log.debug(f"Fault simulation on generated pattern set: {patterns_source}")

        if patterns_source:
            self.append(f'# fault simulation using {self.patterns_source_kind} patterns from {patterns_source}')
            self.append("remove_faults -all")
            self._load_faults()
            self.append(f'set_patterns -external {patterns_source}')

            # Optional extra args to "run_fault_sim" from the Hammer config.
            run_fault_sim_args = self.get_setting("atpg.testmax.run_fault_sim_args", nullvalue=[])  # type: List[str]
            run_fault_sim_args_str = " ".join([a for a in run_fault_sim_args if a])

            if run_fault_sim_args_str:
                self.append(f'run_fault_sim {run_fault_sim_args_str}')
            else:
                self.append('run_fault_sim')
            self.did_fault_sim = True

        return True

    def _load_faults(self) -> None:
        """Append TCL commands to set up the fault list.

        If a faults_file is provided, reads faults from that file.
        Otherwise, adds all faults (excluding fakeram modules).
        """
        if self._additional_modules != None:
            for module_name in self._additional_modules:
                self.append(f"add_nofaults -module \"{module_name}\"")

        sram_libs = self.technology.get_extra_libraries_name()
        for sram_name in sram_libs:
            self.append(f"add_nofaults -module \"{sram_name}.*\"")
        
        if self.faults_file:
            self.append(f'# reading faults from {self.faults_file} with force retain code')
            self.append(f'read_faults {self.faults_file} -force_retain_code')
        else:
            # Args to "add_faults" from the Hammer config.
            add_faults_args = self.get_setting("atpg.testmax.add_faults_args", nullvalue=[])  # type: List[str]
            add_faults_args_str = " ".join([a for a in add_faults_args if a])
            if add_faults_args_str:
                self.append(f'# adding faults with args {add_faults_args_str}')
                self.append(f"add_faults {add_faults_args_str}")
            else:
                self.append(f'# adding all faults for {self.top_module}')
                self.append("add_faults -all")

            # In case of delay-based fault model, we add clock and scan enable faults by default
            if self.fault_model == "sdf" or self.fault_model == "tdf":
                self.append("add_faults -clocks -scan_enable")
            
    def generate_generation_reports(self) -> bool:
        """Generate reports after pattern generation (no fault-sim yet).

        Only runs when patterns have been generated in this flow and no
        user-specified pattern file was used.
        """
        return self._generate_reports(mode="generation")

    def generate_fault_sim_reports(self) -> bool:
        """Generate reports after fault simulation.

        Uses different suffixes to distinguish between:
        - fault simulation of generated patterns (no suffix)
        - fault simulation of a user-specified pattern file ("_user")
        """
        return self._generate_reports(mode="fault_sim")

    def _generate_reports(self, mode: str) -> bool:
        """Internal helper to generate reports.

        mode = "generation"  -> use generation directory, no suffix
        mode = "fault_sim"   -> use fault_sim directory, optional "_user" suffix
        """

        if mode == "generation":
            if not getattr(self, "did_generate_patterns", False):
                return True

            base_dir = os.path.join(self.report_dir, "generation")
            suffix = ""
        elif mode == "fault_sim":
            if not getattr(self, "did_fault_sim", False):
                return True

            base_dir = os.path.join(self.report_dir, "fault_sim")
            kind = getattr(self, "patterns_source_kind", "")
            suffix = "_user" if kind == "user" else ""
        else:
            # Unknown mode: do nothing
            return True

        os.makedirs(base_dir, exist_ok=True)

        report_faults_path = os.path.join(base_dir, f'{self.top_module}{suffix}_faults.fau')
        self.append(f"report_faults -all > {report_faults_path}")

        report_faults_per_clock_path = os.path.join(base_dir, f'{self.top_module}{suffix}_faults_per_clock_domain.fau')
        self.append(f"report_faults -all -per_clock_domain > {report_faults_per_clock_path}")

        report_au_faults_path = os.path.join(base_dir, f'{self.top_module}{suffix}_au_faults.fau')
        self.append(f"report_faults -class AU > {report_au_faults_path}")

        atpg_untestable_path = os.path.join(base_dir, f'{self.top_module}{suffix}_au_analysis.rpt')
        self.append(f"analyze_faults -class AU > {atpg_untestable_path}")

        report_faults_summary = os.path.join(base_dir, f'{self.top_module}{suffix}_faults_summary.rpt')
        self.append(f"report_faults -summary > {report_faults_summary}")

        report_faults_hierarchical = os.path.join(base_dir, f'{self.top_module}{suffix}_faults_hierarchical.rpt')
        self.append(f"report_faults -level 1000 1 > {report_faults_hierarchical}")

        if self.fault_model == "sdf" :
            # Reports a histogram of faults based on the minimum slack numbers read in
            # by the read_timing command. This histogram is either fixed in the number
            # of buckets or fixed in the slack interval between two consecutive buckets. The
            # fixed number of buckets is specified by an integer and the fixed bucket interval is
            # specified with a float. The default is an integer of 10.
            report_faults_slack_tmgn = os.path.join(base_dir, f'{self.top_module}{suffix}_slack_tmgn.rpt')
            self.append(f"report_faults -slack TMgn 10 > {report_faults_slack_tmgn}")

            # Reports a histogram of faults based on the slack numbers for the detection
            # path for each fault (detection slacks). This histogram is either fixed in number
            # of buckets or fixed in the slack interval between two consecutive buckets. The
            # fixed number of buckets is specified by an integer and the fixed bucket interval is
            # specified with a float. The default is an integer of 10. 
            report_faults_slack_tdet = os.path.join(base_dir, f'{self.top_module}{suffix}_tdet.rpt')
            self.append(f"report_faults -slack TDet 10 > {report_faults_slack_tdet}")
            
            # Reports a histogram of faults based on the difference between detection slacks
            # and minimum slacks. The reported histogram is either fixed in number of buckets
            # or fixed in the slack interval between two consecutive buckets. The fixed number
            # of buckets is specified by an integer and the fixed bucket interval is specified with
            # a float. The default is an integer of 10.
            report_faults_slack_delta = os.path.join(base_dir, f'{self.top_module}{suffix}_slack.rpt')
            self.append(f"report_faults -slack DElta 10 > {report_faults_slack_delta}")
            
            # Reports a measure of the effectiveness of the slack-based transition fault set.
            # The measure varies from 0 percent (no faults of interest with detection slacks
            # smaller than the -max_tmgn parameter) to 100 percent (all faults of interest
            # detected on the minimum-slack path).
            report_faults_slack_effectiveness = os.path.join(base_dir, f'{self.top_module}{suffix}_slack_effectiveness.rpt')
            self.append(f"report_faults -slack EFfectiveness > {report_faults_slack_effectiveness}")

            # Let's share the report for future use 
            self.report_faults_slack_coverage = os.path.join(base_dir, f'{self.top_module}{suffix}_slack_coverage.rpt')
            self.append(f"report_faults -slack COVerage > {self.report_faults_slack_coverage}")
        return True

    def _extract_time_margin(self) -> None:
        """Extracts the floating-point value from the 'max tmgn' line in the slack coverage report file."""
        pattern = (
            r"The max tmgn for small delay defect faults has been set to\s+([\d.]+)"
        )
        value = 0.0 # Default value
        with open(self.report_faults_slack_coverage, "r") as file:
            for line in file:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    value = float(match.group(1))
        self.output_time_margin = value

    @property
    def env_vars(self) -> Dict[str, str]:
        env = dict(super().env_vars)
        env["PATH"] = "%s:%s" % (
            os.path.dirname(self.get_setting("atpg.testmax.testmax_bin")),
            os.environ["PATH"])
        return env

    def generate_testbench(self) -> bool:
        # Optional extra args to "stil2verilog".
        stil2verilog_options = self.get_setting("atpg.testmax.stil2verilog_options", nullvalue=[])  # type: List[str]

        # Generate testbench based on the patterns source
        if self.did_generate_patterns:
            # Case 1: We generated patterns in this run -> testbench for generated patterns
            self.write_testbench(self.generated_pattern_path, self.generated_testbench_path, stil2verilog_options)
        elif self.did_fault_sim and self.patterns_file:
            # Case 2: Fault simulation only with user-provided patterns -> testbench for user patterns
            user_testbench_path = os.path.join(self.result_dir, f'{self.top_module}_user_testbench')
            self.write_testbench(self.patterns_file, user_testbench_path, stil2verilog_options)

        return True

    def run_testmax(self) -> bool:
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        testmax_bin = os.path.basename(self.get_setting("atpg.testmax.testmax_bin"))
        testmax_tcl = os.path.join(self.script_dir, "testmax.tcl")
        # Annotate the generated TCL with ATPG settings for traceability
        tcl_lines = []
        tcl_lines.append('# Generated by hammer-vlsi testmax wrapper')
        tcl_lines.append('# atpg.testmax settings:')
        tcl_lines.append(f"#   create_patterns = {self.create_patterns}")
        tcl_lines.append(f"#   fault_model = {self.fault_model}")
        tcl_lines.append('')
        tcl_lines.extend(self.output)

        with open(testmax_tcl, 'w') as _f:
            _f.write('\n'.join(tcl_lines))
            _f.write('\nexit')
        args = [testmax_bin, "-shell", "-64bit", testmax_tcl]
        lines = self.run_executable(args, self.run_dir)
        self.generate_testbench()
        if self.fault_model == "sdf": 
            self._extract_time_margin()
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True
        return True

tool = TESTMAX