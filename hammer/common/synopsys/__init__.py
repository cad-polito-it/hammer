import datetime
import inspect
import os
import json
import copy
from typing import Optional, Dict, List

from hammer.vlsi import HasSDCSupport, TCLTool, HammerTool
import hammer.tech
from hammer.tech import HammerTechnologyUtils

class SynopsysTool(HasSDCSupport, TCLTool, HammerTool):
    """Mix-in trait with functions useful for Synopsys-based tools."""

    ## FIXME: not used by any Synopsys tool
    @property
    def post_synth_sdc(self) -> Optional[str]:
        return None

    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        result = dict(super().env_vars)
        result.update({
            "SNPSLMD_LICENSE_FILE": self.get_setting("synopsys.SNPSLMD_LICENSE_FILE"),
        })
        return result

    def version_number(self, version: str) -> int:
        """
        Assumes versions look like NAME-YYYY.MM-SPMINOR.
        Assumes less than 100 minor versions.

        Handles various version formats:
        - NAME-YYYY.MM (no minor version)
        - NAME-YYYY.MM-N (where N is a number like 4)
        - NAME-YYYY.MM-SPN (where SP is a prefix and N is a number)
        - NAME-YYYY.MM-PREFIXN (where PREFIX is any text and N is a number)
        """
        date = "-".join(version.split("-")[1:])  # type: str
        year = int(date.split(".")[0])  # type: int
        month = int(date.split(".")[1][:2])  # type: int
        minor_version = 0  # type: int
        if "-" in date:
            minor_part = date.split("-")[1]
            # Try to handle both formats: with prefix (like "SP4") and without (like "4")
            # If the minor part starts with non-digits, skip them
            for i, char in enumerate(minor_part):
                if char.isdigit():
                    minor_version = int(minor_part[i:])
                    break
        return (year * 100 + month) * 100 + minor_version

    @property
    def header(self) -> str:
        """
        Header for all generated Tcl scripts
        """
        header_text = f"""
        # ---------------------------------------------------------------------------------
        # Portions Copyright ©{datetime.date.today().year} Synopsys, Inc. All rights reserved. Portions of
        # these TCL scripts are proprietary to and owned by Synopsys, Inc. and may only be
        # used for internal use by educational institutions (including United States
        # government labs, research institutes and federally funded research and
        # development centers) on Synopsys tools for non-profit research, development,
        # instruction, and other non-commercial uses or as otherwise specifically set forth
        # by written agreement with Synopsys. All other use, reproduction, modification, or
        # distribution of these TCL scripts is strictly prohibited.
        # ---------------------------------------------------------------------------------
        """
        return inspect.cleandoc(header_text)

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
    def timing_dbs(self) -> List[str]:
        # Gather/load libraries.
        return self.technology.read_libs(
            [hammer.tech.filters.timing_db_filter],
            HammerTechnologyUtils.to_plain_item)
    @property
    def timing_liberty(self) -> List[str]:
        # Gather/load libraries.
        return self.technology.read_libs(
            [hammer.tech.filters.liberty_lib_filter],
            HammerTechnologyUtils.to_plain_item)

    @property
    def milkyway_lib_dirs(self) -> List[str]:
        return self.technology.read_libs(
            [hammer.tech.filters.milkyway_lib_dir_filter],
            HammerTechnologyUtils.to_plain_item)

    @property
    def milkyway_techfiles(self) -> List[str]:
        return self.technology.read_libs(
            [hammer.tech.filters.milkyway_techfile_filter],
            HammerTechnologyUtils.to_plain_item)

    @property
    def tlu_max_caps(self) -> List[str]:
        return self.technology.read_libs(
            [hammer.tech.filters.tlu_max_cap_filter],
            HammerTechnologyUtils.to_plain_item)

    @property
    def tlu_min_caps(self) -> List[str]:
        return self.technology.read_libs(
            [hammer.tech.filters.tlu_min_cap_filter],
            HammerTechnologyUtils.to_plain_item)

    @property
    def tlu_map(self) -> List[str]:
        return self.technology.read_libs(
            [hammer.tech.filters.tlu_map_file_filter],
            HammerTechnologyUtils.to_plain_item)

    @property
    def verilog(self) -> List[str]:
        return [v for v in list(self.input_files) if v.endswith(".v") or v.endswith(".sv")]


    def get_synopsys_rm_tarball(self, product: str, settings_key: str = "") -> str:
        """Locate reference methodology tarball.

        :param product: Either "DC" or "ICC"
        :param settings_key: Key to retrieve the version for the product. Leave blank for DC and ICC.
        """
        key = self.tool_config_prefix() + "." + "version" # type: str

        synopsys_rm_tarball = os.path.join(self.get_setting("synopsys.rm_dir"), "%s-RM_%s.tar" % (product, self.get_setting(key)))
        if not os.path.exists(synopsys_rm_tarball):
            self.logger.warning("Expected reference methodology tarball not found at %s. Use the Synopsys RM generator <https://solvnet.synopsys.com/rmgen> to generate a DC reference methodology. If these tarballs have been pre-downloaded, you can set synopsys.rm_dir instead of generating them yourself." % (synopsys_rm_tarball))
            return ""
        else:
            return synopsys_rm_tarball
    def child_modules_tcl(self) -> str:
        """
        Dumps a list of child instance paths and their ilm directories.
        Should only be called when self.hierarchical_mode.is_nonleaf_hierarchical()
        """
        if self.get_setting("vlsi.inputs.hierarchical.config_source") != "manual":
            self.logger.warning('''
            Hierarchical write_regs requires having vlsi.inputs.hierarchical.manual_modules specified.
            You may have problems with register forcing in gate-level sim.
            ''')
            return '''
set child_modules_ir "./find_child_modules.json"
set child_modules_ir [open $child_modules_ir "w"]
puts $child_modules_ir "\\{\\}"
close $child_modules_ir
            '''
        else:
            # Write out the paths to all child find_regs_paths.json files
            child_modules = list(next(d for i,d in enumerate(self.get_setting("vlsi.inputs.hierarchical.manual_modules")) if self.top_module in d).values())[0]

            # Get all paths to the child module instances
            # For P&R, this only works in the flattened ILM state
            return '''
set child_modules_ir "./find_child_modules.json"
set child_modules_ir [open $child_modules_ir "w"]
puts $child_modules_ir "\\{{"

set cells {{ {CELLS} }}
set numcells [llength $cells]

for {{set i 0}} {{$i < $numcells}} {{incr i}} {{
    set cell [lindex $cells $i]
    set inst_paths [get_db [get_db modules -if {{.name==$cell}}] .hinsts.name]
    set inst_paths [join $inst_paths "\\", \\""]
    if {{$i == $numcells - 1}} {{
        puts $child_modules_ir "    \\"$cell\\": \\[\\"$inst_paths\\"\\]"
    }} else {{
        puts $child_modules_ir "    \\"$cell\\": \\[\\"$inst_paths\\"\\],"
    }}
}}

puts $child_modules_ir "\\}}"

close $child_modules_ir
        '''.format(CELLS=" ".join(child_modules))

    def write_regs_tcl(self) -> str:
        return '''
set write_cells_ir "./find_regs_cells.json"
set write_cells_ir [open $write_cells_ir "w"]
puts $write_cells_ir "\\["
set index 0
set len_regs [sizeof_collection [all_registers]]

foreach_in_collection reg [all_registers] {
    set name [get_attribute $reg ref_name]
    if { $index == $len_regs - 1 } {
        puts $write_cells_ir "   \\"$name\\" "
    } else {
        puts $write_cells_ir "   \\"$name\\", "
    }
    incr index
}

puts $write_cells_ir "\\]"
close $write_cells_ir
set write_regs_ir "./find_regs_paths.json"
set write_regs_ir [open $write_regs_ir "w"]
puts $write_regs_ir "\\["

set len_regs [sizeof_collection [all_registers -output_pins -edge_triggered]]

set regs_list [list]
foreach_in_collection reg [all_registers -output_pins -edge_triggered] {
    # Enforce the dump of only connected output ports of registers
    set size_connected [sizeof_collection [all_connected $reg]]
    if {$size_connected > 0} {
    set name [get_attribute $reg full_name]
    lappend regs_list [get_attribute $reg full_name]
    }
}

# Join list elements with a comma and a newline for clean formatting
set json_content ""
foreach name $regs_list {
    lappend json_content "  \\"$name\\""
}

puts $write_regs_ir [join $json_content ",\\n"]
puts $write_regs_ir "\\]"

close $write_regs_ir
        '''

    def process_reg_paths(self, path: str) -> bool:
        # Post-process the all_regs list here to avoid having too much logic in TCL
        with open(path, "r+") as f:
            reg_paths = json.load(f)
            output_paths = [] #  type: List[Dict[str,str]]
            assert isinstance(reg_paths, List), "Output find_regs_paths.json should be a json list of strings"
            for i in range(len(reg_paths)):
                split = reg_paths[i].split("/")
                # If the net is part of a generate block, the generated names have a "." in them and the whole name
                # needs to be escaped.
                for index, node in enumerate(split):
                    if "." in node:
                        split[index] = "\\" + node + "\\"
                # If the last net is part of a bus, it needs to be escaped
                if split[-2][-1] == "]":
                    split[-2] = "\\" + split[-2]
                    reg_paths[i] = {"path" : '/'.join(split[0:len(split)-1]), "pin" : split[-1]}
                else:
                    reg_paths[i] = {"path" : '/'.join(split[0:len(split)-1]), "pin" : split[-1]}

            # For parent hierarchical modules, append all child instance regs
            if self.hierarchical_mode.is_nonleaf_hierarchical():
                with open(os.path.join(os.path.dirname(path), "find_child_modules.json"), "r") as cmf:
                    mod_paths = json.load(cmf)
                for mod_path in mod_paths.items():
                    ilm = next(i for i in self.get_input_ilms() if i.module == mod_path[0])  # type: ILMStruct
                    with open(os.path.join(os.path.dirname(ilm.dir), "find_regs_paths.json"), "r") as crf:
                        child_regs = json.load(crf)
                    for inst_path in mod_path[1]:
                        prefixed_regs = copy.deepcopy(child_regs)
                        for reg in prefixed_regs:
                            reg.update({'path': os.path.join(inst_path, reg['path'])})
                        reg_paths.extend(prefixed_regs)

            f.seek(0) # Move to beginning to rewrite file
            json.dump(reg_paths, f, indent=2) # Elide the truncation because we are always increasing file size
        return True

    def write_testbench(self, stil_path: str, testbench_name: str, options: Optional[List[str]] = []) -> bool:
        """
        Generate a Verilog testbench from a STIL file using stil2verilog.

        :param stil_path: Path to the input STIL file.
        :param testbench_name: Name of the output Verilog testbench file.
        :param options: Optional list of additional command-line options for stil2verilog.
                        If None or empty, no additional options are passed.
        :return: True if the testbench was generated successfully.
        """
        args_testbench = ["stil2verilog", stil_path, testbench_name, "-replace"]
        if options:
            args_testbench.extend(options)

        self.run_executable(args_testbench, self.run_dir)

        return True