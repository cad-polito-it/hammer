#  hammer-vlsi plugin for Synopsys VC-Z01X
#
#  See LICENSE for license details.

from hammer.vlsi import HammerFaultSimTool, HammerToolStep, HammerLSFSubmitCommand, HammerLSFSettings
from hammer.common.synopsys import SynopsysTool
from hammer.logging import HammerVLSILogging

from typing import Dict, List, Optional, Callable, Tuple, Any

from hammer.vlsi import FlowLevel, TimeValue

import hammer.utils as hammer_utils
import hammer.tech as hammer_tech
from hammer.tech import HammerTechnologyUtils

import os
import re
import shutil
import json
import subprocess
from multiprocessing import Process

class VC_ZOIX(HammerFaultSimTool, SynopsysTool):

    def append_to_args(self, file_path: str, string_to_append: str) -> bool:
        """
        Finds the '-args' line in a file and appends a string inside the quotes.

        Args:
            file_path: The path to the file to modify.
            string_to_append: The new argument string to add.
        """
        modified_lines = []
        args_found = False
        exec_found = False
        created_report_dir = False
        reports_found = 0

        try:
            # Read all lines from the file into memory
            with open(file_path, 'r') as f:
                lines = f.readlines()

            # Process each line
            for line in lines:
                # Use regex to find the line with -args
                # This captures three groups:
                # 1: The part before the content (e.g., '    -args "')
                # 2: The content currently inside the quotes (e.g., '')
                # 3: The closing quote (e.g., '"')
                match = re.search(r'(\s*-args\s+")(.*?)"', line)

                # 2. Check for -exec
                # Regex: (\s*-exec\s+)(.*?)(\s*\\?\s*)$
                # This captures the prefix (group 1), the command (group 2),
                # and any trailing whitespace or line continuation '\' (group 3)
                exec_match = re.search(r'(\s*-exec\s+)(.*?)(\s*\\?\s*)$', line.rstrip())
                
                # 3. Check for -report lines
                # Regex: -report\s+([^\s]+)
                # This captures the specific filenames (group 2)
                report_match = re.search(r'-report\s+([^\s]+)', line)

                if match:
                    args_found = True
                    prefix = match.group(1)      # e.g., '    -args "'
                    
                    # Get any text that came *after* the closing quote (like a comment or newline)
                    trailing_chars = line[match.end():]

                    new_args = string_to_append

                    # Reconstruct the line
                    new_line = f'{prefix}{new_args}"{trailing_chars}'
                    modified_lines.append(new_line)
                elif exec_match:
                    # --- Handle -exec ---
                    exec_found = True
                    prefix = exec_match.group(1)        # '    -exec '
                    trailing_chars = exec_match.group(3) # ' \'
                    
                    # Standardize on forward slashes for tool compatibility
                    new_exec = self.simulator_executable_path

                    # Rebuild the line, adding the newline back
                    new_line = f'{prefix}{new_exec}{trailing_chars}\n'
                    modified_lines.append(new_line)
                elif report_match:
                    # --- Handle -report ---
                    reports_found += 1
                    # Prefix: Everything before the captured file name
                    prefix = line[:report_match.start(1)]
                    
                    # Get just the base filename, e.g., 'fsim_out.rpt'
                    report_filename = report_match.group(1)
                
                    # Suffix: Everything after the captured file name
                    suffix = line[report_match.end(1):]
                    # Split the basename into name and extension ("program_name", ".riscv")
                    benchmark_name, extension = os.path.splitext(os.path.basename(self.benchmarks[0]))

                    # --- Build new directory structure ---
                    if not created_report_dir: # Only run this once
                        # 1. Create the new directory path string
                        # e.g., "my/results/MyCoreConfig/_sa1_/fft"
                        self.report_folder = os.path.join(self.output_folder,
                                                    self.output_level,
                                                    self.fault_model, 
                                                    benchmark_name)

                        self.results_folder = os.path.join(self.run_dir, self.fault_model, benchmark_name)
                        
                        # 2. Create the directories if they don't exist
                        try:
                            os.makedirs(self.report_folder, exist_ok=True)
                            print(f"Ensured directory exists: {self.results_folder}")
                            created_report_dir = self.results_folder # Cache path
                        except OSError as e:
                            print(f"Error creating directory {self.results_folder}: {e}")
                            modified_lines.append(line) # Add original line and skip
                            continue
                    
                    # 3. Create the new full file path
                    # e.g., "my/results/MyCoreConfig/_sa1_/fft/fsim_out.rpt"
                    new_report_path = os.path.join(created_report_dir, report_filename)
                    # --- End new logic ---
                    
                    new_report_path = new_report_path.replace(os.path.sep, '/')
                    
                    # Rebuild the line
                    new_line = f'{prefix}{new_report_path}{suffix}'
                    modified_lines.append(new_line)
                    
                else:
                    # If it's not the line we're looking for, add it unchanged
                    modified_lines.append(line)

            if not args_found:
                print(f"Warning: '-args' line not found. File not modified for args.")
                return False
            if not exec_found:
                print(f"Warning: '-exec' line not found. File not modified for exec.")
                return False
            if not reports_found:
                print(f"Warning: 'fsim_out.rpt' or 'fsim_out_hier.rpt' not found.")

            # Write the modified lines back to the file
            with open(file_path, 'w') as f:
                f.writelines(modified_lines)

        except FileNotFoundError:
            self.logger.error("Error: File not found at {0}".format(file_path))
        except Exception as e:
            self.logger.error("An error occurred: {0}".format(e))

        return True

    def _read_slack_info(self, input_file: str, default_period: float) -> dict:
        fault_placement = {}
        # Pattern to capture rise, fall, and the point name
        pattern = r'^\s*([-+]?\d*\.?\d+|\*)\s+([-+]?\d*\.?\d+|\*)\s+(.*)$'
        
        # Use a default period if '*' is found
        with open(input_file, "r") as fin:
            for line in fin:
                line = line.strip()
                match = re.match(pattern, line)
                if match:
                    max_rise, max_fall, point = match.groups()
                        
                    max_rise_value = abs(float(max_rise)) if max_rise != '*' else default_period
                    max_fall_value = abs(float(max_fall)) if max_fall != '*' else default_period
                    
                    key = f"{self.campaign_tb_dut}.{point.replace('/', '.')}"
                    fault_placement[key] = {"R": max_rise_value, "F": max_fall_value}
        return fault_placement


    def _fuse_slack_with_sff(self, slack_file_path: str, fault_list_path: str) -> bool:
        """
        Updates the fault list file in-place with delay information from slack file generated by timing tool.
        """
        self.logger.info("Adding small delays in imported fault list")

        # Extract the time unit
        clock_value = self.clocks[0].get('period')
        match = re.search(r"(\d*\.?\d+)\s*([a-zA-Z]+)", clock_value)
        if match:
            clock_value = match.group(1) 
            clock_time_unit = match.group(2)  
        else:
            self.logger.error("No unit detected from clock specification")
            return False
        
        fault_placement = self._read_slack_info(slack_file_path,clock_value)
        
        pattern = r'\s*\s*([\w+-]+)\s*([RF])\s*\{PORT\s+"([^"]+)"\}'
        
        updated_lines = []
        
        with open(fault_list_path, "r") as fin:
            for line in fin:
                if "FaultGenerate" in line:
                    self.logger.error("Fault generate block present in converted fault list")
                    return False
                
                match = re.match(pattern, line.strip())
                if match:
                    status, value, port_raw = match.groups()
                    
                    if port_raw in fault_placement:
                        delay = fault_placement[port_raw][value]
                        timing_str = f"({delay}{clock_time_unit})"
                        # Rebuild line: note the double {{ }} for literal braces
                        new_line = f" {status} {value} {timing_str} {{PORT \"{port_raw}\"}}\n"
                        updated_lines.append(new_line)
                    else:
                        self.logger.error(f"Missing fault placement {port_raw} in slack file")
                        return False
                else:
                    updated_lines.append(line)
        
        with open(fault_list_path, "w") as fout:
            fout.writelines(updated_lines)
            
        return True


    def _add_delays(self, delay_str : str, fault_list_path : str) -> bool:
        """ Add delay str to converted fault list.
            It returns error in case there is a fault generate block
        """ 
        self.logger.info("Adding delays in imported fault list")
        fault_location_pattern = r"([RF])(\s+)(\{)"
        replacement = rf"\1 {delay_str}\2\3"

        updated_lines = []

        with open(fault_list_path, "r") as fault_list:
            for line in fault_list:
                if "FaultGenerate" in line:
                    self.log.error("Fault generate block present in converted fault list")
                    return False
                new_line = re.sub(fault_location_pattern, replacement, line)
                updated_lines.append(new_line)

        with open(fault_list_path, "w") as fault_list:
            fault_list.writelines(updated_lines)
        return True

    def tool_config_prefix(self) -> str:
        return "fsim.vc_zoix"

    def fill_outputs(self) -> bool:
