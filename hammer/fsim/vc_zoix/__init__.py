#  hammer-vlsi plugin for Synopsys VC-Z01X
#
#  See LICENSE for license details.

from hammer.vlsi import HammerFaultSimTool, HammerToolStep, HammerLSFSubmitCommand, HammerLSFSettings
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
                # Regex: (\s-report\s+)(fsim_out(?:_hier)?\.rpt)
                # This captures the flag (group 1) and the specific filenames (group 2)
                report_match = re.search(r'(\s-report\s+)(.*?(fsim_out(?:_hier)?\.rpt))', line)

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
                    prefix = line[:report_match.start(2)] 
                
                    # Get just the base filename, e.g., 'fsim_out.rpt'
                    report_filename = report_match.group(3) 
                    
                    suffix = line[report_match.end(2):]

                    # Split the basename into name and extension ("program_name", ".riscv")
                    benchmark_name, extension = os.path.splitext(os.path.basename(self.benchmarks[0]))

                    # --- Build new directory structure ---
                    if not created_report_dir: # Only run this once
                        # 1. Create the new directory path string
                        # e.g., "my/results/MyCoreConfig/_sa1_/fft"
                        self.report_folder = os.path.join(self.output_folder,
                                                    self.core, 
                                                    self.output_level,
                                                    self.fault_model, 
                                                    benchmark_name)

                        self.results_folder = os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name)
                        
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

    def tool_config_prefix(self) -> str:
        return "fsim.vc_zoix"

    def fill_outputs(self) -> bool:
