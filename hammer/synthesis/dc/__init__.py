#  hammer-vlsi plugin for Synopsys DC.
#
#  See LICENSE for licence details.



from typing import List, Optional, Dict, Any

import os
import re

from hammer.vlsi import HammerSynthesisTool, HammerToolStep
from hammer.logging import HammerVLSILogging
import hammer.tech
from hammer.tech import HammerTechnologyUtils
from hammer.common.synopsys import SynopsysTool

class DC(HammerSynthesisTool, SynopsysTool):
    def fill_outputs(self) -> bool:
        # Check that the regs paths were written properly if the write_regs step was run
        self.output_seq_cells = self.all_cells_path
        self.output_all_regs = self.all_regs_path
        if self.ran_write_regs:
            if not os.path.isfile(self.all_cells_path):
                raise ValueError("Output find_regs_cells.json %s not found" % (self.all_cells_path))

            if not os.path.isfile(self.all_regs_path):
                raise ValueError("Output find_regs_paths.json %s not found" % (self.all_regs_path))

            if not self.process_reg_paths(self.all_regs_path):
                self.logger.error("Failed to process all register paths")
        else:
            self.logger.info("Did not run write_regs")
        # Check that the mapped.v exists if the synthesis run was successful
        # TODO: move this check upwards?
        mapped_v = os.path.join(self.result_dir, self.top_module + ".mapped.v")
        if not os.path.isfile(mapped_v):
            raise ValueError("Output mapped verilog %s not found" % (mapped_v))  # better error?
        self.output_files = [mapped_v]
        # DC does not
        self.output_sdc = self.post_synth_sdc
        self.sdf_file = self.output_sdf_path
        output_spf = os.path.join(self.result_dir, self.top_module + "_test_protocol.spf")
        self.spf_file = output_spf
        if self.ran_write_outputs:
            if not os.path.isfile(mapped_v):
                raise ValueError("Output mapped verilog %s not found" % (mapped_v)) # better error?

            if not os.path.isfile(self.output_sdc):
                self.logger.warning("Output SDC %s not found" % (self.mapped_sdc_path)) # better error?

            if not os.path.isfile(self.output_sdf_path):
                self.logger.warning("Output SDF %s not found" % (self.output_sdf_path))

            if not os.path.isfile(self.spf_file):
                self.logger.warning("Output SPF %s not found" % (self.spf_file))
        else:
            self.logger.info("Did not run write_outputs")

        return True

    def tool_config_prefix(self) -> str:
        return "synthesis.dc"

    @property
    def all_regs_path(self) -> str:
        return os.path.join(self.run_dir, "find_regs_paths.json")

    @property
    def all_cells_path(self) -> str:
        return os.path.join(self.run_dir, "find_regs_cells.json")

    @property
    def ran_write_regs(self) -> bool:
        """The write_regs step sets this to True if it was run."""
        return self.attr_getter("_ran_write_regs", False)

    @ran_write_regs.setter
    def ran_write_regs(self, val: bool) -> None:
        self.attr_setter("_ran_write_regs", val)

    @property
    def ran_write_outputs(self) -> bool:
        """The write_ouputs step sets this to True if it was run."""
        return self.attr_getter("_ran_write_outputs", False)

    @ran_write_outputs.setter
    def ran_write_outputs(self, val: bool) -> None:
        self.attr_setter("_ran_write_outputs", val)

    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = dict(super().export_config_outputs())
        # TODO(edwardw): find a "safer" way of passing around these settings keys.
        outputs["synthesis.outputs.sdc"] = self.output_sdc
        outputs["synthesis.outputs.seq_cells"] = self.output_seq_cells
        outputs["synthesis.outputs.all_regs"] = self.output_all_regs
        outputs["synthesis.outputs.sdf_file"] = self.output_sdf_path
        outputs["synthesis.outputs.spf_file"] = self.spf_file
        return outputs

    @property
    def post_synth_sdc(self) -> Optional[str]:
        return os.path.join(self.result_dir, self.top_module + ".mapped.sdc")

    @property
    def output_sdf_path(self) -> str:
        return os.path.join(self.result_dir, self.top_module + ".mapped.sdf")

    @property
    def steps(self) -> List[HammerToolStep]:
        steps = [
            self.init_environment,
            self.elaborate_design,
            self.apply_constraints]
        if self.get_setting("synthesis.dc.dft_insertion"):
            steps.append(self.insert_dft)
        steps.extend([self.optimize_design,
            self.generate_reports,
            self.generate_dft_reports,
            self.write_outputs,
            self.write_regs])
        return self.make_steps_from_methods(steps)

    def do_post_steps(self) -> bool:
        assert super().do_post_steps()
        return self.run_design_compiler()

    @property
    def output(self) -> List[str]:
        """
        Buffered output to be put into dc.tcl.
        """
        return self.attr_getter("_output", [])

    def append(self, cmd: str) -> None:
        self.tcl_append(cmd, self.output)


    def init_environment(self) -> bool:
        # The following setting removes new variable info messages from the end of the log file
        self.append("set_app_var sh_new_variable_message false")

        # Actually use specified number of cores
        self.append("set disable_multicore_resource_checks true")
        self.append("set_host_options -max_cores %d" % self.get_setting("vlsi.core.max_threads"))

        # Change alib_library_analysis_path to point to a central cache of analyzed libraries
        # to save runtime and disk space.  The following setting only reflects the
        # default value and should be changed to a central location for best results.
        self.append("set_app_var alib_library_analysis_path alib")

        # Search Path Setup
        self.append("set_app_var search_path \". %s $search_path\"" % self.result_dir)

        # Library setup
        for db in self.timing_dbs:
            if not os.path.exists(db):
                self.logger.error("Cannot find %s" % db)
                return False
        self.append("set_app_var target_library \"%s\"" % ' '.join(self.timing_dbs))
        #self.append("set_app_var synthetic_library dw_foundation.sldb")
        self.append("set_app_var link_library \"* $target_library $synthetic_library\"")

        # For designs that don't have tight QoR constraints and don't have register retiming,
        # you can use the following variable to enable the highest productivity single pass flow.
        # This flow modifies the optimizations to make verification easier.
        # This variable setting should be applied prior to reading in the RTL for the design.
        self.append("set_app_var simplified_verification_mode true")
        self.append("set_svf results/%s.mapped.svf" % self.top_module)

        return True

    def elaborate_design(self) -> bool:
        # Add any verilog_synth wrappers
        # (which are needed in some technologies e.g. for SRAMs)
        # which need to be synthesized.
        verilog = self.verilog + self.technology.read_libs([
            hammer.tech.filters.verilog_synth_filter
        ], HammerTechnologyUtils.to_plain_item)
        for v in verilog:
            if not os.path.exists(v):
                self.logger.error("Cannot find %s" % v)
                return False
        # Read RTL
        self.append("define_design_lib WORK -path ./WORK")
        self.append("analyze -format sverilog \"%s\"" % ' '.join(verilog))

        # Elaborate design
        self.append("elaborate %s" % self.top_module)

        # Se the current design
        self.append("current_design %s" % self.top_module)

        # Link the design for possible unresolved errors
        self.append("link")

        # Set Rams as black boxes
        sram_libs = self.technology.get_extra_libraries_name()
        for sram_name in sram_libs:
            self.append(f"foreach_in_collection ram [get_references -hierarchical \"*{sram_name}*\"] {{")
            #self.append("set_attribute $ram is_black_box true")
            self.append("set_attribute $ram is_memory_cell true")
            self.append("set_attribute $ram is_physical_black_box true" )
            self.append("set_dont_touch $ram")
            self.append("}")

        return True

    def apply_constraints(self) -> bool:
        # Generate clock
        clocks = [clock.name for clock in self.get_clock_ports()]
        self.append(self.sdc_clock_constraints)

        # Set ungroup
        for module in self.get_setting('vlsi.inputs.no_ungroup'):
            self.append("set_ungroup [get_designs %s] false" % module)

        # Set retmining
        for module in self.get_setting("vlsi.inputs.retimed_modules"):
            self.append(' '.join([
                "set_optimize_registers", "true",
                "-design", module,
                "-clock", "{%s}" % ' '.join(clocks)
            ] + self.get_setting("synthesis.dc.retiming_args")))

        # Create Default Path Groups
        self.append("""
set ports_clock_root [filter_collection [get_attribute [get_clocks] sources] object_class==port]
group_path -name REGOUT -to [all_outputs]
group_path -name REGIN -from [remove_from_collection [all_inputs] ${ports_clock_root}]
group_path -name FEEDTHROUGH -from [remove_from_collection [all_inputs] ${ports_clock_root}] -to [all_outputs]
""")
        # Prevent assignment statements in the Verilog netlist.
        self.append("set_fix_multiple_port_nets -all -buffer_constants")
        self.append("change_names -rules verilog -hierarchy")
        return True


    def optimize_design(self) -> bool:
        # Optimize design
        self.append("compile_ultra %s" % ' '.join(self.get_setting("vlsi.core.synthesis_tool.compile_args")))

        # Write and close SVF file and make it available for immediate use
        self.append("set_svf -off")
        return True

    def generate_reports(self) -> bool:
        # Naming rules
        self.append("change_names -rules verilog -hierarchy")
        self.append("""
report_reference -hierarchy > \\
    {report_dir}/{design_name}.mapped.report_reference.out
report_qor > \\
    {report_dir}/{design_name}.mapped.qor.rpt
report_area -nosplit > \\
    {report_dir}/{design_name}.mapped.area.rpt
report_timing -max_paths 500 -nworst 10 -input_pins -capacitance \\
    -significant_digits 4 -transition_time -nets -attributes -nosplit > \\
    {report_dir}/{design_name}.mapped.timing.rpt

report_power -nosplit > \\
    {report_dir}/{design_name}.mapped.power.rpt
report_clock_gating -nosplit > \\
    {report_dir}/{design_name}.mapped.clock_gating.rpt
""".format(report_dir=self.report_dir, design_name=self.top_module))
        return True

    def write_outputs(self) -> bool:
        self.append("""
write -format verilog -hierarchy -output \\
    {result_dir}/{design_name}.mapped.v
write -format ddc -hierarchy -output \\
    {result_dir}/{design_name}.mapped.ddc
write_sdc -nosplit \\
    {result_dir}/{design_name}.mapped.sdc
write_sdf -version 2.1 -significant_digits 9 \\
    {sdf_file_path}
""".format(result_dir=self.result_dir, design_name=self.top_module, sdf_file_path = self.output_sdf_path))
        self.ran_write_outputs = True 
        return True

    def generate_dft_reports(self) -> bool:
        self.append("""
write_test_protocol -output {result_dir}/{design_name}_test_protocol.spf
""".format(result_dir=self.result_dir, design_name=self.top_module))
        self.append("""
write_scan_def -output {result_dir}/{design_name}_report_dft.scandef
""".format(result_dir=self.result_dir, design_name=self.top_module))

        return True

    def write_regs(self) -> bool:
        """write regs info to be read in for simulation register forcing"""
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.append(self.child_modules_tcl())
        self.append(self.write_regs_tcl())
        self.ran_write_regs = True
        return True

    def _insert_test_points(self) -> str:
        """Insert Test Points for Rams"""
        sram_libs = self.technology.get_extra_libraries_name()
        
        # Create a space-separated string of patterns for Tcl
        # Example: "*RAM_A* *RAM_B*"
        rams_pattern = " ".join([f"\"*{name}*\"" for name in sram_libs])

        return f"""
set_testability_configuration -control_signal test_mode
set_testability_configuration -target shadow_wrapper -isolate_elements [get_cells -hierarchical [ {rams_pattern} ]]

# get_shadow_wrapper_pins.tcl - get candidate shadow wrapper pins of a cell
# chrispy@synopsys.com
#
# v1.0  04/27/2015 chrispy
#  initial release

proc get_shadow_wrapper_pins {{args}} {{
 parse_proc_arguments -args $args results
 if {{[set cells [get_cells $results(cells)]] eq {{}}}} {{return}}

 set pins {{}}
 foreach_in_collection cell $cells {{
  foreach_in_collection pin [get_pins -quiet -of $cell -filter "pin_direction == $results(-direction)"] {{
   if {{[get_attribute -quiet $pin is_clock_pin] eq true}} {{continue}}
   if {{[get_attribute -quiet $pin is_async_pin] eq true}} {{continue}}
   switch -exact $results(-direction) {{
    in {{
     if {{[get_attribute -quiet $pin signal_type] ne {{}}}} {{continue}}
     if {{[all_fanin -trace all -flat -startpoints_only -to $pin] eq {{}}}} {{continue}}
    }}
    out {{
     if {{[all_fanout -trace all -flat -endpoints_only -from $pin] eq {{}}}} {{continue}}
    }}
   }}
   append_to_collection pins $pin
  }}
 }}

 return [sort_collection -dictionary $pins full_name]
}}

define_proc_attributes get_shadow_wrapper_pins \\
 -info "Get candidate shadow wrapper pins of a cell" \\
 -define_args \\
 {{
    {{-direction "Pin direction to return" direction one_of_string {{required value_help {{values {{in out}}}}}}}}
    {{cells "Cells to examine" "cells" string required}}
 }}


foreach_in_collection cell [get_cells -hierarchical "mem_*_*" -filter "is_memory_cell==true" ] {{
# add observe points at data input pins
set_test_point_element -type observe [get_shadow_wrapper_pins $cell -direction in]

# add control_01 points at data output pins
set_test_point_element -type control_01 [get_shadow_wrapper_pins $cell -direction out]
}}


foreach_in_collection cell [get_cells -hierarchical [{rams_pattern} ]] {{
# add observe points at data input pins
set_test_point_element -type observe [get_shadow_wrapper_pins $cell -direction in]

# add control_01 points at data output pins
set_test_point_element -type control_01 [get_shadow_wrapper_pins $cell -direction out]

}}
"""

    def insert_dft(self) -> bool:
        # Let's keep them here, in case we will need those signals, clock and reset are defined through the yml
        clocks = [clock.name for clock in self.get_clock_ports()]
        resets = [reset.name for reset in self.get_reset_ports()]
        reset_active_negated = [0 if reset.active_negated else 1 for reset in self.get_reset_ports()]
        self.append("set compile_delete_unloaded_sequential_cells true")
        self.append("set_scan_configuration -style %s" % self.get_setting("synthesis.dc.dft.scan.style"))
        self.append("compile -scan")

        self.append("set_scan_configuration -chain_count %d -clock_mixing mix_clocks" % self.get_setting("synthesis.dc.dft.scan.chain_count"))

        # Enable DfT
        if self.get_setting("synthesis.dc.insert_dft.bsd"):
            use_bsd = "enable"
        else:
            use_bsd = "disable"

        if self.get_setting("synthesis.dc.insert_dft.scan"):
            use_scan = "enable"
        else:
            use_scan = "disable"

        if self.get_setting("synthesis.dc.insert_dft.scan_compression"):
            use_scan_compression = "enable"
        else:
            use_scan_compression = "disable"

        self.append(f"set_dft_configuration -bsd {use_bsd} -scan {use_scan} -scan_compression {use_scan_compression} -ieee_1500 disable" )
        self.append("# set_dft_configuration -wrapper enable -fix_clock enable -fix_set enable -fix_reset enable ")
        self.append("# set_wrapper_configuration -class shadow_wrapper  -style shared -use_dedicated_wrapper_clock false -mix_cells true  -safe_state 1 -core [get_references -hierarchical \"*ram*\"] ")

        # Define DfT signals
        for clock_port in self.get_setting("synthesis.dc.dft.scan.clock_ports"):
            name = clock_port.get("name")
            active_state = clock_port.get("active_state")
            timings = " ".join(clock_port.get("timings"))
            self.append(f"set_dft_signal -view existing_dft -type ScanClock -port \"{name}\" -timing [list {timings}] -active_state {active_state}")

        for reset_port in self.get_setting("synthesis.dc.dft.reset_ports"):
            name = reset_port.get("name")
            active_state = reset_port.get("active_state")
            self.append(f"set_dft_signal -view existing_dft -type Reset -port \"{name}\"  -active_state {active_state}")

        # Define/create ports
        for port in self.get_setting("synthesis.dc.dft.scan.ports"):
            name      = port.get("name")
            direction = port.get("direction")
            p_type    = port.get("type")
            existing  = port.get("exist")

            if not existing:
                self.append(f"create_port {name} -direction {direction}" )
                view_type = "spec"
            else:
                view_type = "existing_dft"
            self.append(f"set_dft_signal -view {view_type} -type {p_type} -port \"{name}\"")

        # Add JTAG signals
        self.append("set_dft_signal -view existing_dft -type TDI -port \"%s\" -hookup_pin \"iocell_jtag_TDI/pad\"" % self.get_setting("synthesis.dc.dft.jtag.tdi"))
        self.append("set_dft_signal -view existing_dft -type TRST -port \"%s\" -hookup_pin \"iocell_jtag_reset/pad\" -active_state 1" % self.get_setting("synthesis.dc.dft.jtag.reset"))
        self.append("set_dft_signal -view existing_dft -type TCK -port \"%s\" -hookup_pin \"iocell_jtag_reset/pad\" -timing [list 45 95] -active_state 1" % self.get_setting("synthesis.dc.dft.jtag.clk"))
        self.append("set_dft_signal -view existing_dft -type TMS -port \"%s\" -hookup_pin \"iocell_jtag_TMS/pad\" -active_state 1" % self.get_setting("synthesis.dc.dft.jtag.tms"))
        self.append("set_dft_signal -view existing_dft -type TDO -port \"%s\" -hookup_pin \"iocell_jtag_TDO/pad\"" % self.get_setting("synthesis.dc.dft.jtag.tdo"))

        # Test se and Test mode signals created by default
        self.append("create_port test_se -direction in")
        self.append("create_port test_mode -direction in ")
        self.append("set_dft_signal -view spec -type ScanEnable -port test_se -active_state 1")
        self.append("set_dft_signal -view spec -type TestMode -port test_mode -active_state 1")

        self.append("set_dft_insertion_configuration -synthesis_optimization none")
        self.append("set_dft_configuration -testability enable")

        # Insert Test points for Rams
        if self.get_setting("synthesis.dc.insert_dft.memory_wrapper"):
            self.append(self._insert_test_points())

        # Preview all test structures to be inserted
        self.append("preview_dft -show all -test_wrappers all")
        self.append("report_dft_configuration")

        # Insert DFT and write out design
        self.append("create_test_protocol")
        self.append("dft_drc -verbose")
        # runs TestMAX Advisor to compute test points
        self.append("run_test_point_analysis")
        self.append("preview_dft -test_points all")
        # See the preview of DfT
        self.append("preview_dft ")
        self.append("dft_drc -verbose")
        self.append("insert_dft")

        return True

    @property
    def env_vars(self) -> Dict[str, str]:
        env = dict(super().env_vars)
        env["PATH"] = "%s:%s" % (
            os.path.dirname(self.get_setting("synthesis.dc.dc_bin")),
            os.environ["PATH"])
        # Spyglass is needed only for Test point insertion analysis during the dft insertion
        if self.get_setting("synthesis.dc.dft_insertion"):
            env["PATH"] = "%s:%s" % (
            os.path.dirname(self.get_setting("synthesis.spyglass.spyglass_bin")),
            os.environ["PATH"])
        return env

    def run_design_compiler(self) -> bool:
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        dc_bin = os.path.basename(self.get_setting("synthesis.dc.dc_bin"))
        dc_tcl = os.path.join(self.script_dir, "dc.tcl")
        with open(dc_tcl, 'w') as _f:
            _f.write('\n'.join(self.output))
            _f.write('\nexit')
        args = [dc_bin, "-64bit", "-f", dc_tcl]
        # TODO: check outputs from lines?
        lines = self.run_executable(args, self.run_dir)
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True
        return True

tool = DC