# TODO: support automatic waveform generation for debugging Good Machine and Faulty Machine with Verdi
        self.output_waveforms = []
        self.output_top_module = self.top_module
        self.output_tb_name = self.get_setting("fsim.inputs.tb_name")
        self.output_tb_dut = self.tb_dut
        self.output_strobe_file_name = self.strobe_file_name
        self.output_fault_model = self.fault_model
        self.output_campaign_tb_dut = self.campaign_tb_dut
        self.output_campaign_tcl = self.campaign_tcl
        self.output_output_folder = self.output_folder
        self.output_standard_fault_format = self.standard_fault_format
        self.output_report_folder = self.report_folder
        return True 
        
    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = dict(super().export_config_outputs())
        outputs["fsim.outputs.output_top_module"] = self.output_top_module
        outputs["fsim.outputs.output_top_module"] = self.output_top_module
        outputs["fsim.outputs.output_tb_name"] = self.output_tb_name
        outputs["fsim.outputs.output_tb_dut"] = self.output_tb_dut
        outputs["fsim.outputs.output_strobe_file_name"] = self.output_strobe_file_name
        outputs["fsim.outputs.output_fault_model"] = self.output_fault_model
        outputs["fsim.outputs.output_campaign_tb_dut"] = self.output_campaign_tb_dut
        outputs["fsim.outputs.output_campaign_tcl"] = self.output_campaign_tcl
        outputs["fsim.outputs.output_output_folder"] = self.output_output_folder
        outputs["fsim.outputs.output_standard_fault_format"] = self.output_standard_fault_format
        outputs["fsim.outputs.output_report_folder"] = self.output_report_folder
        return outputs
    
    def generate_files(self) -> bool:
        self.strobe_file_name = self.get_setting("fsim.inputs.strobe_file_name")
        self.clocks = self.get_setting("vlsi.inputs.clocks")
        benchmark_name, extension = os.path.splitext(os.path.basename(self.benchmarks[0]))
        if not os.path.exists(os.path.join(self.run_dir, self.fault_model, benchmark_name)):
            os.makedirs(os.path.join(self.run_dir, self.fault_model, benchmark_name))
        if os.path.exists(self.strobe_file_name) == False:
            self.strobe_file_name = os.path.join(self.run_dir, self.fault_model, benchmark_name, "strobe.sv")
            os.makedirs(os.path.dirname(self.strobe_file_name), exist_ok=True)
            with open(self.strobe_file_name, "w") as f:
                f.write("`ifndef TOPLEVEL\n")
                f.write("    `define TOPLEVEL " + self.tb_dut + "\n")
                f.write("`endif\n")
                f.write("\n")
                f.write("module strobe;\n")
                f.write("\n")
                f.write("initial begin\n")
                f.write("    #`RESET_DELAY;\n")
                f.write("    $display(\"BEFORE ZOIX INJECTION\");\n")
                f.write("    $fs_inject;\n")
                f.write("    $display(\"ZOIX INJECTION\");\n")
                f.write("end\n")
                f.write("\n")
                f.write("always @(negedge `TOPLEVEL." + self.clocks[0].get('name') + ") begin\n")
                self.strobe_modules = self.get_setting("fsim.inputs.strobe_modules")
                for strobe_module in self.strobe_modules:
                    f.write("    $fs_strobe(" + strobe_module + ");\n")
                f.write("end\n")
                f.write("\n")
                f.write("endmodule\n")
        else:  
            # Use a custom fstrobe file 
            internal_strobe_file = os.path.join(self.run_dir, self.fault_model, benchmark_name, "strobe.sv")
            os.makedirs(os.path.dirname(internal_strobe_file), exist_ok=True)
            shutil.copy(self.strobe_file_name,internal_strobe_file)
            self.strobe_file_name = os.path.join(self.run_dir, self.fault_model, benchmark_name, "strobe.sv")
        self.output_level = self.get_setting("fsim.inputs.level")
        self.campaign_tb_dut = self.get_setting("fsim.inputs.campaign_tb_dut")
        self.campaign_tcl = self.get_setting("fsim.inputs.campaign_tcl")
        if os.path.exists(self.campaign_tcl):
            tcl_run_dir_path = os.path.join(self.run_dir, self.fault_model, benchmark_name)
            os.makedirs(tcl_run_dir_path, exist_ok=True)
            tcl_run_dir = os.path.join(tcl_run_dir_path, "fsim.tcl")
            with open(tcl_run_dir, "w") as f, open(self.campaign_tcl,"r") as template:
                for line in template:
                    if "test1" in line:
                        f.write(line.replace("test1",benchmark_name))
                    else:
                        f.write(line)
            # Update the modified TCL
            self.campaign_tcl = tcl_run_dir
        else: 
            self.logger.fatal(f"Base {self.campaign_tcl} does not exists! Please provide a valid path.")
        self.output_folder = self.get_setting("fsim.inputs.output_folder")
        # Generate faults iff the standard fault format does not exists
        self.fsim_generate_faults = False
        self.standard_fault_format = self.get_setting("fsim.inputs.standard_fault_format")
        if os.path.exists(self.standard_fault_format) == False:
            self.fsim_generate_faults = True 
            self.standard_fault_format = os.path.join(self.run_dir, self.fault_model, benchmark_name, "gen_" + self.fault_model + "_" + self.tb_dut.split(".")[-1] + ".sff")
            os.makedirs(os.path.dirname(self.standard_fault_format), exist_ok=True)
            with open(self.standard_fault_format, "w") as f:
                f.write("# Weights\n")
                f.write("Coverage\n")
                f.write("{\n")
                f.write("        Weights\n")
                f.write("        {\n")
                self.weights = self.get_setting("fsim.inputs.weights")
                for w in self.weights:
                    f.write("                " + w.get('fault_class') + "=" + w.get('weight') + ";\n")
                f.write("        }\n")
                self.test_coverage = self.get_setting("fsim.inputs.test_coverage")
                if self.test_coverage:
                    f.write("        \"Test Coverage\" = \"" + self.test_coverage + "\";\n")
                self.fault_coverage = self.get_setting("fsim.inputs.fault_coverage")
                if self.fault_coverage:
                    f.write("        \"Fault Coverage\" = \"" + self.fault_coverage + "\";\n") 
                self.custom_coverage_functions = self.get_setting("fsim.inputs.custom_coverage_functions")
                if self.custom_coverage_functions:
                    for ccf in self.custom_coverage_functions:
                        f.write("        \"" + ccf.get('name') +"\" = \"" + ccf.get('function') + "\";\n") 
                f.write("}\n")
                f.write("\n")
                self.constraints = self.get_setting("fsim.inputs.constraints")
                if self.constraints:
                    for constraint in self.constraints:
                        f.write("Constraint " + constraint.get('name') + "\n")
                        f.write("{\n")
                        for entry in constraint.get('entries'):
                            f.write("        " + entry.get('type') + " \"" + entry.get('signal') + "==" + entry.get('value') + "\";\n")
                        f.write("}\n")
                        f.write("\n")
                    f.write("\n")
                f.write("# Set fault generation constraints\n")
                f.write("FaultGenerate\n")
                f.write("{\n")
                self.fault_locations = self.get_setting("fsim.inputs.fault_locations")
                # If transition delay faults, we set the delay equal to the synthesis clock per fault basis
                if self.fault_model == "tdf" or self.fault_model == "sdf":
                    fault_delay_string = f"({self.clocks[0].get('period')})"
                    self.logger.info(f" Using delay-based fault model for Fault simulation! Remember to set simulation clock to {self.clocks[0].get('period')}")
                else:
                    fault_delay_string = ""
                
                if (self.fault_model == "saf" or self.fault_model == "tdf"):
                    for fl in self.fault_locations:
                        if fl.get('exclude'):
                            f.write("    Exclude {\n")
                            f.write("        NA [" + fl.get('fault_type') + "] " + fault_delay_string + " {" + fl.get('type_of_fault_location') + " \"" + fl.get('location') + "\" }\n")
                            f.write("    }\n")
                        else:
                            f.write("    NA [" + fl.get('fault_type') + "] " + fault_delay_string + " {" + fl.get('type_of_fault_location') + " \"" + fl.get('location') + "\" }\n")
                        f.write("\n")
                if self.fault_model == "sdf":
                    # Check for the slack file 
                    if self.slack_file is None or not(os.path.exists(self.slack_file)):
                        self.logger.error("Slack file for fault simulation of Small Delay faults does not exists")
                        return False

                if (self.fault_model == "tf"):
                    f.write("    Timing (\"" + self.clocks[0].get('name') + "\", CycleTime " + self.clocks[0].get('period') + ")\n")
                    f.write("    UseTiming(\"" + self.clocks[0].get('name') + "\")\n")
                    for fl in self.fault_locations:
                        if fl.exclude:
                            f.write("    Exclude {\n")
                            f.write("        NA ~ (" + fl.fault_type + ") {" + fl.type_of_fault_location + " \"" + fl.location + "\" }\n")
                            f.write("    }")
                        else:
                            f.write("    NA ~ (" + fl.fault_type + ") {" + fl.type_of_fault_location + " \"" + fl.location + "\" }\n")
                    f.write("\n")
                f.write("}\n")
        else:
            # This is primetime and vc zoix dependent
            if self.fault_model == "sdf":
                self.time_margin = self.get_setting("atpg.outputs.sdf_time_margin", None)
                self.slack_file = self.get_setting('timing.outputs.output_slack_file', None)         
                if self.slack_file is None:
                    self.logger.error("Slack file not defined")
                    return False
            # Use a custom sff file 
            filename, extension = os.path.splitext(os.path.basename(self.standard_fault_format))
            if self.standard_fault_format.endswith("sff"):
                internal_standard_fault_format = os.path.join(self.run_dir, self.fault_model, benchmark_name, filename + "_" + self.fault_model + "_" + self.tb_dut.split(".")[-1] + ".sff")
                shutil.copy(self.standard_fault_format, internal_standard_fault_format)
                with open(self.standard_fault_format, "r") as fin:
                    for line in fin:
                        if "FaultGenerate" in line:
                            self.fsim_generate_faults = True 
                if self.fault_model == "sdf":
                    if not(self._fuse_slack_with_sff(self.slack_file, internal_standard_fault_format)):
                        return False
            else:
                internal_standard_fault_format = os.path.join(self.run_dir, self.fault_model, benchmark_name, filename + "_" + self.fault_model + "_" + self.tb_dut.split(".")[-1] + extension)
                shutil.copy(self.standard_fault_format, internal_standard_fault_format)
                # If sdf, it must be converted first and then delays can be added
            os.makedirs(os.path.dirname(internal_standard_fault_format), exist_ok=True)
            self.standard_fault_format = internal_standard_fault_format
        self.report_folder = ""
        return True

    @property
    def steps(self) -> List[HammerToolStep]:
        return self.make_steps_from_methods([
            self.write_gl_files,
            self.generate_files,
            self.run_vcs,  # VCS elaboration for VC-Z01X
            self.generate_tcl,
            self.fgen,
            self.fcc,
            self.fcm
            ])

    def benchmark_run_dir(self, bmark_path: str) -> str:
        """Generate a benchmark run directory."""
        # TODO(ucb-bar/hammer#462) this method should be passed the name of the bmark rather than its path
        bmark = os.path.basename(bmark_path)
        return os.path.join(self.run_dir, bmark)

    @property
    def force_regs_file_path(self) -> str:
        return os.path.join(self.run_dir, "force_regs.ucli")

    @property
    def access_tab_file_path(self) -> str:
        return os.path.join(self.run_dir, "access.tab")

    @property
    def simulator_executable_path(self) -> str:
        return os.path.join(self.run_dir, "simv")

    @property
    def run_tcl_path(self) -> str:
        return os.path.join(self.run_dir, "run.tcl")

    @property
    def run_fsim_tcl_path(self) -> str:
        return os.path.join(self.run_dir, "fsim.tcl")

    @property
    def fault_report_path(self) -> str:
        return os.path.join(self.run_dir, self.get_setting("fsim.inputs.output_fault_rpt"))

    @property
    def fsdb_path(self) -> str:
        return os.path.join(self.run_dir, "fsdb")

    @property
    def env_vars(self) -> Dict[str, str]:
        env = dict(super().env_vars)
        env["VCS_HOME"] = self.get_setting("fsim.vc_zoix.vcs_home")
        env["VERDI_HOME"] = self.get_setting("fsim.vc_zoix.verdi_home")
        env["SNPSLMD_LICENSE_FILE"] = self.get_setting("synopsys.SNPSLMD_LICENSE_FILE")
        return env

    def get_verilog_models(self) -> List[str]:
        verilog_sim_files = self.technology.read_libs([
            hammer_tech.filters.verilog_sim_filter
        ], hammer_tech.HammerTechnologyUtils.to_plain_item)
        return verilog_sim_files

    def write_gl_files(self) -> bool:
        if self.level == FlowLevel.RTL:
            return True

        tb_prefix = self.get_setting("fsim.inputs.tb_dut")
        force_val = self.get_setting("fsim.inputs.gl_register_force_value")

        abspath_seq_cells = os.path.join(os.getcwd(), self.seq_cells)
        if not os.path.isfile(abspath_seq_cells):
            self.logger.error("List of seq cells json not found as expected at {0}".format(self.seq_cells))

        with open(self.access_tab_file_path, "w") as f:
            with open(abspath_seq_cells) as seq_file:
                seq_json = json.load(seq_file)
                assert isinstance(seq_json, List), "list of all sequential cells should be a json list of strings not {}".format(type(seq_json))
                for cell in seq_json:
                    f.write("acc=wn:{cell_name}\n".format(cell_name=cell))

        abspath_all_regs = os.path.join(os.getcwd(), self.all_regs)
        if not os.path.isfile(abspath_all_regs):
            self.logger.error("List of all regs json not found as expected at {0}".format(self.all_regs))

        with open(self.force_regs_file_path, "w") as f:
            with open(abspath_all_regs) as reg_file:
                reg_json = json.load(reg_file)
                assert isinstance(reg_json, List), "list of all sequential cells should be a json list of dictionaries from string to string not {}".format(type(reg_json))
                # If the DfT has been inserted
                if self.get_setting("synthesis.dft_insertion") and self.level.is_gatelevel():
                    f.write("force "+ tb_prefix + ".test_se 0\n")
                    f.write("force "+ tb_prefix + ".test_mode 0\n")
                for reg in sorted(reg_json, key=lambda r: len(r["path"])): # TODO: This is a workaround for a bug in P-2019.06
                    path = reg["path"]
                    path = '.'.join(path.split('/'))
                    pin = reg["pin"]
                    f.write("force -deposit {" + tb_prefix + "." + path + "." + pin + "} " + str(force_val) + "\n")

        return True

    
    def run_vcs(self) -> bool:
        # Run elaboration
        # run through inputs and append to CL arguments
        vcs_bin = self.get_setting("fsim.vc_zoix.vcs_bin")
        if not os.path.isfile(vcs_bin):
            self.logger.error("VCS binary not found as expected at {0}".format(vcs_bin))
            return False

        if not self.check_input_files([".v", ".v.gz", ".sv", ".so", ".cc", ".c"]):
            return False

        # We are switching working directories and we still need to find paths
        abspath_input_files = list(map(lambda name: os.path.join(os.getcwd(), name), self.input_files))

        for v in abspath_input_files:
            if not os.path.exists(v):
                self.logger.error("Cannot find %s" % v)
                return False
        # TODO (franout) make it clever in makefile
        # Check for duplicate files and remove them
        abspath_input_files = list(set(abspath_input_files))
        # Grab the ChipTop RTL file and remove from the input files
        # used for the synthesis (just to be sure to elaborate the correct ChipTop module and submodules)
        if self.level.is_gatelevel():
            rtl_files_to_remove = []
            for v_file in abspath_input_files:
                if v_file in self.get_setting("fsim.inputs.syn_input_files"):
                    rtl_files_to_remove.append(v_file)
            if len(rtl_files_to_remove) > 0:
                for file_to_remove in rtl_files_to_remove:
                    abspath_input_files.remove(file_to_remove)

        top_module = self.top_module
        compiler_cc_opts = self.get_setting("fsim.inputs.compiler_cc_opts", [])
        compiler_ld_opts = self.get_setting("fsim.inputs.compiler_ld_opts", [])
        # TODO(johnwright) sanity check the timescale string
        timescale = self.get_setting("fsim.inputs.timescale")
        options = self.get_setting("fsim.inputs.options", [])
        defines = self.get_setting("fsim.inputs.defines", [])
        optional_elaboration_flags = self.get_setting("fsim.inputs.optional_elaboration_flags", [])
        access_tab_filename = self.access_tab_file_path
        tb_name = self.get_setting("fsim.inputs.tb_name")
        strobe_file_path = os.path.join(os.getcwd(), self.strobe_file_name)

        # Build args
        args = [
        vcs_bin,
        "-kdb",
        "-full64",
        "-hsopt=gates",
        "-lca", # enable advanced features access, add'l no-cost licenses may be req'd depending on feature
        "-debug_access+all" # since I-2014.03, req'd for FSDB dumping & force regs
        ]

        if self.get_setting("fsim.vc_zoix.fgp") and self.version() >= self.version_number("M-2017.03"):
            args.append("-fgp")

        if timescale is not None:
            args.append('-timescale={}'.format(timescale))

        # Add in options we pass to the C++ compiler
        args.extend(['-CC', '-I$(VCS_HOME)/include'])
        for compiler_cc_opt in compiler_cc_opts:
            args.extend(['-CFLAGS', compiler_cc_opt])

        # vcs requires libraries (-l) to be outside of the LDFLAGS
        for compiler_ld_opt in compiler_ld_opts:
            if compiler_ld_opt.startswith('-l'):
                args.extend([compiler_ld_opt])
            else:
                args.extend(['-LDFLAGS', compiler_ld_opt])

        # black box options
        args.extend(options)

        # Multicore elaboration options
        if isinstance(self.submit_command, HammerLSFSubmitCommand):
            if self.submit_command.settings.num_cpus is not None:
                args.extend(['-j'+str(self.submit_command.settings.num_cpus)])

        # Add in all input files
        args.extend(abspath_input_files)


        args.append(strobe_file_path)

        # Note: we always want to get the verilog models because most real designs will instantate a few
        # tech-specific cells in the source RTL (IO cells, clock gaters, etc.)
        args.extend(self.get_verilog_models())

        for define in defines:
            args.extend(['+define+' + define])

        if self.level.is_gatelevel():
            args.extend(['-P'])
            args.extend([access_tab_filename])
            args.extend(["+notimingcheck"])
            if self.get_setting("fsim.inputs.timing_annotated"):
                args.extend(["+neg_tchk"])
                args.extend(["+sdfverbose"])
                if self.sdf_file:
                    args.extend(["-sdf", "typ:{top}:{sdf}".format(top=top_module, sdf=os.path.join(os.getcwd(), self.sdf_file))])
                # Distributed delays are delays on nets, primitives, or continuous assignments. 
                args.extend(["+delay_mode_distributed_path"])
            else:
                args.extend(["+delay_mode_zero"])
        else:
            # Also disable timing at RTL level for any hard macros
            args.extend(["+notimingcheck"])
            args.extend(["+delay_mode_zero"])


        args.extend(["-top", "strobe"])

        args.extend(['-o', self.simulator_executable_path])

        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False

        args.append("-fsim")
        
        # For small delay faults the fault simulation mode must be serial_flow
        # By default the mode is concurrent
        # TODO (franout): Transition delay faults are not yet supported in serial flow combined with standard delay format
        #if self.fault_model == "sdf":
        #    self.logger.info(f"VC Z01X using serial_flow fault simulation mode")
        #    args.append(f"-fsim=serial_flow")
        
        args.append("-fsim=dut:" + self.campaign_tb_dut)

        if self.level.is_gatelevel():
            args.append("-fsim=suppress+cell")
        elif self.level == FlowLevel.RTL:
            args.append("-fsim=portfaults")

        args.append("-suppress=TFIPC")
        args.append("-fsim=class")
        
        args.append("+notimingcheck")
        args.extend(optional_elaboration_flags)
        
        # Remove "+rad" from arguments if present, VC-Z01X does not support it
        args = [arg for arg in args if arg != "+rad"]

        # Delete an old copy of the simulator if it exists
        if os.path.exists(self.simulator_executable_path):
            os.remove(self.simulator_executable_path)

        # Remove the csrc directory (otherwise the simulator will be stale)
        if os.path.exists(os.path.join(self.run_dir, "csrc")):
            shutil.rmtree(os.path.join(self.run_dir, "csrc"))
        # Adding output elaboration dir
        args.append("-Mdir={}".format(self.run_dir))

        # Generate a simulator
        self.run_executable(args, cwd=self.run_dir)

        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        return os.path.exists(self.simulator_executable_path)

    def generate_tcl(self) -> bool:
        top_module = self.top_module
        exec_flags_prepend = self.get_setting("fsim.inputs.execution_flags_prepend", [])
        exec_flags = self.get_setting("fsim.inputs.execution_flags", [])
        exec_flags_append = self.get_setting("fsim.inputs.execution_flags_append", [])
        force_regs_filename = self.force_regs_file_path
        tb_prefix = self.get_setting("fsim.inputs.tb_dut")

        if self.level.is_gatelevel():
            find_regs_run_tcl = []
            find_regs_run_tcl.append("source " + force_regs_filename)
            find_regs_run_tcl.append("run")
            find_regs_run_tcl.append("exit")
            self.write_contents_to_path("\n".join(find_regs_run_tcl), self.run_tcl_path)

        for benchmark in self.benchmarks:
            if not os.path.isfile(benchmark):
                self.logger.error("benchmark not found as expected at {0}".format(benchmark))
                return False

        # setup simulation arguments
        args = [ ]
        args.extend(exec_flags_prepend)
        if self.get_setting("fsim.vc_zoix.fgp") and self.version() >= self.version_number("M-2017.03"):
            # num_threads is in addition to a master thread, so reduce by 1
            num_threads=int(self.get_setting("vlsi.core.fsim.max_threads")) - 1
            args.append("-fgp=num_threads:{threads},num_fsdb_threads:0,allow_less_cores,dynamictoggle".format(threads=max(num_threads,1)))
        args.extend(exec_flags)
        if self.level.is_gatelevel():
            args.extend(["-ucli", "-do", self.run_tcl_path])
        args.extend(exec_flags_append)
        for benchmark in self.benchmarks:
            args.append(benchmark)

        args_to_append = " ".join(args)
        if self.append_to_args(self.campaign_tcl, args_to_append) == False:
            return False

        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        return os.path.exists(self.campaign_tcl)

        
    def fgen(self) -> bool:
        campaign_simv_daidir = self.get_setting("fsim.inputs.campaign_simv_daidir")
        sff_report_path = os.path.join(self.run_dir, self.fault_model + "_" + self.campaign_tb_dut.split(".")[-1] + ".sff")
        
        fcc_bin = self.get_setting("fsim.vc_zoix.vc_fcc_bin")
        if not os.path.isfile(fcc_bin):
            self.logger.error("VC Z01X binary not found as expected at {0}".format(fcc_bin))
            return False
            
        if self.fsim_generate_faults:
            # Build args
            args = [
            fcc_bin,
            "-full64",
            "-daidir " + campaign_simv_daidir,
            "-sff " + self.standard_fault_format,
            "-uncontrollability",
            "-prune", "on",
            "-report " + sff_report_path,
            "-campaign " + self.campaign_tb_dut.split(".")[-1],
            "-collapse off",
            "-overwrite"
            ]

            HammerVLSILogging.enable_colour = False
            HammerVLSILogging.enable_tag = False

            # Generate a simulator
            self.run_executable(args, cwd=self.run_dir)

            HammerVLSILogging.enable_colour = True
            HammerVLSILogging.enable_tag = True

        else: 
            if self.standard_fault_format.endswith("sff"):
                shutil.copyfile(self.standard_fault_format, sff_report_path)
            elif self.standard_fault_format.endswith("fau"):
                # We need to convert to an SFF from Tetramax fault list
                # TODO (franout): temporary fix for appending full hierarchical path to faults location from TestMax (+dut+add+<string>  NYI in VC Z01X)
                dut_path = self.campaign_tb_dut

                with open(self.standard_fault_format,"r") as fault_list:
                    lines = fault_list.readlines()
                with open(self.standard_fault_format,"w") as fault_list:
                    for line in lines:
                        if not line.strip():
                            fault_list.write(line)
                            continue
                        # TODO (franout): temporary fix for supporting TP fault status NYI in VC Z0iX
                        if self.fault_model == "sdf":
                            # TP is transition partially detcted
                            # it can continue to be simulated with the intention of getting a better test for the fault.
                            line = line.replace("TP","NP") # TODO (franout): very optimistic, let's see if fsim can detect it better
                            # Select based on time margin 
                            columns = line.split()
                            # Check against the calculated time margin from the atpg in order to be considered a small delay fault
                            if float(columns[3]) >= self.time_margin :
                                continue
                        else:
                            columns = line.split()

                        if len(columns) >=3:
                            # Add dut path and replace VC-Z01X separator with TestMax separator
                            columns[2] = f'{dut_path.replace(".","/")}/{columns[2]}'
        
                        updated_line = "   ".join(columns)
                        fault_list.write(updated_line + "\n")
        
                # Build args
                args = [
                fcc_bin,
                "-full64",
                "-daidir " + campaign_simv_daidir,
                "-faultlist " + self.standard_fault_format,
                "-format" , "tetramax",
                "-report " + sff_report_path,
                "-campaign " + self.campaign_tb_dut.split(".")[-1],
                # TODO (franout) : to be implemented "-dut_path " + self.campaign_tb_dut,
                "-collapse off",
                "-overwrite"
                ]
                self.logger.info("Converting fault list format from TestMax to VC-Z01X")
                HammerVLSILogging.enable_colour = False
                HammerVLSILogging.enable_tag = False

                # Generate a simulator
                self.run_executable(args, cwd=self.run_dir)

                if self.fault_model == "tdf":
                    # Add per fault delay
                    fault_delay_string = f"({self.clocks[0].get('period')})"
                    if not(self._add_delays(fault_delay_string,sff_report_path)):
                        return False
                elif self.fault_model == "sdf":
                    # Merge with slack based file
                    self.logger.info("Remember to select NP as fault status to re-fault simulate!")
                    if not(self._fuse_slack_with_sff(self.slack_file, sff_report_path)):
                        return False
                HammerVLSILogging.enable_colour = True
                HammerVLSILogging.enable_tag = True
                # Update with the new sff path 
                self.standard_fault_format = sff_report_path
            else:
                self.logger.error(f"Fault format conversion for {self.standard_fault_format} is not supported")
        return os.path.exists(sff_report_path)


    def fcc(self) -> bool:
        fcc_bin = self.get_setting("fsim.vc_zoix.vc_fcc_bin")
        if not os.path.isfile(fcc_bin):
            self.logger.error("VC Z01X binary not found as expected at {0}".format(fcc_bin))
            return False
        campaign_simv_daidir = self.get_setting("fsim.inputs.campaign_simv_daidir")

        # Build args
        args = [
        fcc_bin,
        "-full64",
        "-daidir " + campaign_simv_daidir, 
        "-sff " + os.path.join(self.run_dir, self.fault_model + "_" + self.campaign_tb_dut.split(".")[-1] + ".sff"),
        "-campaign " + self.campaign_tb_dut.split(".")[-1],
        "-prune", "global" ,
        "-uncontrollability",
        "-collapse off",
        "-overwrite"
        ]

        additional_args = self.get_setting("fsim.vc_zoix.fcc_additional_args") 
        args.extend(additional_args)
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False

        self.run_executable(args, cwd=self.run_dir)

        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        return os.path.exists(os.path.join(self.run_dir, campaign_simv_daidir))

    def fcm(self) -> bool:
        fcm_bin = self.get_setting("fsim.vc_zoix.vc_fcm_bin")
        if not os.path.isfile(fcm_bin):
            self.logger.error("VC Z01X binary not found as expected at {0}".format(fcm_bin))
            return False
        benchmark_name, extension = os.path.splitext(os.path.basename(self.benchmarks[0]))
        # Check if already exist a FSDB from the previous test in the same campaign

        fsdb_path = os.path.join(self.run_dir, "fcm_tsim_fsdb", self.campaign_tb_dut.split(".")[-1], f"{benchmark_name}.fsdb" )
        if os.path.exists(fsdb_path):
            # Remove test-related files to force the usage of a new testability database
            fsdb_dir = os.path.join(self.run_dir, "fcm_tsim_fsdb", self.campaign_tb_dut.split(".")[-1])
            # Iterate through all files and directories in the target directory
            for item in os.listdir(fsdb_dir):
                # Check if the basename contains the benchmark_name
                if benchmark_name in os.path.basename(item):
                    item_path = os.path.join(fsdb_dir, item)
                    if os.path.exists(item_path):
                        # Remove the file or directory
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)  # Remove directory
                            self.logger.warning(f"Removed directory: {item_path}")
                        else:
                            os.remove(item_path)  # Remove file
                            self.logger.warning(f"Removed file: {item_path}")
        # Build args
        args = [
        fcm_bin,
        "-tcl_script",
        self.campaign_tcl,
        "-campaign",
        self.campaign_tb_dut.split(".")[-1],
        "-connect"
        ]

        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False

        # Generate a simulator
        self.run_executable(args, cwd=self.run_dir)

        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        self.logger.info("Fault simulation finished.")
        
        source_dir = os.path.join(self.run_dir, self.fault_model, benchmark_name)
        os.makedirs(self.report_folder, exist_ok=True)
        # Copy the entire directory tree to the report folder
        shutil.copytree(source_dir, self.report_folder, dirs_exist_ok=True)
        
        return True

    def handle_errors(self, output: str, code: int) -> bool:
        """
        Function to run on tool error (nonzero return code).
        
        :return: True if successful, False otherwise.
        """
        if code != 0:    
            self.logger.error(output)
        else:
            self.logger.info(output)

tool = VC_ZOIX
