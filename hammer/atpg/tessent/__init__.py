#  hammer-vlsi for Siemens Tessent
#
#  See LICENSE for license details.

from hammer.vlsi import HammerATPGTool, HammerToolStep, TimeValue
from hammer.common.mentor import SiemensTool
from hammer.logging import HammerVLSILogging

from typing import Dict, List, Any, Optional
import hammer.tech
from hammer.tech import HammerTechnologyUtils

import os

class TESSENT(HammerATPGTool, SiemensTool):

    def tool_config_prefix(self) -> str:
        return "atpg.tessent"

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
        outputs["atpg.outputs.output_patterns_file"] = self.output_patterns_file
        outputs["atpg.outputs.output_executed_fault_sim"] = self.executed_fault_sim
        outputs["atpg.outputs.output_executed_generate_patterns"] = self.executed_generate_patterns
        outputs["atpg.outputs.output_patterns_source_kind"] = self.output_patterns_source_kind
        outputs["atpg.outputs.output_input_faults_file"] = self.output_input_faults_file
        return outputs

    @property
    def atpg_fault_type(self) -> str:
        """Map the Hammer ATPG fault model onto a Tessent 'set_fault_type' value."""
        if self.fault_model == "saf":
            return "stuck"
        elif self.fault_model == "tdf":
            return "transition"
        elif self.fault_model == "sdf":
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
            self.generate_fault_sim_reports,
            self.generate_testbench
            ])

    def do_post_steps(self) -> bool:
        assert super().do_post_steps()
        return self.run_tessent()

    def run_build(self) -> bool:
        """Perform ATPG build-related steps:

        1. Set the Tessent Shell pattern context
        2. Read the netlist(s)
        3. Auto black-box unsupported modules (e.g. hard macros/SRAMs)
        4. Set the current design

        Writes initial TCL lines into `self.output` for consumption by `run_atpg`.
        """
        log = HammerVLSILogging.context("atpg.tessent.build")

        if not hasattr(self, "_output"):
            self.attr_setter("_output", [])

        self.append('# Tessent ATPG build script')
        self.append(f'# top_module = {self.top_module}')

        # 1) Set the ATPG pattern context (scan)
        self.append("set_context patterns -scan")

        # Optional extra args to "set_read_verilog_options".
        set_read_verilog_options_args = self.get_setting("atpg.tessent.set_read_verilog_options_args", nullvalue=[])  # type: List[str]
        set_read_verilog_options_args_str = " ".join([a for a in set_read_verilog_options_args if a])
        if set_read_verilog_options_args_str:
            self.append(f"set_read_verilog_options {set_read_verilog_options_args_str}")

        # 2) Read the netlist(s)
        verilog = self.verilog + self.technology.read_libs([
            hammer.tech.filters.verilog_sim_filter
        ], HammerTechnologyUtils.to_plain_item,
        extra_pre_filters=[hammer.tech.filters.technology_filter])
        for v in verilog:
            if not os.path.exists(v):
                self.logger.error("Cannot find %s" % v)
                return False

        for v in verilog:
            self.append(f"read_verilog {v}")

            
        # 3) Set the current design
        self.append(f"set_current_design {self.top_module}")

        # 4) Auto black-box unsupported modules
        add_black_boxes_args = self.get_setting("atpg.tessent.add_black_boxes_args", nullvalue=[])  # type: List[str]
        add_black_boxes_args_str = " ".join([a for a in add_black_boxes_args if a])
        if add_black_boxes_args_str:
            self.append(f"add_black_boxes {add_black_boxes_args_str}")
        else:
            self.append("add_black_boxes -auto")
        
        # Exclude black-boxed SRAM/extra-library modules from faulting (they have no fault model). 
        # add_nofaults is setup-mode only, so it must run here
        for sram_name in self.technology.get_extra_libraries_name():
            self.append(f'add_nofaults {sram_name} -Module')

        return True

    def run_drc(self) -> bool:
        """Run Tessent DRC on the design.

        Sources the dofile.do (scan chain/clock setup, ...) which is expected
        to live in the same directory as the SPF file, then switches to
        analysis system mode to run Tessent's internal DRC rules.
        """
        spf_path = os.path.abspath(self.spf_file) if self.spf_file else ""
        if not spf_path:
            self.logger.error("No SPF file specified for ATPG; cannot locate the ATPG dofile")
            return False

        dofile_path = spf_path + ".do"
        self.append(f"source {dofile_path}")

        self.append("set_system_mode analysis")

        return True

    def run_atpg(self) -> bool:
        """Run the ATPG stage following the canonical ATPG flow.

        Prepares the design for ATPG, sets up the fault list, and runs
        pattern generation (if requested).

        This method builds `self.output` (TCL/dofile lines) which `run_tessent`
        writes and passes to the Tessent Shell binary.
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

        try:
            _ = self.output
        except Exception:
            self.attr_setter("_output", [])

        self.append('# Tessent ATPG flow script')
        self.append(f'# top_module = {self.top_module}')

        # Prepare for ATPG: set the fault type and create the fault list
        self.append(f'set_fault_type {self.atpg_fault_type}')

        if self.fault_model == "sdf":
            # Optional extra args to "read_sdf".
            read_sdf_args = self.get_setting("atpg.tessent.read_sdf_args", nullvalue=[])  # type: List[str]
            read_sdf_args_str = " ".join([a for a in read_sdf_args if a])
            if read_sdf_args_str:
                self.append(f'read_sdf {self.sdf_file} {read_sdf_args_str}')
            else:
                self.append(f'read_sdf {self.sdf_file}')

            # Optional extra args to "set_atpg_timing on".
            set_atpg_timing_args = self.get_setting("atpg.tessent.set_atpg_timing_args", nullvalue=[])  # type: List[str]
            set_atpg_timing_args_str = " ".join([a for a in set_atpg_timing_args if a])
            if set_atpg_timing_args_str:
                self.append(f'set_atpg_timing on {set_atpg_timing_args_str}')
            else:
                self.append('set_atpg_timing on')

            self._define_clock_waveforms()

        self._load_faults()

        self.append("set_system_mode atpg")
        # Run automatic test pattern generation
        if self.create_patterns:
            target_coverage = self.get_setting("atpg.target_coverage", nullvalue=100.00)
            self.append(f'set_atpg_limits -test_coverage {target_coverage}')

            max_test_patterns = self.get_setting("atpg.max_test_patterns", nullvalue=0)
            if max_test_patterns > 0:
                self.append(f'set_atpg_limits -pattern_count {max_test_patterns}')
            
            create_patterns_args = self.get_setting("atpg.tessent.create_patterns_args", nullvalue=[])  # type: List[str]
            create_patterns_args_str = " ".join([a for a in create_patterns_args if a])

            self.generated_patterns_path = os.path.join(self.result_dir, f'{self.top_module}_patterns.stil')
            self.generated_testbench_path = os.path.join(self.result_dir, f'{self.top_module}_patterns_testbench.v')
            self.append(f'# create_patterns -> generate patterns to {self.generated_patterns_path}')

            if create_patterns_args_str:
                self.append(f'create_patterns {create_patterns_args_str}')
            else:
                self.append('create_patterns')

            # Optional extra args to "write_patterns" from the Hammer config.
            write_patterns_args = self.get_setting("atpg.tessent.write_patterns_args", nullvalue=[])  # type: List[str]
            write_patterns_args_str = " ".join([a for a in write_patterns_args if a])
            if write_patterns_args_str:
                self.append(f'write_patterns {self.generated_patterns_path} -stil {write_patterns_args_str} -replace')
            else:
                self.append(f'write_patterns {self.generated_patterns_path} -stil -replace')
            self.patterns = self.generated_patterns_path

            self.did_generate_patterns = True
            self.patterns_source_kind = "generated"

        return True

    def run_fault_sim(self) -> bool:
        """Run fault simulation either on generated patterns (default) or on
        a user-specified pattern file (PATTERNS_FILE=...)."""

        # Track whether we actually invoked fault simulation in this run.
        self.did_fault_sim = False

        log = HammerVLSILogging.context("atpg.fault_sim")

        user_patterns_file = self.patterns_file

        patterns_source = ""

        if user_patterns_file:
            # Mode 2: only fault simulation of an explicit user pattern file.
            patterns_source = os.path.abspath(user_patterns_file)
            self.patterns_source_kind = "user"
            log.debug(f"Fault simulation on user pattern file: {patterns_source}")
        elif self.did_generate_patterns == True:
            # Mode 1: fault simulation of freshly generated patterns.
            patterns_source = self.generated_patterns_path
            self.patterns_source_kind = "generated"
            log.debug(f"Fault simulation on generated pattern set: {patterns_source}")

        if patterns_source:
            self.append(f'# fault simulation using {self.patterns_source_kind} patterns from {patterns_source}')
            self.append("delete_faults -all")
            self._load_faults()
            self.append(f"read_patterns {patterns_source}")

            # Optional extra args to "simulate_patterns" from the Hammer config.
            simulate_patterns_args = self.get_setting("atpg.tessent.simulate_patterns_args", nullvalue=[])  # type: List[str]
            simulate_patterns_args_str = " ".join([a for a in simulate_patterns_args if a])
            if simulate_patterns_args_str:
                self.append(f'simulate_patterns {simulate_patterns_args_str}')
            else:
                self.append('simulate_patterns')
            self.did_fault_sim = True

        return True

    def _load_faults(self) -> None:
        """Append TCL commands to set up the fault list.

        If a faults_file is provided, reads faults from that file.
        Otherwise, adds all faults.
        """
        if self.faults_file:
            self.append(f'# reading faults from {self.faults_file}')
            self.append(f'read_faults {self.faults_file}')
        else:
            # Args to "add_faults" from the Hammer config.
            add_faults_args = self.get_setting("atpg.tessent.add_faults_args", nullvalue=[])  # type: List[str]
            add_faults_args_str = " ".join([a for a in add_faults_args if a])
            if add_faults_args_str:
                self.append(f'# adding faults with args {add_faults_args_str}')
                self.append(f"add_faults {add_faults_args_str}")
            else:
                self.append(f'# adding all faults for {self.top_module}')
                self.append("add_faults -all")

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

        write_faults_format = self.get_setting('atpg.tessent.write_faults_format')

        report_statistics_path = os.path.join(base_dir, f'{self.top_module}{suffix}_statistics.rpt')
        self.append(f"report_statistics -detailed_analysis > {report_statistics_path}")

        report_faults_path = os.path.join(base_dir, f'{self.top_module}{suffix}_faults.fau')
        self.append(f"write_faults {report_faults_path} -all -format {write_faults_format} -NOEQ -replace")

        report_faults_zoix_path = os.path.join(base_dir, f'{self.top_module}{suffix}_faults_zoix.fau')
        self.append(f"write_faults {report_faults_zoix_path} -all -verilog -no_subclass -format basic -replace")

        report_au_faults_path = os.path.join(base_dir, f'{self.top_module}{suffix}_au_faults.fau')
        self.append(f"write_faults {report_au_faults_path} -class AU -format {write_faults_format} -NOEQ -replace")

        report_faults_hierarchical_path = os.path.join(base_dir, f'{self.top_module}{suffix}_faults_hierarchical.fau')
        self.append(f"write_faults {report_faults_hierarchical_path} -hierarchy 1000 -format {write_faults_format} -NOEQ -replace")

        if self.fault_model == "sdf":
            report_faults_delay_data_path = os.path.join(base_dir, f'{self.top_module}{suffix}_faults_delay_data.fau')
            self.append(f"write_faults {report_faults_delay_data_path} -all -delay_data -format {write_faults_format} -NOEQ -replace")
            
            report_atpg_timing_path = os.path.join(base_dir, f'{self.top_module}{suffix}_atpg_timing.rpt')
            self.append(f"report_atpg_timing > {report_atpg_timing_path}")

        return True

    @property
    def env_vars(self) -> Dict[str, str]:
        env = dict(super().env_vars)
        env["PATH"] = "%s:%s" % (
            os.path.dirname(self.get_setting("atpg.tessent.tessent_bin")),
            os.environ["PATH"])
        return env

    def _write_atpg_dofile(self):
        spf_path = os.path.abspath(self.spf_file) if self.spf_file else ""
        if not spf_path:
            self.logger.error("No SPF file specified for ATPG; cannot locate the ATPG dofile")
            return False
        
        self.generate_dofile_from_spf(spf_path)

    def _define_clock_waveforms(self) -> None:
        """Emit 'set_atpg_timing -clock_waveform' for every known clock, plus a DEFAULT
        fallback for every other clock in the design, so timing-aware ATPG (e.g. -retarget)
        has a waveform to work with. """
        periods = {c.name: c.period for c in self.get_clock_ports() if c.period is not None}
        if not periods:
            return

        self.append('# Clock waveforms for timing-aware ATPG')
        for name, period in periods.items():
            period_ns = period.value_in_units("ns")
            half_ns = period_ns / 2
            self.append(f"set_atpg_timing -clock_waveform {name} {period_ns} {half_ns} {half_ns}")

        default_period_ns = TimeValue(self.get_setting("atpg.tessent.default_clock_period")).value_in_units("ns")
        self.append(f"set_atpg_timing -clock_waveform DEFAULT {default_period_ns} {default_period_ns / 2} {default_period_ns / 2}")

        self.append("set_clock_restriction on")

    def generate_testbench(self) -> bool:

        # Generate testbench based on the patterns source
        if self.did_generate_patterns:
            # Case 1: We generated patterns in this run -> testbench for generated patterns
            self.append(f'write_patterns {self.generated_testbench_path} -verilog -replace')
        elif self.did_fault_sim and self.patterns_file:
            # Case 2: Fault simulation only with user-provided patterns -> testbench for user patterns
            user_testbench_path = os.path.join(self.result_dir, f'{self.top_module}_user_testbench.v')
            self.append(f'write_patterns {user_testbench_path} -verilog -replace')

        return True

    def run_tessent(self) -> bool:
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        tessent_bin = os.path.basename(self.get_setting("atpg.tessent.tessent_bin"))
        tessent_dofile = os.path.join(self.script_dir, "tessent.do")
        # Annotate the generated dofile with ATPG settings for traceability
        dofile_lines = []
        dofile_lines.append('# Generated by hammer-vlsi tessent wrapper')
        dofile_lines.append('# atpg.tessent settings:')
        dofile_lines.append(f"#   create_patterns = {self.create_patterns}")
        dofile_lines.append(f"#   fault_model = {self.fault_model}")
        dofile_lines.append('')
        dofile_lines.extend(self.output)

        with open(tessent_dofile, 'w') as _f:
            _f.write('\n'.join(dofile_lines))
            _f.write('\nexit')
        
        self._write_atpg_dofile()
        args = [tessent_bin, "-shell", "-dofile", tessent_dofile]
        self.run_executable(args, self.run_dir)
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True
        return True

tool = TESSENT
