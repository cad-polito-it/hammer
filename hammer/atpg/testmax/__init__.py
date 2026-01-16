#  hammer-vlsi for Synopsys TestMax
#
#  See LICENSE for license details.

from hammer.vlsi import HammerATPGTool, HammerToolStep
from hammer.common.synopsys import SynopsysTool
from hammer.logging import HammerVLSILogging

from typing import Dict, List
import hammer.tech
from hammer.tech import HammerTechnologyUtils

import os
from multiprocessing import Process

class TESTMAX(HammerATPGTool, SynopsysTool):

    def tool_config_prefix(self) -> str:
        return "atpg.testmax"

    def fill_outputs(self) -> bool:
        self.output_top_module = self.top_module
        self.output_tb_name = self.get_setting("atpg.inputs.tb_name")
        self.output_tb_dut = self.get_setting("atpg.inputs.tb_dut")
        self.output_level = self.get_setting("atpg.inputs.level")
        self.output_patterns = []
        self.create_patterns = self.get_setting('atpg.inputs.create_patterns')
        self.fault_model = self.get_setting('atpg.inputs.fault_model')
        self.max_patterns = self.get_setting('atpg.inputs.max_patterns')
        self.spf_file = self.get_setting('atpg.inputs.spf_file')
        return True

    @property
    def atpg_fault_model(self) -> str:
        if self.fault_model == "saf":
            return "stuck"
        elif self.fault_model == "tdf":
            return "transition"
        else:
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
            self.fill_outputs,
            self.run_build,
            self.run_drc,
            self.run_atpg,
            self.generate_reports
            ])

    def do_post_steps(self) -> bool:
        assert super().do_post_steps()
        return self.run_testmax()

    def run_build(self) -> bool:
        """Perform ATPG build-related steps (1-4):

        1. Prepare netlist(s)
        2. Read netlist(s)
        3. Read library models
        4. Build the ATPG design model

        Writes initial TCL lines into `self.output` for consumption by `run_atpg`.
        """
        log = HammerVLSILogging.context("atpg.testmax.build")

        if not hasattr(self, "_output"):
            self.attr_setter("_output", [])
        else:
            # Clear previous contents when starting a fresh build
            self.output.clear()
        self.append('# TestMax ATPG build script')
        self.append(f'# top_module = {self.top_module}')

        # 1) Read the netlist(s) + the library
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
        self.append("set_build -add_celldefine_nets")

        # Setting the severity of the rule B5 to warning so the black boxes are automatically set
        self.append("set_rules B5 warning")
        self.append("set_netlist -escape all")
        # 3) Build the ATPG design model
        self.append(f'run_build_model {self.top_module}')

        return True

    def run_drc(self) -> bool:
        self.append(f'run_drc {os.path.abspath(self.spf_file)}')
        return True

    def run_atpg(self) -> bool:
        """Run the ATPG stage following the canonical ATPG flow.

        5. (test DRC is run in the separate `run_drc` step)
        6. Prepare design for ATPG, set up fault list and options
        7. Run ATPG
        8. Analyze ATPG output and review coverage
        9. Write/save test patterns

        This method builds `self.output` (TCL/script lines) which `run_testmax`
        writes and passes to the TestMax binary.
        """
        log = HammerVLSILogging.context("atpg")

        # Announce configuration
        log.debug(
            f"ATPG create_patterns={self.create_patterns}, fault_model={self.fault_model}, \
             max_patterns={self.max_patterns}, "
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

        # 4) Test DRC is executed earlier (run_drc), so assume model is DRC-clean now.
        self.append('# test_drc assumed completed in run_drc step')

        # 5) Prepare for ATPG: set options and create fault list
        self.append(f'set_faults -model {self.atpg_fault_model}')
        self.append("add_nofaults -module \"fakeram.*\"")
        self.append("add_faults -all")
        if self.max_patterns is not None:
            self.append(f'set_atpg -patterns {self.max_patterns}')
        # if self.pattern_format is not None:
        #     self.append(f'# set_pattern_format {self.pattern_format}')

        # Create a default fault list placeholder
        # faultlist_setting = self.get_setting('atpg.testmax.faultlist')
        # fault_list_path = faultlist_setting if faultlist_setting is not None else os.path.join(self.run_dir, f'{self.top_module}_faultlist.txt')
        # self.append(f'# create_fault_list -> {fault_list_path}')

        # 6) Run automatic test pattern generation
        generated_pattern_path = None
        if self.create_patterns:
            # TODO
            # generated_pattern_path = self.get_setting('atpg.testmax.generated_patterns')
            if generated_pattern_path is None:
                generated_pattern_path = os.path.join(self.result_dir, f'{self.top_module}_patterns')
            self.append(f'# run_atpg -> generate patterns to {generated_pattern_path}')
            self.append(f'run_atpg')

        # 7) Write and save test patterns (already covered by generated_pattern_path)
        if generated_pattern_path is not None:
            self.append(f'write_patterns {generated_pattern_path} -internal -format stil -replace')

        # TODO
        # Fault-simulation: either on an existing pattern set or on generated patterns
        # existing_patterns = self.fault_simulate_existing
        # if existing_patterns:
        #     self.append(f'# fault_simulate existing patterns: {existing_patterns} -> {self.output_fault_list or os.path.join(self.run_dir, f"{self.top_module}_faults.txt")}')
        #     self.append(f'# TODO: invoke TestMax fault-sim on {existing_patterns} and write faults to {self.output_fault_list or os.path.join(self.run_dir, f"{self.top_module}_faults.txt")}')
        # elif generated_pattern_path and self.fault_simulate_generated:
        #     self.append(f'# fault_simulate generated patterns: {generated_pattern_path} -> {self.output_fault_list or os.path.join(self.run_dir, f"{self.top_module}_faults.txt")}')
        #     self.append(f'# TODO: invoke TestMax fault-sim on {generated_pattern_path} and write faults to {self.output_fault_list or os.path.join(self.run_dir, f"{self.top_module}_faults.txt")}')

        # TODO
        # Optionally print/export the fault list
        # if self.print_fault_list:
        #     self.append(f'# print_fault_list {self.output_fault_list or os.path.join(self.run_dir, f"{self.top_module}_faults.txt")}')
        #     self.append(f'write_faults {self.output_fault_list or os.path.join(self.run_dir, f"{self.top_module}_faults.txt")}')

        # Record outputs for downstream consumers (pattern files)
        if generated_pattern_path is not None:
            self.output_patterns = [generated_pattern_path]
        else:
            self.output_patterns = []

        # Ensure fault list output path is set (downstream can check existence)
        #self.output_fault_list = self.output_fault_list or os.path.join(self.run_dir, f'{self.top_module}_faults.txt')

        return True

    def generate_reports(self) -> bool:
        report_faults_path = os.path.join(self.report_dir, f'{self.top_module}_faults.fau')
        self.append(f"report_faults -all > {report_faults_path}")

        report_au_faults_path = os.path.join(self.report_dir, f'{self.top_module}_au_faults.fau')
        self.append(f"report_faults -class AU > {report_au_faults_path}")

        atpg_untestable_path = os.path.join(self.report_dir, f'{self.top_module}_au_analysis.rpt')
        self.append(f"analyze_faults -class AU > {atpg_untestable_path}")
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
        tcl_lines.append('exit')
        with open(testmax_tcl, 'w') as _f:
            _f.write('\n'.join(tcl_lines))
            _f.write('\nexit')
        args = [testmax_bin, "-shell", "-64bit", testmax_tcl]
        lines = self.run_executable(args, self.run_dir)
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True
        return True

tool = TESTMAX