# TODO: support automatic waveform generation for debugging Good Machine and Faulty Machine with Verdi
        self.output_waveforms = []
        self.output_top_module = self.top_module
        self.output_tb_name = self.get_setting("fsim.inputs.tb_name")
        self.output_tb_dut = self.get_setting("fsim.inputs.tb_dut")
        self.strobe_file_name = self.get_setting("fsim.inputs.strobe_file_name")
        self.fault_model = self.get_setting("fsim.inputs.fault_model")
        self.core = self.get_setting("fsim.inputs.core")
        self.clocks = self.get_setting("vlsi.inputs.clocks")
        benchmark_name, extension = os.path.splitext(os.path.basename(self.benchmarks[0]))
        if not os.path.exists(os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name)):
            os.makedirs(os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name))
        if os.path.exists(self.strobe_file_name) == False:
            self.strobe_file_name = os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name, "strobe.sv")
            os.makedirs(os.path.dirname(self.strobe_file_name), exist_ok=True)
            with open(self.strobe_file_name, "w") as f:
                f.write("`ifndef TOPLEVEL\n")
                f.write("    `define TOPLEVEL " + self.output_tb_dut + "\n")
                f.write("`endif\n")
                f.write("\n")
                f.write("module strobe;\n")
                f.write("\n")
                f.write("initial begin\n")
                defines = self.get_setting("fsim.inputs.defines")
                reset_delay = next(
                    (s.split("=", 1)[1] for s in defines if s.startswith("RESET_DELAY=")),
                    "10"
                )
                f.write("    #" + reset_delay + ";\n")
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
        self.output_level = self.get_setting("fsim.inputs.level")
        self.campaign_tb_dut = self.get_setting("fsim.inputs.campaign_tb_dut")
        self.campaign_tcl = self.get_setting("fsim.inputs.campaign_tcl")
        if os.path.exists(self.campaign_tcl) == False:
            self.campaign_tcl = os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name, "fsim.tcl")
            os.makedirs(os.path.dirname(self.campaign_tcl), exist_ok=True)
            with open(self.campaign_tcl, "w") as f:
                f.write("set_config -global_max_jobs 16\n")
                f.write("set_config -fsim_std_args \"-fsim=limit+hyperactive+0\"\n")
                f.write("\n")
                f.write("## DYNAMIC RUNTIME - Do not modify this! The __init__.py checks for the args string to parse the required arguments\n")
                f.write("create_testcases -name {\"test1\"} \\\n")
                f.write("-exec simv \\\n")
                f.write("-args \"\"\n")
                f.write("\n")
                f.write("# Start fault simulation\n")
                f.write("fsim -verbose \n")
                f.write("\n")
                f.write("# Write results report\n")
                f.write("report -campaign  chiptop0 -report fsim_out.rpt -overwrite -showfaultid\n")
                f.write("report -campaign  chiptop0 -report fsim_out_hier.rpt -overwrite -hierarchical 0\n")
        self.output_folder = self.get_setting("fsim.inputs.output_folder")
        self.fsim_generate_faults = int(self.get_setting("fsim.inputs.fsim_generate_faults"))
        self.standard_fault_format = self.get_setting("fsim.inputs.standard_fault_format")
        if os.path.exists(self.standard_fault_format) == False:
            self.standard_fault_format = os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name, "gen_" + self.fault_model + "_" + self.output_tb_dut.split(".")[-1] + ".sff")
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
                f.write("        \"Test Coverage\" = \"(DD * DD_weight + DT * DT_weight + DE * DE_weight + DF * DF_weight + PD * PD_weight + PT * PT_weight)/(Total)\";\n")
                f.write("        \"Fault Coverage\" = \"(DD * DD_weight + DT * DT_weight + DE * DE_weight + DF * DF_weight + PD * PD_weight + PT * PT_weight)/(Total + UB + UI + UR + UT + UU + UO)\";\n") 
                f.write("}\n")
                f.write("\n")
                # self.constraints = self.get_setting("fsim.inputs.constraints")
                # if self.constraints:
                #     for constraint in self.constraints:
                #         f.write("Constraints " + constraint + "\n")
                #         f.write("{\n")
                #     f.write("\n")
                f.write("# Set fault generation constraints\n")
                f.write("FaultGenerate\n")
                f.write("{\n")
                self.fault_locations = self.get_setting("fsim.inputs.fault_locations")
                if (self.fault_model == "saf" or self.fault_model == "tdf"):
                    for fl in self.fault_locations:
                        if fl.get('exclude'):
                            f.write("    Exclude {\n")
                            f.write("        NA [" + fl.get('fault_type') + "] {" + fl.get('type_of_fault_location') + " \"" + fl.get('location') + "\" }\n")
                            f.write("    }\n")
                        else:
                            f.write("    NA [" + fl.get('fault_type') + "] {" + fl.get('type_of_fault_location') + " \"" + fl.get('location') + "\" }\n")
                        f.write("\n")
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
        self.report_folder = ""
        return True

    @property
    def steps(self) -> List[HammerToolStep]:
        return self.make_steps_from_methods([
            self.fill_outputs,
            self.write_gl_files,
            self.run_vcs,
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
        return os.path.join(self.run_dir, "ffsim.tcl")

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
                if self.get_setting("synthesis.dc.dft_insertion"):
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
        optional_execution_flags = self.get_setting("fsim.inputs.optional_execution_flags", [])
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
            if self.get_setting("fsim.inputs.timing_annotated"):
                args.extend(["+neg_tchk"])
                args.extend(["+sdfverbose"])
                args.extend(["-negdelay"])
                args.extend(["-sdf"])
                if self.sdf_file:
                    args.extend(["max:{top}:{sdf}".format(top=top_module, sdf=os.path.join(os.getcwd(), self.sdf_file))])
            else:
                args.extend(["+notimingcheck"])
                args.extend(["+delay_mode_zero"])
        else:
            # Also disable timing at RTL level for any hard macros
            args.extend(["+notimingcheck"])
            args.extend(["+delay_mode_zero"])


        args.extend(["-top", "strobe"])

        args.extend(['-o', self.simulator_executable_path])

        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False

        # ToDO: distinguish between Gate and RTL - saf, tdf, tf
        args.append("-fsim")
        args.append("-fsim=dut:" + self.campaign_tb_dut)

        if self.level.is_gatelevel():
            args.append("-fsim=suppress+cell")
        elif self.level == FlowLevel.RTL:
            args.append("-fsim=portfaults")

        args.append("-suppress=TFIPC")
        args.append("-fsim=class")
        
        args.append("+notimingcheck")
        args.append("+define+fsdb")
        args.extend(optional_execution_flags)
        
        # Remove "+rad" from arguments if present
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
            num_threads=int(self.get_setting("vlsi.core.max_threads")) - 1
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
        if (self.fsim_generate_faults == 1):
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
            "-sff " + self.standard_fault_format,
            "-report " + os.path.join(self.run_dir, self.fault_model + "_" + self.campaign_tb_dut.split(".")[-1] + ".sff"),
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

            return os.path.exists(os.path.join(self.run_dir, self.fault_model + "_" + self.campaign_tb_dut.split(".")[-1] + ".sff"))
        else: 
            shutil.copyfile(self.standard_fault_format, os.path.join(self.run_dir, self.fault_model + "_" + self.campaign_tb_dut.split(".")[-1] + ".sff"))
            return os.path.exists(os.path.join(self.run_dir, self.fault_model + "_" + self.campaign_tb_dut.split(".")[-1] + ".sff"))


    def fcc(self) -> bool:
        #ToDo Check for correct fault collapsing
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
        "-overwrite"
        ]

        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False

        self.run_executable(args, cwd=self.run_dir)

        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True

        return os.path.exists(os.path.join(self.run_dir, campaign_simv_daidir))

    def fcm(self) -> bool:
        #ToDo Check for completed fsim
        fcm_bin = self.get_setting("fsim.vc_zoix.vc_fcm_bin")
        if not os.path.isfile(fcm_bin):
            self.logger.error("VC Z01X binary not found as expected at {0}".format(fcm_bin))
            return False

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
        
        os.makedirs(self.report_folder, exist_ok=True)
        benchmark_name, extension = os.path.splitext(os.path.basename(self.benchmarks[0]))
        subprocess.run("cp " +  os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name) + "/*.rpt " + self.report_folder, shell=True, check=False)
        subprocess.run("cp " +  os.path.join(self.run_dir, self.core, self.fault_model, benchmark_name) + "/*.sff " + self.report_folder, shell=True, check=False)
        subprocess.run("cp " +  self.run_dir + "/*.sff " + self.report_folder, shell=True, check=True)

        # ToDo: change with non-static naming which can be changed in the fsim.tcl file using the fsim.mk
        return os.path.exists(os.path.join(self.report_folder, "fsim_out.rpt"))

tool = VC_ZOIX
